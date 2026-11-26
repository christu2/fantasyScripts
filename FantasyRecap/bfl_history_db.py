#!/usr/bin/env python3
"""
BFL Historical Analytics Warehouse & AI Query Engine
====================================================
Maintains a local SQLite database (`live_state/bfl_history.db`) of:
  - `games`: Every BFL matchup from 2008–present (team scores, opponents, outcomes, playoff tiers).
  - `player_boxscores`: Every NFL player lineup slot & score from 2018–present (QB/RB/WR/TE/FLEX/K/D/ST/Bench/IR).

Supports:
  1. Incremental syncing from ESPN Fantasy API (`--sync`)
  2. Read-only SQL querying (`--sql "SELECT ..."`)
  3. Natural-language AI Q&A powered by Gemini Text-to-SQL (`--ask "question"`)
  4. Database coverage summary (`--stats`)
"""

import os
import re
import sys
import json
import sqlite3
import argparse
from datetime import datetime
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from dotenv import load_dotenv

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from FantasyRecap.core.manifest import get_espn_credentials, load_project_env
load_project_env()

from FantasyRecap.config import CURRENT_YEAR, LEAGUE_ID
from FantasyRecap.core.espn_client import POSITION_MAP, PRO_TEAM_MAP, SLOT_MAP
from FantasyRecap.core.manifest import get_championships, get_team_details
from FantasyRecap.league_history_intelligence import standardize_name

DB_PATH = SCRIPT_DIR / "live_state" / "bfl_history.db"
DB_PATH.parent.mkdir(parents=True, exist_ok=True)


def get_espn_creds():
    creds = get_espn_credentials()
    return creds["league_id"], creds["cookies"]


def standardize_owner_for_season(raw_name: str, season: int) -> str:
    """Standardizes historical owner names while respecting the 2025+ Sydney/Emelie split."""
    low = (raw_name or "").strip().lower()
    if not low:
        return "Unknown"
    if season < 2025 and low in ("sydney christus", "sydney kite", "sydney miller"):
        return "Sydney Miller"
    if low == "emelie lovasko":
        return "Emelie Lovasko"
    if low == "rej hoxha":
        return "rej hoxha"
    return standardize_name(raw_name)


def init_db(conn: sqlite3.Connection):
    cur = conn.cursor()
    cur.executescript(
        """
        CREATE TABLE IF NOT EXISTS games (
            season INTEGER NOT NULL,
            week INTEGER NOT NULL,
            matchup_id INTEGER NOT NULL,
            playoff_tier TEXT NOT NULL,
            is_regular INTEGER NOT NULL,
            is_playoffs INTEGER NOT NULL,
            is_consolation INTEGER NOT NULL,
            team_id INTEGER NOT NULL,
            team_name TEXT NOT NULL,
            owner TEXT NOT NULL,
            raw_owner TEXT NOT NULL,
            co_owners TEXT NOT NULL,
            score REAL NOT NULL,
            opp_team_id INTEGER NOT NULL,
            opp_team_name TEXT NOT NULL,
            opp_owner TEXT NOT NULL,
            opp_raw_owner TEXT NOT NULL,
            opp_score REAL NOT NULL,
            win INTEGER NOT NULL,
            margin REAL NOT NULL,
            PRIMARY KEY (season, week, team_id)
        );

        CREATE TABLE IF NOT EXISTS player_boxscores (
            season INTEGER NOT NULL,
            week INTEGER NOT NULL,
            matchup_id INTEGER NOT NULL,
            team_id INTEGER NOT NULL,
            team_name TEXT NOT NULL,
            owner TEXT NOT NULL,
            co_owners TEXT NOT NULL,
            opp_team_id INTEGER NOT NULL,
            opp_owner TEXT NOT NULL,
            playoff_tier TEXT NOT NULL,
            is_regular INTEGER NOT NULL,
            is_playoffs INTEGER NOT NULL,
            is_consolation INTEGER NOT NULL,
            player_id INTEGER NOT NULL,
            player_name TEXT NOT NULL,
            pro_team TEXT NOT NULL,
            position TEXT NOT NULL,
            slot_id INTEGER NOT NULL,
            slot TEXT NOT NULL,
            is_starter INTEGER NOT NULL,
            points REAL NOT NULL,
            projected_points REAL NOT NULL,
            PRIMARY KEY (season, week, team_id, player_id)
        );

        CREATE TABLE IF NOT EXISTS team_seasons (
            season INTEGER NOT NULL,
            team_id INTEGER NOT NULL,
            team_name TEXT NOT NULL,
            owner TEXT NOT NULL,
            raw_owner TEXT NOT NULL,
            co_owners TEXT NOT NULL,
            division_id INTEGER NOT NULL,
            division_name TEXT NOT NULL,
            wins INTEGER NOT NULL,
            losses INTEGER NOT NULL,
            ties INTEGER NOT NULL,
            win_pct REAL NOT NULL,
            points_for REAL NOT NULL,
            points_against REAL NOT NULL,
            division_wins INTEGER NOT NULL,
            division_losses INTEGER NOT NULL,
            division_ties INTEGER NOT NULL,
            division_rank INTEGER NOT NULL,
            playoff_seed INTEGER NOT NULL,
            final_rank INTEGER NOT NULL,
            made_playoffs INTEGER NOT NULL,
            won_division INTEGER NOT NULL,
            won_championship INTEGER NOT NULL,
            runner_up INTEGER NOT NULL,
            is_season_complete INTEGER NOT NULL,
            PRIMARY KEY (season, team_id)
        );

        CREATE TABLE IF NOT EXISTS draft_picks (
            season INTEGER NOT NULL,
            overall_pick INTEGER NOT NULL,
            round_id INTEGER NOT NULL,
            round_pick INTEGER NOT NULL,
            team_id INTEGER NOT NULL,
            team_name TEXT NOT NULL,
            owner TEXT NOT NULL,
            co_owners TEXT NOT NULL,
            player_id INTEGER NOT NULL,
            player_name TEXT NOT NULL,
            pro_team TEXT NOT NULL,
            position TEXT NOT NULL,
            pos_draft_rank INTEGER NOT NULL,
            bid_amount INTEGER NOT NULL DEFAULT 0,
            keeper INTEGER NOT NULL DEFAULT 0,
            auto_drafted INTEGER NOT NULL DEFAULT 0,
            season_points REAL NOT NULL DEFAULT 0.0,
            season_ppg REAL NOT NULL DEFAULT 0.0,
            overall_finish_rank INTEGER NOT NULL DEFAULT 0,
            pos_finish_rank INTEGER NOT NULL DEFAULT 0,
            overall_value_delta INTEGER NOT NULL DEFAULT 0,
            pos_value_delta INTEGER NOT NULL DEFAULT 0,
            round_avg_points REAL NOT NULL DEFAULT 0.0,
            points_above_round_avg REAL NOT NULL DEFAULT 0.0,
            started_points_for_drafter REAL NOT NULL DEFAULT 0.0,
            weeks_started_for_drafter INTEGER NOT NULL DEFAULT 0,
            has_season_stats INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (season, overall_pick)
        );

        CREATE TABLE IF NOT EXISTS sync_log (
            season INTEGER NOT NULL,
            week INTEGER NOT NULL,
            has_games INTEGER NOT NULL DEFAULT 0,
            has_boxscores INTEGER NOT NULL DEFAULT 0,
            synced_at TEXT NOT NULL,
            PRIMARY KEY (season, week)
        );

        CREATE TABLE IF NOT EXISTS trades (
            trade_id TEXT PRIMARY KEY,
            season INTEGER NOT NULL,
            trade_week INTEGER NOT NULL,
            effective_week INTEGER NOT NULL,
            team_a_id INTEGER NOT NULL,
            owner_a TEXT NOT NULL,
            team_b_id INTEGER NOT NULL,
            owner_b TEXT NOT NULL,
            process_date_ms INTEGER NOT NULL DEFAULT 0,
            source TEXT NOT NULL DEFAULT 'espn_tx'
        );

        CREATE TABLE IF NOT EXISTS trade_items (
            trade_id TEXT NOT NULL,
            season INTEGER NOT NULL,
            from_team_id INTEGER NOT NULL,
            from_owner TEXT NOT NULL,
            to_team_id INTEGER NOT NULL,
            to_owner TEXT NOT NULL,
            player_id INTEGER NOT NULL,
            player_name TEXT NOT NULL,
            position TEXT NOT NULL,
            PRIMARY KEY (trade_id, player_id, from_team_id, to_team_id)
        );

        CREATE INDEX IF NOT EXISTS idx_games_season_owner ON games(season, owner);
        CREATE INDEX IF NOT EXISTS idx_games_opp_owner ON games(season, opp_owner);
        CREATE INDEX IF NOT EXISTS idx_pb_season_pos ON player_boxscores(season, position, is_starter);
        CREATE INDEX IF NOT EXISTS idx_pb_owner ON player_boxscores(owner, season, week);
        CREATE INDEX IF NOT EXISTS idx_pb_opp_owner ON player_boxscores(opp_owner, season, week);
        CREATE INDEX IF NOT EXISTS idx_pb_player ON player_boxscores(player_name, season);
        CREATE INDEX IF NOT EXISTS idx_ts_season_div ON team_seasons(season, division_id);
        CREATE INDEX IF NOT EXISTS idx_ts_owner_season ON team_seasons(owner, season);
        CREATE INDEX IF NOT EXISTS idx_dp_owner_season ON draft_picks(owner, season);
        CREATE INDEX IF NOT EXISTS idx_dp_player_season ON draft_picks(player_name, season);
        CREATE INDEX IF NOT EXISTS idx_dp_pos_season ON draft_picks(position, season);
        CREATE INDEX IF NOT EXISTS idx_trades_season ON trades(season, trade_week);
        CREATE INDEX IF NOT EXISTS idx_trades_owners ON trades(owner_a, owner_b);
        CREATE INDEX IF NOT EXISTS idx_trade_items_season ON trade_items(season, player_id);
        """
    )
    conn.commit()


def _extract_teams_map(data: dict, season: int) -> dict:
    members = {}
    for m in data.get("members", []):
        f = m.get("firstName", "").strip()
        l = m.get("lastName", "").strip()
        full = f"{f} {l}".strip() or m.get("displayName", "").strip()
        members[m["id"]] = full

    teams = {}
    for t in data.get("teams", []):
        tid = t["id"]
        tname = t.get("name") or f"{t.get('location', '')} {t.get('nickname', '')}".strip() or f"Team {tid}"
        primary_oid = t.get("primaryOwner") or (t.get("owners") or [None])[0]
        raw_primary = members.get(primary_oid, "") or tname
        std_primary = standardize_owner_for_season(raw_primary, season)

        all_owners = []
        for oid in t.get("owners", []):
            raw_o = members.get(oid, "")
            if raw_o:
                so = standardize_owner_for_season(raw_o, season)
                if so not in all_owners:
                    all_owners.append(so)
        if std_primary not in all_owners and std_primary:
            all_owners.insert(0, std_primary)

        teams[tid] = {
            "id": tid,
            "name": tname.strip(),
            "owner": std_primary,
            "raw_owner": raw_primary,
            "co_owners": ", ".join(all_owners),
        }
    return teams


def _fetch_season_summary(league_id: str, cookies: dict, season: int, is_current: bool) -> dict:
    hist_lids = list(dict.fromkeys([league_id, "157057"]))
    for lid in hist_lids:
        if is_current or season >= 2018:
            url = f"https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/{season}/segments/0/leagues/{lid}?view=mMatchupScore&view=mTeam&view=mSettings&view=mMembers"
        else:
            url = f"https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/leagueHistory/{lid}?seasonId={season}&view=mMatchupScore&view=mTeam&view=mSettings&view=mMembers"
        try:
            r = requests.get(url, cookies=cookies, timeout=10)
            if r.status_code == 200:
                raw = r.json()
                return raw[0] if isinstance(raw, list) and raw else raw
        except Exception:
            continue
    return {}


def _fetch_week_boxscore(league_id: str, cookies: dict, season: int, week: int) -> tuple:
    hist_lids = list(dict.fromkeys([league_id, "157057"]))
    for lid in hist_lids:
        url = (
            f"https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/{season}"
            f"/segments/0/leagues/{lid}?view=mMatchupScore&view=mBoxscore&view=mTeam&view=mMembers&view=mSettings&scoringPeriodId={week}"
        )
        try:
            r = requests.get(url, cookies=cookies, timeout=12)
            if r.status_code == 200:
                return week, r.json()
        except Exception:
            continue
    return week, {}


def _parse_games_from_schedule(data: dict, season: int, reg_period_count: int, max_week: int = 99) -> list:
    teams = _extract_teams_map(data, season)
    rows = []
    for g in data.get("schedule", []):
        wk = g.get("matchupPeriodId", 0)
        if wk < 1 or wk > max_week:
            continue
        away = g.get("away") or {}
        home = g.get("home") or {}
        aid = away.get("teamId")
        hid = home.get("teamId")
        if aid not in teams or hid not in teams:
            continue

        a_pts = round(float(away.get("totalPoints", 0.0) or away.get("totalPointsLive", 0.0) or 0.0), 2)
        h_pts = round(float(home.get("totalPoints", 0.0) or home.get("totalPointsLive", 0.0) or 0.0), 2)
        if a_pts == 0.0 and h_pts == 0.0:
            continue

        tier = str(g.get("playoffTierType", "NONE"))
        is_playoffs = 1 if tier == "WINNERS_BRACKET" else 0
        is_consolation = 1 if ("CONSOLATION" in tier or "LOSER" in tier) else 0
        is_regular = 1 if (tier == "NONE" and wk <= reg_period_count) else 0
        mid = int(g.get("id", 0))
        margin = round(abs(a_pts - h_pts), 2)

        ta, th = teams[aid], teams[hid]
        rows.append((
            season, wk, mid, tier, is_regular, is_playoffs, is_consolation,
            aid, ta["name"], ta["owner"], ta["raw_owner"], ta["co_owners"], a_pts,
            hid, th["name"], th["owner"], th["raw_owner"], h_pts,
            1 if a_pts > h_pts else 0, margin,
        ))
        rows.append((
            season, wk, mid, tier, is_regular, is_playoffs, is_consolation,
            hid, th["name"], th["owner"], th["raw_owner"], th["co_owners"], h_pts,
            aid, ta["name"], ta["owner"], ta["raw_owner"], a_pts,
            1 if h_pts > a_pts else 0, margin,
        ))
    return rows


def _parse_week_boxscores(data: dict, season: int, target_week: int, reg_period_count: int) -> tuple:
    teams = _extract_teams_map(data, season)
    pb_rows = []
    game_rows = []

    for g in data.get("schedule", []):
        if g.get("matchupPeriodId") != target_week:
            continue
        away = g.get("away") or {}
        home = g.get("home") or {}
        aid = away.get("teamId")
        hid = home.get("teamId")
        if aid not in teams or hid not in teams:
            continue

        tier = str(g.get("playoffTierType", "NONE"))
        is_playoffs = 1 if tier == "WINNERS_BRACKET" else 0
        is_consolation = 1 if ("CONSOLATION" in tier or "LOSER" in tier) else 0
        is_regular = 1 if (tier == "NONE" and target_week <= reg_period_count) else 0
        mid = int(g.get("id", 0))

        ta, th = teams[aid], teams[hid]

        def extract_side(side_dict, my_team, opp_team):
            entries = (
                side_dict.get("rosterForCurrentScoringPeriod", {}).get("entries")
                or side_dict.get("rosterForMatchupPeriod", {}).get("entries")
                or []
            )
            starter_sum = 0.0
            side_pb = []
            for e in entries:
                pe = e.get("playerPoolEntry") or {}
                pl = pe.get("player") or {}
                pid = pl.get("id")
                if not pid:
                    continue
                pos_id = pl.get("defaultPositionId", 0)
                slot_id = e.get("lineupSlotId", 20)
                pts = float(pe.get("appliedStatTotal", 0.0) or 0.0)

                proj = 0.0
                for s in pl.get("stats", []):
                    if s.get("statSourceId") == 1 and s.get("scoringPeriodId") == target_week:
                        proj = float(s.get("appliedTotal", 0.0) or 0.0)
                        break

                if pts == 0.0:
                    for s in pl.get("stats", []):
                        if s.get("statSourceId") == 0 and s.get("scoringPeriodId") == target_week:
                            pts = float(s.get("appliedTotal", 0.0) or 0.0)
                            break

                is_starter = 1 if slot_id not in (20, 21) else 0
                if is_starter:
                    starter_sum += pts

                side_pb.append((
                    season, target_week, mid, my_team["id"], my_team["name"], my_team["owner"], my_team["co_owners"],
                    opp_team["id"], opp_team["owner"],
                    tier, is_regular, is_playoffs, is_consolation,
                    int(pid), pl.get("fullName", f"Player {pid}"),
                    PRO_TEAM_MAP.get(pl.get("proTeamId", 0), "NFL"),
                    POSITION_MAP.get(pos_id, "FLEX"), int(slot_id), SLOT_MAP.get(slot_id, "Bench"),
                    is_starter, round(pts, 2), round(proj, 2),
                ))
            api_pts = float(side_dict.get("totalPoints", 0.0) or side_dict.get("totalPointsLive", 0.0) or 0.0)
            final_pts = round(max(api_pts, starter_sum) if api_pts == 0.0 else api_pts, 2)
            return final_pts, side_pb

        a_pts, a_pb = extract_side(away, ta, th)
        h_pts, h_pb = extract_side(home, th, ta)

        if a_pts == 0.0 and h_pts == 0.0 and not a_pb and not h_pb:
            continue

        pb_rows.extend(a_pb)
        pb_rows.extend(h_pb)

        margin = round(abs(a_pts - h_pts), 2)
        game_rows.append((
            season, target_week, mid, tier, is_regular, is_playoffs, is_consolation,
            aid, ta["name"], ta["owner"], ta["raw_owner"], ta["co_owners"], a_pts,
            hid, th["name"], th["owner"], th["raw_owner"], h_pts,
            1 if a_pts > h_pts else 0, margin,
        ))
        game_rows.append((
            season, target_week, mid, tier, is_regular, is_playoffs, is_consolation,
            hid, th["name"], th["owner"], th["raw_owner"], th["co_owners"], h_pts,
            aid, ta["name"], ta["owner"], ta["raw_owner"], a_pts,
            1 if h_pts > a_pts else 0, margin,
        ))

    return game_rows, pb_rows


def _parse_team_seasons(data: dict, season: int, current_yr: int) -> list:
    """
    Extracts per-season division membership, standings, division rank, playoff seed,
    final league finish, and championship flags for every team in `season`.
    """
    teams_map = _extract_teams_map(data, season)
    sched_settings = data.get("settings", {}).get("scheduleSettings", {})
    div_names = {
        int(d["id"]): (d.get("name") or f"Division {d['id']}").strip()
        for d in sched_settings.get("divisions", [])
        if "id" in d
    }
    playoff_team_count = int(sched_settings.get("playoffTeamCount") or (7 if season >= 2021 else 6))

    # Identify teams that actually appeared in the Winners Bracket playoffs from schedule
    playoff_team_ids = set()
    for g in data.get("schedule", []):
        if str(g.get("playoffTierType", "NONE")) == "WINNERS_BRACKET":
            for side_key in ("away", "home"):
                tid = (g.get(side_key) or {}).get("teamId")
                if tid is not None:
                    playoff_team_ids.add(tid)

    raw_teams = data.get("teams", [])
    has_final_ranks = any(int(t.get("rankCalculatedFinal") or 0) == 1 for t in raw_teams)
    is_complete = 1 if (season < current_yr or has_final_ranks) else 0

    # Cross-check official championship honor roll from manifest for completed seasons
    champ_entry = next((c for c in get_championships() if int(c["year"]) == int(season)), None)

    parsed = []
    by_div = {}
    for t in raw_teams:
        tid = t.get("id")
        if tid not in teams_map:
            continue
        tinfo = teams_map[tid]
        div_id = int(t.get("divisionId", 0) or 0)
        div_name = div_names.get(div_id, f"Division {div_id}")

        rec = t.get("record") or {}
        overall = rec.get("overall") or {}
        div_rec = rec.get("division") or {}

        wins = int(overall.get("wins", 0) or 0)
        losses = int(overall.get("losses", 0) or 0)
        ties = int(overall.get("ties", 0) or 0)
        gp = wins + losses + ties
        win_pct = round(float(overall.get("percentage", (wins + 0.5 * ties) / gp if gp > 0 else 0.0)), 4)
        pf = round(float(overall.get("pointsFor", 0.0) or 0.0), 2)
        pa = round(float(overall.get("pointsAgainst", 0.0) or 0.0), 2)

        div_w = int(div_rec.get("wins", 0) or 0)
        div_l = int(div_rec.get("losses", 0) or 0)
        div_t = int(div_rec.get("ties", 0) or 0)

        playoff_seed = int(t.get("playoffSeed") or 0)
        final_rank = int(t.get("rankCalculatedFinal") or 0)
        if final_rank <= 0:
            final_rank = int(t.get("currentProjectedRank") or playoff_seed or 0)

        entry = {
            "season": season,
            "team_id": tid,
            "team_name": tinfo["name"],
            "owner": tinfo["owner"],
            "raw_owner": tinfo["raw_owner"],
            "co_owners": tinfo["co_owners"],
            "division_id": div_id,
            "division_name": div_name,
            "wins": wins,
            "losses": losses,
            "ties": ties,
            "win_pct": win_pct,
            "points_for": pf,
            "points_against": pa,
            "division_wins": div_w,
            "division_losses": div_l,
            "division_ties": div_t,
            "playoff_seed": playoff_seed,
            "final_rank": final_rank,
        }
        parsed.append(entry)
        by_div.setdefault(div_id, []).append(entry)

    # Compute exact division_rank (1 = 1st in division) within each division
    for div_id, members in by_div.items():
        all_have_seeds = all(m["playoff_seed"] > 0 for m in members)
        if all_have_seeds:
            members.sort(key=lambda m: (m["playoff_seed"], -m["wins"], -m["points_for"]))
        else:
            members.sort(key=lambda m: (m["wins"], m["win_pct"], m["division_wins"], m["points_for"]), reverse=True)
        for rank_idx, m in enumerate(members, 1):
            m["division_rank"] = rank_idx

    rows = []
    for m in parsed:
        co_list = [x.strip().lower() for x in m["co_owners"].split(",") if x.strip()]
        own_low = m["owner"].lower()

        if is_complete and champ_entry:
            c_low = standardize_name(champ_entry["champ"]).lower()
            r_low = standardize_name(champ_entry["runner_up"]).lower()
            won_champ = 1 if (own_low == c_low or c_low in co_list) else 0
            runner_up = 1 if (own_low == r_low or r_low in co_list) else 0
        else:
            won_champ = 1 if (is_complete and m["final_rank"] == 1) else 0
            runner_up = 1 if (is_complete and m["final_rank"] == 2) else 0

        if playoff_team_ids:
            made_po = 1 if m["team_id"] in playoff_team_ids else 0
        elif m["playoff_seed"] > 0:
            made_po = 1 if m["playoff_seed"] <= playoff_team_count else 0
        else:
            made_po = 0

        won_div = 1 if m["division_rank"] == 1 else 0

        rows.append((
            m["season"],
            m["team_id"],
            m["team_name"],
            m["owner"],
            m["raw_owner"],
            m["co_owners"],
            m["division_id"],
            m["division_name"],
            m["wins"],
            m["losses"],
            m["ties"],
            m["win_pct"],
            m["points_for"],
            m["points_against"],
            m["division_wins"],
            m["division_losses"],
            m["division_ties"],
            m["division_rank"],
            m["playoff_seed"],
            m["final_rank"],
            made_po,
            won_div,
            won_champ,
            runner_up,
            is_complete,
        ))
    return rows


def _fetch_season_draft_and_stats(league_id: str, cookies: dict, season: int, is_current: bool) -> tuple:
    """
    Fetches `mDraftDetail` + `mTeam` + `mMembers` for `season`, then batch-fetches
    full-season player metadata & BFL-scored totals from `kona_player_info`.
    Returns `(draft_data_dict, player_info_by_id)`.
    """
    hist_lids = list(dict.fromkeys([league_id, "157057"]))
    draft_data = {}
    used_lid = league_id
    for lid in hist_lids:
        if is_current or season >= 2018:
            url = (
                f"https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/{season}"
                f"/segments/0/leagues/{lid}?view=mDraftDetail&view=mTeam&view=mMembers"
            )
        else:
            url = (
                f"https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/leagueHistory/{lid}"
                f"?seasonId={season}&view=mDraftDetail&view=mTeam&view=mMembers"
            )
        try:
            r = requests.get(url, cookies=cookies, timeout=12)
            if r.status_code == 200:
                raw = r.json()
                d = raw[0] if isinstance(raw, list) and raw else raw
                if d.get("draftDetail", {}).get("picks"):
                    draft_data = d
                    used_lid = lid
                    break
        except Exception:
            continue

    if not draft_data:
        return {}, {}

    picks = draft_data.get("draftDetail", {}).get("picks", [])
    all_pids = sorted({int(pk["playerId"]) for pk in picks if pk.get("playerId")})
    player_info = {}

    # Batch-fetch kona_player_info (in chunks of 100 with explicit limit) for full-season BFL scoring & metadata
    if all_pids and (is_current or season >= 2018):
        kona_url = (
            f"https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/{season}"
            f"/segments/0/leagues/{used_lid}?view=kona_player_info"
        )
        for i in range(0, len(all_pids), 100):
            chunk = all_pids[i : i + 100]
            headers = {
                "x-fantasy-filter": json.dumps({
                    "players": {
                        "filterIds": {"value": chunk},
                        "limit": len(chunk) + 10,
                    }
                })
            }
            try:
                kr = requests.get(kona_url, cookies=cookies, headers=headers, timeout=15)
                if kr.status_code == 200:
                    for pe in kr.json().get("players", []):
                        pl = pe.get("player") or {}
                        pid = pl.get("id")
                        if not pid:
                            continue
                        season_pts = 0.0
                        season_ppg = 0.0
                        has_stat = False
                        for st in pl.get("stats", []):
                            if (
                                st.get("seasonId") == season
                                and st.get("statSourceId") == 0
                                and st.get("statSplitTypeId") == 0
                            ):
                                season_pts = round(float(st.get("appliedTotal", 0.0) or 0.0), 2)
                                season_ppg = round(float(st.get("appliedAverage", 0.0) or 0.0), 2)
                                has_stat = True
                                break
                        player_info[int(pid)] = {
                            "name": pl.get("fullName", f"Player {pid}"),
                            "position": POSITION_MAP.get(pl.get("defaultPositionId", 0), "FLEX"),
                            "pro_team": PRO_TEAM_MAP.get(pl.get("proTeamId", 0), "NFL"),
                            "season_points": season_pts,
                            "season_ppg": season_ppg,
                            "has_season_stats": 1 if has_stat else 0,
                        }
            except Exception:
                continue

    return draft_data, player_info


def _parse_draft_picks(
    draft_data: dict,
    player_info: dict,
    season: int,
    conn: sqlite3.Connection,
) -> list:
    """
    Combines ESPN `mDraftDetail`, `kona_player_info` season totals, and local `player_boxscores`
    (for drafter starter points & fallback player metadata/totals) into `draft_picks` rows,
    complete with positional draft rank, final positional finish rank, value deltas,
    and points above round average.
    """
    teams_map = _extract_teams_map(draft_data, season)
    picks = sorted(
        draft_data.get("draftDetail", {}).get("picks", []),
        key=lambda x: int(x.get("overallPickNumber") or 9999),
    )
    if not picks:
        return []

    cur = conn.cursor()

    # Local boxscore lookup for player metadata, fallback season totals, and drafter-specific started points
    pb_meta = {}
    pb_season_totals = {}
    pb_drafter_started = {}
    for row in cur.execute(
        """
        SELECT player_id, player_name, position, pro_team,
               ROUND(SUM(week_pts), 2) as tot_pts,
               COUNT(*) as wks
        FROM (
            SELECT player_id, player_name, position, pro_team, week, MAX(points) as week_pts
            FROM player_boxscores
            WHERE season = ?
            GROUP BY player_id, week
        )
        GROUP BY player_id
        """,
        (season,),
    ).fetchall():
        pid = int(row[0])
        pb_meta[pid] = {"name": row[1], "position": row[2], "pro_team": row[3]}
        pb_season_totals[pid] = (float(row[4] or 0.0), int(row[5] or 0))

    for row in cur.execute(
        """
        SELECT team_id, player_id, ROUND(SUM(points), 2), COUNT(*)
        FROM player_boxscores
        WHERE season = ? AND is_starter = 1
        GROUP BY team_id, player_id
        """,
        (season,),
    ).fetchall():
        pb_drafter_started[(int(row[0]), int(row[1]))] = (float(row[2] or 0.0), int(row[3] or 0))

    pos_draft_counter = {}
    parsed = []
    for pk in picks:
        ovr = int(pk.get("overallPickNumber") or 0)
        pid = int(pk.get("playerId") or 0)
        tid = int(pk.get("teamId") or 0)
        if not ovr or not pid:
            continue
        tinfo = teams_map.get(
            tid,
            {"name": f"Team {tid}", "owner": "Unknown", "co_owners": ""},
        )
        pmeta = player_info.get(pid) or pb_meta.get(pid) or {}
        pname = pmeta.get("name", f"Player {pid}")
        pos = pmeta.get("position", "FLEX")
        pro_team = pmeta.get("pro_team", "NFL")

        s_pts = float(pmeta.get("season_points", 0.0) or 0.0)
        s_ppg = float(pmeta.get("season_ppg", 0.0) or 0.0)
        has_stats = int(pmeta.get("has_season_stats", 0))

        # Fallback to summed weekly boxscores if kona_player_info didn't return season total
        if not has_stats and pid in pb_season_totals:
            s_pts, wks = pb_season_totals[pid]
            s_ppg = round(s_pts / wks, 2) if wks > 0 else 0.0
            has_stats = 1

        pos_draft_counter[pos] = pos_draft_counter.get(pos, 0) + 1
        pos_draft_rank = pos_draft_counter[pos]
        started_pts, started_wks = pb_drafter_started.get((tid, pid), (0.0, 0))

        parsed.append({
            "season": season,
            "overall_pick": ovr,
            "round_id": int(pk.get("roundId") or 0),
            "round_pick": int(pk.get("roundPickNumber") or 0),
            "team_id": tid,
            "team_name": tinfo["name"],
            "owner": tinfo["owner"],
            "co_owners": tinfo["co_owners"],
            "player_id": pid,
            "player_name": pname,
            "pro_team": pro_team,
            "position": pos,
            "pos_draft_rank": pos_draft_rank,
            "bid_amount": int(pk.get("bidAmount") or 0),
            "keeper": 1 if pk.get("keeper") else 0,
            "auto_drafted": 1 if int(pk.get("autoDraftTypeId") or 0) > 0 else 0,
            "season_points": round(s_pts, 2),
            "season_ppg": round(s_ppg, 2),
            "started_points_for_drafter": round(started_pts, 2),
            "weeks_started_for_drafter": started_wks,
            "has_season_stats": has_stats,
        })

    # Compute overall & positional finish ranks + round average baseline when season stats exist
    any_stats = any(p["has_season_stats"] == 1 and p["season_points"] > 0 for p in parsed)
    if any_stats:
        by_ovr = sorted(parsed, key=lambda x: x["season_points"], reverse=True)
        for rank_idx, item in enumerate(by_ovr, 1):
            item["overall_finish_rank"] = rank_idx
            item["overall_value_delta"] = item["overall_pick"] - rank_idx

        by_pos = {}
        for item in parsed:
            by_pos.setdefault(item["position"], []).append(item)
        for pos, items in by_pos.items():
            items.sort(key=lambda x: x["season_points"], reverse=True)
            for rank_idx, item in enumerate(items, 1):
                item["pos_finish_rank"] = rank_idx
                item["pos_value_delta"] = item["pos_draft_rank"] - rank_idx

        by_round = {}
        for item in parsed:
            by_round.setdefault(item["round_id"], []).append(item["season_points"])
        round_avgs = {
            rnd: round(sum(vals) / len(vals), 2) if vals else 0.0
            for rnd, vals in by_round.items()
        }
        for item in parsed:
            ravg = round_avgs.get(item["round_id"], 0.0)
            item["round_avg_points"] = ravg
            item["points_above_round_avg"] = round(item["season_points"] - ravg, 2)
    else:
        for item in parsed:
            item["overall_finish_rank"] = 0
            item["pos_finish_rank"] = 0
            item["overall_value_delta"] = 0
            item["pos_value_delta"] = 0
            item["round_avg_points"] = 0.0
            item["points_above_round_avg"] = 0.0

    rows = [
        (
            p["season"], p["overall_pick"], p["round_id"], p["round_pick"],
            p["team_id"], p["team_name"], p["owner"], p["co_owners"],
            p["player_id"], p["player_name"], p["pro_team"], p["position"],
            p["pos_draft_rank"], p["bid_amount"], p["keeper"], p["auto_drafted"],
            p["season_points"], p["season_ppg"],
            p["overall_finish_rank"], p["pos_finish_rank"],
            p["overall_value_delta"], p["pos_value_delta"],
            p["round_avg_points"], p["points_above_round_avg"],
            p["started_points_for_drafter"], p["weeks_started_for_drafter"],
            p["has_season_stats"],
        )
        for p in parsed
    ]
    return rows


def sync_history_db(
    start_year: int = 2008,
    end_year: int = None,
    force_seasons: set = None,
    boxscore_start_year: int = 2018,
    quiet: bool = False,
):
    """Incrementally syncs BFL games (2008+), team_seasons/divisions (2008+), player boxscores (2018+), and draft_picks (2018+) into SQLite."""
    current_yr = int(os.getenv("SEASON_YEAR") or CURRENT_YEAR)
    end_year = end_year or current_yr
    force_seasons = force_seasons or set()
    league_id, cookies = get_espn_creds()

    conn = sqlite3.connect(DB_PATH)
    init_db(conn)
    cur = conn.cursor()

    existing_seasons_games = {
        row[0] for row in cur.execute("SELECT DISTINCT season FROM sync_log WHERE has_games = 1").fetchall()
    }
    existing_box_weeks = {
        (row[0], row[1]) for row in cur.execute("SELECT season, week FROM sync_log WHERE has_boxscores = 1").fetchall()
    }
    existing_seasons_standings = {
        row[0] for row in cur.execute("SELECT DISTINCT season FROM team_seasons").fetchall()
    }
    existing_seasons_drafts = {
        row[0] for row in cur.execute("SELECT DISTINCT season FROM draft_picks WHERE has_season_stats = 1").fetchall()
    }

    now_iso = datetime.now().isoformat(timespec="seconds")

    # Fast parallel backfill for past seasons that only need `team_seasons` (divisions/standings)
    standings_only_years = []
    full_sync_years = []
    for yr in range(start_year, end_year + 1):
        is_current = (yr == current_yr)
        need_games = is_current or (yr in force_seasons) or (yr not in existing_seasons_games)
        need_standings = is_current or (yr in force_seasons) or (yr not in existing_seasons_standings)
        need_box = False
        if yr >= boxscore_start_year:
            max_possible_wk = 17 if yr >= 2021 else 16
            if is_current or (yr in force_seasons) or any((yr, w) not in existing_box_weeks for w in range(1, max_possible_wk + 1)):
                need_box = True

        if not need_games and not need_box and need_standings:
            standings_only_years.append(yr)
        elif need_games or need_box or need_standings:
            full_sync_years.append(yr)

    if standings_only_years:
        if not quiet:
            print(f"🏆 Backfilling divisions & standings (`team_seasons`) for {len(standings_only_years)} seasons ({standings_only_years[0]}–{standings_only_years[-1]})...", flush=True)
        with ThreadPoolExecutor(max_workers=6) as pool:
            futs = {
                pool.submit(_fetch_season_summary, league_id, cookies, yr, False): yr
                for yr in standings_only_years
            }
            for fut in as_completed(futs):
                yr = futs[fut]
                summary = fut.result()
                if summary:
                    ts_rows = _parse_team_seasons(summary, yr, current_yr)
                    if ts_rows:
                        cur.execute("DELETE FROM team_seasons WHERE season = ?", (yr,))
                        cur.executemany(
                            "INSERT OR REPLACE INTO team_seasons VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                            ts_rows,
                        )
                        conn.commit()
                        if not quiet:
                            print(f"  ✅ Season {yr}: {len(ts_rows)} teams synced to team_seasons.")

    for yr in full_sync_years:
        is_current = (yr == current_yr)
        need_games = is_current or (yr in force_seasons) or (yr not in existing_seasons_games)
        need_standings = is_current or (yr in force_seasons) or (yr not in existing_seasons_standings)

        # Check which weeks need boxscores (available from 2018 onward)
        target_weeks = []
        if yr >= boxscore_start_year:
            max_possible_wk = 17 if yr >= 2021 else 16
            for w in range(1, max_possible_wk + 1):
                if is_current or (yr in force_seasons) or ((yr, w) not in existing_box_weeks):
                    target_weeks.append(w)

        if not need_games and not target_weeks and not need_standings:
            continue

        if not quiet:
            print(f"📡 Syncing Season {yr}...", flush=True)

        summary = _fetch_season_summary(league_id, cookies, yr, is_current)
        if not summary:
            if not quiet:
                print(f"  ⚠️ No summary data returned for {yr}.")
            continue

        if need_standings:
            ts_rows = _parse_team_seasons(summary, yr, current_yr)
            if ts_rows:
                cur.execute("DELETE FROM team_seasons WHERE season = ?", (yr,))
                cur.executemany(
                    "INSERT OR REPLACE INTO team_seasons VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    ts_rows,
                )
                conn.commit()
                if not quiet:
                    print(f"  ✅ Standings & Divisions: {len(ts_rows)} teams saved.")

        reg_cnt = int(summary.get("settings", {}).get("scheduleSettings", {}).get("matchupPeriodCount", 14 if yr >= 2021 else 13))

        # Determine active/completed weeks in this season summary
        played_weeks = set()
        for g in summary.get("schedule", []):
            wk = g.get("matchupPeriodId", 0)
            a_pts = float((g.get("away") or {}).get("totalPoints", 0.0) or (g.get("away") or {}).get("totalPointsLive", 0.0) or 0.0)
            h_pts = float((g.get("home") or {}).get("totalPoints", 0.0) or (g.get("home") or {}).get("totalPointsLive", 0.0) or 0.0)
            if wk >= 1 and (a_pts > 0.0 or h_pts > 0.0):
                played_weeks.add(wk)

        # Fallback: also allow currentScoringPeriodId - 1 or current if active
        if is_current:
            cur_period = int(summary.get("scoringPeriodId", 0) or 0)
            # Only re-fetch weeks in played_weeks or up to cur_period if not already synced (or refresh latest 2 weeks)
            latest_wk = max(played_weeks) if played_weeks else cur_period
            refresh_floor = max(1, latest_wk - 1)
            target_weeks = [
                w for w in sorted(played_weeks)
                if (w >= refresh_floor) or (yr in force_seasons) or ((yr, w) not in existing_box_weeks)
            ]
        else:
            target_weeks = [w for w in target_weeks if (not played_weeks or w in played_weeks)]

        if need_games:
            g_rows = _parse_games_from_schedule(summary, yr, reg_cnt)
            if g_rows:
                cur.execute("DELETE FROM games WHERE season = ?", (yr,))
                cur.executemany(
                    "INSERT OR REPLACE INTO games VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    g_rows,
                )
                for wk in sorted({r[1] for r in g_rows}):
                    cur.execute(
                        "INSERT INTO sync_log (season, week, has_games, has_boxscores, synced_at) VALUES (?,?,1,0,?) "
                        "ON CONFLICT(season, week) DO UPDATE SET has_games=1, synced_at=excluded.synced_at",
                        (yr, wk, now_iso),
                    )
                conn.commit()
                if not quiet:
                    print(f"  ✅ Games: {len(g_rows) // 2} matchups saved.")

        if target_weeks:
            all_pb = []
            all_wk_games = []
            with ThreadPoolExecutor(max_workers=6) as pool:
                futs = [pool.submit(_fetch_week_boxscore, league_id, cookies, yr, w) for w in target_weeks]
                for fut in as_completed(futs):
                    wk, wk_data = fut.result()
                    if wk_data:
                        wk_g, wk_pb = _parse_week_boxscores(wk_data, yr, wk, reg_cnt)
                        all_wk_games.extend(wk_g)
                        all_pb.extend(wk_pb)

            if all_pb:
                touched_wks = sorted({r[1] for r in all_pb})
                for wk in touched_wks:
                    cur.execute("DELETE FROM player_boxscores WHERE season = ? AND week = ?", (yr, wk))
                cur.executemany(
                    "INSERT OR REPLACE INTO player_boxscores VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    all_pb,
                )
                if all_wk_games:
                    cur.executemany(
                        "INSERT OR REPLACE INTO games VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        all_wk_games,
                    )
                for wk in touched_wks:
                    cur.execute(
                        "INSERT INTO sync_log (season, week, has_games, has_boxscores, synced_at) VALUES (?,?,1,1,?) "
                        "ON CONFLICT(season, week) DO UPDATE SET has_games=1, has_boxscores=1, synced_at=excluded.synced_at",
                        (yr, wk, now_iso),
                    )
                conn.commit()
                if not quiet:
                    print(f"  ✅ Player Boxscores: {len(all_pb)} rows across weeks {touched_wks[0]}–{touched_wks[-1]}.")

    # Sync draft_picks + end-of-season scoring & positional rankings (2018–present, plus active season refresh)
    draft_target_years = [
        yr for yr in range(max(start_year, boxscore_start_year), end_year + 1)
        if (yr == current_yr) or (yr in force_seasons) or (yr not in existing_seasons_drafts)
    ]
    if draft_target_years:
        if not quiet:
            print(f"🎯 Syncing Draft Picks, Final Scoring & Positional Rankings ({draft_target_years[0]}–{draft_target_years[-1]})...", flush=True)
        with ThreadPoolExecutor(max_workers=6) as pool:
            futs = {
                pool.submit(_fetch_season_draft_and_stats, league_id, cookies, yr, yr == current_yr): yr
                for yr in draft_target_years
            }
            for fut in as_completed(futs):
                yr = futs[fut]
                d_data, p_info = fut.result()
                if d_data:
                    dp_rows = _parse_draft_picks(d_data, p_info, yr, conn)
                    if dp_rows:
                        cur.execute("DELETE FROM draft_picks WHERE season = ?", (yr,))
                        cur.executemany(
                            "INSERT OR REPLACE INTO draft_picks VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                            dp_rows,
                        )
                        conn.commit()
                        if not quiet:
                            print(f"  ✅ Season {yr} Draft: {len(dp_rows)} picks synced with final scoring & positional ranks.")

    conn.close()


def validate_readonly_sql(sql: str) -> str:
    """Strictly validates that an AI-generated or user SQL statement is a single read-only SELECT/WITH query."""
    cleaned = sql.strip().rstrip(";").strip()
    if not cleaned:
        raise ValueError("Empty SQL query.")
    if ";" in cleaned:
        raise ValueError("Only a single SQL statement is allowed.")
    low = cleaned.lower()
    if not re.match(r"^(select|with)\b", low):
        raise ValueError("Only SELECT / WITH read-only queries are permitted.")
    forbidden = [
        "insert ", "update ", "delete ", "drop ", "alter ", "create ",
        "attach ", "detach ", "pragma ", "replace ", "vacuum",
    ]
    for tok in forbidden:
        if re.search(rf"\b{tok.strip()}\b", low):
            raise ValueError(f"Forbidden SQL keyword detected: {tok.strip().upper()}")
    return cleaned


def run_sql_query(sql: str) -> tuple:
    cleaned = validate_readonly_sql(sql)
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    cur = conn.cursor()
    cur.execute(cleaned)
    cols = [d[0] for d in cur.description] if cur.description else []
    rows = cur.fetchall()
    conn.close()
    return cols, rows


def format_markdown_table(cols: list, rows: list, max_rows: int = 40) -> str:
    if not cols:
        return "(no columns)"
    if not rows:
        return "(0 rows returned)"
    shown = rows[:max_rows]
    lines = [
        "| " + " | ".join(str(c) for c in cols) + " |",
        "| " + " | ".join("---" for _ in cols) + " |",
    ]
    for r in shown:
        vals = [f"{v:.2f}" if isinstance(v, float) and not v.is_integer() else str(v) for v in r]
        lines.append("| " + " | ".join(vals) + " |")
    if len(rows) > max_rows:
        lines.append(f"\n*... ({len(rows) - max_rows} additional rows omitted)*")
    return "\n".join(lines)


def get_db_schema_context() -> str:
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    cur = conn.cursor()
    g_range = cur.execute("SELECT MIN(season), MAX(season), COUNT(*)/2 FROM games").fetchone()
    p_range = cur.execute("SELECT MIN(season), MAX(season), COUNT(*) FROM player_boxscores").fetchone()
    has_ts_table = cur.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='team_seasons'"
    ).fetchone()[0]
    ts_range = (None, None, 0)
    if has_ts_table:
        ts_range = cur.execute("SELECT MIN(season), MAX(season), COUNT(*) FROM team_seasons").fetchone()
    has_dp_table = cur.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='draft_picks'"
    ).fetchone()[0]
    dp_range = (None, None, 0)
    if has_dp_table:
        dp_range = cur.execute("SELECT MIN(season), MAX(season), COUNT(*) FROM draft_picks").fetchone()
    cur_yr = int(os.getenv("SEASON_YEAR") or CURRENT_YEAR)
    max_wk_cur = cur.execute("SELECT MAX(week) FROM player_boxscores WHERE season = ?", (cur_yr,)).fetchone()[0]
    owners_2yr = cur.execute(
        """
        SELECT season, team_id, owner, team_name, co_owners, COUNT(*) as gp
        FROM games
        WHERE season >= ?
        GROUP BY season, team_id
        ORDER BY season DESC, owner
        """,
        (cur_yr - 2,),
    ).fetchall()
    conn.close()

    roster_ctx = "\n".join(
        f"  - Season {r[0]} | team_id={r[1]} | owner='{r[2]}' | team_name='{r[3]}' | co_owners='{r[4]}' | games={r[5]}"
        for r in owners_2yr
    )

    # Nickname / shorthand lookup from canonical manifest
    nick_lines = [
        f"  - {code} -> '{info['owner']}' (2026 Division: {info['division']})"
        for code, info in get_team_details().items()
    ]
    nick_ctx = "\n".join(nick_lines)

    ts_doc = ""
    if ts_range and ts_range[2] and ts_range[2] > 0:
        ts_doc = f"""
3. Table `team_seasons` (Covers seasons {ts_range[0]}–{ts_range[1]}, {ts_range[2]} team-season rows; 1 row per team per season):
   - season (INTEGER), team_id (INTEGER), team_name (TEXT)
   - owner (TEXT), raw_owner (TEXT), co_owners (TEXT)
   - division_id (INTEGER), division_name (TEXT: e.g. 'North', 'South', 'East', 'West', etc.)
   - wins (INTEGER), losses (INTEGER), ties (INTEGER), win_pct (REAL)
   - points_for (REAL), points_against (REAL)
   - division_wins (INTEGER), division_losses (INTEGER), division_ties (INTEGER)
   - division_rank (INTEGER: 1 = 1st place in division, 2 = 2nd, 3 = 3rd, 4 = 4th — LOWER number is BETTER!)
   - playoff_seed (INTEGER: regular-season seed 1..16 — LOWER number is BETTER!)
   - final_rank (INTEGER: final post-playoff league finish 1..16 — 1 = Champion, LOWER is BETTER!)
   - made_playoffs (INTEGER 1/0), won_division (INTEGER 1/0), won_championship (INTEGER 1/0), runner_up (INTEGER 1/0)
   - is_season_complete (INTEGER 1/0 — 1 for completed past seasons, 0 for in-progress Season {cur_yr})
"""

    dp_doc = ""
    if dp_range and dp_range[2] and dp_range[2] > 0:
        dp_doc = f"""
4. Table `draft_picks` (Covers seasons {dp_range[0]}–{dp_range[1]}, {dp_range[2]} total draft picks; includes end-of-season BFL scoring, positional finish ranks & drafter starter production):
   - season (INTEGER), overall_pick (INTEGER: 1..240+), round_id (INTEGER: 1..15+), round_pick (INTEGER: pick within round)
   - team_id (INTEGER), team_name (TEXT), owner (TEXT), co_owners (TEXT)
   - player_id (INTEGER), player_name (TEXT), pro_team (TEXT), position (TEXT: 'QB','RB','WR','TE','K','D/ST')
   - pos_draft_rank (INTEGER: e.g. 1 = 1st WR drafted in BFL that year, 14 = 14th TE drafted)
   - bid_amount (INTEGER), keeper (INTEGER 1/0), auto_drafted (INTEGER 1/0)
   - season_points (REAL: player's full-season BFL fantasy points in that season; YTD for {cur_yr})
   - season_ppg (REAL: full-season fantasy points per game)
   - overall_finish_rank (INTEGER: player's end-of-season overall fantasy rank among drafted players — LOWER is BETTER!)
   - pos_finish_rank (INTEGER: player's end-of-season positional rank among drafted players, e.g. 1 = RB1 — LOWER is BETTER!)
   - overall_value_delta (INTEGER: `overall_pick - overall_finish_rank` — POSITIVE = beat draft slot / steal, NEGATIVE = bust!)
   - pos_value_delta (INTEGER: `pos_draft_rank - pos_finish_rank` — POSITIVE = beat positional draft rank / steal!)
   - round_avg_points (REAL: average `season_points` of all players drafted in that same `round_id` that season)
   - points_above_round_avg (REAL: `season_points - round_avg_points` — BEST metric for draft efficiency / value above round expectation!)
   - started_points_for_drafter (REAL: actual active-lineup starter points that the drafting owner received from this pick that season)
   - weeks_started_for_drafter (INTEGER: number of weeks the drafting owner started this player that season)
   - has_season_stats (INTEGER 1/0)
"""

    return f"""
SQLite Database Schema for BFL (Beasts Football League):

1. Table `games` (Covers seasons {g_range[0]}–{g_range[1]}, {g_range[2]} total matchups; 2 rows per matchup: 1 from each team's perspective):
   - season (INTEGER), week (INTEGER), matchup_id (INTEGER)
   - playoff_tier (TEXT: 'NONE', 'WINNERS_BRACKET', 'LOSERS_CONSOLATION_LADDER', etc.)
   - is_regular (INTEGER 1/0), is_playoffs (INTEGER 1/0 — Championship Bracket), is_consolation (INTEGER 1/0)
   - team_id (INTEGER), team_name (TEXT), owner (TEXT), raw_owner (TEXT), co_owners (TEXT)
   - score (REAL)
   - opp_team_id (INTEGER), opp_team_name (TEXT), opp_owner (TEXT), opp_raw_owner (TEXT), opp_score (REAL)
   - win (INTEGER 1/0), margin (REAL)

2. Table `player_boxscores` (Covers seasons {p_range[0]}–{p_range[1]}, Season {cur_yr} synced through Week {max_wk_cur}, {p_range[2]} player-game rows — FULL WEEKLY ROSTERS including both Starters and Bench/IR!):
   - season (INTEGER), week (INTEGER), matchup_id (INTEGER)
   - team_id (INTEGER), team_name (TEXT), owner (TEXT), co_owners (TEXT)
   - opp_team_id (INTEGER), opp_owner (TEXT)
   - playoff_tier (TEXT), is_regular (INTEGER 1/0), is_playoffs (INTEGER 1/0), is_consolation (INTEGER 1/0)
   - player_id (INTEGER), player_name (TEXT), pro_team (TEXT)
   - position (TEXT: 'QB', 'RB', 'WR', 'TE', 'K', 'D/ST')
   - slot_id (INTEGER), slot (TEXT: 'QB', 'RB', 'WR', 'TE', 'FLEX', 'K', 'D/ST', 'Bench', 'IR')
   - is_starter (INTEGER 1/0 — ALWAYS filter `is_starter = 1` when asking what a team/opponent scored in active lineups unless the question explicitly asks about full rosters or bench points!)
   - points (REAL), projected_points (REAL)
{ts_doc}{dp_doc}
IMPORTANT DOMAIN RULES WHEN WRITING SQL:
- Current active season is {cur_yr} (synced through Week {max_wk_cur}).
- Owner Nickname / Shorthand Mapping (use canonical `owner` names in WHERE clauses, and optionally check `co_owners LIKE '%Name%'` for co-owned historical seasons):
{nick_ctx}
  - Also: 'Dan'/'DTM' = 'Daniel Kruszewski', 'Tommy'/'Thomas' = 'Tommy Ehrlich', 'Rej' = 'rej hoxha', 'Thor' = 'Shawn Ullenbrauck', 'Lukose' = 'Shawn Lukose', 'AMO' = 'Adam Olen', 'Shooter' = 'Alex Kite'.
- Same-Division & Division-Finish Questions: Use `team_seasons` with a self-join on `a.season = b.season AND a.division_id = b.division_id AND a.team_id != b.team_id`. Lower `division_rank` (e.g. 1 < 2) means finishing HIGHER in the division! Include `season`, `division_name`, both owners' `division_rank`, `wins`, `losses`, `points_for`, and `is_season_complete` so you can cleanly report completed seasons vs. in-progress {cur_yr}.
- Draft Effectiveness, Best Picks / Steals & Busts Questions: Use `draft_picks` (filter `has_season_stats = 1`; for completed seasons use `season < {cur_yr}` or note {cur_yr} is YTD!).
  - "Most effective drafter": Group by `owner` and show `COUNT(DISTINCT season) as drafts`, `ROUND(AVG(points_above_round_avg), 2) as avg_pts_above_round`, `ROUND(SUM(points_above_round_avg), 1) as total_pts_above_round`, and `ROUND(SUM(started_points_for_drafter) / COUNT(DISTINCT season), 1) as avg_started_pts_per_draft` (filter `COUNT(DISTINCT season) >= 3` or show drafts count clearly so 1-year owners don't skew rankings!).
  - "Best draft picks / biggest steals of all time": Rank by `points_above_round_avg DESC` (and show `overall_pick`, `round_id`, `pos_draft_rank`, `pos_finish_rank`, `season_points`, `started_points_for_drafter`, `owner`, `season`) so late-round league-winners (e.g. Rd 10 pick finishing WR5 with +160 pts above round avg) rise to the top!
  - "Biggest draft busts": Filter early rounds (e.g. `round_id <= 2` or `overall_pick <= 32`) and rank by `points_above_round_avg ASC` or `season_points ASC`.
- "Points scored by their kicker" / "points from their K/TE/QB/D/ST" = `WHERE position = 'K' AND is_starter = 1` grouped by `owner` (or `team_id` if franchise continuity matters).
- "Points scored AGAINST them by opponents' kickers" = `WHERE position = 'K' AND is_starter = 1` grouped by `opp_owner` (because `opp_owner` is the team defending against that started player!).
- "Last two years" in Season {cur_yr} could mean active window `{cur_yr - 1}–{cur_yr}` (2025–2026 YTD) OR completed seasons `{cur_yr - 2}–{cur_yr - 1}` (2024–2025). When grouping across seasons, include `COUNT(*) as games` and `ROUND(AVG(points), 2) as ppg` and note any franchise owner who only played 1 of the 2 seasons!
- Recent Owner / Franchise Lineage (Seasons {cur_yr - 2}–{cur_yr}):
{roster_ctx}
"""


def _call_gemini(
    prompt: str,
    api_key: str,
    response_mime: str = "text/plain",
    thinking_budget: int = None,
    temperature: float = 0.1,
    timeout: int = 35,
    max_rounds: int = 2,
) -> str:
    import time as _time

    env_models = os.getenv("GEMINI_MODELS", "").strip()
    if env_models:
        models = [m.strip() for m in env_models.split(",") if m.strip()]
    else:
        # Verified live free-tier Flash-Lite + Flash fallback chain (replaces deprecated 2.5-flash)
        models = [
            "gemini-3.5-flash-lite",
            "gemini-flash-lite-latest",
            "gemini-3.1-flash-lite",
            "gemini-3.1-flash-lite-preview",
            "gemini-3.8-flash",
            "gemini-3.5-flash",
            "gemini-flash-latest",
        ]

    errs = []
    for round_idx in range(max(1, max_rounds)):
        for model in models:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
            headers = {"x-goog-api-key": api_key, "Content-Type": "application/json"}
            gen_cfg = {"temperature": float(temperature), "maxOutputTokens": 4096}
            if response_mime == "application/json":
                gen_cfg["responseMimeType"] = "application/json"
            # Only legacy 2.5-flash supports numeric thinkingBudget; 3.x models reject it with HTTP 400
            if thinking_budget is not None and "2.5-flash" in model:
                gen_cfg["thinkingConfig"] = {"thinkingBudget": int(thinking_budget)}

            payload = {
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": gen_cfg,
            }

            try:
                r = requests.post(url, headers=headers, json=payload, timeout=timeout)
                if r.status_code == 200:
                    data = r.json()
                    cands = data.get("candidates", [])
                    if cands:
                        return cands[0]["content"]["parts"][0]["text"]
                errs.append(f"{model} HTTP {r.status_code}: {r.text[:140]}")
            except Exception as e:
                errs.append(f"{model}: {e}")

        # Brief cooldown between full pool passes when Google is experiencing a 503 demand spike
        if round_idx + 1 < max_rounds:
            _time.sleep(3.5 * (round_idx + 1))

    raise RuntimeError("Gemini API call failed: " + " | ".join(errs[-6:]))


DRAFT_REPLACEMENT_RANKS = {
    "QB": 16,
    "RB": 36,
    "WR": 38,
    "TE": 16,
}


def compute_position_normalized_draft_value(
    db_path: Path = DB_PATH,
    *,
    season: int = None,
    owner: str = None,
    limit: int = 10,
) -> dict:
    """
    Computes Draft VORP Above Slot (`actual_vorp - expected_vorp[pos_draft_rank]`)
    using BFL's exact 16-team positional replacement baselines (QB16, RB36, WR38, TE16).

    Formula per `(season, position)`:
      - `repl_pts` = season points scored by the replacement-level starter (QB16, RB36, WR38, TE16)
      - `actual_vorp = max(0.0, actual_season_points - repl_pts)`
      - `expected_vorp = max(0.0, expected_pts[pos_draft_rank] - repl_pts)`
      - `delta (VORP vs Slot) = actual_vorp - expected_vorp`

    Why VORP beats raw positional points:
      - Late-round backup QBs who merely become average starters (e.g., QB26 -> QB12 or QB18)
        no longer get +200 pt cliff bonuses over waiver-level QBs.
      - True league-winners (Lamar QB1, Mahomes QB1, Kyren RB4) rise to the top.
      - Busts accurately reflect lost VORP (blowing #1 overall RB1 hurts far more than missing on a Rd 5 QB8).
    """
    if not db_path.exists():
        return {"steals": [], "busts": [], "seasons": []}

    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    cur = conn.cursor()
    avail_seasons = [
        int(r[0]) for r in cur.execute(
            "SELECT DISTINCT season FROM draft_picks WHERE has_season_stats = 1 ORDER BY season DESC"
        ).fetchall()
    ]

    where = ["has_season_stats = 1", "position IN ('QB', 'RB', 'WR', 'TE')", "pos_draft_rank IS NOT NULL"]
    params = []
    if season and int(season) > 0:
        where.append("season = ?")
        params.append(int(season))

    rows = cur.execute(
        f"""
        SELECT season, overall_pick, round_id, owner, player_name, position,
               pos_draft_rank, pos_finish_rank, season_points
        FROM draft_picks
        WHERE {' AND '.join(where)}
        """,
        params,
    ).fetchall()
    conn.close()

    # Group by (season, position) to build each season's exact positional scoring curve & replacement threshold
    curves = {}
    for r in rows:
        curves.setdefault((int(r[0]), r[5]), []).append(float(r[8] or 0.0))
    repl_pts_map = {}
    for k, vals in curves.items():
        vals.sort(reverse=True)
        pos = k[1]
        r_rank = DRAFT_REPLACEMENT_RANKS.get(pos, 16)
        r_idx = min(r_rank - 1, len(vals) - 1)
        repl_pts_map[k] = vals[r_idx] if vals else 0.0

    scored = []
    for r in rows:
        yr, pick, rnd, own, pname, pos, pdr, pfr, pts = (
            int(r[0]), int(r[1]), int(r[2]), r[3], r[4], r[5], int(r[6] or 0), int(r[7] or 0), float(r[8] or 0.0)
        )
        if owner and own.lower() != owner.lower():
            continue
        curve = curves.get((yr, pos), [])
        if not curve or pdr <= 0:
            continue
        repl_pts = repl_pts_map.get((yr, pos), 0.0)
        slot_idx = min(pdr - 1, len(curve) - 1)
        expected_pts = curve[slot_idx]
        actual_vorp = max(0.0, pts - repl_pts)
        expected_vorp = max(0.0, expected_pts - repl_pts)
        vorp_over_slot = round(actual_vorp - expected_vorp, 1)
        pos_leap = (pdr - pfr) if pfr > 0 else 0
        scored.append({
            "season": yr,
            "pick": pick,
            "round": rnd,
            "owner": own,
            "player": pname,
            "pos": pos,
            "pos_draft_rank": pdr,
            "pos_finish_rank": pfr,
            "pos_leap": pos_leap,
            "pts": round(pts, 1),
            "expected_pts": round(expected_pts, 1),
            "actual_vorp": round(actual_vorp, 1),
            "expected_vorp": round(expected_vorp, 1),
            "delta": vorp_over_slot,
        })

    # Steals: Round 4+ picks who genuinely beat replacement & outperformed their draft slot's expected VORP
    steals = [x for x in scored if x["round"] >= 4 and x["delta"] > 0 and x["pos_leap"] >= 3]
    steals.sort(key=lambda x: (x["delta"], x["pos_leap"]), reverse=True)

    # Busts: Early-round picks (Rounds 1-5) who severely underperformed their draft slot's expected VORP
    busts = [x for x in scored if x["round"] <= 5 and x["delta"] < 0]
    busts.sort(key=lambda x: (x["delta"], x["pos_leap"]))

    return {
        "steals": steals[:limit],
        "busts": busts[:limit],
        "seasons": avail_seasons,
    }


def answer_natural_language_question(question: str, auto_sync: bool = False) -> dict:
    """
    Library-friendly Gemini Text-to-SQL engine over `bfl_history.db`.
    Returns:
        {
            "answer": str,
            "queries": [{"label": str, "sql": str, "table": str, "row_count": int}],
        }
    Raises RuntimeError / ValueError instead of calling sys.exit(), making it safe
    for Discord bots, web APIs, and CLI callers alike.
    """
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY not found in .env.")

    if not DB_PATH.exists():
        raise RuntimeError(f"Database not initialized at {DB_PATH}. Run `--sync` first.")

    if auto_sync:
        sync_history_db(start_year=int(os.getenv("SEASON_YEAR") or CURRENT_YEAR), quiet=True)

    schema_ctx = get_db_schema_context()
    sql_prompt = f"""You are a precise SQLite data analyst for a fantasy football league database.
Translate the user's question into a single read-only SQLite SELECT/WITH query.
If the user says "last two years" and could mean either the active 2-season span (2025-2026) or the last 2 full seasons (2024-2025), prefer the active 2-year span (`season >= CURRENT_YEAR - 1`, i.e., `2025 AND 2026` unless they say "completed seasons"), and include games played + PPG + seasons covered so partial/1-season owners are obvious. If two separate metrics are asked (e.g., points FOR and points AGAINST), you can either write a combined query with JOIN/CTEs OR output a JSON array `"queries": [{{"label": "...", "sql": "..."}}, ...]`.

Return valid JSON:
{{
  "queries": [
    {{"label": "Short title", "sql": "SELECT ..."}}
  ]
}}

{schema_ctx}

User Question: {question}
"""
    raw_resp = _call_gemini(sql_prompt, api_key, response_mime="application/json", thinking_budget=256)
    parsed = json.loads(raw_resp)
    queries = parsed.get("queries") or [{"label": "Result", "sql": parsed["sql"]}]

    results_blocks = []
    for q in queries:
        lbl = q.get("label", "Query Result")
        sql = q.get("sql", "")
        try:
            cols, rows = run_sql_query(sql)
        except Exception as err:
            # One retry with SQLite error feedback
            fix_prompt = (
                f"Fix this SQLite query which failed with error: {err}\n"
                f"Original SQL:\n{sql}\n\nSchema:\n{schema_ctx}\n"
                f"Return JSON {{\"sql\": \"SELECT ...\"}}"
            )
            fixed = json.loads(_call_gemini(fix_prompt, api_key, response_mime="application/json"))
            sql = fixed["sql"]
            cols, rows = run_sql_query(sql)

        table_md = format_markdown_table(cols, rows)
        results_blocks.append({
            "label": lbl,
            "sql": sql,
            "table": table_md,
            "row_count": len(rows),
        })

    # Synthesize grounded final answer strictly from returned rows
    facts_text = "\n\n".join(
        f"### {b['label']}\nSQL:\n```sql\n{b['sql']}\n```\nResults:\n{b['table']}"
        for b in results_blocks
    )
    conn_meta = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    g_min, g_max = conn_meta.execute("SELECT MIN(season), MAX(season) FROM games").fetchone()
    cur_yr = int(os.getenv("SEASON_YEAR") or CURRENT_YEAR)
    max_wk_cur = conn_meta.execute("SELECT MAX(week) FROM games WHERE season = ?", (cur_yr,)).fetchone()[0]
    conn_meta.close()

    answer_prompt = f"""You are a sharp fantasy football analyst answering a league member's historical stats question.
Use ONLY the exact numbers, owner names, and rows returned by the SQL queries below. Do NOT invent or extrapolate numbers not in the table.
Database Context: The current active season is {cur_yr} (synced through Week {max_wk_cur}); the `games` table covers {g_min}–{g_max} and `player_boxscores` covers 2018–{g_max}. Never refer to an older year like 2024 as "the current season".
Be clear about the exact window covered (seasons/weeks, regular season vs all weeks), call out games played if someone only played 1 of the seasons, and format rankings clearly.

User Question: {question}

Verified Database Output:
{facts_text}
"""
    answer = _call_gemini(answer_prompt, api_key, response_mime="text/plain", thinking_budget=0).strip()
    return {"answer": answer, "queries": results_blocks}


def ask_natural_language(question: str, show_sql: bool = True):
    try:
        res = answer_natural_language_question(question, auto_sync=False)
    except RuntimeError as exc:
        print(f"❌ {exc}")
        sys.exit(1)

    print("\n" + res["answer"] + "\n")
    if show_sql:
        print("=" * 72)
        print("📊 VERIFIED SQL & DATA TABLES:")
        for b in res["queries"]:
            print(f"\n[{b['label']}]\n{b['sql']}\n\n{b['table']}")


def print_stats():
    if not DB_PATH.exists():
        print("❌ Database not initialized yet. Run `python3 bfl_history_db.py --sync` first.")
        return
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    cur = conn.cursor()
    g_cnt = cur.execute("SELECT COUNT(*)/2, MIN(season), MAX(season) FROM games").fetchone()
    pb_cnt = cur.execute("SELECT COUNT(*), MIN(season), MAX(season) FROM player_boxscores").fetchone()
    has_ts = cur.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='team_seasons'").fetchone()[0]
    ts_cnt = cur.execute("SELECT COUNT(*), MIN(season), MAX(season) FROM team_seasons").fetchone() if has_ts else (0, None, None)
    has_dp = cur.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='draft_picks'").fetchone()[0]
    dp_cnt = cur.execute("SELECT COUNT(*), MIN(season), MAX(season) FROM draft_picks").fetchone() if has_dp else (0, None, None)
    last_sync = cur.execute("SELECT MAX(synced_at) FROM sync_log").fetchone()[0]
    by_yr = cur.execute(
        """
        SELECT season, COUNT(DISTINCT week) as wks,
               SUM(CASE WHEN has_games=1 THEN 1 ELSE 0 END) as g_wks,
               SUM(CASE WHEN has_boxscores=1 THEN 1 ELSE 0 END) as pb_wks
        FROM sync_log GROUP BY season ORDER BY season DESC
        """
    ).fetchall()
    conn.close()
    print("=" * 70)
    print(f"🗄️  BFL HISTORICAL ANALYTICS DATABASE ({DB_PATH})")
    print(f"   Games Table           : {g_cnt[0]} matchups ({g_cnt[1]}–{g_cnt[2]})")
    print(f"   Team Seasons Table    : {ts_cnt[0]} team-seasons ({ts_cnt[1]}–{ts_cnt[2]})" if ts_cnt[0] else "   Team Seasons Table    : 0 rows (run `--sync` to backfill divisions & standings)")
    print(f"   Draft Picks Table     : {dp_cnt[0]} draft picks ({dp_cnt[1]}–{dp_cnt[2]})" if dp_cnt[0] else "   Draft Picks Table     : 0 rows (run `--sync` to backfill drafts & rankings)")
    print(f"   Player Boxscores Table: {pb_cnt[0]} player-weeks ({pb_cnt[1]}–{pb_cnt[2]})")
    print(f"   Last Synced           : {last_sync}")
    print("-" * 70)
    print("Season | Game Weeks | Boxscore Weeks")
    for r in by_yr:
        print(f"  {r[0]} |     {r[2]:2d}     |       {r[3]:2d}")
    print("=" * 70)


def main():
    parser = argparse.ArgumentParser(description="BFL Historical Analytics Warehouse & AI Query Engine")
    parser.add_argument("--sync", action="store_true", help="Sync missing/active seasons & weeks from ESPN into SQLite")
    parser.add_argument("--force-season", type=int, action="append", help="Force resync of a specific season year")
    parser.add_argument("--start-year", type=int, default=2008, help="First season to sync (default: 2008)")
    parser.add_argument("--ask", type=str, default="", help="Ask a natural-language question about BFL history")
    parser.add_argument("--sql", type=str, default="", help="Run a direct read-only SELECT/WITH SQL query")
    parser.add_argument("--json", action="store_true", help="Output --sql results as JSON")
    parser.add_argument("--no-sql", action="store_true", help="Hide SQL tables when using --ask")
    parser.add_argument("--no-auto-sync", action="store_true", help="Skip automatic current-season sync check before querying")
    parser.add_argument("--stats", action="store_true", help="Show database coverage & sync status")
    args = parser.parse_args()

    if args.sync or args.force_season or not DB_PATH.exists():
        sync_history_db(
            start_year=args.start_year,
            force_seasons=set(args.force_season or []),
        )
        if not args.ask and not args.sql and not args.stats:
            print_stats()
            return
    elif not args.no_auto_sync and (args.ask or args.sql):
        # Fast incremental sync for active season only
        sync_history_db(start_year=int(os.getenv("SEASON_YEAR") or CURRENT_YEAR), quiet=True)

    if args.stats:
        print_stats()
    if args.sql:
        cols, rows = run_sql_query(args.sql)
        if args.json:
            print(json.dumps([dict(zip(cols, r)) for r in rows], indent=2))
        else:
            print(format_markdown_table(cols, rows, max_rows=200))
    if args.ask:
        ask_natural_language(args.ask, show_sql=(not args.no_sql))


if __name__ == "__main__":
    main()
