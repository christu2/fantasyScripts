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
from datetime import datetime, timezone, timedelta
from pathlib import Path
from dotenv import load_dotenv

env_path = Path(__file__).resolve().parent.parent / '.env'
if env_path.exists():
    load_dotenv(dotenv_path=env_path, override=True)
else:
    load_dotenv(override=True)

DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN", "")
DISCORD_WEBHOOK_PODCAST = os.getenv("DISCORD_WEBHOOK_PODCAST", "")
DISCORD_QUOTES_CHANNEL_ID = os.getenv("DISCORD_QUOTES_CHANNEL_ID") or os.getenv("DISCORD_TRASH_TALK_CHANNEL_ID") or os.getenv("DISCORD_CHANNEL_ID", "")
DISCORD_LIVE_DESK_CHANNEL_ID = os.getenv("DISCORD_LIVE_DESK_CHANNEL_ID", "1540867012996104203")

# Exact Discord User ID to BFL Owner mapping
DISCORD_ID_TO_OWNER = {
    '1540793260610560070': 'Nick Christus',
    '339254563968385031': 'Nitesh Patel',
    '1541444214632611882': 'Shawn Lukose',
    '804923026927124501': 'Dino Davros',
    '606885780190658706': 'Nael Ahmed',
    '1551345621783543869': 'Saagar Gupta',
    '650944454987087912': 'Samran Mirza',
    '1550288879318204447': 'Daniel Kruszewski',
}

# Discord username / nickname to BFL Owner mapping
DISCORD_USER_MAP = {
    'nchristus93': 'Nick Christus',
    'nick': 'Nick Christus',
    'christu2': 'Nick Christus',
    'shawnlukose': 'Shawn Lukose',
    'lukose': 'Shawn Lukose',
    'thor': 'Shawn Ullenbrauck',
    'ullenbrauck': 'Shawn Ullenbrauck',
    'abe': 'Abe Thomas',
    'crashee': 'Abe Thomas',
    'kgarmy0447': 'Saagar Gupta',
    'kgarmy': 'Saagar Gupta',
    'saagar': 'Saagar Gupta',
    'gupta': 'Saagar Gupta',
    'venicado': 'Dino Davros',
    'dino': 'Dino Davros',
    'davros': 'Dino Davros',
    'adam': 'Adam Olen',
    'olen': 'Adam Olen',
    'amo': 'Adam Olen',
    'sydney': 'Sydney Miller',
    'miller': 'Sydney Miller',
    'savage69_09383': 'Daniel Kruszewski',
    'savage69': 'Daniel Kruszewski',
    'dan': 'Daniel Kruszewski',
    'kruszewski': 'Daniel Kruszewski',
    'dtm': 'Daniel Kruszewski',
    'rej': 'rej hoxha',
    'hoxha': 'rej hoxha',
    'tommy': 'Tommy Ehrlich',
    'thomas': 'Tommy Ehrlich',
    'ehrlich': 'Tommy Ehrlich',
    'smirza107986': 'Samran Mirza',
    'smirza10': 'Samran Mirza',
    'samran': 'Samran Mirza',
    'mirza': 'Samran Mirza',
    'naeleezy': 'Nael Ahmed',
    'nael': 'Nael Ahmed',
    'ahmed': 'Nael Ahmed',
    'nmafia': 'Nael Ahmed',
    'blake': 'Blake Whitehouse',
    'whitehouse': 'Blake Whitehouse',
    'alex': 'Alex Kite',
    'kite': 'Alex Kite',
    'shooter': 'Alex Kite',
    'npate2076487': 'Nitesh Patel',
    'npate207': 'Nitesh Patel',
    'nitesh': 'Nitesh Patel',
    'patel': 'Nitesh Patel',
    'edgar': 'Nitesh Patel',
    'nasties': 'Nitesh Patel'
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

def _resolve_discord_mentions(text: str) -> str:
    """Replaces <@USER_ID> mentions with the actual BFL owner's name."""
    def _repl(match):
        uid = match.group(1)
        return DISCORD_ID_TO_OWNER.get(uid, "a leaguemate")
    return re.sub(r"<@!?(\d+)>", _repl, text)


def _clean_discord_message(raw_text: str) -> str:
    """Cleans URLs, markdown artifacts, and skips meta/podcast-link/GIF-only messages."""
    text = _resolve_discord_mentions(raw_text or "")
    # Remove URLs (GIFs, Discord channel links, etc.)
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"[*_`]", "", text).strip()
    if len(text) < 4:
        return ""
    lower = text.lower()
    # Skip meta messages about uploading the podcast or generic short chat filler without football context
    skip_phrases = [
        "podcast is up", "pod is up", "podcast now available", "ai pod is getting better",
        "pitch my new in game bot", "disapproves of my ai prowess", "different league",
        "is sydney not in this discord", "nah she's a hold out", "lmao this shit hilarious",
        "don't get me wrong. i approve", "didn't want you to squander it", "not the meme i was expecting",
        "all i need", "fantastic", "love to see it", "absolute stud", "getting a little heated in here",
        "good work nitesh"
    ]
    if any(sp in lower for sp in skip_phrases):
        return ""
    return text


def harvest_real_quotes_from_discord(channel_id: str = None, week_num: int = None, lookback_days: float = 7.5) -> dict:
    """
    Pulls recent messages from Discord (#trash-talk and #live-game-desk) or a local drop-in file
    and maps them to BFL owners.
    STRICT: Only returns actual, genuine messages from real managers within the current week window.
    Returns: { 'Owner Name': 'Real quote text' }
    """
    owner_lines = {}

    # 1. Check optional local drop-in chatter file (if user pasted chat from #trash-talk / #live-game-desk)
    state_dir = Path(__file__).resolve().parent / "live_state"
    candidate_files = []
    if week_num:
        candidate_files.append(state_dir / f"discord_chatter_week_{week_num}.txt")
    candidate_files.append(state_dir / "discord_chatter.txt")

    for cf in candidate_files:
        if cf.exists():
            try:
                for raw_line in cf.read_text().splitlines():
                    line = raw_line.strip()
                    if not line or line.startswith("#"):
                        continue
                    lower_line = line.lower()
                    for k, owner in DISCORD_USER_MAP.items():
                        if lower_line.startswith(k + ":") or lower_line.startswith(owner.lower() + ":"):
                            msg_part = _clean_discord_message(line.split(":", 1)[1].strip())
                            if msg_part:
                                owner_lines.setdefault(owner, []).append(msg_part)
                            break
            except Exception:
                pass

    # 2. If DISCORD_BOT_TOKEN is configured, harvest from #trash-talk and #live-game-desk
    channels_to_scan = []
    if channel_id:
        channels_to_scan.append(channel_id)
    else:
        if DISCORD_QUOTES_CHANNEL_ID:
            channels_to_scan.append(DISCORD_QUOTES_CHANNEL_ID)
        if DISCORD_LIVE_DESK_CHANNEL_ID and DISCORD_LIVE_DESK_CHANNEL_ID not in channels_to_scan:
            channels_to_scan.append(DISCORD_LIVE_DESK_CHANNEL_ID)

    if DISCORD_BOT_TOKEN and channels_to_scan:
        headers = {"Authorization": f"Bot {DISCORD_BOT_TOKEN}"}
        cutoff_dt = datetime.now(timezone.utc) - timedelta(days=lookback_days)

        for target_channel in channels_to_scan:
            url = f"https://discord.com/api/v10/channels/{target_channel}/messages?limit=100"
            try:
                r = requests.get(url, headers=headers, timeout=10)
                if r.status_code == 200:
                    # Iterate oldest-to-newest so multi-line thoughts read chronologically
                    for m in reversed(r.json()):
                        if m.get('author', {}).get('bot'):
                            continue
                        ts_str = m.get('timestamp', '')
                        if ts_str:
                            try:
                                msg_dt = datetime.fromisoformat(ts_str.replace('Z', '+00:00'))
                                if msg_dt < cutoff_dt:
                                    continue
                            except Exception:
                                pass

                        author = m.get('author', {})
                        uid = str(author.get('id', ''))
                        username = (author.get('username') or '').lower()
                        display_name = (author.get('global_name') or '').lower()

                        owner_matched = DISCORD_ID_TO_OWNER.get(uid)
                        if not owner_matched:
                            for k, owner in DISCORD_USER_MAP.items():
                                if k in username or k in display_name:
                                    owner_matched = owner
                                    break

                        clean_content = _clean_discord_message(m.get('content', ''))
                        if owner_matched and clean_content:
                            existing = owner_lines.setdefault(owner_matched, [])
                            if clean_content not in existing:
                                existing.append(clean_content)
            except Exception as e:
                print(f"⚠️ Error harvesting Discord quotes from channel {target_channel}: {e}")

    quotes = {}
    for owner, lines in owner_lines.items():
        # Keep up to 6 impactful statements per manager from the week
        selected = lines[-6:]
        quotes[owner] = " | ".join(selected)
    return quotes

def get_verified_manager_quotes(channel_id: str = None, week_num: int = None) -> list:
    """
    Returns ONLY real, verified quotes from Discord.
    If no real quotes were submitted, returns an empty list.
    """
    real_quotes = harvest_real_quotes_from_discord(channel_id, week_num=week_num)
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
