#!/usr/bin/env python3
"""
Decoupled Multi-League Live Game Event Ledger (ESPN -> GCS JSONL)
=================================================================
Polls live ESPN matchups for ALL enabled leagues in `leagues_registry.json`
during active NFL windows and records canonical, timestamped game drama
(win-probability shockwaves, lead changes, nuclear explosions, mid-game
injury exits, and SNF/MNF sweat snapshots) to GCS:

  gs://<hub_bucket>/<slug>/telemetry/<season>_week_<w>_events.jsonl
  gs://<hub_bucket>/<slug>/telemetry/<season>_week_<w>_state.json

100% decoupled from Discord:
  - Non-Discord leagues (like `Just for Fun`) capture rich play-by-play swings,
    comebacks, and injuries purely from ESPN for the podcast & web hub.
  - Stateless across runs (hydrates prior state from GCS if local state is absent),
    making it native for serverless GCP Cloud Run Jobs + Cloud Scheduler ($0/mo).
  - Optionally forwards formatted alerts to a league's outbound webhook ONLY when
    configured in `leagues_registry.json`.

Usage:
  python3 FantasyRecap/live_event_ledger.py              # Poll all enabled leagues during NFL windows
  python3 FantasyRecap/live_event_ledger.py --force      # Force immediate poll outside NFL window
  python3 FantasyRecap/live_event_ledger.py --slug jff --force
"""

from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import requests

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from FantasyRecap.core.manifest import get_espn_credentials, load_project_env
load_project_env()

from FantasyRecap.config import CURRENT_YEAR
from FantasyRecap.core.espn_client import POSITION_MAP
from FantasyRecap.game_day_hub_refresher import REGISTRY_PATH, is_nfl_live_window
from FantasyRecap.league_recap_generator import fetch_espn_week_data
from FantasyRecap.multi_league_hub import normalize_generic_owner_name

TELEMETRY_DIR = SCRIPT_DIR / "live_state" / "telemetry"
TELEMETRY_DIR.mkdir(parents=True, exist_ok=True)
CT_TZ = ZoneInfo("America/Chicago")

# Calibrated dual-threshold nuclear criteria (both raw score AND +over-proj required!)
NUCLEAR_THRESHOLDS = {
    "QB": (34.0, 12.0),
    "RB": (28.0, 10.0),
    "WR": (28.0, 10.0),
    "TE": (24.0, 10.0),
    "K": (20.0, 10.0),
    "D/ST": (22.0, 12.0),
}
INJURY_EXIT_STATUSES = {"OUT", "DOUBTFUL", "INJURY_RESERVE", "IR"}
SHOCKWAVE_DELTA_PCT = 18.0


def _norm_cdf(z: float) -> float:
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def _nfl_window_label(now_ct: datetime = None) -> str:
    dt = now_ct or datetime.now(CT_TZ)
    wd = dt.weekday()
    hr = dt.hour + dt.minute / 60.0
    if wd == 3:
        return "Thu/TNF"
    if wd == 4:
        return "Fri NFL"
    if wd == 5:
        return "Sat NFL"
    if wd == 0:
        return "MNF"
    if wd == 6:
        if hr < 15.25:
            return "Sun Early"
        if hr < 18.75:
            return "Sun Late"
        return "SNF"
    return "Off-Window"


def _gcs_download_text(url: str) -> Optional[str]:
    try:
        r = requests.get(url, timeout=6)
        if r.status_code == 200:
            return r.text
    except Exception:
        pass
    return None


def _gcs_upload_file(local_path: Path, bucket: str, blob_path: str, content_type: str, project_id: str) -> bool:
    """
    Uploads to GCS via `google-cloud-storage` SDK (native ADC in Cloud Run!) with automatic
    fallback to `gcloud storage cp` (Mac Mini local CLI).
    """
    try:
        from google.cloud import storage  # type: ignore
        client = storage.Client(project=project_id)
        b = client.bucket(bucket)
        blob = b.blob(blob_path)
        blob.cache_control = "no-cache, max-age=30"
        blob.upload_from_filename(str(local_path), content_type=content_type)
        return True
    except Exception:
        pass

    try:
        subprocess.run(
            [
                "gcloud", "storage", "cp", str(local_path), f"gs://{bucket}/{blob_path}",
                f"--project={project_id}",
                f"--content-type={content_type}",
                "--cache-control=no-cache, max-age=30",
            ],
            check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        return True
    except Exception as exc:
        print(f"⚠️ GCS upload failed for gs://{bucket}/{blob_path}: {exc}")
        return False


def _detect_active_nfl_week(league_id: str, cookies: dict, season: int) -> int:
    url = (
        f"https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/{season}"
        f"/segments/0/leagues/{league_id}?view=mSettings"
    )
    r = requests.get(url, cookies=cookies, timeout=8)
    r.raise_for_status()
    st = r.json().get("status", {})
    return int(st.get("currentMatchupPeriod") or st.get("latestScoringPeriod") or 1)


def _compute_matchup_win_prob(
    a_live: float, a_proj: float, h_live: float, h_proj: float, base_std: float = 17.0
) -> float:
    """Returns Away team win probability in [0.0, 100.0] using ESPN live projections + remaining variance."""
    a_rem = max(0.0, a_proj - a_live)
    h_rem = max(0.0, h_proj - h_live)
    if a_rem < 0.2 and h_rem < 0.2 and (a_live > 0 or h_live > 0):
        return 100.0 if a_live > h_live else (0.0 if h_live > a_live else 50.0)
    a_frac = max(0.02, min(1.0, a_rem / max(1.0, a_proj))) if a_proj > 0 else 1.0
    h_frac = max(0.02, min(1.0, h_rem / max(1.0, h_proj))) if h_proj > 0 else 1.0
    comb_std = math.sqrt((base_std ** 2) * (a_frac + h_frac))
    comb_std = max(2.5, comb_std)
    z = (a_proj - h_proj) / comb_std
    return round(_norm_cdf(z) * 100.0, 1)


def detect_live_events_from_snapshots(
    prev_state: dict,
    curr_matchups: List[dict],
    *,
    slug: str,
    season: int,
    week: int,
    now_epoch: Optional[float] = None,
    now_ct: Optional[datetime] = None,
) -> Tuple[List[dict], dict]:
    """
    Pure, unit-testable state-diff detector comparing `prev_state` vs `curr_matchups`.
    Returns `(new_events, updated_state)`.
    """
    ts = float(now_epoch if now_epoch is not None else time.time())
    dt_ct = now_ct or datetime.fromtimestamp(ts, tz=CT_TZ)
    win_lbl = _nfl_window_label(dt_ct)
    ts_iso = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
    time_ct_str = dt_ct.strftime("%a %I:%M %p CT").replace(" 0", " ")

    prev_m = prev_state.get("matchups", {})
    fired = set(prev_state.get("fired_keys", []))
    new_events: List[dict] = []
    next_m: Dict[str, dict] = {}

    for m in curr_matchups:
        mk = m["matchup_key"]
        a_own, h_own = m["away_owner"], m["home_owner"]
        a_live, h_live = float(m["away_live"]), float(m["home_live"])
        a_proj, h_proj = float(m["away_proj"]), float(m["home_proj"])
        a_wp = _compute_matchup_win_prob(a_live, a_proj, h_live, h_proj)
        h_wp = round(100.0 - a_wp, 1)

        p_scores_curr = {str(p["id"]): p for p in (m.get("away_starters", []) + m.get("home_starters", []))}
        pm = prev_m.get(mk)

        # 1. Nuclear Explosions (dual threshold: raw score AND +over-proj)
        for side_own, opp_own, starters in (
            (a_own, h_own, m.get("away_starters", [])),
            (h_own, a_own, m.get("home_starters", [])),
        ):
            for p in starters:
                pos = p.get("pos", "FLEX")
                pts = float(p.get("pts", 0.0))
                proj = float(p.get("proj", 0.0))
                raw_min, over_min = NUCLEAR_THRESHOLDS.get(pos, (28.0, 10.0))
                n_key = f"nuclear:{mk}:{p['id']}"
                if pts >= raw_min and (pts - proj) >= over_min and n_key not in fired:
                    fired.add(n_key)
                    new_events.append({
                        "ts": ts,
                        "ts_iso": ts_iso,
                        "time_ct": time_ct_str,
                        "window": win_lbl,
                        "league": slug,
                        "season": season,
                        "week": week,
                        "matchup_key": mk,
                        "owners": [a_own, h_own],
                        "type": "nuclear",
                        "owner": side_own,
                        "player": p["name"],
                        "pos": pos,
                        "pts": round(pts, 1),
                        "proj": round(proj, 1),
                        "summary": f"{p['name']} ({pos}, {side_own}) erupted for {pts:.1f} pts (+{pts - proj:.1f} over proj) vs {opp_own} ({a_own} {a_live:.1f} – {h_own} {h_live:.1f}).",
                    })

                # 2. Mid-Game Injury Exit detection (only after player's NFL game has kicked off!)
                inj = str(p.get("injury") or "ACTIVE").upper()
                has_played = bool(p.get("has_played", True))
                i_key = f"injury:{mk}:{p['id']}"
                if has_played and inj in INJURY_EXIT_STATUSES and proj >= 8.0 and 0.0 <= pts <= 3.0 and (a_live + h_live) >= 15.0 and i_key not in fired:
                    fired.add(i_key)
                    new_events.append({
                        "ts": ts,
                        "ts_iso": ts_iso,
                        "time_ct": time_ct_str,
                        "window": win_lbl,
                        "league": slug,
                        "season": season,
                        "week": week,
                        "matchup_key": mk,
                        "owners": [a_own, h_own],
                        "type": "injury_exit",
                        "owner": side_own,
                        "player": p["name"],
                        "pos": pos,
                        "pts": round(pts, 1),
                        "proj": round(proj, 1),
                        "injury": inj,
                        "summary": f"Injury blow for {side_own}: starter {p['name']} ({pos}) flagged {inj} with {pts:.1f} pts (proj {proj:.1f}) vs {opp_own}.",
                    })

        # 3. Win-Probability Shockwaves & Lead Changes (requires prior snapshot!)
        if pm is not None and (a_live > 0 or h_live > 0):
            prev_a_wp = float(pm.get("away_wp", 50.0))
            delta_wp = a_wp - prev_a_wp
            if abs(delta_wp) >= SHOCKWAVE_DELTA_PCT:
                surging = a_own if delta_wp > 0 else h_own
                prev_ps = pm.get("player_pts", {})
                catalysts = []
                for pid_s, p in p_scores_curr.items():
                    jump = float(p["pts"]) - float(prev_ps.get(pid_s, 0.0))
                    if jump >= 5.0:
                        catalysts.append((jump, f"{p['name']} (+{jump:.1f} to {p['pts']:.1f})"))
                catalysts.sort(key=lambda x: -x[0])
                cat_txt = "; ".join(c[1] for c in catalysts[:2]) or "live scoring surge"
                new_events.append({
                    "ts": ts,
                    "ts_iso": ts_iso,
                    "time_ct": time_ct_str,
                    "window": win_lbl,
                    "league": slug,
                    "season": season,
                    "week": week,
                    "matchup_key": mk,
                    "owners": [a_own, h_own],
                    "type": "shockwave",
                    "surging_owner": surging,
                    "prev_away_wp": prev_a_wp,
                    "away_wp": a_wp,
                    "swing_pct": round(abs(delta_wp), 1),
                    "score": f"{a_own} {a_live:.1f} – {h_own} {h_live:.1f}",
                    "catalysts": [c[1] for c in catalysts[:2]],
                    "summary": f"Win-Prob Shockwave ({abs(delta_wp):.1f}% swing toward {surging}): {a_own} WP {prev_a_wp:.0f}% -> {a_wp:.0f}% ({a_live:.1f}–{h_live:.1f}). Driven by {cat_txt}.",
                })

            prev_leader = pm.get("leader")
            curr_leader = a_own if a_live > h_live else (h_own if h_live > a_live else "TIE")
            if prev_leader and prev_leader != "TIE" and curr_leader != "TIE" and curr_leader != prev_leader and min(a_live, h_live) >= 20.0:
                new_events.append({
                    "ts": ts,
                    "ts_iso": ts_iso,
                    "time_ct": time_ct_str,
                    "window": win_lbl,
                    "league": slug,
                    "season": season,
                    "week": week,
                    "matchup_key": mk,
                    "owners": [a_own, h_own],
                    "type": "lead_change",
                    "new_leader": curr_leader,
                    "prev_leader": prev_leader,
                    "summary": f"Lead change ({win_lbl}): {curr_leader} overtook {prev_leader} ({a_own} {a_live:.1f} – {h_own} {h_live:.1f}, WP {a_wp:.0f}%–{h_wp:.0f}%).",
                })

        # 4. Prime-Time Sweat Snapshot (once entering SNF & once entering MNF for undecided games)
        if win_lbl in ("SNF", "MNF"):
            pt_key = f"sweat:{win_lbl}:{mk}"
            a_rem, h_rem = max(0.0, a_proj - a_live), max(0.0, h_proj - h_live)
            if pt_key not in fired and (a_rem >= 5.0 or h_rem >= 5.0) and abs(a_live - h_live) <= 35.0:
                fired.add(pt_key)
                trailing = a_own if a_live < h_live else h_own
                leading = h_own if a_live < h_live else a_own
                deficit = abs(a_live - h_live)
                new_events.append({
                    "ts": ts,
                    "ts_iso": ts_iso,
                    "time_ct": time_ct_str,
                    "window": win_lbl,
                    "league": slug,
                    "season": season,
                    "week": week,
                    "matchup_key": mk,
                    "owners": [a_own, h_own],
                    "type": "prime_time_sweat",
                    "summary": f"{win_lbl} Sweat: {trailing} trails {leading} by {deficit:.1f} pts ({a_own} {a_live:.1f} – {h_own} {h_live:.1f}; live proj {a_proj:.1f}–{h_proj:.1f}, WP {a_wp:.0f}%–{h_wp:.0f}%).",
                })

        next_m[mk] = {
            "away_owner": a_own,
            "home_owner": h_own,
            "away_live": round(a_live, 2),
            "home_live": round(h_live, 2),
            "away_proj": round(a_proj, 2),
            "home_proj": round(h_proj, 2),
            "away_wp": a_wp,
            "leader": a_own if a_live > h_live else (h_own if h_live > a_live else "TIE"),
            "player_pts": {pid_s: round(float(p["pts"]), 2) for pid_s, p in p_scores_curr.items()},
        }

    next_state = {
        "slug": slug,
        "season": season,
        "week": week,
        "updated_at": ts_iso,
        "matchups": next_m,
        "fired_keys": sorted(fired),
    }
    return new_events, next_state


def _fetch_live_matchups_for_league(league_id: str, season: int, week: int, creds: dict) -> List[dict]:
    wk_raw = fetch_espn_week_data(str(league_id), str(season), week, creds["espn_s2"], creds["espn_swid"])
    members = {}
    for m in wk_raw.get("members", []):
        full = f"{m.get('firstName', '').strip()} {m.get('lastName', '').strip()}".strip() or m.get("displayName", "").strip()
        members[m["id"]] = normalize_generic_owner_name(full)

    tid_owner = {}
    tid_starters: Dict[int, List[dict]] = {}
    for t in wk_raw.get("teams", []):
        tid = int(t["id"])
        oid = t.get("primaryOwner") or (t.get("owners") or [None])[0]
        tid_owner[tid] = members.get(oid, t.get("name") or f"Team {tid}")
        starters = []
        for e in (t.get("roster") or {}).get("entries", []):
            slot_id = int(e.get("lineupSlotId", 20))
            if slot_id in (20, 21):
                continue
            pl = (e.get("playerPoolEntry") or {}).get("player") or {}
            pid = int(pl.get("id") or 0)
            pos = POSITION_MAP.get(int(pl.get("defaultPositionId") or 0), "FLEX")
            pts, proj, has_played = 0.0, 0.0, False
            for s in (pl.get("stats") or []):
                if s.get("scoringPeriodId") == week:
                    if s.get("statSourceId") == 0:
                        pts = float(s.get("appliedTotal", 0.0) or 0.0)
                        has_played = True
                    elif s.get("statSourceId") == 1:
                        proj = float(s.get("appliedTotal", 0.0) or 0.0)
            starters.append({
                "id": pid,
                "name": pl.get("fullName") or f"Player {pid}",
                "pos": pos,
                "pts": pts,
                "proj": proj,
                "has_played": has_played,
                "injury": pl.get("injuryStatus", "ACTIVE"),
            })
        tid_starters[tid] = starters

    out = []
    for g in wk_raw.get("schedule", []):
        if int(g.get("matchupPeriodId") or 0) != week:
            continue
        away, home = g.get("away") or {}, g.get("home") or {}
        a_id, h_id = away.get("teamId"), home.get("teamId")
        if not a_id or not h_id or a_id not in tid_owner or h_id not in tid_owner:
            continue
        a_own, h_own = tid_owner[a_id], tid_owner[h_id]
        a_live = float(away.get("totalPointsLive", 0.0) or away.get("totalPoints", 0.0) or 0.0)
        h_live = float(home.get("totalPointsLive", 0.0) or home.get("totalPoints", 0.0) or 0.0)
        a_proj = float(away.get("totalProjectedPointsLive", 0.0) or a_live)
        h_proj = float(home.get("totalProjectedPointsLive", 0.0) or h_live)
        out.append({
            "matchup_key": f"{min(a_id, h_id)}_{max(a_id, h_id)}",
            "away_owner": a_own,
            "home_owner": h_own,
            "away_live": a_live,
            "home_live": h_live,
            "away_proj": a_proj,
            "home_proj": h_proj,
            "away_starters": tid_starters.get(a_id, []),
            "home_starters": tid_starters.get(h_id, []),
        })
    return out


def poll_all_league_ledgers(
    *,
    season: int = int(CURRENT_YEAR),
    slug_filter: str = "",
    force: bool = False,
    sync_gcs: bool = True,
) -> dict:
    """Polls all enabled leagues in `leagues_registry.json`, detects live events, and persists to GCS JSONL."""
    if not force and not is_nfl_live_window():
        return {"ran": False, "reason": "outside_nfl_live_window"}

    reg = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    bucket = reg.get("hub_bucket", "bfl-hub-7x9k2")
    project = reg.get("gcp_project", "bfl-fantasy")
    creds = get_espn_credentials()
    results = {}

    for lg in reg.get("leagues", []):
        if not lg.get("enabled", True) or not lg.get("telemetry_enabled", True):
            continue
        slug = lg["slug"]
        if slug_filter and slug != slug_filter:
            continue
        lid = str(lg["espn_league_id"])
        subpath = lg.get("gcs_subpath", slug).strip("/")
        gcs_prefix = f"{subpath}/telemetry" if subpath else "telemetry"

        try:
            wk = _detect_active_nfl_week(lid, creds["cookies"], season)
            state_file = TELEMETRY_DIR / f"{slug}_{season}_week_{wk}_state.json"
            events_file = TELEMETRY_DIR / f"{slug}_{season}_week_{wk}_events.jsonl"

            # Hydrate statelessly from GCS if local cache doesn't exist (Cloud Run native!)
            prev_state = {}
            if state_file.exists():
                prev_state = json.loads(state_file.read_text(encoding="utf-8"))
            elif sync_gcs:
                remote_st = _gcs_download_text(f"https://storage.googleapis.com/{bucket}/{gcs_prefix}/{season}_week_{wk}_state.json")
                if remote_st:
                    prev_state = json.loads(remote_st)
            if not events_file.exists() and sync_gcs:
                remote_ev = _gcs_download_text(f"https://storage.googleapis.com/{bucket}/{gcs_prefix}/{season}_week_{wk}_events.jsonl")
                if remote_ev:
                    events_file.write_text(remote_ev, encoding="utf-8")

            curr_matchups = _fetch_live_matchups_for_league(lid, season, wk, creds)
            new_events, next_state = detect_live_events_from_snapshots(
                prev_state, curr_matchups, slug=slug, season=season, week=wk
            )

            state_file.write_text(json.dumps(next_state, indent=2), encoding="utf-8")
            if new_events:
                with events_file.open("a", encoding="utf-8") as f:
                    for ev in new_events:
                        f.write(json.dumps(ev) + "\n")
            elif not events_file.exists():
                events_file.write_text("", encoding="utf-8")

            if sync_gcs:
                _gcs_upload_file(state_file, bucket, f"{gcs_prefix}/{season}_week_{wk}_state.json", "application/json; charset=utf-8", project)
                _gcs_upload_file(events_file, bucket, f"{gcs_prefix}/{season}_week_{wk}_events.jsonl", "application/x-ndjson; charset=utf-8", project)

            results[slug] = {"ok": True, "week": wk, "matchups": len(curr_matchups), "new_events": len(new_events)}
            print(f"📡 [{slug}] Wk {wk}: polled {len(curr_matchups)} matchups, recorded {len(new_events)} new live events")
        except Exception as exc:
            print(f"❌ [{slug}] Live ledger poll failed: {exc}")
            results[slug] = {"ok": False, "error": str(exc)}

    return {"ran": True, "results": results}


def main():
    parser = argparse.ArgumentParser(description="Multi-League Decoupled Live Game Event Ledger (ESPN -> GCS)")
    parser.add_argument("--season", type=int, default=int(CURRENT_YEAR))
    parser.add_argument("--slug", type=str, default="")
    parser.add_argument("--force", action="store_true", help="Poll even outside live NFL window")
    parser.add_argument("--no-gcs", action="store_true", help="Skip GCS sync (local only)")
    args = parser.parse_args()

    res = poll_all_league_ledgers(season=args.season, slug_filter=args.slug, force=args.force, sync_gcs=not args.no_gcs)
    print(json.dumps(res, indent=2))


if __name__ == "__main__":
    main()
