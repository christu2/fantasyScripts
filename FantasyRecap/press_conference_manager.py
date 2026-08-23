#!/usr/bin/env python3
"""
BFL Press Conference & Owner Interview Manager
==============================================
Manages the weekly owner interview pipeline:
1. Monday Night Media Call: Reaches out to managers in Discord after MNF.
   - Specifically tags/prompts managers from the week's biggest storylines (Game of the Week,
     Demolition, Bench Blunder, and Bad Beat) while keeping the floor open for all 16 owners.
2. Discord Chat Harvesting: Pulls ONLY REAL post-game quotes submitted in #trash-talk / #press-conference.
3. STRICT NO-FAKE-QUOTES POLICY: If an owner did not reply, NO fake quote is generated.
   Only genuine Discord statements are featured on the broadcast.
"""

import os
import re
import requests
from pathlib import Path
from dotenv import load_dotenv

env_path = Path(__file__).resolve().parent.parent / '.env'
if env_path.exists():
    load_dotenv(dotenv_path=env_path)
else:
    load_dotenv()

DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN", "")
DISCORD_WEBHOOK_PODCAST = os.getenv("DISCORD_WEBHOOK_PODCAST", "")

# Discord username / nickname to BFL Owner mapping
DISCORD_USER_MAP = {
    'nick': 'Nick Christus',
    'christu2': 'Nick Christus',
    'shawnlukose': 'Shawn Lukose',
    'lukose': 'Shawn Lukose',
    'thor': 'Shawn Ullenbrauck',
    'ullenbrauck': 'Shawn Ullenbrauck',
    'abe': 'Abe Thomas',
    'crashee': 'Abe Thomas',
    'saagar': 'Saagar Gupta',
    'gupta': 'Saagar Gupta',
    'dino': 'Dino Davros',
    'davros': 'Dino Davros',
    'adam': 'Adam Olen',
    'olen': 'Adam Olen',
    'sydney': 'Sydney Miller',
    'miller': 'Sydney Miller',
    'dan': 'Daniel Kruszewski',
    'kruszewski': 'Daniel Kruszewski',
    'rej': 'rej hoxha',
    'hoxha': 'rej hoxha',
    'tommy': 'Tommy Ehrlich',
    'ehrlich': 'Tommy Ehrlich',
    'samran': 'Samran Mirza',
    'mirza': 'Samran Mirza',
    'nael': 'Nael Ahmed',
    'ahmed': 'Nael Ahmed',
    'blake': 'Blake Whitehouse',
    'whitehouse': 'Blake Whitehouse',
    'alex': 'Alex Kite',
    'kite': 'Alex Kite',
    'emelie': 'Emelie Lovasko',
    'lovasko': 'Emelie Lovasko'
}

def post_monday_night_interview_call(
    season: int,
    week_num: int,
    target_owners: list = None,
    gotw_info: str = "",
    demolition_info: str = "",
    blunder_info: str = ""
):
    """
    Posts the Monday Night Media Availability prompt to Discord.
    Specifically spotlights the managers involved in the week's biggest headlines
    while welcoming all 16 managers to submit statements.
    """
    if not DISCORD_WEBHOOK_PODCAST:
        print("⚠️ DISCORD_WEBHOOK_PODCAST not set in .env")
        return False

    spotlight_bullets = ""
    if gotw_info:
        spotlight_bullets += f"• **Game of the Week**: {gotw_info}\n"
    if demolition_info:
        spotlight_bullets += f"• **Demolition of the Week**: {demolition_info}\n"
    if blunder_info:
        spotlight_bullets += f"• **Lineup Questions**: {blunder_info}\n"

    tagged_section = ""
    if target_owners:
        tagged_section = f"\n**Spotlight Availability Requested for:** {', '.join(target_owners)}\n"

    prompt_msg = (
        f"🎙️ **BFL POST-GAME MEDIA AVAILABILITY (WEEK {week_num}, {season})** 🎙️\n"
        f"*The Monday Night Football final gun has sounded! The press room microphones are live.*\n"
        f"{tagged_section}\n"
        f"{spotlight_bullets}\n"
        f"**Managers, submit your official post-game press conference statements below before Tuesday at 7:00 AM CT to be featured in the Tuesday Morning Hangover broadcast:**\n\n"
        f"1. What was the key to this week's outcome?\n"
        f"2. Any lineup decisions or roster moves you want on the record?\n"
        f"3. Looking ahead to next week's matchup?\n\n"
        f"*(Only real statements submitted here or in `#trash-talk` will be read and reacted to on air by Chris & Dave!)* 📺"
    )

    try:
        data = {
            'username': 'BFL Press Room Media Desk',
            'avatar_url': 'https://a.espncdn.com/combiner/i?img=/i/headshots/nfl/players/full/3918298.png',
            'thread_name': f"🎙️ Post-Game Press Conference: Week {week_num} Media Calls",
            'content': prompt_msg
        }
        resp = requests.post(DISCORD_WEBHOOK_PODCAST + "?wait=true", data=data, timeout=15)
        if resp.status_code in [200, 201, 204]:
            print("✅ Monday Night Media Interview Call successfully posted to Discord!")
            return True
        else:
            print(f"❌ Error posting media call: {resp.status_code} - {resp.text}")
    except Exception as e:
        print(f"❌ Exception posting media call: {e}")
    return False

def harvest_real_quotes_from_discord(channel_id: str = None) -> dict:
    """
    Pulls recent messages from Discord and maps them to BFL owners.
    STRICT: Only returns actual, genuine messages from real managers.
    Returns: { 'Owner Name': 'Real quote text' }
    """
    quotes = {}
    if not DISCORD_BOT_TOKEN or not channel_id:
        return quotes

    url = f"https://discord.com/api/v10/channels/{channel_id}/messages?limit=50"
    headers = {"Authorization": f"Bot {DISCORD_BOT_TOKEN}"}
    try:
        r = requests.get(url, headers=headers, timeout=10)
        if r.status_code == 200:
            for m in r.json():
                if m.get('author', {}).get('bot'):
                    continue
                username = m.get('author', {}).get('username', '').lower()
                display_name = m.get('author', {}).get('global_name', '').lower()
                content = m.get('content', '').strip()

                owner_matched = None
                for k, owner in DISCORD_USER_MAP.items():
                    if k in username or k in display_name:
                        owner_matched = owner
                        break

                if owner_matched and content and owner_matched not in quotes:
                    clean_content = re.sub(r'[*_`]', '', content)
                    quotes[owner_matched] = clean_content
    except Exception as e:
        print(f"⚠️ Error harvesting Discord quotes: {e}")
    return quotes

def get_verified_manager_quotes(channel_id: str = None) -> list:
    """
    Returns ONLY real, verified quotes from Discord.
    If no real quotes were submitted, returns an empty list.
    """
    real_quotes = harvest_real_quotes_from_discord(channel_id)
    quote_cards = []

    for owner, quote in real_quotes.items():
        quote_cards.append({
            'tag': 'PRESS ROOM',
            'header': f"{owner}:",
            'desc': f"\"{quote}\""
        })

    return quote_cards

if __name__ == "__main__":
    print("Testing BFL Press Conference Manager (Strict No-Fake-Quotes)...")
    verified = get_verified_manager_quotes()
    print(f"Verified quotes harvested: {len(verified)}")
