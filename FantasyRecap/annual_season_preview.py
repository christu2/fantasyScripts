#!/usr/bin/env python3
"""
BFL Annual Season Preview & Division Deep Dive Engine
=====================================================
Generates the comprehensive pre-season master preview:
- State of the League & 18-Year Trophy Count
- Division-by-Division Deep Dive (North, South, East, West)
- Historical Division Win %, Rings, and Rivalry Drama
- Division Favorites, Dark Horses & Marquee Clashes to Watch
- Discord Webhook Embeds & Markdown Document
"""

import os
import sys
import csv
import json
import argparse
import requests
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from FantasyRecap.league_history_intelligence import get_owner_storyline_context, BFL_CHAMPIONSHIPS, OWNER_PROFILES
from FantasyRecap.league_preview_generator import compile_all_time_h2h, TEAM_DETAILS_2026

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
CURRENT_YEAR = os.getenv("SEASON_YEAR") or str(datetime.now().year)

DIVISION_BREAKDOWN = {
    'North': {
        'title': '👑 North Division — "The Heavyweight Gauntlet"',
        'color': 0x3498db,  # Blue
        'members': ['Nick Christus', 'Tommy Ehrlich', 'Daniel Kruszewski', 'Blake Whitehouse'],
        'narrative': "A brutally competitive division boasting 4 combined Super Bowl rings and 6 runner-up appearances. Nick commands 3 titles, former champ Dan Kruszewski reached the finals in both 2022 and 2025, while perennial contenders Tommy and Blake are hungrier than ever for Ring #1.",
        'favorite': 'Nick Christus (3 Rings)',
        'dark_horse': 'Tommy Ehrlich (2x Finalist)'
    },
    'South': {
        'title': '🌴 South Division — "The Reigning Throne & The New Era"',
        'color': 0xe67e22,  # Orange
        'members': ['Abe Thomas', 'Saagar Gupta', 'Nael Ahmed', 'Nitesh Patel'],
        'narrative': "Reigning 2025 champion Abe Thomas (3 Rings) defends his throne against 2008 inaugural champion Saagar Gupta, a dangerous Nael Ahmed franchise, and Nitesh Patel making his solo franchise debut with Big Nasties.",
        'favorite': 'Abe Thomas (Defending Champion)',
        'dark_horse': 'Nitesh Patel (Big Nasties Inaugural Season)'
    },
    'East': {
        'title': '⚔️ East Division — "The Group of Death"',
        'color': 0x9b59b6,  # Purple
        'members': ['Shawn Lukose', 'Dino Davros', 'Samran Mirza', 'rej hoxha'],
        'narrative': "Historically the most decorated and hostile division in the BFL. Features the league's all-time 4-time GOAT Shawn Lukose, 2-time champion Dino Davros, and two elite 2-time Super Bowl finalists in Samran Mirza and Rej Hoxha.",
        'favorite': 'Shawn Lukose (4x GOAT)',
        'dark_horse': 'rej hoxha (2x Finalist seeking redemption)'
    },
    'West': {
        'title': '💥 West Division — "The Wild Wild West"',
        'color': 0x2ecc71,  # Emerald
        'members': ['Sydney Miller', 'Shawn Ullenbrauck', 'Adam Olen', 'Alex Kite'],
        'narrative': "Pure high-octane parity. Features 2024 champion Sydney Miller, 2013 champ Thor (Shawn Ullenbrauck), 2017 champ Adam Olen (AMO), and trade mastermind Alex Kite. Anyone can win this crown on any given Sunday.",
        'favorite': 'Sydney Miller (2024 Champion)',
        'dark_horse': 'Alex Kite (Send Da Trade)'
    }
}

def generate_annual_season_preview(target_season: int, h2h: dict, career_stats: dict):
    """Generates the full markdown preview for the entire season."""
    lines = []
    lines.append(f"# 🏈 BFL {target_season} ANNUAL SEASON PREVIEW & STATE OF THE LEAGUE")
    lines.append(f"*18 Seasons of History • 16 Franchises • 4 Divisions • 1 Lombardi Trophy*\n")
    lines.append("---")
    lines.append("## 🎙️ COMMISSIONER'S STATE OF THE LEAGUE ADDRESS\n")
    lines.append(f"Welcome to the {target_season} campaign of the Beasts Football League! Since our founding in 2008, 18 epic seasons have forged iconic rivalries, legendary championship runs, and unforgettable heartbreaks. With 16 franchises competing across 4 distinct divisions, every single week carries postseason and rivalry implications.\n")
    lines.append("---\n")
    
    # 1. League Ring Count Leaderboard
    lines.append("## 🏆 18-YEAR TROPHY ROOM & CHAMPIONSHIP HONOR ROLL\n")
    lines.append("| Manager | 💍 Rings | Super Bowl Titles | Drought / Title Status |")
    lines.append("|:---|:---:|:---|:---|")
    
    for owner in sorted(OWNER_PROFILES.keys(), key=lambda x: OWNER_PROFILES[x]['rings'], reverse=True):
        ctx = get_owner_storyline_context(owner)
        years_str = ", ".join(map(str, ctx['champ_years'])) if ctx['champ_years'] else "Chasing Ring #1"
        lines.append(f"| **{owner}** | **{ctx['rings']}** | `{years_str}` | {ctx['drought_str']} |")
        
    lines.append("\n---\n")
    
    # 2. Division-by-Division Deep Dive
    division_embed_data = []
    
    for div_name, div_data in DIVISION_BREAKDOWN.items():
        lines.append(f"## {div_data['title']}\n")
        lines.append(f"> *{div_data['narrative']}*\n")
        lines.append(f"* 🥇 **Projected Division Favorite:** **{div_data['favorite']}**")
        lines.append(f"* 🎯 **Dark Horse Contender:** **{div_data['dark_horse']}**\n")
        lines.append("### 👥 Division Franchise Profiles:\n")
        
        member_lines = []
        for m_name in div_data['members']:
            ctx = get_owner_storyline_context(m_name)
            # Find all-time games & wins
            c_stat = career_stats.get(m_name, {'total_pts': 0, 'games': 0, 'wins': 0})
            games = c_stat['games']
            wins = c_stat['wins']
            pct = (wins / games) if games > 0 else 0.0
            
            lines.append(f"#### 👤 {m_name} — *{ctx['tagline']}*")
            lines.append(f"* 📜 **Franchise Bio:** {ctx['summary']}")
            lines.append(f"* 📊 **Career Record (2008–{target_season}):** {wins}W - {games - wins}L (`{pct:.3f}` win%) across {games} career matchups.\n")
            
            member_lines.append({
                'name': m_name,
                'tagline': ctx['tagline'],
                'drought': ctx['drought_str'],
                'record': f"{wins}W - {games - wins}L ({pct:.3f})"
            })
            
        division_embed_data.append({
            'div_name': div_name,
            'title': div_data['title'],
            'color': div_data['color'],
            'narrative': div_data['narrative'],
            'favorite': div_data['favorite'],
            'dark_horse': div_data['dark_horse'],
            'members': member_lines
        })
        lines.append("---\n")
        
    lines.append("## 🔮 2026 MARQUEE RIVALRY GAMES TO WATCH\n")
    lines.append("1. **The Battle of the Shawns VIII** (`Lukose vs. Thor`) — Lifetime series: Lukose 10-8 Thor.")
    lines.append("2. **Samran vs. AMO XV** (`Samran Mirza vs. Adam Olen`) — Deadlocked 7-7 across 14 meetings.")
    lines.append("3. **North Division Rivalry** (`Nick Christus vs. Tommy Ehrlich`) — Nick leads 11-6; Tommy seeks Ring #1.")
    lines.append("4. **The Century Deadlock** (`Dino Davros vs. Rej Hoxha`) — Dino leads 10-6 in 16 clashes since 2008.")
    lines.append("5. **South Division Throne Defense** (`Abe Thomas vs. Saagar Gupta`) — Defending champ vs. Inaugural champ.")
    
    lines.append(f"\n---\n💡 *Generated by BFL Annual Season Preview Engine (2008–{target_season}). Ready for Discord Broadcasting.*")
    return "\n".join(lines), division_embed_data

def post_season_preview_to_discord(webhook_url: str, division_embeds: list, season: int = 2026):
    """Posts the multi-embed Annual Season Preview to Discord."""
    if not webhook_url:
        print("ℹ️ No DISCORD_WEBHOOK_URL configured.")
        return
        
    # Overview Embed
    overview_embed = {
        "title": f"🏈 BFL {season} OFFICIAL ANNUAL SEASON PREVIEW & STATE OF THE LEAGUE",
        "description": f"**18 Seasons of History • 16 Franchises • 4 Divisions • 1 Champion**\n\nThe {season} Beasts Football League campaign is officially underway! Here is your comprehensive guide to all 4 divisions, franchise trophy counts, and rivalry storylines heading into the new year:",
        "color": 0xf1c40f,  # Gold
        "fields": [
            {
                "name": "👑 All-Time BFL GOATs",
                "value": "• **Shawn Lukose**: 4 Rings (2009, 2018, 2021, 2023)\n• **Nick Christus**: 3 Rings (2012, 2015, 2016)\n• **Abe Thomas**: 3 Rings (2011, 2022, 2025 • Defending Champ)\n• **Dino Davros**: 2 Rings (2014, 2020)",
                "inline": False
            },
            {
                "name": "🚪 The 'Chasing Ring #1' Club",
                "value": "• **Tommy Ehrlich** (2x Finalist)\n• **Blake Whitehouse** (2x Finalist)\n• **Samran Mirza** (2x Finalist)\n• **rej hoxha** (2x Finalist)\n• **Nael Ahmed, Alex Kite, Nitesh Patel**",
                "inline": False
            }
        ],
        "footer": {"text": f"Beasts Football League • {season} Annual Season Preview"}
    }
    
    embeds_list = [overview_embed]
    
    for d in division_embeds:
        fields = [
            {"name": "🎯 Division Storyline", "value": d['narrative'], "inline": False},
            {"name": "🥇 Division Favorite", "value": f"**{d['favorite']}**", "inline": True},
            {"name": "🎲 Dark Horse Pick", "value": f"**{d['dark_horse']}**", "inline": True}
        ]
        
        # Member summaries
        mem_str = "\n".join([f"• **{m['name']}** (`{m['record']}`): *{m['tagline']}* [{m['drought']}]" for m in d['members']])
        fields.append({"name": "👥 Franchise Roster", "value": mem_str, "inline": False})
        
        embeds_list.append({
            "title": d['title'],
            "color": d['color'],
            "fields": fields
        })
        
    payload = {
        "username": "BFL League Commissioner",
        "avatar_url": "https://a.espncdn.com/combiner/i?img=/i/headshots/nfl/players/full/3918298.png",
        "embeds": embeds_list
    }
    
    try:
        # Send in chunks if > 10 embeds (here we have 5 embeds)
        resp = requests.post(webhook_url, json=payload, timeout=12)
        if resp.status_code in [200, 204]:
            print("🚀 Successfully broadcasted Annual Season Preview to Discord!")
        else:
            print(f"❌ Discord error status: {resp.status_code}")
    except Exception as e:
        print(f"❌ Error sending Discord webhook: {e}")

def main():
    parser = argparse.ArgumentParser(description="Generate BFL Annual Season Preview")
    parser.add_argument("--season", type=int, default=int(CURRENT_YEAR), help="Season year")
    parser.add_argument("--league-id", default=ESPN_LEAGUE_ID, help="ESPN League ID")
    parser.add_argument("--discord", action="store_true", default=True, help="Broadcast to Discord webhook")
    args = parser.parse_args()
    
    h2h, career_stats = compile_all_time_h2h(args.league_id, ESPN_S2, ESPN_SWID, current_season=args.season, up_to_week=1)
    report_md, division_embeds = generate_annual_season_preview(args.season, h2h, career_stats)
    
    print("\n" + "="*75)
    print(f"🏈 BFL {args.season} ANNUAL SEASON PREVIEW")
    print("="*75)
    print(report_md)
    
    out_file = Path(__file__).resolve().parent / f"annual_season_preview_{args.season}.md"
    with open(out_file, 'w') as f:
        f.write(report_md)
        
    print("\n" + "="*75)
    print(f"💾 Season Preview saved to: {out_file.name}")
    print("="*75)
    
    if args.discord or DISCORD_WEBHOOK_URL:
        post_season_preview_to_discord(DISCORD_WEBHOOK_URL, division_embeds, args.season)

if __name__ == "__main__":
    main()
