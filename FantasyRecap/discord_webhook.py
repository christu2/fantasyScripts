#!/usr/bin/env python3
"""
BFL Discord Webhook Broadcaster
===============================
Takes the weekly analytical breakdown and Gemini AI commentary and broadcasts
rich, beautifully formatted Discord Embed cards to your league's Discord server.
"""

import os
import sys
import json
import argparse
import requests
from datetime import datetime
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from dotenv import load_dotenv

# Load .env
env_path = Path(__file__).resolve().parent.parent / '.env'
if env_path.exists():
    load_dotenv(dotenv_path=env_path)
else:
    load_dotenv()

DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "")

def build_discord_payload(week_num: int, season: str, matchups: list, team_performances: dict, awards: dict, ai_column: str = "") -> dict:
    """Builds a multi-embed Discord payload with colors and structured fields."""
    
    # 1. Main Header Embed
    header_embed = {
        "title": f"🏈 BFL Week {week_num} Official Recap & Power Rankings",
        "description": f"**Season {season} | Beasts Football League**\nAutomated Box Score & Manager IQ Intelligence Report.",
        "color": 0x3498db,  # Blue
        "timestamp": datetime.utcnow().isoformat(),
        "footer": {
            "text": "BFL Analytics Hub • Powered by Google Gemini & ESPN API"
        }
    }
    
    # 2. Superlatives & Awards Embed
    award_fields = []
    if awards.get('game_of_week'):
        gow = awards['game_of_week']
        award_fields.append({
            "name": "🔥 Game of the Week (Thriller)",
            "value": f"**{gow['winner']['owner']}** ({max(gow['away_score'], gow['home_score']):.2f}) def. **{gow['loser']['owner']}** ({min(gow['away_score'], gow['home_score']):.2f}) by **{gow['margin']:.2f} pts**!",
            "inline": False
        })
    if awards.get('blowout_of_week'):
        b = awards['blowout_of_week']
        award_fields.append({
            "name": "🔨 Beatdown of the Week",
            "value": f"**{b['winner']['owner']}** dismantled **{b['loser']['owner']}** by **{b['margin']:.2f} pts** ({max(b['away_score'], b['home_score']):.2f} - {min(b['away_score'], b['home_score']):.2f})",
            "inline": False
        })
    if awards.get('tough_luck'):
        tl = awards['tough_luck']
        award_fields.append({
            "name": "💔 The Tough Luck Heartbreak Award",
            "value": f"**{tl['team']['owner']}** put up **{tl['score']:.2f} pts** (#{tl['weekly_rank']} in league, All-Play: {tl['all_play']}) and took the L.",
            "inline": True
        })
    if awards.get('lucky_winner'):
        lw = awards['lucky_winner']
        award_fields.append({
            "name": "🍀 The Golden Horseshoe",
            "value": f"**{lw['team']['owner']}** scored just **{lw['score']:.2f} pts** (#{lw['weekly_rank']} in league) but stole a win.",
            "inline": True
        })
    if awards.get('bench_mob'):
        bm = awards['bench_mob']
        award_fields.append({
            "name": "🤡 Bench Mob Meltdown",
            "value": f"**{bm['team']['owner']}** left **{bm['bench_lost']:.2f} actionable pts** on the pine (Optimal: {bm['optimal']:.2f}).",
            "inline": False
        })
    if awards.get('worst_blunder'):
        wb = awards['worst_blunder']
        award_fields.append({
            "name": "💣 Single Worst Start/Sit Call",
            "value": f"**{wb['owner']}** started {wb['started']['name']} ({wb['started']['pts']} pts) while **{wb['benched']['name']}** scored **{wb['benched']['pts']} pts** on the bench (+{wb['diff']:.1f} pt blunder).",
            "inline": False
        })

    awards_embed = {
        "title": "🏆 Weekly Superlatives & Roasts",
        "color": 0xf1c40f,  # Gold
        "fields": award_fields
    }

    # 3. Matchup Scores Embed
    scoreboard_lines = []
    for m in matchups:
        w_name = m['winner']['owner'] if isinstance(m['winner'], dict) else 'Tie'
        l_name = m['loser']['owner'] if isinstance(m['loser'], dict) else 'Tie'
        w_score = max(m['away_score'], m['home_score'])
        l_score = min(m['away_score'], m['home_score'])
        scoreboard_lines.append(f"• **{w_name}** ({w_score:.2f}) def. {l_name} ({l_score:.2f}) `[+{m['margin']:.2f}]`")
        
    scores_embed = {
        "title": "📊 Matchup Scoreboard",
        "description": "\n".join(scoreboard_lines),
        "color": 0x2ecc71  # Green
    }

    # 4. Manager IQ & Power Rankings Embed
    sorted_by_eff = sorted(team_performances.values(), key=lambda x: x['efficiency'], reverse=True)
    iq_lines = []
    for idx, p in enumerate(sorted_by_eff[:8]):
        iq_lines.append(f"`#{idx+1:02d}` **{p['team']['owner']}**: **{p['efficiency']:.1f}%** eff (`{p['bench_lost']:.1f}` pts left on bench)")

    sorted_by_score = sorted(team_performances.values(), key=lambda x: x['score'], reverse=True)
    power_lines = []
    for idx, p in enumerate(sorted_by_score[:8]):
        res = "✅" if p['won'] else "❌"
        power_lines.append(f"`#{idx+1:02d}` **{p['team']['owner']}** ({p['score']:.1f} pts) {res} `All-Play: {p['all_play']}`")

    analytics_embed = {
        "title": "🧠 Top Manager IQ & All-Play Rankings",
        "color": 0x9b59b6,  # Purple
        "fields": [
            {"name": "Top Lineup Efficiency (IQ)", "value": "\n".join(iq_lines), "inline": True},
            {"name": "Top Scoring / All-Play", "value": "\n".join(power_lines), "inline": True}
        ]
    }

    payload = {
        "username": "BFL Commish Bot",
        "avatar_url": "https://a.espncdn.com/combiner/i?img=/i/headshots/nfl/players/full/3918298.png",
        "embeds": [header_embed, awards_embed, scores_embed, analytics_embed]
    }
    
    return payload

def post_to_discord(webhook_url: str, payload: dict):
    """Sends the JSON payload to Discord webhook endpoint."""
    if not webhook_url:
        print("⚠️ No DISCORD_WEBHOOK_URL found in .env. Showing payload preview:")
        print(json.dumps(payload, indent=2))
        return False
        
    try:
        resp = requests.post(webhook_url, json=payload, timeout=10)
        if resp.status_code in [200, 204]:
            print("🚀 Successfully broadcasted weekly recap to Discord!")
            return True
        else:
            print(f"❌ Failed to post to Discord (Status {resp.status_code}): {resp.text}")
            return False
    except Exception as e:
        print(f"❌ Error sending Discord webhook: {e}")
        return False

def main():
    parser = argparse.ArgumentParser(description="Broadcast BFL Recap to Discord Webhook")
    parser.add_argument("--week", type=int, default=1, help="Week number to broadcast")
    parser.add_argument("--season", default="2024", help="Season year")
    parser.add_argument("--league-id", default=os.getenv("ESPN_LEAGUE_ID", "157057"), help="ESPN League ID")
    parser.add_argument("--webhook", default=DISCORD_WEBHOOK_URL, help="Custom Discord Webhook URL")
    args = parser.parse_args()

    from FantasyRecap.league_recap_generator import fetch_espn_week_data, analyze_week, generate_weekly_awards

    data = fetch_espn_week_data(args.league_id, args.season, args.week, os.getenv("ESPN_S2"), os.getenv("ESPN_SWID"))
    matchups, team_performances, teams = analyze_week(data, args.week)
    awards = generate_weekly_awards(matchups, team_performances)

    payload = build_discord_payload(args.week, args.season, matchups, team_performances, awards)
    post_to_discord(args.webhook, payload)

if __name__ == "__main__":
    main()
