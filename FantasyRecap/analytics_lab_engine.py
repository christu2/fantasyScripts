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

from FantasyRecap.core.manifest import extract_credited_owners, load_manifest, load_project_env
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
            SELECT season, week, owner, co_owners, score, opp_score, win
            FROM games
            WHERE is_regular = 1 AND season = ?
            """,
            (int(season),),
        ).fetchall()
    else:
        rows_raw = cur.execute(
            """
            SELECT season, week, owner, co_owners, score, opp_score, win
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
        credited = extract_credited_owners(r[2], r[3] or "", canon_fn=_canon_owner)
        by_sw[(s_yr, wk)].append((_canon_owner(r[2]), credited, float(r[4]), float(r[5]), int(r[6])))

    # First pass: keep only completed weeks and compute each season's exact League Avg PPG
    # so cross-era PF+ and PA+ (100 = league avg that year) normalize 2008 Standard vs 2026 Half-PPR!
    valid_weeks = []
    season_scores: Dict[int, List[float]] = defaultdict(list)
    for (s_yr, wk), entries in by_sw.items():
        n_teams = len(entries)
        if n_teams < 2:
            continue
        scores = [e[2] for e in entries]
        # Skip in-progress / unplayed weeks (e.g. Thursday kickoff with 0-0 games or <40 avg score)
        if (sum(scores) / n_teams) < 40.0 or any(e[2] == 0.0 and e[3] == 0.0 for e in entries):
            continue
        valid_weeks.append((s_yr, wk, entries, scores))
        season_scores[s_yr].extend(scores)

    season_avg = {
        s_yr: (sum(vals) / len(vals))
        for s_yr, vals in season_scores.items()
        if vals
    }

    is_all_time = season is None
    stats: Dict[str, dict] = {}
    for s_yr, _wk, entries, scores in valid_weeks:
        n_teams = len(entries)
        s_mean = season_avg.get(s_yr, 90.0) or 90.0
        for primary_own, credited_owners, sc, opp_sc, win in entries:
            # Credit all distinct co-owners (e.g. Nael + Nitesh in 2022–2025) on All-Time view;
            # on a single-season view credit the primary owner so 16 rows sum cleanly to 0 luck!
            targets = credited_owners if is_all_time else [primary_own]
            beat = sum(1 for x in scores if sc > x)
            tied = sum(1 for x in scores if sc == x) - 1  # exclude self
            for own in targets:
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
                    "pf_plus_sum": 0.0,
                    "pa_plus_sum": 0.0,
                })
                st["games"] += 1
                st["wins"] += 1 if win == 1 else 0
                st["losses"] += 0 if win == 1 else 1
                st["ap_wins"] += beat + 0.5 * max(0, tied)
                st["ap_games"] += (n_teams - 1)
                st["pf"] += sc
                st["pa"] += opp_sc
                st["pf_plus_sum"] += (100.0 * sc / s_mean)
                st["pa_plus_sum"] += (100.0 * opp_sc / s_mean)

    out_rows = []
    is_all_time = season is None
    for own, st in stats.items():
        gp = max(1, st["games"])
        ap_g = max(1, st["ap_games"])
        ap_pct = st["ap_wins"] / ap_g
        exp_wins = ap_pct * st["games"]
        luck_delta = st["wins"] - exp_wins
        pf_plus = round(st["pf_plus_sum"] / gp, 1)
        pa_plus = round(st["pa_plus_sum"] / gp, 1)
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
            "pf_plus": pf_plus,
            "pa_plus": pa_plus,
            "verdict": _luck_verdict(luck_delta, is_all_time=is_all_time),
        })

    # Rank schedule difficulty by Era-Normalized PA+ (1 = toughest schedule relative to each season's scoring)
    by_pa = sorted(out_rows, key=lambda x: (-x["pa_plus"], -x["avg_pa"]))
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

        # Build empirical power ratings through week `wk` using James-Stein Bayesian shrinkage
        # (prior_n = 10.0 pseudo-games at league avg: sigma^2 / tau^2 ~= 18^2 / 5.5^2)
        # so Week 1 noise never drops an 0-1 team to an unrealistic ~10% playoff odds!
        all_pts_flat = [p for plist in cum_scores.values() for p in plist]
        lg_avg = statistics.mean(all_pts_flat) if all_pts_flat else 100.0
        reg_weight = 10.0
        team_power_w = {}
        for own in owners:
            pts_list = cum_scores[own]
            n = len(pts_list)
            raw_mean = statistics.mean(pts_list) if n else lg_avg
            reg_mean = ((sum(pts_list) + reg_weight * lg_avg) / (n + reg_weight))
            std_val = statistics.stdev(pts_list) if n >= 4 else 16.5
            std_val = max(12.0, min(22.0, std_val))
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


def export_franchise_dossiers(
    db_path: Path = DB_PATH,
    *,
    trades_pack: Optional[dict] = None,
    luck_all_time_rows: Optional[List[dict]] = None,
    current_season: int = int(CURRENT_YEAR),
) -> List[dict]:
    """
    Builds unified 18-year Franchise Dossiers for all 16 active BFL managers:
      - Trophy case (rings, runner-ups, division titles, playoff appearances)
      - Career W-L, Playoff W-L, All-Play %, PF+/PA+, Luck Delta, High/Low single-week scores
      - Auto-computed Franchise Archetype Badges (data-driven!)
      - H2H Nemesis & Favorite Victim
      - Best/Worst Career Draft Pick (Draft VORP, 2018–present)
      - Career Trade Record, Net VORP, Best Fleece & Worst Regret (2018–present)
      - Complete Season-by-Season Timeline (2008–present)
    """
    from FantasyRecap.bfl_history_db import compute_position_normalized_draft_value

    manifest = load_manifest()
    teams = manifest.get("teams", [])
    active_owners = [t["owner"] for t in teams]
    active_set = {o.lower() for o in active_owners}
    team_meta = {t["owner"]: t for t in teams}

    if not db_path.exists():
        return []

    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    cur = conn.cursor()

    # 1. Career regular + playoff totals & extreme single-week scores (crediting distinct co-owners!)
    game_rows = cur.execute(
        """
        SELECT season, week, owner, co_owners, opp_owner, score, opp_score, win, is_regular, is_playoffs, is_consolation
        FROM games
        ORDER BY season ASC, week ASC
        """
    ).fetchall()

    # Identify completed weeks to avoid in-progress Thursday 0.0-pt lows
    wk_scores_map: Dict[tuple, List[float]] = defaultdict(list)
    for r in game_rows:
        wk_scores_map[(int(r[0]), int(r[1]))].append(float(r[5] or 0.0))
    valid_sw = {k for k, v in wk_scores_map.items() if v and (sum(v) / len(v)) >= 40.0}

    career_g: Dict[str, dict] = {}
    h2h_rec: Dict[str, Dict[str, List[int]]] = defaultdict(lambda: defaultdict(lambda: [0, 0]))
    for r in game_rows:
        s_yr, wk, own_raw, co_raw, opp_raw, sc, _osc, win, is_reg, is_po, is_con = r
        if (int(s_yr), int(wk)) not in valid_sw:
            continue
        credited = extract_credited_owners(own_raw, co_raw or "", canon_fn=_canon_owner)
        opp = _canon_owner(opp_raw)
        sc_f = float(sc or 0.0)
        for own in credited:
            if own.lower() not in active_set:
                continue
            st = career_g.setdefault(own, {
                "reg_w": 0, "reg_l": 0, "po_w": 0, "po_l": 0,
                "high": None, "low": None, "first_season": int(s_yr),
            })
            st["first_season"] = min(st["first_season"], int(s_yr))
            if int(is_reg) == 1:
                st["reg_w"] += 1 if int(win) == 1 else 0
                st["reg_l"] += 0 if int(win) == 1 else 1
            elif int(is_po) == 1:
                st["po_w"] += 1 if int(win) == 1 else 0
                st["po_l"] += 0 if int(win) == 1 else 1

            if int(is_con) == 0 and sc_f > 0:
                if st["high"] is None or sc_f > st["high"]["score"]:
                    st["high"] = {"score": round(sc_f, 2), "season": int(s_yr), "week": int(wk), "opp": opp}
                if st["low"] is None or sc_f < st["low"]["score"]:
                    st["low"] = {"score": round(sc_f, 2), "season": int(s_yr), "week": int(wk), "opp": opp}

            if opp.lower() in active_set and opp != own and opp not in credited:
                if int(win) == 1:
                    h2h_rec[own][opp][0] += 1
                else:
                    h2h_rec[own][opp][1] += 1

    # 2. Season-by-season timeline from team_seasons (crediting distinct co-owners!)
    ts_rows = cur.execute(
        """
        SELECT season, owner, co_owners, team_name, division_name, wins, losses, points_for,
               division_rank, final_rank, made_playoffs, won_division, won_championship,
               runner_up, is_season_complete
        FROM team_seasons
        ORDER BY season DESC
        """
    ).fetchall()
    conn.close()

    timelines: Dict[str, List[dict]] = defaultdict(list)
    for r in ts_rows:
        primary_own = _canon_owner(r[1])
        credited = extract_credited_owners(r[1], r[2] or "", canon_fn=_canon_owner)
        is_done = bool(r[14])
        for own in credited:
            if own.lower() not in active_set:
                continue
            co_partner = [c for c in credited if c != own]
            tname_disp = r[3] or ""
            if co_partner:
                tname_disp = f"{tname_disp} (w/ {co_partner[0].split()[0]})"
            timelines[own].append({
                "season": int(r[0]),
                "team_name": tname_disp,
                "division": r[4] or "",
                "wins": int(r[5]),
                "losses": int(r[6]),
                "pf": round(float(r[7] or 0.0), 1),
                "div_rank": int(r[8] or 0),
                "final_rank": int(r[9] or 0) if is_done else 0,
                "made_playoffs": bool(r[10]) if is_done else False,
                "won_division": bool(r[11]) if is_done else False,
                "won_champ": bool(r[12]) if is_done else False,
                "runner_up": bool(r[13]) if is_done else False,
                "is_complete": is_done,
                "co_owned": bool(co_partner),
            })

    # 3. All-time Luck/PF+/PA+ lookup
    if luck_all_time_rows is None:
        luck_all_time_rows = compute_all_play_luck(db_path, season=None)["rows"]
    luck_map = {r["owner"]: r for r in luck_all_time_rows}

    # 4. Trade report card lookup (All-Time Actual Lineup VORP)
    trade_card_map: Dict[str, dict] = {}
    if trades_pack and trades_pack.get("all_time"):
        for c in trades_pack["all_time"].get("owner_cards", {}).get("started", []):
            trade_card_map[c["owner"]] = c

    # 5. Per-owner career Draft VORP highlights (2018–present)
    draft_highlights: Dict[str, dict] = {}
    for own in active_owners:
        dp = compute_position_normalized_draft_value(db_path, season=None, owner=own, limit=50)
        best_steal = dp["steals"][0] if dp["steals"] else None
        worst_bust = dp["busts"][0] if dp["busts"] else None
        net_top = round(
            sum(s["delta"] for s in dp["steals"][:5]) + sum(b["delta"] for b in dp["busts"][:5]),
            1,
        )
        draft_highlights[own] = {
            "best_steal": best_steal,
            "worst_bust": worst_bust,
            "top5_net_vorp": net_top,
        }

    dossiers = []
    for own in active_owners:
        meta = team_meta[own]
        prof = meta.get("profile", {})
        cg = career_g.get(own, {"reg_w": 0, "reg_l": 0, "po_w": 0, "po_l": 0, "high": None, "low": None, "first_season": current_season})
        tl = timelines.get(own, [])
        lk = luck_map.get(own, {})
        tc = trade_card_map.get(own, {})
        dh = draft_highlights.get(own, {})

        rings = int(prof.get("rings", 0))
        champ_years = prof.get("champ_years", [])
        ru_years = prof.get("runner_up_years", [])
        div_titles = sum(1 for x in tl if x["won_division"])
        po_apps = sum(1 for x in tl if x["made_playoffs"])
        completed_seasons = sum(1 for x in tl if x["is_complete"])
        po_gp = cg["po_w"] + cg["po_l"]
        po_pct = round((cg["po_w"] / po_gp) * 100.0, 1) if po_gp > 0 else 0.0

        # H2H Nemesis & Favorite Victim (prefer opponents with >= 6 meetings, fallback to >= 2)
        opps = [(opp, wl[0], wl[1], wl[0] + wl[1]) for opp, wl in h2h_rec.get(own, {}).items()]
        min_m = 6 if any(x[3] >= 6 for x in opps) else 2
        elig = [x for x in opps if x[3] >= min_m]
        nemesis = None
        victim = None
        if elig:
            nem = min(elig, key=lambda x: (x[1] / x[3], -x[3]))
            vic = max(elig, key=lambda x: (x[1] / x[3], x[3]))
            if nem[1] < nem[2]:
                nemesis = {"owner": nem[0], "w": nem[1], "l": nem[2], "pct": round((nem[1] / nem[3]) * 100.0, 1)}
            if vic[1] > vic[2]:
                victim = {"owner": vic[0], "w": vic[1], "l": vic[2], "pct": round((vic[1] / vic[3]) * 100.0, 1)}

        # Auto-computed Franchise Archetype Badges
        badges: List[str] = []
        if rings >= 2:
            badges.append("Dynasty Royalty")
        elif rings == 1:
            badges.append("BFL Champion")
        if cg["first_season"] == 2008:
            badges.append("2008 Original")
        if tc.get("net_vorp", 0.0) >= 75.0:
            badges.append("Trade Shark")
        elif tc.get("net_vorp", 0.0) <= -75.0:
            badges.append("Trade Mark")
        if (lk.get("pf_plus") or 100.0) >= 103.0:
            badges.append("Era Scorer")
        if po_gp >= 6 and po_pct >= 55.0:
            badges.append("Playoff Closer")
        if (lk.get("luck_delta") or 0.0) <= -5.0:
            badges.append("Schedule Cursed")
        elif (lk.get("luck_delta") or 0.0) >= 5.0:
            badges.append("Schedule Blessed")
        if dh.get("best_steal") and dh["best_steal"]["delta"] >= 140.0:
            badges.append("Draft Wizard")
        if not badges:
            badges.append("BFL Contender")

        dossiers.append({
            "owner": own,
            "code": meta.get("code", own.split()[0]),
            "division": meta.get("division", ""),
            "team_name": meta.get("team_name", ""),
            "first_season": cg["first_season"],
            "seasons_played": len(tl),
            "completed_seasons": completed_seasons,
            "rings": rings,
            "champ_years": champ_years,
            "runner_up_years": ru_years,
            "div_titles": div_titles,
            "po_apps": po_apps,
            "reg_w": cg["reg_w"],
            "reg_l": cg["reg_l"],
            "reg_pct": round((cg["reg_w"] / max(1, cg["reg_w"] + cg["reg_l"])) * 100.0, 1),
            "po_w": cg["po_w"],
            "po_l": cg["po_l"],
            "po_pct": po_pct,
            "ap_pct": lk.get("ap_pct", 50.0),
            "luck_delta": lk.get("luck_delta", 0.0),
            "pf_plus": lk.get("pf_plus", 100.0),
            "pa_plus": lk.get("pa_plus", 100.0),
            "sos_rank": lk.get("sos_rank", 8),
            "high_game": cg["high"],
            "low_game": cg["low"],
            "badges": badges[:5],
            "nemesis": nemesis,
            "victim": victim,
            "draft": dh,
            "trade": {
                "deals": tc.get("trades", 0),
                "wins": tc.get("wins", 0),
                "losses": tc.get("losses", 0),
                "pushes": tc.get("pushes", 0),
                "net_vorp": tc.get("net_vorp", 0.0),
                "best_trade": tc.get("best_trade"),
                "worst_trade": tc.get("worst_trade"),
            },
            "timeline": tl,
        })

    dossiers.sort(key=lambda d: (-d["rings"], -d["reg_pct"], -d["reg_w"]))
    return dossiers
