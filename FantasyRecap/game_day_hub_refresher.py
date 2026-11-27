#!/usr/bin/env python3
"""
Multi-League Live Game-Day Hub Refresher (Mac Mini Cron & GCP Cloud Run Ready)
==============================================================================
Reads `FantasyRecap/leagues_registry.json` and refreshes ALL enabled ESPN leagues
with live in-progress scores, tightened remaining variance, and real-time Playoff /
Bye / Title Odds during active NFL game windows (or unconditionally with `--force`):

Active NFL Live Windows (US Central Time):
  - Thursday : 11:30 AM – 11:30 PM CT (covers Thanksgiving + TNF!)
  - Friday   : 2:00 PM – 7:00 PM CT (Black Friday NFL!)
  - Saturday : 12:00 PM – 11:30 PM CT (late-season Saturday NFL!)
  - Sunday   : 12:00 PM – 11:30 PM CT (all Sunday slates + SNF!)
  - Monday   : 7:00 PM – 11:30 PM CT (MNF!)

Works identically on:
  1. Mac Mini `cron` (`bfl_auto_scheduler.py` calls it every 20 min during NFL games)
  2. GCP Free-Tier Cloud Run Jobs + Cloud Scheduler (`python3 FantasyRecap/game_day_hub_refresher.py --force`)

Usage:
  python3 FantasyRecap/game_day_hub_refresher.py              # Only runs during live NFL game windows
  python3 FantasyRecap/game_day_hub_refresher.py --force      # Force immediate refresh of all leagues
  python3 FantasyRecap/game_day_hub_refresher.py --slug jff --force
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from FantasyRecap.core.manifest import load_project_env
load_project_env()

from FantasyRecap.config import CURRENT_YEAR

REGISTRY_PATH = SCRIPT_DIR / "leagues_registry.json"
STATE_PATH = SCRIPT_DIR / "live_state" / "gameday_refresh_state.json"
CT_TZ = ZoneInfo("America/Chicago")


def is_nfl_live_window(now_ct: datetime = None) -> bool:
    """Returns True during active NFL broadcast windows (US Central Time)."""
    dt = now_ct or datetime.now(CT_TZ)
    wd = dt.weekday()  # Mon=0 .. Sun=6
    hr = dt.hour + (dt.minute / 60.0)
    if wd == 6 and 12.0 <= hr <= 23.5:   # Sunday
        return True
    if wd == 0 and 19.0 <= hr <= 23.5:   # Monday Night Football
        return True
    if wd == 3 and 11.5 <= hr <= 23.5:   # Thursday (TNF & Thanksgiving)
        return True
    if wd == 4 and 14.0 <= hr <= 19.0:   # Black Friday NFL
        return True
    if wd == 5 and 12.0 <= hr <= 23.5:   # December Saturday NFL
        return True
    return False


def _load_registry() -> dict:
    return json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))


def _should_refresh_now(interval_min: int, force: bool, bucket: str = "") -> bool:
    if force:
        return True
    if not is_nfl_live_window():
        return False
    last_ts = 0.0
    if STATE_PATH.exists():
        try:
            st = json.loads(STATE_PATH.read_text(encoding="utf-8"))
            last_ts = float(st.get("last_run_epoch", 0.0))
        except Exception:
            pass
    elif bucket:
        try:
            import requests
            r = requests.get(f"https://storage.googleapis.com/{bucket}/telemetry/_hub_refresh_state.json", timeout=5)
            if r.status_code == 200:
                last_ts = float(r.json().get("last_run_epoch", 0.0))
        except Exception:
            pass
    if last_ts > 0 and (time.time() - last_ts) < (interval_min * 60 - 15):
        return False
    return True


def _detect_completed_week_for_bfl(season: int, league_id: str = "") -> int:
    from FantasyRecap.core.manifest import get_espn_credentials
    from FantasyRecap.multi_league_hub import discover_league_settings
    creds = get_espn_credentials()
    lid = str(league_id or creds["league_id"])
    cfg, _ = discover_league_settings(lid, creds["cookies"], season, "bfl")
    return max(1, int(cfg["completed_week"]))


def refresh_all_leagues(
    *,
    season: int = int(CURRENT_YEAR),
    slug_filter: str = "",
    force: bool = False,
    deploy: bool = True,
) -> dict:
    reg = _load_registry()
    bucket = reg.get("hub_bucket", "bfl-hub-7x9k2")
    project = reg.get("gcp_project", "bfl-fantasy")
    interval = int(reg.get("live_refresh_interval_minutes", 20))
    if not _should_refresh_now(interval, force=force, bucket=bucket):
        return {"ran": False, "reason": "outside_live_window_or_cooldown"}

    results = {}

    for lg in reg.get("leagues", []):
        if not lg.get("enabled", True):
            continue
        slug = lg["slug"]
        if slug_filter and slug != slug_filter:
            continue
        try:
            if lg.get("builder") == "bfl":
                from FantasyRecap.build_league_hub import (
                    DB_PATH,
                    build_hub_data,
                    deploy_hub_to_gcs,
                    refresh_bfl_live_overlay_from_gcs,
                )
                cwk = _detect_completed_week_for_bfl(season, lg.get("espn_league_id", ""))
                if DB_PATH.exists():
                    build_hub_data(season, cwk)
                    url = deploy_hub_to_gcs(bucket, project_id=project) if deploy else "local"
                    results[slug] = {"ok": True, "mode": "full", "completed_week": cwk, "url": url}
                elif deploy:
                    url = refresh_bfl_live_overlay_from_gcs(
                        season, cwk, bucket_name=bucket, project_id=project, league_id=str(lg.get("espn_league_id") or "")
                    )
                    results[slug] = {"ok": True, "mode": "stateless_live_overlay", "completed_week": cwk, "url": url}
                else:
                    results[slug] = {"ok": True, "mode": "skipped_no_local_db", "completed_week": cwk}
            else:
                from FantasyRecap.multi_league_hub import build_generic_hub_data, deploy_multi_league_to_gcs
                hub_data, data_path = build_generic_hub_data(lg["espn_league_id"], slug, season)
                url = deploy_multi_league_to_gcs(slug, data_path, bucket, project_id=project, hub_data=hub_data) if deploy else "local"
                results[slug] = {"ok": True, "completed_week": hub_data["completed_week"], "url": url}
        except Exception as exc:
            print(f"❌ Failed refreshing league '{slug}': {exc}")
            results[slug] = {"ok": False, "error": str(exc)}

    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    state_payload = json.dumps(
        {"last_run_epoch": time.time(), "last_run_iso": datetime.now(CT_TZ).isoformat(), "results": results},
        indent=2,
    )
    STATE_PATH.write_text(state_payload, encoding="utf-8")
    if deploy:
        try:
            from FantasyRecap.build_league_hub import _upload_blob_sdk_or_cli
            _upload_blob_sdk_or_cli(
                STATE_PATH, bucket, "telemetry/_hub_refresh_state.json",
                "application/json; charset=utf-8", "no-cache, max-age=30", project,
            )
        except Exception:
            pass
    return {"ran": True, "results": results}


def main():
    parser = argparse.ArgumentParser(description="Multi-League Live Game-Day Hub Refresher")
    parser.add_argument("--season", type=int, default=int(CURRENT_YEAR))
    parser.add_argument("--slug", type=str, default="", help="Optional single league slug to refresh")
    parser.add_argument("--force", action="store_true", help="Ignore live NFL window & cooldown")
    parser.add_argument("--no-deploy", action="store_true", help="Build JSON locally without GCS upload")
    args = parser.parse_args()

    res = refresh_all_leagues(season=args.season, slug_filter=args.slug, force=args.force, deploy=not args.no_deploy)
    print(json.dumps(res, indent=2))


if __name__ == "__main__":
    main()
