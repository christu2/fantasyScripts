#!/usr/bin/env python3
"""
BFL Human Podcast Publisher
==============================
Takes the raw audio recorded by the commissioner, compresses it to fit Discord's limits,
and uploads the optimized audio directly to the Discord podcast channel.
"""

import os
import sys
import argparse
import subprocess
import requests
from pathlib import Path
from dotenv import load_dotenv

env_path = Path(__file__).resolve().parent.parent / '.env'
if env_path.exists(): load_dotenv(dotenv_path=env_path)

def publish_human_podcast(season: int, week_num: int, audio_path: str, notes: str = "", bitrate: str = "64k"):
    print(f"🎙️ BFL AUDIO COMPILER (Commissioner Edition) - Week {week_num}, {season}")
    
    if not os.path.exists(audio_path):
        print(f"❌ Error: Audio file '{audio_path}' not found!")
        return

    # 1. Get audio duration
    cmd_dur = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", audio_path]
    res = subprocess.run(cmd_dur, stdout=subprocess.PIPE, text=True)
    try:
        dur_seconds = float(res.stdout.strip())
    except:
        print("❌ Could not read audio duration.")
        return
        
    mins, secs = int(dur_seconds // 60), int(dur_seconds % 60)
    print(f"🎵 Loaded Commissioner Audio: {mins}m {secs}s")

    # 2. Compress Audio to fit under Discord limits
    out_dir = Path(__file__).resolve().parent / "output"
    out_dir.mkdir(exist_ok=True)
    compressed_audio_path = str(out_dir / f"bfl_commissioner_podcast_week_{week_num}_{season}.mp3")
    print(f"🗜️ Compressing audio for Discord (Bitrate: {bitrate})...")
    
    cmd_compress = [
        "ffmpeg", "-y",
        "-i", audio_path,
        "-c:a", "libmp3lame", "-b:a", bitrate,
        compressed_audio_path
    ]
    subprocess.run(cmd_compress, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    
    # Check size
    size_mb = os.path.getsize(compressed_audio_path) / (1024 * 1024)
    print(f"✅ Audio compressed! Final size: {size_mb:.2f} MB")

    # 3. Discord Upload
    webhook = os.getenv("DISCORD_WEBHOOK_PODCAST")
    if webhook:
        print("🚀 Uploading to Discord...")
        thread_title = f"🎙️ BFL Commissioner's Podcast: Week {week_num} [{season}]"
        
        notes_str = f"📝 **Notes:** {notes}\n" if notes else ""
        
        with open(compressed_audio_path, 'rb') as f_mp3:
            files = {
                'files[0]': (f"bfl_commissioner_week_{week_num}.mp3", f_mp3, 'audio/mpeg')
            }
            data = {
                'username': 'BFL Studio Desk',
                'thread_name': thread_title,
                'content': f"🏆 **BFL OFFICIAL COMMISSIONER'S PODCAST**\n\n⏱️ **Duration:** `{mins}m {secs}s`\n{notes_str}\n🎧 Listen to the Week {week_num} episode below: 👇"
            }
            r = requests.post(webhook + "?wait=true", data=data, files=files)
            if r.status_code in [200, 204]:
                print("✅ Discord upload complete!")
            else:
                print(f"❌ Discord Upload Failed! Status: {r.status_code}")
                print(f"Response: {r.text}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--season", type=int, default=2026)
    parser.add_argument("--week", type=int, default=3)
    parser.add_argument("--audio", type=str, required=True, help="Path to the Human MP3 or WAV file")
    parser.add_argument("--notes", type=str, default="", help="Optional notes to include in the Discord message")
    parser.add_argument("--bitrate", type=str, default="64k", help="Audio bitrate for compression (default: 64k)")
    args = parser.parse_args()
    publish_human_podcast(args.season, args.week, args.audio, args.notes, args.bitrate)
