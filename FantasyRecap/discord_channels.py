#!/usr/bin/env python3
"""
BFL Discord Multi-Channel Dispatcher
====================================
Routes messages, embeds, and reports to their dedicated Discord channels:
- #commissioner-desk (Thursday Vegas Spreads, Season Preview, Power Rankings)
- #live-game-desk (In-game >= 25% shockwaves & Sunday panic boards)
- #press-room-podcast (Weekly 10-min AI Podcast drops & manager interviews)
"""

import os
import requests
from pathlib import Path
from dotenv import load_dotenv

# Load .env
env_path = Path(__file__).resolve().parent.parent / '.env'
if env_path.exists():
    load_dotenv(dotenv_path=env_path)
else:
    load_dotenv()

DEFAULT_WEBHOOK = os.getenv("DISCORD_WEBHOOK_URL", "")
COMMISH_WEBHOOK = os.getenv("DISCORD_WEBHOOK_COMMISH") or DEFAULT_WEBHOOK
LIVE_WEBHOOK = os.getenv("DISCORD_WEBHOOK_LIVE") or DEFAULT_WEBHOOK
PODCAST_WEBHOOK = os.getenv("DISCORD_WEBHOOK_PODCAST") or DEFAULT_WEBHOOK

def send_to_channel(channel_type: str, payload: dict) -> bool:
    """
    Sends payload to the appropriate webhook based on channel_type:
    'commish', 'live', 'podcast'
    """
    channel_type = channel_type.lower()
    if channel_type in ['commish', 'commissioner', 'announcements']:
        url = COMMISH_WEBHOOK
    elif channel_type in ['live', 'game_desk', 'scoring']:
        url = LIVE_WEBHOOK
    elif channel_type in ['podcast', 'press_room', 'episodes']:
        url = PODCAST_WEBHOOK
    else:
        url = DEFAULT_WEBHOOK
        
    if not url:
        print(f"ℹ️ No webhook URL configured for channel type '{channel_type}'.")
        return False
        
    # Forum channels require thread_name if creating a top-level post
    if channel_type in ['podcast', 'press_room', 'episodes'] and 'thread_name' not in payload:
        payload['thread_name'] = "🎙️ BFL Sunday Night Prime Podcast & Press Room"
        
    try:
        resp = requests.post(url, json=payload, timeout=12)
        return resp.status_code in [200, 204]
    except Exception as e:
        print(f"❌ Error sending to Discord ({channel_type}): {e}")
        return False

if __name__ == "__main__":
    print("BFL Multi-Channel Dispatcher Configured:")
    print("• Commish Webhook:", "Configured" if COMMISH_WEBHOOK else "Missing")
    print("• Live Webhook:", "Configured" if LIVE_WEBHOOK else "Missing")
    print("• Podcast Webhook:", "Configured" if PODCAST_WEBHOOK else "Missing")
