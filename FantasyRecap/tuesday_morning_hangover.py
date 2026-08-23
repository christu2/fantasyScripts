#!/usr/bin/env python3
"""
BFL Tuesday Morning Hangover (Dynamic Animated TV Studio Broadcast Pipeline)
=============================================================================
Produces the weekly television sports show dynamically for ANY ESPN season/week:
- Hosts: Chris (AndrewMultilingualNeural) & Dave (BrianMultilingualNeural)
- Fully Animated Sports Studio Anchors with real-time lip-flapping, gestures & VU meters
- Dynamic Active Team Name & Owner Name Resolution from live ESPN API
- Accurate Phonetics: 'Jabroni' -> 'juh-bro-knee', 'Lukose' -> 'Luke-ose', 'Thor', etc.
- Combined Discord Forum Post with both Audio MP3 & Video MP4 attached in one thread
- CLI Flags: --season <YYYY> --week <N>
"""

import os
import sys
import re
import random
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
from FantasyRecap.video_highlight_engine import render_animated_studio_frame, generate_animated_studio_video

# Load .env
env_path = Path(__file__).resolve().parent.parent / '.env'
if env_path.exists():
    load_dotenv(dotenv_path=env_path)
else:
    load_dotenv()

VOICE_HOST1 = 'en-US-AndrewMultilingualNeural' # Chris (Lead Anchor)
VOICE_HOST2 = 'en-US-BrianMultilingualNeural'  # Dave (Color Analyst)

MASTER_PHONETICS = [
    # --- Lore & Trophy ---
    (r'\bThe Jabroni Trophy\b', 'The juh-bro-knee Trophy'),
    (r'\bThe Jabroni\b', 'The juh-bro-knee'),
    (r'\bJabroni\b', 'juh-bro-knee'),
    (r'\bJabronis\b', 'juh-bro-knees'),

    # --- League Owners ---
    (r'\bShawn Lukose\b', 'Luke-ose'),
    (r'\bLukose\b', 'Luke-ose'),
    (r'\bShawn Ullenbrauck\b', 'Thor'),
    (r'\bTommy\b', 'Thomas'),
    (r'\bMykonos\b', 'Mee-ko-nos'),
    (r'\bNael\b', 'Nile'),
    (r'\bSamran\b', 'Sum-rahn'),
    (r'\bRej\b', 'Redge'),
    (r'\bSaagar\b', 'Sah-gar'),
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
    (r'\b0\.0\b', 'zero'),
    (r'\b1\.3\b', 'one point three'),
    (r'\b3\.04\b', 'three point zero four')
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

async def produce_full_hangover_broadcast(season: int = 2024, week_num: int = 17, post_to_discord: bool = True):
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
    print(f"📝 Generated {len(scenes)} dynamic TV studio scenes!")

    temp_audio_dir = Path(__file__).resolve().parent / f"temp_audio_{pid}"
    temp_slide_dir = Path(__file__).resolve().parent / f"temp_slides_{pid}"
    temp_audio_dir.mkdir(parents=True, exist_ok=True)
    temp_slide_dir.mkdir(parents=True, exist_ok=True)

    all_audio_files = []
    animated_shots = []

    print("🎙️ Synthesizing host dialogue audio and animating TV studio frames...")

    global_seg_idx = 0
    frame_counter = 0

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

            # Generate multi-frame animated shots during this speaker's dialogue turn
            cur_time = 0.0
            step_idx = 0
            while cur_time < dur:
                anim_state = 'TALK' if (step_idx % 2 == 0) else 'IDLE'
                chunk_dur = random.uniform(0.35, 0.55) if anim_state == 'TALK' else random.uniform(0.20, 0.35)
                if cur_time + chunk_dur > dur:
                    chunk_dur = dur - cur_time

                vu = [random.uniform(0.3, 1.0) for _ in range(5)] if anim_state == 'TALK' else [random.uniform(0.1, 0.3) for _ in range(5)]
                frame_path = str(temp_slide_dir / f"anim_{frame_counter:04d}_{speaker}.png")

                render_animated_studio_frame(
                    title=card['title'],
                    subtitle=card['subtitle'],
                    category_badge=card['badge'],
                    items=card['items'],
                    speaker=speaker,
                    anim_state=anim_state,
                    vu_levels=vu,
                    output_path=frame_path,
                    accent_color=card['accent']
                )

                animated_shots.append((frame_path, chunk_dur))
                cur_time += chunk_dur
                step_idx += 1
                frame_counter += 1

            global_seg_idx += 1

        print(f"  • Scene {scene_idx+1:02d}/{len(scenes):02d} [{card['badge']}]: {scene_total_dur:.1f}s ({frame_counter} anim frames)")

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

    # Generate Animated TV Studio MP4 Video Reel
    master_mp4 = str(Path(__file__).resolve().parent / f"bfl_tuesday_hangover_week_{week_num}_{season}.mp4")
    print(f"🎬 Compiling Animated TV Studio MP4 Video Show ({len(animated_shots)} Animated Frames) -> {master_mp4}...")
    generate_animated_studio_video(animated_shots, master_mp3, master_mp4, pid)

    # Post to Discord #press-room-podcast as ONE COMBINED POST
    if post_to_discord and os.getenv("DISCORD_WEBHOOK_PODCAST"):
        print("🚀 Uploading Tuesday Morning Hangover (COMBINED MP3 + MP4 Post) to #press-room-podcast Forum...")
        thread_title = f"☕ BFL Tuesday Morning Hangover: Week {week_num} Show ({mins}m {secs}s) [{season}]"

        with open(master_mp3, 'rb') as f_mp3, open(master_mp4, 'rb') as f_mp4:
            files = {
                'files[0]': (f"bfl_tuesday_morning_hangover_week_{week_num}_{season}.mp3", f_mp3, 'audio/mpeg'),
                'files[1]': (f"bfl_tuesday_hangover_week_{week_num}_{season}.mp4", f_mp4, 'video/mp4')
            }
            data = {
                'username': 'BFL TV Studio Broadcast Desk (Chris & Dave)',
                'avatar_url': 'https://a.espncdn.com/combiner/i?img=/i/headshots/nfl/players/full/3918298.png',
                'thread_name': thread_title,
                'content': f"☕ **BFL TUESDAY MORNING HANGOVER: WEEK {week_num} TELEVISION BROADCAST ({season})**\n*Chris & Dave host live from the animated broadcast desk breaking down all matchups, heartbreakers, blowouts, and the race for The Jabroni Trophy!*\n\n⏱️ **Duration:** `{mins}m {secs}s`\n📺 **Watch the Animated Video or Listen to the Audio Podcast below:** 👇"
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
    parser.add_argument("--season", type=int, default=2024, help="NFL Season (e.g. 2024, 2025)")
    parser.add_argument("--week", type=int, default=17, help="Week number (e.g. 1-17)")
    args = parser.parse_args()

    asyncio.run(produce_full_hangover_broadcast(args.season, args.week))
