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
    '768661044922417185': 'Adam Olen',
    '1320585267849199619': 'rej hoxha',
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
    'ao3312': 'Adam Olen',
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
    'rej0406': 'rej hoxha',
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
    hr = dt_ct.hour + dt_ct.minute / 60.0 + dt_ct.second / 3600.0
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
        elif hr < 22.55:
            return "Live During Sunday Night Football"
        else:
            return "Late Sunday Night Post-SNF / Entering MNF"
    else:  # Monday (wd == 0)
        if hr < 19.15:
            return "Monday Daytime Entering MNF Sweat"
        elif hr < 22.48:
            return "Live During Monday Night Football"
        else:
            return "Monday Night Post-MNF Final Reaction"


def _detect_mentioned_owners(text: str, speaker: str = "") -> list:
    """Finds all BFL owners referenced by name, first name, or league nickname in a message."""
    clean_padded = re.sub(r"[^a-z0-9'\s]", " ", text.lower())
    lower = f" {clean_padded} "
    mentioned = []
    # Explicit nickname & first-name triggers mapped to canonical owners
    triggers = [
        ("nick christus", "Nick Christus"), (" nick ", "Nick Christus"),
        ("shawn lukose", "Shawn Lukose"), ("lukose", "Shawn Lukose"), ("4 time champ", "Shawn Lukose"), (" goat ", "Shawn Lukose"),
        ("shawn ullenbrauck", "Shawn Ullenbrauck"), (" thor ", "Shawn Ullenbrauck"), ("shawn u", "Shawn Ullenbrauck"),
        ("abe thomas", "Abe Thomas"), (" abe ", "Abe Thomas"), ("defending champ", "Abe Thomas"),
        ("saagar gupta", "Saagar Gupta"), ("saagar", "Saagar Gupta"), ("gupta", "Saagar Gupta"),
        ("dino davros", "Dino Davros"), (" dino ", "Dino Davros"),
        ("adam olen", "Adam Olen"), ("ryan olen", "Adam Olen"), (" olen ", "Adam Olen"), (" adam ", "Adam Olen"), (" amo ", "Adam Olen"),
        ("sydney miller", "Sydney Miller"), ("sydney", "Sydney Miller"),
        ("daniel kruszewski", "Daniel Kruszewski"), (" dtm ", "Daniel Kruszewski"), (" dan ", "Daniel Kruszewski"),
        ("rej hoxha", "rej hoxha"), (" rej ", "rej hoxha"), (" raj ", "rej hoxha"),
        ("tommy ehrlich", "Tommy Ehrlich"), ("tommy", "Tommy Ehrlich"),
        ("samran mirza", "Samran Mirza"), ("samran", "Samran Mirza"), ("72 dolphins", "Samran Mirza"),
        ("nael ahmed", "Nael Ahmed"), (" nael ", "Nael Ahmed"),
        ("blake whitehouse", "Blake Whitehouse"), (" blake ", "Blake Whitehouse"),
        ("alex kite", "Alex Kite"), (" alex ", "Alex Kite"), ("shooter", "Alex Kite"),
        ("nitesh patel", "Nitesh Patel"), ("nitesh", "Nitesh Patel"), ("edgar", "Nitesh Patel"),
    ]
    for trig, own in triggers:
        if trig in lower and own not in mentioned and own != speaker:
            mentioned.append(own)

    # Disambiguate bare 'shawn' when paired with second-half / great team / East / trade context
    if " shawn " in lower and "Shawn Lukose" not in mentioned and "Shawn Ullenbrauck" not in mentioned:
        if any(k in lower for k in ["great team", "second half", "east", "trade", "always"]):
            if speaker != "Shawn Lukose":
                mentioned.append("Shawn Lukose")

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
                            b_hay = (etitle + " " + edesc).lower()
                            inv_owners = []
                            for b_own, b_kws in OWNER_TEAM_KEYWORDS.items():
                                if any(kw in b_hay for kw in b_kws):
                                    inv_owners.append(b_own)
                            bot_events.append({
                                'dt_ct': msg_dt,
                                'channel_name': ch_name,
                                'title': etitle,
                                'desc': edesc[:160],
                                'involved_owners': inv_owners,
                            })
                    continue

                raw_human_msgs.append((ch_name, msg_dt, m))
        except Exception as e:
            print(f"⚠️ Error harvesting Discord chatter from {ch_name}: {e}")

    # Sort all human messages chronologically across all channels
    raw_human_msgs.sort(key=lambda x: x[1])
    bot_events.sort(key=lambda x: x['dt_ct'])

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

        # Check if a relevant bot alert fired shortly before this message
        recent_bot_note = ""
        bot_involved_owners = []
        relevant_owners = [speaker] + mentioned
        relevant_kws = []
        for ro in relevant_owners:
            relevant_kws.extend(OWNER_TEAM_KEYWORDS.get(ro, [ro.lower()]))

        for bev in reversed(bot_events):
            delta_secs = (msg_dt - bev['dt_ct']).total_seconds()
            delta_mins = delta_secs / 60.0
            if 0 <= delta_mins <= 10.0:
                b_haystack = (bev['title'] + " " + bev['desc']).lower()
                # If posted directly inside #live-game-desk within 4 minutes of a single-game/player alert, link directly!
                if ch_name == "#live-game-desk" and bev['channel_name'] == "#live-game-desk" and delta_mins <= 4.0 and bev.get('involved_owners'):
                    time_lbl = f"{int(round(delta_secs))}s" if delta_secs < 90 else f"{int(max(1, round(delta_mins)))} min"
                    recent_bot_note = f"Posted {time_lbl} after {bev['channel_name']} alert: '{bev['title']}'"
                    bot_involved_owners = list(bev.get('involved_owners', []))
                    break
                elif any(kw in b_haystack for kw in relevant_kws):
                    recent_bot_note = f"Posted {int(max(1, round(delta_mins)))} min after {bev['channel_name']} alert: '{bev['title']}'"
                    bot_involved_owners = list(bev.get('involved_owners', []))
                    break
                elif ("miracle" in b_haystack or "thursday" in b_haystack or "playoff odds" in b_haystack) and any(
                    w in clean_text.lower() for w in ["need", "avoid", "outing", "masterclass", "miracle", "odds", "spread", "line"]
                ):
                    recent_bot_note = f"Posted {int(max(1, round(delta_mins)))} min after {bev['channel_name']} alert: '{bev['title']}'"
                    break

        # Only merge consecutive messages from the same speaker if sent within 35 seconds AND no conflicting owner mention
        prev_mentions = entries[-1]['mentioned_owners'] if entries else []
        can_merge_mentions = (not mentioned) or (set(mentioned) == set(prev_mentions))
        if (
            entries
            and entries[-1]['speaker'] == speaker
            and entries[-1]['channel_name'] == ch_name
            and not reply_to_owner
            and (msg_dt - entries[-1]['dt_ct']).total_seconds() <= 35
            and can_merge_mentions
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
            'bot_involved_owners': bot_involved_owners,
            'mentioned_owners': mentioned,
        })

    return entries


PLAYER_TOKEN_STOPWORDS = {
    "white", "brown", "black", "young", "little", "golden", "chase", "swift", "marks",
    "price", "smack", "cooper", "lemon", "knight", "tucker", "warren", "fields", "mason",
    "davis", "allen", "jones", "smith", "miller", "wilson", "moore", "taylor", "thomas",
    "williams", "johnson", "robinson", "washington", "harrison", "coleman", "lions",
    "bears", "packers", "vikings", "bills", "chiefs", "texans", "saints", "browns",
    "eagles", "rams", "49ers", "seahawks", "jaguars", "broncos", "buccaneers", "falcons",
    "patriots", "steelers", "commanders", "giants", "chargers", "hunter", "henry", "love",
    "gordon", "mitchell", "pierce", "sutton", "brooks", "sampson", "bryant", "douglas",
    "wright", "conner", "worthy", "elliott", "walker", "barkley", "london", "downs",
}


def attribute_discord_chatter_to_matchups(detailed_chatter: list, matchups: list, perfs: dict, recent_trades: list = None) -> dict:
    """
    Intelligently attributes Discord messages to the specific Week Matchup(s) they are actually about
    using a multi-signal contextual engine:
      1. Roster & Player-Name Recognition (maps players like 'Will Reichard', 'Concepcion', 'Hockenson',
         'Aaron Jones', 'Bucky Irving', 'Justice Hill' to the exact owner & matchup).
      2. Explicit Owner Mentions, Discord @Tags, and Reply Chains.
      3. #live-game-desk Bot Alert Proximity (links reactions in #live-game-desk to the alert's matchup).
      4. Multi-Turn Conversation Thread Clustering (so when a 3rd-party owner like Nick or Dino is
         bantering back-and-forth in an active conversation thread about another matchup—such as
         Saagar vs. Adam Olen's Will Reichard MNF sweat or Nael benching Justice Hill—their comments
         stay inside that matchup's conversation thread instead of being falsely dumped into their own game).
    Returns:
      { pair_key: [ list of enriched message dicts ] }
    """
    owner_to_pair = {}
    opponent_of = {}
    for m in matchups:
        w, l = m.get('winner', {}), m.get('loser', {})
        if isinstance(w, dict) and isinstance(l, dict):
            w_own, l_own = w.get('owner'), l.get('owner')
            if w_own and l_own:
                pk = tuple(sorted([w_own, l_own]))
                owner_to_pair[w_own] = pk
                owner_to_pair[l_own] = pk
                opponent_of[w_own] = l_own
                opponent_of[l_own] = w_own

    # Build dynamic player -> (canonical_player_name, owner_name) lookup from all 16 rosters
    full_name_map = {
        "big justice": ("Justice Hill", "Nael Ahmed"),
        "kendre": ("Kendre Miller", "Nael Ahmed"),
    }
    token_candidates = {}  # token -> list of (player_name, owner_name)

    for m in matchups:
        for side_key in ('winner', 'loser'):
            side = m.get(side_key, {})
            if not isinstance(side, dict):
                continue
            tid = side.get('id')
            own = side.get('owner')
            if not tid or not own or tid not in perfs:
                continue
            p_list = perfs[tid].get('starters', []) + perfs[tid].get('bench', [])
            for p in p_list:
                pname = (p.get('name') or '').strip()
                if not pname or "D/ST" in pname:
                    continue
                low_full = pname.lower()
                full_name_map[low_full] = (pname, own)
                # Strip Jr./Sr./III/II for clean full-name matching (e.g. 'aaron jones', 'james cook')
                stripped = re.sub(r"\s+(jr\.?|sr\.?|iii|ii|iv)$", "", low_full).strip()
                if stripped and len(stripped) >= 6:
                    full_name_map[stripped] = (pname, own)

                parts = [re.sub(r"[^a-z']", "", pt) for pt in stripped.split()]
                parts = [pt for pt in parts if len(pt) >= 5 and pt not in PLAYER_TOKEN_STOPWORDS]
                for pt in parts:
                    token_candidates.setdefault(pt, []).append((pname, own))

    unique_token_map = {
        tok: vals[0]
        for tok, vals in token_candidates.items()
        if len( set(v[1] for v in vals) ) == 1
    }

    # First pass: compute direct signals for every message (in-place so chronological timeline also has target_pairs)
    enriched = detailed_chatter
    for d in enriched:
        text_low = d['text'].lower()
        padded_low = f" {re.sub(r'[^a-z0-9\s]', ' ', text_low)} "
        speaker = d['speaker']

        matched_players = []
        # 1. Check multi-word full names & nicknames first
        for fn, (pname, pown) in sorted(full_name_map.items(), key=lambda x: len(x[0]), reverse=True):
            if f" {fn} " in padded_low or fn in text_low:
                if (pname, pown) not in matched_players:
                    matched_players.append((pname, pown))

        # 2. Check unique single-token player names (e.g., 'reichard', 'concepcion', 'hockenson', 'bucky')
        for tok, (pname, pown) in unique_token_map.items():
            if f" {tok} " in padded_low:
                if (pname, pown) not in matched_players:
                    matched_players.append((pname, pown))

        d['matched_players'] = matched_players

        direct_pairs = []
        direct_reasons = {}

        # Next-week lookahead filter (e.g. "Samran take care of nael next week pls")
        is_next_week_lookahead = "next week" in text_low

        if is_next_week_lookahead:
            # Attribute to speaker's own matchup or division rival rather than next week's opponent's Week 9 box score
            if speaker in owner_to_pair:
                pk = owner_to_pair[speaker]
                direct_pairs.append(pk)
                direct_reasons[pk] = "Next-week lookahead barb"
        else:
            # Explicit owner mentions & replies (plus trade-injury banter like "selling damaged goods")
            is_trade_banter = any(tb in text_low for tb in ["damaged goods", "selling", "fleeced", "traded"])
            for mo in d.get('mentioned_owners', []):
                routed_via_trade = False
                if is_trade_banter and recent_trades:
                    for tr in recent_trades:
                        oa, ob = tr.get('owner_a'), tr.get('owner_b')
                        if mo in (oa, ob):
                            partner = ob if mo == oa else oa
                            sent_list = tr.get('a_sends', []) if mo == oa else tr.get('b_sends', [])
                            sent_names = ", ".join([p['name'] if isinstance(p, dict) else str(p) for p in sent_list])
                            if partner in owner_to_pair:
                                pk_partner = owner_to_pair[partner]
                                if pk_partner not in direct_pairs:
                                    direct_pairs.append(pk_partner)
                                direct_reasons[pk_partner] = f"Reacting to {mo} trading {sent_names} to {partner}"
                                routed_via_trade = True
                if not routed_via_trade and mo in owner_to_pair:
                    pk = owner_to_pair[mo]
                    if pk not in direct_pairs:
                        direct_pairs.append(pk)
                    direct_reasons[pk] = f"Direct mention/reply regarding {mo}"

            # Rostered player mentions
            for pname, pown in matched_players:
                if pown in owner_to_pair:
                    pk = owner_to_pair[pown]
                    if pk not in direct_pairs:
                        direct_pairs.append(pk)
                    direct_reasons[pk] = f"References {pname} ({pown}'s roster)"

            # #live-game-desk bot alert reaction
            for bo in d.get('bot_involved_owners', []):
                if bo in owner_to_pair:
                    pk = owner_to_pair[bo]
                    if pk not in direct_pairs:
                        direct_pairs.append(pk)
                    if pk not in direct_reasons:
                        direct_reasons[pk] = f"Reacting to #live-game-desk alert on {pk[0]} vs. {pk[1]}"

            # Special league-context phrases
            if "undefeated" in text_low and "winless" in text_low:
                for special_own in ("Samran Mirza", "rej hoxha"):
                    if special_own in owner_to_pair:
                        pk = owner_to_pair[special_own]
                        if pk not in direct_pairs:
                            direct_pairs.append(pk)
                        direct_reasons[pk] = f"References {special_own}'s milestone result"

        # Check if message is explicitly a first-person statement about the speaker's own matchup
        self_matchup_phrases = [
            "my game", "i need to avoid", "with 50+ points sitting on the bench",
            "can i ever play someone", "leading league in scoring", "it was a good run",
            "on the hunt for an rb", "so mad bro", "edge of my seat",
            "4 least pf", "voodoo ritual",
        ]
        d['is_explicit_self_matchup'] = any(sp in text_low for sp in self_matchup_phrases)
        if d['is_explicit_self_matchup'] and not direct_pairs and speaker in owner_to_pair:
            pk = owner_to_pair[speaker]
            direct_pairs.append(pk)
            direct_reasons[pk] = f"{speaker} reacting to their own matchup"

        d['direct_pairs'] = direct_pairs
        d['direct_reasons'] = direct_reasons

    # Second pass: group messages by channel into Conversation Clusters (gap <= 5 minutes)
    clusters = []
    for d in enriched:
        if (
            clusters
            and clusters[-1][-1]['channel_name'] == d['channel_name']
            and (d['dt_ct'] - clusters[-1][-1]['dt_ct']).total_seconds() <= 300
        ):
            clusters[-1].append(d)
        else:
            clusters.append([d])

    # Resolve conversation context inside each cluster
    for cluster in clusters:
        # Check if two opponents from the same matchup are both actively speaking in this cluster
        cluster_speakers = set(item['speaker'] for item in cluster)
        head_to_head_cluster_pair = None
        for sp in cluster_speakers:
            opp = opponent_of.get(sp)
            if opp and opp in cluster_speakers:
                head_to_head_cluster_pair = owner_to_pair.get(sp)
                break

        active_pair = head_to_head_cluster_pair
        for idx, d in enumerate(cluster):
            if d['direct_pairs']:
                d['target_pairs'] = list(d['direct_pairs'])
                d['attribution_notes'] = dict(d['direct_reasons'])
                # Update active conversation topic if this message points to a specific matchup
                if len(d['direct_pairs']) == 1:
                    active_pair = d['direct_pairs'][0]
                continue

            # Look back in the cluster for the most recent resolved topic
            inferred_pair = active_pair
            if not inferred_pair:
                for prev in reversed(cluster[:idx]):
                    if prev.get('target_pairs') and len(prev['target_pairs']) == 1:
                        inferred_pair = prev['target_pairs'][0]
                        break
            # Or look ahead 1 message within 60 seconds if this opened a short exchange
            if not inferred_pair and idx + 1 < len(cluster):
                nxt = cluster[idx + 1]
                if (nxt['dt_ct'] - d['dt_ct']).total_seconds() <= 60 and nxt.get('direct_pairs') and len(nxt['direct_pairs']) == 1:
                    inferred_pair = nxt['direct_pairs'][0]

            if inferred_pair:
                d['target_pairs'] = [inferred_pair]
                active_pair = inferred_pair
                if d['speaker'] not in inferred_pair:
                    d['attribution_notes'] = {
                        inferred_pair: f"Part of live conversation thread on {inferred_pair[0]} vs. {inferred_pair[1]}"
                    }
                else:
                    d['attribution_notes'] = {
                        inferred_pair: f"In-thread reaction by {d['speaker']} during {inferred_pair[0]} vs. {inferred_pair[1]}"
                    }
            else:
                # Fallback to speaker's own matchup
                sp_pair = owner_to_pair.get(d['speaker'])
                d['target_pairs'] = [sp_pair] if sp_pair else []
                d['attribution_notes'] = {sp_pair: f"{d['speaker']}'s matchup comment"} if sp_pair else {}

    per_matchup_quotes = {}
    for d in enriched:
        for pk in d.get('target_pairs', []):
            if pk:
                per_matchup_quotes.setdefault(pk, []).append(d)

    return per_matchup_quotes


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
