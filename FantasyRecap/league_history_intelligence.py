#!/usr/bin/env python3
"""
BFL League History & Drama Intelligence Engine
=============================================
Stores, calculates, and exposes deep historical storylines for every owner:
- Ring Count & Championship Years (2008-2025)
- Title Droughts (Years since last Super Bowl)
- Playoff Heartbreaks (Super Bowl Runner-Up Finishes)
- All-Time Playoff Records (Winners Bracket Only)
- In-Season Rematches & Head-to-Head Win Streaks
"""

import csv
import json
import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
import requests

_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from FantasyRecap.config import CURRENT_YEAR
from FantasyRecap.core.manifest import (
    get_championships,
    get_espn_credentials,
    get_manager_synonyms,
    get_owner_profiles,
    get_owner_to_code,
    get_team_details,
    standardize_name,
)

# Canonical data loaded from FantasyRecap/league_manifest.json
BFL_CHAMPIONSHIPS = get_championships()
OWNER_PROFILES = get_owner_profiles()
MANAGER_SYNONYMS = get_manager_synonyms()
TEAM_DETAILS_2026 = get_team_details()
OWNER_TO_CODE_2026 = get_owner_to_code()


def get_owner_storyline_context(owner: str) -> dict:
    """Returns narrative context, title drought, and historical bio for an owner."""
    prof = OWNER_PROFILES.get(owner, {
        'rings': 0, 'champ_years': [], 'runner_up_years': [],
        'tagline': "Franchise Owner", 'summary': "BFL Franchise Manager."
    })

    rings = prof['rings']
    if rings > 0:
        last_champ = prof['champ_years'][-1]
        drought = CURRENT_YEAR - last_champ
        drought_str = "Defending Champion (2025)" if last_champ == 2025 else f"{drought}-year title drought (last won in {last_champ})"
    else:
        last_champ = None
        drought_str = "Chasing first career championship"

    return {
        'owner': owner,
        'rings': rings,
        'champ_years': prof['champ_years'],
        'runner_up_years': prof['runner_up_years'],
        'drought_str': drought_str,
        'tagline': prof['tagline'],
        'summary': prof['summary']
    }

def _process_season_games(data, year: int, h2h: dict, career_stats: dict, max_week: int = 99):
    """Parses season data and attributes games across co-owners with playoff tier classification."""
    members = {}
    for m in data.get('members', []):
        f = m.get('firstName', '').strip()
        l = m.get('lastName', '').strip()
        full = f"{f} {l}".strip()
        disp = m.get('displayName', '').strip()
        members[m['id']] = standardize_name(full if full else disp)
        
    teams = {}
    for t in data.get('teams', []):
        tid = t['id']
        owner_ids = t.get('owners', [])
        if not owner_ids and t.get('primaryOwner'):
            owner_ids = [t['primaryOwner']]
            
        owners = set()
        for oid in owner_ids:
            if oid in members:
                owners.add(members[oid])
                
        if not owners:
            tname = f"{t.get('location', '')} {t.get('nickname', '')}".strip()
            if tname:
                owners.add(standardize_name(tname))
            else:
                owners.add(f"Team {tid}")
                
        teams[tid] = list(owners)
        
    for g in data.get('schedule', []):
        w = g.get('matchupPeriodId', 0)
        if w > max_week:
            continue
            
        away = g.get('away', {})
        home = g.get('home', {})
        if not away or not home:
            continue
            
        a_id = away.get('teamId')
        h_id = home.get('teamId')
        a_owners = teams.get(a_id, [])
        h_owners = teams.get(h_id, [])
        a_score = float(away.get('totalPoints', 0.0) or away.get('totalPointsLive', 0.0) or 0.0)
        h_score = float(home.get('totalPoints', 0.0) or home.get('totalPointsLive', 0.0) or 0.0)
        tier = g.get('playoffTierType', 'NONE')
        
        is_champ_playoff = (tier == 'WINNERS_BRACKET')
        is_consolation = ('CONSOLATION' in str(tier) or 'LOSER' in str(tier))
        is_regular = (tier == 'NONE' and w <= 14)
        
        if a_score > 0 and h_score > 0 and a_owners and h_owners:
            margin = round(abs(a_score - h_score), 2)
            for own_a in a_owners:
                for own_b in h_owners:
                    if own_a == own_b:
                        continue
                    pair = tuple(sorted([own_a, own_b]))
                    if pair not in h2h:
                        h2h[pair] = []
                        
                    winner = own_a if a_score > h_score else own_b
                    loser = own_b if a_score > h_score else own_a
                    
                    h2h[pair].append({
                        'year': year,
                        'week': w,
                        'owner_a': own_a,
                        'owner_b': own_b,
                        'score_a': a_score,
                        'score_b': h_score,
                        'winner': winner,
                        'loser': loser,
                        'margin': margin,
                        'is_champ_playoff': is_champ_playoff,
                        'is_consolation': is_consolation,
                        'is_regular': is_regular
                    })
                    
                    for own, pts, is_win in [(own_a, a_score, a_score > h_score), (own_b, h_score, h_score > a_score)]:
                        if own not in career_stats:
                            career_stats[own] = {'total_pts': 0.0, 'games': 0, 'wins': 0}
                        career_stats[own]['total_pts'] += pts
                        career_stats[own]['games'] += 1
                        if is_win:
                            career_stats[own]['wins'] += 1

def _compile_h2h_from_sqlite(current_season: int, up_to_week: int, db_path: Path = None):
    """
    Fast-path H2H & career stats compiler backed by `live_state/bfl_history.db`.
    Avoids 18+ sequential ESPN HTTP calls when historical seasons are already synced locally.
    Returns `(h2h, career_stats, covered_years)` where `covered_years` is the set of past
    seasons (2008..current_season-1) present in SQLite.
    """
    target_db = db_path or (Path(__file__).resolve().parent / "live_state" / "bfl_history.db")
    if not target_db.exists():
        return {}, {}, set()

    h2h = {}
    career_stats = {}
    covered_years = set()
    try:
        conn = sqlite3.connect(f"file:{target_db}?mode=ro", uri=True)
        cur = conn.cursor()
        # Each matchup is stored as 2 mirrored rows (team_id < opp_team_id gives exactly 1 row per game)
        rows = cur.execute(
            """
            SELECT season, week, owner, co_owners, opp_owner, score, opp_score,
                   is_playoffs, is_consolation, is_regular, margin, team_id, opp_team_id
            FROM games
            WHERE (season < ?) OR (season = ? AND week < ?)
            ORDER BY season ASC, week ASC
            """,
            (current_season, current_season, up_to_week),
        ).fetchall()

        # Also map (season, week, team_id) -> co_owners list so both sides include co-owners
        co_map = {}
        for r in rows:
            season, wk, own, co_str = r[0], r[1], r[2], r[3]
            tid = r[11]
            owners_list = [standardize_name(x.strip()) for x in (co_str or "").split(",") if x.strip()]
            if not owners_list and own:
                owners_list = [standardize_name(own)]
            co_map[(season, wk, tid)] = list(dict.fromkeys(owners_list))

        seen_games = set()
        for r in rows:
            season, wk, own, _, opp_own, a_score, h_score, is_po, is_con, is_reg, margin, tid, opp_tid = r
            if season < current_season:
                covered_years.add(season)
            game_key = (season, wk, min(tid, opp_tid), max(tid, opp_tid))
            if game_key in seen_games:
                continue
            seen_games.add(game_key)

            a_owners = co_map.get((season, wk, tid), [standardize_name(own)])
            h_owners = co_map.get((season, wk, opp_tid), [standardize_name(opp_own)])

            for own_a in a_owners:
                for own_b in h_owners:
                    if own_a == own_b:
                        continue
                    pair = tuple(sorted([own_a, own_b]))
                    h2h.setdefault(pair, [])
                    winner = own_a if a_score > h_score else own_b
                    loser = own_b if a_score > h_score else own_a
                    h2h[pair].append({
                        "year": season,
                        "week": wk,
                        "owner_a": own_a,
                        "owner_b": own_b,
                        "score_a": float(a_score),
                        "score_b": float(h_score),
                        "winner": winner,
                        "loser": loser,
                        "margin": round(float(margin), 2),
                        "is_champ_playoff": bool(is_po),
                        "is_consolation": bool(is_con),
                        "is_regular": bool(is_reg),
                    })
                    for o, pts, is_w in [(own_a, float(a_score), a_score > h_score), (own_b, float(h_score), h_score > a_score)]:
                        cs = career_stats.setdefault(o, {"total_pts": 0.0, "games": 0, "wins": 0})
                        cs["total_pts"] += pts
                        cs["games"] += 1
                        if is_w:
                            cs["wins"] += 1
        conn.close()
    except Exception:
        return {}, {}, set()

    return h2h, career_stats, covered_years


def compile_all_time_h2h(league_id: str, s2: str, swid: str, current_season: int = 2026, up_to_week: int = 1):
    """
    Compiles all historical H2H matchups (2008 to current_season up_to_week-1).
    Uses local SQLite warehouse (`live_state/bfl_history.db`) for instant <10ms loads,
    and only queries ESPN API for any missing seasons or live current-season weeks.
    """
    cookies = {"espn_s2": s2, "SWID": swid} if (s2 and swid) else {}
    h2h, career_stats, covered_years = _compile_h2h_from_sqlite(current_season, up_to_week)

    # Historical seasons (2008..current_season-1) not yet in SQLite fall back to ESPN API
    creds = get_espn_credentials()
    hist_league_ids = list(dict.fromkeys([str(league_id)] + creds["all_league_ids"]))

    missing_past_years = [y for y in range(2008, current_season) if y not in covered_years]
    for y in missing_past_years:
        for lid in hist_league_ids:
            url = f"https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/leagueHistory/{lid}?seasonId={y}&view=mMatchupScore&view=mTeam&view=mSettings&view=mMembers"
            try:
                resp = requests.get(url, cookies=cookies, timeout=6)
                if resp.status_code == 200:
                    raw = resp.json()
                    data = raw[0] if isinstance(raw, list) and len(raw) > 0 else raw
                    _process_season_games(data, y, h2h, career_stats)
                    break
            except Exception:
                continue

    # Check if SQLite already has all current_season weeks up to up_to_week - 1; if not, fetch current season live
    if up_to_week > 1:
        max_cur_wk_in_h2h = 0
        for games_list in h2h.values():
            for g in games_list:
                if g["year"] == current_season and g["week"] > max_cur_wk_in_h2h:
                    max_cur_wk_in_h2h = g["week"]

        if max_cur_wk_in_h2h < (up_to_week - 1):
            # Remove partial current-season entries before refreshing from live ESPN to avoid duplicates
            if max_cur_wk_in_h2h > 0:
                h2h_clean = {}
                for pk, gl in h2h.items():
                    kept = [g for g in gl if g["year"] != current_season]
                    if kept:
                        h2h_clean[pk] = kept
                h2h = h2h_clean
                # Recompute career_stats from cleaned h2h
                career_stats = {}
                for gl in h2h.values():
                    for g in gl:
                        for o, pts, is_w in [
                            (g["owner_a"], g["score_a"], g["winner"] == g["owner_a"]),
                            (g["owner_b"], g["score_b"], g["winner"] == g["owner_b"]),
                        ]:
                            cs = career_stats.setdefault(o, {"total_pts": 0.0, "games": 0, "wins": 0})
                            cs["total_pts"] += pts
                            cs["games"] += 1
                            if is_w:
                                cs["wins"] += 1

            url_curr = f"https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/{current_season}/segments/0/leagues/{league_id}?view=mMatchupScore&view=mTeam&view=mSettings&view=mMembers"
            try:
                resp = requests.get(url_curr, cookies=cookies, timeout=8)
                if resp.status_code == 200:
                    _process_season_games(resp.json(), current_season, h2h, career_stats, max_week=up_to_week - 1)
            except Exception:
                pass

    return h2h, career_stats

def load_week_matchups(csv_path: str = None, target_week: int = 1, espn_data: dict = None):
    """Load the official schedule for a given week from CSV or directly from live ESPN schedule."""
    matchups = []
    if csv_path and os.path.exists(csv_path):
        with open(csv_path, 'r') as f:
            reader = csv.DictReader(f)
            for r in reader:
                if int(r['Week']) == target_week:
                    away_code = r['Away'].strip()
                    home_code = r['Home'].strip()
                    matchups.append({
                        'away_code': away_code,
                        'home_code': home_code,
                        'away_info': TEAM_DETAILS_2026.get(away_code, {'owner': away_code, 'team_name': away_code, 'division': 'N/A'}),
                        'home_info': TEAM_DETAILS_2026.get(home_code, {'owner': home_code, 'team_name': home_code, 'division': 'N/A'}),
                        'type': r.get('Type', '')
                    })
        if matchups:
            return matchups

    # Fallback: build directly from live ESPN schedule data
    if espn_data:
        from FantasyRecap.league_recap_generator import parse_league_members_and_teams
        teams = parse_league_members_and_teams(espn_data)
        for g in espn_data.get('schedule', []):
            if g.get('matchupPeriodId') == target_week:
                aid = g.get('away', {}).get('teamId')
                hid = g.get('home', {}).get('teamId')
                if aid in teams and hid in teams:
                    a_own = standardize_name(teams[aid]['owner'])
                    h_own = standardize_name(teams[hid]['owner'])
                    a_code = OWNER_TO_CODE_2026.get(a_own, a_own)
                    h_code = OWNER_TO_CODE_2026.get(h_own, h_own)
                    is_div = teams[aid].get('division') == teams[hid].get('division')
                    matchups.append({
                        'away_code': a_code,
                        'home_code': h_code,
                        'away_info': {'owner': a_own, 'team_name': teams[aid]['name'], 'division': teams[aid]['division']},
                        'home_info': {'owner': h_own, 'team_name': teams[hid]['name'], 'division': teams[hid]['division']},
                        'type': 'Division' if is_div else 'Cross-Division'
                    })
    return matchups

if __name__ == "__main__":
    print("="*75)
    print("🏆 BFL ALL-TIME CHAMPIONSHIP ROLL & OWNER CONTEXT ENGINE")
    print("="*75)
    for owner in OWNER_PROFILES:
        ctx = get_owner_storyline_context(owner)
        print(f"{owner:<20} | Rings: {ctx['rings']:<2} | {ctx['drought_str']:<35} | {ctx['tagline']}")
