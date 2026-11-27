#!/usr/bin/env python3
"""
GCP Free-Tier Serverless Cloud Run Job Entrypoint
=================================================
Runs on Cloud Scheduler every 5 minutes (only doing ESPN/GCS work during active
NFL live windows — exiting in <0.2s outside game windows to stay < 7% of GCP
Always Free Tier / $0.00/mo!):

  1. Polls `live_event_ledger.py` for ALL enabled leagues in `leagues_registry.json`
     (`bfl`, `jff`, etc.) and appends play-by-play shockwaves, lead changes,
     nuclear explosions, injury exits, and prime-time sweats to GCS JSONL.
  2. Runs `game_day_hub_refresher.py` (with GCS-persisted 20-minute cooldown) to
     refresh live mid-game scores and real-time Playoff / Title Odds on all public
     web hubs (`gs://bfl-hub-7x9k2/index.html`, `.../jff/index.html`).

Environment Variables (injected via Cloud Run Job + Secret Manager):
  - `ESPN_S2`          : ESPN Fantasy session cookie
  - `ESPN_SWID`        : ESPN Fantasy SWID cookie
  - `FORCE_RUN`        : Optional ("1") to force execution outside live NFL windows
  - `SKIP_HUB_REFRESH` : Optional ("1") to run telemetry ledger only
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from FantasyRecap.config import CURRENT_YEAR
from FantasyRecap.game_day_hub_refresher import is_nfl_live_window, refresh_all_leagues
from FantasyRecap.live_event_ledger import poll_all_league_ledgers


def main() -> int:
    force = os.getenv("FORCE_RUN", "").strip().lower() in ("1", "true", "yes")
    skip_hub = os.getenv("SKIP_HUB_REFRESH", "").strip().lower() in ("1", "true", "yes")
    season = int(os.getenv("SEASON_YEAR") or CURRENT_YEAR)

    if not force and not is_nfl_live_window():
        print("💤 Outside active NFL live window — exiting cleanly (<1s, $0.00).")
        return 0

    print(f"⚡ Cloud Run Game-Day Poller starting (season={season}, force={force})...")
    ledger_res = poll_all_league_ledgers(season=season, force=force, sync_gcs=True)
    print("📡 Event Ledger Result:", json.dumps(ledger_res))

    hub_res = {"ran": False, "reason": "skipped"}
    if not skip_hub:
        hub_res = refresh_all_leagues(season=season, force=force, deploy=True)
        print("🌐 Hub Refresher Result:", json.dumps(hub_res))

    # Exit non-zero only if every league failed in ledger poll
    l_results = ledger_res.get("results", {})
    if l_results and all(not v.get("ok") for v in l_results.values()):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
