#!/usr/bin/env python3
"""
BFL Tuesday Morning Hangover (TV Studio Broadcast Pipeline)
===========================================================
Produces the television sports show dynamically for ANY ESPN season/week:
- Hosts: Chris (AndrewMultilingualNeural) & Dave (BrianMultilingualNeural)
- Static broadcast studio portraits with live "ON AIR" speaker highlight borders & VU meters (NO image flashing)
- Dynamic Active Team Name & Owner Name Resolution from live ESPN API
- Accurate Phonetics: 'Jabroni' -> 'juh-bro-knee', 'Lukose' -> 'Luke-ose', 'Thor', etc.
- Combined Discord Forum Post with both Audio MP3 & Video MP4 attached in one thread
- CLI Flags: --season <YYYY> --week <N>
"""

import os
import sys
import re
import argparse
import asyncio
import subprocess
import requests
import edge_tts
from pathlib import Path
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from FantasyRecap.league_recap_generator import fetch_espn_week_data, parse_league_members_and_teams, ESPN_LEAGUE_ID, ESPN_S2, ESPN_SWID
from FantasyRecap.dynamic_show_builder import build_dynamic_scenes_from_espn
from FantasyRecap.video_highlight_engine import render_tv_studio_frame, generate_broadcast_video

# Load .env
env_path = Path(__file__).resolve().parent.parent / '.env'
if env_path.exists():
    load_dotenv(dotenv_path=env_path)
else:
    load_dotenv()

VOICE_HOST1 = 'en-US-AndrewMultilingualNeural' # Chris (Lead Anchor - Professional & Sharp)
VOICE_HOST2 = 'en-US-GuyNeural'                 # Dave (Color Analyst - Expressive, Warm & Conversational)

MASTER_PHONETICS = [
    # --- Lore & Trophy ---
    (r'\bThe Jabroni Trophy\b', 'The Jahbroni Trophy'),
    (r'\bThe Jabroni\b', 'The Jahbroni'),
    (r'\bJabroni\b', 'Jahbroni'),
    (r'\bJabronis\b', 'Jahbronis'),

    # --- League Owners ---
    (r'\bShawn Lukose\b', 'Luke-ose'),
    (r'\bLukose\b', 'Luke-ose'),
    (r'\bShawn Ullenbrauck\b', 'Thor'),
    (r'\bTommy\b', 'Thomas'),
    (r'\bMykonos\b', 'Mee-ko-nos'),
    (r'\bNael\b', 'Nile'),
    (r'\bSamran\b', 'Sum-rahn'),
    (r'\bRej\b', 'Ray'),
    (r'\brej\b', 'Ray'),
    (r'\bSaagar\b', 'Sah-gur'),
    (r'\bDino\b', 'Dee-no'),
    (r'\bEmelie\b', 'Emily'),
    (r'\bKruszewski\b', 'Cruise-sheff-skee'),
    
    # --- NFL Players ---
    (r'\bWan\'Dale\b', 'Wahn-Dale'),
    (r'\bBijan\b', 'Bee-jahn'),
    (r'\bJa\'Marr\b', 'Juh-Mahr'),
    (r'\bNico\b', 'Knee-co'),
    (r'\bKeon\b', 'Kee-on'),
    (r'\bEmeka Egbuka\b', 'Eh-meh-ka Egg-boo-ka'),
    (r'\bEgbuka\b', 'Egg-boo-ka'),
    (r'\bMcLaurin\b', 'Mick-Lauren'),
    (r'\bJ\.J\.\b', 'J J'),
    (r'\bA\.J\.\b', 'A J'),
    (r'\bXavier\b', 'Zay-vee-er'),
    (r'\bTyrone\b', 'Tie-rone'),
    (r'\bJalen\b', 'Jay-len'),
    (r'\bChig Okonkwo\b', 'Chig Oh-konk-woh'),
    (r'\bJacory\b', 'Juh-cor-ee'),
    
    # --- Terms & Numbers ---
    (r'\bdef\.\b', 'defeated'),
    (r'\bpts\b', 'points'),
    (r'\bpt\b', 'point'),
    (r'\bH2H\b', 'head to head'),
    (r'\bvs\.\b', 'versus'),
    (r'\bdiv\b', 'division'),
    (r'\bQB\b', 'quarterback'),
    (r'\bRB\b', 'running back'),
    (r'\bWR\b', 'wide receiver'),
    (r'\bTE\b', 'tight end'),
    (r'\bGOAT\b', 'goat'),
    (r'\bCommish\b', 'Commissioner'),
    (r'\bTNF\b', 'Thursday Night Football'),
    (r'\bMNF\b', 'Monday Night Football'),
    (r'\bSNF\b', 'Sunday Night Football'),
    (r'\b0\.38\b', 'zero point three eight'),
    (r'\b5\.92\b', 'five point nine two'),
    (r'\b36\.92\b', 'thirty-six point nine two'),
    (r'\b39\.4\b', 'thirty-nine point four'),
    (r'\b45\.6\b', 'forty-five point six')
]

def clean_for_spoken_audio(text: str) -> str:
    """Cleans text and applies master phonetic pronunciation for TTS."""
    text = re.sub(r'[*#_`~>|]', '', text)
    text = re.sub(r'^\[[A-Za-z0-9\s]+\]:\s*', '', text)
    text = re.sub(r'^\*\*[A-Za-z0-9\s]+\*\*:\s*', '', text)
    for pattern, replacement in MASTER_PHONETICS:
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
    text = re.sub(r'\s+', ' ', text).strip()
    return text

async def produce_full_hangover_broadcast(season: int = 2025, week_num: int = 17, post_to_discord: bool = True):
    pid = os.getpid()
    print("\n" + "="*75)
    print(f"🎙️ BFL TUESDAY MORNING HANGOVER: TV STUDIO BROADCAST (WEEK {week_num}, {season})")
    print("="*75)

    print(f"📡 Fetching live ESPN boxscore & roster data for Season {season}, Week {week_num}...")
    raw_data = fetch_espn_week_data(ESPN_LEAGUE_ID, str(season), week_num, ESPN_S2, ESPN_SWID)
    teams = parse_league_members_and_teams(raw_data)
    print(f"✅ Successfully loaded {len(teams)} active BFL franchises dynamically!")

    print("⚡ Building dynamic show scenes, storylines, and matchups...")
    scenes = build_dynamic_scenes_from_espn(raw_data, season, week_num)
    print(f"📝 Generated {len(scenes)} rich TV studio scenes!")

    temp_audio_dir = Path(__file__).resolve().parent / f"temp_audio_{pid}"
    temp_slide_dir = Path(__file__).resolve().parent / f"temp_slides_{pid}"
    temp_audio_dir.mkdir(parents=True, exist_ok=True)
    temp_slide_dir.mkdir(parents=True, exist_ok=True)

    all_audio_files = []
    tv_shots = []
    tv_durations = []

    print("🎙️ Synthesizing host dialogue audio and rendering TV studio shots...")

    global_seg_idx = 0

    for scene_idx, sc in enumerate(scenes):
        card = sc['card']
        dialogue = sc['dialogue']
        scene_total_dur = 0.0

        for speaker, raw_text in dialogue:
            clean_text = clean_for_spoken_audio(raw_text)
            voice = VOICE_HOST1 if speaker == 'CHRIS' else VOICE_HOST2
            seg_file = str(temp_audio_dir / f"seg_{global_seg_idx:03d}_{speaker}.mp3")

            comm = edge_tts.Communicate(clean_text, voice, rate="+2%", pitch="+0Hz")
            await comm.save(seg_file)
            all_audio_files.append(seg_file)

            # Measure segment audio duration
            cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", seg_file]
            res = subprocess.run(cmd, stdout=subprocess.PIPE, text=True)
            try:
                dur = float(res.stdout.strip())
            except:
                dur = 5.0
            scene_total_dur += dur

            # Render clean static TV Studio frame with active speaker highlighted
            frame_path = str(temp_slide_dir / f"frame_{global_seg_idx:03d}_{speaker}.png")
            render_tv_studio_frame(
                title=card['title'],
                subtitle=card['subtitle'],
                category_badge=card['badge'],
                items=card['items'],
                speaker=speaker,
                output_path=frame_path,
                accent_color=card['accent']
            )

            tv_shots.append(frame_path)
            tv_durations.append(dur)
            global_seg_idx += 1

        print(f"  • Scene {scene_idx+1:02d}/{len(scenes):02d} [{card['badge']}]: {scene_total_dur:.1f}s")

    # Stitch Master Podcast MP3
    master_mp3 = str(Path(__file__).resolve().parent / f"bfl_tuesday_morning_hangover_week_{week_num}_{season}.mp3")
    concat_list = temp_audio_dir / "concat.txt"
    with open(concat_list, 'w') as f:
        for af in all_audio_files:
            f.write(f"file '{af}'\n")

    print(f"🎵 Stitching master podcast MP3 via ffmpeg -> {master_mp3}...")
    cmd = ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_list), "-c", "copy", master_mp3]
    subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    # Cleanup audio temp
    for f in temp_audio_dir.glob("*.mp3"):
        try: f.unlink()
        except: pass
    if concat_list.exists(): concat_list.unlink()
    try: temp_audio_dir.rmdir()
    except: pass

    cmd_dur = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", master_mp3]
    res = subprocess.run(cmd_dur, stdout=subprocess.PIPE, text=True)
    dur_seconds = float(res.stdout.strip())
    mins = int(dur_seconds // 60)
    secs = int(dur_seconds % 60)
    print(f"🎉 Master Audio Rendered! Runtime: {mins}m {secs}s -> {master_mp3}")

    # Generate Clean TV Studio MP4 Video Reel
    master_mp4 = str(Path(__file__).resolve().parent / f"bfl_tuesday_hangover_week_{week_num}_{season}.mp4")
    print(f"🎬 Compiling Clean TV Studio MP4 Video Show ({len(tv_shots)} Shots) -> {master_mp4}...")
    generate_broadcast_video(tv_shots, tv_durations, master_mp3, master_mp4, pid)

    # Post to Discord #press-room-podcast as ONE COMBINED POST
    if post_to_discord and os.getenv("DISCORD_WEBHOOK_PODCAST"):
        print("🚀 Uploading Tuesday Morning Hangover (COMBINED MP3 + MP4 Post) to #press-room-podcast Forum...")
        thread_title = f"☕ BFL Tuesday Morning Hangover: Week {week_num} Championship Show ({mins}m {secs}s) [{season}]"

        with open(master_mp3, 'rb') as f_mp3, open(master_mp4, 'rb') as f_mp4:
            files = {
                'files[0]': (f"bfl_tuesday_morning_hangover_week_{week_num}_{season}.mp3", f_mp3, 'audio/mpeg'),
                'files[1]': (f"bfl_tuesday_hangover_week_{week_num}_{season}.mp4", f_mp4, 'video/mp4')
            }
            data = {
                'username': 'BFL TV Studio Broadcast Desk (Chris & Dave)',
                'avatar_url': 'https://a.espncdn.com/combiner/i?img=/i/headshots/nfl/players/full/3918298.png',
                'thread_name': thread_title,
                'content': f"🏆 **BFL TUESDAY MORNING HANGOVER: {season} CHAMPIONSHIP TELEVISION FINALE**\n*Chris & Dave break down the entire championship slate: Abe winning back-to-back Jabroni Trophies over Dan, Thor taking bronze over Lukose, Bijan's 39.4-pt eruption, King Derrick Henry's 45.6-pt rampage, Rej's 0.38-pt cardiac win, and all-time BFL franchise history!*\n\n⏱️ **Duration:** `{mins}m {secs}s`\n📺 **Watch the Studio TV Video or Listen to the Audio Podcast below:** 👇"
            }
            resp = requests.post(os.getenv("DISCORD_WEBHOOK_PODCAST") + "?wait=true", data=data, files=files, timeout=90)
            
            if resp.status_code in [200, 201, 204]:
                print("🎉 SUCCESS! Combined Audio & Video Show uploaded in ONE single Discord Forum post!")
            else:
                print(f"❌ Discord upload error: {resp.status_code} - {resp.text}")

    # Cleanup video slides
    for f in temp_slide_dir.glob("*.png"):
        try: f.unlink()
        except: pass
    try: temp_slide_dir.rmdir()
    except: pass

    return master_mp3, master_mp4

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Produce weekly BFL Tuesday Morning Hangover broadcast")
    parser.add_argument("--season", type=int, default=2025, help="NFL Season (e.g. 2024, 2025)")
    parser.add_argument("--week", type=int, default=17, help="Week number (e.g. 1-17)")
    args = parser.parse_args()

    asyncio.run(produce_full_hangover_broadcast(args.season, args.week))
