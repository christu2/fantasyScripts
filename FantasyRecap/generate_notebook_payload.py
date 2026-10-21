#!/usr/bin/env python3
"""
BFL NotebookLM Payload Generator
================================
Fetches ESPN data, historical data, standings, transactions, and real-time player news
to generate a comprehensive, highly analytical Markdown file for Google NotebookLM.
"""

import os
import sys
import json
import argparse
import datetime
import requests
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from FantasyRecap.league_recap_generator import fetch_espn_week_data, parse_league_members_and_teams, analyze_week, ESPN_LEAGUE_ID, ESPN_S2, ESPN_SWID
from FantasyRecap.league_history_intelligence import get_owner_storyline_context, BFL_CHAMPIONSHIPS
from FantasyRecap.press_conference_manager import get_verified_manager_quotes
from FantasyRecap.config import CURRENT_YEAR

NFL_TEAMS = {
    22: 'ARI', 1: 'ATL', 33: 'BAL', 2: 'BUF', 29: 'CAR', 3: 'CHI', 4: 'CIN', 5: 'CLE',
    6: 'DAL', 7: 'DEN', 8: 'DET', 9: 'GB', 34: 'HOU', 11: 'IND', 30: 'JAX', 12: 'KC',
    13: 'LV', 24: 'LAC', 14: 'LAR', 15: 'MIA', 16: 'MIN', 17: 'NE', 18: 'NO', 19: 'NYG',
    20: 'NYJ', 21: 'PHI', 23: 'PIT', 25: 'SF', 26: 'SEA', 27: 'TB', 10: 'TEN', 28: 'WSH'
}

rotowire_cache = {}

def fetch_rotowire_details(pid: int) -> dict:
    """Fetch real-time Rotowire headline, story, and global fantasy ownership."""
    if pid in rotowire_cache:
        return rotowire_cache[pid]
    res = {'headline': '', 'story': '', 'date': '', 'sentiment': '', 'spin': ''}
    try:
        url = f"https://site.web.api.espn.com/apis/common/v3/sports/football/nfl/athletes/{pid}/overview"
        r = requests.get(url, timeout=3)
        if r.status_code == 200:
            data = r.json()
            roto = data.get('rotowire', {})
            fantasy_data = data.get('fantasy', {})
            
            sentiment = ""
            if fantasy_data.get('percentOwned'):
                trend = fantasy_data.get('last7Days', '0.0')
                trend_dir = "Up" if float(trend) > 0 else "Down"
                sentiment = f" | Global Sentiment: Rostered in {fantasy_data['percentOwned']}% of leagues ({trend_dir} {abs(float(trend))}% this week)."
            
            headline = roto.get('headline', '') if isinstance(roto, dict) else ''
            story = roto.get('story', '') if isinstance(roto, dict) else ''
            date_str = roto.get('date', '') if isinstance(roto, dict) else ''
            
            spin_str = ""
            if headline and story:
                spin_str = f"{headline} Spin: {story}{sentiment}"
            elif sentiment:
                spin_str = f"News: None.{sentiment}"
                
            res = {
                'headline': headline,
                'story': story,
                'date': date_str,
                'sentiment': sentiment,
                'spin': spin_str
            }
    except Exception:
        pass
    rotowire_cache[pid] = res
    return res

INJURY_KEYWORDS = [
    'injury', 'injured', 'concussion', 'hamstring', 'groin', 'ankle', 'knee',
    'exited', 'carted', 'locker room', 'ruled out', 'did not return',
    'downgraded to out', 'sprain', 'strain', 'dislocated', 'shoulder',
    'forced out', 'knocked out', 'broken', 'fracture', 'torn', 'tear'
]

IN_GAME_SIGNALS = [
    'early exit', 'exited', 'locker room', 'did not return', 'forced out',
    'knocked out', 'during sunday', 'during thursday', 'during monday',
    'downgraded to out', 'opening possession', 'first quarter', 'second quarter',
    'first half', 'sustained a', 'suffered a', "in sunday's", "in thursday's",
    "in monday's", 'left sunday', 'left thursday', 'left monday'
]

def detect_in_game_injury(player: dict) -> dict:
    """
    Evaluates whether a player suffered an in-game injury during the week.
    Returns injury context dict if an in-game injury occurred, else None.
    """
    pts = player.get('pts', 0.0)
    proj = player.get('proj', 0.0)
    
    # If they scored 10+ points, injury didn't kill their day
    if pts >= 8.0:
        return None
        
    details = fetch_rotowire_details(player.get('id', 0))
    text = (details['headline'] + ' ' + details['story']).lower()
    
    has_inj_kw = any(kw in text for kw in INJURY_KEYWORDS)
    has_in_game_sig = any(sig in text for sig in IN_GAME_SIGNALS)
    
    if has_inj_kw and has_in_game_sig:
        return {
            'player': player.get('name', 'Unknown'),
            'pts': pts,
            'proj': proj,
            'headline': details['headline'],
            'story': details['story']
        }
    elif pts <= 3.0 and proj >= 7.0 and has_inj_kw:
        return {
            'player': player.get('name', 'Unknown'),
            'pts': pts,
            'proj': proj,
            'headline': details['headline'],
            'story': details['story']
        }
    return None

def format_nfl_statline(stats):
    if not stats: return ""
    parts = []
    # Passing
    p_yd, p_td, ints = stats.get('3', 0.0), stats.get('4', 0.0), stats.get('20', 0.0)
    if p_yd > 0:
        parts.append(f"{int(p_yd)} Pass Yds, {int(p_td)} Pass TDs")
        if ints > 0: parts.append(f"{int(ints)} INTs")
    # Rushing
    r_yd, r_td = stats.get('24', 0.0), stats.get('25', 0.0)
    if r_yd > 0: parts.append(f"{int(r_yd)} Rush Yds")
    if r_td > 0: parts.append(f"{int(r_td)} Rush TDs")
    # Receiving
    rec, rec_yd, rec_td = stats.get('53', 0.0), stats.get('42', 0.0), stats.get('43', 0.0)
    if rec > 0 or rec_yd > 0: parts.append(f"{int(rec)} rec for {int(rec_yd)} Yds")
    if rec_td > 0: parts.append(f"{int(rec_td)} Rec TDs")
    
    if not parts: return ""
    return " | " + ", ".join(parts)

athlete_tenure_cache = {}
_tenure_cache_file = Path(__file__).resolve().parent / "athlete_tenure_cache.json"
if _tenure_cache_file.exists():
    try:
        with open(_tenure_cache_file, "r") as tf:
            athlete_tenure_cache = json.load(tf)
    except Exception:
        athlete_tenure_cache = {}

def save_tenure_cache():
    try:
        with open(_tenure_cache_file, "w") as tf:
            json.dump(athlete_tenure_cache, tf)
    except Exception:
        pass

def fetch_athlete_tenure(pid: int) -> str:
    """Fetches official NFL experience and draft year from ESPN Core API."""
    pid_str = str(pid)
    if pid_str in athlete_tenure_cache:
        return athlete_tenure_cache[pid_str]
    url = f"https://sports.core.api.espn.com/v2/sports/football/leagues/nfl/athletes/{pid}"
    try:
        r = requests.get(url, timeout=1.5)
        if r.status_code == 200:
            d = r.json()
            years = d.get('experience', {}).get('years')
            draft_yr = d.get('draft', {}).get('year')
            if years == 1 or draft_yr == 2026:
                s = "Rookie (1st Year, Class of 2026)"
            elif years == 2 or draft_yr == 2025:
                s = "2nd-Year Sophomore (Drafted 2025)"
            elif years == 3 or draft_yr == 2024:
                s = "3rd-Year Player (Drafted 2024)"
            elif years and years > 3:
                s = f"{years}th-Year Veteran" + (f" (Drafted {draft_yr})" if draft_yr else "")
            elif draft_yr:
                s = f"Drafted in {draft_yr}"
            else:
                s = ""
            athlete_tenure_cache[pid_str] = s
            return s
    except Exception:
        pass
    athlete_tenure_cache[pid_str] = ""
    return ""

def format_player_string(p, player_context):
    ctx = player_context.get(p['id'], {'injury': 'ACTIVE', 'outlook': '', 'adp': 200.0, 'season_avg': 0.0, 'tenure': ''})
    adp_str = f"ADP {ctx['adp']}" if ctx['adp'] < 180 else "Undrafted Flyer"
    avg_str = f", Avg {ctx['season_avg']} pts/gm" if ctx['season_avg'] > 0 else ""
    inj_str = f" [{ctx['injury']}]" if ctx['injury'] != 'ACTIVE' and ctx['injury'] != 'NORMAL' else ""
    tenure_str = f" [{ctx['tenure']}]" if ctx.get('tenure') else ""
    stats_str = format_nfl_statline(p.get('stats_dict', {}))
    
    team_abbr = NFL_TEAMS.get(p.get('pro_team_id', 0), 'FA')
    team_str = f" ({team_abbr})" if team_abbr != 'FA' else ""
    
    return f"{p['name']}{team_str}{tenure_str}{inj_str} (Scored: {p['pts']}, Proj: {p['proj']}, {adp_str}{avg_str}{stats_str})"

def calculate_season_trajectories(raw_data: dict, current_week: int, teams: dict):
    """Calculates division records, overall records, streaks, and trajectories for all 16 teams."""
    records = {tid: {
        'overall_w': 0, 'overall_l': 0,
        'div_w': 0, 'div_l': 0,
        'points_for': 0.0, 'points_against': 0.0,
        'results': [],
        'scores': []
    } for tid in teams}

    for g in sorted(raw_data.get('schedule', []), key=lambda x: x.get('matchupPeriodId', 0)):
        wk = g.get('matchupPeriodId')
        if wk <= current_week:
            a_id = g.get('away', {}).get('teamId')
            h_id = g.get('home', {}).get('teamId')
            a_pts = g.get('away', {}).get('totalPoints', 0.0)
            h_pts = g.get('home', {}).get('totalPoints', 0.0)
            if not a_id or not h_id: continue
            
            a_div = teams[a_id].get('division')
            h_div = teams[h_id].get('division')
            is_div = (a_div == h_div)
            
            records[a_id]['points_for'] += a_pts
            records[a_id]['points_against'] += h_pts
            records[h_id]['points_for'] += h_pts
            records[h_id]['points_against'] += a_pts
            
            records[a_id]['scores'].append(a_pts)
            records[h_id]['scores'].append(h_pts)
            
            if a_pts > h_pts:
                records[a_id]['overall_w'] += 1
                records[h_id]['overall_l'] += 1
                records[a_id]['results'].append(('W', wk, teams[h_id]['owner'], a_pts, h_pts, is_div))
                records[h_id]['results'].append(('L', wk, teams[a_id]['owner'], h_pts, a_pts, is_div))
                if is_div:
                    records[a_id]['div_w'] += 1
                    records[h_id]['div_l'] += 1
            else:
                records[h_id]['overall_w'] += 1
                records[a_id]['overall_l'] += 1
                records[h_id]['results'].append(('W', wk, teams[a_id]['owner'], h_pts, a_pts, is_div))
                records[a_id]['results'].append(('L', wk, teams[h_id]['owner'], a_pts, h_pts, is_div))
                if is_div:
                    records[h_id]['div_w'] += 1
                    records[a_id]['div_l'] += 1

    # Calculate league-wide ranks for Points For (PF) and Points Against (PA)
    by_pf = sorted(records.items(), key=lambda x: x[1]['points_for'], reverse=True)
    for rank, (tid, r) in enumerate(by_pf, 1):
        r['pf_rank'] = rank
        
    by_pa = sorted(records.items(), key=lambda x: x[1]['points_against'], reverse=True)
    for rank, (tid, r) in enumerate(by_pa, 1):
        r['pa_rank'] = rank

    # Streaks and narrative status
    for tid, r in records.items():
        res_letters = [x[0] for x in r['results']]
        if res_letters:
            streak_type = res_letters[-1]
            streak_cnt = 0
            for x in reversed(res_letters):
                if x == streak_type:
                    streak_cnt += 1
                else:
                    break
            r['streak_str'] = f"{streak_type}{streak_cnt}"
            r['streak_type'] = streak_type
            r['streak_cnt'] = streak_cnt
        else:
            r['streak_str'] = "N/A"
            r['streak_type'] = ""
            r['streak_cnt'] = 0

        # Trajectory & Luck classification
        if r['overall_l'] == 0:
            r['status_tag'] = f"🔥 UNDEFEATED JUGGERNAUT ({r['overall_w']}-0)"
        elif r['overall_w'] == 0:
            r['status_tag'] = f"💔 WINLESS & SNAKEBITTEN (0-{r['overall_l']}, Faced Rank #{r['pa_rank']} Gauntlet)"
        elif r['overall_w'] < r['overall_l'] and r['pf_rank'] <= 5:
            r['status_tag'] = f"⚡ UNLUCKY POWERHOUSE (Rank #{r['pf_rank']} Scoring, but {r['overall_w']}-{r['overall_l']})"
        elif r['overall_w'] > r['overall_l'] and r['pf_rank'] >= 12:
            r['status_tag'] = f"🍀 LUCKY CHARM / PAPER TIGER (Rank #{r['pf_rank']} Scoring, yet {r['overall_w']}-{r['overall_l']})"
        elif r['streak_type'] == 'W' and r['streak_cnt'] >= 3:
            r['status_tag'] = f"🚀 SURGING (Red-Hot {r['streak_cnt']}-Game Win Streak)"
        elif r['streak_type'] == 'L' and r['streak_cnt'] >= 3:
            r['status_tag'] = f"🧊 IN FREEFALL (Cold {r['streak_cnt']}-Game Losing Skid)"
        else:
            r['status_tag'] = "⚖️ CONTENDER IN THE PACK"

    return records

def fetch_league_trades(raw_data: dict, current_week: int, season: int, cookies: dict, teams: dict):
    """Pulls verified trades and chat drama from ESPN API."""
    comm_url = f'https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/{season}/segments/0/leagues/{ESPN_LEAGUE_ID}?view=kona_league_communication'
    try:
        r = requests.get(comm_url, cookies=cookies, timeout=5)
        if r.status_code != 200:
            return [], []
        comm = r.json()
    except Exception:
        return [], []

    pnames = {}
    for t in raw_data.get('teams', []):
        for entry in t.get('roster', {}).get('entries', []):
            pl = entry.get('playerPoolEntry', {}).get('player', {})
            if pl.get('id'):
                pnames[pl['id']] = pl.get('fullName', f"Player {pl['id']}")

    trade_events = {}
    topics = comm.get('communication', {}).get('topics', [])
    for top in topics:
        if top.get('type') == 'ACTIVITY_TRANSACTIONS':
            for m in top.get('messages', []):
                if m.get('messageTypeId') == 244: # Upheld trade
                    pid = int(m.get('targetId', 0))
                    pname = pnames.get(pid, f"Player {pid}")
                    from_t = m.get('from')
                    to_t = m.get('to')
                    from_owner = teams.get(from_t, {}).get('owner', f"Team {from_t}")
                    to_owner = teams.get(to_t, {}).get('owner', f"Team {to_t}")
                    date_ms = m.get('date', 0)
                    date_str = datetime.datetime.fromtimestamp(date_ms/1000).strftime('%Y-%m-%d')
                    
                    pair_key = tuple(sorted([from_owner, to_owner])) + (date_str,)
                    if pair_key not in trade_events:
                        trade_events[pair_key] = {
                            'date': date_str,
                            'owner_a': from_owner,
                            'owner_b': to_owner,
                            'a_sends': [],
                            'b_sends': []
                        }
                    if from_owner == trade_events[pair_key]['owner_a']:
                        if pname not in trade_events[pair_key]['a_sends']:
                            trade_events[pair_key]['a_sends'].append(pname)
                    else:
                        if pname not in trade_events[pair_key]['b_sends']:
                            trade_events[pair_key]['b_sends'].append(pname)
                            
    trades_list = list(trade_events.values())
    return trades_list

def fetch_recent_roster_moves(raw_data: dict, current_week: int, season: int, cookies: dict, teams: dict) -> list:
    """Fetches notable recent waiver, drop, and free agent moves from ESPN mTransactions2."""
    tx_url = f'https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/{season}/segments/0/leagues/{ESPN_LEAGUE_ID}?view=mTransactions2'
    moves = []
    try:
        r = requests.get(tx_url, cookies=cookies, timeout=5)
        if r.status_code != 200:
            return []
        data = r.json()
        
        pnames = {}
        for t in raw_data.get('teams', []):
            for entry in t.get('roster', {}).get('entries', []):
                pl = entry.get('playerPoolEntry', {}).get('player', {})
                if pl.get('id'):
                    pnames[pl['id']] = pl.get('fullName', f"Player {pl['id']}")

        for t in data.get('transactions', []):
            status = t.get('status')
            tx_type = t.get('type')
            for it in t.get('items', []):
                it_type = it.get('type')
                pid = it.get('playerId')
                pname = pnames.get(pid)
                if not pname:
                    try:
                        ar = requests.get(f"https://sports.core.api.espn.com/v2/sports/football/leagues/nfl/athletes/{pid}", timeout=1).json()
                        pname = ar.get('fullName', f"Player {pid}")
                        pnames[pid] = pname
                    except Exception:
                        pname = f"Player {pid}"
                from_id = it.get('fromTeamId')
                to_id = it.get('toTeamId')
                
                if it_type == 'DROP' and from_id in teams:
                    owner = teams[from_id]['owner']
                    moves.append(f"✂️ **{owner}** cut **{pname}**")
                elif it_type == 'ADD' and to_id in teams and status == 'EXECUTED':
                    owner = teams[to_id]['owner']
                    moves.append(f"⚡ **{owner}** signed **{pname}**")
                elif it_type == 'ADD' and to_id in teams and status == 'PENDING':
                    owner = teams[to_id]['owner']
                    moves.append(f"⏳ **{owner}** placed a waiver claim on **{pname}**")
    except Exception:
        pass
    return moves

def build_previous_weeks_chronicle(raw_data: dict, current_week: int, teams: dict) -> str:
    """Builds a rich, concise narrative digest of all completed previous weeks."""
    weeks_data = {}
    for g in sorted(raw_data.get('schedule', []), key=lambda x: x.get('matchupPeriodId', 0)):
        wk = g.get('matchupPeriodId')
        if wk < current_week:
            if wk not in weeks_data: weeks_data[wk] = []
            a_id = g.get('away', {}).get('teamId')
            h_id = g.get('home', {}).get('teamId')
            a_pts = g.get('away', {}).get('totalPoints', 0.0)
            h_pts = g.get('home', {}).get('totalPoints', 0.0)
            if a_id and h_id:
                a_owner = teams.get(a_id, {}).get('owner', 'Unknown')
                h_owner = teams.get(h_id, {}).get('owner', 'Unknown')
                w_owner = a_owner if a_pts > h_pts else h_owner
                l_owner = h_owner if a_pts > h_pts else a_owner
                w_pts = max(a_pts, h_pts)
                l_pts = min(a_pts, h_pts)
                weeks_data[wk].append({
                    'winner': w_owner, 'loser': l_owner,
                    'w_pts': w_pts, 'l_pts': l_pts,
                    'margin': round(w_pts - l_pts, 2)
                })

    week_themes = {
        1: "Week 1: Opening Blitz & Overreactions. Shawn Lukose dropped 153.8 to lead the league; Saagar survived Abe in a 126-120 barnburner.",
        2: "Week 2: Blowouts & Beatdowns. Tommy Ehrlich exploded for 143.8 to crush Nael; Dino Davros edged Daniel by 0.54 points (79.1 to 78.5) to send Daniel to 0-2.",
        3: "Week 3: Turning Tides. Sydney Miller stunned Shawn Lukose (129.8 to 113.5); Daniel got off the schneid with a 1.0-point escape over Abe (84.8 to 83.8).",
        4: "Week 4: The Separation & Injury Shocks. Saagar and Samran both improved to 4-0; Saquon Barkley (hamstring) and Rashee Rice (knee) suffered brutal in-game injuries; Nael and Daniel both won their second straight.",
        5: "Week 5: Mid-Quarter Reckoning & Blockbuster Trades. Samran remained the sole undefeated at 5-0; Daniel upset previously unbeaten Saagar (93.2 to 69.5); Shawn U blew out Blake (125.7 to 57.0); rej dropped 125.1 but lost an agonizing 3.9-pt duel to Samran; blockbuster trades reshaped rosters."
    }

    md = f"## 📜 SEASON CHRONICLE & PREVIOUS WEEKS DIGEST (WEEKS 1 TO {current_week - 1})\n"
    md += "*Use this background context to maintain ongoing storyline continuity. Reference past games naturally rather than reciting long history bios.*\n\n"
    
    for wk, games in weeks_data.items():
        theme = week_themes.get(wk, f"Week {wk} Action")
        md += f"### 🏈 WEEK {wk} RECAP ({theme})\n"
        for g in games:
            md += f"- **{g['winner']}** defeated **{g['loser']}** ({g['w_pts']:.1f} to {g['l_pts']:.1f}, Margin: {g['margin']:.2f})\n"
        md += "\n"
        
    return md

def generate_payload(season: int, week_num: int):
    print(f"📡 Fetching live ESPN data for Season {season}, Week {week_num}...")
    raw_data = fetch_espn_week_data(ESPN_LEAGUE_ID, str(season), week_num, ESPN_S2, ESPN_SWID)
    teams = parse_league_members_and_teams(raw_data)
    matchups, perfs, _ = analyze_week(raw_data, week_num)
    verified_quotes = get_verified_manager_quotes(None)
    cookies = {"espn_s2": ESPN_S2, "SWID": ESPN_SWID}

    trajectories = calculate_season_trajectories(raw_data, week_num, teams)
    trades = fetch_league_trades(raw_data, week_num, season, cookies, teams)

    payload = f"# BFL (Beasts Football League) - Season {season}, Week {week_num} Data Sheet\n\n"
    
    # --- DYNAMIC STAKES INTELLIGENCE ---
    is_championship = (week_num == 17)
    is_playoffs = (week_num >= 15)
    
    if is_championship:
        stakes_header = "🚨 CRITICAL CONTEXT: THIS IS THE WEEK 17 CHAMPIONSHIP FINALE! 🚨"
        stakes_desc = "DO NOT talk about regular season standings, wins, or losses. This is the Super Bowl! Your entire focus MUST be on the Championship Game between the final two owners. Hype up the history, the stakes, and the pressure of hoisting The Jabroni Trophy. The other matchups are just 3rd place or consolation Toilet Bowl games."
    elif is_playoffs:
        stakes_header = f"🚨 CRITICAL CONTEXT: WEEK {week_num} IS THE FANTASY PLAYOFFS! 🚨"
        stakes_desc = "The regular season is over. This is a win-or-go-home playoff week. Focus on playoff survival, heartbreak, and advancing to the Championship. Ignore regular season records now."
    elif week_num == 14:
        stakes_header = "🚨 CRITICAL CONTEXT: WEEK 14 IS THE REGULAR SEASON FINALE! 🚨"
        stakes_desc = "This is the final week before the playoffs. Teams are fighting desperately for the 4 Division Crowns and the 3 Wild Card spots. Focus on tiebreakers, division rivalries, and teams getting eliminated from contention."
    else:
        stakes_header = f"🚨 WEEK {week_num} MID-SEASON PIVOT POINT 🚨"
        stakes_desc = "We have officially crossed the first month of the season. Every win and loss shapes division races, winning streaks, and playoff momentum."

    payload += "## 🎙️ HOST INSTRUCTIONS & SHOW IDENTITY\n"
    payload += "You are hosting the 'Tuesday Morning Hangover', the premier fantasy football post-game show for the Beasts Football League (BFL).\n"
    payload += "Hosts: Chris & Dave. Style: High energy, sharp comedic banter, authentic sports radio debates, laughing with and roasting the managers.\n\n"
    
    themes = {
        1: "Overreactions and Panic. Everyone is freaking out over Week 1.",
        2: "Adjustments. Who was a one-week wonder and who is actually good?",
        3: "Early Season Identity. Teams are starting to show who they really are.",
        4: "The Quarter Mark. Time to start worrying if you have a losing record.",
        5: "The Mid-Quarter Reckoning: The Sole Undefeated, The 4-Way West Deadlock, and Blockbuster Trades!",
        6: "Separation. Contenders are pulling away from pretenders.",
        7: "Mid-Season Check-In. The playoff picture is taking shape.",
        8: "Trade Deadline Looming. Teams are getting desperate.",
        9: "The Second Half Push. Every win is critical now.",
        10: "Playoff Push. The margin for error is gone.",
        11: "Crunch Time. Heartbreaks and miracles.",
        12: "Thanksgiving Week. Feasting on opponents or starving for wins.",
        13: "The Penultimate Week. The playoff bubble is bursting.",
        14: "The Regular Season Finale. Win and in. Do or die.",
    }
    weekly_theme = themes.get(week_num, "Playoff Survival. Win or go home.")
    if is_championship: weekly_theme = "THE CHAMPIONSHIP. Legacy, pressure, and hoisting The Jabroni."
    
    payload += f"**THIS WEEK'S PODCAST THEME:** {weekly_theme}\n\n"

    # --- STRICT PACING & BROADCAST TIME ALLOCATION (15% / 70% / 15%) ---
    payload += "### ⏱️ STRICT BROADCAST STRUCTURE & TIME ALLOCATION (PACING GUIDE)\n"
    payload += "To deliver a balanced, entertaining, and comprehensive episode, you MUST adhere to this exact 3-Act structure:\n\n"
    payload += f"1. **ACT 1: THE OPENING DRIVE (FIRST ~15% OF THE EPISODE)**\n"
    payload += "   - High-energy show intro and setting the tone for the week's theme.\n"
    payload += "   - **League News, Trades & Significant Roster Moves:** Debate any completed trades, waiver wire coups, and major NFL injuries shaking up rosters (e.g. Terry McLaurin season-ending patellar tendon tear, Ladd McConkey concussion, Blake Grupe torn ACL, key waiver claims and cuts).\n"
    payload += "   - **Division Standings & Luck Index Snapshot:** Quick overview of division leaders, hot streaks, losing skids, and calling out the 'Lucky Paper Tigers' vs 'Unlucky Powerhouses' (Points Scored vs Win-Loss Records).\n"
    payload += "   - *Strict Pacing Rule:* Keep this high-level, fast-paced, and punchy! Do NOT spend more than ~15% of your total broadcast time here so you have ample time for the matchups.\n\n"
    payload += f"2. **ACT 2: GAME-BY-GAME BREAKDOWNS (THE CORE ~70% OF THE EPISODE)**\n"
    payload += "   - **MANDATORY EQUAL COVERAGE RULE:** You MUST cover **ALL 8 MATCHUPS and ALL 16 TEAMS**! Do NOT skip any matchup or relegate any team to an afterthought!\n"
    payload += "   - **NO SKEWING HEAVILY TO A HANDFUL OF TEAMS:** Balance your coverage! Give every single matchup roughly 1.5 to 2 minutes of focused, entertaining attention.\n"
    payload += "   - **WHAT TO COVER FOR EVERY GAME:**\n"
    payload += "     a) Final score, winner, and margin of victory.\n"
    payload += "     b) Key performers (studs and duds for BOTH teams).\n"
    payload += "     c) In-game injuries during live play (DO NOT blame the manager—sympathize with them!).\n"
    payload += "     d) Key managerial decisions, start/sit blunders, and bench points.\n"
    payload += "     e) What the win or loss means for each owner's division standing and season trajectory.\n\n"
    payload += f"3. **ACT 3: THE TWO-MINUTE DRILL & NEXT WEEK LOOKAHEAD (FINAL ~15% OF THE EPISODE)**\n"
    payload += f"   - Comprehensive preview of Week {week_num + 1} matchups.\n"
    payload += "   - Highlight the 'Game of the Week', critical division rivalry battles, and must-win bubble showdowns.\n"
    payload += "   - Closing thoughts, sign-off banter, and parting barbs.\n\n"

    # --- CRITICAL HOST DIRECTIVES ---
    payload += "### ⚡ CRITICAL SHOW DIRECTIVES (MUST FOLLOW):\n"
    payload += "1. **NO FANTASY EXPLANATIONS:** Never explain what fantasy football is or how head-to-head works. The listeners are the 16 managers. Talk like seasoned sports talk radio hosts.\n"
    payload += "2. **BALANCED COVERAGE (ALL 16 TEAMS):** Touch on every single team and break down all 8 matchups. Do NOT focus solely on the undefeated team or high-profile owners.\n"
    payload += "3. **IN-GAME INJURIES (ABSOLUTE DIRECTIVE):** NFL players get injured during live games! League managers DO NOT intentionally start inactive or injured players! If a starter scored 0 to 4 points and has an in-game injury noted below (e.g., Terry McLaurin patellar tendon tear, Ladd McConkey concussion, Blake Grupe ACL tear), NEVER accuse the manager of 'starting an injured player' or committing a 'blunder'! Explicitly recognize on-air that the player suffered an in-game injury during live play. Sympathize with the manager for brutal injury heartbreak and bad luck.\n"
    
    # Dynamic continuity highlights
    undefeated_teams = [t['owner'] for t in teams.values() if trajectories[t['id']]['overall_l'] == 0]
    winless_teams = [t['owner'] for t in teams.values() if trajectories[t['id']]['overall_w'] == 0]
    continuity_snippets = []
    if undefeated_teams:
        continuity_snippets.append(f"{', '.join(undefeated_teams)} remaining undefeated")
    if winless_teams:
        continuity_snippets.append(f"{', '.join(winless_teams)} still searching for win #1")
    continuity_str = f" This is Episode {week_num}! DO NOT repeat static multi-year owner biographies or ring counts from scratch. Instead, focus on the DYNAMIC {season} SEASON STORY: " + (", ".join(continuity_snippets) if continuity_snippets else "the tight playoff and division races taking shape") + ".\n"
    payload += f"4. **EPISODIC CONTINUITY & ANTI-REPETITION:**{continuity_str}"
    
    payload += "5. **DISCUSS DIVISION DYNAMICS & STREAKS:** Talk about division races! Who leads? Who is tied? Point out who is riding hot winning streaks versus who is in freefall.\n"
    payload += "6. **BLAME THE PLAYER, NOT THE MANAGER:** When a star player underperforms while healthy, roast the player for disappearing, not the manager for starting their studs.\n"
    payload += "7. **TIMELINE REALITY & PLAYER TENURE (DO NOT CALL VETERANS ROOKIES):** The current NFL season is 2026!\n"
    payload += "   - Every player in this document now has their official NFL tenure tag right next to their name (e.g. `[2nd-Year Sophomore (Drafted 2025)]` or `[3rd-Year Player (Drafted 2024)]`).\n"
    payload += "   - **Colston Loveland, Luther Burden III, Tyler Warren, Jeremiyah Love, and Quinshon Judkins** were drafted in 2025 and ARE IN THEIR 2ND SEASON (SOPHOMORES). THEY ARE NOT ROOKIES! DO NOT CALL COLSTON LOVELAND A ROOKIE!\n"
    payload += "   - **Caleb Williams, Jayden Daniels, Bo Nix, Marvin Harrison Jr, Malik Nabers, Rome Odunze, and Brock Bowers** were drafted in 2024 and ARE IN THEIR 3RD SEASON (ESTABLISHED VETERANS).\n"
    payload += "   - ONLY players explicitly labeled `[Rookie (1st Year, Class of 2026)]` are rookies. You are strictly forbidden from calling Colston Loveland or any 2024/2025 draft pick a rookie.\n"
    payload += "8. **CENTRAL TIME ZONE:** The BFL operates in Central Time (CT). Sunday games kick off at 12:00 PM CT (noon).\n"
    
    # Dynamic Luck Directive
    pt_callouts = [f"{teams[tid]['owner']} ({tr['overall_w']}-{tr['overall_l']} with {round(tr['points_for'], 1)} PF, #{tr['pf_rank']} scoring)" for tid, tr in trajectories.items() if tr['overall_w'] > tr['overall_l'] and tr['pf_rank'] >= 10]
    unlucky_callouts = [f"{teams[tid]['owner']} ({tr['overall_w']}-{tr['overall_l']} despite {round(tr['points_for'], 1)} PF, #{tr['pf_rank']} scoring)" for tid, tr in trajectories.items() if tr['overall_w'] <= tr['overall_l'] and tr['pf_rank'] <= 6]
    gauntlet_callouts = [f"{teams[tid]['owner']} ({round(tr['points_against'], 1)} PA, #{tr['pa_rank']} schedule)" for tid, tr in trajectories.items() if tr['pa_rank'] <= 2]
    
    luck_dir_str = " Records alone are deceiving! You MUST explicitly compare teams' win-loss records to their total Points Scored (Points For). "
    if pt_callouts:
        luck_dir_str += f"Roast the lucky paper tigers ({', '.join(pt_callouts)}). "
    if unlucky_callouts:
        luck_dir_str += f"Stand up for the unlucky powerhouses ({', '.join(unlucky_callouts)}). "
    if gauntlet_callouts:
        luck_dir_str += f"Mention the brutal schedule gauntlet faced by {', '.join(gauntlet_callouts)}!\n\n"
    else:
        luck_dir_str += "\n\n"
    payload += f"9. **POINTS SCORED VS RECORDS (LUCK & FRAUD FACTOR):**{luck_dir_str}"

    # --- AUTOMATIC NFL GAME RECAPS ---
    try:
        nfl_url = f"http://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard?seasontype=2&week={week_num}"
        r = requests.get(nfl_url, timeout=5)
        if r.status_code == 200:
            nfl_data = r.json()
            headlines = []
            for e in nfl_data.get('events', []):
                for comp in e.get('competitions', []):
                    if 'headlines' in comp and comp['headlines']:
                        headlines.append(comp['headlines'][0].get('description', ''))
            
            if headlines:
                payload += "## 🏈 OFFICIAL NFL WEEKLY HEADLINES & GAME RECAPS\n"
                payload += "*Use these real-life NFL game recaps to add flavor when talking about players who were in these games:*\n"
                for h in headlines[:8]:
                    if h: payload += f"- {h}\n"
                payload += "\n"
    except Exception as e:
        print(f"⚠️ Could not fetch NFL Game Recaps: {e}")

    # --- LEAGUE TRANSACTIONS & ROSTER MOVES ---
    roster_moves = fetch_recent_roster_moves(raw_data, week_num, season, cookies, teams)
    if trades or roster_moves:
        payload += "## 🔥 LEAGUE TRANSACTIONS: TRADES, WAIVERS & ROSTER SHIFTS\n"
        payload += "*Discuss and debate these recent transactions and roster shakeups during Act 1!*\n\n"
        if trades:
            for tr in trades:
                payload += f"### 🔄 Trade on {tr['date']}: {tr['owner_a']} <---> {tr['owner_b']}\n"
                payload += f"- **{tr['owner_a']} receives:** {', '.join(tr['b_sends']) if tr['b_sends'] else 'Assets'}\n"
                payload += f"- **{tr['owner_b']} receives:** {', '.join(tr['a_sends']) if tr['a_sends'] else 'Assets'}\n"
                if 'Abe Thomas' in [tr['owner_a'], tr['owner_b']] and 'Nitesh Patel' in [tr['owner_a'], tr['owner_b']]:
                    payload += "  *Context & Banter:* Defending champ Abe Thomas shipped out Aaron Rodgers, Justice Hill, and Keon Coleman to Nitesh Patel for Caleb Williams and 2nd-year TE Colston Loveland! Debate if this move has paid off.\n"
                elif 'Shawn Ullenbrauck' in [tr['owner_a'], tr['owner_b']] and 'Daniel Kruszewski' in [tr['owner_a'], tr['owner_b']]:
                    payload += "  *Context & Banter:* Daniel Kruszewski flipped injured T.J. Hockenson to Shawn Ullenbrauck for Brenton Strange!\n"
                payload += "\n"
        if roster_moves:
            payload += "### 📋 Notable Recent Waiver & Roster Moves:\n"
            for rm in roster_moves[:12]:
                payload += f"- {rm}\n"
            payload += "\n"

    # --- LEAGUE-WIDE POINTS RANKINGS & LUCK INDEX ---
    payload += "## 🎯 LEAGUE-WIDE POINTS RANKINGS & THE LUCK INDEX\n"
    payload += "*CRITICAL TOPIC FOR THE HOSTS: Debate how misleading win-loss records are by looking at Points For vs Points Against!*\n\n"
    payload += "| Rank | Owner | Team | Record | Points For (Avg PPG) | Points Against (Opp PPG) | Luck Diagnosis |\n"
    payload += "| :--- | :--- | :--- | :--- | :--- | :--- | :--- |\n"
    
    all_teams_by_pf = sorted(teams.values(), key=lambda t: trajectories[t['id']]['points_for'], reverse=True)
    for rank, t in enumerate(all_teams_by_pf, 1):
        tr = trajectories[t['id']]
        avg_ppg = tr['points_for'] / week_num
        opp_ppg = tr['points_against'] / week_num
        
        if tr['overall_w'] < tr['overall_l'] and rank <= 5:
            diagnosis = "⚡ **UNLUCKY POWERHOUSE** (Top 5 Scoring, Losing Record)"
        elif tr['overall_w'] > tr['overall_l'] and rank >= 12:
            diagnosis = "🍀 **PAPER TIGER / LUCKY CHARM** (Bottom 5 Scoring, Winning Record)"
        elif tr['pa_rank'] <= 2 and tr['overall_w'] <= 2:
            diagnosis = "💀 **SCHEDULE VICTIM** (Faced Top 2 Most Points in BFL)"
        elif tr['overall_l'] == 0:
            diagnosis = f"👑 **THE UNDEFEATED KING** (Rank #{rank} Scoring, {tr['overall_w']}-{tr['overall_l']})"
        else:
            diagnosis = "⚖️ Fair Matchup Balance"
            
        payload += f"| #{rank} | **{t['owner']}** | {t['name']} | **{tr['overall_w']}-{tr['overall_l']}** | {round(tr['points_for'], 1)} ({round(avg_ppg, 1)} PPG) | {round(tr['points_against'], 1)} ({round(opp_ppg, 1)} PPG) | {diagnosis} |\n"
    payload += "\n"
    
    payload += "### 🗣️ TALKING POINTS ON POINTS VS LUCK:\n"
    if pt_callouts:
        payload += f"- **The Lucky Paper Tigers:** {', '.join(pt_callouts)}. Living on borrowed time and soft opponent scoring!\n"
    if unlucky_callouts:
        payload += f"- **The Unlucky Powerhouses:** {', '.join(unlucky_callouts)}. Scoring tons of points, outscoring most of the league, but stuck with losing records due to opponent schedule luck. Call for justice!\n"
    if gauntlet_callouts:
        payload += f"- **The Schedule Gauntlet:** {', '.join(gauntlet_callouts)}. Opponents are putting up season-highs against them week after week!\n\n"

    # --- DIVISION STANDINGS & TITLE RACE DYNAMICS ---
    payload += f"## 📊 DIVISION STANDINGS & TITLE RACE DYNAMICS (THROUGH WEEK {week_num})\n"
    division_standings = {}
    for t in teams.values():
        div = t.get('division', 'Unknown')
        if div not in division_standings: division_standings[div] = []
        division_standings[div].append(t)

    div_narratives = {}
    for div in ['East', 'North', 'South', 'West']:
        div_teams = division_standings.get(div, [])
        div_teams.sort(key=lambda x: (
            trajectories[x['id']]['overall_w'],
            trajectories[x['id']]['div_w'],
            trajectories[x['id']]['points_for']
        ), reverse=True)
        if div_teams:
            leader_w = trajectories[div_teams[0]['id']]['overall_w']
            leaders = [t for t in div_teams if trajectories[t['id']]['overall_w'] == leader_w]
            if len(leaders) == 1:
                gap = leader_w - trajectories[div_teams[1]['id']]['overall_w']
                gap_str = f"holds a {gap}-game lead" if gap > 0 else "holds the tiebreaker"
                div_narratives[div] = f"{leaders[0]['owner']} sits alone in 1st place at {leader_w}-{trajectories[leaders[0]['id']]['overall_l']} ({gap_str})! "
            else:
                names = ', '.join([t['owner'] for t in leaders])
                div_narratives[div] = f"MASSIVE {len(leaders)}-WAY DEADLOCK AT THE TOP! {names} are all tied for 1st place at {leader_w}-{trajectories[leaders[0]['id']]['overall_l']}! "
            chasers = [f"{t['owner']} ({trajectories[t['id']]['overall_w']}-{trajectories[t['id']]['overall_l']})" for t in div_teams[len(leaders):]]
            if chasers:
                div_narratives[div] += f"Chasing: {', '.join(chasers)}."

    for div in ['East', 'North', 'South', 'West']:
        div_teams = division_standings.get(div, [])
        div_teams.sort(key=lambda x: (
            trajectories[x['id']]['overall_w'],
            trajectories[x['id']]['div_w'],
            trajectories[x['id']]['points_for']
        ), reverse=True)
        
        payload += f"### 🏈 {div.upper()} DIVISION\n"
        payload += f"*{div_narratives.get(div, '')}*\n"
        for rank, t in enumerate(div_teams, 1):
            tr = trajectories[t['id']]
            payload += f"{rank}. **{t['owner']}** ({t['name']}): **{tr['overall_w']}-{tr['overall_l']}** (Div: {tr['div_w']}-{tr['div_l']}) | Streak: `{tr['streak_str']}` | **{round(tr['points_for'], 1)} PF (Rank #{tr['pf_rank']})** | PA: {round(tr['points_against'], 1)} (Rank #{tr['pa_rank']} hardest) | {tr['status_tag']}\n"
        payload += "\n"

    # --- SEASON TRAJECTORIES & MOMENTUM ---
    payload += "## 📈 LEAGUE MOMENTUM & TRAJECTORIES\n"
    payload += "### 🔥 Surging Teams (On Fire):\n"
    surging_items = []
    for tid, tr in sorted(trajectories.items(), key=lambda x: (x[1]['streak_cnt'], x[1]['overall_w']), reverse=True):
        if tr['streak_type'] == 'W' and tr['streak_cnt'] >= 2:
            surging_items.append(f"- **{teams[tid]['owner']} ({tr['overall_w']}-{tr['overall_l']}, {tr['streak_str']}):** Won {tr['streak_cnt']} straight ({round(tr['points_for'], 1)} PF, Rank #{tr['pf_rank']}).")
    if surging_items:
        payload += "\n".join(surging_items) + "\n\n"
    else:
        payload += "- No teams currently on a 2+ game winning streak.\n\n"
    
    payload += "### 🧊 Teams in Freefall (Losing Skids):\n"
    freefall_items = []
    for tid, tr in sorted(trajectories.items(), key=lambda x: (x[1]['streak_cnt'], -x[1]['overall_w']), reverse=True):
        if tr['streak_type'] == 'L' and tr['streak_cnt'] >= 2:
            freefall_items.append(f"- **{teams[tid]['owner']} ({tr['overall_w']}-{tr['overall_l']}, {tr['streak_str']}):** Dropped {tr['streak_cnt']} straight ({round(tr['points_for'], 1)} PF, Rank #{tr['pf_rank']}; {round(tr['points_against'], 1)} PA, Rank #{tr['pa_rank']} hardest).")
    if freefall_items:
        payload += "\n".join(freefall_items) + "\n\n"
    else:
        payload += "- No teams currently on a 2+ game losing skid.\n\n"

    # --- BUILD RICH PLAYER CONTEXT ---
    player_context = {}
    for t in raw_data.get('teams', []):
        for entry in t.get('roster', {}).get('entries', []):
            p_data = entry.get('playerPoolEntry', {}).get('player', {})
            pid = p_data.get('id')
            if pid:
                outlook = p_data.get('seasonOutlook', '')
                season_avg = 0.0
                for s in p_data.get('stats', []):
                    if s.get('statSourceId') == 0 and s.get('scoringPeriodId') == 0 and s.get('seasonId') == season:
                        season_avg = round(s.get('appliedAverage', 0.0), 1)
                        break
                        
                player_context[pid] = {
                    'injury': p_data.get('injuryStatus', 'ACTIVE'),
                    'outlook': outlook,
                    'adp': p_data.get('ownership', {}).get('averageDraftPosition', 200.0),
                    'season_avg': season_avg,
                    'tenure': ''
                }

    # Fetch official NFL experience and draft tenure for all active players
    from concurrent.futures import ThreadPoolExecutor
    all_pids = list(player_context.keys())
    with ThreadPoolExecutor(max_workers=12) as executor:
        tenures = list(executor.map(fetch_athlete_tenure, all_pids))
    for pid, ten in zip(all_pids, tenures):
        player_context[pid]['tenure'] = ten
    save_tenure_cache()

    # --- MATCHUPS & BOX SCORES ---
    payload += f"## 🏈 WEEK {week_num} MATCHUPS, IN-GAME INJURIES & BOX SCORES\n"
    seen_owners = set()
    history_str = "## 📚 LEAGUE LORE & OWNER CONTEXT (MINIMAL RECAP)\n"
    
    memory_file = Path(__file__).resolve().parent / f"podcast_memory_{season}.json"
    if memory_file.exists():
        with open(memory_file, 'r') as mf:
            podcast_memory = json.load(mf)
    else:
        podcast_memory = {"lore_mentioned": []}

    for m in matchups:
        w = m['winner']
        l = m['loser']
        if isinstance(w, dict):
            w_team = teams.get(w['id'], {})
            l_team = teams.get(l['id'], {})
            w_tr = trajectories.get(w['id'], {})
            l_tr = trajectories.get(l['id'], {})
            
            w_rec = f"{w_tr.get('overall_w', 0)}-{w_tr.get('overall_l', 0)} (Streak: {w_tr.get('streak_str', 'N/A')})"
            l_rec = f"{l_tr.get('overall_w', 0)}-{l_tr.get('overall_l', 0)} (Streak: {l_tr.get('streak_str', 'N/A')})"
            
            div_tag = " [💥 DIVISIONAL RIVALRY]" if w_team.get('division') == l_team.get('division') else ""

            payload += f"### MATCHUP: {w['owner']} ({w_rec}) vs {l['owner']} ({l_rec}){div_tag}\n"
            payload += f"**Result:** {w['owner']} ({w['name']}) DEFEATED {l['owner']} ({l['name']})\n"
            payload += f"**Final Score:** {max(m['away_score'], m['home_score'])} to {min(m['away_score'], m['home_score'])} (Margin: {m['margin']})\n\n"
            
            w_perf = perfs[w['id']]
            l_perf = perfs[l['id']]
            
            # Identify in-game injuries for both teams
            matchup_injuries = []
            for s in w_perf.get('starters', []) + l_perf.get('starters', []):
                inj = detect_in_game_injury(s)
                if inj:
                    s_owner = w['owner'] if s in w_perf.get('starters', []) else l['owner']
                    matchup_injuries.append((s_owner, s, inj))

            if matchup_injuries:
                payload += "**🚨 IN-GAME INJURY REPORT (CRITICAL CONTEXT - DO NOT CALL THESE BLUNDERS):**\n"
                for s_owner, s, inj in matchup_injuries:
                    payload += f"- **{s['name']}** ({s_owner}'s starter): Scored {s['pts']} pts (Proj: {s['proj']}). SUFFERED AN IN-GAME INJURY! {inj['headline']} — {inj['story'][:180]}...\n"
                    payload += f"  *Directive:* {s_owner} did NOT make a mistake starting him. He was healthy at kickoff and was injured during the game. Sympathize with {s_owner}!\n"
                payload += "\n"

            # Top Performers
            w_starters = sorted(w_perf.get('starters', []), key=lambda x: x['pts'], reverse=True)
            l_starters = sorted(l_perf.get('starters', []), key=lambda x: x['pts'], reverse=True)
            
            payload += "**Winner's Top 3 Starters:**\n"
            for p in w_starters[:3]:
                ctx = player_context.get(p['id'], {'outlook': ''})
                payload += f"- {format_player_string(p, player_context)}\n"
                if ctx['outlook']: payload += f"  *Season Outlook:* {ctx['outlook']}\n"
                roto = fetch_rotowire_details(p['id'])
                if roto.get('spin'): payload += f"  *Recent News & Spin:* {roto['spin']}\n"
            
            payload += "\n**Loser's Top 3 Starters:**\n"
            for p in l_starters[:3]:
                ctx = player_context.get(p['id'], {'outlook': ''})
                payload += f"- {format_player_string(p, player_context)}\n"
                if ctx['outlook']: payload += f"  *Season Outlook:* {ctx['outlook']}\n"
                roto = fetch_rotowire_details(p['id'])
                if roto.get('spin'): payload += f"  *Recent News & Spin:* {roto['spin']}\n"
            
            payload += "\n**Roster Decisions, Blunders & Bad Luck:**\n"
            
            # Winner blunder evaluation
            if w_perf.get('blunders') and w_perf['blunders'][0]['diff'] > 15.0:
                b = w_perf['blunders'][0]
                started_inj = detect_in_game_injury(b['started'])
                if started_inj:
                    payload += f"- WINNER OVERCAME INJURY: {w['owner']} won despite losing {b['started']['name']} early to injury ({started_inj['headline']}). Leaving {b['benched']['name']} on the bench didn't cost them the win.\n"
                else:
                    payload += f"- WINNER'S BLUNDER: {w['owner']} won, but benched {format_player_string(b['benched'], player_context)} to start {format_player_string(b['started'], player_context)}. Laugh at them for getting away with it.\n"
            
            # Loser blunder evaluation
            if l_perf.get('blunders'):
                b = l_perf['blunders'][0]
                started_inj = detect_in_game_injury(b['started'])
                if started_inj:
                    payload += f"- 🚑 CRUEL INJURY MISFORTUNE (NOT A COACHING BLUNDER): {l['owner']} lost by {m['margin']}, and lost {b['started']['name']} to an in-game injury ({started_inj['headline']}) after scoring only {b['started']['pts']} points! While {b['benched']['name']} scored {b['benched']['pts']} on the bench, starting {b['started']['name']} was 100% the correct call before kickoff. DO NOT roast {l['owner']}—this was heartbreak caused by the injury gods!\n"
                elif b['diff'] >= m['margin']:
                    payload += f"- GAME-LOSING BLUNDER: {l['owner']} lost by {m['margin']}, and it is entirely because they benched {format_player_string(b['benched'], player_context)} to start {format_player_string(b['started'], player_context)}! This single bad decision cost them {round(b['diff'], 2)} points and the win. Roast them for this massive mistake.\n"
                    roto_s = fetch_rotowire_details(b['started']['id'])
                    if roto_s.get('spin'): payload += f"  *News on the started bust:* {roto_s['spin']}\n"
                else:
                    payload += f"- BENCH BLUNDER: {l['owner']} lost by {m['margin']} and left points on the bench. Their biggest mistake was benching {format_player_string(b['benched'], player_context)} to start {format_player_string(b['started'], player_context)}, leaving {round(b['diff'], 2)} points on the table.\n"
            else:
                busts = sorted(l_starters, key=lambda x: x['pts'] - x['proj'])
                if busts and (busts[0]['pts'] - busts[0]['proj'] < -3):
                    biggest_bust = busts[0]
                    started_inj = detect_in_game_injury(biggest_bust)
                    if started_inj:
                        payload += f"- IN-GAME INJURY HEARTBREAK: {l['owner']}'s player {biggest_bust['name']} got injured during the game ({started_inj['headline']}), sinking their chances.\n"
                    else:
                        payload += f"- TEAM UNDERPERFORMED: {l['owner']} lost by {m['margin']}. Their biggest bust was {format_player_string(biggest_bust, player_context)}. Discuss how this player let the team down.\n"
                        roto_s = fetch_rotowire_details(biggest_bust['id'])
                        if roto_s.get('spin'): payload += f"  *News on the biggest bust:* {roto_s['spin']}\n"
            
            payload += "\n"
            
            for owner in [w['owner'], l['owner']]:
                if owner not in seen_owners:
                    ctx = get_owner_storyline_context(owner)
                    history_str += f"- **{owner}**: {ctx['rings']} Rings. {ctx['tagline']}\n"
                    seen_owners.add(owner)

    payload += history_str
    
    # Save updated memory
    with open(memory_file, 'w') as mf:
        json.dump(podcast_memory, mf)

    if verified_quotes:
        payload += "\n## 🎤 MANAGER POST-GAME QUOTES (FROM DISCORD)\n"
        for q in verified_quotes:
            payload += f"- {q['header']} said: '{q['desc']}'\n"
            
    # --- HISTORICAL MATCHUP INTELLIGENCE (SUPER BOWLS) ---
    sb_history_str = ""
    for m in matchups:
        w_owner = m['winner']['owner'] if isinstance(m['winner'], dict) else None
        l_owner = m['loser']['owner'] if isinstance(m['loser'], dict) else None
        if w_owner and l_owner:
            for sb in BFL_CHAMPIONSHIPS:
                if (sb['champ'] == w_owner and sb['runner_up'] == l_owner) or (sb['champ'] == l_owner and sb['runner_up'] == w_owner):
                    sb_history_str += f"- **HISTORIC SUPER BOWL REMATCH:** {w_owner} and {l_owner} previously faced off in the {sb['year']} BFL Championship! {sb['champ']} won the title that year {sb['score']} (Margin: {sb['margin']}).\n"
    
    if sb_history_str:
        payload += "\n## ⚔️ HISTORICAL CHAMPIONSHIP RIVALRIES ACTIVATED THIS WEEK\n"
        payload += sb_history_str

    # --- PREVIOUS WEEKS RECAP ---
    payload += "\n" + build_previous_weeks_chronicle(raw_data, week_num, teams)

    # --- NEXT WEEK PREVIEW ---
    if week_num < 14:
        next_week_games = [g for g in raw_data.get('schedule', []) if g.get('matchupPeriodId') == week_num + 1]
        if next_week_games:
            payload += f"\n## 🔮 NEXT WEEK'S MATCHUPS (WEEK {week_num + 1} PREVIEW)\n"
            payload += "*Preview these key matchups and discuss the playoff stakes:*\n"
            for g in next_week_games:
                away_id = g.get('away', {}).get('teamId')
                home_id = g.get('home', {}).get('teamId')
                if away_id and home_id and away_id in teams and home_id in teams:
                    a_name = teams[away_id]['owner']
                    h_name = teams[home_id]['owner']
                    a_tr = trajectories.get(away_id, {})
                    h_tr = trajectories.get(home_id, {})
                    is_div = teams[away_id].get('division') == teams[home_id].get('division')
                    div_str = " [💥 DIVISION RIVALRY]" if is_div else ""
                    payload += f"- **{a_name}** ({a_tr.get('overall_w',0)}-{a_tr.get('overall_l',0)}) vs **{h_name}** ({h_tr.get('overall_w',0)}-{h_tr.get('overall_l',0)}){div_str}\n"

    # Write primary payload file
    out_file = Path(__file__).resolve().parent / f"notebook_payload_week_{week_num}_{season}.md"
    with open(out_file, 'w') as f:
        f.write(payload)

    # Write companion season chronicle file
    chronicle_content = f"# BFL (Beasts Football League) - {season} Season Chronicle (Weeks 1 to {week_num - 1})\n\n"
    chronicle_content += "This document contains the chronological history and results of the earlier weeks in the season.\n"
    chronicle_content += "Upload this file as a secondary reference source in Google NotebookLM so the hosts maintain perfect continuity without repeating themselves.\n\n"
    chronicle_content += build_previous_weeks_chronicle(raw_data, week_num, teams)
    
    chronicle_file = Path(__file__).resolve().parent / f"season_chronicle_weeks_1_to_{week_num - 1}_{season}.md"
    with open(chronicle_file, 'w') as f:
        f.write(chronicle_content)

    print(f"🎉 SUCCESS! Payload generated: {out_file}")
    print(f"📚 Companion Chronicle generated: {chronicle_file}")
    print("👉 Upload both files to NotebookLM to generate the audio overview with perfect continuity!")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--season", type=int, default=int(CURRENT_YEAR))
    parser.add_argument("--week", type=int, default=6)
    args = parser.parse_args()
    generate_payload(args.season, args.week)
