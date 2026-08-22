#!/usr/bin/env python3
"""
BFL Post-Game Manager Interview & Press Conference Engine
=========================================================
Generates customized, dramatic post-game press conference questions for all 16
managers after Monday Night Football, and collects their quotes/voice notes
to feed directly into the Weekly 10-Minute AI Podcast & Video Review Show.
"""

import os
import sys
import json
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from FantasyRecap.league_history_intelligence import get_owner_storyline_context

def generate_postgame_interview_prompts(week_num: int, games: list) -> dict:
    """
    Analyzes game outcomes, margins, bench blunders, and historical rivalries
    to generate sharp, authentic post-game press conference questions for both managers.
    """
    interviews = {}
    
    for g in games:
        w_owner = g['winner']
        l_owner = g['loser']
        w_team = g.get('winner_team', w_owner)
        l_team = g.get('loser_team', l_owner)
        w_score = max(g['away_score'], g['home_score'])
        l_score = min(g['away_score'], g['home_score'])
        margin = round(abs(w_score - l_score), 2)
        bench_blunder = g.get('loser_blunder', None)
        swing_info = g.get('swing_info', None)
        
        # Winner Question
        if margin <= 4.0:
            w_q = f"🎙️ Commish Press Room: You escaped with a heart-pounding {margin}-point victory over {l_owner}! When the clock hit zero on Monday night, what was the emotion in the war room?"
        elif margin >= 30.0:
            w_q = f"🎙️ Commish Press Room: Total demolition! You put up {w_score:.2f} points and blew out {l_owner} by {margin:.2f}. Is your squad officially the team to beat in the division?"
        else:
            w_q = f"🎙️ Commish Press Room: A solid {margin}-point statement win over {l_owner} ({w_score:.2f} - {l_score:.2f}). What was the key tactical adjustment that secured this victory?"
            
        # Loser Question
        if margin <= 4.0:
            blunder_p = bench_blunder.get('benched', {}).get('name') if isinstance(bench_blunder, dict) else None
            blunder_diff = bench_blunder.get('diff', 0.0) if isinstance(bench_blunder, dict) else 0.0
            blunder_text = f" Leaving {blunder_diff:.1f} pts on the bench with {blunder_p} proved fatal." if blunder_p else ""
            l_q = f"🎙️ Commish Press Room: A heartbreaking {margin}-point loss to {w_owner}.{blunder_text} What went wrong in the final minutes, and how do you rally the locker room for next week?"
        elif margin >= 30.0:
            l_q = f"🎙️ Commish Press Room: Complete disaster week—blown out by {margin:.2f} points. Are you putting starters on the trade block or calling an emergency team meeting?"
        else:
            l_q = f"🎙️ Commish Press Room: Tough {margin}-point defeat against {w_owner}. What was the biggest disappointment in your lineup this week?"
            
        interviews[w_owner] = {
            'team': w_team,
            'result': 'WIN',
            'score': w_score,
            'opp_score': l_score,
            'margin': margin,
            'question': w_q,
            'response': None  # Populated via manager DM / text
        }
        
        interviews[l_owner] = {
            'team': l_team,
            'result': 'LOSS',
            'score': l_score,
            'opp_score': w_score,
            'margin': margin,
            'question': l_q,
            'response': None
        }
        
    return interviews

def save_interview_file(week_num: int, interviews_data: dict):
    """Saves interview prompts to JSON."""
    out_dir = Path(__file__).resolve().parent
    out_file = out_dir / f"interviews_week_{week_num}.json"
    with open(out_file, 'w') as f:
        json.dump(interviews_data, f, indent=2)
    print(f"💾 Post-Game Interview Prompts saved to: {out_file.name}")
    return out_file

if __name__ == "__main__":
    print("="*75)
    print("🎙️ BFL POST-GAME PRESS CONFERENCE ENGINE TEST")
    print("="*75)
    
    sample_games = [
        {
            'winner': 'Nick Christus', 'loser': 'Tommy Ehrlich',
            'winner_team': 'Mykonos Minotaurs', 'loser_team': 'The Ehrly Birds',
            'away_score': 100.88, 'home_score': 99.20,
            'loser_blunder': {'player': 'James Cook', 'pts': 19.5}
        },
        {
            'winner': 'Abe Thomas', 'loser': 'Saagar Gupta',
            'winner_team': 'Crashee Bandicoot', 'loser_team': "King Gupta's Army",
            'away_score': 125.40, 'home_score': 84.10
        }
    ]
    
    prompts = generate_postgame_interview_prompts(1, sample_games)
    for owner, data in prompts.items():
        print(f"\n👤 [{owner} - {data['result']}]:")
        print(f"  {data['question']}")
