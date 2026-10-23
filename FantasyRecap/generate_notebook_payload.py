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
from FantasyRecap.league_history_intelligence import get_owner_storyline_context, BFL_CHAMPIONSHIPS, standardize_name
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

def generate_payload(season: int, week_num: int):
    print(f"📡 Fetching live ESPN data for Season {season}, Week {week_num}...")
    raw_data = fetch_espn_week_data(ESPN_LEAGUE_ID, str(season), week_num, ESPN_S2, ESPN_SWID)
    teams = parse_league_members_and_teams(raw_data)
    matchups, perfs, _ = analyze_week(raw_data, week_num)
    verified_quotes = get_verified_manager_quotes()
    cookies = {"espn_s2": ESPN_S2, "SWID": ESPN_SWID}

    trajectories = calculate_season_trajectories(raw_data, week_num, teams)
    trades = fetch_league_trades(raw_data, week_num, season, cookies, teams, trajectories)
    playoff_odds = simulate_league_playoff_odds_from_espn(raw_data, week_num, n_sims=5000) if week_num <= 14 else {}

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

    # --- STRICT PACING & BROADCAST TIME ALLOCATION (15% / 70% / 15%) ---
    payload += "### ⏱️ STRICT BROADCAST STRUCTURE & TIME ALLOCATION (PACING GUIDE)\n"
    payload += "To deliver a balanced, entertaining, and comprehensive episode, you MUST adhere to this exact 3-Act structure:\n\n"
    payload += f"1. **ACT 1: THE OPENING DRIVE (FIRST ~15% OF THE EPISODE)**\n"
    payload += f"   - **SportsCenter Desk Welcome:** Welcome listeners to the Week {week_num} edition of the Tuesday Morning Hangover, Chris & Dave introduce themselves, and immediately tease the biggest headlines and storylines of the week.\n"
    payload += "   - **In-Depth Trade Breakdown & Strategic Debate:** Dedicate the bulk of your transaction discussion to recent trades! Walk through each trade in-depth: explain why each manager made the move (e.g. consolidation upgrade, one-week rental, bye-week coverage, injury stash for a playoff run, or depth replenishment), debate who won the deal, and discuss the ripple effects across the league.\n"
    payload += "   - **Minor Free Agent Moves (Strict 10-Second Passing Mention Only):** Spend LESS time on minor waiver wire and free agent moves! Give them at most a 10-second rapid mention or gloss over them. Do NOT dwell on minor bench flyers or cuts.\n"
    payload += "   - **Major NFL Injury News:** Mention any catastrophic NFL injuries impacting starting lineups (e.g. season-ending tears, concussions).\n"
    payload += "   - **Division Standings & Scoring Snapshot:** Brief analytical look at division leaders, win streaks, losing skids, and points scored (treat points scored strictly as factual statistics, NOT as an opening philosophical debate on luck vs skill).\n"
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
    payload += f"1. **MANDATORY SPORTSCENTER DESK INTRO & NO HYPOTHETICAL SHTICK:** Never open the episode asking listeners to 'imagine' a scenario or asking rhetorical questions ('Imagine scoring 120 points and losing, how would you feel?'). Open like professional sports anchors: 'Welcome to the Week {week_num} edition of the Tuesday Morning Hangover, I'm Chris alongside Dave!' Introduce yourselves, tease the week's wild results, and get straight into the action.\n"
    payload += "2. **NO FANTASY EXPLANATIONS:** Never explain what fantasy football is or how head-to-head works. The listeners are the 16 managers. Talk like seasoned sports talk radio hosts.\n"
    payload += "3. **BALANCED COVERAGE (ALL 16 TEAMS):** Touch on every single team and break down all 8 matchups. Do NOT focus solely on the undefeated team or high-profile owners.\n"
    payload += "4. **IN-GAME INJURIES (ABSOLUTE DIRECTIVE):** NFL players get injured during live games! League managers DO NOT intentionally start inactive or injured players! If a starter scored 0 to 4 points and has an in-game injury noted below (e.g., Terry McLaurin patellar tendon tear, Ladd McConkey concussion, Blake Grupe ACL tear), NEVER accuse the manager of 'starting an injured player' or committing a 'blunder'! Explicitly recognize on-air that the player suffered an in-game injury during live play. Sympathize with the manager for brutal injury heartbreak and bad luck.\n"
    
    # Dynamic continuity highlights
    undefeated_teams = [t['owner'] for t in teams.values() if trajectories[t['id']]['overall_l'] == 0]
    winless_teams = [t['owner'] for t in teams.values() if trajectories[t['id']]['overall_w'] == 0]
    continuity_snippets = []
    if undefeated_teams:
        continuity_snippets.append(f"{', '.join(undefeated_teams)} remaining undefeated")
    if winless_teams:
        continuity_snippets.append(f"{', '.join(winless_teams)} still searching for win #1")
    continuity_str = f" This is Episode {week_num}! DO NOT repeat static multi-year owner biographies or ring counts from scratch. Instead, focus on the DYNAMIC {season} SEASON STORY: " + (", ".join(continuity_snippets) if continuity_snippets else "the tight playoff and division races taking shape") + ".\n"
    payload += f"5. **EPISODIC CONTINUITY & ANTI-REPETITION:**{continuity_str}"
    
    payload += "6. **DISCUSS DIVISION DYNAMICS & STREAKS:** Talk about division races! Who leads? Who is tied? Point out who is riding hot winning streaks versus who is in freefall.\n"
    payload += "7. **BLAME THE PLAYER, NOT THE MANAGER:** When a star player underperforms while healthy, roast the player for disappearing, not the manager for starting their studs.\n"
    payload += "8. **TIMELINE REALITY & PLAYER TENURE (DO NOT CALL VETERANS ROOKIES):** The current NFL season is 2026!\n"
    payload += "   - Every player in this document now has their official NFL tenure tag right next to their name (e.g. `[2nd-Year Sophomore (Drafted 2025)]` or `[3rd-Year Player (Drafted 2024)]`).\n"
    payload += "   - **Colston Loveland, Luther Burden III, Tyler Warren, Jeremiyah Love, and Quinshon Judkins** were drafted in 2025 and ARE IN THEIR 2ND SEASON (SOPHOMORES). THEY ARE NOT ROOKIES! DO NOT CALL COLSTON LOVELAND A ROOKIE!\n"
    payload += "   - **Caleb Williams, Jayden Daniels, Bo Nix, Marvin Harrison Jr, Malik Nabers, Rome Odunze, and Brock Bowers** were drafted in 2024 and ARE IN THEIR 3RD SEASON (ESTABLISHED VETERANS).\n"
    payload += "   - ONLY players explicitly labeled `[Rookie (1st Year, Class of 2026)]` are rookies. You are strictly forbidden from calling Colston Loveland or any 2024/2025 draft pick a rookie.\n"
    payload += "9. **CENTRAL TIME ZONE:** The BFL operates in Central Time (CT). Sunday games kick off at 12:00 PM CT (noon).\n"
    
    # Dynamic Luck Directive
    pt_callouts = [f"{teams[tid]['owner']} ({tr['overall_w']}-{tr['overall_l']} with {round(tr['points_for'], 1)} PF, #{tr['pf_rank']} scoring)" for tid, tr in trajectories.items() if tr['overall_w'] > tr['overall_l'] and tr['pf_rank'] >= 10]
    unlucky_callouts = [f"{teams[tid]['owner']} ({tr['overall_w']}-{tr['overall_l']} despite {round(tr['points_for'], 1)} PF, #{tr['pf_rank']} scoring)" for tid, tr in trajectories.items() if tr['overall_w'] <= tr['overall_l'] and tr['pf_rank'] <= 6]
    gauntlet_callouts = [f"{teams[tid]['owner']} ({round(tr['points_against'], 1)} PA, #{tr['pa_rank']} schedule)" for tid, tr in trajectories.items() if tr['pa_rank'] <= 2]
    
    luck_dir_str = " When checking the standings in Act 1, mention teams' Points For alongside their records as factual stats (e.g. noting paper tigers with low scoring or powerhouses who ran into buzzsaws). Treat this strictly as sports statistics, NEVER as an opening philosophical debate on luck vs skill.\n\n"
    payload += f"10. **ANALYTICAL SCORING CHECK (NOT AN OPENING ESSAY):**{luck_dir_str}"
    payload += "11. **NO PENDING WAIVER CLAIMS OR UNEXECUTED MOVES:** Only discuss transactions that have officially EXECUTED and are public league knowledge. Never speculate on or reference pending waiver claims, bids, or unexecuted moves. Only talk about finalized cuts, signings, and trades.\n"
    payload += "12. **IN-DEPTH TRADE ANALYSIS & MINIMAL TIME ON MINOR FREE AGENTS:**\n"
    payload += "   - When discussing trades in Act 1, GO IN-DEPTH! Do not just read off player names. Explain the strategic rationale for BOTH managers:\n"
    payload += "     * Why Team A made the move (e.g. consolidation upgrade, acquiring an elite weekly starter, playoff injury stash).\n"
    payload += "     * Why Team B made the move (e.g. 2-for-1 depth replenishment, one-week rental to cover a bye week, immediate win-now starting points to survive a losing streak).\n"
    payload += "     * Host debate: Have Chris and Dave take opposing sides on who won the trade, grade the trade, and note any league controversy.\n"
    payload += "   - Spend LESS time on minor free agent pickups. Dismiss minor waiver moves in 10 seconds or less—never treat a backup free agent signing with the same weight as a blockbuster trade.\n"
    payload += "13. **OFFICIAL PRONUNCIATION, NICKNAMES & INSIDE JOKES GUIDE (MANDATORY):**\n"
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
        payload += "## 🔥 LEAGUE TRANSACTIONS: TRADES & ROSTER SHIFTS (ACT 1 SPOTLIGHT)\n"
        payload += "*DEDICATE AIRTIME IN ACT 1 TO DEEP DIVE ON TRADES! Explain why each team made the move and debate who won. Keep minor free agent waiver moves to a brief 10-second passing mention.*\n\n"
        
        recent_weeks = [week_num, week_num - 1] if week_num > 1 else [1]
        recent_trades = [tr for tr in trades if tr.get('week') in recent_weeks]
        earlier_trades = [tr for tr in trades if tr.get('week') not in recent_weeks]
        
        if recent_trades:
            payload += f"### 🚨 RECENT TRADES IN THE SPOTLIGHT (GO IN-DEPTH ON AIR):\n"
            payload += "*Hosts MUST walk through each of these trades, explain the tactical motives for both owners (consolidation, rental, stash, depth), and take opposing sides on who won the deal:*\n\n"
            for tr in recent_trades:
                payload += format_in_depth_trade(tr)
        elif trades:
            payload += f"### 🚨 LATEST LEAGUE TRADES (IN-DEPTH ANALYSIS):\n\n"
            for tr in trades[-2:]:
                payload += format_in_depth_trade(tr)
                
        if earlier_trades:
            payload += "### 📜 PREVIOUS SEASON TRADES LEDGER (HISTORICAL CONTEXT):\n"
            payload += "*Reference these past deals naturally if relevant to ongoing storylines:*\n"
            for tr in earlier_trades:
                a_str = ', '.join([f"{p['name']} ({p.get('pos', 'FLEX')})" if isinstance(p, dict) else p for p in tr['a_sends']])
                b_str = ', '.join([f"{p['name']} ({p.get('pos', 'FLEX')})" if isinstance(p, dict) else p for p in tr['b_sends']])
                payload += f"- **Week {tr['week']} Trade:** **{tr['owner_a']}** sent {a_str} to **{tr['owner_b']}** for {b_str}\n"
            payload += "\n"
            
        if roster_moves:
            payload += "### 📋 Notable Free Agent Pickups (Brief 10-Second Mention Only - Do NOT Dwell):\n"
            payload += "*Strict Rule: Spend less than 10 seconds total on these. Mention them as rapid-fire roster maintenance, then move on. Focus the transaction discussion on the trades above!*\n"
            for rm in roster_moves[:4]:
                payload += f"- {rm}\n"
            payload += "\n"

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
                        lev_note = f" — *Playoff Odds & Leverage:* {a_name} ({a_po['playoff_pct']}% PO; Win `{a_po['win_po_pct']}%` / Loss `{a_po['loss_po_pct']}%`) vs {h_name} ({h_po['playoff_pct']}% PO; Win `{h_po['win_po_pct']}%` / Loss `{h_po['loss_po_pct']}%`)"
                    payload += f"- **{a_name}** ({a_tr.get('overall_w',0)}-{a_tr.get('overall_l',0)}) vs **{h_name}** ({h_tr.get('overall_w',0)}-{h_tr.get('overall_l',0)}){div_str}{lev_note}\n"

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

    print(f"🎉 SUCCESS! Payload generated: {out_file}")
    print(f"📚 Companion Chronicle generated: {chronicle_file}")
    print("👉 Upload both files to NotebookLM to generate the audio overview with perfect continuity!")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--season", type=int, default=int(CURRENT_YEAR))
    parser.add_argument("--week", type=int, default=6)
    args = parser.parse_args()
    generate_payload(args.season, args.week)
