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

env_path = PROJECT_ROOT / ".env"
if env_path.exists():
    load_dotenv(dotenv_path=env_path)
else:
    load_dotenv()

from FantasyRecap.config import CURRENT_YEAR, LEAGUE_ID
from FantasyRecap.league_history_intelligence import standardize_name

DB_PATH = SCRIPT_DIR / "live_state" / "bfl_history.db"
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

POSITION_MAP = {1: "QB", 2: "RB", 3: "WR", 4: "TE", 5: "K", 16: "D/ST"}
SLOT_MAP = {
    0: "QB",
    2: "RB",
    4: "WR",
    6: "TE",
    16: "D/ST",
    17: "K",
    20: "Bench",
    21: "IR",
    23: "FLEX",
}
PRO_TEAM_MAP = {
    0: "FA", 22: "ARI", 1: "ATL", 33: "BAL", 2: "BUF", 29: "CAR", 3: "CHI", 4: "CIN",
    5: "CLE", 6: "DAL", 7: "DEN", 8: "DET", 9: "GB", 34: "HOU", 11: "IND", 30: "JAX",
    12: "KC", 13: "LV", 24: "LAC", 14: "LAR", 15: "MIA", 16: "MIN", 17: "NE", 18: "NO",
    19: "NYG", 20: "NYJ", 21: "PHI", 23: "PIT", 25: "SF", 26: "SEA", 27: "TB", 10: "TEN",
    28: "WSH",
}


def get_espn_creds():
    lid = str(os.getenv("ESPN_LEAGUE_ID") or os.getenv("LEAGUE_ID") or LEAGUE_ID or "157057")
    s2 = os.getenv("ESPN_S2", "")
    swid = os.getenv("ESPN_SWID") or os.getenv("SWID", "")
    cookies = {"espn_s2": s2, "SWID": swid} if (s2 and swid) else {}
    return lid, cookies


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

        CREATE TABLE IF NOT EXISTS sync_log (
            season INTEGER NOT NULL,
            week INTEGER NOT NULL,
            has_games INTEGER NOT NULL DEFAULT 0,
            has_boxscores INTEGER NOT NULL DEFAULT 0,
            synced_at TEXT NOT NULL,
            PRIMARY KEY (season, week)
        );

        CREATE INDEX IF NOT EXISTS idx_games_season_owner ON games(season, owner);
        CREATE INDEX IF NOT EXISTS idx_games_opp_owner ON games(season, opp_owner);
        CREATE INDEX IF NOT EXISTS idx_pb_season_pos ON player_boxscores(season, position, is_starter);
        CREATE INDEX IF NOT EXISTS idx_pb_owner ON player_boxscores(owner, season, week);
        CREATE INDEX IF NOT EXISTS idx_pb_opp_owner ON player_boxscores(opp_owner, season, week);
        CREATE INDEX IF NOT EXISTS idx_pb_player ON player_boxscores(player_name, season);
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


def sync_history_db(
    start_year: int = 2008,
    end_year: int = None,
    force_seasons: set = None,
    boxscore_start_year: int = 2018,
    quiet: bool = False,
):
    """Incrementally syncs BFL games (2008+) and player boxscores (2018+) into SQLite."""
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

    now_iso = datetime.now().isoformat(timespec="seconds")

    for yr in range(start_year, end_year + 1):
        is_current = (yr == current_yr)
        need_games = is_current or (yr in force_seasons) or (yr not in existing_seasons_games)

        # Check which weeks need boxscores (available from 2018 onward)
        target_weeks = []
        if yr >= boxscore_start_year:
            max_possible_wk = 17 if yr >= 2021 else 16
            for w in range(1, max_possible_wk + 1):
                if is_current or (yr in force_seasons) or ((yr, w) not in existing_box_weeks):
                    target_weeks.append(w)

        if not need_games and not target_weeks:
            continue

        if not quiet:
            print(f"📡 Syncing Season {yr}...", flush=True)

        summary = _fetch_season_summary(league_id, cookies, yr, is_current)
        if not summary:
            if not quiet:
                print(f"  ⚠️ No summary data returned for {yr}.")
            continue

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

2. Table `player_boxscores` (Covers seasons {p_range[0]}–{p_range[1]}, Season {cur_yr} synced through Week {max_wk_cur}, {p_range[2]} player-game rows):
   - season (INTEGER), week (INTEGER), matchup_id (INTEGER)
   - team_id (INTEGER), team_name (TEXT), owner (TEXT), co_owners (TEXT)
   - opp_team_id (INTEGER), opp_owner (TEXT)
   - playoff_tier (TEXT), is_regular (INTEGER 1/0), is_playoffs (INTEGER 1/0), is_consolation (INTEGER 1/0)
   - player_id (INTEGER), player_name (TEXT), pro_team (TEXT)
   - position (TEXT: 'QB', 'RB', 'WR', 'TE', 'K', 'D/ST')
   - slot_id (INTEGER), slot (TEXT: 'QB', 'RB', 'WR', 'TE', 'FLEX', 'K', 'D/ST', 'Bench', 'IR')
   - is_starter (INTEGER 1/0 — ALWAYS filter `is_starter = 1` when asking what a team/opponent scored in active lineups unless the question explicitly asks about bench points!)
   - points (REAL), projected_points (REAL)

IMPORTANT DOMAIN RULES WHEN WRITING SQL:
- Current active season is {cur_yr} (synced through Week {max_wk_cur}).
- "Points scored by their kicker" / "points from their K/TE/QB/D/ST" = `WHERE position = 'K' AND is_starter = 1` grouped by `owner` (or `team_id` if franchise continuity matters).
- "Points scored AGAINST them by opponents' kickers" = `WHERE position = 'K' AND is_starter = 1` grouped by `opp_owner` (because `opp_owner` is the team defending against that started player!).
- "Last two years" in Season {cur_yr} could mean active window `{cur_yr - 1}–{cur_yr}` (2025–2026 YTD) OR completed seasons `{cur_yr - 2}–{cur_yr - 1}` (2024–2025). When grouping across seasons, include `COUNT(*) as games` and `ROUND(AVG(points), 2) as ppg` and note any franchise owner who only played 1 of the 2 seasons!
- Recent Owner / Franchise Lineage (Seasons {cur_yr - 2}–{cur_yr}):
{roster_ctx}
"""


def _call_gemini(prompt: str, api_key: str, response_mime: str = "text/plain") -> str:
    models = ["gemini-flash-latest", "gemini-3-flash-preview", "gemini-3.1-pro-preview", "gemini-pro-latest"]
    errs = []
    for model in models:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        headers = {"x-goog-api-key": api_key, "Content-Type": "application/json"}
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.1},
        }
        if response_mime == "application/json":
            payload["generationConfig"]["responseMimeType"] = "application/json"
        try:
            r = requests.post(url, headers=headers, json=payload, timeout=25)
            if r.status_code == 200:
                data = r.json()
                cands = data.get("candidates", [])
                if cands:
                    return cands[0]["content"]["parts"][0]["text"]
            errs.append(f"{model} HTTP {r.status_code}: {r.text[:200]}")
        except Exception as e:
            errs.append(f"{model}: {e}")
    raise RuntimeError("Gemini API call failed: " + " | ".join(errs))


def ask_natural_language(question: str, show_sql: bool = True):
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        print("❌ GEMINI_API_KEY not found in .env. Use --sql \"SELECT ...\" to query directly.")
        sys.exit(1)

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
    raw_resp = _call_gemini(sql_prompt, api_key, response_mime="application/json")
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
        results_blocks.append({"label": lbl, "sql": sql, "table": table_md})

    # Synthesize grounded final answer strictly from returned rows
    facts_text = "\n\n".join(
        f"### {b['label']}\nSQL:\n```sql\n{b['sql']}\n```\nResults:\n{b['table']}"
        for b in results_blocks
    )
    answer_prompt = f"""You are a sharp fantasy football analyst answering a league member's historical stats question.
Use ONLY the exact numbers, owner names, and rows returned by the SQL queries below. Do NOT invent or extrapolate numbers not in the table.
Be clear about the exact window covered (seasons/weeks, regular season vs all weeks), call out games played if someone only played 1 of the seasons, and format rankings clearly.

User Question: {question}

Verified Database Output:
{facts_text}
"""
    answer = _call_gemini(answer_prompt, api_key, response_mime="text/plain")
    print("\n" + answer.strip() + "\n")
    if show_sql:
        print("=" * 72)
        print("📊 VERIFIED SQL & DATA TABLES:")
        for b in results_blocks:
            print(f"\n[{b['label']}]\n{b['sql']}\n\n{b['table']}")


def print_stats():
    if not DB_PATH.exists():
        print("❌ Database not initialized yet. Run `python3 bfl_history_db.py --sync` first.")
        return
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    cur = conn.cursor()
    g_cnt = cur.execute("SELECT COUNT(*)/2, MIN(season), MAX(season) FROM games").fetchone()
    pb_cnt = cur.execute("SELECT COUNT(*), MIN(season), MAX(season) FROM player_boxscores").fetchone()
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
