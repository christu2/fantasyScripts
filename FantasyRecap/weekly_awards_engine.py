#!/usr/bin/env python3
"""
BFL Weekly Awards & Manager Briefing Engine (Phase 2)
=====================================================
Generates crisp, witty, non-repetitive Tuesday morning Weekly Awards cards from
live ESPN boxscores and Monte Carlo playoff simulations:

  • Manager of the Week        (highest team score + margin / lineup efficiency)
  • Start/Sit Blunder Award    (biggest costly healthy bench leave—excludes in-game injuries!)
  • Photo Finish / Lucky Win   (tightest escape or lowest-scoring win)
  • Bad Beat of the Week       (highest-scoring loser)
  • Player of the Week         (top individual starter eruption)
  • Next-Week Pressure Cooker  (manager facing the biggest playoff leverage swing)

Usage:
  python3 FantasyRecap/weekly_awards_engine.py --season 2026 --week 11 --dry-run
  python3 FantasyRecap/weekly_awards_engine.py --season 2026 --week 11 --post-discord
"""

import argparse
import hashlib
import os
import sys
from pathlib import Path
from typing import Dict, List, Optional

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from FantasyRecap.core.manifest import load_project_env
load_project_env()

from FantasyRecap.config import CURRENT_YEAR
from FantasyRecap.league_recap_generator import (
    ESPN_LEAGUE_ID,
    ESPN_S2,
    ESPN_SWID,
    analyze_week,
    fetch_espn_week_data,
    parse_league_members_and_teams,
)


def _pick_quip(seed: str, variants: List[str]) -> str:
    idx = int(hashlib.md5(seed.encode("utf-8")).hexdigest()[:8], 16) % len(variants)
    return variants[idx]


def compute_weekly_awards(
    matchups: list,
    perfs: dict,
    *,
    week_num: int,
    season: int,
    playoff_odds: Optional[dict] = None,
) -> Dict:
    """
    Computes structured Weekly Awards + rotating non-repetitive broadcast copy
    from `analyze_week()` output. Pure function—100% unit-testable without network calls!
    """
    if not matchups:
        raise ValueError("No matchups provided to compute weekly awards.")

    all_sides = []
    winners = []
    losers = []
    for m in matchups:
        w = m["winner"]
        l = m["loser"]
        w_pts = max(float(m["away_score"]), float(m["home_score"]))
        l_pts = min(float(m["away_score"]), float(m["home_score"]))
        margin = round(float(m["margin"]), 2)
        w_entry = {"owner": w["owner"], "team": w["name"], "id": w["id"], "pts": w_pts, "opp": l["owner"], "opp_pts": l_pts, "margin": margin, "won": True}
        l_entry = {"owner": l["owner"], "team": l["name"], "id": l["id"], "pts": l_pts, "opp": w["owner"], "opp_pts": w_pts, "margin": margin, "won": False}
        winners.append(w_entry)
        losers.append(l_entry)
        all_sides.extend([w_entry, l_entry])

    awards: List[dict] = []

    # 1. Manager of the Week (Highest scorer)
    top_mgr = max(all_sides, key=lambda x: x["pts"])
    seed_gm = f"gm:{season}:{week_num}:{top_mgr['owner']}"
    gm_quip = _pick_quip(seed_gm, [
        f"**{top_mgr['owner']}** hung **`{top_mgr['pts']:.2f}` pts** on {top_mgr['opp']} ({top_mgr['opp_pts']:.2f})—nobody else in the BFL came close.",
        f"Clinic from **{top_mgr['owner']}** ({top_mgr['team']}): **`{top_mgr['pts']:.2f}` pts** and a `+{top_mgr['margin']:.2f}`-pt statement over {top_mgr['opp']}.",
        f"**{top_mgr['owner']}** owned Week {week_num}, dropping **`{top_mgr['pts']:.2f}` pts** to roll past {top_mgr['opp']} (`{top_mgr['opp_pts']:.2f}`).",
        f"Top of the league table this week: **{top_mgr['owner']}** posted **`{top_mgr['pts']:.2f}` pts** against {top_mgr['opp']}.",
    ])
    awards.append({
        "key": "manager_of_week",
        "title": "Manager of the Week",
        "winner": top_mgr["owner"],
        "metric": f"{top_mgr['pts']:.2f} pts",
        "body": gm_quip,
    })

    # 2. Player of the Week (Top individual starter)
    best_starter = None
    for side in all_sides:
        pinfo = perfs.get(side["id"], {})
        for st in pinfo.get("starters", []):
            if best_starter is None or float(st.get("pts", 0.0)) > best_starter["pts"]:
                best_starter = {
                    "name": st["name"],
                    "pts": float(st.get("pts", 0.0)),
                    "proj": float(st.get("proj", 0.0)),
                    "owner": side["owner"],
                    "won": side["won"],
                }
    if best_starter:
        delta_p = best_starter["pts"] - best_starter["proj"]
        outcome_note = "in a victory" if best_starter["won"] else "in a painful losing effort"
        mvp_quip = _pick_quip(f"mvp:{season}:{week_num}:{best_starter['name']}", [
            f"**{best_starter['name']}** ({best_starter['owner']}) erupted for **`{best_starter['pts']:.2f}` pts** (`+{delta_p:.1f}` vs proj) {outcome_note}.",
            f"Week {week_num} belonged to **{best_starter['name']}** (`{best_starter['pts']:.2f}` pts on `{best_starter['proj']:.1f}` proj) for **{best_starter['owner']}** {outcome_note}.",
            f"**{best_starter['owner']}** got a `{best_starter['pts']:.2f}`-pt gem from **{best_starter['name']}** (`+{delta_p:.1f}` over projection) {outcome_note}.",
        ])
        awards.append({
            "key": "player_of_week",
            "title": "Single-Player MVP",
            "winner": f"{best_starter['name']} ({best_starter['owner']})",
            "metric": f"{best_starter['pts']:.2f} pts",
            "body": mvp_quip,
        })

    # 3. Start/Sit Blunder of the Week (Healthy skill-position bench leave; prioritizes cost-the-game blunders!)
    blunder_candidates = []
    for side in all_sides:
        pinfo = perfs.get(side["id"], {})
        for b in pinfo.get("blunders", []):
            started = b.get("started", {})
            benched = b.get("benched", {})
            diff = float(b.get("diff", 0.0))
            if diff < 8.0 or started.get("pos_id") in (5, 16):
                continue
            # Exclude likely in-game injury exits (0-2 pts on a 10+ proj starter)
            if float(started.get("pts", 0.0)) <= 2.0 and float(started.get("proj", 0.0)) >= 10.0:
                continue
            cost_win = (not side["won"]) and (diff >= side["margin"])
            blunder_candidates.append({
                "owner": side["owner"],
                "opp": side["opp"],
                "margin": side["margin"],
                "won": side["won"],
                "cost_win": cost_win,
                "diff": round(diff, 2),
                "started": started,
                "benched": benched,
            })

    if blunder_candidates:
        blunder_candidates.sort(key=lambda x: (x["cost_win"], not x["won"], x["diff"]), reverse=True)
        bl = blunder_candidates[0]
        s_nm, s_pts = bl["started"]["name"], float(bl["started"]["pts"])
        b_nm, b_pts = bl["benched"]["name"], float(bl["benched"]["pts"])
        if bl["cost_win"]:
            bl_quip = _pick_quip(f"bl:{season}:{week_num}:{bl['owner']}", [
                f"**{bl['owner']}** lost to {bl['opp']} by `{bl['margin']:.2f}` while leaving **`+{bl['diff']:.2f}` pts** on the bench ({s_nm} `{s_pts:.1f}` over {b_nm} `{b_pts:.1f}`).",
                f"Direct game-swinger: **{bl['owner']}** started {s_nm} (`{s_pts:.1f}`) over {b_nm} (`{b_pts:.1f}`)—a **`{bl['diff']:.2f}`-pt** miss in a `{bl['margin']:.2f}`-pt loss.",
                f"Painful tinkering by **{bl['owner']}**: swapping {b_nm} (`{b_pts:.1f}`) in for {s_nm} (`{s_pts:.1f}`) would have flipped the `{bl['margin']:.2f}`-pt loss to {bl['opp']}.",
            ])
        else:
            tail = "Still escaped with the win, somehow." if bl["won"] else f"Dropped it to {bl['opp']} anyway, though the bench had answers."
            bl_quip = _pick_quip(f"bl:{season}:{week_num}:{bl['owner']}", [
                f"**{bl['owner']}** left **`{bl['diff']:.2f}` pts** rotting on the bench ({s_nm} `{s_pts:.1f}` over {b_nm} `{b_pts:.1f}`). {tail}",
                f"Benchwarmer spotlight on **{bl['owner']}**: {b_nm} (`{b_pts:.1f}`) outscored starter {s_nm} (`{s_pts:.1f}`) by **`+{bl['diff']:.2f}` pts**. {tail}",
            ])
        awards.append({
            "key": "start_sit_blunder",
            "title": "Start/Sit Blunder of the Week",
            "winner": bl["owner"],
            "metric": f"-{bl['diff']:.2f} pts",
            "body": bl_quip,
        })

    # 4. Bad Beat / Unluckiest Loss (Highest-scoring loser)
    bad_beat = max(losers, key=lambda x: x["pts"])
    bb_quip = _pick_quip(f"bb:{season}:{week_num}:{bad_beat['owner']}", [
        f"**{bad_beat['owner']}** put up **`{bad_beat['pts']:.2f}` pts**—and still caught an L against {bad_beat['opp']} (`{bad_beat['opp_pts']:.2f}`). Brutal schedule draw.",
        f"Wrong place, wrong time for **{bad_beat['owner']}**: **`{bad_beat['pts']:.2f}` pts** wasn't enough to survive {bad_beat['opp']}'s `{bad_beat['opp_pts']:.2f}`.",
        f"Pour one out for **{bad_beat['owner']}**, who scored **`{bad_beat['pts']:.2f}`** only to run straight into {bad_beat['opp']} (`{bad_beat['opp_pts']:.2f}`).",
    ])
    awards.append({
        "key": "bad_beat",
        "title": "Unluckiest Loss (Bad Beat)",
        "winner": bad_beat["owner"],
        "metric": f"{bad_beat['pts']:.2f} pts (L)",
        "body": bb_quip,
    })

    # 5. Photo Finish / Luckiest Escape (Closest margin OR lowest-scoring winner if no thriller < 5 pts)
    closest_win = min(winners, key=lambda x: x["margin"])
    lowest_win = min(winners, key=lambda x: x["pts"])
    if closest_win["margin"] <= 6.0:
        pf_quip = _pick_quip(f"pf:{season}:{week_num}:{closest_win['owner']}", [
            f"**{closest_win['owner']}** survived {closest_win['opp']} by just **`{closest_win['margin']:.2f}` pts** (`{closest_win['pts']:.2f}`–`{closest_win['opp_pts']:.2f}`). Pure sweat.",
            f"Cardiac escape: **{closest_win['owner']}** edged {closest_win['opp']} (`{closest_win['pts']:.2f}`–`{closest_win['opp_pts']:.2f}`) with only **`{closest_win['margin']:.2f}` pts** to spare.",
            f"Razor-thin finish—**{closest_win['owner']}** clipped {closest_win['opp']} by **`{closest_win['margin']:.2f}` pts** (`{closest_win['pts']:.2f}`–`{closest_win['opp_pts']:.2f}`).",
        ])
        awards.append({
            "key": "photo_finish",
            "title": "Photo Finish of the Week",
            "winner": closest_win["owner"],
            "metric": f"+{closest_win['margin']:.2f} margin",
            "body": pf_quip,
        })
    else:
        lw_quip = _pick_quip(f"lw:{season}:{week_num}:{lowest_win['owner']}", [
            f"**{lowest_win['owner']}** stole a win with just **`{lowest_win['pts']:.2f}` pts**—all thanks to {lowest_win['opp']} putting up `{lowest_win['opp_pts']:.2f}`.",
            f"Ugly W counts the same: **{lowest_win['owner']}** escaped with only **`{lowest_win['pts']:.2f}` pts** against {lowest_win['opp']} (`{lowest_win['opp_pts']:.2f}`).",
        ])
        awards.append({
            "key": "lucky_escape",
            "title": "Luckiest Escape",
            "winner": lowest_win["owner"],
            "metric": f"{lowest_win['pts']:.2f} pts (W)",
            "body": lw_quip,
        })

    # 6. Next-Week Pressure Cooker (Highest Monte Carlo leverage swing if playoff_odds supplied)
    if playoff_odds:
        top_lev = max(playoff_odds.values(), key=lambda x: float(x.get("leverage_swing", 0.0)))
        if float(top_lev.get("leverage_swing", 0.0)) >= 12.0:
            lev_quip = _pick_quip(f"lev:{season}:{week_num}:{top_lev['owner']}", [
                f"**{top_lev['owner']}** ({top_lev['record']}) faces {top_lev['next_opp']} in Week {week_num + 1}: win moves them to **`{top_lev['win_po_pct']}%`**, loss drops them to **`{top_lev['loss_po_pct']}%`** (`±{top_lev['leverage_swing']}%` swing).",
                f"Circle Week {week_num + 1} for **{top_lev['owner']}** vs. {top_lev['next_opp']}—a massive **`±{top_lev['leverage_swing']}%`** playoff swing (`{top_lev['win_po_pct']}%` with a W vs. `{top_lev['loss_po_pct']}%` with an L).",
            ])
            awards.append({
                "key": "pressure_cooker",
                "title": f"Week {week_num + 1} Pressure Cooker",
                "winner": top_lev["owner"],
                "metric": f"±{top_lev['leverage_swing']}% swing",
                "body": lev_quip,
            })

    avg_score = round(sum(x["pts"] for x in all_sides) / len(all_sides), 2)
    return {
        "season": season,
        "week": week_num,
        "league_avg": avg_score,
        "awards": awards,
    }


def format_awards_discord_payload(awards_data: dict) -> dict:
    """Formats Weekly Awards into a clean, readable, zero-clutter Discord embed."""
    wk = awards_data["week"]
    season = awards_data["season"]
    fields = [
        {"name": f"{a['title']} — {a['metric']}", "value": a["body"], "inline": False}
        for a in awards_data["awards"]
    ]
    return {
        "username": "BFL Tuesday Awards Desk",
        "avatar_url": "https://a.espncdn.com/combiner/i?img=/i/headshots/nfl/players/full/3918298.png",
        "embeds": [{
            "title": f"BFL Week {wk} Awards & Morning Briefing ({season})",
            "description": f"League scoring average: **`{awards_data['league_avg']:.2f}` pts** across all 16 teams.",
            "color": 0xF1C40F,
            "fields": fields,
            "footer": {"text": f"Beasts Football League • Week {wk} Honors & Lowlights"},
        }],
    }


def run_weekly_awards(season: int, week_num: int, post_discord: bool = False) -> dict:
    raw_data = fetch_espn_week_data(ESPN_LEAGUE_ID, str(season), week_num, ESPN_S2, ESPN_SWID)
    matchups, perfs, _ = analyze_week(raw_data, week_num)
    playoff_odds = None
    if week_num <= 13:
        try:
            from FantasyRecap.playoff_odds_simulator import simulate_league_playoff_odds_from_espn
            playoff_odds = simulate_league_playoff_odds_from_espn(raw_data, week_num, n_sims=3000)
        except Exception:
            playoff_odds = None

    data = compute_weekly_awards(matchups, perfs, week_num=week_num, season=season, playoff_odds=playoff_odds)
    payload = format_awards_discord_payload(data)

    if post_discord:
        from FantasyRecap.discord_channels import send_to_channel
        ok = send_to_channel("commish", payload)
        print("🚀 Broadcasted Weekly Awards to #commissioner-desk!" if ok else "⚠️ Discord post failed.")
    else:
        emb = payload["embeds"][0]
        print(f"\n{emb['title']}\n{'=' * 68}\n{emb['description']}\n")
        for f in emb["fields"]:
            print(f"• {f['name']}\n  {f['value']}\n")
    return data


def main():
    parser = argparse.ArgumentParser(description="BFL Weekly Awards & Manager Briefing Engine")
    parser.add_argument("--season", type=int, default=int(CURRENT_YEAR))
    parser.add_argument("--week", type=int, default=11)
    parser.add_argument("--post-discord", action="store_true", help="Post awards card to #commissioner-desk")
    args = parser.parse_args()
    run_weekly_awards(args.season, args.week, post_discord=args.post_discord)


if __name__ == "__main__":
    main()
