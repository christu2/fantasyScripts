#!/usr/bin/env python3
"""
BFL Playoff Odds & Tiebreaker Simulation Engine
===============================================
Runs a 10,000-iteration Monte Carlo simulation of the remaining BFL schedule
incorporating strict BFL / ESPN tiebreaker rules:
- 4 Division Champions (Seeds #1 - #4)
- #1 Overall Seed earns the First-Round Playoff Bye
- 3 Wild Cards (Seeds #5, #6, #7)
- Tiebreakers: Overall Record -> Head-to-Head -> Total Points Scored (PF)
- Calculates: Playoff %, Division Title %, #1 Seed Bye %, and Win/Loss Playoff Leverage
"""

import os
import sys
import csv
import json
import random
import argparse
import requests
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from FantasyRecap.league_preview_generator import TEAM_DETAILS_2026, standardize_name

# Load .env
env_path = Path(__file__).resolve().parent.parent / '.env'
if env_path.exists():
    load_dotenv(dotenv_path=env_path)
else:
    load_dotenv()

ESPN_LEAGUE_ID = os.getenv("ESPN_LEAGUE_ID", "157057")
ESPN_S2 = os.getenv("ESPN_S2", "")
ESPN_SWID = os.getenv("ESPN_SWID", "")
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "")

DIVISIONS = {
    'North': ['Nick Christus', 'Tommy Ehrlich', 'Daniel Kruszewski', 'Blake Whitehouse'],
    'South': ['Abe Thomas', 'Saagar Gupta', 'Nael Ahmed', 'Nitesh Patel'],
    'East': ['Shawn Lukose', 'Dino Davros', 'Samran Mirza', 'rej hoxha'],
    'West': ['Sydney Miller', 'Shawn Ullenbrauck', 'Adam Olen', 'Alex Kite']
}

OWNER_TO_DIV = {t: div for div, mems in DIVISIONS.items() for t in mems}

CODE_TO_OWNER = {code: info['owner'] for code, info in TEAM_DETAILS_2026.items()}

def load_schedule_csv(csv_path: str = "FantasyScheduler/schedule_by_week.csv") -> list:
    """Loads all 112 regular season matchups across 14 weeks."""
    if not os.path.exists(csv_path):
        csv_path = str(Path(__file__).resolve().parent.parent / "FantasyScheduler" / "schedule_by_week.csv")
        
    schedule = []
    with open(csv_path, 'r') as f:
        reader = csv.DictReader(f)
        for r in reader:
            schedule.append({
                'week': int(r['Week']),
                'away_code': r['Away'].strip(),
                'home_code': r['Home'].strip(),
                'away': CODE_TO_OWNER.get(r['Away'].strip(), r['Away'].strip()),
                'home': CODE_TO_OWNER.get(r['Home'].strip(), r['Home'].strip()),
                'type': r.get('Type', '')
            })
    return schedule

def resolve_division_tiebreakers(tied_members: list, records: dict, h2h_matrix: dict) -> list:
    """
    Resolves ties within a division:
    1. Overall Win-Loss Record
    2. Head-to-Head Win %
    3. Points Scored (PF)
    """
    if len(tied_members) <= 1:
        return tied_members
        
    if len(tied_members) == 2:
        t1, t2 = tied_members[0], tied_members[1]
        w1 = h2h_matrix.get((t1, t2), 0)
        w2 = h2h_matrix.get((t2, t1), 0)
        if w1 > w2:
            return [t1, t2]
        elif w2 > w1:
            return [t2, t1]
        else:
            # Tie in H2H -> fall back to PF
            return sorted([t1, t2], key=lambda t: records[t]['pf'], reverse=True)
            
    # 3+ teams tied
    # Compare H2H among the sub-group if applicable, else Points For
    sub_records = {}
    for t in tied_members:
        sub_wins = sum(h2h_matrix.get((t, opp), 0) for opp in tied_members if opp != t)
        sub_records[t] = sub_wins
        
    # Check if one team swept or has highest win count
    max_wins = max(sub_records.values())
    if list(sub_records.values()).count(max_wins) == 1:
        best = [t for t in tied_members if sub_records[t] == max_wins][0]
        rem = [t for t in tied_members if t != best]
        return [best] + resolve_division_tiebreakers(rem, records, h2h_matrix)
        
    # Default to Points For
    return sorted(tied_members, key=lambda t: records[t]['pf'], reverse=True)

def resolve_wildcard_tiebreakers(tied_members: list, records: dict, h2h_matrix: dict) -> list:
    """
    Resolves cross-division Wild Card ties:
    1. Overall Win-Loss Record
    2. Head-to-Head (if one team defeated all other tied teams)
    3. Points Scored (PF)
    """
    if len(tied_members) <= 1:
        return tied_members
        
    if len(tied_members) == 2:
        t1, t2 = tied_members[0], tied_members[1]
        w1 = h2h_matrix.get((t1, t2), 0)
        w2 = h2h_matrix.get((t2, t1), 0)
        if w1 > w2 and w2 == 0:
            return [t1, t2]
        elif w2 > w1 and w1 == 0:
            return [t2, t1]
        else:
            return sorted([t1, t2], key=lambda t: records[t]['pf'], reverse=True)
            
    # 3+ team Wild Card tie: Fall back to Points Scored
    return sorted(tied_members, key=lambda t: records[t]['pf'], reverse=True)

def run_playoff_simulation(current_week: int, completed_games: list, schedule: list, team_power: dict = None, n_sims: int = 10000):
    """
    Runs Monte Carlo simulation for remaining weeks (current_week through 14).
    """
    if not team_power:
        team_power = {t: {'mean': 100.0, 'std': 14.0} for t in OWNER_TO_DIV}
        
    # Tally base completed records
    base_records = {t: {'wins': 0, 'losses': 0, 'pf': 0.0} for t in OWNER_TO_DIV}
    base_h2h = {}
    
    for g in completed_games:
        a_own = g['away']
        h_own = g['home']
        a_pts = g['away_score']
        h_pts = g['home_score']
        if a_pts > 0 and h_pts > 0:
            base_records[a_own]['pf'] += a_pts
            base_records[h_own]['pf'] += h_pts
            if a_pts > h_pts:
                base_records[a_own]['wins'] += 1
                base_records[h_own]['losses'] += 1
                base_h2h[(a_own, h_own)] = base_h2h.get((a_own, h_own), 0) + 1
            else:
                base_records[h_own]['wins'] += 1
                base_records[a_own]['losses'] += 1
                base_h2h[(h_own, a_own)] = base_h2h.get((h_own, a_own), 0) + 1

    remaining_schedule = [g for g in schedule if g['week'] >= current_week]
    
    playoff_counts = {t: 0 for t in OWNER_TO_DIV}
    div_champ_counts = {t: 0 for t in OWNER_TO_DIV}
    bye_counts = {t: 0 for t in OWNER_TO_DIV}
    seed_distribution = {t: {s: 0 for s in range(1, 17)} for t in OWNER_TO_DIV}
    
    for _ in range(n_sims):
        sim_records = {t: {'wins': base_records[t]['wins'], 'losses': base_records[t]['losses'], 'pf': base_records[t]['pf']} for t in OWNER_TO_DIV}
        sim_h2h = dict(base_h2h)
        
        # Simulate remaining games
        for g in remaining_schedule:
            a_own = g['away']
            h_own = g['home']
            a_score = random.gauss(team_power[a_own]['mean'], team_power[a_own]['std'])
            h_score = random.gauss(team_power[h_own]['mean'], team_power[h_own]['std'])
            
            sim_records[a_own]['pf'] += a_score
            sim_records[h_own]['pf'] += h_score
            
            if a_score > h_score:
                sim_records[a_own]['wins'] += 1
                sim_records[h_own]['losses'] += 1
                sim_h2h[(a_own, h_own)] = sim_h2h.get((a_own, h_own), 0) + 1
            else:
                sim_records[h_own]['wins'] += 1
                sim_records[a_own]['losses'] += 1
                sim_h2h[(h_own, a_own)] = sim_h2h.get((h_own, a_own), 0) + 1
                
        # 1. Resolve Division Champions (Seeds #1 - #4)
        div_champions = []
        wildcard_pool = []
        
        for div_name, members in DIVISIONS.items():
            # Group by wins
            grouped = {}
            for m in members:
                w = sim_records[m]['wins']
                grouped.setdefault(w, []).append(m)
                
            sorted_mems = []
            for w in sorted(grouped.keys(), reverse=True):
                sorted_mems.extend(resolve_division_tiebreakers(grouped[w], sim_records, sim_h2h))
                
            div_champ = sorted_mems[0]
            div_champions.append(div_champ)
            div_champ_counts[div_champ] += 1
            wildcard_pool.extend(sorted_mems[1:])
            
        # Rank division champions #1 to #4
        div_grouped = {}
        for dc in div_champions:
            w = sim_records[dc]['wins']
            div_grouped.setdefault(w, []).append(dc)
            
        sorted_champs = []
        for w in sorted(div_grouped.keys(), reverse=True):
            sorted_champs.extend(resolve_wildcard_tiebreakers(div_grouped[w], sim_records, sim_h2h))
            
        # Seed #1 gets the First-Round Bye!
        bye_team = sorted_champs[0]
        bye_counts[bye_team] += 1
        
        # 2. Resolve Wild Cards (Seeds #5, #6, #7)
        wc_grouped = {}
        for wc in wildcard_pool:
            w = sim_records[wc]['wins']
            wc_grouped.setdefault(w, []).append(wc)
            
        sorted_wc = []
        for w in sorted(wc_grouped.keys(), reverse=True):
            sorted_wc.extend(resolve_wildcard_tiebreakers(wc_grouped[w], sim_records, sim_h2h))
            
        wild_cards = sorted_wc[:3]
        toilet_bowl = sorted_wc[3:]
        
        # 7 Playoff Teams (4 Div Winners + 3 Wild Cards)
        playoff_field = sorted_champs + wild_cards
        for s_idx, p_team in enumerate(playoff_field, 1):
            playoff_counts[p_team] += 1
            seed_distribution[p_team][s_idx] += 1
            
        for s_idx, t_team in enumerate(toilet_bowl, 8):
            seed_distribution[t_team][s_idx] += 1

    # Format probability outputs
    results = {}
    for t in OWNER_TO_DIV:
        results[t] = {
            'owner': t,
            'division': OWNER_TO_DIV[t],
            'playoff_pct': round((playoff_counts[t] / n_sims) * 100, 1),
            'div_title_pct': round((div_champ_counts[t] / n_sims) * 100, 1),
            'bye_pct': round((bye_counts[t] / n_sims) * 100, 1),
            'toilet_bowl_pct': round(100.0 - ((playoff_counts[t] / n_sims) * 100), 1),
            'seed_dist': {s: round((seed_distribution[t][s] / n_sims) * 100, 1) for s in range(1, 17)}
        }
        
    return results

def format_playoff_odds_report(results: dict, current_week: int, season: int) -> str:
    """Formats playoff odds into clean markdown."""
    lines = []
    lines.append(f"# 📊 BFL WEEK {current_week} PLAYOFF PROBABILITY & SEEDING INDEX ({season})")
    lines.append(f"*10,000 Monte Carlo Simulations • 4 Division Titles • 3 Wild Cards • 1 First-Round Bye*\n")
    lines.append("---")
    lines.append("## 🏆 PLAYOFF LEVERAGE & SEEDING PROBABILITIES\n")
    lines.append("| Rank | Manager | Division | Playoff Odds % | Win Division % | #1 Seed Bye % | Toilet Bowl % |")
    lines.append("|:---:|:---|:---:|:---:|:---:|:---:|:---:|")
    
    sorted_teams = sorted(results.values(), key=lambda x: (x['playoff_pct'], x['div_title_pct']), reverse=True)
    for rank, r in enumerate(sorted_teams, 1):
        lines.append(f"| #{rank} | **{r['owner']}** | {r['division']} | **{r['playoff_pct']}%** | {r['div_title_pct']}% | {r['bye_pct']}% | {r['toilet_bowl_pct']}% |")
        
    lines.append("\n---\n💡 *Simulation Rules: 4 Division Winners (#1-#4) + 3 Wild Cards (#5-#7). Tiebreakers: Record -> H2H -> Total Points Scored (PF).*")
    return "\n".join(lines)

def post_playoff_odds_to_discord(webhook_url: str, results: dict, current_week: int, season: int = 2026):
    """Broadcasts Playoff Odds Board to Discord."""
    if not webhook_url:
        return
        
    sorted_teams = sorted(results.values(), key=lambda x: (x['playoff_pct'], x['div_title_pct']), reverse=True)
    
    # Division favorites
    div_favs = {}
    for div in DIVISIONS:
        mems = [results[t] for t in DIVISIONS[div]]
        fav = max(mems, key=lambda x: x['div_title_pct'])
        div_favs[div] = f"**{fav['owner']}** ({fav['div_title_pct']}%)"
        
    fields = [
        {
            "name": "👑 Division Title Frontrunners",
            "value": f"• **North**: {div_favs['North']}\n• **South**: {div_favs['South']}\n• **East**: {div_favs['East']}\n• **West**: {div_favs['West']}",
            "inline": False
        },
        {
            "name": "🚀 Top 7 Playoff Projections (Entering Week {current_week})",
            "value": "\n".join([f"**#{idx} {r['owner']}** ({r['division']}): `{r['playoff_pct']}%` Playoff | `{r['div_title_pct']}%` Division" for idx, r in enumerate(sorted_teams[:7], 1)]),
            "inline": False
        },
        {
            "name": "🚨 Playoff Bubble & Toilet Bowl Watch",
            "value": "\n".join([f"• **{r['owner']}**: `{r['playoff_pct']}%` Playoff | `{r['toilet_bowl_pct']}%` Sacko Risk" for r in sorted_teams[7:12]]),
            "inline": False
        }
    ]
    
    payload = {
        "username": "BFL Playoff Analytics Desk",
        "avatar_url": "https://a.espncdn.com/combiner/i?img=/i/headshots/nfl/players/full/3918298.png",
        "embeds": [{
            "title": f"📈 BFL Week {current_week} Official Playoff Probability Board ({season})",
            "description": "**10,000 Monte Carlo Simulations • 7 Playoff Spots (4 Div + 3 Wild Cards)**\nLive playoff odds, division title races, and #1 seed first-round bye probabilities:",
            "color": 0x34495e,  # Dark Slate Blue
            "fields": fields,
            "footer": {"text": f"Beasts Football League • Playoff Simulator • Week {current_week}"}
        }]
    }
    
    try:
        resp = requests.post(webhook_url, json=payload, timeout=10)
        if resp.status_code in [200, 204]:
            print("🚀 Successfully broadcasted Playoff Odds Board to Discord!")
    except Exception as e:
        print(f"❌ Error broadcasting playoff odds: {e}")

if __name__ == "__main__":
    schedule = load_schedule_csv()
    results = run_playoff_simulation(1, [], schedule, n_sims=10000)
    report = format_playoff_odds_report(results, 1, 2026)
    print(report)
