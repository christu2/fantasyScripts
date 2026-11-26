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
import io
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
from typing import Dict, List, Optional, Tuple
import numpy as np
import requests

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from FantasyRecap.core.manifest import load_project_env
load_project_env()

from FantasyRecap.bfl_history_db import _call_gemini
from FantasyRecap.config import CURRENT_YEAR
from FantasyRecap.podcast_packaging import get_packaging_assets
from FantasyRecap.podcast_rss_builder import add_episode_to_rss

OUTPUT_DIR = SCRIPT_DIR / "output"
OUTPUT_DIR.mkdir(exist_ok=True)

# Phonetic replacements applied ONLY to the spoken TTS script so hosts nail BFL names every time.
# Note: "AMO" is intentionally left untouched because "Ammo" is the league's preferred pronunciation.
PHONETIC_REPLACEMENTS = [
    (r"\brej hoxha\b", "Ray Hozha"),
    (r"\brej\b", "Ray"),
    (r"\bSaagar\b", "Sah-gar"),
    (r"\bDino\b", "Dee-no"),
    (r"\bLukose\b", "Luke-ose"),
    (r"\bMykonos\b", "Mee-ko-nos"),
    (r"\bNael\b", "Nyle"),
    (r"\bSamran\b", "Sum-rahn"),
    (r"\bKruszewski\b", "Kroo-sheff-skee"),
    (r"\bJabroni\b", "Jah-bronee"),
    (r"\bSkattebo\b", "Skat-uh-bo"),
    (r"\bPerine\b", "Pee-rine"),
    (r"\bShough\b", "Shuck"),
    (r"\bJeanty\b", "Jen-tee"),
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


def format_spoken_records(script_text: str) -> str:
    """
    Rewrites hyphenated Win-Loss & H2H series records (`6-5`, `10-1`, `12-10`) into
    natural spoken English (`6 and 5`, `10 and 1`, `12 and 10`) so Gemini TTS never
    reads a team record as "six five". Carefully ignores NFL game scores (`27-24`),
    dates (`11-23`), hyphenated words (`3-for-1`, `four-to-five`, `B-plus`), and
    range phrases (`Weeks 1-11`).
    """
    out = script_text

    def _is_valid_record(w_s: str, l_s: str, *, allow_all_time: bool = False) -> bool:
        w, l = int(w_s), int(l_s)
        if allow_all_time:
            return 0 <= w <= 35 and 0 <= l <= 35 and (w + l) >= 1
        # Regular season + playoffs in BFL (0..17 games); excludes NFL scores like 27-24 or 34-7
        return 0 <= w <= 17 and 0 <= l <= 17 and 1 <= (w + l) <= 17

    # 1. Explicit W-L / H2H series cue BEFORE the hyphenated pair (e.g. "sitting at 6-5", "drops to 2-9", "leads 12-10")
    pre_cue_re = re.compile(
        r"\b("
        r"at|to|now|is|was|are|were|being|sitting at|sits at|entered at|entering at|entered|"
        r"came in at|coming in at|came in|went in at|drops to|dropped to|falls to|fell to|"
        r"moves to|moved to|improves to|improved to|jumps to|climbs to|slips to|slipped to|"
        r"tumbles to|slides to|stands at|tied at|deadlocked at|stuck at|record of|record at|"
        r"all-time series|series|all-time|leads|trails|split|splits|splitting|a|an|that|their|his|her"
        r")\s+(\d{1,2})-(\d{1,2})\b(?!\s*(?:loss|win|victory|defeat|blowout|thriller|ballgame|game|points?|pts|yard|yd|for|to|year|month|day|week))",
        flags=re.IGNORECASE,
    )

    def _repl_pre(m: re.Match) -> str:
        prefix, w_s, l_s = m.group(1), m.group(2), m.group(3)
        allow_h2h = any(k in prefix.lower() for k in ("series", "all-time", "leads", "trails", "split"))
        if _is_valid_record(w_s, l_s, allow_all_time=allow_h2h):
            return f"{prefix} {w_s} and {l_s}"
        return m.group(0)

    out = pre_cue_re.sub(_repl_pre, out)

    # 2. Explicit W-L / series cue AFTER the hyphenated pair (e.g. "6-5 record", "5-6 team", "12-10 all-time")
    post_cue_re = re.compile(
        r"\b(\d{1,2})-(\d{1,2})(\s+(?:record|overall|on the year|on the season|in the|entering|heading into|"
        r"team|squad|club|start|mark|all-time|advantage|lead|split|h2h|head-to-head))\b",
        flags=re.IGNORECASE,
    )

    def _repl_post(m: re.Match) -> str:
        w_s, l_s, suffix = m.group(1), m.group(2), m.group(3)
        allow_h2h = any(k in suffix.lower() for k in ("all-time", "advantage", "lead", "split", "h2h", "head-to-head"))
        if _is_valid_record(w_s, l_s, allow_all_time=allow_h2h):
            return f"{w_s} and {l_s}{suffix}"
        return m.group(0)

    out = post_cue_re.sub(_repl_post, out)

    # 3. Parenthetical season record like "(6-5)" or "(10-1)"
    paren_re = re.compile(r"\((\d{1,2})-(\d{1,2})\)")

    def _repl_paren(m: re.Match) -> str:
        w_s, l_s = m.group(1), m.group(2)
        if _is_valid_record(w_s, l_s, allow_all_time=False):
            return f"({w_s} and {l_s})"
        return m.group(0)

    out = paren_re.sub(_repl_paren, out)
    return out


def apply_phonetics(script_text: str) -> str:
    """Applies spoken W-L records, BFL pronunciation spellings, and natural contractions for clean TTS delivery."""
    out = script_text
    # Clean stray punctuation / duplicate unit typos like "9. points" -> "9 points" or "point points" -> "points"
    out = re.sub(r"\b(\d+)\.\s+(points?)\b", r"\1 \2", out, flags=re.IGNORECASE)
    out = re.sub(r"\bpoint\s+points\b", "points", out, flags=re.IGNORECASE)
    out = format_spoken_records(out)
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
    work_dir: Optional[Path] = None,
    fresh: bool = False,
) -> List[Tuple[str, str]]:
    """
    Uses Gemini Flash to write a punchy `Chris:` / `Dave:` dialogue script.
    `length_mode`:
      - 'sample'   : ~2–3 min highlight clip (1 segment)
      - 'standard' : ~16–20 min episode (6 segments: intro + 4 matchup pairs + standings)
      - 'deep'     : ~30–40 min deep-dive episode (10 segments: intro + 1 dedicated segment per matchup + extended standings/preview)
    Checkpoints each generated segment to `work_dir` so transient 503s never lose progress.
    """
    sections = extract_scoreboard_sections(payload_md)
    segments: List[Tuple[str, str]] = []

    def _get_or_write_segment(seg_name: str, label: str, prompt: str) -> str:
        cache_file = (work_dir / f"{seg_name}.txt") if (work_dir and length_mode != "sample") else None
        if cache_file and not fresh and cache_file.exists():
            txt = cache_file.read_text(encoding="utf-8").strip()
            if txt:
                print(f"♻️  Using cached script segment {label} ({len(txt.split())} words)")
                return txt
        print(f"✍️  {label}...")
        raw = _call_gemini(prompt, api_key, response_mime="text/plain", temperature=0.8, timeout=40, max_rounds=3)
        cleaned = _sanitize_script(raw)
        if cache_file and cleaned:
            cache_file.write_text(cleaned + "\n", encoding="utf-8")
        return cleaned

    base_rules = f"""You are the head writer for "Tuesday Morning Hangover", the Beasts Football League (BFL) weekly fantasy football podcast (Season {season}, Week {week}).
Write ONLY spoken dialogue alternating strictly between two hosts labeled `Chris:` and `Dave:`.

CRITICAL WRITING RULES FOR LIFELIKE SPORTS-RADIO BANTER:
1. REAL PING-PONG CHEMISTRY & NATURAL RHYTHM (NOT CHRONOLOGICAL RECITATION):
   - Write like two sharp buddies actually riffing—NOT taking turns reading box scores!
   - Mix **short 4–12 word reactions, questions, pushback, and disbelief** ("Wait—he actually posted that in Discord?!", "That's an all-time self-own.", "Hold on, don't gloss over that!") with punchy 1–2 sentence takes AND an occasional passionate 2–3 sentence **soapbox rant** when a storyline truly deserves it so the pacing breathes naturally.
   - Vary your conversational starters—do NOT overuse `"Bro"` (use it at most once per segment, if at all).
   - NEVER say "Matchup 7 of 8 featured..." or read boring lists of minor waiver cuts—lead straight with the drama!
2. NATURAL CONTRACTIONS & SPOKEN ENGLISH ONLY:
   - ALWAYS use contractions (`I'm`, `we're`, `let's`, `it's`, `that's`, `we've`, `didn't`, `can't`, `he's`, `you're`, `what's`).
   - NEVER write stiff formal phrases like `I am`, `let us`, `we are`, `we have got`, `it is`, `that is`.
   - NEVER write literal `"Haha"`, `"Hehe"`, or `"Lol"` (TTS reads "ha-ha" awkwardly)—use real spoken reactions like `"Oh man, hold on—"`, `"Wait a second—"`, `"No chance!"`.
3. USE LEAGUE NICKNAMES & FIRST NAMES LIKE INSIDERS:
   - Use **Thor** (for Shawn Ullenbrauck), **Lukose** (for Shawn Lukose), **AMO** (for Adam Olen, pronounced Ammo), **Shooter** (for Alex Kite), **Dan** or **DTM** (for Daniel Kruszewski), **Rej** (spoken as Ray), **Saagar** (spoken as Sah-gar), **Dino** (spoken as Dee-no).
   - After introducing someone once, use their first name or nickname (`Nick`, `Dino`, `Blake`, `Lukose`, `Thor`, `Nael`, `Samran`) instead of repeating their full first+last name every turn!
4. CLEAN DIGITS, SPOKEN RECORDS & ZERO FORMATTING:
   - Write scores and percentages as clean digits (`111.5`, `88.2%`, `Week {week}`)—NEVER spell scores out in words!
   - **ALWAYS write Win-Loss records and all-time H2H series records with the word `"and"` (`6 and 5`, `4 and 6`, `10 and 1`, `12 and 10`)—NEVER hyphenated (`6-5`)**, so the hosts always say "six and five" instead of "six five"!
   - Output ONLY `Chris: ...` and `Dave: ...` lines. Zero markdown (`**bold**`, `*italics*`), zero emojis, zero bracketed stage directions.
5. ZERO RECORD SPOILERS & ENTERING-THE-WEEK FRAMING:
   - **NEVER spoil post-week records early!** When discussing trades in Act 1, ONLY refer to each manager's **entering-Week-{week}** record (before this week's game was played).
   - When introducing any matchup in Act 2, frame both teams with the records they held **entering Week {week}** (e.g., "Alex came in at 5 and 5"), and ONLY state their updated post-week record ("moves to 6 and 5") AFTER revealing how the game played out!
6. 100% FACTUAL ACCURACY & CALIBRATED DRAMA:
   - Stick strictly to verified scores, playoff odds, Discord quotes, and players in the source below—never invent stats or injuries, never say "Data Sheet", and frame mid-game Discord trash talk against how the game actually ended.
   - Match your dramatic framing to each team's ACTUAL playoff odds (e.g., don't say a loss "obliterated" a 6 and 4 team's playoff hopes if they still sit at 88% playoff odds—call it a missed chance at the #1 Bye or division lead!).
"""

    if length_mode == "sample":
        headline = _trim_matchup_for_script(pick_headline_matchup(sections["matchups"]))
        act1_snip = sections["act1"][:3000]
        p_s1 = f"""{base_rules}

TASK (SAMPLE PART 1 OF 2 — COLD OPEN & TRADE DESK, ~130–170 words, 8–10 snappy turns):
- Energetic SportsCenter-style cold open welcoming BFL listeners to Week {week} of the Tuesday Morning Hangover (NO game score or post-week record spoilers yet!).
- Quickly break down this week's trade using ONLY each team's **entering-Week-{week}** record, explain the REAL roster/injury crunch behind each side, and calibrate your tone strictly to `Trade Magnitude` (never call a `Depth / Role-Player Swap` a "blockbuster"!).
- End cleanly on one crisp handoff line into our headline game ("Alright, let's jump straight into our marquee matchup of the week.").

--- ACT 1 (TRADES) ---
{act1_snip}
"""
        p_s2 = f"""{base_rules}

TASK (SAMPLE PART 2 OF 2 — HEADLINE MATCHUP SHOWCASE, ~340–400 words, 18–24 dynamic turns):
- Jump STRAIGHT into this headline matchup after the act bumper—do NOT re-introduce the show and do NOT say "Matchup X of 8".
- Frame both managers with their **entering-Week-{week}** records before revealing the final score and updated records!
- React to the funniest real Discord quotes & trash talk, debate the biggest player eruptions / draft steals, and hit the updated playoff odds.
- Close with a brisk 1-line broadcast tease ("Stick around—we've got seven more matchups and the full playoff picture coming up next on the Hangover!").

--- HEADLINE MATCHUP ---
{headline}
"""
        print("✍️  [1/2] Writing Sample Cold Open & Trade Desk (Chris & Dave)...")
        s1 = _sanitize_script(_call_gemini(p_s1, api_key, response_mime="text/plain", temperature=0.8))
        print("✍️  [2/2] Writing Sample Headline Matchup Showcase (Chris & Dave)...")
        s2 = _sanitize_script(_call_gemini(p_s2, api_key, response_mime="text/plain", temperature=0.8))
        segments.append(("sample_intro_trades", s1))
        segments.append(("sample_headline_matchup", s2))
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
- Energetic SportsCenter-style cold open welcoming BFL listeners to Week {week} of the Tuesday Morning Hangover (NO game score or post-week record spoilers yet!).
- Debate this week's trade(s): break down BOTH sides' roster strategy using ONLY their **entering-Week-{week}** records (NEVER mention their post-Week-{week} records here!), explain the REAL positional/injury/bye-week crunch behind each manager's move, and calibrate your tone strictly to `Trade Magnitude` (if it's a `Depth / Role-Player Swap`, treat it as a savvy, practical spot-start/depth move—NEVER call a role-player swap a "blockbuster"!). Skip minor waiver cuts unless hilarious.
- End crisply on a single short handoff line ("Alright, let's crack open our first game.") — do NOT pad extra filler lines!

--- ACT 1 ---
{sections['act1'][:4500]}
"""
        segments.append(("seg01_intro_trades", _get_or_write_segment("seg01_intro_trades", f"[1/{total_segs}] Writing Tight Cold Open & Trade Desk ({trade_count} trade(s) detected)", p1)))
        prev_label = "the Cold Open & Trade Desk"

        for idx, m_block in enumerate(matchups, 1):
            seg_num = idx + 1
            trimmed_m = _trim_matchup_for_script(m_block, max_chars=9500)
            m_title = trimmed_m.splitlines()[0].replace("### 🏟️", "").strip() if trimmed_m else f"Matchup {idx}"
            prompt_m = f"""{base_rules}

TASK (SEGMENT {seg_num} OF {total_segs} — DEDICATED MATCHUP DEEP-DIVE #{idx} OF {len(matchups)}, ~650–740 words, 32–40 dynamic turns):
- Give THIS single matchup a rich, unhurried 3.5–4 minute sports-talk breakdown!{_continuity_hint(prev_label)}
- Vary your entry point: open directly with the wildest hook (a Live Game Desk shockwave, a hilarious Discord reverse-jinx, a costly start/sit blunder, an injury break, or an 18-year rivalry twist)—NEVER say "Matchup number {idx}".
- Set up the stakes using the records both managers had **entering Week {week}** before revealing their updated post-game records!
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
            seg_key = f"seg{seg_num:02d}_matchup_{idx:02d}"
            segments.append((seg_key, _get_or_write_segment(seg_key, f"[{seg_num}/{total_segs}] Writing Dedicated Matchup Deep-Dive #{idx}/{len(matchups)}", prompt_m)))
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
        end_key = f"seg{total_segs:02d}_standings_outro"
        segments.append((end_key, _get_or_write_segment(end_key, f"[{total_segs}/{total_segs}] Writing Extended Standings, 4 Division Races & Week {week + 1} Preview", p_end)))
        return segments

    # STANDARD 16-20 MINUTE MODE: 6 tightly-paced segments (~450-540 words each)
    pairs = [matchups[i : i + 2] for i in range(0, len(matchups), 2)]
    total_segs = len(pairs) + 2

    p1 = f"""{base_rules}

TASK (SEGMENT 1 OF {total_segs} — COLD OPEN & TRADES, ~180–260 words, 10–14 rapid turns):
- Clean, energetic broadcast open welcoming listeners to Week {week} of the Tuesday Morning Hangover (NO game score or post-week record spoilers yet!).
- Debate this week's trades using ONLY each team's **entering-Week-{week}** record, highlight the real roster/injury need behind each move, and calibrate your tone strictly to `Trade Magnitude` (never hype a `Depth / Role-Player Swap` as a "blockbuster"!). Skip minor cuts unless hilarious; ZERO generic filler!
- End on one crisp handoff line into the first matchup.

--- ACT 1 ---
{sections['act1'][:4000]}
"""
    print(f"✍️  [1/{total_segs}] Writing Cold Open & Trades...")
    segments.append(("seg01_intro_trades", _sanitize_script(_call_gemini(p1, api_key, response_mime="text/plain", temperature=0.8))))
    prev_label = "the Cold Open & Trade Desk"

    for idx, pair in enumerate(pairs, 1):
        seg_num = idx + 1
        trimmed_pair = "\n\n---\n\n".join(_trim_matchup_for_script(m, max_chars=6500) for m in pair)
        prompt_m = f"""{base_rules}

TASK (SEGMENT {seg_num} OF {total_segs} — MATCHUPS BREAKDOWN, ~460–540 words, 24–30 rapid turns):
- You're mid-show (NO "welcome back" intro—jump straight into the first matchup!).{_continuity_hint(prev_label)}
- Cover BOTH matchups organically—set up each game using the teams' **entering-Week-{week}** records before revealing updated records, and vary your entry point (lead with a wild Live Desk shockwave, a hilarious Discord reverse-jinx, an injury break, or a brutal start/sit blunder).
- Weave in real Discord quotes, draft steals/busts, and playoff odds through real back-and-forth banter. End cleanly on the final takeaway (no cliché transitions!).

--- MATCHUPS ---
{trimmed_pair}
"""
        print(f"✍️  [{seg_num}/{total_segs}] Writing Matchup Pair #{idx}...")
        segments.append((f"seg{seg_num:02d}_matchups_{idx}", _sanitize_script(_call_gemini(prompt_m, api_key, response_mime="text/plain", temperature=0.8))))
        prev_label = f"Matchup Pair #{idx}"

    p_end = f"""{base_rules}

TASK (FINAL SEGMENT {total_segs} OF {total_segs} — STANDINGS, PLAYOFF PICTURE & WEEK {week + 1} PREVIEW, ~440–520 words, 22–28 rapid turns):
- Now that all 8 results are in, unpack the 4 Division Races, the 7-team Wild Card cutline, Monte Carlo playoff odds, and the highest-leverage Week {week + 1} matchups to circle on Thursday.{_continuity_hint(prev_label)}
- Wrap with a sharp, funny sign-off ("Good luck on waivers, BFL...").

--- ACT 3 & NEXT WEEK ---
{sections['act3'][:16000]}
"""
    print(f"✍️  [{total_segs}/{total_segs}] Writing Standings, Playoff Picture & Week {week + 1} Preview...")
    segments.append((f"seg{total_segs:02d}_standings_outro", _sanitize_script(_call_gemini(p_end, api_key, response_mime="text/plain", temperature=0.8))))

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
    # Guard against LLM output truncation mid-sentence: drop any final line lacking terminal punctuation
    while len(lines) > 1 and not re.search(r"[.!?\"'”’]\s*$", lines[-1]):
        lines.pop()
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


def _build_tts_payload(model: str, dialogue_text: str, chris_voice: str, dave_voice: str) -> dict:
    """
    Builds the exact REST payload for either:
      - `gemini-3.8-flash*-tts` (structured per-turn `speechMetadata.speaker` parts), OR
      - `gemini-2.5-flash-preview-tts` / `gemini-3.1-flash-tts-preview` (classic transcript + `prebuiltVoiceConfig`).
    """
    if "3.8-flash" in model:
        parts = []
        for ln in dialogue_text.splitlines():
            clean = ln.strip()
            if clean.startswith("Chris:"):
                parts.append({
                    "text": clean[len("Chris:"):].strip(),
                    "speechMetadata": {"speaker": "Chris", "style": "lively, warm sports-talk podcast host"},
                })
            elif clean.startswith("Dave:"):
                parts.append({
                    "text": clean[len("Dave:"):].strip(),
                    "speechMetadata": {"speaker": "Dave", "style": "sharp, punchy, playful sports-talk co-host"},
                })
        return {
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": {
                "responseModalities": ["AUDIO"],
                "speechConfig": {
                    "multiSpeakerVoiceConfig": {
                        "speakerVoiceConfigs": [
                            {"speaker": "Chris", "voiceConfig": {"prebuiltVoiceConfig": {"voiceName": chris_voice}}},
                            {"speaker": "Dave", "voiceConfig": {"prebuiltVoiceConfig": {"voiceName": dave_voice}}},
                        ]
                    }
                },
            },
        }

    prompt_text = f"{STYLE_DIRECTIVE}\n\n{dialogue_text}"
    return {
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
        # Verified live free-tier multi-speaker TTS models (separate RPD pools!).
        # `gemini-3.8-flash-tts` is primary: ~14 dB cleaner noise floor (-50 dB vs -35 dB)
        # and eliminates the "old record player" cold-start warble seen on 2.5-flash-preview-tts.
        models = [
            "gemini-3.8-flash-tts",
            "gemini-3.8-flash-lite-tts",
            "gemini-2.5-flash-preview-tts",
            "gemini-3.1-flash-tts-preview",
        ]

    headers = {"x-goog-api-key": api_key, "Content-Type": "application/json"}
    errs = []
    for model in models:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        payload = _build_tts_payload(model, dialogue_text, chris_voice, dave_voice)
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
                            return _unwrap_wav_if_needed(base64.b64decode(b64), fallback_sr=sample_rate)
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


def _unwrap_wav_if_needed(raw_bytes: bytes, fallback_sr: int = 24000) -> Tuple[bytes, int]:
    """
    `gemini-3.8-flash-tts` returns a complete RIFF/WAVE container (including a
    trailing IPTC/C2PA metadata chunk after the audio `data` chunk), whereas
    `gemini-2.5-flash-preview-tts` returns raw headerless 16-bit PCM.
    Unwrapping RIFF/WAVE via `wave.open()` extracts ONLY the exact audio samples,
    completely eliminating the ~160ms trailing static pop!
    """
    if len(raw_bytes) >= 44 and raw_bytes[:4] == b"RIFF" and raw_bytes[8:12] == b"WAVE":
        try:
            with wave.open(io.BytesIO(raw_bytes), "rb") as wf:
                sr = wf.getframerate() or fallback_sr
                pcm = wf.readframes(wf.getnframes())
                if pcm:
                    return pcm, sr
        except Exception:
            pass
    return raw_bytes, fallback_sr


def _clean_pcm_chunk(
    pcm_bytes: bytes,
    sample_rate: int = 24000,
    fade_in_ms: int = 12,
    fade_out_ms: int = 25,
) -> bytes:
    """
    Conditions a 16-bit mono PCM TTS chunk:
    - Unwraps RIFF/WAVE header & trailing metadata chunks if present
    - Removes DC offset
    - Applies smooth cosine fade-in / fade-out so chunk edges never click
    """
    pcm_bytes, sample_rate = _unwrap_wav_if_needed(pcm_bytes, fallback_sr=sample_rate)
    if len(pcm_bytes) < 4:
        return pcm_bytes
    usable = (len(pcm_bytes) // 2) * 2
    arr = np.frombuffer(pcm_bytes[:usable], dtype=np.int16).astype(np.float32)
    if len(arr) == 0:
        return pcm_bytes

    # Remove DC offset
    arr = arr - float(np.mean(arr))

    # Smooth half-cosine edge fades
    fi = min(int(sample_rate * (fade_in_ms / 1000.0)), len(arr) // 4)
    fo = min(int(sample_rate * (fade_out_ms / 1000.0)), len(arr) // 4)
    if fi > 1:
        t_in = np.linspace(0.0, np.pi / 2.0, fi, dtype=np.float32)
        arr[:fi] *= np.sin(t_in) ** 2
    if fo > 1:
        t_out = np.linspace(0.0, np.pi / 2.0, fo, dtype=np.float32)
        arr[-fo:] *= np.cos(t_out) ** 2

    return np.clip(arr, -32768.0, 32767.0).astype(np.int16).tobytes()


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
            wf.writeframes(_clean_pcm_chunk(pcm_bytes, sample_rate=sample_rate))
            if idx < len(pcm_chunks) - 1:
                wf.writeframes(silence_bytes)
    return wav_path


def _two_pass_linear_normalize_wav(
    in_wav: Path,
    out_wav: Path,
    *,
    target_i: float = -16.0,
    target_tp: float = -1.5,
    target_lra: float = 11.0,
    sample_rate: int = 44100,
    channels: int = 2,
    denoise: bool = False,
) -> Path:
    """
    Applies broadcast-grade TWO-PASS LINEAR `loudnorm` via FFmpeg (`linear=true`).
    Unlike single-pass dynamic `loudnorm` (which pumps gain on breaths/hiss and can
    cause vinyl-like warble), two-pass linear normalization measures the exact LUFS
    first and applies one clean constant gain + true-peak ceiling.
    """
    pre_chain = "highpass=f=65" + (",afftdn=nf=-28" if denoise else "")
    pass1_cmd = [
        "ffmpeg", "-y", "-i", str(in_wav),
        "-af", f"{pre_chain},loudnorm=I={target_i}:TP={target_tp}:LRA={target_lra}:print_format=json",
        "-f", "null", "-",
    ]
    p1 = subprocess.run(pass1_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    measured = None
    if p1.returncode == 0:
        m = re.search(r"\{\s*\"input_i\".*?\}", p1.stderr, flags=re.S)
        if m:
            try:
                measured = json.loads(m.group(0))
            except Exception:
                measured = None

    if measured and measured.get("input_i") not in (None, "-inf"):
        ln_filter = (
            f"{pre_chain},"
            f"loudnorm=I={target_i}:TP={target_tp}:LRA={target_lra}"
            f":measured_I={measured['input_i']}"
            f":measured_LRA={measured['input_lra']}"
            f":measured_TP={measured['input_tp']}"
            f":measured_thresh={measured['input_thresh']}"
            f":offset={measured.get('target_offset', '0.0')}"
            f":linear=true"
        )
    else:
        ln_filter = pre_chain

    pass2_cmd = [
        "ffmpeg", "-y", "-i", str(in_wav),
        "-af", ln_filter,
        "-ar", str(sample_rate),
        "-ac", str(channels),
        "-c:a", "pcm_s16le",
        str(out_wav),
    ]
    p2 = subprocess.run(pass2_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if p2.returncode != 0:
        # Clean fallback without loudnorm if needed
        fallback = [
            "ffmpeg", "-y", "-i", str(in_wav),
            "-ar", str(sample_rate), "-ac", str(channels), "-c:a", "pcm_s16le", str(out_wav),
        ]
        p3 = subprocess.run(fallback, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if p3.returncode != 0:
            raise RuntimeError(f"ffmpeg normalization failed: {p3.stderr[:400]}")
    return out_wav


def _load_audio_as_stereo_f32(audio_path: Path, sample_rate: int = 44100) -> np.ndarray:
    """Loads any `.wav`/`.mp3`/`.m4a` asset as float32 stereo PCM `shape=(N, 2)` via FFmpeg."""
    cmd = [
        "ffmpeg", "-y", "-i", str(audio_path),
        "-ar", str(sample_rate), "-ac", "2",
        "-f", "s16le", "-acodec", "pcm_s16le", "pipe:1",
    ]
    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if res.returncode != 0 or not res.stdout:
        raise RuntimeError(f"Failed to decode audio asset {audio_path}: {res.stderr.decode('utf-8', 'ignore')[:250]}")
    usable = (len(res.stdout) // 4) * 4
    arr = np.frombuffer(res.stdout[:usable], dtype=np.int16).astype(np.float32) / 32768.0
    return arr.reshape(-1, 2)


def assemble_packaged_episode(
    pcm_chunks: List[Tuple[bytes, int]],
    mp3_path: Path,
    *,
    bumper_after_indices: Optional[List[int]] = None,
    enable_packaging: bool = True,
    pause_ms: int = 600,
    bitrate: str = "128k",
) -> Tuple[Path, float]:
    """
    Studio mastering + show packaging pipeline:
      1. Edge-conditions every PCM chunk (DC strip + trailing pop trim + smooth cosine fades)
      2. Runs TWO-PASS LINEAR `loudnorm` (-16 LUFS, -1.5 dBTP) per chunk at 44.1kHz stereo
         so every segment has identical studio volume with ZERO dynamic pumping or warble
      3. Mixes broadcast show packaging (`intro_theme` fading cleanly under Chris's open,
         crisp `act_bumper` stingers between acts, and resolving `outro_theme`)
      4. Encodes final 44.1kHz stereo 128 kbps MP3
    Returns `(mp3_path, final_duration_sec)`.
    """
    if not pcm_chunks:
        raise ValueError("No PCM audio chunks provided.")
    if not shutil.which("ffmpeg"):
        raise RuntimeError("ffmpeg not found on PATH; cannot master MP3.")

    sr_out = 44100
    bumper_set = set(bumper_after_indices or [])
    mp3_path.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        norm_chunks: List[np.ndarray] = []

        for idx, (pcm_bytes, sr_in) in enumerate(pcm_chunks):
            cleaned = _clean_pcm_chunk(pcm_bytes, sample_rate=sr_in)
            raw_wav = tmp / f"raw_{idx:02d}.wav"
            norm_wav = tmp / f"norm_{idx:02d}.wav"
            with wave.open(str(raw_wav), "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(sr_in)
                wf.writeframes(cleaned)

            # Check chunk noise floor: if older 2.5-tts chunk has elevated hiss (> -41 dB floor),
            # engage gentle spectral denoise (`afftdn=nf=-28`) during linear normalization.
            a_chk = np.frombuffer(cleaned, dtype=np.int16).astype(np.float32) / 32768.0
            denoise_needed = False
            if len(a_chk) >= sr_in:
                n_fr = (len(a_chk) // (sr_in // 20)) * (sr_in // 20)
                frms = np.sqrt(np.mean(a_chk[:n_fr].reshape(-1, sr_in // 20) ** 2, axis=1))
                floor_db = 20.0 * np.log10(float(np.percentile(frms, 10)) + 1e-9)
                denoise_needed = floor_db > -41.0

            _two_pass_linear_normalize_wav(
                raw_wav, norm_wav, sample_rate=sr_out, channels=2, denoise=denoise_needed
            )
            norm_chunks.append(_load_audio_as_stereo_f32(norm_wav, sample_rate=sr_out))

        pieces: List[np.ndarray] = []
        pause_arr = np.zeros((int(sr_out * (pause_ms / 1000.0)), 2), dtype=np.float32)

        if enable_packaging:
            assets = get_packaging_assets()
            intro_arr = _load_audio_as_stereo_f32(assets["intro"], sample_rate=sr_out) * 0.62
            bumper_arr = _load_audio_as_stereo_f32(assets["bumper"], sample_rate=sr_out) * 0.52
            outro_arr = _load_audio_as_stereo_f32(assets["outro"], sample_rate=sr_out) * 0.58

            # Intro theme plays 2.4s solo, then ducks gently under the start of Chunk 1!
            voice_start = min(int(2.4 * sr_out), len(intro_arr))
            first = norm_chunks[0]
            first_mix_len = max(len(intro_arr), voice_start + len(first))
            first_mix = np.zeros((first_mix_len, 2), dtype=np.float32)
            # Duck intro tail smoothly once Chris begins speaking
            duck = np.ones(len(intro_arr), dtype=np.float32)
            if voice_start < len(intro_arr):
                duck_len = len(intro_arr) - voice_start
                duck[voice_start:] = np.linspace(0.60, 0.0, duck_len, dtype=np.float32) ** 1.4
            first_mix[: len(intro_arr)] += intro_arr * duck[:, None]
            first_mix[voice_start : voice_start + len(first)] += first
            pieces.append(first_mix)

            pre_b = np.zeros((int(0.35 * sr_out), 2), dtype=np.float32)
            post_b = np.zeros((int(0.25 * sr_out), 2), dtype=np.float32)
            for idx in range(1, len(norm_chunks)):
                if (idx - 1) in bumper_set:
                    pieces.extend([pre_b, bumper_arr, post_b, norm_chunks[idx]])
                else:
                    pieces.extend([pause_arr, norm_chunks[idx]])

            # Mix outro theme gently rising over the final 0.75s of speech and resolving smoothly
            body = np.concatenate(pieces, axis=0)
            overlap = min(int(0.75 * sr_out), len(body), len(outro_arr) // 3)
            out_len = len(body) - overlap + len(outro_arr)
            final_mix = np.zeros((out_len, 2), dtype=np.float32)
            final_mix[: len(body)] += body
            final_mix[len(body) - overlap : len(body) - overlap + len(outro_arr)] += outro_arr
        else:
            for idx, ch in enumerate(norm_chunks):
                pieces.append(ch)
                if idx < len(norm_chunks) - 1:
                    pieces.append(pause_arr)
            final_mix = np.concatenate(pieces, axis=0)

        # Gentle soft-clip safety ceiling (-1.0 dBFS) so overlaps never distort
        peak = float(np.max(np.abs(final_mix))) + 1e-9
        if peak > 0.89:
            final_mix = final_mix * (0.89 / peak)

        master_wav = tmp / "master_44k_stereo.wav"
        pcm16 = np.clip(final_mix * 32767.0, -32768.0, 32767.0).astype(np.int16)
        with wave.open(str(master_wav), "wb") as wf:
            wf.setnchannels(2)
            wf.setsampwidth(2)
            wf.setframerate(sr_out)
            wf.writeframes(pcm16.tobytes())

        enc_cmd = [
            "ffmpeg", "-y",
            "-i", str(master_wav),
            "-ar", str(sr_out),
            "-ac", "2",
            "-c:a", "libmp3lame",
            "-b:a", bitrate,
            str(mp3_path),
        ]
        res = subprocess.run(enc_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if res.returncode != 0:
            raise RuntimeError(f"ffmpeg MP3 encoding failed: {res.stderr[:400]}")

        return mp3_path, len(final_mix) / float(sr_out)


def master_wav_to_mp3(wav_path: Path, mp3_path: Path, bitrate: str = "128k") -> Path:
    """Applies two-pass linear podcast loudness normalization and encodes a broadcast MP3 via FFmpeg."""
    mp3_path.parent.mkdir(parents=True, exist_ok=True)
    if not shutil.which("ffmpeg"):
        raise RuntimeError("ffmpeg not found on PATH; cannot encode MP3.")

    with tempfile.TemporaryDirectory() as tmpdir:
        norm_wav = Path(tmpdir) / "norm.wav"
        _two_pass_linear_normalize_wav(wav_path, norm_wav, sample_rate=44100, channels=2)
        cmd = [
            "ffmpeg", "-y",
            "-i", str(norm_wav),
            "-ar", "44100",
            "-ac", "2",
            "-c:a", "libmp3lame",
            "-b:a", bitrate,
            str(mp3_path),
        ]
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if res.returncode != 0:
            raise RuntimeError(f"ffmpeg encoding failed: {res.stderr[:400]}")
    return mp3_path


def run_generator(
    season: int,
    week: int,
    *,
    length_mode: str = "sample",
    from_script: str = "",
    chris_voice: str = DEFAULT_CHRIS_VOICE,
    dave_voice: str = DEFAULT_DAVE_VOICE,
    tts_model: str = "",
    update_rss: bool = False,
    rss_base_url: str = "",
    publish_discord: bool = False,
    fresh: bool = False,
    enable_packaging: bool = True,
    remaster_only: bool = False,
) -> dict:
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not remaster_only and not api_key:
        raise RuntimeError("GEMINI_API_KEY not found in .env.")

    tag = length_mode
    script_out = OUTPUT_DIR / f"gemini_script_week_{week}_{season}_{tag}.txt"
    mp3_out = OUTPUT_DIR / f"bfl_gemini_podcast_week_{week}_{season}_{tag}.mp3"
    work_dir = OUTPUT_DIR / "podcast_work" / f"week_{week}_{season}_{tag}"
    if fresh and not remaster_only and work_dir.exists():
        shutil.rmtree(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)

    # Keep every TTS request <= 650 words so Google's non-streaming TTS gateway never
    # drops long connections with RemoteDisconnected (previously seen on 750-800+ word chunks).
    chunk_word_cap = 650
    bumper_after_indices: List[int] = []

    if remaster_only:
        cached_pcms = sorted(work_dir.glob(f"chunk_*_{chris_voice}_{dave_voice}.pcm"))
        if not cached_pcms:
            raise FileNotFoundError(f"No cached PCM chunks found in {work_dir} for --remaster.")
        pcm_results = [(p.read_bytes(), 24000) for p in cached_pcms]
        # Place act bumpers after Act 1 (chunk 0) and before Act 3 (last chunk) when >= 3 chunks
        if len(pcm_results) >= 3:
            bumper_after_indices = [0, len(pcm_results) - 2]
        print(f"🎛️  Remastering {len(pcm_results)} cached PCM chunk(s) with 2-pass linear loudnorm & show packaging...")
    else:
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
                segments = generate_dialogue_script(
                    payload_md,
                    season,
                    week,
                    api_key=api_key,
                    length_mode=length_mode,
                    work_dir=work_dir,
                    fresh=fresh,
                )
                cached_segments_file.write_text(json.dumps(segments, indent=2), encoding="utf-8")
                full_script = "\n\n".join(text for _, text in segments if text.strip())
                script_out.write_text(full_script + "\n", encoding="utf-8")
                total_w = sum(len(t.split()) for _, t in segments)
                print(f"💾 Saved dialogue script ({total_w:,} words • ~{total_w / 195:.1f} min est): {script_out}")

            tts_chunks = []
            for seg_idx, (seg_name, seg_text) in enumerate(segments):
                seg_chunks = split_script_into_tts_chunks(seg_text, max_words=chunk_word_cap)
                if not seg_chunks:
                    continue
                # Insert a clean broadcast act bumper right before Act 3 (standings/outro)
                if "standings_outro" in seg_name and tts_chunks:
                    bumper_after_indices.append(len(tts_chunks) - 1)
                tts_chunks.extend(seg_chunks)
                # Insert a clean broadcast act bumper right after Act 1 (intro/trades)
                if "intro_trades" in seg_name and seg_idx < len(segments) - 1:
                    bumper_after_indices.append(len(tts_chunks) - 1)

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
            try:
                pcm_bytes, sr = synthesize_multispeaker_pcm(
                    chunk,
                    api_key=api_key,
                    chris_voice=chris_voice,
                    dave_voice=dave_voice,
                    tts_models=[tts_model] if tts_model else None,
                )
            except RuntimeError as exc:
                # If an oversized chunk (>320 words) still triggers a gateway disconnect, split it in half cleanly
                if wc > 320 and "RemoteDisconnected" in str(exc):
                    halves = split_script_into_tts_chunks(chunk, max_words=max(200, (wc // 2) + 25))
                    if len(halves) > 1:
                        print(f"   ↪️ Gateway dropped {wc}-word chunk; splitting into {len(halves)} balanced sub-chunks...")
                        sub_pcms = []
                        sr = 24000
                        for h_idx, sub in enumerate(halves, 1):
                            print(f"      🎙️ Sub-chunk {h_idx}/{len(halves)} ({len(sub.split())} words)...")
                            b_sub, sr = synthesize_multispeaker_pcm(
                                sub,
                                api_key=api_key,
                                chris_voice=chris_voice,
                                dave_voice=dave_voice,
                                tts_models=[tts_model] if tts_model else None,
                            )
                            sub_pcms.append(_clean_pcm_chunk(b_sub, sample_rate=sr))
                            if h_idx < len(halves):
                                time.sleep(1.5)
                        pcm_bytes = (b"\x00\x00" * int(sr * 0.25)).join(sub_pcms)
                    else:
                        raise
                else:
                    raise
            cache_pcm.write_bytes(pcm_bytes)
            secs = len(pcm_bytes) / (sr * 2)
            print(f"   ✅ Rendered {secs:.1f}s of audio in {time.time() - t0:.1f}s")
            pcm_results.append((pcm_bytes, sr))

            # Polite pacing between TTS chunks on multi-chunk episodes to avoid bursting RPM limits
            if idx < len(tts_chunks):
                time.sleep(2.0)

    print("🎛️  Mastering episode (2-pass linear loudnorm" + (" + intro/bumpers/outro packaging" if enable_packaging else "") + ")...")
    _, total_audio_sec = assemble_packaged_episode(
        pcm_results,
        mp3_out,
        bumper_after_indices=bumper_after_indices,
        enable_packaging=enable_packaging,
        pause_ms=650 if length_mode != "sample" else 450,
        bitrate="128k",
    )

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
    parser.add_argument("--tts-model", type=str, default="", help="Override TTS model (e.g. gemini-3.1-flash-tts-preview, gemini-3.8-flash-tts)")
    parser.add_argument("--rss", action="store_true", help="Also add episode to Private Podcast RSS (`output/rss/feed.xml`)")
    parser.add_argument("--rss-base-url", type=str, default="", help="Hosted HTTPS base URL for the RSS feed")
    parser.add_argument("--publish-discord", action="store_true", help="Also upload to Discord #press-room-podcast")
    parser.add_argument("--no-packaging", action="store_true", help="Disable intro/outro music theme and act transition bumpers")
    parser.add_argument("--remaster", action="store_true", help="Re-master existing cached PCM chunks without calling Gemini APIs")
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
        tts_model=args.tts_model,
        update_rss=args.rss,
        rss_base_url=args.rss_base_url,
        publish_discord=args.publish_discord,
        fresh=args.fresh,
        enable_packaging=not args.no_packaging,
        remaster_only=args.remaster,
    )


if __name__ == "__main__":
    main()
