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

import os
import sys
from datetime import datetime
from pathlib import Path
from FantasyRecap.config import CURRENT_YEAR

# Complete Official BFL Super Bowl Honor Roll (2008-2025)
BFL_CHAMPIONSHIPS = [
    {'year': 2008, 'champ': 'Saagar Gupta', 'runner_up': 'Alex Shimomura', 'score': '98.00 - 71.00', 'margin': 27.00},
    {'year': 2009, 'champ': 'Shawn Lukose', 'runner_up': 'Maria Christus', 'score': '97.00 - 94.00', 'margin': 3.00},
    {'year': 2010, 'champ': 'Daniel Kruszewski', 'runner_up': 'Shawn Lukose', 'score': '90.00 - 79.00', 'margin': 11.00},
    {'year': 2011, 'champ': 'Abe Thomas', 'runner_up': 'Blake Whitehouse', 'score': '108.00 - 52.00', 'margin': 56.00},
    {'year': 2012, 'champ': 'Nick Christus', 'runner_up': 'Shawn Lukose', 'score': '86.00 - 82.00', 'margin': 4.00},
    {'year': 2013, 'champ': 'Shawn Ullenbrauck', 'runner_up': 'Tommy Ehrlich', 'score': '81.00 - 77.00', 'margin': 4.00},
    {'year': 2014, 'champ': 'Dino Davros', 'runner_up': 'Abe Thomas', 'score': '141.00 - 86.00', 'margin': 55.00},
    {'year': 2015, 'champ': 'Nick Christus', 'runner_up': 'Tommy Ehrlich', 'score': '66.00 - 48.00', 'margin': 18.00},
    {'year': 2016, 'champ': 'Nick Christus', 'runner_up': 'Blake Whitehouse', 'score': '99.00 - 95.00', 'margin': 4.00},
    {'year': 2017, 'champ': 'Adam Olen', 'runner_up': 'Shawn Lukose', 'score': '77.00 - 76.00', 'margin': 1.00},
    {'year': 2018, 'champ': 'Shawn Lukose', 'runner_up': 'Samran Mirza', 'score': '114.00 - 88.00', 'margin': 26.00},
    {'year': 2019, 'champ': 'Mattheos Houlis', 'runner_up': 'Ryan Olen', 'score': '96.16 - 87.24', 'margin': 8.92},
    {'year': 2020, 'champ': 'Dino Davros', 'runner_up': 'rej hoxha', 'score': '92.28 - 92.20', 'margin': 0.08},
    {'year': 2021, 'champ': 'Shawn Lukose', 'runner_up': 'rej hoxha', 'score': '133.38 - 80.60', 'margin': 52.78},
    {'year': 2022, 'champ': 'Abe Thomas', 'runner_up': 'Daniel Kruszewski', 'score': '108.66 - 52.58', 'margin': 56.08},
    {'year': 2023, 'champ': 'Shawn Lukose', 'runner_up': 'Samran Mirza', 'score': '102.56 - 82.72', 'margin': 19.84},
    {'year': 2024, 'champ': 'Sydney Miller', 'runner_up': 'Ryan Olen', 'score': '119.88 - 100.88', 'margin': 19.00},
    {'year': 2025, 'champ': 'Abe Thomas', 'runner_up': 'Daniel Kruszewski', 'score': '107.08 - 101.16', 'margin': 5.92},
]

# Owner Trophy Profiles
OWNER_PROFILES = {
    'Shawn Lukose': {
        'rings': 4,
        'champ_years': [2009, 2018, 2021, 2023],
        'runner_up_years': [2010, 2012, 2017],
        'tagline': "The 4-Time GOAT",
        'summary': "All-time BFL championship leader (4 Rings, 7 Super Bowl appearances)."
    },
    'Nick Christus': {
        'rings': 3,
        'champ_years': [2012, 2015, 2016],
        'runner_up_years': [],
        'tagline': "3-Time Champion (10-Year Drought)",
        'summary': "Dominant back-to-back champ (2015-2016), currently fighting a 10-year title drought. The league Commissioner and husband to Sydney Miller."
    },
    'Abe Thomas': {
        'rings': 3,
        'champ_years': [2011, 2022, 2025],
        'runner_up_years': [2014],
        'tagline': "Defending Champion (3 Rings)",
        'summary': "Won the 2025 Super Bowl. Tied for 2nd all-time with 3 Jabroni Trophies."
    },
    'Dino Davros': {
        'rings': 2,
        'champ_years': [2014, 2020],
        'runner_up_years': [],
        'tagline': "2-Time Champion",
        'summary': "2-time champion (2014, 2020), famous for the 0.08-point miracle win in the 2020 Super Bowl."
    },
    'Daniel Kruszewski': {
        'rings': 1,
        'champ_years': [2010],
        'runner_up_years': [2022, 2025],
        'tagline': "2010 Champ (Heartbreak Era)",
        'summary': "Won in 2010, but suffered brutal Super Bowl losses to Abe in 2022 and 2025."
    },
    'Shawn Ullenbrauck': {
        'rings': 1,
        'champ_years': [2013],
        'runner_up_years': [],
        'tagline': "2013 Jabroni Champion",
        'summary': "Won the 2013 Super Bowl in a tight 4-point clash."
    },
    'Adam Olen': {
        'rings': 1,
        'champ_years': [2017],
        'runner_up_years': [],
        'tagline': "2017 Jabroni Champion",
        'summary': "Pulled off the narrow 1-point Super Bowl victory in 2017 to hoist his lone Jabroni Trophy."
    },
    'Saagar Gupta': {
        'rings': 1,
        'champ_years': [2008],
        'runner_up_years': [],
        'tagline': "Inaugural Champion (18-Year Jabroni Drought)",
        'summary': "Won the first-ever Jabroni Trophy in 2008; currently enduring the league's longest active title drought (18 years)."
    },
    'Sydney Miller': {
        'rings': 1,
        'champ_years': [2024],
        'runner_up_years': [],
        'tagline': "2024 Jabroni Champion",
        'summary': "Hoisted the 2024 Jabroni Trophy. She is the only female owner in the league, and is married to the Commissioner, Nick Christus."
    },
    'Tommy Ehrlich': {
        'rings': 0,
        'champ_years': [],
        'runner_up_years': [2013, 2015],
        'tagline': "2x Finalist Chasing Jabroni #1",
        'summary': "Two Super Bowl runner-up finishes (2013, 2015); perpetually hunting for his first Jabroni Trophy."
    },
    'Blake Whitehouse': {
        'rings': 0,
        'champ_years': [],
        'runner_up_years': [2011, 2016],
        'tagline': "2x Finalist Chasing Jabroni #1",
        'summary': "Two Super Bowl appearances (2011, 2016); searching for that elusive first Jabroni Trophy."
    },
    'Samran Mirza': {
        'rings': 0,
        'champ_years': [],
        'runner_up_years': [2018, 2023],
        'tagline': "2x Finalist Chasing Jabroni #1",
        'summary': "Twice reached the mountaintop only to fall to Lukose; chasing his first career Jabroni."
    },
    'rej hoxha': {
        'rings': 0,
        'champ_years': [],
        'runner_up_years': [2020, 2021],
        'tagline': "2x Finalist Chasing Jabroni #1",
        'summary': "Back-to-back finalist (2020, 2021) including the heartbreaking 0.08-pt Super Bowl loss to Dino in the 2020 Jabroni clash."
    },
    'Nael Ahmed': {
        'rings': 0,
        'champ_years': [],
        'runner_up_years': [],
        'tagline': "Inaugural Solo Franchise",
        'summary': "Split from the 'Big Nasties' franchise this year to launch his first solo campaign. Looking to prove he was the brains of the operation."
    },
    'Alex Kite': {
        'rings': 0,
        'champ_years': [],
        'runner_up_years': [],
        'tagline': "West Division Contender",
        'summary': "High-variance powerhouse seeking its first franchise Super Bowl appearance."
    },
    'Nitesh Patel': {
        'rings': 0,
        'champ_years': [],
        'runner_up_years': [],
        'tagline': "Co-Owner of Big Nasties",
        'summary': "Co-owns the Big Nasties franchise with Edgar. Retooling the squad to prove they don't need Nael to win a Jabroni Trophy."
    }
}

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

# Shared League Constants, Synonyms & Historical H2H Engine (2008-2026)
import csv
import json
import requests

MANAGER_SYNONYMS = {
    'thomas ehrlich': 'Tommy Ehrlich',
    'tom ehrlich': 'Tommy Ehrlich',
    'tommy ehrlich': 'Tommy Ehrlich',
    'dan kruszewski': 'Daniel Kruszewski',
    'daniel kruszewski': 'Daniel Kruszewski',
    'ali bhujwala': 'Daniel Kruszewski',
    'sydney kite': 'Sydney Miller',
    'sydney miller': 'Sydney Miller',
    'sydney christus': 'Sydney Miller',
    'emelie lovasko': 'Emelie Lovasko',
    'shawn ullenbrauck': 'Shawn Ullenbrauck',
    'shawn lukose': 'Shawn Lukose',
    'nick christus': 'Nick Christus',
    'blake whitehouse': 'Blake Whitehouse',
    'nael ahmed': 'Nael Ahmed',
    'saagar gupta': 'Saagar Gupta',
    'ayush gupta': 'Saagar Gupta',
    'abe thomas': 'Abe Thomas',
    'nitesh patel': 'Nitesh Patel',
    'edgar': 'Nitesh Patel',
    'rej hoxha': 'rej hoxha',
    'samran mirza': 'Samran Mirza',
    'gabriel zbaala': 'Gabriel Zabala',
    'dino davros': 'Dino Davros',
    'adam olen': 'Adam Olen',
    'ryan olen': 'Ryan Olen',
    'alex kite': 'Alex Kite',
    'evan hagedorn': 'Alex Kite',
    'matt rosato': 'Austin Russell',
    'bubba franks': 'Austin Russell',
    'austin russell': 'Austin Russell',
    'alexandra christus': 'Alex Christus',
    'georgia batman': 'Georgia Christus',
    'georgia christus': 'Georgia Christus',
}

TEAM_DETAILS_2026 = {
    'DTM':     {'owner': 'Daniel Kruszewski', 'team_name': 'Dynasty Destroyers', 'division': 'North'},
    'Thomas':  {'owner': 'Tommy Ehrlich',     'team_name': 'The Ehrly Birds', 'division': 'North'},
    'Nick':    {'owner': 'Nick Christus',     'team_name': 'Mykonos Minotaurs', 'division': 'North'},
    'Blake':   {'owner': 'Blake Whitehouse',  'team_name': 'Block O meets O Block', 'division': 'North'},
    'Nael':    {'owner': 'Nael Ahmed',        'team_name': 'NMAfia', 'division': 'South'},
    'Saagar':  {'owner': 'Saagar Gupta',      'team_name': "King Gupta's Army", 'division': 'South'},
    'Abe':     {'owner': 'Abe Thomas',        'team_name': 'Crashee Bandicoot', 'division': 'South'},
    'Nasties': {'owner': 'Nitesh Patel',      'team_name': 'Big Nasties', 'division': 'South'},
    'Lukose':  {'owner': 'Shawn Lukose',      'team_name': 'Nilgiri Tahrs', 'division': 'East'},
    'Rej':     {'owner': 'rej hoxha',         'team_name': 'Steve Bartman', 'division': 'East'},
    'Samran':  {'owner': 'Samran Mirza',      'team_name': "De'von Intervention", 'division': 'East'},
    'Dino':    {'owner': 'Dino Davros',       'team_name': 'Taliban Gang Mujahideen', 'division': 'East'},
    'AMO':     {'owner': 'Adam Olen',         'team_name': 'Green and Golden', 'division': 'West'},
    'Shooter': {'owner': 'Alex Kite',         'team_name': 'Send Da Trade', 'division': 'West'},
    'Sydney':  {'owner': 'Sydney Miller',     'team_name': "30p Chance I'm Already Winning", 'division': 'West'},
    'Thor':    {'owner': 'Shawn Ullenbrauck', 'team_name': "Pat N' Pending", 'division': 'West'},
}

OWNER_TO_CODE_2026 = {v['owner']: k for k, v in TEAM_DETAILS_2026.items()}

def standardize_name(name: str) -> str:
    if not name:
        return 'Unknown'
    clean = name.lower().strip()
    if clean in MANAGER_SYNONYMS:
        return MANAGER_SYNONYMS[clean]
    if clean == 'rej hoxha':
        return 'rej hoxha'
    return name.strip().title()

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

def compile_all_time_h2h(league_id: str, s2: str, swid: str, current_season: int = 2026, up_to_week: int = 1):
    """
    Fetches all historical seasons (2008 to current) via ESPN leagueHistory and season APIs.
    Uses disk caching for completed past seasons (2008 to current_season-1) for fast execution.
    """
    cookies = {"espn_s2": s2, "SWID": swid} if (s2 and swid) else {}
    h2h = {}
    career_stats = {}
    
    # Historical seasons (2008..2025) used legacy league_id 157057 if league_id was updated
    hist_league_ids = list(dict.fromkeys([str(league_id), "157057"]))
    
    for y in range(2008, current_season):
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
                
    if up_to_week > 1:
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
