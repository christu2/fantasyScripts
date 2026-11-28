#!/usr/bin/env python3
"""
Zero-Auth Sleeper Fantasy Football Adapter (Hub Builder & Live Telemetry)
=========================================================================
Ingests ANY Sleeper Fantasy Football league (`https://api.sleeper.app/v1/...`)
with ZERO cookies, ZERO API keys, and ZERO OAuth friction:

  - Supports Redraft, Keeper, Dynasty (incl. multi-year `previous_league_id`
    history chains and traded future draft picks!), Snake, Auction (`$`), and
    Linear Rookie Drafts.
  - Supports Superflex (`SUPER_FLEX`), TE Premium (`bonus_rec_te`), IDP (`DL`,
    `LB`, `DB`, `IDP_FLEX`), No-Kicker / No-DEF formats, Divisions or Flat
    standings, Fixed vs Re-Seeded brackets, 1-Wk or 2-Wk Playoff Rounds, and
    **League Median Scoring** (`league_average_match == 1`).
  - Tracks per-season rule evolution (`season_rules`) so leagues that changed
    team counts, scoring, or draft formats over time evaluate each season's
    Draft Hall & Trade Vault VORP against that exact season's baselines!
  - Exposes `fetch_sleeper_live_matchups()` so `live_event_ledger.py` captures
    real-time Sleeper shockwaves, lead changes, nuclear games, and injury exits!

Example:
  python3 FantasyRecap/sleeper_adapter.py --league-id 1313213902351273984 --slug sf-dynasty
  python3 FantasyRecap/sleeper_adapter.py --league-id 1389739171089756160 --slug lead-eaters --deploy-gcs bfl-hub-7x9k2
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sqlite3
import statistics
import sys
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import requests

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from FantasyRecap.bfl_history_db import init_db
from FantasyRecap.core.identity_resolver import LeagueIdentityResolver
from FantasyRecap.core.manifest import dedupe_co_owners
from FantasyRecap.core.security import validate_league_id, validate_sleeper_username, validate_slug
from FantasyRecap.multi_league_hub import (
    FALLBACK_REPLACEMENT_PPG,
    LEAGUES_DB_DIR,
    PLAYOFF_WEIGHT,
    TIE_MARGIN_VORP,
    WEB_HUB_DIR,
    _compute_generic_repl_baselines,
    _load_optional_owner_aliases,
    compute_dynamic_replacement_ranks,
    compute_h2h_extremes,
    deploy_multi_league_to_gcs,
    make_league_badge,
    normalize_generic_owner_name,
    partner_trade_extremes,
    run_generic_playoff_simulation,
    score_draft_picks_multi_format,
)
from FantasyRecap.weekly_awards_engine import compute_weekly_awards

SLEEPER_BASE = "https://api.sleeper.app/v1"
PLAYERS_CACHE_PATH = SCRIPT_DIR / "live_state" / "sleeper_players_slim.json"
PLAYERS_CACHE_TTL_SEC = 86400


def _sleeper_get(subpath: str, timeout: float = 12.0):
    url = f"{SLEEPER_BASE}/{subpath.lstrip('/')}"
    for attempt in range(3):
        try:
            r = requests.get(url, timeout=timeout)
            if r.status_code == 404:
                return None
            r.raise_for_status()
            return r.json()
        except Exception:
            if attempt == 2:
                raise
            time.sleep(0.6 * (attempt + 1))
    return None


def slot_tier_from_slot(slot_1based: int, team_count: int) -> str:
    """Classifies a 1-based draft slot (`1..N`) into `'early'`, `'mid'`, or `'late'`."""
    n = max(4, int(team_count))
    s = max(1, min(n, int(slot_1based)))
    early_cut = math.ceil(n / 3.0)
    mid_cut = math.ceil(2.0 * n / 3.0)
    if s <= early_cut:
        return "early"
    if s <= mid_cut:
        return "mid"
    return "late"


def estimate_dynasty_pick_vorp(
    round_num: int,
    slot_tier: str = "mid",
    *,
    is_superflex: bool = False,
    years_out: int = 1,
) -> float:
    """
    Calibrated Expected Multi-Season Dynasty VORP for un-drafted future rookie picks
    (and pre-draft flipped pick capital), scaled for Superflex QB depth and multi-year
    time discounting (`0.88^(years_out - 1)`).
    """
    tier = slot_tier if slot_tier in ("early", "mid", "late") else "mid"
    rnd = max(1, int(round_num))
    base_table = {
        1: {"early": 46.0, "mid": 32.0, "late": 21.0},
        2: {"early": 15.0, "mid": 11.0, "late": 8.0},
        3: {"early": 6.0, "mid": 4.5, "late": 3.0},
    }
    base = base_table.get(rnd, {"early": 2.0, "mid": 2.0, "late": 2.0})[tier]
    if is_superflex:
        sf_mult = 1.15 if rnd == 1 else (1.12 if rnd == 2 else 1.05)
        base *= sf_mult
    discount = 0.88 ** max(0, int(years_out) - 1)
    return round(base * discount, 1)


def estimate_faab_trade_vorp(faab_amount: int, season_budget: int = 100) -> Tuple[float, int]:
    """
    Calibrates net traded Sleeper FAAB (`$`) onto our VORP scale, normalized by the league's
    season `waiver_budget` (`$100`, `$200`, `$1000`, etc.) so `$25/$100` == `$250/$1000` (`25%`).
    Full 100% season waiver budget = +20.0 Expected VORP (so 25% FAAB = +5.0 VORP ~ Mid 3rd pick;
    10% FAAB = +2.0 VORP ~ 4th-round dart throw). Returns `(est_vorp, pct_of_budget_int)`.
    """
    amt = max(0, int(faab_amount or 0))
    bud = max(1, int(season_budget or 100))
    if amt <= 0:
        return 0.0, 0
    raw_ratio = amt / float(bud)
    pct_int = max(1, round(raw_ratio * 100))
    capped_ratio = min(2.0, raw_ratio)
    return round(capped_ratio * 20.0, 1), pct_int


def _stable_int_pid(raw_pid: str) -> int:
    """Maps Sleeper string player IDs (numeric `'4046'` or team `'PHI'`) to positive SQLite INTEGER IDs."""
    s = str(raw_pid or "").strip()
    if s.isdigit():
        return int(s)
    digest = hashlib.md5(s.encode("utf-8"), usedforsecurity=False).hexdigest()[:7]
    return 900_000_000 + int(digest, 16)


def _norm_pos(pos: str) -> str:
    p = str(pos or "").upper().strip()
    if p == "DEF":
        return "D/ST"
    if p in ("DE", "DT", "DL"):
        return "DL"
    if p in ("CB", "S", "FS", "SS", "DB"):
        return "DB"
    if p in ("ILB", "OLB", "MLB", "LB"):
        return "LB"
    if p in ("QB", "RB", "WR", "TE", "K", "D/ST"):
        return p
    return "FLEX"


def load_sleeper_players_directory() -> Dict[str, Tuple[str, str, str, str]]:
    """
    Returns `{sleeper_pid_str: (full_name, normalized_pos, pro_team, injury_status)}`.
    Caches a compact ~1.3 MB slim file locally for 24h per Sleeper API guidelines.
    """
    PLAYERS_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    if PLAYERS_CACHE_PATH.exists():
        age = time.time() - PLAYERS_CACHE_PATH.stat().st_mtime
        if age < PLAYERS_CACHE_TTL_SEC:
            try:
                raw = json.loads(PLAYERS_CACHE_PATH.read_text(encoding="utf-8"))
                return {k: (v[0], v[1], v[2], v[3] if len(v) > 3 else "ACTIVE") for k, v in raw.items()}
            except Exception:
                pass

    print("📥 Caching slimmed Sleeper NFL players directory (once per 24h)...")
    full_data = _sleeper_get("players/nfl", timeout=30.0) or {}
    slim = {}
    for pid, info in full_data.items():
        if not isinstance(info, dict):
            continue
        pos = _norm_pos(info.get("position") or (info.get("fantasy_positions") or [""])[0])
        team = str(info.get("team") or "FA")
        if info.get("position") == "DEF":
            fn = f"{info.get('first_name', '')} {info.get('last_name', '')}".strip() or f"{pid} D/ST"
            name = f"{fn} D/ST" if "D/ST" not in fn else fn
        else:
            name = (info.get("full_name") or f"{info.get('first_name', '')} {info.get('last_name', '')}").strip() or f"Player {pid}"
        inj = str(info.get("injury_status") or "ACTIVE").upper()
        slim[str(pid)] = [name, pos, team, inj]

    PLAYERS_CACHE_PATH.write_text(json.dumps(slim, separators=(",", ":")), encoding="utf-8")
    return {k: (v[0], v[1], v[2], v[3]) for k, v in slim.items()}


def _parse_sleeper_league_config(lg_raw: dict, users: list, rosters: list, drafts: list, slug: str) -> dict:
    settings = lg_raw.get("settings") or {}
    scoring = lg_raw.get("scoring_settings") or {}
    meta = lg_raw.get("metadata") or {}
    r_positions = lg_raw.get("roster_positions") or []

    league_id = str(lg_raw["league_id"])
    season = int(lg_raw.get("season") or 2026)
    league_name = (lg_raw.get("name") or f"Sleeper {league_id}").strip()
    team_count = int(lg_raw.get("total_rosters") or len(rosters) or 12)
    po_start = int(settings.get("playoff_week_start") or 15)
    reg_weeks = max(1, po_start - 1) if po_start > 1 else 14
    po_teams = int(settings.get("playoff_teams") or 6)
    po_byes = 2 if po_teams == 6 else (1 if po_teams in (5, 7) else 0)
    po_reseed = int(settings.get("playoff_seed_type") or 0) == 1
    po_round_weeks = 2 if int(settings.get("playoff_round_type") or 0) == 2 else 1
    uses_median = int(settings.get("league_average_match") or 0) == 1
    waiver_budget = max(1, int(settings.get("waiver_budget") or 100))

    rec_pts = float(scoring.get("rec", 0.0) or 0.0)
    tep = float(scoring.get("bonus_rec_te", 0.0) or 0.0)
    base_sc = "Full PPR" if rec_pts >= 0.9 else ("Half PPR" if rec_pts >= 0.4 else "Standard")
    scoring_label = f"{base_sc} + TEP" if tep > 0 else base_sc

    # Draft & League Format
    dtype = "SNAKE"
    if drafts:
        raw_dt = str((drafts[0].get("type") or "snake")).upper()
        if raw_dt in ("AUCTION", "LINEAR", "SNAKE"):
            dtype = raw_dt
    lg_type = int(settings.get("type") or 0)  # 0=Redraft, 1=Keeper, 2=Dynasty
    if lg_type == 2:
        format_label = "Dynasty"
    elif lg_type == 1:
        format_label = "Keeper"
    elif dtype == "AUCTION":
        format_label = "Auction"
    else:
        format_label = "Redraft"

    pos_cnt = Counter(r_positions)
    idp_flex = int(pos_cnt.get("IDP_FLEX", 0))
    roster_slots = {
        "QB": int(pos_cnt.get("QB", 1)),
        "RB": int(pos_cnt.get("RB", 2)),
        "WR": int(pos_cnt.get("WR", 2)),
        "TE": int(pos_cnt.get("TE", 1)),
        "FLEX": int(pos_cnt.get("FLEX", 0)) + int(pos_cnt.get("REC_FLEX", 0)) + int(pos_cnt.get("WRRB_FLEX", 0)),
        "OP": int(pos_cnt.get("SUPER_FLEX", 0)),
        "K": int(pos_cnt.get("K", 0)),
        "D/ST": int(pos_cnt.get("DEF", 0)),
        "DL": int(pos_cnt.get("DL", 0)) + (1 if idp_flex > 0 and pos_cnt.get("DL", 0) == 0 else 0),
        "LB": int(pos_cnt.get("LB", 0)) + (1 if idp_flex > 0 and pos_cnt.get("LB", 0) == 0 else 0),
        "DB": int(pos_cnt.get("DB", 0)),
    }
    active_positions = [p for p in ("QB", "RB", "WR", "TE") if roster_slots.get(p, 0) > 0]
    for opt_p in ("K", "D/ST", "DL", "LB", "DB"):
        if roster_slots.get(opt_p, 0) > 0:
            active_positions.append(opt_p)

    # Divisions
    n_divs = int(settings.get("divisions") or 0)
    has_divisions = n_divs > 1
    div_names = {i: (meta.get(f"division_{i}") or f"Division {i}").strip() for i in range(1, n_divs + 1)}

    # Users -> Rosters mapping (handles co-owners & orphan teams cleanly)
    user_map = {}
    user_team_name = {}
    for u in (users or []):
        uid = str(u.get("user_id") or "")
        disp = normalize_generic_owner_name(u.get("display_name") or u.get("username") or "")
        if uid and disp:
            user_map[uid] = disp
            tn = ((u.get("metadata") or {}).get("team_name") or "").strip()
            if tn:
                user_team_name[uid] = tn

    teams_by_id = {}
    divisions_map: Dict[str, List[str]] = {}
    used_owners: Counter = Counter()
    for r in sorted(rosters or [], key=lambda x: int(x.get("roster_id") or 0)):
        rid = int(r.get("roster_id") or 0)
        if rid <= 0:
            continue
        oid = str(r.get("owner_id") or "")
        base_own = user_map.get(oid) or f"Team {rid}"
        used_owners[base_own] += 1
        own = base_own if used_owners[base_own] == 1 else f"{base_own} ({rid})"
        co_ids = [oid] + [str(c) for c in (r.get("co_owners") or []) if c]
        co_list = dedupe_co_owners([own] + [user_map.get(cid, "") for cid in co_ids if cid in user_map])
        tname = ((r.get("metadata") or {}).get("team_name") or user_team_name.get(oid) or f"Team {own}").strip()
        div_id = int((r.get("settings") or {}).get("division") or 0)
        div_nm = div_names.get(div_id, f"Division {div_id}") if (has_divisions and div_id > 0) else ""
        teams_by_id[rid] = {
            "id": rid,
            "owner_id": oid,
            "name": tname,
            "owner": own,
            "co_owners": ", ".join(co_list),
            "division_id": div_id,
            "division": div_nm,
            "players": [str(p) for p in (r.get("players") or [])],
            "starters": [str(p) for p in (r.get("starters") or []) if p and str(p) != "0"],
        }
        if has_divisions and div_nm:
            divisions_map.setdefault(div_nm, []).append(own)

    return {
        "platform": "sleeper",
        "league_id": league_id,
        "season": season,
        "slug": slug,
        "name": league_name,
        "badge": make_league_badge(league_name, slug),
        "team_count": team_count,
        "regular_season_weeks": reg_weeks,
        "playoff_teams": po_teams,
        "playoff_byes": po_byes,
        "playoff_reseed": po_reseed,
        "playoff_round_weeks": po_round_weeks,
        "uses_league_median": uses_median,
        "waiver_budget": waiver_budget,
        "draft_type": dtype,
        "league_type": lg_type,
        "format_label": format_label,
        "tiebreaker_rule": "TOTAL_POINTS_SCORED",
        "has_divisions": has_divisions and bool(divisions_map),
        "divisions": divisions_map,
        "scoring_label": scoring_label,
        "roster_slots": roster_slots,
        "active_positions": active_positions,
        "replacement_ranks": compute_dynamic_replacement_ranks(team_count, roster_slots),
        "current_leg": int(settings.get("leg") or 1),
        "status": str(lg_raw.get("status") or "in_season"),
        "previous_league_id": str(lg_raw.get("previous_league_id") or "") if lg_raw.get("previous_league_id") else "",
        "teams_by_id": teams_by_id,
    }


def _discover_sleeper_history_chain(league_id: str, slug: str, max_years: int = 5) -> List[dict]:
    """Walks `previous_league_id` backwards to collect multi-season Sleeper league configs."""
    chain = []
    cur_lid = validate_league_id(league_id)
    seen = set()
    while cur_lid and cur_lid != "0" and cur_lid not in seen and len(chain) < max_years:
        seen.add(cur_lid)
        lg_raw = _sleeper_get(f"league/{cur_lid}")
        if not lg_raw:
            break
        if str(lg_raw.get("status")) == "pre_draft" and chain:
            break
        users = _sleeper_get(f"league/{cur_lid}/users") or []
        rosters = _sleeper_get(f"league/{cur_lid}/rosters") or []
        drafts = _sleeper_get(f"league/{cur_lid}/drafts") or []
        cfg = _parse_sleeper_league_config(lg_raw, users, rosters, drafts, slug)
        cfg["_drafts_raw"] = drafts
        cfg["_rosters_raw"] = rosters
        chain.append(cfg)
        prev = cfg.get("previous_league_id", "")
        cur_lid = prev if (prev and prev.isdigit()) else ""
    chain.sort(key=lambda c: c["season"])
    return chain


def detect_sleeper_active_week(league_id: str) -> int:
    lg = _sleeper_get(f"league/{validate_league_id(league_id)}") or {}
    leg = int((lg.get("settings") or {}).get("leg") or 0)
    if leg > 0:
        return leg
    st = _sleeper_get("state/nfl") or {}
    return int(st.get("week") or 1)


def fetch_sleeper_live_matchups(league_id: str, week: int) -> List[dict]:
    """Returns canonical live matchup snapshots for `live_event_ledger.py` (100% zero-auth!)."""
    lid = validate_league_id(league_id)
    lg = _sleeper_get(f"league/{lid}") or {}
    users = _sleeper_get(f"league/{lid}/users") or []
    rosters = _sleeper_get(f"league/{lid}/rosters") or []
    cfg = _parse_sleeper_league_config(lg, users, rosters, [], "live")
    tmap = cfg["teams_by_id"]
    pdir = load_sleeper_players_directory()

    # Estimate baseline starter PPG from completed weeks (1 .. max(1, week-1)) for live projections
    prev_wks = list(range(1, max(1, week)))
    p_hist: Dict[str, List[float]] = defaultdict(list)
    if prev_wks:
        with ThreadPoolExecutor(max_workers=6) as ex:
            for mlist in ex.map(lambda w: _sleeper_get(f"league/{lid}/matchups/{w}") or [], prev_wks):
                for m in mlist:
                    for spid, pts in (m.get("players_points") or {}).items():
                        v = float(pts or 0.0)
                        if v != 0.0:
                            p_hist[str(spid)].append(v)

    curr_raw = _sleeper_get(f"league/{lid}/matchups/{week}") or []
    by_mid = defaultdict(list)
    for m in curr_raw:
        mid = m.get("matchup_id")
        if mid is not None:
            by_mid[int(mid)].append(m)

    out = []
    for mid, pair in sorted(by_mid.items()):
        if len(pair) != 2:
            continue
        away_m, home_m = pair[0], pair[1]
        a_rid, h_rid = int(away_m["roster_id"]), int(home_m["roster_id"])
        if a_rid not in tmap or h_rid not in tmap:
            continue

        def _extract_side(m_entry):
            starters = [str(s) for s in (m_entry.get("starters") or []) if s and str(s) != "0"]
            s_pts_list = m_entry.get("starters_points") or []
            p_pts_map = m_entry.get("players_points") or {}
            st_out = []
            proj_sum = 0.0
            for idx, spid in enumerate(starters):
                pname, pos, _pro, inj = pdir.get(spid, (f"Player {spid}", "FLEX", "FA", "ACTIVE"))
                pts = float(s_pts_list[idx]) if idx < len(s_pts_list) and s_pts_list[idx] is not None else float(p_pts_map.get(spid, 0.0) or 0.0)
                hist = p_hist.get(spid, [])
                base_proj = round(statistics.mean(hist), 2) if hist else FALLBACK_REPLACEMENT_PPG.get(pos, 10.0)
                has_played = pts != 0.0
                eff_proj = max(pts, base_proj) if not has_played else max(pts, round(0.5 * pts + 0.5 * base_proj, 2))
                proj_sum += eff_proj
                st_out.append({
                    "id": _stable_int_pid(spid),
                    "name": pname,
                    "pos": pos,
                    "pts": round(pts, 2),
                    "proj": round(base_proj, 2),
                    "has_played": has_played,
                    "injury": inj,
                })
            live_tot = float(m_entry.get("points") or sum(x["pts"] for x in st_out) or 0.0)
            return live_tot, max(live_tot, round(proj_sum, 2)), st_out

        a_live, a_proj, a_st = _extract_side(away_m)
        h_live, h_proj, h_st = _extract_side(home_m)
        out.append({
            "matchup_key": f"{min(a_rid, h_rid)}_{max(a_rid, h_rid)}",
            "away_owner": tmap[a_rid]["owner"],
            "home_owner": tmap[h_rid]["owner"],
            "away_live": a_live,
            "home_live": h_live,
            "away_proj": a_proj,
            "home_proj": h_proj,
            "away_starters": a_st,
            "home_starters": h_st,
        })
    return out


def lookup_sleeper_user_leagues(username: str, season: int = 2026) -> dict:
    """
    Zero-auth lookup of a Sleeper user's NFL fantasy leagues for `season`.
    Strictly validates `username` against `SLEEPER_USERNAME_RE` before outbound call.
    """
    clean_user = validate_sleeper_username(username)
    u = _sleeper_get(f"user/{clean_user}", timeout=8.0)
    if not u or not u.get("user_id"):
        raise LookupError(f"Sleeper user '{clean_user}' not found.")
    uid = str(u["user_id"])
    raw_leagues = _sleeper_get(f"user/{uid}/leagues/nfl/{int(season)}", timeout=10.0) or []
    leagues_out = []
    for lg in raw_leagues:
        lid = str(lg.get("league_id") or "").strip()
        if not lid.isdigit():
            continue
        settings = lg.get("settings") or {}
        scoring = lg.get("scoring_settings") or {}
        rpos = [str(p).upper() for p in (lg.get("roster_positions") or [])]
        ltype = int(settings.get("type") or 0)
        fmt = "Dynasty" if ltype == 2 else ("Keeper" if ltype == 1 else "Redraft")
        rec = float(scoring.get("rec") or 0.0)
        sc_lbl = "Full PPR" if rec >= 0.9 else ("Half PPR" if rec >= 0.4 else "Standard")
        if "SUPER_FLEX" in rpos:
            sc_lbl += " • SF"
        if int(settings.get("league_average_match") or 0) == 1:
            sc_lbl += " • Median"
        leagues_out.append({
            "league_id": lid,
            "name": (lg.get("name") or f"Sleeper League {lid}").strip(),
            "season": int(lg.get("season") or season),
            "total_rosters": int(lg.get("total_rosters") or settings.get("num_teams") or 12),
            "format_label": fmt,
            "scoring_label": sc_lbl,
            "status": str(lg.get("status") or ""),
        })
    return {
        "username": u.get("username") or clean_user,
        "display_name": u.get("display_name") or clean_user,
        "season": int(season),
        "leagues": leagues_out,
    }


def build_sleeper_hub_data(
    league_id: str,
    slug: str,
    *,
    force_week: int = 0,
    max_history_years: int = 5,
    ephemeral: bool = False,
) -> Tuple[dict, Optional[Path]]:
    """
    Auto-discovers ANY Sleeper league (and its historical `previous_league_id` chain),
    syncs an isolated SQLite warehouse, and outputs a complete universal `hub_data.json`.
    When `ephemeral=True`, uses an auto-cleaned `TemporaryDirectory` so 0 bytes remain on disk.
    """
    import tempfile

    tmp_ctx = tempfile.TemporaryDirectory(prefix="frecap_sleeper_") if ephemeral else None
    try:
        return _build_sleeper_hub_data_impl(
            league_id,
            slug,
            force_week=force_week,
            max_history_years=max_history_years,
            ephemeral=ephemeral,
            tmp_dir=Path(tmp_ctx.name) if tmp_ctx else None,
        )
    finally:
        if tmp_ctx is not None:
            tmp_ctx.cleanup()


def _build_sleeper_hub_data_impl(
    league_id: str,
    slug: str,
    *,
    force_week: int = 0,
    max_history_years: int = 5,
    ephemeral: bool = False,
    tmp_dir: Optional[Path] = None,
) -> Tuple[dict, Optional[Path]]:
    lid = validate_league_id(league_id)
    slug = validate_slug(slug)
    pdir = load_sleeper_players_directory()

    print(f"🔍 Auto-discovering Sleeper League {lid} ({slug})...")
    chain = _discover_sleeper_history_chain(lid, slug, max_years=max_history_years)
    if not chain:
        raise LookupError(f"Sleeper league {lid} not found.")

    curr_cfg = chain[-1]
    curr_season = curr_cfg["season"]
    first_season = chain[0]["season"]
    curr_cfg["first_season"] = first_season
    curr_cfg["is_first_year"] = len(chain) == 1

    # 3-Tier Identity Resolution across seasons: separates replaced managers (Alumni),
    # unifies same-account handle/married-name changes, and flags ambiguous slot transitions!
    resolver = LeagueIdentityResolver(
        owner_aliases={} if ephemeral else _load_optional_owner_aliases(slug),
        name_normalizer=normalize_generic_owner_name,
    )
    for cfg in chain:
        is_c = (cfg["season"] == curr_season)
        for rid, t in cfg["teams_by_id"].items():
            raw_nm = t["owner"] if t.get("owner_id") else ""
            resolver.observe(cfg["season"], t.get("owner_id"), raw_nm, team_id=rid, team_name=t["name"], is_current_season=is_c)
    resolver.finalize()
    for cfg in chain:
        for rid, t in cfg["teams_by_id"].items():
            raw_nm = t["owner"] if t.get("owner_id") else ""
            t["owner"] = resolver.resolve(t.get("owner_id"), raw_nm, season=cfg["season"], team_id=rid)
        if cfg["has_divisions"]:
            new_divs: Dict[str, List[str]] = {}
            for t in cfg["teams_by_id"].values():
                if t["division"]:
                    new_divs.setdefault(t["division"], []).append(t["owner"])
            cfg["divisions"] = new_divs

    db_path = (tmp_dir / f"{slug}_sleeper_history.db") if tmp_dir else (LEAGUES_DB_DIR / f"{slug}_sleeper_history.db")
    if db_path.exists():
        db_path.unlink()
    conn = sqlite3.connect(db_path)
    init_db(conn)
    cur = conn.cursor()

    season_rules_by_yr: Dict[int, dict] = {}
    season_tmap_by_yr: Dict[int, dict] = {}
    championships_log: List[dict] = []
    champ_count: Counter = Counter()
    runner_up_count: Counter = Counter()
    champ_years_map: Dict[str, List[int]] = defaultdict(list)
    ru_years_map: Dict[str, List[int]] = defaultdict(list)
    curr_week_matchups_cache: Dict[int, list] = {}
    traded_picks_raw: Dict[str, Dict[int, List[dict]]] = defaultdict(lambda: defaultdict(list))
    traded_faab_raw: Dict[str, Dict[int, int]] = defaultdict(lambda: defaultdict(int))
    # Chronological pick ownership hops: (pick_season, round, orig_rid) -> [(yr, wk, ts, from_rid, to_rid, to_owner)]
    pick_transfers: Dict[Tuple[int, int, int], List[Tuple[int, int, int, int, int, str]]] = defaultdict(list)
    trade_ts_by_id: Dict[str, int] = {}
    draft_orig_slot_by_yr: Dict[int, Dict[int, int]] = {}
    draft_picks_by_yr_rnd_slot: Dict[int, Dict[Tuple[int, int], dict]] = defaultdict(dict)
    draft_picks_by_yr_rnd_tid: Dict[int, Dict[Tuple[int, int], List[dict]]] = defaultdict(lambda: defaultdict(list))
    player_transfers: Dict[int, List[Tuple[int, int, int, int, str]]] = defaultdict(list)

    for cfg in chain:
        yr = cfg["season"]
        ylid = cfg["league_id"]
        is_curr = (yr == curr_season)
        reg_wks = cfg["regular_season_weeks"]
        tmap = cfg["teams_by_id"]
        season_tmap_by_yr[yr] = tmap
        max_fetch_wk = reg_wks if is_curr else min(18, reg_wks + (6 if int(cfg.get("playoff_round_weeks") or 1) == 2 else 4))

        sf_tag = " • SF" if cfg["roster_slots"].get("OP", 0) > 0 else ""
        med_tag = " • Median" if cfg["uses_league_median"] else ""
        season_rules_by_yr[yr] = {
            "teams": cfg["team_count"],
            "scoring": cfg["scoring_label"],
            "draft_type": cfg["draft_type"],
            "format": cfg["format_label"],
            "superflex": cfg["roster_slots"].get("OP", 0) > 0,
            "waiver_budget": cfg.get("waiver_budget", 100),
            "repl_ranks": cfg["replacement_ranks"],
            "summary": f"{cfg['team_count']}T • {cfg['format_label']} ({cfg['draft_type'].title()}) • {cfg['scoring_label']}{sf_tag}{med_tag}",
        }

        with ThreadPoolExecutor(max_workers=8) as ex:
            wk_matchups = dict(zip(range(1, max_fetch_wk + 1), ex.map(lambda w: _sleeper_get(f"league/{ylid}/matchups/{w}") or [], range(1, max_fetch_wk + 1))))
            wk_txs = dict(zip(range(1, reg_wks + 2), ex.map(lambda w: _sleeper_get(f"league/{ylid}/transactions/{w}") or [], range(1, reg_wks + 2))))

        if is_curr:
            curr_week_matchups_cache = wk_matchups

        # Detect completed regular-season week for `yr`
        completed_wk_yr = 0
        nfl_leg = cfg["current_leg"]
        for wk in range(1, reg_wks + 1):
            if is_curr and cfg["status"] == "in_season" and nfl_leg > 0 and wk >= nfl_leg:
                # Check if all matchups in `leg` are only partially complete: treat `< leg` as completed!
                continue
            ms = wk_matchups.get(wk, [])
            pts_all = [float(x.get("points") or 0.0) for x in ms if x.get("matchup_id") is not None]
            if len(pts_all) >= 2 and (sum(pts_all) / len(pts_all)) >= 35.0 and not all(p == 0.0 for p in pts_all):
                completed_wk_yr = wk
        if is_curr and force_week > 0:
            completed_wk_yr = force_week
        if is_curr:
            curr_cfg["completed_week"] = completed_wk_yr

        # Check winners_bracket for completed past seasons to record true Champions & Runners-Up!
        wb = _sleeper_get(f"league/{ylid}/winners_bracket") or []
        champ_rid, ru_rid = 0, 0
        # Winners-bracket pairings (championship path only, excludes 3rd/5th place games) + exact final placements
        # from Sleeper `p` (1=Champ/2=RU, 3=3rd/4=4th, 5=5th/6=6th, ...) + every playoff team.
        bracket_pairs: set = set()
        playoff_rids: set = set()
        final_rank_by_rid: Dict[int, int] = {}
        max_po_round: Dict[int, int] = defaultdict(int)
        if not is_curr or cfg["status"] == "complete":
            for bm in wb:
                p_val = bm.get("p")
                if p_val == 1 and bm.get("w") and bm.get("l"):
                    champ_rid, ru_rid = int(bm["w"]), int(bm["l"])
                if isinstance(p_val, int) and p_val >= 1 and bm.get("w") and bm.get("l"):
                    final_rank_by_rid[int(bm["w"])] = p_val
                    final_rank_by_rid[int(bm["l"])] = p_val + 1
                rnd_num = int(bm.get("r") or 0)
                t1_raw, t2_raw = bm.get("t1"), bm.get("t2")
                t1 = int(t1_raw) if isinstance(t1_raw, (int, str)) and str(t1_raw).isdigit() else 0
                t2 = int(t2_raw) if isinstance(t2_raw, (int, str)) and str(t2_raw).isdigit() else 0
                if t1:
                    playoff_rids.add(t1)
                    max_po_round[t1] = max(max_po_round[t1], rnd_num)
                if t2:
                    playoff_rids.add(t2)
                    max_po_round[t2] = max(max_po_round[t2], rnd_num)
                if t1 and t2 and p_val in (None, 1):
                    bracket_pairs.add(frozenset((t1, t2)))

        # 1. Insert Games & Player Boxscores
        p_season_pts: Dict[int, float] = defaultdict(float)
        p_season_gp: Dict[int, int] = defaultdict(int)
        p_meta_local: Dict[int, Tuple[str, str, str]] = {}

        max_box_wk = completed_wk_yr if is_curr else max_fetch_wk
        for wk in range(1, max_box_wk + 1):
            ms = wk_matchups.get(wk, [])
            by_mid = defaultdict(list)
            for m in ms:
                if m.get("matchup_id") is not None:
                    by_mid[int(m["matchup_id"])].append(m)
            for mid, pair in by_mid.items():
                if len(pair) != 2:
                    continue
                m1, m2 = pair[0], pair[1]
                r1, r2 = int(m1["roster_id"]), int(m2["roster_id"])
                if r1 not in tmap or r2 not in tmap:
                    continue
                s1, s2 = float(m1.get("points") or 0.0), float(m2.get("points") or 0.0)
                if s1 == 0.0 and s2 == 0.0:
                    continue
                is_reg = 1 if wk <= reg_wks else 0
                in_bracket = (not bracket_pairs) or (frozenset((r1, r2)) in bracket_pairs)
                is_po = 1 if (wk > reg_wks and in_bracket) else 0
                is_cons = 1 if (wk > reg_wks and not in_bracket) else 0
                tier = "NONE" if is_reg else ("WINNERS_BRACKET" if is_po else "CONSOLATION")
                for (tid_a, sc_a, tid_b, sc_b, m_obj) in ((r1, s1, r2, s2, m1), (r2, s2, r1, s1, m2)):
                    ta, tb = tmap[tid_a], tmap[tid_b]
                    cur.execute(
                        "INSERT OR REPLACE INTO games VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (yr, wk, mid, tier, is_reg, is_po, is_cons, tid_a, ta["name"], ta["owner"], ta["owner"],
                         ta["co_owners"], sc_a, tid_b, tb["name"], tb["owner"], tb["owner"], sc_b,
                         1 if sc_a >= sc_b else 0, round(sc_a - sc_b, 2)),
                    )
                    starters_set = {str(s) for s in (m_obj.get("starters") or []) if s and str(s) != "0"}
                    pp_map = m_obj.get("players_points") or {}
                    for spid, pval in pp_map.items():
                        spid_s = str(spid)
                        pid_i = _stable_int_pid(spid_s)
                        pname, pos, pro, _inj = pdir.get(spid_s, (f"Player {spid_s}", "FLEX", "FA", "ACTIVE"))
                        p_meta_local[pid_i] = (pname, pos, pro)
                        pts = float(pval or 0.0)
                        is_st = 1 if spid_s in starters_set else 0
                        cur.execute(
                            "INSERT OR REPLACE INTO player_boxscores VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                            (yr, wk, mid, tid_a, ta["name"], ta["owner"], ta["co_owners"], tb["id"], tb["owner"],
                             tier, is_reg, is_po, is_cons, pid_i, pname, pro, pos, 0 if is_st else 20,
                             pos if is_st else "Bench", is_st, round(pts, 2), round(pts, 2)),
                        )
                        p_season_pts[pid_i] += pts
                        if pts != 0.0 or is_st:
                            p_season_gp[pid_i] += 1

        if champ_rid in tmap and ru_rid in tmap:
            c_own, r_own = tmap[champ_rid]["owner"], tmap[ru_rid]["owner"]
            champ_count[c_own] += 1
            runner_up_count[r_own] += 1
            champ_years_map[c_own].append(yr)
            ru_years_map[r_own].append(yr)
            championships_log.append({"year": yr, "champ": c_own, "runner_up": r_own, "score": "Playoff Bracket"})

        # 2. Team Seasons table (with exact final_rank 1..N from winners_bracket + regular-season standings & division titles)
        is_complete = 1 if (not is_curr or cfg["status"] == "complete") else 0
        roster_stats: List[Tuple[int, int, int, int, float, float]] = []
        for r_raw in (cfg.get("_rosters_raw") or []):
            rid = int(r_raw.get("roster_id") or 0)
            if rid not in tmap:
                continue
            rs = r_raw.get("settings") or {}
            w = int(rs.get("wins") or 0)
            l = int(rs.get("losses") or 0)
            ties = int(rs.get("ties") or 0)
            pf = float(rs.get("fpts") or 0.0) + float(rs.get("fpts_decimal") or 0.0) / 100.0
            pa = float(rs.get("fpts_against") or 0.0) + float(rs.get("fpts_against_decimal") or 0.0) / 100.0
            roster_stats.append((rid, w, l, ties, pf, pa))

        # Fill any unplaced playoff teams (when 3rd/5th place games are disabled) & all non-playoff teams
        if is_complete and roster_stats:
            used_ranks = set(final_rank_by_rid.values())
            unplaced_po = [x for x in roster_stats if x[0] in playoff_rids and x[0] not in final_rank_by_rid]
            unplaced_po.sort(key=lambda x: (-max_po_round.get(x[0], 0), -x[1], -x[4]))
            unplaced_non = [x for x in roster_stats if x[0] not in playoff_rids and x[0] not in final_rank_by_rid]
            unplaced_non.sort(key=lambda x: (-x[1], -x[4]))
            avail_ranks = [rk for rk in range(1, len(roster_stats) + 1) if rk not in used_ranks]
            for x, rk in zip(unplaced_po + unplaced_non, avail_ranks):
                final_rank_by_rid[x[0]] = rk

        # Detect division winners for completed seasons with divisions
        div_winner_rids: set = set()
        if is_complete and cfg.get("has_divisions"):
            by_div_rs: Dict[int, List[Tuple[int, int, float]]] = defaultdict(list)
            for rid, w, _l, _t, pf, _pa in roster_stats:
                did_v = int(tmap[rid].get("division_id") or 0)
                if did_v > 0:
                    by_div_rs[did_v].append((rid, w, pf))
            for _did_v, members in by_div_rs.items():
                if members:
                    members.sort(key=lambda x: (-x[1], -x[2]))
                    div_winner_rids.add(members[0][0])

        for rid, w, l, ties, pf, pa in roster_stats:
            ti = tmap[rid]
            gp = max(1, w + l + ties)
            won_ch = 1 if rid == champ_rid else 0
            ru_ch = 1 if rid == ru_rid else 0
            f_rk = int(final_rank_by_rid.get(rid, 0)) if is_complete else 0
            won_div = 1 if rid in div_winner_rids else 0
            cur.execute(
                "INSERT OR REPLACE INTO team_seasons VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (yr, rid, ti["name"], ti["owner"], ti["owner"], ti["co_owners"], ti["division_id"], ti["division"],
                 w, l, ties, round(w / gp, 4), round(pf, 2), round(pa, 2), 0,
                 0, 0, 0, 0, f_rk, 1 if (won_ch or ru_ch or rid in playoff_rids) else 0, won_div, won_ch, ru_ch, is_complete),
            )

        # 3. Draft Picks (Snake, Auction `$`, Linear Rookie, Keepers + Traded-Slot Indexing)
        drafts_list = cfg.get("_drafts_raw") or []
        if drafts_list:
            d0 = drafts_list[0]
            did = str(d0.get("draft_id") or "")
            s2r_raw = d0.get("slot_to_roster_id")
            if not s2r_raw and did:
                dfull = _sleeper_get(f"draft/{did}") or {}
                s2r_raw = dfull.get("slot_to_roster_id")
            if isinstance(s2r_raw, dict):
                draft_orig_slot_by_yr[yr] = {
                    int(orig_rid): int(slot_k)
                    for slot_k, orig_rid in s2r_raw.items()
                    if str(slot_k).isdigit() and orig_rid is not None
                }
            picks = _sleeper_get(f"draft/{did}/picks") or [] if did else []
            if picks:
                pos_draft_cnt: Counter = Counter()
                by_pos_pts: Dict[str, List[Tuple[float, int]]] = defaultdict(list)
                parsed_p = []
                n_teams_d = max(1, int(cfg["team_count"]))
                for p in sorted(picks, key=lambda x: int(x.get("pick_no") or 0)):
                    rid = int(p.get("roster_id") or 0)
                    spid = str(p.get("player_id") or "")
                    if rid not in tmap or not spid:
                        continue
                    pid_i = _stable_int_pid(spid)
                    md = p.get("metadata") or {}
                    pdir_entry = pdir.get(spid)
                    pname = (f"{md.get('first_name', '')} {md.get('last_name', '')}".strip() or (pdir_entry[0] if pdir_entry else f"Player {spid}"))
                    pos = _norm_pos(md.get("position") or (pdir_entry[1] if pdir_entry else "FLEX"))
                    pro = str(md.get("team") or (pdir_entry[2] if pdir_entry else "FA"))
                    bid = int(float(md.get("amount") or 0))
                    is_k = 1 if p.get("is_keeper") else 0
                    spts = round(p_season_pts.get(pid_i, 0.0), 2)
                    by_pos_pts[pos].append((spts, pid_i))
                    opick = int(p.get("pick_no") or 0)
                    rnd = int(p.get("round") or 1)
                    dslot = int(p.get("draft_slot") or 1)
                    parsed_p.append((opick, rnd, dslot, rid, pid_i, pname, pro, pos, bid, is_k, spts))
                    p_info = {
                        "pick_no": opick,
                        "round": rnd,
                        "draft_slot": dslot,
                        "pick_in_round": ((opick - 1) % n_teams_d) + 1 if opick > 0 else dslot,
                        "drafted_by_tid": rid,
                        "drafted_by_owner": tmap[rid]["owner"],
                        "pid": pid_i,
                        "name": pname,
                        "pos": pos,
                    }
                    draft_picks_by_yr_rnd_slot[yr][(rnd, dslot)] = p_info
                    draft_picks_by_yr_rnd_tid[yr][(rnd, rid)].append(p_info)
                pfr_map = {}
                for pos, plist in by_pos_pts.items():
                    plist.sort(key=lambda x: -x[0])
                    for idx_r, (_v, pid_i) in enumerate(plist, 1):
                        pfr_map[(pos, pid_i)] = idx_r
                for opick, rnd, rpick, rid, pid_i, pname, pro, pos, bid, is_k, spts in parsed_p:
                    pos_draft_cnt[pos] += 1
                    pdr = pos_draft_cnt[pos]
                    pfr = pfr_map.get((pos, pid_i), 0)
                    ti = tmap[rid]
                    gp = max(1, p_season_gp.get(pid_i, 1))
                    cur.execute(
                        "INSERT OR REPLACE INTO draft_picks VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (yr, opick, rnd, rpick, rid, ti["name"], ti["owner"], ti["co_owners"],
                         pid_i, pname, pro, pos, pdr, bid, is_k, 0, spts, round(spts / gp, 2),
                         0, pfr, 0, pdr - pfr, 0.0, 0.0, 0.0, 0, 1),
                    )

        # 4. Trades (Players + Dynasty Realized & Future Draft Picks!)
        for twk, tx_list in sorted(wk_txs.items()):
            for tx in tx_list:
                if tx.get("type") != "trade" or tx.get("status") != "complete":
                    continue
                rids = [int(x) for x in (tx.get("roster_ids") or []) if int(x) in tmap]
                if len(rids) != 2:
                    continue
                ta, tb = rids[0], rids[1]
                tx_id = f"{yr}_w{twk:02d}_{tx.get('transaction_id', '')[:10]}"
                adds = tx.get("adds") or {}
                dp_swaps = tx.get("draft_picks") or []
                wb_swaps = tx.get("waiver_budget") or []
                raw_faab_a = sum(
                    max(0, int(wb.get("amount") or 0))
                    for wb in wb_swaps
                    if int(wb.get("receiver") or 0) == ta and int(wb.get("sender") or 0) == tb
                )
                raw_faab_b = sum(
                    max(0, int(wb.get("amount") or 0))
                    for wb in wb_swaps
                    if int(wb.get("receiver") or 0) == tb and int(wb.get("sender") or 0) == ta
                )
                net_faab_a = max(0, raw_faab_a - raw_faab_b)
                net_faab_b = max(0, raw_faab_b - raw_faab_a)
                if not adds and not dp_swaps and net_faab_a == 0 and net_faab_b == 0:
                    continue
                cur.execute(
                    "INSERT OR REPLACE INTO trades VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (tx_id, yr, twk, twk, ta, tmap[ta]["owner"], tb, tmap[tb]["owner"], int(tx.get("status_updated") or 0), "sleeper_tx"),
                )
                trade_ts_by_id[tx_id] = int(tx.get("status_updated") or 0)
                if net_faab_a > 0:
                    traded_faab_raw[tx_id][ta] = net_faab_a
                if net_faab_b > 0:
                    traded_faab_raw[tx_id][tb] = net_faab_b
                for spid, to_rid in adds.items():
                    to_i = int(to_rid)
                    if to_i not in (ta, tb):
                        continue
                    fr_i = tb if to_i == ta else ta
                    pid_i = _stable_int_pid(str(spid))
                    pname, pos, _pro, _inj = pdir.get(str(spid), (f"Player {spid}", "FLEX", "FA", "ACTIVE"))
                    cur.execute(
                        "INSERT OR REPLACE INTO trade_items VALUES (?,?,?,?,?,?,?,?,?)",
                        (tx_id, yr, fr_i, tmap[fr_i]["owner"], to_i, tmap[to_i]["owner"], pid_i, pname, pos),
                    )
                    player_transfers[pid_i].append((yr, twk, fr_i, to_i, tmap[to_i]["owner"]))
                for dp in dp_swaps:
                    to_i = int(dp.get("owner_id") or 0)
                    if to_i in (ta, tb):
                        pk_season = int(dp.get("season") or yr)
                        pk_round = int(dp.get("round") or 1)
                        pk_orig = int(dp.get("roster_id") or 0)
                        traded_picks_raw[tx_id][to_i].append({
                            "season": pk_season,
                            "round": pk_round,
                            "orig_rid": pk_orig,
                            "to_rid": to_i,
                        })
                        fr_pk = int(dp.get("previous_owner_id") or 0) or (tb if to_i == ta else ta)
                        pick_transfers[(pk_season, pk_round, pk_orig)].append(
                            (yr, twk, trade_ts_by_id.get(tx_id, 0), fr_pk, to_i, tmap[to_i]["owner"])
                        )

        conn.commit()
        print(f"   ✅ Synced Sleeper {cfg['name']} ({yr}): {completed_wk_yr} completed wks, format={season_rules_by_yr[yr]['summary']}")

    # Assemble universal `hub_data.json` for `curr_cfg`
    tmap = curr_cfg["teams_by_id"]
    owners = [t["owner"] for t in tmap.values()]
    reg_wks = curr_cfg["regular_season_weeks"]
    completed_week = curr_cfg["completed_week"]
    repl_ranks = curr_cfg["replacement_ranks"]

    completed_games, full_schedule = [], []
    scores_by_own = defaultdict(list)
    for wk in range(1, reg_wks + 1):
        ms = curr_week_matchups_cache.get(wk, [])
        by_mid = defaultdict(list)
        for m in ms:
            if m.get("matchup_id") is not None:
                by_mid[int(m["matchup_id"])].append(m)
        for _mid, pair in sorted(by_mid.items()):
            if len(pair) != 2:
                continue
            r1, r2 = int(pair[0]["roster_id"]), int(pair[1]["roster_id"])
            if r1 not in tmap or r2 not in tmap:
                continue
            a_own, h_own = tmap[r1]["owner"], tmap[r2]["owner"]
            full_schedule.append({"week": wk, "away": a_own, "home": h_own})
            if wk <= completed_week:
                sa, sh = float(pair[0].get("points") or 0.0), float(pair[1].get("points") or 0.0)
                completed_games.append({"week": wk, "away": a_own, "home": h_own, "away_score": sa, "home_score": sh})
                scores_by_own[a_own].append(sa)
                scores_by_own[h_own].append(sh)

    all_pts = [p for plist in scores_by_own.values() for p in plist]
    lg_avg = statistics.mean(all_pts) if all_pts else 115.0
    team_power = {}
    for o in owners:
        pl = scores_by_own[o]
        raw_m = statistics.mean(pl) if pl else lg_avg
        reg_m = (sum(pl) + 4.0 * lg_avg) / (len(pl) + 4.0)
        std_v = max(12.0, min(28.0, statistics.stdev(pl) if len(pl) >= 3 else 18.0))
        team_power[o] = {"mean": round(reg_m, 2), "std": round(std_v, 2), "raw_avg": round(raw_m, 2)}

    # Build live mid-week projections for Week `completed_week + 1` + season-avg optimal projections for future weeks
    weekly_proj = {}
    live_wk = completed_week + 1
    if live_wk <= reg_wks:
        live_snaps = fetch_sleeper_live_matchups(curr_cfg["league_id"], live_wk)
        any_live_pts = any((s["away_live"] + s["home_live"]) > 0 for s in live_snaps)
        wk_map = {}
        for s in live_snaps:
            for own, lpts, lproj in ((s["away_owner"], s["away_live"], s["away_proj"]), (s["home_owner"], s["home_live"], s["home_proj"])):
                rem = max(0.0, lproj - lpts)
                rfrac = max(0.05, min(1.0, rem / max(1.0, lproj))) if any_live_pts else 1.0
                wk_map[own] = {
                    "optimal_proj": round(lproj, 2),
                    "live_proj": round(lproj, 2),
                    "fa_fills": [],
                    "is_live": any_live_pts,
                    "live_pts": round(lpts, 2),
                    "rem_frac": round(rfrac, 3),
                }
        if wk_map:
            weekly_proj[live_wk] = wk_map
    for fwk in range(completed_week + 2, reg_wks + 1):
        weekly_proj[fwk] = {
            o: {"optimal_proj": team_power[o]["mean"], "live_proj": team_power[o]["mean"], "fa_fills": [], "is_live": False, "live_pts": 0.0, "rem_frac": 1.0}
            for o in owners
        }

    baseline_odds = run_generic_playoff_simulation(
        curr_cfg, completed_games, full_schedule, team_power, weekly_projections=weekly_proj, n_sims=4000
    )

    # Weekly Awards for `completed_week`
    awards_data = {"week": completed_week, "season": curr_season, "league_avg": round(lg_avg, 1), "awards": []}
    if completed_week >= 1:
        ms_aw = curr_week_matchups_cache.get(completed_week, [])
        by_mid_aw = defaultdict(list)
        for m in ms_aw:
            if m.get("matchup_id") is not None:
                by_mid_aw[int(m["matchup_id"])].append(m)
        m_list_aw, perfs_aw = [], {}
        for _mid, pair in by_mid_aw.items():
            if len(pair) != 2:
                continue
            m1, m2 = pair[0], pair[1]
            r1, r2 = int(m1["roster_id"]), int(m2["roster_id"])
            if r1 not in tmap or r2 not in tmap:
                continue
            s1, s2 = float(m1.get("points") or 0.0), float(m2.get("points") or 0.0)
            w_r, l_r = (r1, r2) if s1 >= s2 else (r2, r1)
            m_list_aw.append({
                "winner": {"id": w_r, "owner": tmap[w_r]["owner"], "name": tmap[w_r]["name"]},
                "loser": {"id": l_r, "owner": tmap[l_r]["owner"], "name": tmap[l_r]["name"]},
                "away_score": s1, "home_score": s2, "margin": round(abs(s1 - s2), 2),
            })
            for m_obj, rid in ((m1, r1), (m2, r2)):
                st_ids = [str(s) for s in (m_obj.get("starters") or []) if s and str(s) != "0"]
                pp = m_obj.get("players_points") or {}
                starters = []
                for spid in st_ids:
                    pnm, pos, _pr, _ij = pdir.get(spid, (f"Player {spid}", "FLEX", "FA", "ACTIVE"))
                    pts = float(pp.get(spid, 0.0) or 0.0)
                    starters.append({"name": pnm, "pos": pos, "pts": pts, "proj": FALLBACK_REPLACEMENT_PPG.get(pos, 10.0)})
                perfs_aw[rid] = {"starters": starters, "blunders": []}
        if m_list_aw:
            awards_data = compute_weekly_awards(m_list_aw, perfs_aw, week_num=completed_week, season=curr_season, playoff_odds=baseline_odds)

    # Career + H2H
    g_rows = cur.execute("SELECT season, week, owner, opp_owner, is_playoffs, score, opp_score, win, margin FROM games WHERE is_regular = 1 OR is_playoffs = 1 ORDER BY season, week").fetchall()
    career_agg = {}
    pair_games = defaultdict(list)
    for s_yr, wk, own, opp, is_po, sc, osc, win, margin in g_rows:
        sc_f, osc_f = float(sc), float(osc)
        ca = career_agg.setdefault(own, {"w": 0, "l": 0, "pw": 0, "pl": 0, "pf": 0.0, "pa": 0.0, "hi": None, "lo": None, "seasons": set(), "first": int(s_yr)})
        ca["w"] += int(win); ca["l"] += 1 - int(win)
        if int(is_po): ca["pw"] += int(win); ca["pl"] += 1 - int(win)
        ca["pf"] += sc_f; ca["pa"] += osc_f
        if ca["hi"] is None or sc_f > ca["hi"][0]:
            ca["hi"] = (sc_f, int(s_yr), int(wk))
        if sc_f > 0 and (ca["lo"] is None or sc_f < ca["lo"][0]):
            ca["lo"] = (sc_f, int(s_yr), int(wk))
        ca["seasons"].add(int(s_yr)); ca["first"] = min(ca["first"], int(s_yr))
        if own < opp:
            pair_games[f"{own}|||{opp}"].append({"season": int(s_yr), "week": int(wk), "is_playoffs": bool(is_po), "s1": round(sc_f, 2), "s2": round(osc_f, 2), "w1": int(win) == 1, "margin": round(abs(float(margin)), 2)})

    owner_div = {t["owner"]: t["division"] for t in tmap.values()}
    owner_tname = {t["owner"]: t["name"] for t in tmap.values()}
    career = []
    for own, ca in career_agg.items():
        gp = max(1, ca["w"] + ca["l"])
        career.append({
            "owner": own, "code": own.split()[0], "division": owner_div.get(own, ""), "team_name": owner_tname.get(own, ""),
            "is_active": own in owner_tname, "former_names": resolver.former_names_for(own),
            "seasons": len(ca["seasons"]), "first_season": ca["first"], "wins": ca["w"], "losses": ca["l"],
            "win_pct": round((ca["w"] / gp) * 100.0, 1), "po_wins": ca["pw"], "po_losses": ca["pl"],
            "avg_pf": round(ca["pf"] / gp, 2), "avg_pa": round(ca["pa"] / gp, 2), "high_score": round(ca["hi"][0], 2) if ca["hi"] else 0.0,
            "high_game": {"score": round(ca["hi"][0], 2), "season": ca["hi"][1], "week": ca["hi"][2]} if ca["hi"] else None,
            "low_game": {"score": round(ca["lo"][0], 2), "season": ca["lo"][1], "week": ca["lo"][2]} if ca["lo"] else None,
            "rings": champ_count[own], "champ_years": sorted(champ_years_map[own]), "runner_up_years": sorted(ru_years_map[own]),
        })
    career.sort(key=lambda x: (-x["rings"], -x["win_pct"], -x["avg_pf"]))

    h2h_summary = {}
    for key, glist in pair_games.items():
        o1, o2 = key.split("|||")
        w1 = sum(1 for g in glist if g["w1"]); w2 = len(glist) - w1
        po1 = sum(1 for g in glist if g["is_playoffs"] and g["w1"])
        po2 = sum(1 for g in glist if g["is_playoffs"] and not g["w1"])
        st_w = o1 if glist[-1]["w1"] else o2
        st_n = 0
        for g in reversed(glist):
            if g["w1"] == glist[-1]["w1"]: st_n += 1
            else: break
        closest = min(glist, key=lambda g: g["margin"])
        last_g = glist[-1]
        h2h_summary[key] = {
            "o1": o1, "o2": o2, "w1": w1, "w2": w2, "games": len(glist), "po1": po1, "po2": po2,
            "avg1": round(sum(g["s1"] for g in glist) / len(glist), 1), "avg2": round(sum(g["s2"] for g in glist) / len(glist), 1),
            "streak_owner": st_w, "streak_len": st_n,
            "last": {"season": last_g["season"], "week": last_g["week"], "winner": o1 if last_g["w1"] else o2, "hi": max(last_g["s1"], last_g["s2"]), "lo": min(last_g["s1"], last_g["s2"])},
            "closest": {"season": closest["season"], "week": closest["week"], "winner": o1 if closest["w1"] else o2, "margin": closest["margin"]},
            "super_bowls": [sb for sb in championships_log if {sb["champ"], sb["runner_up"]} == {o1, o2}],
        }

    h2h_ext = compute_h2h_extremes(h2h_summary)

    # Draft Hall (Per-Season Snake / Auction / Linear + Keeper Roll-Up & Multi-Year Dynasty Career!)
    dp_rows = cur.execute(
        "SELECT season, overall_pick, round_id, owner, player_name, position, pos_draft_rank, pos_finish_rank, season_points, bid_amount, keeper FROM draft_picks WHERE position IN ('QB','RB','WR','TE')"
    ).fetchall()
    ps_totals = cur.execute(
        """
        SELECT season, player_name, position, SUM(pts) FROM (
            SELECT season, week, player_id, MAX(player_name) AS player_name, MAX(position) AS position, MAX(points) AS pts
            FROM player_boxscores WHERE is_consolation = 0 AND position IN ('QB','RB','WR','TE')
            GROUP BY season, week, player_id
        ) GROUP BY season, player_id
        """
    ).fetchall()
    season_dtypes = {yr: info["draft_type"] for yr, info in season_rules_by_yr.items()}
    season_rranks = {yr: info["repl_ranks"] for yr, info in season_rules_by_yr.items()}
    drafts, draft_seasons, scored_dp = score_draft_picks_multi_format(
        dp_rows, repl_ranks, curr_cfg["team_count"],
        draft_type=curr_cfg["draft_type"], season_draft_types=season_dtypes, season_repl_ranks=season_rranks,
        league_format=curr_cfg["format_label"],
        player_season_totals=ps_totals,
        current_season=curr_season,
        is_current_season_complete=(curr_cfg["status"] == "complete"),
    )

    # All-Play Schedule Luck + PF+/PA+
    by_sw = defaultdict(list)
    for r in cur.execute("SELECT season, week, owner, score, opp_score, win FROM games WHERE is_regular = 1").fetchall():
        by_sw[(int(r[0]), int(r[1]))].append((r[2], float(r[3]), float(r[4]), int(r[5])))
    s_scores = defaultdict(list)
    valid_w = []
    for (s_yr, wk), ents in by_sw.items():
        if len(ents) < 2: continue
        scs = [e[1] for e in ents]
        if (sum(scs) / len(scs)) < 35.0 or all(e[1] == 0.0 for e in ents): continue
        valid_w.append((s_yr, wk, ents, scs))
        s_scores[s_yr].extend(scs)
    s_avg = {y: sum(v) / len(v) for y, v in s_scores.items() if v}

    def _calc_luck(filter_yr: Optional[int]):
        st = {}
        for s_yr, _wk, ents, scs in valid_w:
            if filter_yr is not None and s_yr != filter_yr: continue
            sm = s_avg.get(s_yr, 100.0)
            n_t = len(ents)
            for own, sc, osc, win in ents:
                d = st.setdefault(own, {"owner": own, "division": owner_div.get(own, ""), "g": 0, "w": 0, "l": 0, "apw": 0.0, "apg": 0, "pf": 0.0, "pa": 0.0, "pfp": 0.0, "pap": 0.0})
                beat = sum(1 for x in scs if sc > x); tied = sum(1 for x in scs if sc == x) - 1
                d["g"] += 1; d["w"] += win; d["l"] += 1 - win
                d["apw"] += beat + 0.5 * max(0, tied); d["apg"] += (n_t - 1)
                d["pf"] += sc; d["pa"] += osc; d["pfp"] += 100.0 * sc / sm; d["pap"] += 100.0 * osc / sm
        rows = []
        for own, d in st.items():
            gp = max(1, d["g"]); ap_pct = d["apw"] / max(1, d["apg"]); exp_w = ap_pct * d["g"]; ld = d["w"] - exp_w
            v = "Schedule Blessed" if ld >= 1.5 else ("Slightly Lucky" if ld >= 0.7 else ("Schedule Cursed" if ld <= -1.5 else ("Unlucky" if ld <= -0.7 else "Fair Schedule")))
            rows.append({
                "owner": own, "division": d["division"], "games": d["g"], "wins": d["w"], "losses": d["l"],
                "win_pct": round((d["w"] / gp) * 100.0, 1), "ap_wins": round(d["apw"], 1), "ap_losses": round(d["apg"] - d["apw"], 1),
                "ap_pct": round(ap_pct * 100.0, 1), "expected_wins": round(exp_w, 2), "luck_delta": round(ld, 2),
                "avg_pf": round(d["pf"] / gp, 1), "avg_pa": round(d["pa"] / gp, 1),
                "pf_plus": round(d["pfp"] / gp, 1), "pa_plus": round(d["pap"] / gp, 1), "verdict": v,
            })
        for idx, r in enumerate(sorted(rows, key=lambda x: -x["pa_plus"]), 1): r["sos_rank"] = idx
        rows.sort(key=lambda x: (-x["ap_pct"], -x["avg_pf"]))
        for idx, r in enumerate(rows, 1): r["true_rank"] = idx
        return rows

    luck_curr = _calc_luck(curr_season)
    luck_all = _calc_luck(None)

    # Weekly Trends
    weeks_list = list(range(1, max(1, completed_week) + 1))
    series = {o: {"po": [], "rank": [], "pf": [], "wins": [], "ap_pct": []} for o in owners}
    cum_sc, cum_w, cum_apw, cum_apg = {o: [] for o in owners}, Counter(), defaultdict(float), Counter()
    by_wk_g = defaultdict(list)
    for g in completed_games:
        by_wk_g[int(g["week"])].append(g)
    for wk in weeks_list:
        wk_sc = {}
        for g in by_wk_g.get(wk, []):
            a, h, sa, sh = g["away"], g["home"], float(g["away_score"]), float(g["home_score"])
            wk_sc[a] = sa; wk_sc[h] = sh
            cum_sc[a].append(sa); cum_sc[h].append(sh)
            if sa >= sh: cum_w[a] += 1
            else: cum_w[h] += 1
        vals = list(wk_sc.values())
        if len(vals) >= 2:
            for o, sc in wk_sc.items():
                cum_apw[o] += sum(1 for x in vals if sc > x) + 0.5 * max(0, sum(1 for x in vals if sc == x) - 1)
                cum_apg[o] += (len(vals) - 1)
        flat = [p for pl in cum_sc.values() for p in pl]
        l_avg = statistics.mean(flat) if flat else 115.0
        pwr_w = {
            o: {"mean": round((sum(cum_sc[o]) + 10.0 * l_avg) / (len(cum_sc[o]) + 10.0), 2),
                "std": round(max(12.0, min(28.0, statistics.stdev(cum_sc[o]) if len(cum_sc[o]) >= 4 else 18.0)), 2)}
            for o in owners
        }
        odds_w = baseline_odds if wk == completed_week else run_generic_playoff_simulation(
            curr_cfg, [g for g in completed_games if int(g["week"]) <= wk], full_schedule, pwr_w, n_sims=500, seed=curr_season * 100 + wk
        )
        rt = sorted(
            [(o, 0.55 * (cum_apw[o] / max(1, cum_apg[o])) + 0.35 * (cum_w[o] / max(1, len(cum_sc[o]))) + 0.10 * (sum(cum_sc[o]) / max(1.0, l_avg * wk)), sum(cum_sc[o])) for o in owners],
            key=lambda x: (-x[1], -x[2]),
        )
        rmap = {o: idx for idx, (o, _, _) in enumerate(rt, 1)}
        for o in owners:
            series[o]["po"].append(odds_w[o]["playoff_pct"])
            series[o]["rank"].append(rmap[o])
            series[o]["pf"].append(round(sum(cum_sc[o]), 1))
            series[o]["wins"].append(cum_w[o])
            series[o]["ap_pct"].append(round((cum_apw[o] / max(1, cum_apg[o])) * 100.0, 1))

    # Positional Strength Heatmap
    active_pos = curr_cfg["active_positions"]
    baselines = _compute_generic_repl_baselines(conn, curr_season, repl_ranks)
    pb_rows = cur.execute(
        "SELECT owner, position, week, points FROM player_boxscores WHERE season = ? AND is_starter = 1 AND week <= ?",
        (curr_season, completed_week),
    ).fetchall()
    own_wks, pos_agg = defaultdict(set), defaultdict(list)
    for own, pos, wk, pts in pb_rows:
        own_wks[own].add(int(wk))
        pos_agg[(own, pos)].append(float(pts or 0.0))
    hm_rows, pos_lists = [], {p: [] for p in active_pos}
    for o in owners:
        nw = max(1, len(own_wks.get(o, set())))
        pd_map, tot_v, sk = {}, 0.0, []
        for p in active_pos:
            vs = pos_agg.get((o, p), [])
            rp = baselines.get(p, 8.0)
            tv = sum(x - rp for x in vs)
            vw = round(tv / nw, 1)
            tot_v += tv
            pos_lists[p].append(vw)
            pd_map[p] = {"starts": len(vs), "ppg": round(sum(vs) / len(vs), 1) if vs else 0.0, "vorp_wk": vw, "total_vorp": round(tv, 1)}
            if p in ("QB", "RB", "WR", "TE"): sk.append((p, vw))
        hm_rows.append({
            "owner": o, "division": owner_div.get(o, ""), "weeks": nw,
            "total_vorp_wk": round(tot_v / nw, 1), "total_vorp": round(tot_v, 1),
            "best_pos": max(sk, key=lambda x: x[1])[0] if sk else "QB",
            "worst_pos": min(sk, key=lambda x: x[1])[0] if sk else "TE",
            "positions": pd_map,
        })
    hm_rows.sort(key=lambda r: -r["total_vorp_wk"])
    hm_bounds = {p: {"min": min(pos_lists[p] or [0.0]), "max": max(pos_lists[p] or [0.0])} for p in active_pos}

    # Dual-Lens Trade Vault (Multi-Season Dynasty Horizon + Realized Rookie Pick Resolver + Future Pick VORP!)
    trade_rows = cur.execute("SELECT trade_id, season, trade_week, effective_week, team_a_id, owner_a, team_b_id, owner_b FROM trades ORDER BY season DESC, trade_week DESC").fetchall()
    all_synced_seasons = sorted(season_rules_by_yr.keys())
    baselines_by_yr = {
        s_yr: (baselines if s_yr == curr_season else _compute_generic_repl_baselines(conn, s_yr, season_rranks.get(s_yr, repl_ranks)))
        for s_yr in all_synced_seasons
    }
    titems_by_id = defaultdict(list)
    for r in cur.execute("SELECT trade_id, season, from_team_id, from_owner, to_team_id, to_owner, player_id, player_name, position FROM trade_items").fetchall():
        titems_by_id[r[0]].append({"from_tid": int(r[2]), "to_tid": int(r[4]), "pid": int(r[6]), "name": r[7], "pos": r[8] or "FLEX"})
    pb_all = defaultdict(list)
    for r in cur.execute("SELECT season, week, team_id, player_id, is_starter, is_playoffs, points FROM player_boxscores ORDER BY season, week").fetchall():
        pb_all[(int(r[0]), int(r[3]))].append({"wk": int(r[1]), "tid": int(r[2]), "st": int(r[4]) == 1, "po": int(r[5]) == 1, "pts": float(r[6] or 0.0)})

    for pid_k in player_transfers:
        player_transfers[pid_k].sort(key=lambda x: (x[0], x[1]))

    # Projected rookie draft order (1 = worst current team -> Early pick; N = best current team -> Late pick)
    standings_for_picks = []
    for r_raw in (curr_cfg.get("_rosters_raw") or []):
        rid_p = int(r_raw.get("roster_id") or 0)
        rs_p = r_raw.get("settings") or {}
        w_p = int(rs_p.get("wins") or 0)
        pf_p = float(rs_p.get("fpts") or 0.0) + float(rs_p.get("fpts_decimal") or 0.0) / 100.0
        if rid_p in tmap:
            standings_for_picks.append((w_p, pf_p, rid_p))
    standings_for_picks.sort(key=lambda x: (x[0], x[1]))
    proj_future_slot_by_rid = {rid_p: idx_s for idx_s, (_w, _pf, rid_p) in enumerate(standings_for_picks, 1)}
    is_multi_yr_keeper = curr_cfg["format_label"] in ("Dynasty", "Keeper")
    is_sf_curr = curr_cfg["roster_slots"].get("OP", 0) > 0

    # Per-season playoff outcomes for trade-card badges (Won Championship / Runner-Up / Made Playoffs)
    outcome_by_yr_own: Dict[Tuple[int, str], dict] = {}
    for r_o in cur.execute("SELECT season, owner, made_playoffs, won_championship, runner_up, is_season_complete FROM team_seasons").fetchall():
        outcome_by_yr_own[(int(r_o[0]), r_o[1])] = {
            "is_complete": bool(r_o[5]), "made_playoffs": bool(r_o[2]), "won_champ": bool(r_o[3]), "runner_up": bool(r_o[4]),
        }

    for pk_key in pick_transfers:
        pick_transfers[pk_key].sort(key=lambda x: (x[0], x[1], x[2]))

    def _find_pick_flip(p_yr: int, rnd: int, orig_rid: int, acq_tid: int, after_key: Tuple[int, int, int]) -> Optional[dict]:
        """First later trade where `acq_tid` sent this exact pick onward (chronological, multi-hop aware)."""
        for f_yr, f_wk, f_ts, fr_i, _to_i, next_own in pick_transfers.get((p_yr, rnd, orig_rid), []):
            if fr_i == acq_tid and (f_yr, f_wk, f_ts) > after_key:
                return {"season": f_yr, "week": f_wk, "to": next_own, "is_pick_flip": True}
        return None

    def _eval_player_horizon(pid_i: int, pos: str, start_yr: int, start_wk: int, acq_tid: int) -> dict:
        horizon_yrs = [s for s in all_synced_seasons if s >= start_yr] if is_multi_yr_keeper else ([start_yr] if start_yr in season_rules_by_yr else [])
        flip_info = None
        for f_yr, f_wk, fr_i, _to_i, next_own in player_transfers.get(pid_i, []):
            if fr_i == acq_tid and (f_yr > start_yr or (f_yr == start_yr and f_wk > start_wk)):
                flip_info = {"season": f_yr, "week": f_wk, "to": next_own}
                break

        sv, po_sv, sp, sw = 0.0, 0.0, 0.0, 0
        rv_raw, po_rv, rp_pts, rw = 0.0, 0.0, 0.0, 0
        for s_yr in horizon_yrs:
            s_base = baselines_by_yr.get(s_yr, baselines)
            rp = s_base.get(pos, 8.0)
            min_w = start_wk if s_yr == start_yr else 1
            seen_w = set()
            for x in pb_all.get((s_yr, pid_i), []):
                if x["wk"] < min_w:
                    continue
                after_flip = flip_info is not None and (s_yr > flip_info["season"] or (s_yr == flip_info["season"] and x["wk"] >= flip_info["week"]))
                w_mult = PLAYOFF_WEIGHT if x["po"] else 1.0
                if not after_flip and x["tid"] == acq_tid and x["st"]:
                    v_inc = w_mult * (x["pts"] - rp)
                    sv += v_inc
                    if x["po"]:
                        po_sv += v_inc
                    sp += x["pts"]
                    sw += 1
                if x["wk"] not in seen_w:
                    seen_w.add(x["wk"])
                    if x["pts"] > 0 or x["st"]:
                        rv_inc = w_mult * (x["pts"] - rp)
                        rv_raw += rv_inc
                        if x["po"]:
                            po_rv += rv_inc
                        rp_pts += x["pts"]
                        rw += 1
        rv = max(0.0, rv_raw)
        return {
            "started_wks": sw, "started_pts": round(sp, 1), "started_ppg": round(sp / sw, 1) if sw else 0.0,
            "started_vorp": round(sv, 1), "po_started_vorp": round(max(0.0, po_sv), 1),
            "ros_wks": rw, "ros_pts": round(rp_pts, 1), "ros_ppg": round(rp_pts / rw, 1) if rw else 0.0,
            "ros_vorp": round(rv, 1), "po_ros_vorp": round(max(0.0, po_rv), 1),
            "flipped": flip_info,
        }

    def _eval_traded_pick(pk: dict, acq_tid: int, trade_key: Tuple[int, int, int] = (0, 0, 0)) -> dict:
        p_yr = int(pk["season"])
        rnd = int(pk["round"])
        orig_rid = int(pk["orig_rid"])
        yr_tmap = season_tmap_by_yr.get(p_yr, tmap)
        orig_owner = (yr_tmap.get(orig_rid) or tmap.get(orig_rid) or {}).get("owner", "")

        # Case A: Completed Draft in `p_yr` -> resolve exact slot & drafted player!
        if p_yr in draft_picks_by_yr_rnd_slot and draft_picks_by_yr_rnd_slot[p_yr]:
            dslot = draft_orig_slot_by_yr.get(p_yr, {}).get(orig_rid)
            resolved = draft_picks_by_yr_rnd_slot[p_yr].get((rnd, dslot)) if dslot else None
            if resolved is None:
                cands = draft_picks_by_yr_rnd_tid[p_yr].get((rnd, acq_tid)) or draft_picks_by_yr_rnd_tid[p_yr].get((rnd, orig_rid)) or []
                resolved = cands[0] if cands else None
            if resolved is not None:
                pir = int(resolved["pick_in_round"])
                pick_tag = f"'{str(p_yr)[-2:]} {rnd}.{pir:02d}"
                n_t = season_rules_by_yr.get(p_yr, {}).get("teams", curr_cfg["team_count"])
                is_sf = season_rules_by_yr.get(p_yr, {}).get("superflex", is_sf_curr)
                slot_ev = estimate_dynasty_pick_vorp(rnd, slot_tier_from_slot(pir, n_t), is_superflex=is_sf, years_out=1)
                perf = _eval_player_horizon(resolved["pid"], resolved["pos"], p_yr, 1, resolved["drafted_by_tid"])

                if resolved["drafted_by_tid"] == acq_tid:
                    # Acquiring manager kept the pick and drafted `resolved["name"]`!
                    return {
                        "player_id": resolved["pid"],
                        "name": resolved["name"],
                        "pos": resolved["pos"],
                        "pick_badge": pick_tag,
                        **perf,
                    }
                else:
                    # Acquiring manager flipped this pick before draft day -> headline is the PICK itself (never the
                    # player someone else drafted), credited at liquid slot value, with the exact flip hop + who drafted.
                    flip = _find_pick_flip(p_yr, rnd, orig_rid, acq_tid, trade_key) or {
                        "season": p_yr, "week": 0, "to": resolved["drafted_by_owner"], "is_pick_flip": True,
                    }
                    flip["drafted_by"] = resolved["drafted_by_owner"]
                    flip["drafted_name"] = resolved["name"]
                    ros_val = perf["ros_vorp"] if perf["ros_wks"] >= 4 else slot_ev
                    return {
                        "player_id": 0,
                        "name": f"{p_yr} Pick {rnd}.{pir:02d}",
                        "pos": "PICK",
                        "pick_badge": "",
                        "started_wks": 0,
                        "started_pts": 0.0,
                        "started_ppg": 0.0,
                        "started_vorp": slot_ev,
                        "po_started_vorp": 0.0,
                        "started_stat_note": "Flipped Pick Capital",
                        "ros_wks": perf["ros_wks"],
                        "ros_pts": perf["ros_pts"],
                        "ros_ppg": perf["ros_ppg"],
                        "ros_vorp": round(ros_val, 1),
                        "po_ros_vorp": perf["po_ros_vorp"],
                        "ros_stat_note": "" if perf["ros_wks"] >= 4 else "Est. Pick Value",
                        "flipped": flip,
                    }

        # Case B: Future Un-Drafted Pick (`2027`, `2028`, etc.) -> Projected Slot Expected VORP!
        years_out = max(1, p_yr - curr_season)
        if years_out == 1 and orig_rid in proj_future_slot_by_rid:
            stier = slot_tier_from_slot(proj_future_slot_by_rid[orig_rid], curr_cfg["team_count"])
            tier_tag = f" • Proj. {stier.capitalize()}"
        else:
            stier = "mid"
            tier_tag = ""
        ev = estimate_dynasty_pick_vorp(rnd, stier, is_superflex=is_sf_curr, years_out=years_out)
        via_short = orig_owner.split()[0] if orig_owner else ""
        via_txt = f" ({via_short}{tier_tag})" if via_short else (f" ({tier_tag.lstrip(' • ')})" if tier_tag else "")
        return {
            "player_id": 0,
            "name": f"{p_yr} Rd {rnd} Pick{via_txt}",
            "pos": "PICK",
            "pick_badge": "",
            "started_wks": 0, "started_pts": 0.0, "started_ppg": 0.0, "started_vorp": ev, "po_started_vorp": 0.0,
            "started_stat_note": "Future Pick Capital",
            "ros_wks": 0, "ros_pts": 0.0, "ros_ppg": 0.0, "ros_vorp": ev, "po_ros_vorp": 0.0,
            "ros_stat_note": "Future Pick Capital",
            "flipped": _find_pick_flip(p_yr, rnd, orig_rid, acq_tid, trade_key),
        }

    eval_live, eval_all = [], []
    for r in trade_rows:
        tid_s, yr, twk, ewk, ta, oa, tb, ob = r[0], int(r[1]), int(r[2]), int(r[3]), int(r[4]), r[5], int(r[6]), r[7]
        its = titems_by_id.get(tid_s, [])
        a_its = [x for x in its if x["to_tid"] == ta]
        b_its = [x for x in its if x["to_tid"] == tb]
        a_picks = traded_picks_raw.get(tid_s, {}).get(ta, [])
        b_picks = traded_picks_raw.get(tid_s, {}).get(tb, [])
        a_faab = int(traded_faab_raw.get(tid_s, {}).get(ta, 0))
        b_faab = int(traded_faab_raw.get(tid_s, {}).get(tb, 0))
        if not (a_its or a_picks or a_faab > 0) or not (b_its or b_picks or b_faab > 0):
            continue
        yr_faab_budget = int(season_rules_by_yr.get(yr, {}).get("waiver_budget") or curr_cfg.get("waiver_budget") or 100)

        def _side(s_its, acq_tid, pick_objs, faab_amt):
            pout, sv_tot, rv_tot = [], 0.0, 0.0
            for it in s_its:
                perf = _eval_player_horizon(it["pid"], it["pos"], yr, ewk, acq_tid)
                sv_tot += perf["started_vorp"]
                rv_tot += perf["ros_vorp"]
                pout.append({
                    "player_id": it["pid"], "name": it["name"], "pos": it["pos"], "pick_badge": "",
                    **perf,
                })
            for pk in pick_objs:
                pk_eval = _eval_traded_pick(pk, acq_tid, (yr, twk, trade_ts_by_id.get(tid_s, 0)))
                sv_tot += pk_eval["started_vorp"]
                rv_tot += pk_eval["ros_vorp"]
                pout.append(pk_eval)
            if faab_amt > 0:
                fv, fpct = estimate_faab_trade_vorp(faab_amt, yr_faab_budget)
                sv_tot += fv
                rv_tot += fv
                faab_note = f"{fpct}% Waiver Budget"
                pout.append({
                    "player_id": 0,
                    "name": f"${faab_amt} FAAB",
                    "pos": "FAAB",
                    "pick_badge": "",
                    "started_wks": 0, "started_pts": 0.0, "started_ppg": 0.0, "started_vorp": fv, "po_started_vorp": 0.0,
                    "started_stat_note": faab_note,
                    "ros_wks": 0, "ros_pts": 0.0, "ros_ppg": 0.0, "ros_vorp": fv, "po_ros_vorp": 0.0,
                    "ros_stat_note": faab_note,
                    "flipped": None,
                })
            pout.sort(key=lambda z: (-z["started_vorp"], -z["ros_vorp"]))
            return pout, round(sv_tot, 1), round(rv_tot, 1)

        ap, asv, arv = _side(a_its, ta, a_picks, a_faab)
        bp, bsv, brv = _side(b_its, tb, b_picks, b_faab)

        def _lens(av, bv):
            sw = round(av - bv, 1); ab = round(abs(sw), 1)
            w = "PUSH" if ab <= TIE_MARGIN_VORP else (oa if sw > 0 else ob)
            lo = "PUSH" if w == "PUSH" else (ob if w == oa else oa)
            tags = ["Pick Swap"] if (a_picks or b_picks) else []
            if a_faab > 0 or b_faab > 0:
                tags.append("FAAB Deal")
            if min(av, bv) >= 12.0 and ab <= 18.0: tags.append("Win-Win")
            return {"a_vorp": av, "b_vorp": bv, "swing_a": sw, "abs_swing": ab, "winner": w, "loser": lo, "win_playoff_vorp": 0.0, "tags": tags}

        rec_t = {
            "trade_id": tid_s, "season": yr, "trade_week": twk, "effective_week": ewk, "source": "sleeper_tx",
            "owner_a": oa, "owner_b": ob, "a_owners": [oa], "b_owners": [ob],
            "a_outcome": outcome_by_yr_own.get((yr, oa), {"is_complete": yr < curr_season}),
            "b_outcome": outcome_by_yr_own.get((yr, ob), {"is_complete": yr < curr_season}),
            "a_games_swung": 0, "b_games_swung": 0, "a_received": ap, "b_received": bp,
            "started": _lens(asv, bsv), "ros": _lens(arv, brv),
        }
        (eval_live if yr == curr_season else eval_all).append(rec_t)

    def _owner_cards(tlist, lens):
        st = {o: {"owner": o, "trades": 0, "wins": 0, "losses": 0, "pushes": 0, "net_vorp": 0.0} for o in owners}
        ot = defaultdict(list)
        pc = defaultdict(Counter)
        pn = defaultdict(lambda: defaultdict(float))
        for tr in tlist:
            ld = tr[lens]
            for side, o in (("a", tr["owner_a"]), ("b", tr["owner_b"])):
                if o not in st: continue
                ms = ld["swing_a"] if side == "a" else -ld["swing_a"]
                opp = tr["owner_b"] if side == "a" else tr["owner_a"]
                pc[o][opp] += 1; pn[o][opp] += ms
                st[o]["trades"] += 1; st[o]["net_vorp"] += ms
                if ms > TIE_MARGIN_VORP: st[o]["wins"] += 1
                elif ms < -TIE_MARGIN_VORP: st[o]["losses"] += 1
                else: st[o]["pushes"] += 1
                ot[o].append((ms, tr))
        out = []
        for o, d in st.items():
            dec = d["wins"] + d["losses"]
            tl = ot[o]
            bt = max(tl, key=lambda x: x[0]) if tl else None
            wt = min(tl, key=lambda x: x[0]) if tl else None
            if bt and wt and bt[1]["trade_id"] == wt[1]["trade_id"]:
                if bt[0] >= 0: wt = None
                else: bt = None
            def _br(pair):
                if not pair: return None
                s, t = pair
                return {"trade_id": t["trade_id"], "season": t["season"], "week": t["trade_week"], "partner": t["owner_b"] if t["owner_a"] == o else t["owner_a"], "swing": round(s, 1), "received": [p["name"] for p in (t["a_received"] if t["owner_a"] == o else t["b_received"])], "sent": [p["name"] for p in (t["b_received"] if t["owner_a"] == o else t["a_received"])]}
            out.append({
                "owner": o, "trades": d["trades"], "wins": d["wins"], "losses": d["losses"], "pushes": d["pushes"],
                "win_pct": round((d["wins"] / dec) * 100.0, 1) if dec else 0.0,
                "net_vorp": round(d["net_vorp"], 1), "avg_swing": round(d["net_vorp"] / d["trades"], 1) if d["trades"] else 0.0,
                "best_trade": _br(bt), "worst_trade": _br(wt), **partner_trade_extremes(pc[o], pn[o]),
            })
        out.sort(key=lambda x: (-x["net_vorp"], -x["trades"]))
        return out

    live_cards = {"started": _owner_cards(eval_live, "started"), "ros": _owner_cards(eval_live, "ros")}
    all_cards = {"started": _owner_cards(eval_all, "started"), "ros": _owner_cards(eval_all, "ros")}

    # Franchise Dossiers (with per-season `era_note` when league rules changed over time!)
    luck_map = {r["owner"]: r for r in luck_all}
    tc_map = {c["owner"]: c for c in (all_cards["started"] if eval_all else live_cards["started"])}
    distinct_eras = len({info["summary"] for info in season_rules_by_yr.values()}) > 1
    dossiers = []
    for c in career:
        o = c["owner"]
        is_act = o in owner_tname
        lk = luck_map.get(o, {})
        tc = tc_map.get(o, {})
        my_dp = [x for x in scored_dp if x["owner"] == o]
        bs = max((x for x in my_dp if ((x.get("bid", 0) <= 15) if x.get("draft_mode") == "AUCTION" else (x["round"] >= 2 if x.get("draft_mode") == "LINEAR" else x["round"] >= 4)) and x["delta"] > 0), key=lambda x: x["delta"], default=None)
        wb = min((x for x in my_dp if ((x.get("bid", 0) >= 20) if x.get("draft_mode") == "AUCTION" else (x["round"] == 1 if x.get("draft_mode") == "LINEAR" else x["round"] <= 5)) and x["delta"] < 0), key=lambda x: x["delta"], default=None)
        badges = []
        if not is_act: badges.append("Alumni")
        if c["rings"] > 0: badges.append(f"{c['rings']}x Champion")
        if (lk.get("pf_plus") or 100.0) >= 105.0: badges.append("Era Scorer")
        if (lk.get("luck_delta") or 0.0) >= 1.5: badges.append("Schedule Blessed")
        elif (lk.get("luck_delta") or 0.0) <= -1.5: badges.append("Schedule Cursed")
        if tc.get("net_vorp", 0.0) >= 35.0: badges.append("Trade Shark")
        if not badges or badges == ["Alumni"]:
            if is_act:
                badges.append(f"{curr_cfg['badge']} Contender")
        ts_tl = [
            {
                "season": int(r[0]), "team_name": r[1], "division": r[2],
                "era_note": season_rules_by_yr.get(int(r[0]), {}).get("summary", "") if distinct_eras else "",
                "wins": int(r[3]), "losses": int(r[4]), "pf": round(float(r[5]), 1),
                "final_rank": int(r[6]), "made_playoffs": bool(r[7]), "won_division": bool(r[8]),
                "won_champ": bool(r[9]), "runner_up": bool(r[10]), "is_complete": bool(r[11]),
            }
            for r in cur.execute("SELECT season, team_name, division_name, wins, losses, points_for, final_rank, made_playoffs, won_division, won_championship, runner_up, is_season_complete FROM team_seasons WHERE owner = ? ORDER BY season DESC", (o,)).fetchall()
        ]
        dossiers.append({
            "owner": o, "code": c["code"], "division": c["division"], "team_name": c["team_name"] or (ts_tl[0]["team_name"] if ts_tl else o),
            "is_active": is_act, "former_names": resolver.former_names_for(o),
            "first_season": c["first_season"], "seasons_played": c["seasons"], "completed_seasons": sum(1 for s in ts_tl if s["is_complete"]),
            "rings": c["rings"], "champ_years": c["champ_years"], "runner_up_years": c["runner_up_years"], "div_titles": sum(1 for s in ts_tl if s["won_division"]), "po_apps": sum(1 for s in ts_tl if s["made_playoffs"]),
            "reg_w": c["wins"], "reg_l": c["losses"], "reg_pct": c["win_pct"], "po_w": c["po_wins"], "po_l": c["po_losses"], "po_pct": round((c["po_wins"] / max(1, c["po_wins"] + c["po_losses"])) * 100.0, 1) if (c["po_wins"] + c["po_losses"]) else 0.0,
            "ap_pct": lk.get("ap_pct", 50.0), "luck_delta": lk.get("luck_delta", 0.0),
            "pf_plus": lk.get("pf_plus", 100.0), "pa_plus": lk.get("pa_plus", 100.0), "sos_rank": lk.get("sos_rank", 1),
            "high_game": c.get("high_game"), "low_game": c.get("low_game"),
            "badges": badges, "nemesis": h2h_ext.get(o, {}).get("nemesis"), "victim": h2h_ext.get(o, {}).get("victim"),
            "draft": {"best_steal": bs, "worst_bust": wb},
            "trade": {"deals": tc.get("trades", 0), "wins": tc.get("wins", 0), "losses": tc.get("losses", 0), "pushes": tc.get("pushes", 0), "net_vorp": tc.get("net_vorp", 0.0), "best_trade": tc.get("best_trade"), "worst_trade": tc.get("worst_trade")},
            "timeline": ts_tl,
        })
    dossiers.sort(key=lambda d: (0 if d["is_active"] else 1, -d["rings"], -d["reg_pct"]))
    conn.close()

    teams_info = []
    for t in tmap.values():
        o = t["owner"]
        p = team_power[o]
        bo = baseline_odds[o]
        teams_info.append({
            "owner": o, "code": o.split()[0], "division": t["division"], "team_name": t["name"], "rings": champ_count[o],
            "power_mean": p["mean"], "power_std": p["std"], "raw_avg": p["raw_avg"],
            "baseline_playoff_pct": bo["playoff_pct"], "baseline_div_pct": bo["div_title_pct"],
            "baseline_bye_pct": bo["bye_pct"], "baseline_leverage": bo["leverage_swing"],
        })

    weekly_proj_export = {
        str(wk): {
            own: {
                "proj": info["optimal_proj"], "fa_fills": info["fa_fills"],
                "is_live": bool(info.get("is_live", False)),
                "live_pts": float(info.get("live_pts", 0.0)),
                "rem_frac": float(info.get("rem_frac", 1.0)),
            }
            for own, info in wk_map.items()
        }
        for wk, wk_map in weekly_proj.items()
    }

    med_note = " (+1 Extra Win/Loss vs League Median each week)" if curr_cfg["uses_league_median"] else ""
    hub_data = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "season": curr_season,
        "completed_week": completed_week,
        "regular_season_weeks": reg_wks,
        "league_config": {
            "platform": "sleeper",
            "league_id": curr_cfg["league_id"],
            "slug": slug,
            "name": curr_cfg["name"],
            "badge": curr_cfg["badge"],
            "first_season": first_season,
            "scoring_label": curr_cfg["scoring_label"],
            "format_label": curr_cfg["format_label"],
            "draft_type": curr_cfg["draft_type"],
            "has_divisions": curr_cfg["has_divisions"],
            "is_first_year": curr_cfg["is_first_year"],
        },
        "divisions": curr_cfg["divisions"],
        "tiebreaker_rules": {
            "playoff_teams": curr_cfg["playoff_teams"],
            "playoff_byes": curr_cfg["playoff_byes"],
            "playoff_reseed": curr_cfg["playoff_reseed"],
            "playoff_round_weeks": curr_cfg["playoff_round_weeks"],
            "uses_league_median": curr_cfg["uses_league_median"],
            "division_champions": len(curr_cfg["divisions"]) if curr_cfg["has_divisions"] else 0,
            "wild_cards": curr_cfg["playoff_teams"] - (len(curr_cfg["divisions"]) if curr_cfg["has_divisions"] else 0),
            "first_round_bye": f"Seeds #1–#{curr_cfg['playoff_byes']}" if curr_cfg["playoff_byes"] > 1 else ("Seed #1" if curr_cfg["playoff_byes"] == 1 else "None"),
            "tiebreaker_rule": "TOTAL_POINTS_SCORED",
            "order": f"Overall Record{med_note} -> Total Points For (PF)",
            "reseed": "NFL Dynamic Re-Seeding" if curr_cfg["playoff_reseed"] else "Fixed Bracket (No Re-Seeding)",
        },
        "teams": teams_info,
        "completed_games": completed_games,
        "remaining_schedule": [g for g in full_schedule if int(g["week"]) > completed_week],
        "weekly_projections": weekly_proj_export,
        "awards": awards_data,
        "history": {
            "career": career,
            "h2h": h2h_summary,
            "championships": sorted(championships_log, key=lambda x: -int(x["year"])),
            "drafts": drafts,
            "draft_seasons": draft_seasons,
            "season_rules": {str(yr): {k: v for k, v in info.items() if k != "repl_ranks"} for yr, info in sorted(season_rules_by_yr.items())},
            "identity_review_candidates": resolver.review_candidates(),
        },
        "trades": {
            "all_time": {"seasons": sorted({t["season"] for t in eval_all}, reverse=True), "trade_count": len(eval_all), "trades": eval_all, "owner_cards": all_cards},
            "live": {"season": curr_season, "trade_count": len(eval_live), "trades": eval_live, "owner_cards": live_cards},
        },
        "analytics": {
            "luck": {"seasons": sorted({r[0] for r in valid_w}, reverse=True), "by_season": {str(curr_season): luck_curr, "all": luck_all}},
            "trends": {"season": curr_season, "weeks": weeks_list, "series": series},
            "heatmap": {"season": curr_season, "positions": list(active_pos), "baselines": baselines, "bounds": hm_bounds, "rows": hm_rows},
        },
        "dossiers": dossiers,
        "podcast": {"rss_url": "", "episodes": []},
    }

    if ephemeral:
        return hub_data, None

    out_dir = WEB_HUB_DIR / "leagues" / slug
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "hub_data.json"
    out_path.write_text(json.dumps(hub_data, indent=2), encoding="utf-8")
    print(f"✅ Wrote Sleeper '{curr_cfg['name']}' hub data ({out_path.stat().st_size / 1024:.1f} KB): {out_path}")
    return hub_data, out_path


def main():
    parser = argparse.ArgumentParser(description="Build & deploy ANY Sleeper League Hub (Zero-Auth)")
    parser.add_argument("--league-id", type=str, required=True, help="Sleeper numeric League ID")
    parser.add_argument("--slug", type=str, required=True, help="Short URL slug")
    parser.add_argument("--week", type=int, default=0, help="Override completed week (0 = auto-detect)")
    parser.add_argument("--max-history-years", type=int, default=5)
    parser.add_argument("--deploy-gcs", type=str, default="", help="Optional GCS bucket name")
    parser.add_argument("--gcp-project", type=str, default="bfl-fantasy")
    args = parser.parse_args()

    hub_data, data_path = build_sleeper_hub_data(
        args.league_id, args.slug, force_week=args.week, max_history_years=args.max_history_years
    )
    if args.deploy_gcs:
        deploy_multi_league_to_gcs(args.slug, data_path, args.deploy_gcs, project_id=args.gcp_project, hub_data=hub_data)


if __name__ == "__main__":
    main()
