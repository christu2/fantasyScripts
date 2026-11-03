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

from zoneinfo import ZoneInfo

DISCORD_COMMISH_CHANNEL_ID = os.getenv("DISCORD_COMMISH_CHANNEL_ID", "1540866967462875186")
CT_TZ = ZoneInfo("America/Chicago")

OWNER_TEAM_KEYWORDS = {
    "Nick Christus": ["mykonos", "minotaurs", "nick christus"],
    "Shawn Lukose": ["nilgiri", "tahrs", "shawn lukose"],
    "Shawn Ullenbrauck": ["omarion", "wayward", "pat n", "shawn ullenbrauck"],
    "Abe Thomas": ["substation", "shockers", "crashee", "bandicoot", "abe thomas"],
    "Saagar Gupta": ["king gupta", "gupta's army", "saagar gupta"],
    "Dino Davros": ["taliban", "mujahideen", "dino davros"],
    "Adam Olen": ["cisse", "green and golden", "adam olen"],
    "Sydney Miller": ["queen of fantasy", "30p chance", "sydney miller"],
    "Daniel Kruszewski": ["dynasty destroyers", "daniel kruszewski"],
    "rej hoxha": ["chase-ing", "steve bartman", "rej hoxha"],
    "Tommy Ehrlich": ["ehrly birds", "tommy ehrlich"],
    "Samran Mirza": ["de'von intervention", "samran mirza"],
    "Nael Ahmed": ["mahomes alone", "nmafia", "nael ahmed"],
    "Blake Whitehouse": ["dan loves obama", "block o", "blake whitehouse"],
    "Alex Kite": ["future gym teachers", "send da trade", "alex kite"],
    "Nitesh Patel": ["big nasties", "nitesh patel"],
}

def _match_author_to_owner(author: dict) -> str:
    """Maps a Discord author object to a canonical BFL owner name."""
    if not author or author.get('bot'):
        return ""
    uid = str(author.get('id', ''))
    if uid in DISCORD_ID_TO_OWNER:
        return DISCORD_ID_TO_OWNER[uid]
    username = (author.get('username') or '').lower()
    display_name = (author.get('global_name') or '').lower()
    for k, owner in DISCORD_USER_MAP.items():
        if k in username or k in display_name:
            return owner
    return ""


def _classify_nfl_slate_window(dt_ct: datetime) -> str:
    """Classifies a Central Time timestamp into the exact NFL / fantasy game-slate window."""
    wd = dt_ct.weekday()  # Mon=0, Tue=1, Wed=2, Thu=3, Fri=4, Sat=5, Sun=6
    hr = dt_ct.hour + dt_ct.minute / 60.0
    if wd in (1, 2):
        return "Tue/Wed Pre-Week & Waivers"
    elif wd == 3:
        if hr < 19.2:
            return "Thursday Pre-TNF Lineup & Spreads Banter"
        elif hr <= 22.75:
            return "Live During Thursday Night Football"
        else:
            return "Thursday Night Post-TNF Reaction"
    elif wd in (4, 5):
        return "Fri/Sat Pre-Game Build-Up"
    elif wd == 6:
        if hr < 8.5:
            return "Sunday Pre-Kickoff"
        elif hr < 12.0:
            return "Sunday Morning / International Game Window"
        elif hr < 15.35:
            return "Live During Sunday 12:00 PM Early Slate"
        elif hr < 19.25:
            return "Live During Sunday 3:25 PM Late Afternoon Slate"
        elif hr < 22.5:
            return "Live During Sunday Night Football"
        else:
            return "Late Sunday Night Post-SNF / Entering MNF"
    else:  # Monday (wd == 0)
        if hr < 19.15:
            return "Monday Daytime Entering MNF Sweat"
        elif hr < 22.15:
            return "Live During Monday Night Football"
        else:
            return "Monday Night Post-MNF Final Reaction"


def _detect_mentioned_owners(text: str, speaker: str = "") -> list:
    """Finds all BFL owners referenced by name, first name, or league nickname in a message."""
    lower = f" {text.lower()} "
    mentioned = []
    # Explicit nickname & first-name triggers mapped to canonical owners
    triggers = [
        ("nick christus", "Nick Christus"), (" nick ", "Nick Christus"),
        ("shawn lukose", "Shawn Lukose"), ("lukose", "Shawn Lukose"), ("4 time champ", "Shawn Lukose"),
        ("shawn ullenbrauck", "Shawn Ullenbrauck"), (" thor ", "Shawn Ullenbrauck"), ("shawn u", "Shawn Ullenbrauck"),
        ("abe thomas", "Abe Thomas"), (" abe ", "Abe Thomas"), ("defending champ", "Abe Thomas"),
        ("saagar gupta", "Saagar Gupta"), ("saagar", "Saagar Gupta"), ("gupta", "Saagar Gupta"),
        ("dino davros", "Dino Davros"), (" dino ", "Dino Davros"),
        ("adam olen", "Adam Olen"), (" adam ", "Adam Olen"), (" amo ", "Adam Olen"),
        ("sydney miller", "Sydney Miller"), ("sydney", "Sydney Miller"),
        ("daniel kruszewski", "Daniel Kruszewski"), (" dtm ", "Daniel Kruszewski"), (" dan ", "Daniel Kruszewski"),
        ("rej hoxha", "rej hoxha"), (" rej ", "rej hoxha"),
        ("tommy ehrlich", "Tommy Ehrlich"), ("tommy", "Tommy Ehrlich"),
        ("samran mirza", "Samran Mirza"), ("samran", "Samran Mirza"), ("72 dolphins", "Samran Mirza"),
        ("nael ahmed", "Nael Ahmed"), (" nael ", "Nael Ahmed"),
        ("blake whitehouse", "Blake Whitehouse"), (" blake", "Blake Whitehouse"),
        ("alex kite", "Alex Kite"), (" alex ", "Alex Kite"), ("shooter", "Alex Kite"),
        ("nitesh patel", "Nitesh Patel"), ("nitesh", "Nitesh Patel"), ("edgar", "Nitesh Patel"),
    ]
    for trig, own in triggers:
        if trig in lower and own not in mentioned and own != speaker:
            mentioned.append(own)
    return mentioned


def _resolve_discord_mentions(text: str) -> str:
    """Replaces <@USER_ID> mentions with the actual BFL owner's name."""
    def _repl(match):
        uid = match.group(1)
        return DISCORD_ID_TO_OWNER.get(uid, "a leaguemate")
    return re.sub(r"<@!?(\d+)>", _repl, text)


def _clean_discord_message(raw_text: str, embeds: list = None, is_reply: bool = False) -> str:
    """Cleans URLs, markdown artifacts, preserves reaction GIF titles on replies, and skips metapodcast links."""
    text = _resolve_discord_mentions(raw_text or "")
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"[*_`]", "", text).strip()

    # Check if a reaction GIF embed title exists (e.g. 'Iron Man Weeps' or 'Worried Kermit The Frog')
    gif_title = ""
    if embeds:
        for emb in embeds:
            t = (emb.get('title') or '').strip()
            if t and len(t) < 45 and not t.startswith("🏈") and not t.startswith("📈"):
                gif_title = t
                break

    if not text and gif_title:
        return f"[Reacted with GIF: '{gif_title}']"
    if len(text) < 4:
        return ""
    lower = text.lower()
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
    if gif_title:
        return f"{text} [GIF: '{gif_title}']"
    return text


def harvest_detailed_discord_chatter(channel_id: str = None, week_num: int = None, lookback_days: float = 6.5) -> list:
    """
    Pulls recent messages from Discord (#trash-talk, #live-game-desk, #commissioner-desk)
    and returns a chronologically sorted list of contextualized message dicts:
      {
        'dt_ct': datetime,
        'timestamp_str': 'Sun 11/01 02:54 PM CT',
        'slate_window': 'Live During Sunday 12:00 PM Early Slate',
        'channel_name': '#trash-talk',
        'speaker': 'Samran Mirza',
        'text': 'The perfect season over. The 72 dolphins can rest easy',
        'reply_to_owner': '...',
        'reply_to_text': '...',
        'bot_context': '...',
        'mentioned_owners': ['...'],
      }
    """
    entries = []
    if not DISCORD_BOT_TOKEN:
        return entries

    headers = {"Authorization": f"Bot {DISCORD_BOT_TOKEN}"}
    cutoff_dt = datetime.now(timezone.utc) - timedelta(days=lookback_days)

    channel_configs = []
    if channel_id:
        channel_configs.append(("#trash-talk", channel_id))
    else:
        if DISCORD_QUOTES_CHANNEL_ID:
            channel_configs.append(("#trash-talk", DISCORD_QUOTES_CHANNEL_ID))
        if DISCORD_LIVE_DESK_CHANNEL_ID and DISCORD_LIVE_DESK_CHANNEL_ID != DISCORD_QUOTES_CHANNEL_ID:
            channel_configs.append(("#live-game-desk", DISCORD_LIVE_DESK_CHANNEL_ID))
        if DISCORD_COMMISH_CHANNEL_ID and DISCORD_COMMISH_CHANNEL_ID not in (DISCORD_QUOTES_CHANNEL_ID, DISCORD_LIVE_DESK_CHANNEL_ID):
            channel_configs.append(("#commissioner-desk", DISCORD_COMMISH_CHANNEL_ID))

    # First pass: collect both bot alerts (for timestamp correlation) and human messages
    bot_events = []
    raw_human_msgs = []

    for ch_name, cid in channel_configs:
        url = f"https://discord.com/api/v10/channels/{cid}/messages?limit=100"
        try:
            r = requests.get(url, headers=headers, timeout=10)
            if r.status_code != 200:
                continue
            for m in reversed(r.json()):
                ts_str = m.get('timestamp', '')
                if not ts_str:
                    continue
                try:
                    msg_dt = datetime.fromisoformat(ts_str.replace('Z', '+00:00')).astimezone(CT_TZ)
                    if msg_dt < cutoff_dt:
                        continue
                except Exception:
                    continue

                if m.get('author', {}).get('bot'):
                    for emb in m.get('embeds', []):
                        etitle = (emb.get('title') or '').strip()
                        edesc = (emb.get('description') or '').strip()
                        if etitle:
                            bot_events.append({
                                'dt_ct': msg_dt,
                                'channel_name': ch_name,
                                'title': etitle,
                                'desc': edesc[:140]
                            })
                    continue

                raw_human_msgs.append((ch_name, msg_dt, m))
        except Exception as e:
            print(f"⚠️ Error harvesting Discord chatter from {ch_name}: {e}")

    # Sort all human messages chronologically across all channels
    raw_human_msgs.sort(key=lambda x: x[1])
    bot_events.sort(key=lambda x: x['dt_ct'])

    # Merge consecutive messages sent by the same speaker within 3 minutes so rapid 2-line thoughts stay together
    for ch_name, msg_dt, m in raw_human_msgs:
        speaker = _match_author_to_owner(m.get('author', {}))
        if not speaker:
            continue

        ref = m.get('referenced_message') or {}
        reply_to_owner = _match_author_to_owner(ref.get('author', {}))
        reply_to_text = _clean_discord_message(ref.get('content', '')) if ref else ""

        clean_text = _clean_discord_message(m.get('content', ''), embeds=m.get('embeds', []), is_reply=bool(reply_to_owner))
        if not clean_text:
            continue

        mentioned = _detect_mentioned_owners(clean_text + " " + reply_to_text, speaker=speaker)
        if reply_to_owner and reply_to_owner not in mentioned and reply_to_owner != speaker:
            mentioned.insert(0, reply_to_owner)

        # Check if a relevant bot alert fired within the preceding 10 minutes
        recent_bot_note = ""
        relevant_owners = [speaker] + mentioned
        relevant_kws = []
        for ro in relevant_owners:
            relevant_kws.extend(OWNER_TEAM_KEYWORDS.get(ro, [ro.lower()]))

        for bev in reversed(bot_events):
            delta_mins = (msg_dt - bev['dt_ct']).total_seconds() / 60.0
            if 0 <= delta_mins <= 10.0:
                b_haystack = (bev['title'] + " " + bev['desc']).lower()
                if any(kw in b_haystack for kw in relevant_kws) or "miracle" in b_haystack or "thursday" in b_haystack or "playoff odds" in b_haystack:
                    recent_bot_note = f"Posted {int(max(1, round(delta_mins)))} min after {bev['channel_name']} alert: '{bev['title']}'"
                    break

        # Merge if immediately following the same speaker within 3 minutes and no new reply target
        if (
            entries
            and entries[-1]['speaker'] == speaker
            and entries[-1]['channel_name'] == ch_name
            and not reply_to_owner
            and (msg_dt - entries[-1]['dt_ct']).total_seconds() <= 180
        ):
            if clean_text not in entries[-1]['text']:
                entries[-1]['text'] += f" — {clean_text}"
                for mo in mentioned:
                    if mo not in entries[-1]['mentioned_owners']:
                        entries[-1]['mentioned_owners'].append(mo)
            continue

        entries.append({
            'dt_ct': msg_dt,
            'timestamp_str': msg_dt.strftime("%a %m/%d %I:%M %p CT"),
            'slate_window': _classify_nfl_slate_window(msg_dt),
            'channel_name': ch_name,
            'speaker': speaker,
            'text': clean_text,
            'reply_to_owner': reply_to_owner,
            'reply_to_text': reply_to_text[:90] if reply_to_text else "",
            'bot_context': recent_bot_note,
            'mentioned_owners': mentioned,
        })

    return entries


def harvest_real_quotes_from_discord(channel_id: str = None, week_num: int = None, lookback_days: float = 6.5) -> dict:
    """
    Pulls recent messages from Discord and maps them to BFL owners with full timestamps,
    NFL game-slate windows, and reply context.
    Returns: { 'Owner Name': 'Timestamped & contextualized quotes' }
    """
    owner_lines = {}
    detailed = harvest_detailed_discord_chatter(channel_id=channel_id, week_num=week_num, lookback_days=lookback_days)
    for item in detailed:
        speaker = item['speaker']
        reply_tag = f" (replying to {item['reply_to_owner']}: \"{item['reply_to_text']}\")" if item['reply_to_owner'] and item['reply_to_text'] else (f" (replying to {item['reply_to_owner']})" if item['reply_to_owner'] else "")
        formatted = f"[{item['timestamp_str']} — {item['slate_window']} in {item['channel_name']}]{reply_tag} \"{item['text']}\""
        owner_lines.setdefault(speaker, []).append(formatted)

    quotes = {}
    for owner, lines in owner_lines.items():
        selected = lines[-6:]
        quotes[owner] = " | ".join(selected)
    return quotes


def get_verified_manager_quotes(channel_id: str = None, week_num: int = None) -> list:
    """
    Returns ONLY real, verified quotes from Discord with timestamps & game-slate context.
    """
    real_quotes = harvest_real_quotes_from_discord(channel_id, week_num=week_num)
    quote_cards = []

    for owner, quote in real_quotes.items():
        quote_cards.append({
            'tag': 'PRESS ROOM',
            'header': f"{owner}:",
            'desc': f"{quote}"
        })

    return quote_cards

if __name__ == "__main__":
    print("Testing BFL Press Conference Manager (Timestamped & Contextualized)...")
    detailed = harvest_detailed_discord_chatter()
    print(f"Detailed messages harvested: {len(detailed)}")
    for d in detailed:
        rep = f" [Reply to {d['reply_to_owner']}: '{d['reply_to_text']}']" if d['reply_to_owner'] else ""
        bot_c = f" ({d['bot_context']})" if d['bot_context'] else ""
        print(f"  • [{d['timestamp_str']} | {d['slate_window']} | {d['channel_name']}] {d['speaker']}{rep}: \"{d['text']}\"{bot_c} -> Mentions: {d['mentioned_owners']}")
