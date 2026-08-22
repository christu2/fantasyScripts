#!/usr/bin/env python3
"""
BFL Live Win Probability & Shockwave Alert Engine
=================================================
Calculates real-time fantasy matchup win probabilities using pre-game baseline
projections with elapsed game-clock decay and positional variance modeling.

Key Features:
- Baseline Pre-Game Projection Tracking (statSourceId: 1 at kickoff)
- Positional Variance Modeling (QB: 6.5, RB: 6.0, WR: 6.2, TE: 4.5, K/DST: 4.0)
- Minute-Decay Win Probability Formula (Gaussian CDF)
- Swing Detection: Flags win probability shockwaves (>= 25% swing)
- Photo Finish & Lead Change Alerts
- Discord Webhook In-Game Alert Cards
"""

import os
import sys
import math
import json
import time
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

# Positional standard deviations for fantasy scoring variance (per 60 mins)
POSITION_STD_DEV = {
    1: 6.5,   # QB
    2: 6.0,   # RB
    3: 6.2,   # WR
    4: 4.5,   # TE
    5: 3.8,   # K
    16: 4.2   # D/ST
}

def normal_cdf(x: float) -> float:
    """Standard normal cumulative distribution function."""
    return (1.0 + math.erf(x / math.sqrt(2.0))) / 2.0

def calculate_matchup_win_probability(away_starters: list, home_starters: list, away_current_pts: float, home_current_pts: float) -> tuple:
    """
    Calculates exact win probability using pre-game baseline projections,
    current points scored, and remaining player variance.
    
    Returns: (away_win_prob, home_win_prob, away_live_exp, home_live_exp)
    """
    away_rem_exp = 0.0
    away_rem_var = 0.0
    for p in away_starters:
        base_proj = p.get('baseline_proj', 0.0)
        curr_score = p.get('current_score', 0.0)
        status = p.get('game_status', 'PRE') # PRE, IN_PROGRESS, FINAL
        pct_rem = p.get('pct_remaining', 1.0) # 1.0 = pre-game, 0.5 = halftime, 0.0 = final
        pos_id = p.get('pos_id', 3)
        std_dev = POSITION_STD_DEV.get(pos_id, 5.5)
        
        if status == 'FINAL' or pct_rem <= 0.0:
            pass # No remaining projection or variance
        elif status == 'IN_PROGRESS':
            away_rem_exp += (base_proj * pct_rem)
            away_rem_var += ((std_dev ** 2) * pct_rem)
        else: # PRE
            away_rem_exp += base_proj
            away_rem_var += (std_dev ** 2)
            
    home_rem_exp = 0.0
    home_rem_var = 0.0
    for p in home_starters:
        base_proj = p.get('baseline_proj', 0.0)
        curr_score = p.get('current_score', 0.0)
        status = p.get('game_status', 'PRE')
        pct_rem = p.get('pct_remaining', 1.0)
        pos_id = p.get('pos_id', 3)
        std_dev = POSITION_STD_DEV.get(pos_id, 5.5)
        
        if status == 'FINAL' or pct_rem <= 0.0:
            pass
        elif status == 'IN_PROGRESS':
            home_rem_exp += (base_proj * pct_rem)
            home_rem_var += ((std_dev ** 2) * pct_rem)
        else: # PRE
            home_rem_exp += base_proj
            home_rem_var += (std_dev ** 2)
            
    total_away_exp = away_current_pts + away_rem_exp
    total_home_exp = home_current_pts + home_rem_exp
    total_var = away_rem_var + home_rem_var
    
    # If all players have completed their games
    if total_var <= 0.001:
        if away_current_pts > home_current_pts:
            return 1.0, 0.0, total_away_exp, total_home_exp
        elif home_current_pts > away_current_pts:
            return 0.0, 1.0, total_away_exp, total_home_exp
        else:
            return 0.5, 0.5, total_away_exp, total_home_exp
            
    z = (total_away_exp - total_home_exp) / math.sqrt(total_var)
    away_prob = normal_cdf(z)
    home_prob = 1.0 - away_prob
    
    return round(away_prob, 4), round(home_prob, 4), round(total_away_exp, 2), round(total_home_exp, 2)

def detect_probability_shockwaves(prev_state: dict, current_state: dict, threshold: float = 0.25) -> list:
    """
    Compares current matchup win probabilities against previous state.
    Triggers shockwave alerts when win prob shifts by >= threshold (e.g. 25%).
    """
    alerts = []
    for match_id, curr_data in current_state.items():
        if match_id not in prev_state:
            continue
        prev_data = prev_state[match_id]
        
        prev_a_prob = prev_data['away_prob']
        curr_a_prob = curr_data['away_prob']
        swing = round(curr_a_prob - prev_a_prob, 4)
        
        # Check if swing exceeds threshold
        if abs(swing) >= threshold:
            favored_team = curr_data['away_team'] if swing > 0 else curr_data['home_team']
            underdog_team = curr_data['home_team'] if swing > 0 else curr_data['away_team']
            alerts.append({
                'match_id': match_id,
                'away_team': curr_data['away_team'],
                'home_team': curr_data['home_team'],
                'away_score': curr_data['away_score'],
                'home_score': curr_data['home_score'],
                'prev_away_prob': prev_a_prob,
                'curr_away_prob': curr_a_prob,
                'prev_home_prob': 1.0 - prev_a_prob,
                'curr_home_prob': 1.0 - curr_a_prob,
                'swing_pct': round(abs(swing) * 100, 1),
                'direction': 'AWAY' if swing > 0 else 'HOME',
                'favored': favored_team,
                'underdog': underdog_team
            })
            
    return alerts

def post_shockwave_alert_to_discord(webhook_url: str, alert: dict):
    """Broadcasts a high-impact in-game win probability shockwave alert to Discord."""
    if not webhook_url:
        return
        
    fav = alert['favored']
    swing_val = alert['swing_pct']
    
    payload = {
        "username": "BFL Live In-Game Tracker",
        "avatar_url": "https://a.espncdn.com/combiner/i?img=/i/headshots/nfl/players/full/3918298.png",
        "embeds": [{
            "title": f"🚨 WIN PROBABILITY SHOCKWAVE: {alert['away_team']} @ {alert['home_team']}",
            "description": f"⚡ **Massive {swing_val}% Win Probability Swing!**\nMomentum has violently shifted in this matchup:\n",
            "color": 0xe74c3c,  # Red Alert
            "fields": [
                {
                    "name": "📊 Live Scoreboard",
                    "value": f"• **{alert['away_team']}**: `{alert['away_score']:.2f}` pts ({alert['curr_away_prob']*100:.1f}% Win Prob)\n• **{alert['home_team']}**: `{alert['home_score']:.2f}` pts ({alert['curr_home_prob']*100:.1f}% Win Prob)",
                    "inline": False
                },
                {
                    "name": "📈 Probability Shift",
                    "value": f"**{alert['away_team']}**: `{alert['prev_away_prob']*100:.1f}%` ➡️ `{alert['curr_away_prob']*100:.1f}%`\n**{alert['home_team']}**: `{alert['prev_home_prob']*100:.1f}%` ➡️ `{alert['curr_home_prob']*100:.1f}%`",
                    "inline": False
                }
            ],
            "footer": {"text": "Beasts Football League • Live Win Probability Monitor"}
        }]
    }
    
    try:
        resp = requests.post(webhook_url, json=payload, timeout=8)
        if resp.status_code in [200, 204]:
            print(f"🚀 Broadcasted {swing_val}% Shockwave Alert to Discord for {alert['away_team']} @ {alert['home_team']}!")
    except Exception as e:
        print(f"❌ Error sending shockwave alert: {e}")

if __name__ == "__main__":
    print("="*75)
    print("⚡ BFL LIVE WIN PROBABILITY & SHOCKWAVE MONITOR ENGINE TEST")
    print("="*75)
    
    # Test Scenario: 4th Quarter Comeback
    away_starters = [
        {'name': 'Josh Allen', 'pos_id': 1, 'baseline_proj': 22.0, 'current_score': 24.5, 'game_status': 'FINAL', 'pct_remaining': 0.0},
        {'name': 'Saquon Barkley', 'pos_id': 2, 'baseline_proj': 16.0, 'current_score': 21.0, 'game_status': 'FINAL', 'pct_remaining': 0.0},
        {'name': 'CeeDee Lamb', 'pos_id': 3, 'baseline_proj': 15.0, 'current_score': 18.2, 'game_status': 'IN_PROGRESS', 'pct_remaining': 0.15} # 9 mins left in 4th
    ]
    home_starters = [
        {'name': 'Patrick Mahomes', 'pos_id': 1, 'baseline_proj': 20.0, 'current_score': 16.2, 'game_status': 'FINAL', 'pct_remaining': 0.0},
        {'name': 'Derrick Henry', 'pos_id': 2, 'baseline_proj': 14.0, 'current_score': 18.5, 'game_status': 'FINAL', 'pct_remaining': 0.0},
        {'name': 'Travis Kelce', 'pos_id': 4, 'baseline_proj': 12.0, 'current_score': 7.1, 'game_status': 'FINAL', 'pct_remaining': 0.0}
    ]
    
    # Prior state (Home was up by 15)
    prev_away_prob, prev_home_prob, _, _ = calculate_matchup_win_probability(away_starters, home_starters, away_current_pts=45.5, home_current_pts=58.0)
    
    # Current state (CeeDee catches 45-yd TD: Away jumps to 63.7 pts)
    curr_away_prob, curr_home_prob, exp_a, exp_h = calculate_matchup_win_probability(away_starters, home_starters, away_current_pts=63.7, home_current_pts=58.0)
    
    print(f"Pre-Score State:  Away Win%: {prev_away_prob*100:.1f}% | Home Win%: {prev_home_prob*100:.1f}%")
    print(f"Post-Score State: Away Win%: {curr_away_prob*100:.1f}% | Home Win%: {curr_home_prob*100:.1f}%")
    print(f"Probability Swing: {abs(curr_away_prob - prev_away_prob)*100:.1f}%")
    
    sample_prev = {'game_1': {'away_team': 'Mykonos Minotaurs', 'home_team': 'The Ehrly Birds', 'away_prob': prev_away_prob, 'home_prob': prev_home_prob}}
    sample_curr = {'game_1': {'away_team': 'Mykonos Minotaurs', 'home_team': 'The Ehrly Birds', 'away_score': 63.7, 'home_score': 58.0, 'away_prob': curr_away_prob, 'home_prob': curr_home_prob}}
    
    alerts = detect_probability_shockwaves(sample_prev, sample_curr, threshold=0.25)
    print(f"\n🚨 Shockwave Alerts Detected: {len(alerts)}")
    if alerts:
        print(f"Alert 1: {alerts[0]['favored']} gained a {alerts[0]['swing_pct']}% probability swing!")
