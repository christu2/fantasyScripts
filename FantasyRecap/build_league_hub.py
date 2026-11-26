#!/usr/bin/env python3
"""
BFL League Hub & Interactive Playoff Machine — Data Builder & GCP Deployer
==========================================================================
Exports live 2026 ESPN standings, remaining Weeks 12-14 schedule, empirical
team power ratings, 18-year SQLite Record Book & H2H Matrix (2008-2026),
Draft Steals & Busts (2018-2026), Weekly Awards, and GCS Podcast RSS episodes
into `FantasyRecap/web_hub/hub_data.json`.

Optionally deploys the entire static web app to a Free-Tier Google Cloud Storage
bucket (`US-CENTRAL1`, Standard class — $0.00/mo within Always Free Tier!).

Usage:
  python3 FantasyRecap/build_league_hub.py --season 2026 --week 11
  python3 FantasyRecap/build_league_hub.py --season 2026 --week 11 --deploy-gcs bfl-hub-7x9k2
"""

import argparse
import json
import os
import sqlite3
import subprocess
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from FantasyRecap.core.manifest import get_divisions, get_owner_to_division, load_manifest, load_project_env
load_project_env()

from FantasyRecap.bfl_history_db import DB_PATH
from FantasyRecap.config import CURRENT_YEAR
from FantasyRecap.league_history_intelligence import BFL_CHAMPIONSHIPS, standardize_name
from FantasyRecap.league_recap_generator import (
    ESPN_LEAGUE_ID,
    ESPN_S2,
    ESPN_SWID,
    analyze_week,
    fetch_espn_week_data,
    parse_league_members_and_teams,
)
from FantasyRecap.playoff_odds_simulator import (
    extract_live_state_from_espn,
    run_playoff_simulation,
)
from FantasyRecap.weekly_awards_engine import compute_weekly_awards

WEB_HUB_DIR = SCRIPT_DIR / "web_hub"
WEB_HUB_DIR.mkdir(exist_ok=True)
DEFAULT_RSS_URL = "https://storage.googleapis.com/bfl-podcast-7x9k2/feed.xml"


def _canon_owner(name: str) -> str:
    s = standardize_name(name)
    return "rej hoxha" if s.lower() == "rej hoxha" else s


def export_history_and_drafts(db_path: Path = DB_PATH) -> dict:
    """Exports 18-year career standings, 16x16 H2H matrix, championships, and draft steals/busts."""
    manifest = load_manifest()
    active_owners = [t["owner"] for t in manifest.get("teams", [])]
    active_set = {o.lower() for o in active_owners}
    team_meta = {t["owner"]: t for t in manifest.get("teams", [])}

    if not db_path.exists():
        return {"career": [], "h2h": {}, "championships": BFL_CHAMPIONSHIPS, "drafts": {}}

    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    cur = conn.cursor()

    # 1. Career franchise leaderboard (active 16 owners)
    career_rows = cur.execute(
        """
        SELECT owner,
               COUNT(*) AS games,
               SUM(CASE WHEN win = 1 THEN 1 ELSE 0 END) AS wins,
               SUM(CASE WHEN win = 0 THEN 1 ELSE 0 END) AS losses,
               SUM(CASE WHEN is_playoffs = 1 AND win = 1 THEN 1 ELSE 0 END) AS po_wins,
               SUM(CASE WHEN is_playoffs = 1 AND win = 0 THEN 1 ELSE 0 END) AS po_losses,
               ROUND(AVG(score), 2) AS avg_pf,
               ROUND(AVG(opp_score), 2) AS avg_pa,
               ROUND(MAX(score), 2) AS high_score,
               MIN(season) AS first_season,
               COUNT(DISTINCT season) AS seasons
        FROM games
        GROUP BY owner
        """
    ).fetchall()

    career = []
    for r in career_rows:
        own = _canon_owner(r[0])
        if own.lower() not in active_set:
            continue
        prof = team_meta.get(own, {}).get("profile", {})
        wins, losses = int(r[2] or 0), int(r[3] or 0)
        gp = max(1, wins + losses)
        career.append({
            "owner": own,
            "code": team_meta.get(own, {}).get("code", own.split()[0]),
            "division": team_meta.get(own, {}).get("division", ""),
            "team_name": team_meta.get(own, {}).get("team_name", ""),
            "seasons": int(r[10] or 0),
            "first_season": int(r[9] or 2008),
            "wins": wins,
            "losses": losses,
            "win_pct": round((wins / gp) * 100.0, 1),
            "po_wins": int(r[4] or 0),
            "po_losses": int(r[5] or 0),
            "avg_pf": float(r[6] or 0.0),
            "avg_pa": float(r[7] or 0.0),
            "high_score": float(r[8] or 0.0),
            "rings": int(prof.get("rings", 0)),
            "champ_years": prof.get("champ_years", []),
            "runner_up_years": prof.get("runner_up_years", []),
        })
    career.sort(key=lambda x: (-x["rings"], -x["win_pct"], -x["wins"]))

    # 2. All-time H2H matrix between active 16 owners
    h2h_rows = cur.execute(
        """
        SELECT owner, opp_owner, season, week, is_playoffs, score, opp_score, win, margin
        FROM games
        ORDER BY season ASC, week ASC
        """
    ).fetchall()
    pair_games: Dict[str, List[dict]] = {}
    for r in h2h_rows:
        o1, o2 = _canon_owner(r[0]), _canon_owner(r[1])
        if o1.lower() not in active_set or o2.lower() not in active_set or o1 >= o2:
            continue
        key = f"{o1}|||{o2}"
        pair_games.setdefault(key, []).append({
            "season": int(r[2]),
            "week": int(r[3]),
            "is_playoffs": bool(r[4]),
            "s1": round(float(r[5]), 2),
            "s2": round(float(r[6]), 2),
            "w1": int(r[7]) == 1,
            "margin": round(abs(float(r[8])), 2),
        })

    h2h_summary = {}
    for key, glist in pair_games.items():
        o1, o2 = key.split("|||")
        w1 = sum(1 for g in glist if g["w1"])
        w2 = len(glist) - w1
        po1 = sum(1 for g in glist if g["is_playoffs"] and g["w1"])
        po2 = sum(1 for g in glist if g["is_playoffs"] and not g["w1"])
        avg1 = round(sum(g["s1"] for g in glist) / len(glist), 1)
        avg2 = round(sum(g["s2"] for g in glist) / len(glist), 1)
        streak_w = o1 if glist[-1]["w1"] else o2
        streak_n = 0
        last_flag = glist[-1]["w1"]
        for g in reversed(glist):
            if g["w1"] == last_flag:
                streak_n += 1
            else:
                break
        last_g = glist[-1]
        closest = min(glist, key=lambda g: g["margin"])
        sbs = [sb for sb in BFL_CHAMPIONSHIPS if {sb["champ"].lower(), sb["runner_up"].lower()} == {o1.lower(), o2.lower()}]
        h2h_summary[key] = {
            "o1": o1, "o2": o2, "w1": w1, "w2": w2, "games": len(glist),
            "po1": po1, "po2": po2, "avg1": avg1, "avg2": avg2,
            "streak_owner": streak_w, "streak_len": streak_n,
            "last": {
                "season": last_g["season"], "week": last_g["week"],
                "winner": o1 if last_g["w1"] else o2,
                "hi": max(last_g["s1"], last_g["s2"]), "lo": min(last_g["s1"], last_g["s2"]),
            },
            "closest": {
                "season": closest["season"], "week": closest["week"],
                "winner": o1 if closest["w1"] else o2,
                "margin": closest["margin"],
            },
            "super_bowls": sbs,
        }

    # 3. Draft Steals & Busts by Season (2018-present) + All-Time
    draft_seasons = [
        int(r[0]) for r in cur.execute(
            "SELECT DISTINCT season FROM draft_picks WHERE has_season_stats = 1 ORDER BY season DESC"
        ).fetchall()
    ]
    drafts = {}
    for yr_key in [0] + draft_seasons:
        yr_cond = "AND season = ?" if yr_key > 0 else ""
        params = [yr_key] if yr_key > 0 else []
        steals = cur.execute(
            f"""
            SELECT season, overall_pick, round_id, owner, player_name, position,
                   pos_draft_rank, pos_finish_rank, season_points, points_above_round_avg
            FROM draft_picks
            WHERE has_season_stats = 1 AND position IN ('QB','RB','WR','TE')
              AND round_id >= 4 AND points_above_round_avg IS NOT NULL {yr_cond}
            ORDER BY points_above_round_avg DESC LIMIT 12
            """,
            params,
        ).fetchall()
        busts = cur.execute(
            f"""
            SELECT season, overall_pick, round_id, owner, player_name, position,
                   pos_draft_rank, pos_finish_rank, season_points, points_above_round_avg
            FROM draft_picks
            WHERE has_season_stats = 1 AND position IN ('QB','RB','WR','TE')
              AND round_id <= 5 AND points_above_round_avg IS NOT NULL {yr_cond}
            ORDER BY points_above_round_avg ASC LIMIT 12
            """,
            params,
        ).fetchall()

        def _ser(rows):
            return [
                {
                    "season": int(x[0]), "pick": int(x[1]), "round": int(x[2]),
                    "owner": _canon_owner(x[3]), "player": x[4], "pos": x[5],
                    "pos_draft_rank": int(x[6] or 0), "pos_finish_rank": int(x[7] or 0),
                    "pts": round(float(x[8] or 0.0), 1), "delta": round(float(x[9] or 0.0), 1),
                }
                for x in rows
            ]

        drafts["all" if yr_key == 0 else str(yr_key)] = {"steals": _ser(steals), "busts": _ser(busts)}

    conn.close()
    return {
        "career": career,
        "h2h": h2h_summary,
        "championships": sorted(BFL_CHAMPIONSHIPS, key=lambda x: -int(x["year"])),
        "drafts": drafts,
        "draft_seasons": draft_seasons,
    }


def fetch_rss_episodes(rss_url: str = DEFAULT_RSS_URL) -> List[dict]:
    """Parses live GCS Apple Podcasts RSS feed for the embedded Podcast Studio tab."""
    episodes = []
    try:
        import requests
        r = requests.get(rss_url, timeout=8)
        if r.status_code == 200:
            root = ET.fromstring(r.text)
            for item in root.findall("./channel/item"):
                enc = item.find("enclosure")
                audio_url = enc.attrib.get("url", "") if enc is not None else ""
                dur_el = item.find("{http://www.itunes.com/dtds/podcast-1.0.dtd}duration")
                episodes.append({
                    "title": (item.findtext("title") or "").strip(),
                    "description": (item.findtext("description") or "").strip(),
                    "pub_date": (item.findtext("pubDate") or "").strip(),
                    "duration": (dur_el.text or "").strip() if dur_el is not None else "",
                    "audio_url": audio_url,
                })
    except Exception as exc:
        print(f"⚠️ Could not fetch RSS feed ({rss_url}): {exc}")
    return episodes


def build_hub_data(season: int, completed_week: int) -> dict:
    """Assembles the complete JSON payload powering the BFL League Hub & Interactive Playoff Machine."""
    print(f"📡 Building BFL League Hub dataset (Season {season}, through Week {completed_week})...")
    raw_data = fetch_espn_week_data(ESPN_LEAGUE_ID, str(season), completed_week, ESPN_S2, ESPN_SWID)
    teams_raw = parse_league_members_and_teams(raw_data)
    completed_games, full_schedule, team_power = extract_live_state_from_espn(raw_data, completed_week)

    target_sim_week = min(14, completed_week + 1)
    baseline_odds = run_playoff_simulation(
        target_sim_week, completed_games, full_schedule, team_power=team_power, n_sims=5000
    )

    # Compute current weekly awards
    matchups_wk, perfs_wk, _ = analyze_week(raw_data, completed_week)
    awards_data = compute_weekly_awards(
        matchups_wk, perfs_wk, week_num=completed_week, season=season, playoff_odds=baseline_odds
    )

    # Team metadata + current live team names from ESPN
    manifest = load_manifest()
    espn_team_names = {_canon_owner(t["owner"]): t["name"] for t in teams_raw.values()}
    teams_info = []
    for t in manifest.get("teams", []):
        own = t["owner"]
        pwr = team_power.get(own, {"mean": 100.0, "std": 14.0, "raw_avg": 100.0})
        bo = baseline_odds.get(own, {})
        teams_info.append({
            "owner": own,
            "code": t["code"],
            "division": t["division"],
            "team_name": espn_team_names.get(own, t["team_name"]),
            "rings": int(t.get("profile", {}).get("rings", 0)),
            "power_mean": pwr["mean"],
            "power_std": pwr["std"],
            "raw_avg": pwr["raw_avg"],
            "baseline_playoff_pct": bo.get("playoff_pct", 0.0),
            "baseline_div_pct": bo.get("div_title_pct", 0.0),
            "baseline_bye_pct": bo.get("bye_pct", 0.0),
            "baseline_leverage": bo.get("leverage_swing", 0.0),
        })

    remaining_schedule = [g for g in full_schedule if g["week"] > completed_week]
    history_pack = export_history_and_drafts(DB_PATH)
    episodes = fetch_rss_episodes()

    hub_data = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "season": season,
        "completed_week": completed_week,
        "regular_season_weeks": 14,
        "divisions": get_divisions(),
        "tiebreaker_rules": {
            "playoff_teams": 7,
            "division_champions": 4,
            "wild_cards": 3,
            "first_round_bye": "Seed #1 (top Division Champion)",
            "order": "Overall Record -> Head-to-Head Record -> Total Points For (PF)",
            "reseed": "NFL Dynamic Re-Seeding in Round 2 (#1 plays lowest surviving seed)",
        },
        "teams": teams_info,
        "completed_games": completed_games,
        "remaining_schedule": remaining_schedule,
        "awards": awards_data,
        "history": history_pack,
        "podcast": {
            "rss_url": DEFAULT_RSS_URL,
            "episodes": episodes,
        },
    }

    out_path = WEB_HUB_DIR / "hub_data.json"
    out_path.write_text(json.dumps(hub_data, indent=2), encoding="utf-8")
    print(f"✅ Wrote League Hub data ({out_path.stat().st_size / 1024:.1f} KB): {out_path}")
    return hub_data


def deploy_hub_to_gcs(bucket_name: str, project_id: str = "bfl-fantasy") -> str:
    """
    Deploys `web_hub/` static files to a Free-Tier GCS bucket (`US-CENTRAL1`, Standard)
    using `gcloud storage`, ensuring public read access and fast cache revalidation.
    """
    # Create bucket in Always-Free-Tier US-CENTRAL1 if it does not yet exist
    chk = subprocess.run(
        ["gcloud", "storage", "buckets", "describe", f"gs://{bucket_name}", f"--project={project_id}"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    if chk.returncode != 0:
        print(f"🪣 Creating Always-Free-Tier GCS bucket gs://{bucket_name} in US-CENTRAL1 ({project_id})...")
        cr = subprocess.run(
            [
                "gcloud", "storage", "buckets", "create", f"gs://{bucket_name}",
                f"--project={project_id}",
                "--location=US-CENTRAL1",
                "--default-storage-class=STANDARD",
                "--uniform-bucket-level-access",
            ],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        if cr.returncode != 0:
            raise RuntimeError(f"Failed creating bucket gs://{bucket_name}: {cr.stderr}")

        # Configure public read & index.html main page
        subprocess.run(
            ["gcloud", "storage", "buckets", "add-iam-policy-binding", f"gs://{bucket_name}",
             "--member=allUsers", "--role=roles/storage.objectViewer", f"--project={project_id}"],
            check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        subprocess.run(
            ["gcloud", "storage", "buckets", "update", f"gs://{bucket_name}",
             "--web-main-page-suffix=index.html", "--web-error-page=index.html", f"--project={project_id}"],
            check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )

    uploads = [
        ("index.html", "text/html; charset=utf-8", "no-cache, max-age=60"),
        ("styles.css", "text/css; charset=utf-8", "public, max-age=300"),
        ("app.js", "application/javascript; charset=utf-8", "public, max-age=300"),
        ("hub_data.json", "application/json; charset=utf-8", "no-cache, max-age=60"),
    ]
    for fname, ctype, cctl in uploads:
        src = WEB_HUB_DIR / fname
        if not src.exists():
            raise FileNotFoundError(f"Missing web hub asset: {src}")
        subprocess.run(
            [
                "gcloud", "storage", "cp", str(src), f"gs://{bucket_name}/{fname}",
                f"--project={project_id}",
                f"--content-type={ctype}",
                f"--cache-control={cctl}",
            ],
            check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )

    url = f"https://storage.googleapis.com/{bucket_name}/index.html"
    print(f"🚀 Deployed BFL League Hub live to GCP Free Tier: {url}")
    return url


def main():
    parser = argparse.ArgumentParser(description="Build & deploy BFL League Hub + Interactive Playoff Machine")
    parser.add_argument("--season", type=int, default=int(CURRENT_YEAR))
    parser.add_argument("--week", type=int, default=11, help="Completed week to export through")
    parser.add_argument("--deploy-gcs", type=str, default="", help="GCS bucket name to deploy live (e.g. bfl-hub-7x9k2)")
    parser.add_argument("--gcp-project", type=str, default="bfl-fantasy")
    args = parser.parse_args()

    build_hub_data(args.season, args.week)
    if args.deploy_gcs:
        deploy_hub_to_gcs(args.deploy_gcs, project_id=args.gcp_project)


if __name__ == "__main__":
    main()
