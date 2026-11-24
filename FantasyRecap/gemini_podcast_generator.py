#!/usr/bin/env python3
"""
BFL Automated Podcast Generator (Gemini Scriptwriter + Gemini Native Multi-Speaker TTS)
=======================================================================================
Turns your existing `output/notebook_payload_week_{W}_{Y}.md` Scoreboard into a
fully automated, broadcast-mastered Chris & Dave sports-talk MP3 — with ZERO manual
NotebookLM upload/download steps — ready for Apple Podcasts Private RSS & Discord!

Two-Stage Pipeline:
  1. Showrunner Scriptwriter (`gemini-2.5-flash`):
     Reads the weekly Scoreboard Markdown and writes a natural, conversational
     `Chris:` / `Dave:` sports-talk script with exact league phonetics baked in
     (`Sah-gur`, `Ray`, `Luke-ose`, `Jah-BRO-nee`, `Mee-ko-nos`, `Nile`, `Sum-rahn`).
  2. Native Multi-Speaker TTS (`gemini-2.5-flash-preview-tts`):
     Synthesizes expressive two-host dialogue in clean 24kHz PCM chunks, wraps to WAV,
     applies broadcast loudness normalization (`ffmpeg loudnorm`), and exports a
     crisp 128 kbps MP3 (and optionally updates your Private Podcast RSS `feed.xml`!).

Usage:
  # 1. Quick 2-3 minute highlight clip to audition voice quality vs. NotebookLM:
  python3 FantasyRecap/gemini_podcast_generator.py --season 2026 --week 10 --sample

  # 2. Full 15-22 minute episode covering all 8 matchups + standings + next week:
  python3 FantasyRecap/gemini_podcast_generator.py --season 2026 --week 10 --full

  # 3. Full episode + auto-update Private Apple Podcasts RSS feed:
  python3 FantasyRecap/gemini_podcast_generator.py --season 2026 --week 10 --full --rss

  # 4. Audition different host voices or re-render from a tweaked script:
  python3 FantasyRecap/gemini_podcast_generator.py --season 2026 --week 10 --sample --chris-voice Orus --dave-voice Fenrir
  python3 FantasyRecap/gemini_podcast_generator.py --season 2026 --week 10 --from-script FantasyRecap/output/gemini_script_week_10_2026_sample.txt
"""

import argparse
import base64
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import wave
from pathlib import Path
from typing import List, Optional, Tuple
import requests

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from FantasyRecap.core.manifest import load_project_env
load_project_env()

from FantasyRecap.bfl_history_db import _call_gemini
from FantasyRecap.config import CURRENT_YEAR
from FantasyRecap.podcast_rss_builder import add_episode_to_rss

OUTPUT_DIR = SCRIPT_DIR / "output"
OUTPUT_DIR.mkdir(exist_ok=True)

# Phonetic replacements applied ONLY to the spoken TTS script so hosts nail BFL names every time
PHONETIC_REPLACEMENTS = [
    (r"\brej hoxha\b", "Ray Hozha"),
    (r"\brej\b", "Ray"),
    (r"\bSaagar\b", "Sah-gur"),
    (r"\bLukose\b", "Luke-ose"),
    (r"\bMykonos\b", "Mee-ko-nos"),
    (r"\bNael\b", "Nile"),
    (r"\bSamran\b", "Sum-rahn"),
    (r"\bKruszewski\b", "Kroo-sheff-skee"),
    (r"\bJabroni\b", "Jah-bronee"),
    (r"\bSkattebo\b", "Skat-uh-bo"),
]

# Deterministic spoken-English contractions so TTS never sounds stiff ("Let us", "I am", "That is")
CONTRACTION_REPLACEMENTS = [
    (r"\bLet us\b", "Let's"),
    (r"\blet us\b", "let's"),
    (r"\bI am\b", "I'm"),
    (r"\bWe are\b", "We're"),
    (r"\bwe are\b", "we're"),
    (r"\bWe have got\b", "We've got"),
    (r"\bwe have got\b", "we've got"),
    (r"\bYou have got\b", "You've got"),
    (r"\byou have got\b", "you've got"),
    (r"\bThat is\b", "That's"),
    (r"\bthat is\b", "that's"),
    (r"\bIt is\b", "It's"),
    (r"\bit is\b", "it's"),
    (r"\bHe is\b", "He's"),
    (r"\bhe is\b", "he's"),
    (r"\bShe is\b", "She's"),
    (r"\bshe is\b", "she's"),
    (r"\bThey are\b", "They're"),
    (r"\bthey are\b", "they're"),
    (r"\bYou are\b", "You're"),
    (r"\byou are\b", "you're"),
    (r"\bCould not\b", "Couldn't"),
    (r"\bcould not\b", "couldn't"),
    (r"\bWould not\b", "Wouldn't"),
    (r"\bwould not\b", "wouldn't"),
    (r"\bDid not\b", "Didn't"),
    (r"\bdid not\b", "didn't"),
    (r"\bDo not\b", "Don't"),
    (r"\bdo not\b", "don't"),
    (r"\bCannot\b", "Can't"),
    (r"\bcannot\b", "can't"),
]

# Default expressive voices for Chris (lead sports desk anchor) & Dave (sharp, punchy co-host)
DEFAULT_CHRIS_VOICE = "Charon"
DEFAULT_DAVE_VOICE = "Puck"

STYLE_DIRECTIVE = (
    "Perform this as a fast-paced, genuinely funny fantasy football sports-talk podcast "
    "between lifelong buddies Chris and Dave (Bill Simmons / Dan Le Batard / SportsCenter energy). "
    "Deliver short reactions with real punch, playful sarcasm, natural emphasis, and warm conversational flow—"
    "like two friends riffing live at a studio desk, never reading off a teleprompter."
)


def apply_phonetics(script_text: str) -> str:
    """Applies BFL-specific pronunciation spellings and natural contractions for clean TTS delivery."""
    out = script_text
    for pattern, repl in CONTRACTION_REPLACEMENTS:
        out = re.sub(pattern, repl, out)
    for pattern, repl in PHONETIC_REPLACEMENTS:
        out = re.sub(pattern, repl, out, flags=re.IGNORECASE)
    return out


def extract_scoreboard_sections(payload_md: str) -> dict:
    """
    Splits `notebook_payload_week_{W}_{Y}.md` into logical broadcast acts so long
    episodes can be scripted & rendered in clean, high-energy chunks without
    hitting single-call TTS output token limits.
    """
    act1_match = re.search(r"(## 🔄 ACT 1:.*?)(?=## 🏈 ACT 2:)", payload_md, flags=re.S)
    act2_match = re.search(r"(## 🏈 ACT 2:.*?)(?=## 🏆 ACT 3:|## 📊 ACT 3:|## 📚 LEAGUE LORE)", payload_md, flags=re.S)
    act3_match = re.search(r"((?:## 🏆 ACT 3:|## 📊 ACT 3:|## 🔮 NEXT WEEK).*)", payload_md, flags=re.S)

    act1 = act1_match.group(1).strip() if act1_match else payload_md[:4000]
    act2_full = act2_match.group(1).strip() if act2_match else ""
    act3 = act3_match.group(1).strip() if act3_match else payload_md[-10000:]

    # Split Act 2 into individual matchup blocks
    matchups = re.findall(r"(### 🏟️ MATCHUP #\d+ OF \d+:.*?)(?=### 🏟️ MATCHUP #|\Z)", act2_full, flags=re.S)
    matchups = [m.strip() for m in matchups if m.strip()]

    return {
        "act1": act1,
        "matchups": matchups,
        "act3": act3[:18000],
    }


def pick_headline_matchup(matchups: List[str]) -> str:
    """Selects the most drama-packed matchup (most Live Desk swings + Discord chatter) for --sample clips."""
    if not matchups:
        return ""

    def _drama_score(block: str) -> int:
        return (
            block.count("LIVE DESK SHOCKWAVE") * 4
            + block.count("NUCLEAR ERUPTION") * 2
            + block.count("💬") * 3
            + block.count("🩹 In-Game Injury") * 2
            + block.count("Costly Start/Sit") * 3
        )

    return max(matchups, key=_drama_score)


def _trim_matchup_for_script(block: str, max_chars: int = 7500) -> str:
    """Strips bulky preseason 'Season Outlook' essays so the scriptwriter focuses on live Week drama."""
    cleaned = re.sub(r"^\s*\*Season Outlook:\*.*$", "", block, flags=re.M)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned[:max_chars]


def generate_dialogue_script(
    payload_md: str,
    season: int,
    week: int,
    *,
    api_key: str,
    length_mode: str = "sample",
) -> List[Tuple[str, str]]:
    """
    Uses Gemini Flash to write a punchy `Chris:` / `Dave:` dialogue script.
    `length_mode`:
      - 'sample'   : ~2–3 min highlight clip (1 segment)
      - 'standard' : ~16–20 min episode (6 segments: intro + 4 matchup pairs + standings)
      - 'deep'     : ~30–40 min deep-dive episode (10 segments: intro + 1 dedicated segment per matchup + extended standings/preview)
    Returns a list of `(segment_name, dialogue_text)` tuples ready for TTS rendering.
    """
    sections = extract_scoreboard_sections(payload_md)
    segments: List[Tuple[str, str]] = []

    base_rules = f"""You are the head writer for "Tuesday Morning Hangover", the Beasts Football League (BFL) weekly fantasy football podcast (Season {season}, Week {week}).
Write ONLY spoken dialogue alternating strictly between two hosts labeled `Chris:` and `Dave:`.

CRITICAL WRITING RULES FOR LIFELIKE SPORTS-RADIO BANTER:
1. REAL PING-PONG CHEMISTRY (NOT CHRONOLOGICAL RECITATION):
   - Write like two sharp buddies actually riffing—NOT taking turns reading box scores!
   - Mix **short 4–12 word reactions, questions, pushback, and disbelief** ("Wait—he actually posted that in Discord?!", "That's an all-time self-own.", "Hold on, don't gloss over that!") between punchy 1–3 sentence takes.
   - Vary your conversational starters—do NOT overuse `"Bro"` (use it at most once per segment, if at all).
   - NEVER say "Matchup 7 of 8 featured..." or read boring lists of minor waiver cuts—lead straight with the drama!
2. NATURAL CONTRACTIONS & SPOKEN ENGLISH ONLY:
   - ALWAYS use contractions (`I'm`, `we're`, `let's`, `it's`, `that's`, `we've`, `didn't`, `can't`, `he's`, `you're`, `what's`).
   - NEVER write stiff formal phrases like `I am`, `let us`, `we are`, `we have got`, `it is`, `that is`.
   - NEVER write literal `"Haha"`, `"Hehe"`, or `"Lol"` (TTS reads "ha-ha" awkwardly)—use real spoken reactions like `"Oh man, hold on—"`, `"Wait a second—"`, `"No chance!"`.
3. USE LEAGUE NICKNAMES & FIRST NAMES LIKE INSIDERS:
   - Use **Thor** (for Shawn Ullenbrauck), **Lukose** (for Shawn Lukose), **AMO** (for Adam Olen), **Shooter** (for Alex Kite), **Dan** or **DTM** (for Daniel Kruszewski), **Rej** (spoken as Ray), **Saagar** (spoken as Sah-gur).
   - After introducing someone once, use their first name or nickname (`Nick`, `Dino`, `Blake`, `Lukose`, `Thor`, `Nael`, `Samran`) instead of repeating their full first+last name every turn!
4. CLEAN DIGITS & ZERO FORMATTING:
   - Write all scores, records, and percentages as clean digits (`111.5`, `88.2%`, `4-6`, `Week 10`)—NEVER spell numbers out in words!
   - Output ONLY `Chris: ...` and `Dave: ...` lines. Zero markdown (`**bold**`, `*italics*`), zero emojis, zero bracketed stage directions.
5. 100% FACTUAL ACCURACY & CALIBRATED DRAMA:
   - Stick strictly to verified scores, playoff odds, Discord quotes, and players in the source below—never invent stats or injuries, never say "Data Sheet", and frame mid-game Discord trash talk against how the game actually ended.
   - Match your dramatic framing to each team's ACTUAL playoff odds (e.g., don't say a loss "obliterated" a 6-4 team's playoff hopes if they still sit at 88% playoff odds—call it a missed chance at the #1 Bye or division lead!).
"""

    if length_mode == "sample":
        headline = _trim_matchup_for_script(pick_headline_matchup(sections["matchups"]))
        act1_snip = sections["act1"][:2500]
        prompt = f"""{base_rules}

TASK: Write a killer ~420–490 word (~2.5 minute) SAMPLE HIGHLIGHT SEGMENT (aim for 22–28 rapid-fire turns!):
- Punchy 15-second cold open ("Welcome back to the Week {week} Tuesday Morning Hangover, I'm Chris alongside Dave...") + quick 25-second debate on this week's trade (skip boring waiver cuts!).
- Dive straight into this headline matchup—don't announce "Matchup X of 8", just hook us with the best storyline, react to the funniest real Discord quotes & trash talk, debate the biggest player eruptions, and hit the playoff odds!
- Close with a brisk 1-line tease ("Stick around—we've got seven more matchups and the full playoff picture coming up next!").

--- ACT 1 (TRADES) ---
{act1_snip}

--- HEADLINE MATCHUP ---
{headline}
"""
        print("✍️  Writing 2–3 minute sample highlight script (Chris & Dave)...")
        raw_script = _call_gemini(prompt, api_key, response_mime="text/plain", thinking_budget=256, temperature=0.8)
        segments.append(("sample_highlight", _sanitize_script(raw_script)))
        return segments

    def _continuity_hint(prev_label: str) -> str:
        if not prev_label:
            return ""
        return (
            f"\nCONTINUITY: We just finished `{prev_label}`. Jump STRAIGHT into the new matchup/topic below—"
            f"do NOT re-introduce the show, do NOT echo or repeat the previous closing line, and NEVER use tired "
            f"cliché transitions like 'flip the page' or 'keep this train rolling'!"
        )

    matchups = sections["matchups"]
    trade_count = sections["act1"].count("#### 🔄 Trade")

    if length_mode == "deep":
        # DEEP-DIVE 30-40 MINUTE MODE: 10 rich segments (adaptive Act 1 so 1-trade weeks never stall!)
        total_segs = len(matchups) + 2
        if trade_count <= 1:
            act1_words = "180–240 words (10–14 snappy turns — keep it TIGHT and punchy! Once you finish grading the trade, IMMEDIATELY end the segment so we dive into Matchup 1—ZERO stalling or generic hype filler!)"
        else:
            act1_words = "340–440 words (18–24 turns — debate each trade and give letter grades, then hand off immediately with ZERO generic filler!)"

        p1 = f"""{base_rules}

TASK (SEGMENT 1 OF {total_segs} — COLD OPEN & TRADE DESK, {act1_words}):
- Energetic SportsCenter-style cold open welcoming BFL listeners to Week {week} of the Tuesday Morning Hangover (NO game score spoilers yet!).
- Debate this week's trade(s): break down BOTH sides' roster strategy and give quick letter grades. Skip minor waiver cuts unless hilarious.
- End crisply on a single short handoff line ("Alright, let's crack open our first game.") — do NOT pad extra filler lines!

--- ACT 1 ---
{sections['act1'][:4500]}
"""
        print(f"✍️  [1/{total_segs}] Writing Tight Cold Open & Trade Desk ({trade_count} trade(s) detected)...")
        segments.append(("seg01_intro_trades", _sanitize_script(_call_gemini(p1, api_key, thinking_budget=256, temperature=0.8))))
        prev_label = "the Cold Open & Trade Desk"

        for idx, m_block in enumerate(matchups, 1):
            seg_num = idx + 1
            trimmed_m = _trim_matchup_for_script(m_block, max_chars=9500)
            m_title = trimmed_m.splitlines()[0].replace("### 🏟️", "").strip() if trimmed_m else f"Matchup {idx}"
            prompt_m = f"""{base_rules}

TASK (SEGMENT {seg_num} OF {total_segs} — DEDICATED MATCHUP DEEP-DIVE #{idx} OF {len(matchups)}, ~650–740 words, 32–40 dynamic turns):
- Give THIS single matchup a rich, unhurried 3.5–4 minute sports-talk breakdown!{_continuity_hint(prev_label)}
- Vary your entry point: open directly with the wildest hook (a Live Game Desk shockwave, a hilarious Discord reverse-jinx, a costly start/sit blunder, an injury break, or a 18-year rivalry twist)—NEVER say "Matchup number {idx}".
- Weave in ALL the juicy layers organically through back-and-forth banter:
  * Exact Live Game Desk swings & play catalysts
  * The funniest real timestamped Discord quotes & third-party chirps
  * Top performers' Draft Pick vs. Reality steals/busts & 3-game NFL usage trends
  * Lineup blunders (natural ribbing!) OR in-game injury bad luck + Moving Forward roster depth fallout
  * All-time H2H rivalry context & updated Monte Carlo playoff odds!
- End cleanly on the final takeaway for THIS game (do NOT add a generic "let's keep this train rolling" or "let's flip the page" sign-off!).

--- MATCHUP SOURCE ---
{trimmed_m}
"""
            print(f"✍️  [{seg_num}/{total_segs}] Writing Dedicated Matchup Deep-Dive #{idx}/{len(matchups)}...")
            segments.append((f"seg{seg_num:02d}_matchup_{idx:02d}", _sanitize_script(_call_gemini(prompt_m, api_key, thinking_budget=256, temperature=0.8))))
            prev_label = m_title

        p_end = f"""{base_rules}

TASK (FINAL SEGMENT {total_segs} OF {total_segs} — STANDINGS, ALL 4 DIVISION RACES, PLAYOFF PICTURE & WEEK {week + 1} PREVIEW, ~720–820 words, 34–42 turns):
- Now that all 8 games are in the books, take a full 4–4.5 minutes to zoom out across the entire league!{_continuity_hint(prev_label)}
- Unpack ALL FOUR Division Races (North, South, East, West), the #1 Seed First-Round Bye battle, the 7-team Wild Card cutline, and the biggest Monte Carlo playoff risers/fallers.
- Preview the highest-leverage Week {week + 1} matchups (highlight the exact Win vs. Loss playoff leverage swings!) and make bold picks.
- Wrap with a memorable, funny sign-off ("Good luck on the waiver wire, BFL...").

--- ACT 3 & NEXT WEEK ---
{sections['act3'][:18000]}
"""
        print(f"✍️  [{total_segs}/{total_segs}] Writing Extended Standings, 4 Division Races & Week {week + 1} Preview...")
        segments.append((f"seg{total_segs:02d}_standings_outro", _sanitize_script(_call_gemini(p_end, api_key, thinking_budget=256, temperature=0.8))))
        return segments

    # STANDARD 16-20 MINUTE MODE: 6 tightly-paced segments (~450-540 words each)
    pairs = [matchups[i : i + 2] for i in range(0, len(matchups), 2)]
    total_segs = len(pairs) + 2

    p1 = f"""{base_rules}

TASK (SEGMENT 1 OF {total_segs} — COLD OPEN & TRADES, ~180–260 words, 10–14 rapid turns):
- Clean, energetic broadcast open welcoming listeners to Week {week} of the Tuesday Morning Hangover (NO game score spoilers yet!).
- Debate this week's trades and grade who won/lost the deal (skip minor cuts unless hilarious; ZERO generic filler!).
- End on one crisp handoff line into the first matchup.

--- ACT 1 ---
{sections['act1'][:4000]}
"""
    print(f"✍️  [1/{total_segs}] Writing Cold Open & Trades...")
    segments.append(("seg01_intro_trades", _sanitize_script(_call_gemini(p1, api_key, thinking_budget=256, temperature=0.8))))
    prev_label = "the Cold Open & Trade Desk"

    for idx, pair in enumerate(pairs, 1):
        seg_num = idx + 1
        trimmed_pair = "\n\n---\n\n".join(_trim_matchup_for_script(m, max_chars=6500) for m in pair)
        prompt_m = f"""{base_rules}

TASK (SEGMENT {seg_num} OF {total_segs} — MATCHUPS BREAKDOWN, ~460–540 words, 24–30 rapid turns):
- You're mid-show (NO "welcome back" intro—jump straight into the first matchup!).{_continuity_hint(prev_label)}
- Cover BOTH matchups organically—vary your entry point (lead with a wild Live Desk shockwave, a hilarious Discord reverse-jinx, an injury break, or a brutal start/sit blunder).
- Weave in real Discord quotes, draft steals/busts, and playoff odds through real back-and-forth banter. End cleanly on the final takeaway (no cliché transitions!).

--- MATCHUPS ---
{trimmed_pair}
"""
        print(f"✍️  [{seg_num}/{total_segs}] Writing Matchup Pair #{idx}...")
        segments.append((f"seg{seg_num:02d}_matchups_{idx}", _sanitize_script(_call_gemini(prompt_m, api_key, thinking_budget=256, temperature=0.8))))
        prev_label = f"Matchup Pair #{idx}"

    p_end = f"""{base_rules}

TASK (FINAL SEGMENT {total_segs} OF {total_segs} — STANDINGS, PLAYOFF PICTURE & WEEK {week + 1} PREVIEW, ~440–520 words, 22–28 rapid turns):
- Now that all 8 results are in, unpack the 4 Division Races, the 7-team Wild Card cutline, Monte Carlo playoff odds, and the highest-leverage Week {week + 1} matchups to circle on Thursday.{_continuity_hint(prev_label)}
- Wrap with a sharp, funny sign-off ("Good luck on waivers, BFL...").

--- ACT 3 & NEXT WEEK ---
{sections['act3'][:16000]}
"""
    print(f"✍️  [{total_segs}/{total_segs}] Writing Standings, Playoff Picture & Week {week + 1} Preview...")
    segments.append((f"seg{total_segs:02d}_standings_outro", _sanitize_script(_call_gemini(p_end, api_key, thinking_budget=256, temperature=0.8))))

    return segments


def _sanitize_script(raw_text: str) -> str:
    """Cleans markdown/stage artifacts and ensures clean `Chris:` / `Dave:` dialogue lines."""
    lines = []
    for line in raw_text.splitlines():
        clean = line.strip()
        if not clean:
            continue
        clean = clean.replace("**", "").replace("__", "").strip()
        clean = re.sub(r"^\*+|\*+$", "", clean).strip()
        clean = re.sub(r"^(Chris|Dave)\s*:", lambda m: f"{m.group(1).title()}:", clean, flags=re.IGNORECASE)
        if clean.startswith(("Chris:", "Dave:")):
            # Remove bracketed stage directions like [laughs] or [SFX: ...]
            clean = re.sub(r"\[[^\]]{1,40}\]", "", clean)
            clean = re.sub(r"\s{2,}", " ", clean).strip()
            lines.append(apply_phonetics(clean))
    return "\n".join(lines)


def split_script_into_tts_chunks(script_text: str, max_words: int = 620) -> List[str]:
    """
    Splits a long `Chris:` / `Dave:` script evenly across speaker turn boundaries
    so we never leave a tiny awkward tail chunk (which causes audible room-tone shifts).
    """
    turns = [ln.strip() for ln in script_text.splitlines() if ln.strip().startswith(("Chris:", "Dave:"))]
    if not turns:
        return []

    total_words = sum(len(t.split()) for t in turns)
    if total_words <= max_words:
        return ["\n".join(turns)]

    # Balance word counts evenly across the minimum number of chunks needed
    num_chunks = (total_words + max_words - 1) // max_words
    target_per_chunk = total_words / num_chunks

    chunks: List[str] = []
    curr: List[str] = []
    curr_words = 0
    for idx, t in enumerate(turns):
        wc = len(t.split())
        remaining_chunks = num_chunks - len(chunks)
        if curr and remaining_chunks > 1 and (curr_words + wc) > target_per_chunk and curr_words >= (target_per_chunk * 0.65):
            chunks.append("\n".join(curr))
            curr = [t]
            curr_words = wc
        else:
            curr.append(t)
            curr_words += wc
    if curr:
        chunks.append("\n".join(curr))
    return chunks


def synthesize_multispeaker_pcm(
    dialogue_text: str,
    *,
    api_key: str,
    chris_voice: str = DEFAULT_CHRIS_VOICE,
    dave_voice: str = DEFAULT_DAVE_VOICE,
    tts_models: Optional[List[str]] = None,
) -> Tuple[bytes, int]:
    """
    Calls Gemini Native Multi-Speaker TTS REST API (`generateContent` with `responseModalities: ["AUDIO"]`)
    and returns `(raw_pcm_bytes, sample_rate_hz)`.
    """
    env_tts = os.getenv("GEMINI_TTS_MODELS", "").strip()
    if tts_models:
        models = tts_models
    elif env_tts:
        models = [m.strip() for m in env_tts.split(",") if m.strip()]
    else:
        models = [
            "gemini-2.5-flash-preview-tts",
            "gemini-2.5-pro-preview-tts",
        ]

    prompt_text = f"{STYLE_DIRECTIVE}\n\n{dialogue_text}"
    payload = {
        "contents": [{"parts": [{"text": prompt_text}]}],
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": {
                "multiSpeakerVoiceConfig": {
                    "speakerVoiceConfigs": [
                        {
                            "speaker": "Chris",
                            "voiceConfig": {"prebuiltVoiceConfig": {"voiceName": chris_voice}},
                        },
                        {
                            "speaker": "Dave",
                            "voiceConfig": {"prebuiltVoiceConfig": {"voiceName": dave_voice}},
                        },
                    ]
                }
            },
        },
    }

    headers = {"x-goog-api-key": api_key, "Content-Type": "application/json"}
    errs = []
    for model in models:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        for attempt in range(3):
            try:
                resp = requests.post(url, headers=headers, json=payload, timeout=120)
                if resp.status_code == 200:
                    data = resp.json()
                    parts = data.get("candidates", [{}])[0].get("content", {}).get("parts", [])
                    for part in parts:
                        inline = part.get("inlineData") or {}
                        b64 = inline.get("data")
                        if b64:
                            mime = inline.get("mimeType", "audio/L16;codec=pcm;rate=24000")
                            rate_match = re.search(r"rate=(\d+)", mime)
                            sample_rate = int(rate_match.group(1)) if rate_match else 24000
                            return base64.b64decode(b64), sample_rate
                    errs.append(f"{model}: 200 OK but no inlineData audio returned")
                    break
                if resp.status_code in (429, 503) and attempt < 2:
                    wait_s = 4.0 if resp.status_code == 429 else 2.5
                    time.sleep(wait_s * (attempt + 1))
                    continue
                errs.append(f"{model} HTTP {resp.status_code}: {resp.text[:220]}")
                break
            except Exception as exc:
                if attempt < 2:
                    time.sleep(3.0 * (attempt + 1))
                    continue
                errs.append(f"{model}: {exc}")
                break

    raise RuntimeError("Gemini Native TTS failed: " + " | ".join(errs))


def pcm_chunks_to_wav(pcm_chunks: List[Tuple[bytes, int]], wav_path: Path, pause_ms: int = 450) -> Path:
    """Concatenates raw 16-bit mono PCM chunks with clean broadcast pauses into a WAV file."""
    if not pcm_chunks:
        raise ValueError("No PCM audio chunks provided.")
    sample_rate = pcm_chunks[0][1]
    silence_bytes = b"\x00\x00" * int(sample_rate * (pause_ms / 1000.0))

    wav_path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(wav_path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)  # 16-bit signed linear PCM
        wf.setframerate(sample_rate)
        for idx, (pcm_bytes, _) in enumerate(pcm_chunks):
            wf.writeframes(pcm_bytes)
            if idx < len(pcm_chunks) - 1:
                wf.writeframes(silence_bytes)
    return wav_path


def master_wav_to_mp3(wav_path: Path, mp3_path: Path, bitrate: str = "128k") -> Path:
    """Applies podcast loudness normalization (`loudnorm`) and encodes a broadcast MP3 via FFmpeg."""
    mp3_path.parent.mkdir(parents=True, exist_ok=True)
    if not shutil.which("ffmpeg"):
        raise RuntimeError("ffmpeg not found on PATH; cannot encode MP3.")

    cmd = [
        "ffmpeg", "-y",
        "-i", str(wav_path),
        "-af", "loudnorm=I=-16:TP=-1.5:LRA=11",
        "-c:a", "libmp3lame",
        "-b:a", bitrate,
        str(mp3_path),
    ]
    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if res.returncode != 0:
        # Retry without loudnorm if an older ffmpeg build lacks the filter
        cmd_plain = ["ffmpeg", "-y", "-i", str(wav_path), "-c:a", "libmp3lame", "-b:a", bitrate, str(mp3_path)]
        res2 = subprocess.run(cmd_plain, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if res2.returncode != 0:
            raise RuntimeError(f"ffmpeg encoding failed: {res2.stderr[:400]}")
    return mp3_path


def run_generator(
    season: int,
    week: int,
    *,
    length_mode: str = "sample",
    from_script: str = "",
    chris_voice: str = DEFAULT_CHRIS_VOICE,
    dave_voice: str = DEFAULT_DAVE_VOICE,
    update_rss: bool = False,
    rss_base_url: str = "",
    publish_discord: bool = False,
    fresh: bool = False,
) -> dict:
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY not found in .env.")

    tag = length_mode
    script_out = OUTPUT_DIR / f"gemini_script_week_{week}_{season}_{tag}.txt"
    mp3_out = OUTPUT_DIR / f"bfl_gemini_podcast_week_{week}_{season}_{tag}.mp3"
    work_dir = OUTPUT_DIR / "podcast_work" / f"week_{week}_{season}_{tag}"
    if fresh and work_dir.exists():
        shutil.rmtree(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)

    # For 30-40 min `deep` episodes, allow up to 850 words per single-segment TTS pass
    # so each of the 10 segments renders in ONE continuous pass (10 TTS calls total, zero mid-matchup seam!)
    chunk_word_cap = 850 if length_mode == "deep" else 650

    if from_script:
        script_text = Path(from_script).read_text(encoding="utf-8")
        tts_chunks = split_script_into_tts_chunks(script_text, max_words=chunk_word_cap)
        print(f"📄 Loaded existing script ({len(tts_chunks)} TTS chunk(s)): {from_script}")
    else:
        payload_path = OUTPUT_DIR / f"notebook_payload_week_{week}_{season}.md"
        if not payload_path.exists():
            raise FileNotFoundError(
                f"Weekly Scoreboard payload not found at {payload_path}. "
                f"Run `python3 FantasyRecap/generate_notebook_payload.py --season {season} --week {week}` first."
            )
        cached_segments_file = work_dir / "segments.json"
        if not fresh and length_mode != "sample" and cached_segments_file.exists():
            segments = [tuple(x) for x in json.loads(cached_segments_file.read_text(encoding="utf-8"))]
            print(f"♻️  Resuming from cached script segments ({len(segments)} segments): {cached_segments_file}")
        else:
            payload_md = payload_path.read_text(encoding="utf-8")
            segments = generate_dialogue_script(payload_md, season, week, api_key=api_key, length_mode=length_mode)
            cached_segments_file.write_text(json.dumps(segments, indent=2), encoding="utf-8")
            full_script = "\n\n".join(text for _, text in segments if text.strip())
            script_out.write_text(full_script + "\n", encoding="utf-8")
            total_w = sum(len(t.split()) for _, t in segments)
            print(f"💾 Saved dialogue script ({total_w:,} words • ~{total_w / 195:.1f} min est): {script_out}")

        tts_chunks = []
        for _, seg_text in segments:
            tts_chunks.extend(split_script_into_tts_chunks(seg_text, max_words=chunk_word_cap))

    if not tts_chunks:
        raise RuntimeError("No valid `Chris:` / `Dave:` dialogue lines found to synthesize.")

    pcm_results: List[Tuple[bytes, int]] = []
    for idx, chunk in enumerate(tts_chunks, 1):
        wc = len(chunk.split())
        cache_pcm = work_dir / f"chunk_{idx:02d}_{chris_voice}_{dave_voice}.pcm"
        if not fresh and length_mode != "sample" and cache_pcm.exists() and cache_pcm.stat().st_size > 1000:
            pcm_bytes = cache_pcm.read_bytes()
            sr = 24000
            secs = len(pcm_bytes) / (sr * 2)
            print(f"♻️  Using cached TTS chunk {idx}/{len(tts_chunks)} ({secs:.1f}s)")
            pcm_results.append((pcm_bytes, sr))
            continue

        print(f"🎙️  Rendering TTS chunk {idx}/{len(tts_chunks)} ({wc} words • Chris={chris_voice}, Dave={dave_voice})...")
        t0 = time.time()
        pcm_bytes, sr = synthesize_multispeaker_pcm(
            chunk,
            api_key=api_key,
            chris_voice=chris_voice,
            dave_voice=dave_voice,
        )
        cache_pcm.write_bytes(pcm_bytes)
        secs = len(pcm_bytes) / (sr * 2)
        print(f"   ✅ Rendered {secs:.1f}s of audio in {time.time() - t0:.1f}s")
        pcm_results.append((pcm_bytes, sr))

        # Polite pacing between TTS chunks on multi-chunk episodes to avoid bursting RPM limits
        if idx < len(tts_chunks):
            time.sleep(2.0)

    with tempfile.TemporaryDirectory() as tmpdir:
        wav_tmp = Path(tmpdir) / "episode.wav"
        pcm_chunks_to_wav(pcm_results, wav_tmp, pause_ms=650 if length_mode != "sample" else 450)
        master_wav_to_mp3(wav_tmp, mp3_out, bitrate="128k")

    total_audio_sec = sum(len(b) / (sr * 2) for b, sr in pcm_results)
    size_mb = round(mp3_out.stat().st_size / (1024 * 1024), 2)
    mins, secs = int(total_audio_sec // 60), int(total_audio_sec % 60)
    print(f"\n🎉 DONE! Mastered MP3: {mp3_out} ({mins}m {secs}s • {size_mb} MB @ 128 kbps)")

    rss_info = None
    if update_rss or rss_base_url:
        rss_info = add_episode_to_rss(
            season=season,
            week=week,
            audio_path=str(mp3_out),
            title=f"Week {week} ({season}) — BFL Tuesday Morning Hangover",
            notes=f"Automated Gemini Native TTS broadcast (Chris & Dave) for BFL Week {week}.",
            episode_type="gemini",
            base_url=rss_base_url,
        )
        print(f"📡 Private Apple Podcasts RSS updated: {rss_info['feed_path']}")

    if publish_discord:
        from FantasyRecap.publish_notebook_audio import publish_notebook_audio
        publish_notebook_audio(
            season=season,
            week_num=week,
            audio_path=str(mp3_out),
            notes="Automated Gemini Native TTS Edition (Chris & Dave).",
            update_rss=False,
        )

    return {
        "script_path": str(script_out),
        "mp3_path": str(mp3_out),
        "duration_sec": round(total_audio_sec, 1),
        "size_mb": size_mb,
        "rss": rss_info,
    }


def main():
    parser = argparse.ArgumentParser(description="Generate BFL Chris & Dave Podcast via Gemini Native Multi-Speaker TTS")
    parser.add_argument("--season", type=int, default=int(CURRENT_YEAR))
    parser.add_argument("--week", type=int, default=10)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--sample", action="store_true", help="Generate a fast ~2 minute highlight clip")
    mode.add_argument("--full", action="store_true", help="Generate a standard ~16-20 minute episode")
    mode.add_argument("--deep", action="store_true", help="Generate a full ~30-40 minute deep-dive episode (1 dedicated segment per matchup!)")
    parser.add_argument("--length", choices=["sample", "standard", "deep"], default="", help="Explicit length mode (alternative to --sample/--full/--deep)")
    parser.add_argument("--fresh", action="store_true", help="Ignore cached script/audio chunks and regenerate from scratch")
    parser.add_argument("--from-script", type=str, default="", help="Skip script generation and render audio from an existing script file")
    parser.add_argument("--chris-voice", type=str, default=DEFAULT_CHRIS_VOICE, help=f"Gemini voice for Chris (default: {DEFAULT_CHRIS_VOICE})")
    parser.add_argument("--dave-voice", type=str, default=DEFAULT_DAVE_VOICE, help=f"Gemini voice for Dave (default: {DEFAULT_DAVE_VOICE})")
    parser.add_argument("--rss", action="store_true", help="Also add episode to Private Podcast RSS (`output/rss/feed.xml`)")
    parser.add_argument("--rss-base-url", type=str, default="", help="Hosted HTTPS base URL for the RSS feed")
    parser.add_argument("--publish-discord", action="store_true", help="Also upload to Discord #press-room-podcast")
    args = parser.parse_args()

    if args.length:
        length_mode = args.length
    elif args.deep:
        length_mode = "deep"
    elif args.full:
        length_mode = "standard"
    else:
        length_mode = "sample"

    run_generator(
        season=args.season,
        week=args.week,
        length_mode=length_mode,
        from_script=args.from_script,
        chris_voice=args.chris_voice,
        dave_voice=args.dave_voice,
        update_rss=args.rss,
        rss_base_url=args.rss_base_url,
        publish_discord=args.publish_discord,
        fresh=args.fresh,
    )


if __name__ == "__main__":
    main()
