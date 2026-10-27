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
from FantasyRecap.league_history_intelligence import get_owner_storyline_context, BFL_CHAMPIONSHIPS, standardize_name, compile_all_time_h2h
from FantasyRecap.press_conference_manager import get_verified_manager_quotes
from FantasyRecap.playoff_odds_simulator import simulate_league_playoff_odds_from_espn
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
    'exited', 'carted', 'ruled out', 'did not return',
    'downgraded to out', 'sprain', 'strain', 'dislocated', 'shoulder',
    'forced out', 'knocked out', 'broken', 'fracture', 'torn', 'tear',
    'pectoral', 'achilles', 'clavicle', 'ribs', 'quadriceps', 'calf', ' wrist'
]

IN_GAME_EXIT_SIGNALS = [
    'early exit', 'exited', 'carted', 'did not return', 'forced out',
    'knocked out', 'downgraded to out', 'left the game',
    'left sunday', 'left thursday', 'left monday',
    'sustained a', 'suffered a', 'evaluated for a', 'taken to the locker room',
    'headed to the locker room', 'went to the locker room', 'briefly exited'
]

def detect_in_game_injury(player: dict, player_context: dict = None) -> dict:
    """
    Evaluates whether THIS specific player genuinely suffered an in-game injury during the week.
    Strictly avoids false positives where a Rotowire story mentions a teammate's injury or a normal 'in Sunday's win' phrase.
    """
    pts = player.get('pts', 0.0)
    proj = player.get('proj', 0.0)
    pid = player.get('id', 0)

    # If they scored 8+ points, an early exit didn't sink their fantasy day
    if pts >= 8.0:
        return None

    # Check ESPN official injury status if available
    espn_status = 'ACTIVE'
    if player_context and pid in player_context:
        espn_status = player_context[pid].get('injury', 'ACTIVE')

    details = fetch_rotowire_details(pid)
    headline_lower = details['headline'].lower()
    story_lower = details['story'].lower()
    full_text = headline_lower + ' ' + story_lower

    # The HEADLINE is always about this specific player (not a teammate).
    # Require that the headline itself mentions an injury or in-game exit, OR the player is officially flagged injured on ESPN
    # AND the first sentence of the story/headline explicitly states they exited/suffered an injury.
    headline_has_inj = any(kw in headline_lower for kw in INJURY_KEYWORDS)
    has_exit_signal = any(sig in full_text for sig in IN_GAME_EXIT_SIGNALS)

    if headline_has_inj and has_exit_signal:
        return {
            'player': player.get('name', 'Unknown'),
            'pts': pts,
            'proj': proj,
            'headline': details['headline'],
            'story': details['story']
        }
    elif espn_status not in ('ACTIVE', 'NORMAL') and headline_has_inj and pts <= 4.0 and proj >= 7.0:
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
            a_pts = float(g.get('away', {}).get('totalPoints', 0.0) or g.get('away', {}).get('totalPointsLive', 0.0) or 0.0)
            h_pts = float(g.get('home', {}).get('totalPoints', 0.0) or g.get('home', {}).get('totalPointsLive', 0.0) or 0.0)
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

def analyze_trade_strategic_motives(tr: dict, teams: dict = None, trajectories: dict = None) -> dict:
    """Generates deep strategic explanations for why each manager made the move."""
    a = tr['owner_a']
    b = tr['owner_b']
    a_sends = tr['a_sends']
    b_sends = tr['b_sends']
    
    a_names = [p['name'] if isinstance(p, dict) else p for p in a_sends]
    b_names = [p['name'] if isinstance(p, dict) else p for p in b_sends]
    all_names_str = ' '.join(a_names + b_names)
    
    a_str = ', '.join([f"{p['name']} ({p.get('pos', 'FLEX')})" if isinstance(p, dict) else p for p in a_sends])
    b_str = ', '.join([f"{p['name']} ({p.get('pos', 'FLEX')})" if isinstance(p, dict) else p for p in b_sends])
    
    # Specific verified league trade narratives & strategic motives
    if 'Brenton Strange' in all_names_str and 'Hockenson' in all_names_str:
        return {
            'theme': 'Win-Now Emergency Starter vs. Injured IR Playoff Stash',
            'owner_a_why': f"{a} flipped active TE Brenton Strange to Daniel Kruszewski for T.J. Hockenson. Shawn U had the standings cushion and luxury to stash injured Pro-Bowl TE Hockenson on IR as an elite TE1 lottery ticket for the fantasy playoffs, trading immediate production for championship ceiling.",
            'owner_b_why': f"{b} desperately needed live, healthy starting fantasy points at tight end right now to stop a losing skid and stay afloat in the playoff race. Daniel could not afford an empty roster spot or waiting on Hockenson's return, opting for Brenton Strange as an immediate plug-and-play starter.",
            'debate': "Did Daniel panic-sell an elite weapon too early for a short-term bandaid, or was dumping an injured player the only pragmatic move to keep his season alive? Who wins this deal come playoff time?"
        }
    if 'Caleb Williams' in all_names_str and ('Aaron Rodgers' in all_names_str or 'Colston Loveland' in all_names_str):
        return {
            'theme': 'Blockbuster Franchise Overhaul: Youth & Elite Ceilings vs. Veteran Depth',
            'owner_a_why': f"{a} went all-in on dynamic youth and explosive ceiling! Shipped out veteran QB Aaron Rodgers, RB depth piece Justice Hill, and WR Keon Coleman to acquire 3rd-year franchise QB Caleb Williams and 2nd-year athletic phenom TE Colston Loveland, locking in two high-ceiling positional centerpieces.",
            'owner_b_why': f"{b} prioritized multi-position depth and starting stability. Nitesh absorbed veteran QB Aaron Rodgers to stabilize his signal-caller floor while adding Justice Hill (immediate backfield touches) and Keon Coleman to patch multiple lineup holes and cover upcoming bye weeks.",
            'debate': "Blockbuster of the year! Did Abe pull off a masterclass securing two young high-ceiling cornerstones, or did Nitesh fleece him by turning two players into a balanced haul of starting depth?"
        }
    if 'Tyler Warren' in all_names_str and ('Rachaad White' in all_names_str or 'Dallas Goedert' in all_names_str):
        return {
            'theme': '2-for-1 Elite TE Consolidation vs. Multi-Position Roster Refuel',
            'owner_a_why': f"{a} executed a textbook 2-for-1 consolidation! Shawn packaged starting RB2 Rachaad White and veteran TE Dallas Goedert to land breakout sophomore TE Tyler Warren, securing an every-week mismatch at tight end while trimming excess depth.",
            'owner_b_why': f"{b} was bleeding in the standings and desperately needed to turn a single star into multiple starting pieces. rej received starting RB Rachaad White and proven veteran TE Dallas Goedert to instantly raise his weekly scoring floor and fill two starting vacancies.",
            'debate': "Did rej sell his brightest young star out of desperation, or did the 2-for-1 haul give him the balanced starting lineup he desperately needed to stop his slide?"
        }
    if 'Ladd McConkey' in all_names_str and 'Marvin Harrison' in all_names_str:
        return {
            'theme': 'The 3-for-1 Mega-Consolidation Blockbuster',
            'owner_a_why': f"{a} targeted Ladd McConkey aggressively, paying a massive depth toll (Marvin Harrison Jr., Emmett Johnson, and MarShawn Lloyd) to consolidate his roster and secure McConkey as his preferred WR anchor.",
            'owner_b_why': f"{b} capitalized on an aggressive buyer to restock his entire roster with three high-upside weapons, adding massive insulation across multiple positions.",
            'debate': "A colossal 3-for-1! Did Nick overpay for McConkey, or did Shawn Lukose win the trade on sheer asset value and depth?"
        }
    if 'Jakobi Meyers' in all_names_str and 'Matthew Golden' in all_names_str:
        return {
            'theme': 'Youth / Upside Flyer for Proven Veteran Floor',
            'owner_a_why': f"{a} acquired young talent to take a flyer on upside.",
            'owner_b_why': f"{b} acquired proven veteran Jakobi Meyers to provide reliable weekly PPR targets.",
            'debate': "Who gets more value: the steady veteran floor or the youthful upside flyer?"
        }
    if 'James Cook' in all_names_str and 'Achane' in all_names_str:
        return {
            'theme': 'RB Bellcow Consolidation vs. Explosive 2-for-1 Upside',
            'owner_a_why': f"{a} traded De'Von Achane and Jadarian Price to secure James Cook III as a true workhorse RB1.",
            'owner_b_why': f"{b} took on two explosive backs in Achane and Price to diversify his backfield depth and upside.",
            'debate': "Workhorse stability versus explosive multi-back depth: who won the backfield swap?"
        }

    # Dynamic Heuristic Engine for automated analysis
    a_len = len(a_sends)
    b_len = len(b_sends)
    
    # Lookup records & scoring profiles from trajectories if available
    a_rec_str, b_rec_str = "", ""
    if teams and trajectories:
        for tid, t_info in teams.items():
            if t_info.get('owner') == a and tid in trajectories:
                tr_a = trajectories[tid]
                a_rec_str = f" ({tr_a['overall_w']}-{tr_a['overall_l']}, #{tr_a['pf_rank']} scoring)"
            elif t_info.get('owner') == b and tid in trajectories:
                tr_b = trajectories[tid]
                b_rec_str = f" ({tr_b['overall_w']}-{tr_b['overall_l']}, #{tr_b['pf_rank']} scoring)"

    a_has_inj = any(isinstance(p, dict) and p.get('injury') in ['OUT', 'INJURY_RESERVE', 'IR', 'DOUBTFUL'] for p in a_sends)
    b_has_inj = any(isinstance(p, dict) and p.get('injury') in ['OUT', 'INJURY_RESERVE', 'IR', 'DOUBTFUL'] for p in b_sends)
    
    if a_has_inj and not b_has_inj:
        theme = "Win-Now Rental / Starter Acquisition vs. Injured Playoff Stash"
        a_why = f"{a}{a_rec_str} moved an injured asset ({a_str}) to acquire healthy, active talent ({b_str}) to score immediate points and survive a must-win stretch."
        b_why = f"{b}{b_rec_str} took on an injured player ({a_str}) at a discount to stash on IR for the late-season playoff push, sacrificing current production for high-ceiling playoff upside."
        debate = f"Can {a} stay alive in the playoff race with {b_str}, or will {b}'s injured stash pay off in a championship run?"
    elif b_has_inj and not a_has_inj:
        theme = "Injured Playoff Stash vs. Win-Now Rental / Starter Acquisition"
        a_why = f"{a}{a_rec_str} took on an injured player ({b_str}) to stash for a late-season playoff push, using current roster depth to absorb the downtime."
        b_why = f"{b}{b_rec_str} moved injured {b_str} to land healthy, immediate starting points ({a_str}) to plug lineup holes."
        debate = f"Did {b} give away an elite playoff asset too cheaply to survive the regular season?"
    elif a_len > b_len:
        theme = f"Consolidation Trade ({a_len}-for-{b_len} Upgrade)"
        a_why = f"{a}{a_rec_str} bundled multiple depth assets ({a_str}) to consolidate into an upgraded, higher-tier starter ({b_str}) to maximize starting lineup firepower."
        b_why = f"{b}{b_rec_str} parted with a marquee piece ({b_str}) to restock bench depth and patch multiple starting vacancies with {a_str}."
        debate = f"Did {a} sacrifice too much roster depth, or did {b} give up the best player in the deal?"
    elif b_len > a_len:
        theme = f"Depth Replenishment ({b_len}-for-{a_len} Multi-Asset Haul)"
        a_why = f"{a}{a_rec_str} traded a key starter ({a_str}) to acquire multiple starting-caliber pieces ({b_str}) to patch lineup holes and survive bye weeks."
        b_why = f"{b}{b_rec_str} packaged multiple bench pieces ({b_str}) to target a single premium centerpiece ({a_str})."
        debate = f"Did {b} consolidate successfully, or does {a}'s multi-piece haul provide better weekly floor?"
    else:
        theme = "Positional Need & Lineup Realignment"
        a_why = f"{a}{a_rec_str} acquired {b_str} to address a specific starting positional deficit or bye-week scheduling crunch."
        b_why = f"{b}{b_rec_str} acquired {a_str} to rebalance roster depth and fit preferred weekly matchups."
        debate = f"A classic one-for-one positional challenge: which manager got the bigger immediate upgrade?"

    return {
        'theme': theme,
        'owner_a_why': a_why,
        'owner_b_why': b_why,
        'debate': debate
    }

def format_in_depth_trade(tr: dict) -> str:
    """Formats an individual trade into an analytical, debate-sparking segment for Act 1."""
    a = tr['owner_a']
    b = tr['owner_b']
    wk = tr.get('week', '?')
    a_str = ', '.join([f"{p['name']} ({p.get('pos', 'FLEX')})" if isinstance(p, dict) else p for p in tr['a_sends']])
    b_str = ', '.join([f"{p['name']} ({p.get('pos', 'FLEX')})" if isinstance(p, dict) else p for p in tr['b_sends']])
    an = tr.get('analysis', {})
    
    out = f"#### 🔄 Trade (Week {wk}): {a} <---> {b} [{an.get('theme', 'Blockbuster Swap')}]\n"
    out += f"- **{a} acquired:** {b_str} (from {b})\n"
    out += f"- **{b} acquired:** {a_str} (from {a})\n"
    out += f"- **Why {a} made the move:** {an.get('owner_a_why', 'Upgraded starting lineup.')}\n"
    out += f"- **Why {b} made the move:** {an.get('owner_b_why', 'Acquired roster depth.')}\n"
    out += f"- **Host Debate & Trade Grade:** {an.get('debate', 'Debate who won!')}\n\n"
    return out

def fetch_league_trades(raw_data: dict, current_week: int, season: int, cookies: dict, teams: dict, trajectories: dict = None) -> list:
    """Pulls verified trades via multi-week roster reconciliation diffing with disk caching and strategic motive analysis."""
    cache_path = Path(__file__).resolve().parent / f"trade_history_{season}.json"
    cached_trades = []
    cached_weeks = set()
    
    if cache_path.exists():
        try:
            with open(cache_path, 'r') as cf:
                data = json.load(cf)
                cached_trades = data.get('trades', [])
                for tr in cached_trades:
                    cached_weeks.add(tr.get('week', 0))
        except Exception:
            cached_trades = []
            cached_weeks = set()

    max_target_week = current_week + 1
    needs_scan = False
    for w in range(2, max_target_week + 1):
        if w not in cached_weeks:
            needs_scan = True
            break

    if needs_scan or not cached_trades:
        all_raw = {current_week: raw_data}
        for w in range(1, max_target_week + 1):
            if w == current_week: continue
            try:
                all_raw[w] = fetch_espn_week_data(ESPN_LEAGUE_ID, str(season), w, ESPN_S2, ESPN_SWID)
            except Exception:
                pass

        def get_roster_dict(raw):
            rosters = {}
            player_info = {}
            for t in raw.get('teams', []):
                tid = t['id']
                rosters[tid] = {}
                for e in t.get('roster', {}).get('entries', []):
                    p = e.get('playerPoolEntry', {}).get('player', {})
                    pid = p.get('id')
                    pos_id = p.get('defaultPositionId', 0)
                    pos_name = {1: 'QB', 2: 'RB', 3: 'WR', 4: 'TE', 5: 'K', 16: 'D/ST'}.get(pos_id, 'FLEX')
                    name = p.get('fullName', f"Player {pid}")
                    injury = p.get('injuryStatus', 'ACTIVE')
                    rosters[tid][pid] = name
                    player_info[pid] = {'name': name, 'pos': pos_name, 'injury': injury}
            return rosters, player_info

        discovered_trades = []
        for w in range(2, max(all_raw.keys()) + 1):
            if w not in all_raw or (w - 1) not in all_raw: continue
            r_prev, info_prev = get_roster_dict(all_raw[w - 1])
            r_curr, info_curr = get_roster_dict(all_raw[w])
            
            all_tids = list(r_curr.keys())
            for i in range(len(all_tids)):
                for j in range(i + 1, len(all_tids)):
                    t1, t2 = all_tids[i], all_tids[j]
                    t1_sends = [info_prev.get(pid, {'name': name, 'pos': 'FLEX', 'injury': 'ACTIVE'}) for pid, name in r_prev[t1].items() if pid in r_curr[t2]]
                    t2_sends = [info_prev.get(pid, {'name': name, 'pos': 'FLEX', 'injury': 'ACTIVE'}) for pid, name in r_prev[t2].items() if pid in r_curr[t1]]
                    if t1_sends and t2_sends:
                        owner_1 = teams.get(t1, {}).get('owner', f"Team {t1}")
                        owner_2 = teams.get(t2, {}).get('owner', f"Team {t2}")
                        discovered_trades.append({
                            'week': w,
                            'owner_a': owner_1,
                            'owner_b': owner_2,
                            'a_sends': t1_sends,
                            'b_sends': t2_sends
                        })
        if discovered_trades:
            cached_trades = discovered_trades
            try:
                with open(cache_path, 'w') as cf:
                    json.dump({'season': season, 'trades': cached_trades}, cf, indent=2)
            except Exception:
                pass

    # Enrich each trade with strategic motives
    for tr in cached_trades:
        tr['analysis'] = analyze_trade_strategic_motives(tr, teams, trajectories)

    return cached_trades

def fetch_recent_roster_moves(raw_data: dict, current_week: int, season: int, cookies: dict, teams: dict) -> list:
    """Fetches notable recent waiver and free agent moves for skill positions, strictly filtering out kickers, D/ST, and minor churn."""
    tx_url = f'https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/{season}/segments/0/leagues/{ESPN_LEAGUE_ID}?view=mTransactions2'
    moves = []
    try:
        r = requests.get(tx_url, cookies=cookies, timeout=5)
        if r.status_code != 200:
            return []
        data = r.json()
        
        pinfo = {}
        for t in raw_data.get('teams', []):
            for entry in t.get('roster', {}).get('entries', []):
                pl = entry.get('playerPoolEntry', {}).get('player', {})
                pid = pl.get('id')
                if pid:
                    pos_id = pl.get('defaultPositionId', 0)
                    pos_name = {1: 'QB', 2: 'RB', 3: 'WR', 4: 'TE', 5: 'K', 16: 'D/ST'}.get(pos_id, 'FLEX')
                    pinfo[pid] = {
                        'name': pl.get('fullName', f"Player {pid}"),
                        'pos': pos_name,
                        'pos_id': pos_id
                    }

        for t in data.get('transactions', []):
            status = t.get('status')
            if status != 'EXECUTED':
                continue  # STRICT RULE: Never process pending waiver bids or unexecuted moves!
            for it in t.get('items', []):
                it_type = it.get('type')
                pid = it.get('playerId')
                info = pinfo.get(pid)
                if not info:
                    try:
                        ar = requests.get(f"https://sports.core.api.espn.com/v2/sports/football/leagues/nfl/athletes/{pid}", timeout=1).json()
                        pos_abbr = ar.get('position', {}).get('abbreviation', 'FLEX')
                        info = {'name': ar.get('fullName', f"Player {pid}"), 'pos': pos_abbr, 'pos_id': 0}
                        pinfo[pid] = info
                    except Exception:
                        info = {'name': f"Player {pid}", 'pos': 'FLEX', 'pos_id': 0}
                
                pname = info['name']
                pos = info['pos']
                pos_id = info['pos_id']
                
                # STRICT FILTER: Exclude Kickers, D/ST, and minor special teams churn
                if pos in ['PK', 'K', 'DEF', 'DST', 'D/ST'] or pos_id in [5, 16] or 'D/ST' in pname or pid < 0:
                    continue
                    
                from_id = it.get('fromTeamId')
                to_id = it.get('toTeamId')
                
                if it_type == 'DROP' and from_id in teams:
                    owner = teams[from_id]['owner']
                    move_str = f"✂️ **{owner}** cut **{pname}** ({pos})"
                    if move_str not in moves:
                        moves.append(move_str)
                elif it_type == 'ADD' and to_id in teams:
                    owner = teams[to_id]['owner']
                    move_str = f"⚡ **{owner}** signed **{pname}** ({pos})"
                    if move_str not in moves:
                        moves.append(move_str)
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
            a_pts = float(g.get('away', {}).get('totalPoints', 0.0) or g.get('away', {}).get('totalPointsLive', 0.0) or 0.0)
            h_pts = float(g.get('home', {}).get('totalPoints', 0.0) or g.get('home', {}).get('totalPointsLive', 0.0) or 0.0)
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
        if wk in week_themes:
            theme = week_themes[wk]
        elif games:
            top_g = max(games, key=lambda x: x['w_pts'])
            closest_g = min(games, key=lambda x: x['margin'])
            blowout_g = max(games, key=lambda x: x['margin'])
            theme = (
                f"Week {wk}: {top_g['winner']} led the league with {top_g['w_pts']:.1f} pts; "
                f"{closest_g['winner']} edged {closest_g['loser']} by {closest_g['margin']:.2f} pts ({closest_g['w_pts']:.1f}-{closest_g['l_pts']:.1f}); "
                f"{blowout_g['winner']} routed {blowout_g['loser']} by {blowout_g['margin']:.1f} pts."
            )
        else:
            theme = f"Week {wk} Action"
        md += f"### 🏈 WEEK {wk} RECAP ({theme})\n"
        for g in games:
            md += f"- **{g['winner']}** defeated **{g['loser']}** ({g['w_pts']:.1f} to {g['l_pts']:.1f}, Margin: {g['margin']:.2f})\n"
        md += "\n"
        
    return md

def build_completed_matchup_h2h_tidbit(w_owner: str, l_owner: str, h2h_through_week: dict, current_season: int, current_week: int) -> str:
    """Builds a punchy historical H2H & rivalry tidbit for a just-completed matchup in Act 2."""
    w_std = standardize_name(w_owner)
    l_std = standardize_name(l_owner)
    pair = tuple(sorted([w_std, l_std]))
    history = sorted(h2h_through_week.get(pair, []), key=lambda x: (x['year'], x['week']))
    if not history:
        return f"**📜 All-Time Series Tidbit:** First-ever BFL meeting between {w_std} and {l_std} ({w_std} now leads 1-0)."

    w_wins = sum(1 for g in history if g['winner'] == w_std)
    l_wins = sum(1 for g in history if g['winner'] == l_std)
    total = len(history)
    champ_playoffs = [g for g in history if g.get('is_champ_playoff')]

    # Calculate active winning streak (including this week's game)
    streak_winner = None
    streak_len = 0
    for g in reversed(history):
        if streak_winner is None:
            streak_winner = g['winner']
            streak_len = 1
        elif g['winner'] == streak_winner:
            streak_len += 1
        else:
            break

    if w_wins > l_wins:
        series_str = f"{w_std} now leads the all-time series **{w_wins}-{l_wins}** ({total} meetings since 2008)"
    elif l_wins > w_wins:
        series_str = f"{l_std} still leads the all-time series **{l_wins}-{w_wins}** despite {w_std}'s win ({total} meetings since 2008)"
    else:
        series_str = f"{w_std}'s victory knots their all-time series at **{w_wins}-{l_wins}** ({total} meetings since 2008)"

    notes = []
    # Check Super Bowl history
    sb_list = [sb for sb in BFL_CHAMPIONSHIPS if {sb['champ'], sb['runner_up']} == {w_std, l_std}]
    if sb_list:
        sb = sb_list[-1]
        notes.append(f"🏆 **Super Bowl History:** Previously met in the {sb['year']} BFL Championship (won by {sb['champ']}, {sb['score']})")

    # Check if they played earlier this same season
    earlier_this_yr = [g for g in history if g['year'] == current_season and g['week'] < current_week]
    if earlier_this_yr:
        eg = earlier_this_yr[-1]
        if eg['winner'] == w_std:
            notes.append(f"🧹 **Season Sweep!** {w_std} also won their Week {eg['week']} clash (+{eg['margin']:.2f} pts) to complete the {current_season} regular-season sweep")
        else:
            notes.append(f"🔁 **Season Split:** {w_std} avenges their Week {eg['week']} loss to {l_std} (+{eg['margin']:.2f} pts), splitting the {current_season} season series 1-1")
    elif len(history) >= 2:
        prev_g = history[-2]
        prev_hi = max(prev_g['score_a'], prev_g['score_b'])
        prev_lo = min(prev_g['score_a'], prev_g['score_b'])
        prev_stage = " in the Championship Playoffs" if prev_g.get('is_champ_playoff') else ""
        notes.append(f"Previous meeting: {prev_g['winner']} won in {prev_g['year']} Week {prev_g['week']}{prev_stage} ({prev_hi:.1f}-{prev_lo:.1f})")

    if streak_len >= 3:
        notes.append(f"🔥 {streak_winner} has now won **{streak_len} straight** in this head-to-head rivalry")
    elif champ_playoffs:
        w_pw = sum(1 for g in champ_playoffs if g['winner'] == w_std)
        l_pw = sum(1 for g in champ_playoffs if g['winner'] == l_std)
        notes.append(f"Playoff H2H: {w_std} {w_pw}-{l_pw} {l_std}")

    notes_str = " | " + " | ".join(notes) if notes else ""
    return f"**📜 All-Time Rivalry Context:** {series_str}{notes_str}."

def build_upcoming_matchup_h2h_tidbit(a_owner: str, h_owner: str, h2h_through_week: dict, current_season: int) -> str:
    """Builds a concise historical H2H & rematch note for an upcoming Act 3 preview matchup."""
    a_std = standardize_name(a_owner)
    h_std = standardize_name(h_owner)
    pair = tuple(sorted([a_std, h_std]))
    history = sorted(h2h_through_week.get(pair, []), key=lambda x: (x['year'], x['week']))
    if not history:
        return "First-ever meeting in BFL history (0-0)"

    a_wins = sum(1 for g in history if g['winner'] == a_std)
    h_wins = sum(1 for g in history if g['winner'] == h_std)
    total = len(history)
    last_g = history[-1]
    lg_hi = max(last_g['score_a'], last_g['score_b'])
    lg_lo = min(last_g['score_a'], last_g['score_b'])
    lg_stage = " Playoff" if last_g.get('is_champ_playoff') else ""

    if a_wins > h_wins:
        rec_str = f"{a_std} leads {a_wins}-{h_wins}"
    elif h_wins > a_wins:
        rec_str = f"{h_std} leads {h_wins}-{a_wins}"
    else:
        rec_str = f"Series tied {a_wins}-{h_wins}"

    # Check if in-season rematch
    in_season = [g for g in history if g['year'] == current_season]
    sb_list = [sb for sb in BFL_CHAMPIONSHIPS if {sb['champ'], sb['runner_up']} == {a_std, h_std}]

    if in_season:
        ig = in_season[-1]
        rev = h_std if ig['winner'] == a_std else a_std
        hook = f"🔁 **In-Season Rematch** ({ig['winner']} won Week {ig['week']} `{max(ig['score_a'], ig['score_b']):.1f}-{min(ig['score_a'], ig['score_b']):.1f}`; {rev} seeks revenge)"
    elif sb_list:
        sb = sb_list[-1]
        hook = f"🏆 **{sb['year']} Super Bowl Rematch** ({sb['champ']} won title {sb['score']}; last met {last_g['year']} Wk {last_g['week']}{lg_stage}: {last_g['winner']} +{last_g['margin']:.2f})"
    else:
        hook = f"Last met {last_g['year']} Wk {last_g['week']}{lg_stage}: {last_g['winner']} won `{lg_hi:.1f}-{lg_lo:.1f}` (+{last_g['margin']:.2f} pts)"

    return f"**18-Yr H2H:** {rec_str} ({total} games) — {hook}"


def extract_live_desk_by_matchup(season: int, week_num: int, matchups: list) -> tuple:
    """
    Returns (per_matchup_dict, full_markdown_section) where per_matchup_dict maps
    tuple(sorted([owner_a, owner_b])) -> list of bullet strings for that matchup.
    """
    state_dir = Path(__file__).resolve().parent / "live_state"
    state_file = state_dir / f"state_{season}_week_{week_num}.json"
    log_file = state_dir / f"alerts_log_{season}_week_{week_num}.jsonl"

    per_matchup = {}
    bullets = []

    if not state_file.exists() and not log_file.exists():
        return per_matchup, ""

    if state_file.exists():
        try:
            with open(state_file, "r") as sf:
                live_st = json.load(sf)

            final_by_pair = {}
            for m in matchups:
                a_own = m['away']['owner']
                h_own = m['home']['owner']
                final_by_pair[(a_own, h_own)] = m

            for mid, st_m in live_st.items():
                a_own = st_m.get('away_owner', '')
                h_own = st_m.get('home_owner', '')
                fin = final_by_pair.get((a_own, h_own))
                if not fin or not isinstance(fin.get('winner'), dict):
                    continue

                pair_key = tuple(sorted([a_own, h_own]))
                per_matchup.setdefault(pair_key, [])

                fin_winner = fin['winner']['owner']
                fin_loser = fin['loser']['owner']
                fin_w_pts = max(fin['away_score'], fin['home_score'])
                fin_l_pts = min(fin['away_score'], fin['home_score'])
                fin_margin = fin['margin']

                snf_a_score = float(st_m.get('away_score', 0.0))
                snf_h_score = float(st_m.get('home_score', 0.0))
                snf_a_prob = float(st_m.get('away_prob', 0.5))
                snf_h_prob = float(st_m.get('home_prob', 0.5))

                snf_leader = a_own if snf_a_score > snf_h_score else (h_own if snf_h_score > snf_a_score else 'TIED')
                win_was_away = (fin_winner == a_own)
                winner_snf_score = snf_a_score if win_was_away else snf_h_score
                loser_snf_score = snf_h_score if win_was_away else snf_a_score
                winner_snf_prob = (snf_a_prob if win_was_away else snf_h_prob) * 100.0

                w_side_key = 'away_starters' if win_was_away else 'home_starters'
                mnf_heroes = [
                    s['name'] for s in st_m.get(w_side_key, [])
                    if s.get('game_status') == 'PRE' or float(s.get('pct_remaining', 0.0)) >= 0.9
                ]
                mnf_hero_str = f" behind MNF heroics from **{', '.join(mnf_heroes)}**" if mnf_heroes else " on Monday Night Football"

                if snf_leader != 'TIED' and snf_leader != fin_winner and loser_snf_score > winner_snf_score:
                    deficit = round(loser_snf_score - winner_snf_score, 2)
                    mnf_pts_added = round(fin_w_pts - winner_snf_score, 2)
                    if winner_snf_prob <= 15.0:
                        b_str = f"- 🚨 **MONDAY NIGHT MIRACLE ON `#live-game-desk`:** **{fin_winner}** entered Monday Night Football trailing **{fin_loser}** `{winner_snf_score:.2f}` to `{loser_snf_score:.2f}` (a `{deficit:.2f}`-point deficit with just a **`{winner_snf_prob:.1f}%` win probability** on the Sunday Night Miracle Board!). {fin_winner} erupted for **`+{mnf_pts_added:.2f}` points**{mnf_hero_str} to pull off a stunning `{fin_w_pts:.2f}` to `{fin_l_pts:.2f}` comeback victory (Margin: `{fin_margin:.2f}` pts)!"
                    else:
                        b_str = f"- 🔄 **MONDAY NIGHT OVERTHROW ON `#live-game-desk`:** **{fin_winner}** trailed **{fin_loser}** `{winner_snf_score:.2f}` to `{loser_snf_score:.2f}` entering MNF (`{winner_snf_prob:.1f}%` MNF win odds), then poured in `+{mnf_pts_added:.2f}` pts{mnf_hero_str} to overtake {fin_loser} `{fin_w_pts:.2f}` to `{fin_l_pts:.2f}`!"
                    bullets.append(b_str)
                    per_matchup[pair_key].append(b_str)
                elif fin_margin <= 2.5 and st_m.get('last_alert_time', 0) > 0:
                    b_str = f"- ⚡ **SUNDAY NIGHT 4TH-QUARTER SHOCKWAVE & LEAD CHANGE (`#live-game-desk`):** **{fin_winner}** pulled off a jaw-dropping late rally to edge **{fin_loser}** `{fin_w_pts:.2f}` to `{fin_l_pts:.2f}` (Margin: **`{fin_margin:.2f}` pts**), capped by a late probability shockwave and lead overtake on the Live Game Desk!"
                    bullets.append(b_str)
                    per_matchup[pair_key].append(b_str)

                for nuc_name in st_m.get('nuclear_alerted', []):
                    b_str = f"- ☢️ **LIVE DESK NUCLEAR ERUPTION:** **{nuc_name}** triggered the `#live-game-desk` Nuclear Player Siren during `{a_own} vs {h_own}`!"
                    bullets.append(b_str)
                    per_matchup[pair_key].append(b_str)
        except Exception:
            pass

    if log_file.exists():
        try:
            with open(log_file, "r") as lf:
                for line in lf:
                    if not line.strip():
                        continue
                    ev = json.loads(line)
                    if ev.get("status") == "FIRED" and ev.get("event_type") in ["SHOCKWAVE", "LEAD_CHANGE"]:
                        cat = ev.get("catalyst", "")
                        b_str = f"- 📡 **LIVE DESK ALERT ({ev.get('event_type')}):** {ev.get('matchup')} ({ev.get('swing_pct', '')}% swing) {cat}".strip()
                        bullets.append(b_str)
        except Exception:
            pass

    if not bullets:
        return per_matchup, ""

    out = "\n## ⚡ LIVE GAME DESK: IN-GAME WIN PROBABILITY SWINGS & MNF MIRACLES\n"
    out += "*These real-time odds swings, 4th-quarter rallies, and Monday Night Football comebacks lit up the `#live-game-desk` channel this week. Weave this drama directly into your matchup breakdowns:*\n"
    out += "\n".join(bullets) + "\n"
    return per_matchup, out


def build_live_game_desk_recap_section(season: int, week_num: int, matchups: list) -> str:
    _, out = extract_live_desk_by_matchup(season, week_num, matchups)
    return out



def generate_payload(season: int, week_num: int):
    print(f"📡 Fetching live ESPN data for Season {season}, Week {week_num}...")
    raw_data = fetch_espn_week_data(ESPN_LEAGUE_ID, str(season), week_num, ESPN_S2, ESPN_SWID)
    teams = parse_league_members_and_teams(raw_data)
    matchups, perfs, _ = analyze_week(raw_data, week_num)
    verified_quotes = get_verified_manager_quotes(week_num=week_num)
    cookies = {"espn_s2": ESPN_S2, "SWID": ESPN_SWID}

    trajectories = calculate_season_trajectories(raw_data, week_num, teams)
    trades = fetch_league_trades(raw_data, week_num, season, cookies, teams, trajectories)
    playoff_odds = simulate_league_playoff_odds_from_espn(raw_data, week_num, n_sims=5000) if week_num <= 14 else {}
    h2h_through_week, _ = compile_all_time_h2h(ESPN_LEAGUE_ID, ESPN_S2, ESPN_SWID, current_season=season, up_to_week=week_num + 1)

    payload = f"# BFL (Beasts Football League) - Season {season}, Week {week_num} Scoreboard\n\n"
    
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
    payload += "Hosts: Chris & Dave. Style: High energy, sharp comedic banter, authentic television/radio sports desk style (like ESPN's SportsCenter or NFL Live) where the anchors introduce themselves and immediately dive into the real action.\n\n"
    
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

    # --- MANDATORY SHOW COLD OPEN ---
    payload += "### 📺 MANDATORY BROADCAST COLD OPEN (SPORTSCENTER / SPORTS RADIO STYLE):\n"
    payload += "- **STRICTLY BANNED OPENING SHTICK:** ABSOLUTELY DO NOT open the show with rhetorical questions, hypothetical scenarios, or philosophical musings (e.g. 'Imagine scoring a high number of points but still losing, how would you feel?', 'Picture this...', 'Have you ever wondered...'). NO hypothetical opening monologues!\n"
    payload += "- **MANDATORY BROADCAST INTRO:** You MUST start the episode immediately as a clean, authentic broadcast desk introduction:\n"
    payload += f"  1. Welcome the audience: 'Welcome to the Week {week_num} edition of the Tuesday Morning Hangover!'\n"
    payload += "  2. The hosts MUST introduce themselves right away: 'I'm Chris alongside Dave...'\n"
    payload += f"  3. Deliver a punchy 2-sentence teaser of Week {week_num}'s biggest headlines, upsets, and division shifts.\n"
    payload += "  4. Immediately transition straight into the action and the recap of the week!\n\n"

    # --- STRICT PACING & BROADCAST TIME ALLOCATION (TARGET: 20 TO 30 MINUTES, 75%+ ON MATCHUPS & QUOTES) ---
    payload += "### ⏱️ MANDATORY EPISODE LENGTH (20–30 MINUTES) & TIME ALLOCATION (75%+ MATCHUPS & OWNER QUOTES)\n"
    payload += "**CRITICAL DURATION DIRECTIVE:** This is a full-length **20 to 30 minute** weekly flagship broadcast! DO NOT rush through the games or wrap up in 12–13 minutes. Take your time unpacking all 8 matchups in rich detail (~2.5 to 3.5 minutes per matchup) and reacting to every single owner quote!\n\n"
    payload += "1. **ACT 1: THE OPENING DRIVE (STRICT MAXIMUM ~10% OF THE EPISODE / UNDER 2.5 MINUTES TOTAL)**\n"
    payload += f"   - **SportsCenter Desk Welcome:** Welcome listeners to the Week {week_num} edition of the Tuesday Morning Hangover, Chris & Dave introduce themselves, and tease the biggest headlines of the week.\n"
    payload += "   - **Division Standings Snapshot:** Quick 60-second look at division leaders, win streaks, and losing skids.\n"
    payload += "   - **Trades From This Past Week ONLY (Strict 60–90 Second Cap):** ONLY mention trades that happened in the last 7 days (since last Tuesday morning). Spend **NO MORE THAN 60–90 SECONDS TOTAL** on trades! NEVER spend 5 minutes debating trades, and NEVER discuss trades from earlier weeks.\n"
    payload += "   - *Strict Pacing Rule:* Wrap up Act 1 in under 2.5 minutes so you can spend 75%+ of the show on the 8 matchups and owner quotes!\n\n"
    payload += f"2. **ACT 2: DEEP-DIVE GAME-BY-GAME BREAKDOWNS & OWNER QUOTES (THE CORE 75%+ OF THE EPISODE / 16–22 MINUTES)**\n"
    payload += "   - **MANDATORY DEEP COVERAGE (2.5 TO 3.5 MINUTES PER GAME):** You MUST cover **ALL 8 MATCHUPS and ALL 16 TEAMS** in full detail! Spend 2.5 to 3.5 minutes on EVERY SINGLE matchup so the full show runs 20–30 minutes.\n"
    payload += "   - **WHAT TO COVER FOR EVERY SINGLE GAME (DO NOT RUSH):**\n"
    payload += "     a) **Final Score, Margin & 18-Year All-Time Rivalry Context:** Who won, by how much, and how it shifts their all-time head-to-head series.\n"
    payload += "     b) **Direct Owner Quotes & Discord Trash Talk (MANDATORY):** Read verbatim and react to the **💬 DIRECT OWNER QUOTES & TRASH TALK** embedded inside each matchup! Banter about the owners' excuses, boasts, and saltiness.\n"
    payload += "     c) **Live Game Desk Win-Probability Swings & MNF Drama:** Break down the 4th-quarter comebacks, Sunday Night shockwaves, and Monday Night Football miracles.\n"
    payload += "     d) **Studs, Duds & Start/Sit Blunders for BOTH Teams:** Discuss the top 3 starters, bench blunders, and play-by-play NFL statlines for both the winner and the loser.\n"
    payload += "     e) **Standings & Monte Carlo Playoff Odds Impact:** How the win or loss changes each manager's division race and playoff percentage.\n\n"
    payload += f"3. **ACT 3: THE TWO-MINUTE DRILL & NEXT WEEK LOOKAHEAD (FINAL ~15% OF THE EPISODE / 3–4 MINUTES)**\n"
    payload += f"   - Preview all 8 Week {week_num + 1} matchups, highlighting 18-year H2H history, Super Bowl rematches, and Win/Loss Playoff Leverage swings.\n"
    payload += "   - Closing thoughts, sign-off banter, and parting barbs.\n\n"

    # --- CRITICAL HOST DIRECTIVES ---
    payload += "### ⚡ CRITICAL SHOW DIRECTIVES (MUST FOLLOW):\n"
    payload += f"1. **MANDATORY SPORTSCENTER DESK INTRO & NO HYPOTHETICAL SHTICK:** Never open the episode asking listeners to 'imagine' a scenario or asking rhetorical questions. Open like professional sports anchors: 'Welcome to the Week {week_num} edition of the Tuesday Morning Hangover, I'm Chris alongside Dave!' NEVER use the term 'Data Sheet'—always refer to the board as the **'Scoreboard'**.\n"
    payload += "2. **20–30 MINUTE TARGET LENGTH & 75%+ ON MATCHUPS/QUOTES:** Do NOT cut the show short at 13 minutes! Unpack every matchup thoroughly, read the owners' actual Discord quotes out loud, and debate both sides of every game.\n"
    payload += "3. **DO NOT INVENT MID-GAME INJURIES (CRITICAL ACCURACY RULE):** NEVER claim a player got injured mid-game unless they are explicitly listed under a `🚨 VERIFIED IN-GAME INJURY REPORT` block! If a player scored 0 to 6 points and is NOT listed in a Verified In-Game Injury Report, they did NOT get hurt mid-game—they were healthy and simply laid an egg (a dud performance). Roast healthy players who disappeared; only sympathize with verified in-game injuries.\n"
    
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
    payload += "5. **HEAVY FOCUS ON OWNER QUOTES & TRASH TALK:** The managers love hearing their own Discord quotes from `#trash-talk` and `#live-game-desk` read and roasted on the air! Quote them directly during their matchup breakdowns.\n"
    payload += "6. **STRICT 60–90 SECOND CAP ON TRADES (LAST 7 DAYS ONLY):** Only discuss trades that occurred this past week (since last Tuesday morning), and spend at most 60–90 seconds on them total. Do NOT let trade talk eat into matchup breakdown time.\n"
    payload += "7. **TIMELINE REALITY & PLAYER TENURE (DO NOT CALL VETERANS ROOKIES):** The current NFL season is 2026!\n"
    payload += "   - Every player in this document has their official NFL tenure tag right next to their name (e.g. `[2nd-Year Sophomore (Drafted 2025)]` or `[3rd-Year Player (Drafted 2024)]`).\n"
    payload += "   - **Colston Loveland, Luther Burden III, Tyler Warren, Jeremiyah Love, and Quinshon Judkins** were drafted in 2025 and ARE IN THEIR 2ND SEASON (SOPHOMORES). THEY ARE NOT ROOKIES!\n"
    payload += "   - **Caleb Williams, Jayden Daniels, Bo Nix, Marvin Harrison Jr, Malik Nabers, Rome Odunze, and Brock Bowers** were drafted in 2024 and ARE IN THEIR 3RD SEASON (VETERANS).\n"
    payload += "   - ONLY players explicitly labeled `[Rookie (1st Year, Class of 2026)]` are rookies.\n"
    payload += "8. **CENTRAL TIME ZONE:** The BFL operates in Central Time (CT). Sunday games kick off at 12:00 PM CT (noon).\n"
    
    # Dynamic Luck Directive
    pt_callouts = [f"{teams[tid]['owner']} ({tr['overall_w']}-{tr['overall_l']} with {round(tr['points_for'], 1)} PF, #{tr['pf_rank']} scoring)" for tid, tr in trajectories.items() if tr['overall_w'] > tr['overall_l'] and tr['pf_rank'] >= 10]
    unlucky_callouts = [f"{teams[tid]['owner']} ({tr['overall_w']}-{tr['overall_l']} despite {round(tr['points_for'], 1)} PF, #{tr['pf_rank']} scoring)" for tid, tr in trajectories.items() if tr['overall_w'] <= tr['overall_l'] and tr['pf_rank'] <= 6]
    gauntlet_callouts = [f"{teams[tid]['owner']} ({round(tr['points_against'], 1)} PA, #{tr['pa_rank']} schedule)" for tid, tr in trajectories.items() if tr['pa_rank'] <= 2]
    
    payload += "9. **OFFICIAL PRONUNCIATION, NICKNAMES & INSIDE JOKES GUIDE (MANDATORY):**\n"
    payload += "   - **The Jabroni Trophy:** Pronounced *'Jah-BRO-nee'* (the coveted league championship trophy).\n"
    payload += "   - **Saagar Gupta:** Pronounced *'SAH-gur'* (also known as 'King Gupta').\n"
    payload += "   - **rej hoxha:** Pronounced *'Ray'* (first name Rej is spoken like Ray).\n"
    payload += "   - **Shawn Lukose:** Last name pronounced *'Luke-ose'* (4-time BFL GOAT).\n"
    payload += "   - **Shawn Ullenbrauck:** Commonly called by his league nickname **'Thor'** or 'Shawn U'.\n"
    payload += "   - **Nael Ahmed:** Pronounced *'Nile'*.\n"
    payload += "   - **Samran Mirza:** Pronounced *'Sum-rahn'*.\n"
    payload += "   - **Dino Davros:** Pronounced *'Dee-no'*.\n"
    payload += "   - **Daniel Kruszewski:** Last name pronounced *'Cruise-sheff-skee'* (or 'Dan' / 'DTM').\n"
    payload += "   - **Adam Olen:** Often called by his initials **'AMO'**.\n"
    payload += "   - **Alex Kite:** Often called by his nickname **'Shooter'**.\n"
    payload += "   - **Mykonos Minotaurs (Nick Christus):** Pronounced *'Mee-ko-nos'*.\n\n"

    # Filter trades strictly to the last week (since last Tuesday morning: week_num or week_num + 1)
    recent_weeks = [week_num, week_num + 1]
    recent_trades = [tr for tr in trades if tr.get('week') in recent_weeks]
    roster_moves = fetch_recent_roster_moves(raw_data, week_num, season, cookies, teams)

    # --- LEAGUE-WIDE POINTS RANKINGS & SCORING SUMMARY ---
    payload += "## 📊 LEAGUE-WIDE SCORING & POINTS SUMMARY\n"
    payload += "*Analytical reference for Act 1: Quick data comparison of total points scored (PF) versus points allowed (PA). Use purely as statistical context, not as an opening philosophical debate.*\n\n"
    payload += "| Rank | Owner | Team | Record | Points For (Avg PPG) | Points Against (Opp PPG) | Scoring Profile |\n"
    payload += "| :--- | :--- | :--- | :--- | :--- | :--- | :--- |\n"
    
    all_teams_by_pf = sorted(teams.values(), key=lambda t: trajectories[t['id']]['points_for'], reverse=True)
    for rank, t in enumerate(all_teams_by_pf, 1):
        tr = trajectories[t['id']]
        avg_ppg = tr['points_for'] / week_num
        opp_ppg = tr['points_against'] / week_num
        
        if tr['overall_w'] < tr['overall_l'] and rank <= 5:
            diagnosis = "⚡ **HIGH SCORER / UNLUCKY** (Top 5 Scoring, Losing Record)"
        elif tr['overall_w'] > tr['overall_l'] and rank >= 12:
            diagnosis = "🍀 **LOW SCORER / WINNING** (Bottom 5 Scoring, Winning Record)"
        elif tr['pa_rank'] <= 2 and tr['overall_w'] <= 2:
            diagnosis = "💀 **TOUGH SCHEDULE** (Faced Top 2 Most Points in BFL)"
        elif tr['overall_l'] == 0:
            diagnosis = f"👑 **THE UNDEFEATED LEADER** (Rank #{rank} Scoring, {tr['overall_w']}-{tr['overall_l']})"
        else:
            diagnosis = "⚖️ Balanced Matchups"
            
        payload += f"| #{rank} | **{t['owner']}** | {t['name']} | **{tr['overall_w']}-{tr['overall_l']}** | {round(tr['points_for'], 1)} ({round(avg_ppg, 1)} PPG) | {round(tr['points_against'], 1)} ({round(opp_ppg, 1)} PPG) | {diagnosis} |\n"
    payload += "\n"
    
    payload += "### 🗣️ TALKING POINTS ON POINTS & SCORING:\n"
    if pt_callouts:
        payload += f"- **Low-Scoring Winning Teams:** {', '.join(pt_callouts)}. Winning with clutch defense and low-scoring dogfights!\n"
    if unlucky_callouts:
        payload += f"- **High-Scoring Losing Teams:** {', '.join(unlucky_callouts)}. Scoring big points but running into high opponent totals.\n"
    if gauntlet_callouts:
        payload += f"- **Hardest Schedules:** {', '.join(gauntlet_callouts)}. Opponents are putting up big totals against them.\n\n"

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
            std_own = standardize_name(t['owner'])
            if std_own.lower() == 'rej hoxha': std_own = 'rej hoxha'
            po_info = playoff_odds.get(std_own, {})
            po_str = f" | **Playoff Odds: {po_info.get('playoff_pct', 0)}%** (Div Title: {po_info.get('div_title_pct', 0)}%, #1 Bye: {po_info.get('bye_pct', 0)}%)" if po_info else ""
            payload += f"{rank}. **{t['owner']}** ({t['name']}): **{tr['overall_w']}-{tr['overall_l']}** (Div: {tr['div_w']}-{tr['div_l']}) | Streak: `{tr['streak_str']}` | **{round(tr['points_for'], 1)} PF (Rank #{tr['pf_rank']})** | PA: {round(tr['points_against'], 1)} (Rank #{tr['pa_rank']} hardest){po_str} | {tr['status_tag']}\n"
        payload += "\n"

    if playoff_odds:
        payload += f"## 🎲 10,000-SIMULATION PLAYOFF ODDS, BYE RACE & LEVERAGE STAKES (ENTERING WEEK {week_num + 1})\n"
        payload += "*Weave these real Monte Carlo percentages into Act 1 (Standings), Act 2 (what the result did to their odds), and Act 3 (next week's must-win leverage games):*\n\n"
        sorted_po = sorted(playoff_odds.values(), key=lambda x: (x['playoff_pct'], x['top3_payout_pct'], x['pf']), reverse=True)
        for rank, r in enumerate(sorted_po, 1):
            payload += (
                f"- **#{rank} {r['owner']} ({r['record']}, {r['division']}):** "
                f"**{r['playoff_pct']}% Playoff Odds** | {r['div_title_pct']}% Div Title | {r['bye_pct']}% #1 Seed Bye | {r['champ_pct']}% Ring | "
                f"Week {week_num + 1} vs {r['next_opp']}: Win ➡️ `{r['win_po_pct']}%` / Loss ➡️ `{r['loss_po_pct']}%` (**±{r['leverage_swing']}% Leverage Swing**) | "
                f"Loser's Bracket #1 Draft Pick Odds: {r['draft_choice_fav_pct']}%\n"
            )
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

    # Extract per-matchup Live Game Desk swings & map owner quotes
    per_matchup_live, full_live_desk_md = extract_live_desk_by_matchup(season, week_num, matchups)
    quotes_by_owner = {}
    if verified_quotes:
        for q in verified_quotes:
            q_owner = q['header'].rstrip(':').strip()
            quotes_by_owner[q_owner] = q['desc']

    # --- MATCHUPS & BOX SCORES (ACT 2: CORE 75%+ OF THE EPISODE) ---
    payload += f"## 🏈 ACT 2 (CORE 75%+ OF SHOW — 16 TO 22 MINUTES): WEEK {week_num} MATCHUPS, OWNER QUOTES & BOX SCORES\n"
    payload += "*PRODUCER PACING RULE: Spend **2.5 to 3.5 minutes on EVERY SINGLE ONE of these 8 matchups** so the full episode runs **20 to 30 minutes**! Read the embedded Owner Quotes verbatim and break down both sides of every game.*\n\n"
    seen_owners = set()
    history_str = "## 📚 LEAGUE LORE & OWNER CONTEXT (MINIMAL RECAP)\n"
    
    memory_file = Path(__file__).resolve().parent / f"podcast_memory_{season}.json"
    if memory_file.exists():
        with open(memory_file, 'r') as mf:
            podcast_memory = json.load(mf)
    else:
        podcast_memory = {"lore_mentioned": []}

    for idx_m, m in enumerate(matchups, 1):
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

            payload += f"### 🏟️ MATCHUP #{idx_m} OF 8 (Target: ~3 Minutes On-Air): {w['owner']} ({w_rec}) vs {l['owner']} ({l_rec}){div_tag}\n"
            payload += f"**Result:** {w['owner']} ({w['name']}) DEFEATED {l['owner']} ({l['name']})\n"
            payload += f"**Final Score:** {max(m['away_score'], m['home_score'])} to {min(m['away_score'], m['home_score'])} (Margin: {m['margin']})\n"
            payload += f"{build_completed_matchup_h2h_tidbit(w['owner'], l['owner'], h2h_through_week, season, week_num)}\n"

            # Playoff Odds context for both owners right inside the matchup
            w_std = standardize_name(w['owner'])
            if w_std.lower() == 'rej hoxha': w_std = 'rej hoxha'
            l_std = standardize_name(l['owner'])
            if l_std.lower() == 'rej hoxha': l_std = 'rej hoxha'
            w_po = playoff_odds.get(w_std, {})
            l_po = playoff_odds.get(l_std, {})
            if w_po and l_po:
                payload += f"**🎲 Playoff Odds After Result:** {w['owner']} sits at **{w_po['playoff_pct']}% Playoff Odds** ({w_po['div_title_pct']}% Div Title) | {l['owner']} sits at **{l_po['playoff_pct']}% Playoff Odds** ({l_po['div_title_pct']}% Div Title)\n"
            payload += "\n"

            # Inject Live Game Desk Swings for THIS matchup directly here!
            pair_key = tuple(sorted([w['owner'], l['owner']]))
            m_live_bullets = per_matchup_live.get(pair_key, [])
            if m_live_bullets:
                payload += "**⚡ LIVE GAME DESK WIN-PROBABILITY SWINGS IN THIS MATCHUP:**\n"
                for lb in m_live_bullets:
                    payload += f"{lb}\n"
                payload += "\n"

            # Inject Owner Quotes for THIS matchup directly here!
            matchup_quote_lines = []
            for m_own in [w['owner'], l['owner']]:
                if m_own in quotes_by_owner:
                    matchup_quote_lines.append(f"- **💬 {m_own} (in Discord this week):** *{quotes_by_owner[m_own]}*")
            # Also check if any third-party owner specifically mentioned either of these two owners in their quote
            for other_own, q_text in quotes_by_owner.items():
                if other_own not in (w['owner'], l['owner']):
                    w_first = w['owner'].split()[0]
                    l_first = l['owner'].split()[0]
                    if w['owner'] in q_text or l['owner'] in q_text or f" {w_first} " in f" {q_text} " or f" {l_first} " in f" {q_text} ":
                        matchup_quote_lines.append(f"- **💬 League Reaction from {other_own}:** *{q_text}*")

            if matchup_quote_lines:
                payload += "**🎤 DIRECT OWNER QUOTES & TRASH TALK FOR THIS MATCHUP (MANDATORY — READ & REACT ON AIR!):**\n"
                payload += "\n".join(matchup_quote_lines) + "\n\n"
            
            w_perf = perfs[w['id']]
            l_perf = perfs[l['id']]
            
            # Identify verified in-game injuries for both teams
            matchup_injuries = []
            for s in w_perf.get('starters', []) + l_perf.get('starters', []):
                inj = detect_in_game_injury(s, player_context)
                if inj:
                    s_owner = w['owner'] if s in w_perf.get('starters', []) else l['owner']
                    matchup_injuries.append((s_owner, s, inj))

            if matchup_injuries:
                payload += "**🚨 VERIFIED IN-GAME INJURY REPORT (CRITICAL CONTEXT - DO NOT CALL THESE BLUNDERS):**\n"
                for s_owner, s, inj in matchup_injuries:
                    payload += f"- **{s['name']}** ({s_owner}'s starter): Scored {s['pts']} pts (Proj: {s['proj']}). SUFFERED A VERIFIED IN-GAME INJURY! {inj['headline']} — {inj['story'][:180]}...\n"
                    payload += f"  *Directive:* {s_owner} did NOT make a mistake starting him. He was healthy at kickoff and exited with an injury during the game. Sympathize with {s_owner}!\n"
                payload += "\n"
            else:
                payload += "*Note: Zero mid-game injuries occurred in this matchup. Any low-scoring starters were healthy duds!*\n\n"

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
            
            payload += "\n**Roster Decisions, Blunders & Duds:**\n"
            
            # Winner blunder evaluation
            if w_perf.get('blunders') and w_perf['blunders'][0]['diff'] > 15.0:
                b = w_perf['blunders'][0]
                started_inj = detect_in_game_injury(b['started'], player_context)
                if started_inj:
                    payload += f"- WINNER OVERCAME INJURY: {w['owner']} won despite losing {b['started']['name']} early to injury ({started_inj['headline']}). Leaving {b['benched']['name']} on the bench didn't cost them the win.\n"
                else:
                    payload += f"- WINNER'S BLUNDER: {w['owner']} won, but benched {format_player_string(b['benched'], player_context)} to start {format_player_string(b['started'], player_context)}. Laugh at them for getting away with it.\n"
            
            # Loser blunder evaluation
            if l_perf.get('blunders'):
                b = l_perf['blunders'][0]
                started_inj = detect_in_game_injury(b['started'], player_context)
                if started_inj:
                    payload += f"- 🚑 CRUEL INJURY MISFORTUNE (NOT A COACHING BLUNDER): {l['owner']} lost by {m['margin']}, and lost {b['started']['name']} to an in-game injury ({started_inj['headline']}) after scoring only {b['started']['pts']} points! While {b['benched']['name']} scored {b['benched']['pts']} on the bench, starting {b['started']['name']} was 100% the correct call before kickoff. DO NOT roast {l['owner']}—this was heartbreak caused by the injury gods!\n"
                elif b['diff'] >= m['margin']:
                    payload += f"- GAME-LOSING BLUNDER: {l['owner']} lost by {m['margin']}, and it is entirely because they benched {format_player_string(b['benched'], player_context)} to start healthy bust {format_player_string(b['started'], player_context)}! This single bad decision cost them {round(b['diff'], 2)} points and the win. Roast them for this massive mistake.\n"
                    roto_s = fetch_rotowire_details(b['started']['id'])
                    if roto_s.get('spin'): payload += f"  *News on the started bust:* {roto_s['spin']}\n"
                else:
                    payload += f"- BENCH BLUNDER: {l['owner']} lost by {m['margin']} and left points on the bench. Their biggest mistake was benching {format_player_string(b['benched'], player_context)} to start {format_player_string(b['started'], player_context)}, leaving {round(b['diff'], 2)} points on the table.\n"
            else:
                busts = sorted(l_starters, key=lambda x: x['pts'] - x['proj'])
                if busts and (busts[0]['pts'] - busts[0]['proj'] < -3):
                    biggest_bust = busts[0]
                    started_inj = detect_in_game_injury(biggest_bust, player_context)
                    if started_inj:
                        payload += f"- IN-GAME INJURY HEARTBREAK: {l['owner']}'s player {biggest_bust['name']} got injured during the game ({started_inj['headline']}), sinking their chances.\n"
                    else:
                        payload += f"- TEAM UNDERPERFORMED (HEALTHY DUD): {l['owner']} lost by {m['margin']}. Their biggest healthy bust was {format_player_string(biggest_bust, player_context)}. Discuss how this player disappeared.\n"
                        roto_s = fetch_rotowire_details(biggest_bust['id'])
                        if roto_s.get('spin'): payload += f"  *News on the biggest bust:* {roto_s['spin']}\n"
            
            payload += "\n"
            
            for owner in [w['owner'], l['owner']]:
                if owner not in seen_owners:
                    ctx = get_owner_storyline_context(owner)
                    history_str += f"- **{owner}**: {ctx['rings']} Rings. {ctx['tagline']}\n"
                    seen_owners.add(owner)

    # Include THIS WEEK'S trades only (strictly capped at 60-90 seconds)
    if recent_trades or roster_moves:
        payload += "## 🔄 THIS WEEK'S TRANSACTIONS (STRICT 60–90 SECOND CAP — DO NOT DWELL)\n"
        payload += "*STRICT TIME LIMIT: Spend NO MORE THAN 60–90 seconds total on these transactions! Only trades from the last 7 days (since last Tuesday morning) are listed here. Do NOT discuss older trades from prior weeks!*\n\n"
        if recent_trades:
            for tr in recent_trades:
                payload += format_in_depth_trade(tr)
        if roster_moves:
            payload += "### 📋 Minor Free Agent Pickups (10-Second Passing Mention Only):\n"
            for rm in roster_moves[:4]:
                payload += f"- {rm}\n"
            payload += "\n"

    payload += history_str
    
    # Save updated memory
    with open(memory_file, 'w') as mf:
        json.dump(podcast_memory, mf)

    # --- LIVE GAME DESK SWINGS & MNF MIRACLES ---
    payload += full_live_desk_md

    if verified_quotes:
        payload += "\n## 🎤 ALL MANAGER POST-GAME QUOTES & DISCORD CHATTER (READ & ROAST ON AIR!)\n"
        payload += "*Make sure every single one of these real Discord quotes from `#trash-talk` and `#live-game-desk` gets quoted and reacted to during the show:*\n"
        for q in verified_quotes:
            payload += f"- **{q['header']}** said: '{q['desc']}'\n"
            
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
            payload += f"\n## 🔮 NEXT WEEK'S MATCHUPS (WEEK {week_num + 1} PREVIEW & PLAYOFF LEVERAGE)\n"
            payload += "*Preview these key matchups in Act 3 and highlight the massive Win/Loss Playoff Leverage swings at stake:*\n"
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
                    a_std = standardize_name(a_name)
                    if a_std.lower() == 'rej hoxha': a_std = 'rej hoxha'
                    h_std = standardize_name(h_name)
                    if h_std.lower() == 'rej hoxha': h_std = 'rej hoxha'
                    a_po = playoff_odds.get(a_std, {})
                    h_po = playoff_odds.get(h_std, {})
                    lev_note = ""
                    if a_po and h_po:
                        lev_note = f" | *Playoff Odds & Leverage:* {a_name} ({a_po['playoff_pct']}% PO; Win `{a_po['win_po_pct']}%` / Loss `{a_po['loss_po_pct']}%`) vs {h_name} ({h_po['playoff_pct']}% PO; Win `{h_po['win_po_pct']}%` / Loss `{h_po['loss_po_pct']}%`)"
                    h2h_note = build_upcoming_matchup_h2h_tidbit(a_name, h_name, h2h_through_week, season)
                    payload += f"- **{a_name}** ({a_tr.get('overall_w',0)}-{a_tr.get('overall_l',0)}) vs **{h_name}** ({h_tr.get('overall_w',0)}-{h_tr.get('overall_l',0)}){div_str} — {h2h_note}{lev_note}\n"

    out_dir = Path(__file__).resolve().parent / "output"
    out_dir.mkdir(exist_ok=True)

    # Write primary payload file
    out_file = out_dir / f"notebook_payload_week_{week_num}_{season}.md"
    with open(out_file, 'w') as f:
        f.write(payload)

    # Write companion season chronicle file
    chronicle_content = f"# BFL (Beasts Football League) - {season} Season Chronicle (Weeks 1 to {week_num - 1})\n\n"
    chronicle_content += "This document contains the chronological history and results of the earlier weeks in the season.\n"
    chronicle_content += "Upload this file as a secondary reference source in Google NotebookLM so the hosts maintain perfect continuity without repeating themselves.\n\n"
    chronicle_content += build_previous_weeks_chronicle(raw_data, week_num, teams)
    
    chronicle_file = out_dir / f"season_chronicle_weeks_1_to_{week_num - 1}_{season}.md"
    with open(chronicle_file, 'w') as f:
        f.write(chronicle_content)

    # Write ready-to-paste NotebookLM 'Customize' prompt file
    customize_prompt = (
        f"Host a 20-to-30 minute SportsCenter-style Week {week_num} BFL recap as Chris & Dave. "
        f"STRICT PACING: Spend 75%+ of the episode (2.5-3.5 mins per game) breaking down ALL 8 Week {week_num} matchups in detail "
        f"(scores, studs/duds, start/sit blunders, 18-yr H2H, Live Game Desk swings) AND reading/roasting the owners' actual Discord quotes verbatim. "
        f"Spend under 60 seconds total on this week's trades only (ignore older trades). "
        f"NEVER say a player got hurt mid-game unless listed in a Verified In-Game Injury Report."
    )
    customize_file = out_dir / f"notebook_customize_prompt_week_{week_num}_{season}.txt"
    with open(customize_file, 'w') as f:
        f.write(customize_prompt + "\n")

    print(f"🎉 SUCCESS! Payload generated: {out_file}")
    print(f"📚 Companion Chronicle generated: {chronicle_file}")
    print(f"🎛️ NotebookLM Customize Prompt saved: {customize_file}")
    print(f"\n📋 COPY-PASTE THIS INTO NOTEBOOKLM 'CUSTOMIZE' BOX (AND SELECT 'LONGER'):\n{customize_prompt}\n")
    print("👉 Upload ONLY the primary payload file (or both files) to NotebookLM and paste the Customize prompt above!")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--season", type=int, default=int(CURRENT_YEAR))
    parser.add_argument("--week", type=int, default=6)
    args = parser.parse_args()
    generate_payload(args.season, args.week)
