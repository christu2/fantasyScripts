#!/usr/bin/env python3
"""
Pluggable League Chat Adapters & Chronological Timeline Merger
==============================================================
Decouples league chat integrations (Discord, Sleeper, etc.) from core ESPN
game telemetry so:
  1. Leagues WITHOUT any chat integration still get rich, timestamped game
     timelines (shockwaves, lead changes, nuclear games, injury exits, sweats)
     sourced purely from GCS `telemetry/<season>_week_<w>_events.jsonl`.
  2. Leagues WITH an optional chat provider (`chat.provider: "discord"` in
     `leagues_registry.json`) seamlessly interleave manager trash talk into
     the exact chronological game timeline!
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo

import requests

SCRIPT_DIR = Path(__file__).resolve().parent
TELEMETRY_DIR = SCRIPT_DIR / "live_state" / "telemetry"
CT_TZ = ZoneInfo("America/Chicago")


def load_league_game_events(
    slug: str,
    season: int,
    week: int,
    *,
    bucket: str = "bfl-hub-7x9k2",
    gcs_subpath: Optional[str] = None,
) -> List[dict]:
    """Loads canonical game events JSONL from local cache or GCS telemetry rail."""
    local_file = TELEMETRY_DIR / f"{slug}_{season}_week_{week}_events.jsonl"
    raw_text = ""
    if local_file.exists():
        raw_text = local_file.read_text(encoding="utf-8")
    else:
        sub = (slug if gcs_subpath is None else gcs_subpath).strip("/")
        prefix = f"{sub}/telemetry" if sub else "telemetry"
        url = f"https://storage.googleapis.com/{bucket}/{prefix}/{season}_week_{week}_events.jsonl"
        try:
            r = requests.get(url, timeout=6)
            if r.status_code == 200:
                raw_text = r.text
        except Exception:
            raw_text = ""

    events = []
    for line in raw_text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except Exception:
            continue
    events.sort(key=lambda e: float(e.get("ts", 0.0)))
    return events


def fetch_optional_chat_messages(
    chat_config: Optional[dict],
    *,
    start_ts: Optional[float] = None,
    end_ts: Optional[float] = None,
    limit: int = 100,
) -> List[dict]:
    """
    Optional chat adapter plugin. Returns normalized timestamped chat messages:
      `[{"ts": float, "time_ct": str, "type": "chat", "author": str, "text": str}]`
    Returns `[]` cleanly when `chat_config` is absent or `provider == "none"`.
    """
    if not chat_config:
        return []
    provider = str(chat_config.get("provider") or "none").lower()
    if provider == "none":
        return []

    if provider == "discord":
        ch_env = str(chat_config.get("channel_id_env") or "").strip()
        channel_id = str(chat_config.get("channel_id") or (os.getenv(ch_env, "") if ch_env else "")).strip()
        token_env = str(chat_config.get("bot_token_env") or "DISCORD_BOT_TOKEN")
        token = os.getenv(token_env, "").strip()
        if not (channel_id and token):
            return []
        try:
            r = requests.get(
                f"https://discord.com/api/v10/channels/{channel_id}/messages?limit={min(100, limit)}",
                headers={"Authorization": f"Bot {token}"},
                timeout=8,
            )
            if r.status_code != 200:
                return []
            msgs = []
            for m in r.json():
                if (m.get("author") or {}).get("bot"):
                    continue
                txt = (m.get("content") or "").strip()
                if not txt:
                    continue
                dt = datetime.fromisoformat(m["timestamp"].replace("Z", "+00:00"))
                ts = dt.timestamp()
                if start_ts is not None and ts < start_ts:
                    continue
                if end_ts is not None and ts > end_ts:
                    continue
                author = (m.get("author") or {}).get("global_name") or (m.get("author") or {}).get("username") or "Manager"
                msgs.append({
                    "ts": ts,
                    "time_ct": dt.astimezone(CT_TZ).strftime("%a %I:%M %p CT").replace(" 0", " "),
                    "type": "chat",
                    "author": author,
                    "text": txt[:280],
                })
            msgs.sort(key=lambda x: x["ts"])
            return msgs
        except Exception:
            return []

    return []


def merge_game_and_chat_timeline(
    game_events: List[dict],
    chat_messages: List[dict],
    *,
    matchup_owners: Optional[List[str]] = None,
    chat_window_sec: int = 900,
) -> List[str]:
    """
    Merges pure-ESPN game events and optional chat messages into one chronological
    narrative stream for the podcast scriptwriter!

    When `matchup_owners` is provided, filters game events to that matchup AND
    automatically attaches any chat message authored by one of those owners OR
    posted within `±chat_window_sec` (15 min) of a shockwave/nuclear play in that game.
    """
    owner_lows = {o.lower() for o in (matchup_owners or [])}
    first_lows = {o.split()[0].lower() for o in (matchup_owners or []) if o}

    rel_events = []
    event_timestamps = []
    for ev in game_events:
        if owner_lows:
            ev_owns = {o.lower() for o in (ev.get("owners") or [])}
            if not (ev_owns & owner_lows):
                continue
        rel_events.append(ev)
        if ev.get("type") in ("shockwave", "nuclear", "lead_change", "injury_exit"):
            event_timestamps.append(float(ev.get("ts", 0.0)))

    rel_chats = []
    for ch in chat_messages:
        if not owner_lows:
            rel_chats.append(ch)
            continue
        auth_low = str(ch.get("author") or "").lower()
        txt_low = str(ch.get("text") or "").lower()
        ts = float(ch.get("ts", 0.0))
        direct_match = any(fn in auth_low or fn in txt_low for fn in first_lows)
        near_play = any(abs(ts - ets) <= chat_window_sec for ets in event_timestamps)
        if direct_match or near_play:
            rel_chats.append(ch)

    combined = []
    for ev in rel_events:
        combined.append((
            float(ev.get("ts", 0.0)),
            f"[{ev.get('time_ct', ev.get('window', ''))}] {ev.get('type', 'EVENT').upper()}: {ev.get('summary', '')}",
        ))
    for ch in rel_chats:
        combined.append((
            float(ch.get("ts", 0.0)),
            f"[{ch.get('time_ct', '')}] CHAT ({ch.get('author', 'Manager')}): \"{ch.get('text', '')}\"",
        ))

    combined.sort(key=lambda x: x[0])
    return [line for _, line in combined]
