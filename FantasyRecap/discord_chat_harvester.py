#!/usr/bin/env python3
"""
BFL Discord Chat & Banter Harvester
===================================
Fetches recent chat messages, trash talk, and reactions from Discord channels
(#trash-talk, #general, #live-game-desk) to incorporate real league drama
into Tuesday Morning Hangover episodes.
"""

import os
import requests
from pathlib import Path
from dotenv import load_dotenv

env_path = Path(__file__).resolve().parent.parent / '.env'
if env_path.exists():
    load_dotenv(dotenv_path=env_path)
else:
    load_dotenv()

BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN", "")

def fetch_recent_channel_messages(channel_id: str, limit: int = 30) -> list:
    """Fetches the latest messages from a Discord channel using bot token."""
    if not BOT_TOKEN:
        return []
        
    url = f"https://discord.com/api/v10/channels/{channel_id}/messages?limit={limit}"
    headers = {"Authorization": f"Bot {BOT_TOKEN}"}
    try:
        r = requests.get(url, headers=headers, timeout=10)
        if r.status_code == 200:
            msgs = r.json()
            extracted = []
            for m in reversed(msgs):
                # Ignore bot posts
                if m.get('author', {}).get('bot'):
                    continue
                user = m.get('author', {}).get('global_name') or m.get('author', {}).get('username', 'Unknown')
                content = m.get('content', '').strip()
                if content:
                    extracted.append(f"{user}: \"{content}\"")
            return extracted
    except Exception as e:
        print(f"⚠️ Error fetching Discord messages: {e}")
    return []

def get_sample_trash_talk_banter():
    """Fallback / sample real-world trash talk quotes when bot token is not yet connected."""
    return [
        "Dino: \"Nick's kicker had 14 points on Monday night. Pure luck as usual.\"",
        "Thomas: \"Xavier Worthy put up a literal donut. I am dropping him to waivers at 3 AM.\"",
        "Rej: \"Josh Allen has 39 points and I am still losing. I hate fantasy football so much.\"",
        "Adam: \"Thank you Emelie for benching Wan'Dale Robinson! Best win of my life.\"",
        "Sydney: \"Who told Shawn Lukose to start Drake Maye over Justin Fields? Show yourself 😂\"",
        "Saagar: \"18 years and counting... Patrick Mahomes tried but AJ Brown destroyed my season.\""
    ]

if __name__ == "__main__":
    print("BFL Discord Chat Harvester Ready.")
    sample = get_sample_trash_talk_banter()
    for s in sample:
        print("💬", s)
