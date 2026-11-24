#!/usr/bin/env python3
"""
BFL NotebookLM Audio Publisher
==============================
Takes the raw audio downloaded from NotebookLM, compresses it to fit Discord's limits,
and uploads the optimized audio directly to the Discord podcast channel.
"""

import os
import sys
import argparse
import subprocess
import requests
from pathlib import Path
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from FantasyRecap.core.manifest import load_project_env
load_project_env()

import json
from FantasyRecap.podcast_rss_builder import add_episode_to_rss


def publish_notebook_audio(
    season: int,
    week_num: int,
    audio_path: str,
    notes: str = "",
    edit_thread_id: str = "",
    update_rss: bool = False,
    rss_base_url: str = "",
):
    print(f"🎙️ BFL AUDIO COMPILER (NotebookLM Edition) - Week {week_num}, {season}")
    
    if not os.path.exists(audio_path):
        print(f"❌ Error: Audio file '{audio_path}' not found!")
        return

    # Optional: Export crisp 128 kbps MP3 & update Private Podcast RSS feed (Apple Podcasts / Spotify)
    rss_info = None
    if update_rss or rss_base_url or os.getenv("PODCAST_RSS_BASE_URL"):
        try:
            rss_info = add_episode_to_rss(
                season=season,
                week=week_num,
                audio_path=audio_path,
                notes=notes,
                episode_type="notebooklm",
                base_url=rss_base_url,
            )
            print(f"📡 Private RSS updated ({rss_info['size_mb']} MB HQ MP3): {rss_info['feed_path']}")
        except Exception as exc:
            print(f"⚠️ RSS build failed (continuing with Discord upload): {exc}")

    # 1. Get audio duration
    cmd_dur = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", audio_path]
    res = subprocess.run(cmd_dur, stdout=subprocess.PIPE, text=True)
    try:
        dur_seconds = float(res.stdout.strip())
    except:
        print("❌ Could not read audio duration.")
        return
        
    mins, secs = int(dur_seconds // 60), int(dur_seconds % 60)
    print(f"🎵 Loaded NotebookLM Audio: {mins}m {secs}s")

    # 2. Compress Audio to fit safely under Discord's 10 MB unboosted webhook limit
    # Target ~8.8 MB max regardless of episode duration (supports 60–90+ min episodes)
    target_kbps = max(12, min(64, int((8.8 * 8192) / max(dur_seconds, 1))))
    valid_kbps = [64, 56, 48, 40, 32, 24, 16, 12]
    chosen_kbps = next((k for k in valid_kbps if k <= target_kbps), 12)

    out_dir = Path(__file__).resolve().parent / "output"
    out_dir.mkdir(exist_ok=True)
    compressed_audio_path = str(out_dir / f"bfl_notebooklm_hangover_week_{week_num}_{season}.mp3")

    while True:
        bitrate_str = f"{chosen_kbps}k"
        print(f"🗜️ Compressing audio for Discord ({bitrate_str} mono MP3)...")
        cmd_compress = [
            "ffmpeg", "-y",
            "-i", audio_path,
            "-ac", "1",
        ]
        if chosen_kbps < 32:
            cmd_compress.extend(["-ar", "22050"])
        cmd_compress.extend([
            "-c:a", "libmp3lame", "-b:a", bitrate_str,
            compressed_audio_path,
        ])
        subprocess.run(cmd_compress, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        size_mb = os.path.getsize(compressed_audio_path) / (1024 * 1024)
        if size_mb <= 9.6 or chosen_kbps <= 12:
            break
        # Step down one bitrate tier if still close to Discord's 10 MB limit
        idx = valid_kbps.index(chosen_kbps)
        if idx + 1 >= len(valid_kbps):
            break
        chosen_kbps = valid_kbps[idx + 1]

    print(f"✅ Audio compressed! Final size: {size_mb:.2f} MB ({chosen_kbps}k)")

    # 3. Discord Upload or In-Place Edit
    webhook = os.getenv("DISCORD_WEBHOOK_PODCAST")
    if webhook:
        thread_title = f"☕ BFL Tuesday Hangover (NotebookLM): Week {week_num} [{season}]"
        notes_str = f"📝 **Episode Notes:**\n{notes}\n\n" if notes else ""
        rss_line = ""
        if rss_info and "example.com" not in rss_info["feed_url"]:
            rss_line = f"📡 **Apple Podcasts / Spotify Private RSS:** {rss_info['feed_url']}\n\n"
        content_body = f"🏆 **BFL TUESDAY MORNING HANGOVER (NOTEBOOK LM EDITION)**\n\n⏱️ **Duration:** `{mins}m {secs}s`\n\n{notes_str}{rss_line}🎧 Listen to the Week {week_num} Recap below: 👇"

        with open(compressed_audio_path, 'rb') as f_mp3:
            files = {
                'files[0]': (f"bfl_audio_week_{week_num}.mp3", f_mp3, 'audio/mpeg')
            }
            if edit_thread_id:
                print(f"✏️ Updating existing Discord thread/post {edit_thread_id} in place...")
                payload_json = json.dumps({'content': content_body, 'attachments': []})
                r = requests.patch(
                    f"{webhook}/messages/{edit_thread_id}?thread_id={edit_thread_id}",
                    data={'payload_json': payload_json},
                    files=files
                )
            else:
                print("🚀 Uploading new thread to Discord...")
                data = {
                    'username': 'BFL Studio Desk',
                    'thread_name': thread_title,
                    'content': content_body
                }
                r = requests.post(webhook + "?wait=true", data=data, files=files)
            if r.status_code in [200, 204]:
                print("✅ Discord upload/update complete!")
            else:
                print(f"❌ Discord Upload Failed! Status: {r.status_code}")
                print(f"Response: {r.text}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--season", type=int, default=2026)
    parser.add_argument("--week", type=int, default=5)
    parser.add_argument("--audio", type=str, required=True, help="Path to the NotebookLM MP3, M4A, or WAV file")
    parser.add_argument("--notes", type=str, default="", help="Optional notes to include in the Discord message")
    parser.add_argument("--edit-thread-id", type=str, default="", help="Optional Discord thread/message ID to update in place")
    parser.add_argument("--rss", action="store_true", help="Also encode 128k HQ MP3 & update output/rss/feed.xml")
    parser.add_argument("--rss-base-url", type=str, default="", help="Public HTTPS base URL for private RSS feed")
    args = parser.parse_args()
    publish_notebook_audio(
        args.season,
        args.week,
        args.audio,
        args.notes,
        args.edit_thread_id,
        update_rss=args.rss,
        rss_base_url=args.rss_base_url,
    )
