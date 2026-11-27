#!/usr/bin/env python3
"""
Generalized Multi-League Fantasy Football Podcast Generator
===========================================================
Generates studio-packaged Chris & Dave (`Charon` + `Iapetus` on `gemini-3.8-flash-tts`)
weekly recap podcasts for ANY ESPN Fantasy Football league — zero BFL lore leakage,
zero manual setup required, with optional per-league flavor/pronunciation overrides!

Features:
  - Auto-reads league rules, standings, Playoff Odds, All-Play Schedule Luck,
    Positional VORP edges, Draft Steals, Trades, and Weekly Awards directly from
    `web_hub/leagues/<slug>/hub_data.json` + live ESPN boxscores.
  - Adapts automatically to Year-1 leagues (strict anti-hallucination guardrail: never
    invents fake rings or prior seasons!) and any league size/format (8–16 teams,
    0/N divisions, Fixed Bracket vs NFL Re-Seeding, H2H vs PF tiebreakers).
  - Applies universal NFL player phonetics + automatic manager name cleanup +
    optional per-league overrides in `FantasyRecap/leagues/<slug>_podcast_config.json`.
  - Reuses hardened audio engineering from `gemini_podcast_generator.py` &
    `podcast_packaging.py` (RIFF/IPTC unwrapper, Two-Pass Linear -16 LUFS mastering,
    Fender-Rhodes intro/bumper/outro, truncation retry guardrails).
  - Supports `--script-only` (free instant dry-run) or full MP3 + per-league Private
    Apple Podcasts RSS (`gs://<podcast-bucket>/<slug>/feed.xml`).

Usage:
  python3 FantasyRecap/generic_podcast_generator.py --league-id 755112359 --slug jff --week 11 --sample
  python3 FantasyRecap/generic_podcast_generator.py --league-id 755112359 --slug jff --week 11 --deep --publish-gcs-rss bfl-podcast-7x9k2
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from FantasyRecap.core.manifest import get_espn_credentials, load_project_env
load_project_env()

from FantasyRecap.config import CURRENT_YEAR
from FantasyRecap.chat_adapters import (
    fetch_optional_chat_messages,
    load_league_game_events,
    merge_game_and_chat_timeline,
)
from FantasyRecap.game_day_hub_refresher import REGISTRY_PATH
from FantasyRecap.gemini_podcast_generator import (
    CONTRACTION_REPLACEMENTS,
    DEFAULT_CHRIS_VOICE,
    DEFAULT_DAVE_VOICE,
    _call_gemini,
    _clean_pcm_chunk,
    _sanitize_script,
    assemble_packaged_episode,
    format_spoken_records,
    split_script_into_tts_chunks,
    synthesize_multispeaker_pcm,
)
from FantasyRecap.league_recap_generator import analyze_week, fetch_espn_week_data
from FantasyRecap.multi_league_hub import (
    WEB_HUB_DIR,
    build_generic_hub_data,
    normalize_generic_owner_name,
)

LEAGUE_CONFIGS_DIR = SCRIPT_DIR / "leagues"
LEAGUE_CONFIGS_DIR.mkdir(exist_ok=True)
OUTPUT_DIR = SCRIPT_DIR / "output"
OUTPUT_DIR.mkdir(exist_ok=True)

# Universal NFL player + common tricky fantasy phonetics (shared across ALL leagues)
UNIVERSAL_PHONETICS = [
    (r"\bChristus\b", "Kris-tus"),
    (r"\bShough\b", "Shuck"),
    (r"\bJeanty\b", "Jen-tee"),
    (r"\bPerine\b", "Pee-rine"),
    (r"\bAchane\b", "Ay-chan"),
    (r"\bNacua\b", "Nuh-koo-uh"),
    (r"\bTagovailoa\b", "Tung-oh-vye-lo-uh"),
    (r"\bNjoku\b", "En-joe-koo"),
    (r"\bJuszczyk\b", "Yooz-check"),
    (r"\bOluokun\b", "Oh-loo-uh-koon"),
    (r"\bChuba\b", "Choo-buh"),
    (r"\bWan'Dale\b", "Wahn-dayl"),
    (r"\bBucky\b", "Bucky"),
    (r"\bOHern\b", "O'Hern"),
    (r"\bD/ST\b", "defense"),
    (r"\bIR\b", "I R"),
    (r"\bPF\b", "points for"),
    (r"\bPA\b", "points against"),
    (r"\bVORP\b", "value over replacement"),
    (r"\bPPG\b", "points per game"),
    (r"\bPPR\b", "P P R"),
    (r"\bQBs\b", "quarterbacks"),
    (r"\bRBs\b", "running backs"),
    (r"\bWRs\b", "wide receivers"),
    (r"\bTEs\b", "tight ends"),
    (r"\bQB\b", "quarterback"),
    (r"\bRB\b", "running back"),
    (r"\bWR\b", "wide receiver"),
    (r"\bTE\b", "tight end"),
]


def load_or_default_podcast_config(slug: str, hub_data: dict) -> dict:
    """
    Loads optional per-league podcast overrides from `FantasyRecap/leagues/<slug>_podcast_config.json`.
    If no override file exists, returns clean zero-config defaults derived from `hub_data`.
    """
    cfg_path = LEAGUE_CONFIGS_DIR / f"{slug}_podcast_config.json"
    lcfg = hub_data.get("league_config", {})
    league_name = lcfg.get("name", slug.upper())
    defaults = {
        "slug": slug,
        "show_title": f"{league_name} — Weekly Fantasy Recap",
        "league_name": league_name,
        "badge": lcfg.get("badge", slug.upper()),
        "pronunciations": {},
        "manager_notes": {},
        "sign_off": f"Good luck on waivers, {league_name}!",
    }
    if cfg_path.exists():
        try:
            user_cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
            defaults.update({k: v for k, v in user_cfg.items() if v is not None})
        except Exception:
            pass
    return defaults


def apply_generic_phonetics(text: str, custom_pronunciations: Optional[Dict[str, str]] = None) -> str:
    """Applies universal NFL phonetics, spoken record formatting, contractions, and optional league overrides."""
    out = format_spoken_records(text)
    # Auto-space standalone 2-letter uppercase initials like "Scott WK" -> "Scott W K"
    out = re.sub(r"\b([A-Z])([A-Z])\b(?=\s|$|[.,!?])", lambda m: f"{m.group(1)} {m.group(2)}" if m.group(0) not in ("QB", "RB", "WR", "TE", "IR", "PF", "PA", "TD", "FA") else m.group(0), out)
    if custom_pronunciations:
        for src, dst in custom_pronunciations.items():
            if src and dst:
                out = re.sub(rf"\b{re.escape(src)}\b", dst, out)
    for pat, repl in UNIVERSAL_PHONETICS:
        out = re.sub(pat, repl, out)
    for pat, repl in CONTRACTION_REPLACEMENTS:
        out = re.sub(pat, repl, out)
    return out


def build_generic_podcast_dossier(
    league_id: str,
    slug: str,
    season: int,
    week: int,
    hub_data: dict,
    pod_cfg: dict,
) -> dict:
    """
    Constructs a rich, data-grounded 3-Act broadcast dossier for ANY ESPN league
    using `hub_data` (playoff odds, schedule luck, positional heatmap, awards, trades)
    plus exact Week `week` ESPN boxscores (studs, busts, bench blunders, entering records).
    """
    creds = get_espn_credentials()
    wk_raw = fetch_espn_week_data(str(league_id), str(season), week, creds["espn_s2"], creds["espn_swid"])
    matchups_wk, perfs_wk, _ = analyze_week(wk_raw, week)

    # Map team IDs -> normalized owners from hub_data & ESPN members
    members = {}
    for m in wk_raw.get("members", []):
        full = f"{m.get('firstName', '').strip()} {m.get('lastName', '').strip()}".strip() or m.get("displayName", "").strip()
        members[m["id"]] = normalize_generic_owner_name(full)
    tid_to_owner = {}
    for t in wk_raw.get("teams", []):
        tid = int(t["id"])
        oid = t.get("primaryOwner") or (t.get("owners") or [None])[0]
        tid_to_owner[tid] = members.get(oid, t.get("name") or f"Team {tid}")

    # Compute entering-Week-N records AND post-Week-N records directly from completed_games
    entering_rec = {t["owner"]: [0, 0] for t in hub_data.get("teams", [])}
    post_rec = {t["owner"]: [0, 0] for t in hub_data.get("teams", [])}
    for g in hub_data.get("completed_games", []):
        gwk = int(g["week"])
        a, h, sa, sh = g["away"], g["home"], float(g["away_score"]), float(g["home_score"])
        w_own, l_own = (a, h) if sa >= sh else (h, a)
        if gwk < week:
            if w_own in entering_rec: entering_rec[w_own][0] += 1
            if l_own in entering_rec: entering_rec[l_own][1] += 1
        if gwk <= week:
            if w_own in post_rec: post_rec[w_own][0] += 1
            if l_own in post_rec: post_rec[l_own][1] += 1

    # Lookup helpers for story enrichment
    team_info = {t["owner"]: t for t in hub_data.get("teams", [])}
    luck_rows = (hub_data.get("analytics", {}).get("luck", {}).get("by_season", {}) or {}).get(str(season), [])
    luck_map = {r["owner"]: r for r in luck_rows}
    hm_rows = hub_data.get("analytics", {}).get("heatmap", {}).get("rows", [])
    hm_map = {r["owner"]: r for r in hm_rows}

    def _manager_blurb(own: str) -> str:
        t = team_info.get(own, {})
        lk = luck_map.get(own, {})
        hm = hm_map.get(own, {})
        note = pod_cfg.get("manager_notes", {}).get(own, "")
        bits = [f"Team: {t.get('team_name', own)}"]
        if lk:
            bits.append(f"All-Play {lk.get('ap_pct', 50)}% (True #{lk.get('true_rank', '?')}, Luck {lk.get('luck_delta', 0):+.1f}W — {lk.get('verdict', '')})")
        if hm:
            bits.append(f"Positional Edge: {hm.get('best_pos', '')} / Hole: {hm.get('worst_pos', '')}")
        if t.get("baseline_playoff_pct") is not None:
            bits.append(f"Current Playoff Odds: {t['baseline_playoff_pct']}% (Bye {t.get('baseline_bye_pct', 0)}%)")
        if note:
            bits.append(f"Note: {note}")
        return " | ".join(bits)

    # Act 1: League Overview, Weekly Awards, Recent Trades
    lcfg = hub_data.get("league_config", {})
    tb = hub_data.get("tiebreaker_rules", {})
    awards = (hub_data.get("awards") or {}).get("awards", [])
    awards_lines = "\n".join(f"- {a['title']} ({a['metric']}): {a['body']}" for a in awards) or "None."
    live_trades = [
        tr for tr in (hub_data.get("trades", {}).get("live", {}).get("trades", []) or [])
        if abs(int(tr.get("trade_week", 0)) - week) <= 2
    ]
    if live_trades:
        tr_lines = "\n".join(
            f"- Week {tr['trade_week']}: {tr['owner_a']} got [{', '.join(p['name'] for p in tr['a_received'])}] "
            f"({tr['started']['a_vorp']:+.1f} VORP) <-> {tr['owner_b']} got [{', '.join(p['name'] for p in tr['b_received'])}] "
            f"({tr['started']['b_vorp']:+.1f} VORP)"
            for tr in live_trades
        )
    else:
        tr_lines = "Quiet trade desk this week — no major trades executed."

    act1_text = f"""LEAGUE SNAPSHOT:
- League: {lcfg.get('name', slug)} ({len(hub_data.get('teams', []))} teams, {lcfg.get('scoring_label', 'PPR')}, {'Divisions' if lcfg.get('has_divisions') else 'No Divisions'})
- Inaugural Season: {'YES (Year 1 — DO NOT invent past championships, rings, or prior seasons!)' if lcfg.get('is_first_year') else f"Est. {lcfg.get('first_season')}"}
- Playoff Rules: Top {tb.get('playoff_teams', 6)} qualify (Byes: {tb.get('first_round_bye', 'None')}), {tb.get('reseed', 'Fixed Bracket')}, Tiebreaker: {tb.get('order', 'Record -> PF')}

WEEK {week} AWARDS & HEADLINES (League Avg: {(hub_data.get('awards') or {}).get('league_avg', 0):.1f} pts):
{awards_lines}

RECENT TRADES:
{tr_lines}
"""

    # Load decoupled ESPN live event ledger + optional chat plugin (Discord, etc.) from registry
    reg_entry = {}
    hub_bucket = "bfl-hub-7x9k2"
    if REGISTRY_PATH.exists():
        try:
            reg_data = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
            hub_bucket = reg_data.get("hub_bucket", hub_bucket)
            for lg in reg_data.get("leagues", []):
                if lg.get("slug") == slug:
                    reg_entry = lg
                    break
        except Exception:
            pass
    game_events = load_league_game_events(
        slug, season, week, bucket=hub_bucket, gcs_subpath=reg_entry.get("gcs_subpath", slug)
    )
    ev_ts = [float(e.get("ts", 0.0)) for e in game_events if e.get("ts")]
    chat_msgs = fetch_optional_chat_messages(
        reg_entry.get("chat"),
        start_ts=(min(ev_ts) - 14400) if ev_ts else None,
        end_ts=(max(ev_ts) + 21600) if ev_ts else None,
    )

    # Act 2: Individual Matchup Breakdowns (ordered by excitement / closeness & playoff leverage)
    matchup_blocks: List[Tuple[str, str]] = []
    for m in matchups_wk:
        w_tid = int(m["winner"]["id"])
        l_tid = int(m["loser"]["id"])
        w_own = tid_to_owner.get(w_tid, m["winner"]["owner"])
        l_own = tid_to_owner.get(l_tid, m["loser"]["owner"])
        w_pts = max(float(m["away_score"]), float(m["home_score"]))
        l_pts = min(float(m["away_score"]), float(m["home_score"]))
        margin = round(w_pts - l_pts, 2)

        we, le = entering_rec.get(w_own, [0, 0]), entering_rec.get(l_own, [0, 0])
        wp, lp = post_rec.get(w_own, [0, 0]), post_rec.get(l_own, [0, 0])

        def _fmt_team_box(tid: int, own: str) -> str:
            p = perfs_wk.get(tid, {})
            starters = sorted(p.get("starters", []), key=lambda x: -float(x.get("pts", 0)))
            studs = ", ".join(f"{x['name']} ({x['pts']:.1f} vs {x.get('proj', 0):.1f} proj)" for x in starters[:3])
            busts = ", ".join(f"{x['name']} ({x['pts']:.1f} vs {x.get('proj', 0):.1f} proj)" for x in starters[-2:] if float(x.get("pts", 0)) < float(x.get("proj", 0)) - 3)
            blunders = p.get("blunders", [])
            bl_txt = f" | Bench Blunder: started {blunders[0]['started']['name']} ({blunders[0]['started']['pts']:.1f}) over {blunders[0]['benched']['name']} ({blunders[0]['benched']['pts']:.1f}, +{blunders[0]['diff']:.1f} left on bench)" if blunders else ""
            return f"  - Top Performers: {studs}\n  - Dud/Disappointment: {busts or 'None major'}{bl_txt}"

        timeline_lines = merge_game_and_chat_timeline(
            game_events, chat_msgs, matchup_owners=[w_own, l_own]
        )
        timeline_section = ""
        if timeline_lines:
            capped = timeline_lines[:10]
            timeline_section = "\nLIVE GAME TIMELINE, SWINGS & CHAT REACTIONS:\n" + "\n".join(f"  - {ln}" for ln in capped) + "\n"

        blk = f"""MATCHUP: {w_own} (entering {we[0]}-{we[1]}) vs {l_own} (entering {le[0]}-{le[1]})
FINAL RESULT: {w_own} def. {l_own}, {w_pts:.2f} to {l_pts:.2f} (margin: {margin:.2f} pts)
UPDATED RECORDS: {w_own} moves to {wp[0]}-{wp[1]}; {l_own} drops to {lp[0]}-{lp[1]}

WINNER CONTEXT — {w_own}:
  {_manager_blurb(w_own)}
{_fmt_team_box(w_tid, w_own)}

LOSER CONTEXT — {l_own}:
  {_manager_blurb(l_own)}
{_fmt_team_box(l_tid, l_own)}{timeline_section}
"""
        matchup_blocks.append((f"{w_own} vs {l_own}", blk))

    # Act 3: Standings, Playoff Race, Schedule Frauds/Cursed, & Next Week Preview
    standings_sorted = sorted(
        hub_data.get("teams", []),
        key=lambda t: (-post_rec.get(t["owner"], [0, 0])[0], -float(t.get("raw_avg", 0.0))),
    )
    st_lines = "\n".join(
        f"{i:2d}. {t['owner']} ({t['team_name']}) — {post_rec.get(t['owner'], [0,0])[0]}-{post_rec.get(t['owner'], [0,0])[1]} | "
        f"PO {t['baseline_playoff_pct']}% | Bye {t['baseline_bye_pct']}% | Next-Wk Leverage ±{t['baseline_leverage']}%"
        for i, t in enumerate(standings_sorted, 1)
    )
    luckiest = sorted(luck_rows, key=lambda x: -x["luck_delta"])[:3]
    cursed = sorted(luck_rows, key=lambda x: x["luck_delta"])[:3]
    luck_txt = (
        "Schedule Blessed: " + "; ".join(f"{r['owner']} ({r['wins']}-{r['losses']} vs {r['expected_wins']:.1f} exp, {r['luck_delta']:+.1f}W)" for r in luckiest)
        + "\nSchedule Cursed: " + "; ".join(f"{r['owner']} ({r['wins']}-{r['losses']} vs {r['expected_wins']:.1f} exp, {r['luck_delta']:+.1f}W)" for r in cursed)
    )
    next_wk_games = [g for g in hub_data.get("remaining_schedule", []) if int(g["week"]) == week + 1]
    next_lines = "\n".join(
        f"- {g['away']} ({post_rec.get(g['away'], [0,0])[0]}-{post_rec.get(g['away'], [0,0])[1]}, PO {team_info.get(g['away'],{}).get('baseline_playoff_pct',0)}%) "
        f"vs {g['home']} ({post_rec.get(g['home'], [0,0])[0]}-{post_rec.get(g['home'], [0,0])[1]}, PO {team_info.get(g['home'],{}).get('baseline_playoff_pct',0)}%)"
        for g in next_wk_games
    ) or "End of regular season — Playoff Bracket next!"

    act3_text = f"""UPDATED STANDINGS & PLAYOFF RACE (Top {tb.get('playoff_teams', 6)} make playoffs; Byes: {tb.get('first_round_bye', 'None')}):
{st_lines}

ALL-PLAY SCHEDULE LUCK (FRAUD DETECTOR):
{luck_txt}

WEEK {week + 1} MATCHUPS TO CIRCLE:
{next_lines}
"""
    return {
        "act1": act1_text,
        "matchups": matchup_blocks,
        "act3": act3_text,
    }


def _publish_league_podcast_rss(
    slug: str,
    pod_cfg: dict,
    season: int,
    week: int,
    mp3_path: Path,
    duration_sec: float,
    bucket_name: str,
    project_id: str = "bfl-fantasy",
) -> str:
    """Uploads episode MP3 and updates isolated per-league RSS feed at `gs://<bucket>/<slug>/feed.xml`."""
    ep_name = mp3_path.name
    audio_gcs = f"gs://{bucket_name}/{slug}/episodes/{ep_name}"
    feed_gcs = f"gs://{bucket_name}/{slug}/feed.xml"
    audio_url = f"https://storage.googleapis.com/{bucket_name}/{slug}/episodes/{ep_name}"
    feed_url = f"https://storage.googleapis.com/{bucket_name}/{slug}/feed.xml"

    subprocess.run(
        ["gcloud", "storage", "cp", str(mp3_path), audio_gcs, f"--project={project_id}",
         "--content-type=audio/mpeg", "--cache-control=public, max-age=86400"],
        check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )

    # Load existing feed if present or initialize fresh iTunes RSS
    local_dir = OUTPUT_DIR / "rss" / slug
    local_dir.mkdir(parents=True, exist_ok=True)
    local_feed = local_dir / "feed.xml"
    existing_items = []
    try:
        import requests
        r = requests.get(feed_url, timeout=6)
        if r.status_code == 200 and "<rss" in r.text:
            root = ET.fromstring(r.text)
            for item in root.findall("./channel/item"):
                g = (item.findtext("guid") or "").strip()
                if g != f"{slug}-{season}-w{week}":
                    existing_items.append(ET.tostring(item, encoding="unicode"))
    except Exception:
        pass

    mins, secs = int(duration_sec // 60), int(duration_sec % 60)
    pub_rfc = format_datetime(datetime.now(timezone.utc))
    size_bytes = mp3_path.stat().st_size
    title = f"Week {week} ({season}) — {pod_cfg['show_title']}"
    desc = f"Automated Chris &amp; Dave weekly recap for {pod_cfg['league_name']} Week {week}."
    new_item = f"""    <item>
      <title>{title}</title>
      <description>{desc}</description>
      <guid isPermaLink="false">{slug}-{season}-w{week}</guid>
      <pubDate>{pub_rfc}</pubDate>
      <enclosure url="{audio_url}" length="{size_bytes}" type="audio/mpeg" />
      <itunes:duration>{mins:02d}:{secs:02d}</itunes:duration>
    </item>"""

    all_items = "\n".join([new_item] + existing_items)
    xml_doc = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">
  <channel>
    <title>{pod_cfg['show_title']}</title>
    <description>Automated weekly fantasy football recap podcast for {pod_cfg['league_name']} (Chris &amp; Dave).</description>
    <language>en-us</language>
    <itunes:author>{pod_cfg['league_name']} Media Desk</itunes:author>
    <itunes:explicit>false</itunes:explicit>
{all_items}
  </channel>
</rss>
"""
    local_feed.write_text(xml_doc, encoding="utf-8")
    subprocess.run(
        ["gcloud", "storage", "cp", str(local_feed), feed_gcs, f"--project={project_id}",
         "--content-type=application/rss+xml; charset=utf-8", "--cache-control=no-cache, max-age=60"],
        check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    print(f"📡 Published {slug} Private Podcast RSS: {feed_url}")
    return feed_url


def generate_generic_podcast(
    league_id: str,
    slug: str,
    season: int,
    week: int,
    *,
    length_mode: str = "sample",
    script_only: bool = False,
    chris_voice: str = DEFAULT_CHRIS_VOICE,
    dave_voice: str = DEFAULT_DAVE_VOICE,
    publish_gcs_rss: str = "",
    enable_packaging: bool = True,
) -> dict:
    """End-to-end generalized podcast pipeline for ANY ESPN league."""
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY missing in .env")

    # Load existing league hub_data.json or build fresh on the fly
    data_path = WEB_HUB_DIR / "leagues" / slug / "hub_data.json"
    if data_path.exists():
        hub_data = json.loads(data_path.read_text(encoding="utf-8"))
    else:
        hub_data, data_path = build_generic_hub_data(league_id, slug, season, force_week=week)

    pod_cfg = load_or_default_podcast_config(slug, hub_data)
    dossier = build_generic_podcast_dossier(league_id, slug, season, week, hub_data, pod_cfg)

    lcfg = hub_data.get("league_config", {})
    is_yr1 = bool(lcfg.get("is_first_year", False))
    yr1_guard = (
        "CRITICAL: This is the league's INAUGURAL season — NEVER invent past seasons, championships, rings, or fake multi-year history! Stick strictly to this season's real stats."
        if is_yr1 else
        f"Respect this league's real history (Est. {lcfg.get('first_season')}); never invent stats not in the dossier."
    )

    base_rules = f"""You are writing a lively, natural, studio-recorded two-host fantasy football podcast script for "{pod_cfg['show_title']}".
Hosts: Chris (lead sports desk anchor — warm, sharp, conversational) and Dave (witty co-host — punchy takes, playful banter).

STRICT FORMAT RULES:
- Output ONLY `Chris:` and `Dave:` dialogue lines. NO markdown, NO stage directions, NO [laughs], NO headers.
- Speak records naturally ("6 and 5", never "6-5") and use contractions naturally.
- {yr1_guard}
- Introduce each matchup with each manager's ENTERING-Week-{week} record before revealing the final score and updated record.
"""

    segments: List[Tuple[str, str]] = []
    if length_mode == "sample":
        prompt = f"""{base_rules}
TASK (2-MINUTE HIGHLIGHT REEL, ~280–340 words, 14–18 crisp back-and-forth turns):
- Energetic open welcoming listeners to Week {week} of {pod_cfg['show_title']}.
- Highlight the #1 headline matchup & biggest Weekly Award, tease the tight playoff race & Schedule Luck fraud/cursed story, and wrap with a punchy sign-off ("{pod_cfg['sign_off']}").

--- ACT 1 ---
{dossier['act1'][:3500]}

--- TOP MATCHUPS ---
{dossier['matchups'][0][1][:2500] if dossier['matchups'] else ''}

--- PLAYOFF RACE ---
{dossier['act3'][:2500]}
"""
        print(f"✍️  Writing {slug} Week {week} sample highlight script...")
        raw = _call_gemini(prompt, api_key, response_mime="text/plain", temperature=0.8)
        clean = apply_generic_phonetics(_sanitize_script(raw), pod_cfg.get("pronunciations"))
        segments.append(("seg01_sample", clean))
    else:
        # Full Standard (~16–20m) or Deep (~28–38m) 3-Act Structure!
        m_list = dossier["matchups"]
        if length_mode == "deep":
            groups = [[m] for m in m_list]
            m_words = "650–780"
        else:
            groups = [m_list[i : i + 2] for i in range(0, len(m_list), 2)]
            m_words = "460–540"
        total_segs = len(groups) + 2

        p1 = f"""{base_rules}
TASK (SEGMENT 1 OF {total_segs} — COLD OPEN, AWARDS & TRADES, ~240–320 words):
- Welcome listeners to Week {week} of {pod_cfg['show_title']}. Cover the Weekly Awards and any recent trades without spoiling upcoming matchup scores. End with a crisp handoff into the first matchup.
--- ACT 1 ---
{dossier['act1'][:4500]}
"""
        print(f"✍️  [1/{total_segs}] Writing {slug} Cold Open & Awards...")
        segments.append(("seg01_intro", apply_generic_phonetics(_sanitize_script(_call_gemini(p1, api_key, response_mime="text/plain", temperature=0.8)), pod_cfg.get("pronunciations"))))

        for idx, grp in enumerate(groups, 1):
            seg_n = idx + 1
            body = "\n\n---\n\n".join(b for _, b in grp)
            pm = f"""{base_rules}
TASK (SEGMENT {seg_n} OF {total_segs} — MATCHUP BREAKDOWN, ~{m_words} words):
- Mid-show (no "welcome back"). Break down every matchup below with studs, busts, bench blunders, schedule luck, and playoff implications.
- Whenever a matchup includes a LIVE GAME TIMELINE, SWINGS & CHAT REACTIONS section, vividly weave the win-probability shockwaves, lead changes, nuclear plays, injury exits, prime-time sweats, and any manager chat quotes into the storytelling!
--- MATCHUPS ---
{body[:9000]}
"""
            print(f"✍️  [{seg_n}/{total_segs}] Writing {slug} Matchup Block #{idx}...")
            segments.append((f"seg{seg_n:02d}_matchup_{idx:02d}", apply_generic_phonetics(_sanitize_script(_call_gemini(pm, api_key, response_mime="text/plain", temperature=0.8)), pod_cfg.get("pronunciations"))))

        p_end = f"""{base_rules}
TASK (FINAL SEGMENT {total_segs} OF {total_segs} — PLAYOFF RACE, SCHEDULE LUCK & WEEK {week + 1} PREVIEW, ~440–540 words):
- Break down the playoff standings, bye race, Schedule Blessed vs Schedule Cursed managers, and top Week {week + 1} matchups to watch. Sign off warmly ("{pod_cfg['sign_off']}").
--- ACT 3 ---
{dossier['act3'][:12000]}
"""
        print(f"✍️  [{total_segs}/{total_segs}] Writing {slug} Playoff Race & Outro...")
        segments.append((f"seg{total_segs:02d}_outro", apply_generic_phonetics(_sanitize_script(_call_gemini(p_end, api_key, response_mime="text/plain", temperature=0.8)), pod_cfg.get("pronunciations"))))

    full_script = "\n\n".join(txt for _, txt in segments if txt.strip())
    out_dir = OUTPUT_DIR / slug
    out_dir.mkdir(parents=True, exist_ok=True)
    script_path = out_dir / f"{slug}_{season}_week_{week}_{length_mode}_script.txt"
    script_path.write_text(full_script, encoding="utf-8")
    wc = len(full_script.split())
    print(f"✅ Wrote {slug} script ({wc} words, ~{wc / 155:.1f} min): {script_path}")

    if script_only:
        return {"script_path": str(script_path), "word_count": wc, "mp3_path": ""}

    # Render multi-speaker audio with Chris=Charon + Dave=Iapetus & Rhodes packaging!
    tts_chunks: List[str] = []
    bumper_after: List[int] = []
    for s_idx, (_sname, stxt) in enumerate(segments):
        parts = split_script_into_tts_chunks(stxt, max_words=380)
        tts_chunks.extend(parts)
        if len(segments) >= 3 and s_idx in (0, len(segments) - 2):
            bumper_after.append(len(tts_chunks) - 1)

    pcm_results: List[Tuple[bytes, int]] = []
    for idx, chunk in enumerate(tts_chunks, 1):
        print(f"🎙️  Rendering {slug} TTS chunk {idx}/{len(tts_chunks)} ({len(chunk.split())} words • {chris_voice} + {dave_voice})...")
        pcm_b, sr = synthesize_multispeaker_pcm(chunk, api_key=api_key, chris_voice=chris_voice, dave_voice=dave_voice)
        pcm_results.append((_clean_pcm_chunk(pcm_b, sample_rate=sr), sr))
        if idx < len(tts_chunks):
            time.sleep(2.0)

    mp3_path = out_dir / f"{slug}_{season}_week_{week}_{length_mode}.mp3"
    _, dur_sec = assemble_packaged_episode(
        pcm_results,
        mp3_path,
        bumper_after_indices=bumper_after,
        enable_packaging=enable_packaging,
        pause_ms=650 if length_mode != "sample" else 450,
        bitrate="128k",
    )
    print(f"🎉 Mastered {slug} MP3: {mp3_path} ({int(dur_sec // 60)}m {int(dur_sec % 60)}s)")

    feed_url = ""
    if publish_gcs_rss:
        feed_url = _publish_league_podcast_rss(slug, pod_cfg, season, week, mp3_path, dur_sec, publish_gcs_rss)

    return {"script_path": str(script_path), "word_count": wc, "mp3_path": str(mp3_path), "duration_sec": round(dur_sec, 1), "feed_url": feed_url}


def main():
    parser = argparse.ArgumentParser(description="Generalized Multi-League Podcast Generator (Chris & Dave)")
    parser.add_argument("--league-id", type=str, required=True)
    parser.add_argument("--slug", type=str, required=True)
    parser.add_argument("--season", type=int, default=int(CURRENT_YEAR))
    parser.add_argument("--week", type=int, default=11)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--sample", action="store_true", help="~2 minute highlight episode")
    mode.add_argument("--standard", action="store_true", help="~16-20 minute episode")
    mode.add_argument("--deep", action="store_true", help="~28-38 minute deep-dive episode")
    parser.add_argument("--script-only", action="store_true", help="Generate script only (skip TTS audio)")
    parser.add_argument("--publish-gcs-rss", type=str, default="", help="GCS podcast bucket for isolated per-league RSS (e.g. bfl-podcast-7x9k2)")
    args = parser.parse_args()

    lmode = "deep" if args.deep else ("standard" if args.standard else "sample")
    generate_generic_podcast(
        args.league_id,
        args.slug,
        args.season,
        args.week,
        length_mode=lmode,
        script_only=args.script_only,
        publish_gcs_rss=args.publish_gcs_rss,
    )


if __name__ == "__main__":
    main()
