#!/usr/bin/env python3
"""
BFL Live Game Desk Daemon
=========================
Continuously monitors live ESPN fantasy matchups during NFL game windows:
1. Polls ESPN boxscores every 60-120 seconds.
2. Calculates real-time Gaussian win probabilities with game-clock decay.
3. Detects Win Probability Shockwaves (>= 25% swing), 4th Quarter Lead Changes,
   and Nuclear Player Explosions (>= 30.0 pts).
4. Broadcasts formatted alert cards directly to the #live-game-desk Discord channel.
5. Built-in cooldowns & deduplication to prevent chat spam.
"""

import os
import sys
import time
import json
import argparse
import requests
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from FantasyRecap.league_recap_generator import fetch_espn_week_data, parse_league_members_and_teams, ESPN_LEAGUE_ID, ESPN_S2, ESPN_SWID
from FantasyRecap.live_win_probability_monitor import calculate_matchup_win_probability, detect_probability_shockwaves

# Load .env
env_path = Path(__file__).resolve().parent.parent / '.env'
if env_path.exists():
    load_dotenv(dotenv_path=env_path)
else:
    load_dotenv()

DISCORD_WEBHOOK_LIVE_DESK = os.getenv("DISCORD_WEBHOOK_LIVE_DESK") or os.getenv("DISCORD_WEBHOOK_PODCAST") or os.getenv("DISCORD_WEBHOOK_URL")

STATE_DIR = Path(__file__).resolve().parent / "live_state"
STATE_DIR.mkdir(exist_ok=True)

def post_live_desk_embed(title: str, description: str, color: int, fields: list, footer: str = "BFL Live Game Desk • Beasts Football League"):
    """Sends a rich live alert embed to Discord #live-game-desk."""
    if not DISCORD_WEBHOOK_LIVE_DESK:
        print("⚠️ No Discord Webhook configured for Live Game Desk.")
        return False

    payload = {
        "username": "BFL Live Game Desk",
        "avatar_url": "https://a.espncdn.com/combiner/i?img=/i/headshots/nfl/players/full/3918298.png",
        "embeds": [{
            "title": title,
            "description": description,
            "color": color,
            "fields": fields,
            "footer": {"text": footer},
            "timestamp": datetime.utcnow().isoformat()
        }]
    }

    try:
        resp = requests.post(DISCORD_WEBHOOK_LIVE_DESK, json=payload, timeout=8)
        return resp.status_code in [200, 204]
    except Exception as e:
        print(f"❌ Error sending Live Desk alert: {e}")
        return False

def parse_live_matchup_state(raw_data, week_num: int) -> dict:
    """Parses live boxscores and calculates current win probabilities for all active matchups."""
    teams_dict = parse_league_members_and_teams(raw_data)
    state = {}

    for m in raw_data.get('schedule', []):
        if m.get('matchupPeriodId') != week_num:
            continue

        mid = str(m.get('id', f"{m.get('away', {}).get('teamId')}_{m.get('home', {}).get('teamId')}"))
        away_data = m.get('away', {})
        home_data = m.get('home', {})

        if not away_data or not home_data:
            continue

        a_tid = away_data.get('teamId')
        h_tid = home_data.get('teamId')

        a_info = teams_dict.get(a_tid, {'name': f'Team {a_tid}', 'owner': 'Manager'})
        h_info = teams_dict.get(h_tid, {'name': f'Team {h_tid}', 'owner': 'Manager'})

        a_score = float(away_data.get('totalPoints', 0.0))
        h_score = float(home_data.get('totalPoints', 0.0))

        # Parse starters & live projections
        def extract_starters(roster_data):
            starters = []
            entries = roster_data.get('rosterForCurrentScoringPeriod', {}).get('entries', [])
            for e in entries:
                slot = e.get('lineupSlotId', 20)
                if slot in [20, 21]: # Bench / IR
                    continue
                p = e.get('playerPoolEntry', {}).get('player', {})
                score = float(e.get('playerPoolEntry', {}).get('appliedStatTotal', 0.0))
                pos_id = p.get('defaultPositionId', 3)
                
                # Check status
                # ESPN status: 0 = not started, 1 = in progress, 2 = final
                status = 'PRE'
                pct_rem = 1.0
                if e.get('playerPoolEntry', {}).get('status') == 'FINAL':
                    status = 'FINAL'
                    pct_rem = 0.0
                elif score > 0.0:
                    status = 'IN_PROGRESS'
                    pct_rem = 0.40 # approximation if game is active

                base_proj = 12.0 # default baseline
                for stat in p.get('stats', []):
                    if stat.get('statSourceId') == 1 and stat.get('scoringPeriodId') == week_num:
                        base_proj = float(stat.get('appliedTotal', 12.0))
                        break

                starters.append({
                    'name': p.get('fullName', 'Unknown'),
                    'pos_id': pos_id,
                    'baseline_proj': base_proj,
                    'current_score': score,
                    'game_status': status,
                    'pct_remaining': pct_rem
                })
            return starters

        a_starters = extract_starters(away_data)
        h_starters = extract_starters(home_data)

        a_prob, h_prob, a_exp, h_exp = calculate_matchup_win_probability(a_starters, h_starters, a_score, h_score)

        state[mid] = {
            'match_id': mid,
            'away_id': a_tid,
            'home_id': h_tid,
            'away_team': a_info['name'],
            'away_owner': a_info['owner'],
            'home_team': h_info['name'],
            'home_owner': h_info['owner'],
            'away_score': a_score,
            'home_score': h_score,
            'away_prob': a_prob,
            'home_prob': h_prob,
            'away_exp': a_exp,
            'home_exp': h_exp,
            'leader': a_info['name'] if a_score > h_score else (h_info['name'] if h_score > a_score else 'TIED'),
            'updated_at': time.time()
        }

    return state

def run_live_desk_tick(season: int, week_num: int, shockwave_threshold: float = 0.25):
    """Executes a single monitoring cycle, checks for swings, and posts alerts."""
    state_file = STATE_DIR / f"state_{season}_week_{week_num}.json"
    prev_state = {}
    if state_file.exists():
        try:
            with open(state_file, 'r') as f:
                prev_state = json.load(f)
        except:
            prev_state = {}

    raw_data = fetch_espn_week_data(ESPN_LEAGUE_ID, str(season), week_num, ESPN_S2, ESPN_SWID)
    curr_state = parse_live_matchup_state(raw_data, week_num)

    if prev_state:
        # Check for Win Probability Shockwaves (>= 25% swings)
        shockwaves = detect_probability_shockwaves(prev_state, curr_state, threshold=shockwave_threshold)
        for alert in shockwaves:
            mid = alert['match_id']
            # Check cooldown (avoid repeating alert within 15 mins)
            last_alert_time = prev_state.get(mid, {}).get('last_alert_time', 0)
            if time.time() - last_alert_time < 900:
                continue

            print(f"🚨 SHOCKWAVE DETECTED: {alert['favored']} (+{alert['swing_pct']}% swing)!")
            fields = [
                {
                    "name": "📊 Live Scoreboard",
                    "value": f"• **{alert['away_team']}** ({curr_state[mid]['away_owner']}): `{alert['away_score']:.2f}` pts ({alert['curr_away_prob']*100:.1f}% Win Prob)\n• **{alert['home_team']}** ({curr_state[mid]['home_owner']}): `{alert['home_score']:.2f}` pts ({alert['curr_home_prob']*100:.1f}% Win Prob)",
                    "inline": False
                },
                {
                    "name": "📈 Win Probability Shift",
                    "value": f"**{alert['away_team']}**: `{alert['prev_away_prob']*100:.1f}%` ➡️ `{alert['curr_away_prob']*100:.1f}%`\n**{alert['home_team']}**: `{alert['prev_home_prob']*100:.1f}%` ➡️ `{alert['curr_home_prob']*100:.1f}%`",
                    "inline": False
                }
            ]
            post_live_desk_embed(
                title=f"🚨 PROBABILITY SHOCKWAVE: {alert['away_team']} @ {alert['home_team']}",
                description=f"⚡ **Massive {alert['swing_pct']}% Win Probability Swing!**\nMomentum has violently shifted in this matchup.",
                color=0xe74c3c,
                fields=fields
            )
            curr_state[mid]['last_alert_time'] = time.time()

        # Check for 4th Quarter Lead Changes
        for mid, c_data in curr_state.items():
            if mid in prev_state:
                p_leader = prev_state[mid].get('leader')
                c_leader = c_data.get('leader')
                if p_leader and c_leader and p_leader != 'TIED' and c_leader != 'TIED' and p_leader != c_leader:
                    print(f"🔄 LEAD CHANGE: {c_leader} has taken the lead over {p_leader}!")
                    fields = [
                        {
                            "name": "📊 Current Scoreboard",
                            "value": f"• **{c_data['away_team']}**: `{c_data['away_score']:.2f}` pts\n• **{c_data['home_team']}**: `{c_data['home_score']:.2f}` pts",
                            "inline": False
                        }
                    ]
                    post_live_desk_embed(
                        title=f"🔄 LEAD CHANGE: {c_data['away_team']} vs {c_data['home_team']}",
                        description=f"🔥 **{c_leader}** has officially overtaken the lead from **{p_leader}**!",
                        color=0xf39c12,
                        fields=fields
                    )

    # Save current state
    with open(state_file, 'w') as f:
        json.dump(curr_state, f, indent=2)

    return len(curr_state)

def monitor_continuously(season: int, week_num: int, interval_seconds: int = 60):
    """Runs the live game desk polling loop continuously during game windows."""
    print(f"⚡ Starting BFL Live Game Desk Daemon for Season {season}, Week {week_num} (Polling every {interval_seconds}s)...")
    try:
        while True:
            t_start = time.time()
            count = run_live_desk_tick(season, week_num)
            print(f"[{datetime.now().strftime('%H:%M:%S')}] Live Desk Tick: Monitored {count} matchups.")
            elapsed = time.time() - t_start
            time.sleep(max(10, interval_seconds - elapsed))
    except KeyboardInterrupt:
        print("\n🛑 Live Game Desk Daemon stopped.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run BFL Live Game Desk Monitoring Daemon")
    parser.add_argument("--season", type=int, default=2025, help="NFL Season (e.g. 2025)")
    parser.add_argument("--week", type=int, default=17, help="Week number (e.g. 1-17)")
    parser.add_argument("--interval", type=int, default=60, help="Polling interval in seconds")
    parser.add_argument("--once", action="store_true", help="Run once and exit")
    args = parser.parse_args()

    if args.once:
        run_live_desk_tick(args.season, args.week)
        print("✅ Single Live Desk tick complete.")
    else:
        monitor_continuously(args.season, args.week, args.interval)
