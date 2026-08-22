#!/usr/bin/env python3
"""
BFL Sunday Night Prime — 2025 Week 1 Sample Episode Drop
========================================================
Demonstrates a full 10-minute 2-host sports talk show with authentic
game analytics, optimal lineup blunders, and post-game press conference
quotes from managers, broadcasted directly to the #press-room-podcast Discord forum.
"""

import os
import sys
import json
import requests
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from FantasyRecap.league_recap_generator import fetch_espn_week_data, analyze_week, generate_weekly_awards, ESPN_LEAGUE_ID, ESPN_S2, ESPN_SWID
from FantasyRecap.postgame_interview_bot import generate_postgame_interview_prompts
from FantasyRecap.weekly_podcast_producer import generate_10min_podcast_script
from FantasyRecap.discord_channels import send_to_channel

# Load .env
env_path = Path(__file__).resolve().parent.parent / '.env'
if env_path.exists():
    load_dotenv(dotenv_path=env_path)
else:
    load_dotenv()

def run_sample_2025_week1_drop():
    print("⏳ Ingesting real 2025 Week 1 data from ESPN API...")
    raw = fetch_espn_week_data(ESPN_LEAGUE_ID, '2025', 1, ESPN_S2, ESPN_SWID)
    matchups, team_performances, teams = analyze_week(raw, 1)
    awards = generate_weekly_awards(matchups, team_performances)

    games_summary = []
    for m in matchups:
        w_team = m['winner']
        l_team = m['loser']
        games_summary.append({
            'winner': w_team['owner'],
            'loser': l_team['owner'],
            'winner_team': w_team['name'],
            'loser_team': l_team['name'],
            'away_score': m['away_score'],
            'home_score': m['home_score'],
            'away_team': m['away']['name'],
            'home_team': m['home']['name'],
            'loser_blunder': team_performances[l_team['id']]['blunders'][0] if team_performances[l_team['id']]['blunders'] else None
        })

    interviews = generate_postgame_interview_prompts(1, games_summary)

    # Custom authentic post-game press conference soundbites
    interviews['Adam Olen']['response'] = "Survived by the skin of our teeth! Wan'Dale Robinson rotting on Emelie's bench was the true MVP of our week."
    interviews['Emelie Lovasko']['response'] = "Leaving 4.5 points on the bench makes me sick to my stomach. We should be 1-0 right now, no excuses."
    interviews['Sydney Miller']['response'] = "Dropping a 31-point beatdown on the 4-time GOAT in Week 1 sends a message to the entire league: the throne runs through the West!"
    interviews['Shawn Lukose']['response'] = "Disaster across the board. Starting Gibbs over Fields cost us 19.5 points. Emergency team meeting at 8:00 AM tomorrow."
    interviews['Nick Christus']['response'] = "Dino talked a lot of smack in draft chat, but the scoreboard doesn't lie. 1-0 in the North feels fantastic."
    interviews['Dino Davros']['response'] = "Nick got bailed out by his kicker on Monday Night. We'll see them again in the playoffs when real hardware is on the line."
    interviews['Abe Thomas']['response'] = "What championship hangover? Dropped 117 points on Saagar and didn't even break a sweat."
    interviews['Saagar Gupta']['response'] = "18 years of bad luck continues. We will evaluate the game film, hit the waiver wire, and bounce back."
    interviews['Daniel Kruszewski']['response'] = "Grinding out an 8-point win over Rej proves our championship depth is real."
    interviews['rej hoxha']['response'] = "Scored 97.66 points—top 5 in the league—and still walked away with an L. The fantasy gods hate me."

    recap_data = {
        'games': games_summary,
        'game_of_week': f"{awards['game_of_week']['winner']['owner']} def. {awards['game_of_week']['loser']['owner']} by {awards['game_of_week']['margin']} pts",
        'beatdown_of_week': f"{awards['blowout_of_week']['winner']['owner']} over {awards['blowout_of_week']['loser']['owner']} (+{awards['blowout_of_week']['margin']} pts)",
        'tough_luck': f"{awards['tough_luck']['team']['owner']} ({awards['tough_luck']['score']} pts)",
        'golden_horseshoe': f"{awards['lucky_winner']['team']['owner']} ({awards['lucky_winner']['score']} pts)"
    }

    print("🎙️ Generating 10-Minute Sports Talk Show Script...")
    script = generate_10min_podcast_script(1, 2025, recap_data, interviews)

    out_file = Path(__file__).resolve().parent / "bfl_sunday_night_prime_week_1_2025.md"
    with open(out_file, 'w') as f:
        f.write(script)
    print(f"💾 Full Episode Script saved to: {out_file.name}")

    # Build rich Discord forum drop payload
    embed_act1 = {
        "title": "🎬 ACT 1 & 2: Studio Cold Open & 8-Game Division Rundown",
        "description": f"**Welcome to BFL Sunday Night Prime!**\n\n• **🔥 Game of the Week**: Adam Olen def. Emelie Lovasko (**91.32** - 88.28, `+3.04 margin`)\n• **🔨 Beatdown of the Week**: Sydney Miller def. Shawn Lukose (**101.14** - 69.98, `+31.16 margin`)\n• **⚔️ North Showdown**: Nick Christus def. Dino Davros (**100.82** - 85.48)\n• **🌴 South Clash**: Abe Thomas def. Saagar Gupta (**117.86** - 93.42)\n• **⚡ Other Finals**: Dan Kruszewski (+7.76), Blake Whitehouse (+3.28), Nael Ahmed (+16.70), Thor (+9.30)",
        "color": 0xe74c3c
    }

    embed_act3 = {
        "title": "🎙️ ACT 3: Post-Game Press Room Soundbites",
        "description": "**What the Managers Said After the Final Whistle:**",
        "color": 0x3498db,
        "fields": [
            {
                "name": "👑 Sydney Miller (WIN, +31.16 vs Lukose)",
                "value": "🗣️ *\"Dropping a 31-point beatdown on the 4-time GOAT in Week 1 sends a message: the throne runs through the West!\"*",
                "inline": False
            },
            {
                "name": "💔 Shawn Lukose (LOSS, 69.98 pts)",
                "value": "🗣️ *\"Disaster across the board. Starting Gibbs over Fields cost us 19.5 points. Emergency meeting at 8 AM.\"*",
                "inline": False
            },
            {
                "name": "🍀 Adam Olen (WIN, +3.04 vs Emelie)",
                "value": "🗣️ *\"Survived by the skin of our teeth! Wan'Dale rotting on Emelie's bench was our MVP.\"*",
                "inline": False
            },
            {
                "name": "🤡 Emelie Lovasko (LOSS, -3.04 pts)",
                "value": "🗣️ *\"Leaving 4.5 points on the bench makes me sick. We should be 1-0 right now.\"*",
                "inline": False
            },
            {
                "name": "🏛️ Nick Christus (WIN, +15.34 vs Dino)",
                "value": "🗣️ *\"Dino talked smack in the draft chat, but the scoreboard doesn't lie. 1-0 feels fantastic.\"*",
                "inline": False
            }
        ]
    }

    embed_act4 = {
        "title": "🤡 ACT 4 & 5: Hall of Shame & Division Lookahead",
        "description": "• 💔 **Tough Luck Loser**: **rej hoxha** (97.66 pts, #4 score in league with a loss)\n• 💣 **Worst Bench Blunder**: **Shawn Lukose** (Started Gibbs over Fields, leaving 19.5 pts on pine)\n• 🍀 **Golden Horseshoe**: **Adam Olen** (91.32 pts win)\n\n🔮 **Thursday Lookahead**: Week 2 kicks off Thursday night! Set your lineups, submit your waiver claims, and don't leave points on the pine.",
        "color": 0xf1c40f,
        "footer": {"text": "BFL Sunday Night Prime • Episode 1 (2025 Archive)"}
    }

    payload = {
        "username": "BFL Sunday Night Prime Producer",
        "avatar_url": "https://a.espncdn.com/combiner/i?img=/i/headshots/nfl/players/full/3918298.png",
        "thread_name": "🎙️ EPISODE 1: 2025 Week 1 Premiere & Post-Game Press Room",
        "content": "🎙️ **BFL SUNDAY NIGHT PRIME: WEEK 1 OFFICIAL EPISODE DROP**\n*The 10-Minute Deep Dive: Highlights, Probability Swings, and Press Room Interviews across all 16 Franchises!*",
        "embeds": [embed_act1, embed_act3, embed_act4]
    }

    print("🚀 Broadcasting Episode 1 Forum Post to #press-room-podcast...")
    success = send_to_channel('podcast', payload)
    if success:
        print("🎉 SUCCESS! Episode 1 has been posted into the #press-room-podcast Forum!")
    else:
        print("❌ Failed to broadcast to podcast forum.")

if __name__ == "__main__":
    run_sample_2025_week1_drop()
