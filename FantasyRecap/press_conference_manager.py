#!/usr/bin/env python3
"""
BFL Press Conference & Owner Interview Manager
==============================================
Manages the weekly owner interview pipeline:
1. Monday Night Media Call: Posts customized interview prompts to Discord after MNF.
2. Discord Chat Harvesting: Pulls real post-game quotes from #trash-talk / #press-conference.
3. Contextual Fallback Engine: Generates realistic manager statements for owners who didn't submit.
4. Integrates seamlessly into Tuesday Morning Hangover show.
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

def post_monday_night_interview_call(season: int, week_num: int, game_of_week: dict = None, demolition: dict = None):
    """
    Posts an automated Monday Night Media Availability prompt to Discord
    inviting managers to submit post-game press conference statements.
    """
    if not DISCORD_WEBHOOK_PODCAST:
        print("⚠️ DISCORD_WEBHOOK_PODCAST not set in .env")
        return False

    prompt_msg = (
        f"🎙️ **BFL POST-GAME MEDIA AVAILABILITY (WEEK {week_num}, {season})** 🎙️\n"
        f"*The Monday Night Football final gun has sounded! The microphones are live in the media room.*\n\n"
        f"**Managers, submit your official post-game press conference statements below before Tuesday at 7:00 AM CT for inclusion in the BFL Tuesday Morning Hangover show:**\n\n"
        f"• **Winners**: What was the key to securing victory this week?\n"
        f"• **Heartbreakers**: What went wrong in the fourth quarter?\n"
        f"• **Coaches on the Hot Seat**: Any lineup regrets or benching blunders you want to address?\n"
        f"• **Looking Ahead**: Who is calling out their next opponent?\n\n"
        f"*(Quotes submitted here or in `#trash-talk` will be featured on air by Chris & Dave tomorrow morning!)* 📺"
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
    Returns a dict: { 'Owner Name': 'Quote text' }
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
                    # Clean markdown and quote format
                    clean_content = re.sub(r'[*_`]', '', content)
                    quotes[owner_matched] = clean_content
    except Exception as e:
        print(f"⚠️ Error harvesting Discord quotes: {e}")
    return quotes

def get_weekly_manager_quotes(season: int, week_num: int, matchups: list, channel_id: str = None) -> list:
    """
    Returns a comprehensive list of manager quote dicts for the show:
    [{'tag': '...', 'owner': '...', 'team': '...', 'quote': '...'}]
    Prioritizes real Discord submissions and fills missing quotes with intelligent contextual quotes.
    """
    real_quotes = harvest_real_quotes_from_discord(channel_id)
    quote_cards = []

    # Map of fallback templates based on matchup context
    for m in matchups[:4]:
        w = m['winner'] if isinstance(m['winner'], dict) else {'name': str(m['winner']), 'owner': str(m['winner'])}
        l = m['loser'] if isinstance(m['loser'], dict) else {'name': str(m['loser']), 'owner': str(m['loser'])}
        margin = m.get('margin', 10.0)

        w_owner = w.get('owner', 'Winner')
        w_team = w.get('name', 'Team')
        l_owner = l.get('owner', 'Loser')
        l_team = l.get('name', 'Team')

        # Winner Quote
        if w_owner in real_quotes:
            quote_cards.append({
                'tag': 'PRESS ROOM',
                'header': f"{w_owner} ({w_team}):",
                'desc': f"\"{real_quotes[w_owner]}\""
            })
        else:
            w_quote = f"Winning by {margin:.2f} points shows the heart of this roster. We are locked in on The Jabroni!"
            quote_cards.append({
                'tag': 'VICTORY',
                'header': f"{w_owner} ({w_team}):",
                'desc': f"\"{w_quote}\""
            })

        # Loser Quote
        if l_owner in real_quotes:
            quote_cards.append({
                'tag': 'PRESS ROOM',
                'header': f"{l_owner} ({l_team}):",
                'desc': f"\"{real_quotes[l_owner]}\""
            })
        else:
            l_quote = f"Dropping a {margin:.2f}-point decision stings. Emergency team meeting at 8 AM to fix the lineup."
            quote_cards.append({
                'tag': 'HEARTBREAK',
                'header': f"{l_owner} ({l_team}):",
                'desc': f"\"{l_quote}\""
            })

    return quote_cards[:4]

if __name__ == "__main__":
    print("Testing BFL Press Conference Manager...")
    quotes = get_weekly_manager_quotes(2025, 1, [{'winner': {'owner': 'Abe Thomas', 'name': 'Crashee Bandicoot'}, 'loser': {'owner': 'Saagar Gupta', 'name': \"King Gupta's Army\"}, 'margin': 24.44}])
    for q in quotes:
        print("🎙️", q['header'], q['desc'])
