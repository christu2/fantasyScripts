#!/usr/bin/env python3
"""
BFL Season Storylines & Analytics Lab Engine
============================================
Computes deep league analytics from `bfl_history.db` and live ESPN schedule data:
  1. All-Play Record & Schedule Luck Index ("Schedule Fraud Detector") for 2026,
     individual seasons (2018–2025), and All-Time (2008–present).
  2. Week-by-Week 2026 Power Rankings & Playoff Odds Rollercoaster (Weeks 1..N)
     using deterministic seeded Monte Carlo snapshots at the end of each week.
  3. 2026 Positional Strength Heatmap (Starter VORP/Week across QB/RB/WR/TE/K/D/ST)
     using BFL's unified 16-team positional replacement baselines.
"""

from __future__ import annotations

import random
import sqlite3
import statistics
import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from FantasyRecap.core.manifest import load_manifest, load_project_env
load_project_env()

from FantasyRecap.bfl_history_db import DB_PATH
from FantasyRecap.config import CURRENT_YEAR
from FantasyRecap.league_history_intelligence import standardize_name
from FantasyRecap.playoff_odds_simulator import run_playoff_simulation
from FantasyRecap.trade_vault_engine import FALLBACK_REPLACEMENT_PPG, compute_season_replacement_baselines

POSITIONS = ("QB", "RB", "WR", "TE", "K", "D/ST")


def _canon_owner(name: str) -> str:
    s = standardize_name(name)
    return "rej hoxha" if s.lower() == "rej hoxha" else s


def _luck_verdict(luck_delta: float, is_all_time: bool = False) -> str:
    hi = 6.0 if is_all_time else 1.5
    md = 2.5 if is_all_time else 0.7
    if luck_delta >= hi:
        return "Schedule Blessed"
    if luck_delta >= md:
        return "Slightly Lucky"
    if luck_delta <= -hi:
        return "Schedule Cursed"
    if luck_delta <= -md:
        return "Unlucky"
    return "Fair Schedule"


def compute_all_play_luck(
    db_path: Path = DB_PATH,
    *,
    season: Optional[int] = None,
    max_week: Optional[int] = None,
    active_only: bool = True,
) -> dict:
    """
    Computes All-Play Record (if every team played all other teams each week),
    Expected Wins, Luck Delta (`actual_wins - expected_wins`), and Schedule Difficulty (Avg PA).
    Pass `season=None` for All-Time (2008–present), or a specific year (e.g., 2026).
    Automatically excludes in-progress / unplayed weeks (avg score < 40 or 0-0 games).
    """
    if not db_path.exists():
        return {"season": season or "all", "rows": [], "seasons": []}

    manifest = load_manifest()
    active_owners = [t["owner"] for t in manifest.get("teams", [])]
    active_set = {o.lower() for o in active_owners}
    div_map = {t["owner"]: t["division"] for t in manifest.get("teams", [])}

    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    cur = conn.cursor()
    avail_seasons = [
        int(r[0]) for r in cur.execute(
            "SELECT DISTINCT season FROM games WHERE is_regular = 1 ORDER BY season DESC"
        ).fetchall()
    ]

    if season is not None:
        rows_raw = cur.execute(
            """
            SELECT season, week, owner, score, opp_score, win
            FROM games
            WHERE is_regular = 1 AND season = ?
            """,
            (int(season),),
        ).fetchall()
    else:
        rows_raw = cur.execute(
            """
            SELECT season, week, owner, score, opp_score, win
            FROM games
            WHERE is_regular = 1
            """
        ).fetchall()
    conn.close()

    by_sw: Dict[tuple, List[tuple]] = defaultdict(list)
    for r in rows_raw:
        s_yr, wk = int(r[0]), int(r[1])
        if max_week is not None and season is not None and wk > int(max_week):
            continue
        by_sw[(s_yr, wk)].append((_canon_owner(r[2]), float(r[3]), float(r[4]), int(r[5])))

    stats: Dict[str, dict] = {}
    for (_s, _w), entries in by_sw.items():
        n_teams = len(entries)
        if n_teams < 2:
            continue
        scores = [e[1] for e in entries]
        # Skip in-progress / unplayed weeks (e.g. Thursday kickoff with 0-0 games or <40 avg score)
        if (sum(scores) / n_teams) < 40.0 or any(e[1] == 0.0 and e[2] == 0.0 for e in entries):
            continue
        for own, sc, opp_sc, win in entries:
            if active_only and own.lower() not in active_set:
                continue
            st = stats.setdefault(own, {
                "owner": own,
                "division": div_map.get(own, ""),
                "games": 0,
                "wins": 0,
                "losses": 0,
                "ap_wins": 0.0,
                "ap_games": 0,
                "pf": 0.0,
                "pa": 0.0,
            })
            beat = sum(1 for x in scores if sc > x)
            tied = sum(1 for x in scores if sc == x) - 1  # exclude self
            st["games"] += 1
            st["wins"] += 1 if win == 1 else 0
            st["losses"] += 0 if win == 1 else 1
            st["ap_wins"] += beat + 0.5 * max(0, tied)
            st["ap_games"] += (n_teams - 1)
            st["pf"] += sc
            st["pa"] += opp_sc

    out_rows = []
    is_all_time = season is None
    for own, st in stats.items():
        gp = max(1, st["games"])
        ap_g = max(1, st["ap_games"])
        ap_pct = st["ap_wins"] / ap_g
        exp_wins = ap_pct * st["games"]
        luck_delta = st["wins"] - exp_wins
        out_rows.append({
            "owner": own,
            "division": st["division"],
            "games": st["games"],
            "wins": st["wins"],
            "losses": st["losses"],
            "win_pct": round((st["wins"] / gp) * 100.0, 1),
            "ap_wins": round(st["ap_wins"], 1),
            "ap_losses": round(ap_g - st["ap_wins"], 1),
            "ap_pct": round(ap_pct * 100.0, 1),
            "expected_wins": round(exp_wins, 2),
            "luck_delta": round(luck_delta, 2),
            "avg_pf": round(st["pf"] / gp, 1),
            "avg_pa": round(st["pa"] / gp, 1),
            "verdict": _luck_verdict(luck_delta, is_all_time=is_all_time),
        })

    # Rank schedule difficulty (1 = toughest / highest Avg PA)
    by_pa = sorted(out_rows, key=lambda x: -x["avg_pa"])
    for idx, row in enumerate(by_pa, 1):
        row["sos_rank"] = idx

    # Default sort: All-Play Win % descending ("True Strength Order")
    out_rows.sort(key=lambda x: (-x["ap_pct"], -x["avg_pf"]))
    for idx, row in enumerate(out_rows, 1):
        row["true_rank"] = idx

    return {
        "season": "all" if is_all_time else int(season),
        "rows": out_rows,
        "seasons": avail_seasons,
    }


def compute_weekly_trends(
    completed_games: List[dict],
    full_schedule: List[dict],
    *,
    season: int = int(CURRENT_YEAR),
    completed_week: int = 11,
    n_sims: int = 800,
    final_week_odds: Optional[dict] = None,
) -> dict:
    """
    Computes week-by-week 2026 trajectories (Weeks 1..completed_week):
      - `po`: Playoff Odds % at the end of each week (deterministic seeded Monte Carlo)
      - `rank`: Power Rank (1..16) blending All-Play %, Record, and PF
      - `pf`: Cumulative Points For
      - `wins`: Cumulative Wins
    """
    manifest = load_manifest()
    owners = [t["owner"] for t in manifest.get("teams", [])]
    weeks = list(range(1, max(1, int(completed_week)) + 1))

    series = {
        own: {"po": [], "rank": [], "pf": [], "wins": [], "ap_pct": []}
        for own in owners
    }

    # Group completed games by week
    games_by_wk: Dict[int, List[dict]] = defaultdict(list)
    for g in completed_games:
        games_by_wk[int(g["week"])].append(g)

    cum_scores: Dict[str, List[float]] = {o: [] for o in owners}
    cum_wins: Dict[str, int] = {o: 0 for o in owners}
    cum_ap_wins: Dict[str, float] = {o: 0.0 for o in owners}
    cum_ap_games: Dict[str, int] = {o: 0 for o in owners}

    for wk in weeks:
        wk_games = games_by_wk.get(wk, [])
        wk_scores: Dict[str, float] = {}
        for g in wk_games:
            a, h = g["away"], g["home"]
            sa, sh = float(g["away_score"]), float(g["home_score"])
            wk_scores[a] = sa
            wk_scores[h] = sh
            if a in cum_scores:
                cum_scores[a].append(sa)
            if h in cum_scores:
                cum_scores[h].append(sh)
            if sa >= sh:
                if a in cum_wins:
                    cum_wins[a] += 1
            else:
                if h in cum_wins:
                    cum_wins[h] += 1

        # All-Play update for this week
        all_wk_vals = list(wk_scores.values())
        n_wk = len(all_wk_vals)
        if n_wk >= 2:
            for own, sc in wk_scores.items():
                if own not in cum_ap_wins:
                    continue
                beat = sum(1 for x in all_wk_vals if sc > x)
                tied = sum(1 for x in all_wk_vals if sc == x) - 1
                cum_ap_wins[own] += beat + 0.5 * max(0, tied)
                cum_ap_games[own] += (n_wk - 1)

        # Build empirical power ratings through week `wk` (regressed cleanly in early weeks)
        all_pts_flat = [p for plist in cum_scores.values() for p in plist]
        lg_avg = statistics.mean(all_pts_flat) if all_pts_flat else 100.0
        reg_weight = 4.0  # prior pseudo-games at league avg
        team_power_w = {}
        for own in owners:
            pts_list = cum_scores[own]
            n = len(pts_list)
            raw_mean = statistics.mean(pts_list) if n else lg_avg
            reg_mean = ((sum(pts_list) + reg_weight * lg_avg) / (n + reg_weight))
            std_val = statistics.stdev(pts_list) if n >= 3 else 15.5
            std_val = max(11.0, min(22.0, std_val))
            team_power_w[own] = {"mean": round(reg_mean, 2), "std": round(std_val, 2), "raw_avg": round(raw_mean, 2)}

        # Deterministic seeded Monte Carlo snapshot as of end of week `wk`.
        # For the latest completed week, anchor to the live Optimal Weekly Lineups (+FA Fill)
        # odds (`final_week_odds`) when provided so the endpoint matches the Playoff Machine 1:1.
        if wk == int(completed_week) and final_week_odds:
            odds_w = final_week_odds
        else:
            games_up_to_wk = [g for g in completed_games if int(g["week"]) <= wk]
            random.seed(season * 1000 + wk * 17)
            odds_w = run_playoff_simulation(
                min(14, wk + 1),
                games_up_to_wk,
                full_schedule,
                team_power=team_power_w,
                n_sims=n_sims,
            )

        # Power Rank (1..16): 55% All-Play %, 35% Actual Win %, 10% Normalized PF
        rank_tuples = []
        for own in owners:
            ap = (cum_ap_wins[own] / cum_ap_games[own]) if cum_ap_games[own] > 0 else 0.5
            wp = cum_wins[own] / max(1, len(cum_scores[own]))
            pf_tot = sum(cum_scores[own])
            score_idx = 0.55 * ap + 0.35 * wp + 0.10 * (pf_tot / max(1.0, lg_avg * max(1, wk)))
            rank_tuples.append((own, score_idx, pf_tot))
        rank_tuples.sort(key=lambda x: (-x[1], -x[2]))
        rank_map = {own: idx for idx, (own, _, _) in enumerate(rank_tuples, 1)}

        for own in owners:
            ap_pct = round(((cum_ap_wins[own] / cum_ap_games[own]) * 100.0) if cum_ap_games[own] > 0 else 50.0, 1)
            series[own]["po"].append(round(float(odds_w.get(own, {}).get("playoff_pct", 0.0)), 1))
            series[own]["rank"].append(rank_map[own])
            series[own]["pf"].append(round(sum(cum_scores[own]), 1))
            series[own]["wins"].append(cum_wins[own])
            series[own]["ap_pct"].append(ap_pct)

    return {
        "season": season,
        "weeks": weeks,
        "series": series,
    }


def compute_positional_strength_heatmap(
    db_path: Path = DB_PATH,
    *,
    season: int = int(CURRENT_YEAR),
    max_week: Optional[int] = None,
) -> dict:
    """
    Computes each active manager's average started PPG and Started VORP/Week
    at every fantasy position (QB, RB, WR, TE, K, D/ST) for `season` using
    BFL's unified 16-team positional replacement baselines.
    Automatically excludes in-progress / unplayed weeks.
    """
    if not db_path.exists():
        return {"season": season, "positions": list(POSITIONS), "baselines": {}, "rows": []}

    manifest = load_manifest()
    active_owners = [t["owner"] for t in manifest.get("teams", [])]
    active_set = {o.lower() for o in active_owners}
    div_map = {t["owner"]: t["division"] for t in manifest.get("teams", [])}

    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    baselines = compute_season_replacement_baselines(conn, season)
    cur = conn.cursor()

    # Determine completed weeks (avg team score >= 40) so in-progress Thursday games never dilute averages
    completed_wks = {
        int(r[0]) for r in cur.execute(
            """
            SELECT week FROM games
            WHERE season = ? AND is_consolation = 0
            GROUP BY week
            HAVING AVG(score) >= 40.0
            """,
            (int(season),),
        ).fetchall()
    }
    if max_week is not None:
        completed_wks = {w for w in completed_wks if w <= int(max_week)}

    rows_raw = cur.execute(
        """
        SELECT owner, position, week, points
        FROM player_boxscores
        WHERE season = ?
          AND is_starter = 1
          AND is_consolation = 0
          AND position IN ('QB', 'RB', 'WR', 'TE', 'K', 'D/ST')
        """,
        (int(season),),
    ).fetchall()
    conn.close()

    # Aggregate per (owner, position) and track completed weeks played per owner
    owner_weeks: Dict[str, set] = defaultdict(set)
    agg: Dict[tuple, List[float]] = defaultdict(list)
    for own_raw, pos, wk, pts in rows_raw:
        if completed_wks and int(wk) not in completed_wks:
            continue
        own = _canon_owner(own_raw)
        if own.lower() not in active_set:
            continue
        owner_weeks[own].add(int(wk))
        agg[(own, pos)].append(float(pts or 0.0))

    team_rows = []
    pos_vorp_lists: Dict[str, List[float]] = {p: [] for p in POSITIONS}

    for own in active_owners:
        n_wks = max(1, len(owner_weeks.get(own, set())))
        pos_data = {}
        tot_vorp = 0.0
        skill_scores = []

        for pos in POSITIONS:
            vals = agg.get((own, pos), [])
            repl = baselines.get(pos, FALLBACK_REPLACEMENT_PPG.get(pos, 8.0))
            starts = len(vals)
            avg_ppg = round(sum(vals) / starts, 1) if starts else 0.0
            pos_tot_vorp = sum(v - repl for v in vals)
            vorp_per_wk = round(pos_tot_vorp / n_wks, 1)
            vorp_per_start = round(avg_ppg - repl, 1) if starts else 0.0
            tot_vorp += pos_tot_vorp
            pos_vorp_lists[pos].append(vorp_per_wk)
            pos_data[pos] = {
                "starts": starts,
                "ppg": avg_ppg,
                "vorp_wk": vorp_per_wk,
                "vorp_start": vorp_per_start,
                "total_vorp": round(pos_tot_vorp, 1),
            }
            if pos in ("QB", "RB", "WR", "TE"):
                skill_scores.append((pos, vorp_per_wk))

        best_pos = max(skill_scores, key=lambda x: x[1])[0] if skill_scores else "WR"
        worst_pos = min(skill_scores, key=lambda x: x[1])[0] if skill_scores else "TE"
        team_rows.append({
            "owner": own,
            "division": div_map.get(own, ""),
            "weeks": n_wks,
            "total_vorp_wk": round(tot_vorp / n_wks, 1),
            "total_vorp": round(tot_vorp, 1),
            "best_pos": best_pos,
            "worst_pos": worst_pos,
            "positions": pos_data,
        })

    team_rows.sort(key=lambda r: -r["total_vorp_wk"])

    # Export per-position min/max for smooth color normalization in UI
    pos_bounds = {}
    for pos in POSITIONS:
        vals = pos_vorp_lists[pos] or [0.0]
        pos_bounds[pos] = {"min": min(vals), "max": max(vals)}

    return {
        "season": season,
        "positions": list(POSITIONS),
        "baselines": {p: baselines.get(p, 0.0) for p in POSITIONS},
        "bounds": pos_bounds,
        "rows": team_rows,
    }


def build_analytics_lab_bundle(
    completed_games: List[dict],
    full_schedule: List[dict],
    *,
    db_path: Path = DB_PATH,
    season: int = int(CURRENT_YEAR),
    completed_week: int = 11,
    final_week_odds: Optional[dict] = None,
) -> dict:
    """Assembles the full Analytics Lab payload for `hub_data.json`."""
    luck_current = compute_all_play_luck(db_path, season=season, max_week=completed_week)
    luck_all_time = compute_all_play_luck(db_path, season=None)
    # Also export recent individual seasons (2018..season-1) for quick dropdown exploration
    luck_by_season = {
        str(season): luck_current["rows"],
        "all": luck_all_time["rows"],
    }
    recent_years = [y for y in luck_all_time["seasons"] if 2018 <= y < season]
    for yr in recent_years:
        luck_by_season[str(yr)] = compute_all_play_luck(db_path, season=yr)["rows"]

    trends = compute_weekly_trends(
        completed_games,
        full_schedule,
        season=season,
        completed_week=completed_week,
        n_sims=800,
        final_week_odds=final_week_odds,
    )
    heatmap = compute_positional_strength_heatmap(db_path, season=season, max_week=completed_week)

    return {
        "luck": {
            "seasons": [season] + recent_years,
            "by_season": luck_by_season,
        },
        "trends": trends,
        "heatmap": heatmap,
    }
