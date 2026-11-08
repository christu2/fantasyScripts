#!/usr/bin/env python3
"""
BFL NFL-Schedule-Aware Automation Orchestrator
==============================================
Automatically manages the entire weekly BFL broadcast & Discord bot lifecycle
by reading the real-time NFL schedule from ESPN's Scoreboard API:

1. Thursday Pre-TNF Package (Thu >= 10:00 AM CT):
   - Posts Entering-Week Playoff Odds, Leverage & Draft Stakes (`playoff_odds_simulator.py`)
   - Posts Thursday Matchup & Lineup Preview + Upset Poll (`thursday_lineup_preview.py`)
2. Live Game Desk Auto-Polling (Whenever ANY NFL Game is LIVE):
   - Automatically wakes up during TNF, Sunday International/Early/Late/SNF,
     Saturday games, and MNF to poll win probabilities every 60s and fire
     #live-game-desk alerts. Automatically stops as soon as the active slate ends!
3. Sunday Night Post-SNF Miracle & Clinch Board (Sun Night right after SNF hits FINAL):
   - Automatically posts the Entering-MNF Miracle & Clinch Board (`--mnf-board`)
     to #live-game-desk as soon as Sunday Night Football ends.
4. Post-MNF Podcast Scoreboard Generator (Mon Night ~15 mins after MNF hits FINAL):
   - Automatically generates `output/notebook_payload_week_{W}_{Y}.md` and
     `output/notebook_customize_prompt_week_{W}_{Y}.txt` after MNF and post-game chats.
5. Tuesday Morning Final Refresh (Tue >= 6:45 AM CT):
   - Re-generates the podcast Scoreboard payload on Tuesday morning to capture
     any late-night Monday/Tuesday morning Discord trash talk and stat corrections.

Usage:
  python3 bfl_auto_scheduler.py                 # Run one orchestrator check (ideal for cron every 5 mins)
  python3 bfl_auto_scheduler.py --status        # Show current NFL week, game windows & milestone state
  python3 bfl_auto_scheduler.py --install-cron  # Install automatic */5 * * * * cron job on macOS
  python3 bfl_auto_scheduler.py --uninstall-cron# Remove automatic cron job
"""

import os
import sys
import json
import time
import argparse
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
import requests

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(PROJECT_ROOT))
STATE_DIR = SCRIPT_DIR / "live_state"
STATE_DIR.mkdir(exist_ok=True)
LOCK_FILE = STATE_DIR / "bfl_auto_scheduler.lock"
CT_TZ = ZoneInfo("America/Chicago")

CRON_MARKER = "# BFL_AUTO_SCHEDULER_CRON"


def fetch_current_nfl_slate(override_season: int = None, override_week: int = None) -> dict:
    """
    Queries ESPN's NFL Scoreboard API to determine the active NFL season, week,
    and the exact status/kickoff times of every NFL game in the week.
    """
    base_url = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard"
    params = {}
    if override_season and override_week:
        params = {"dates": override_season, "seasontype": 2, "week": override_week}

    resp = requests.get(base_url, params=params, timeout=10)
    resp.raise_for_status()
    data = resp.json()

    season_yr = override_season or int(data.get("season", {}).get("year", 2026))
    season_type = int(data.get("season", {}).get("type", 2))
    week_num = override_week or int(data.get("week", {}).get("number", 1))

    games = []
    for ev in data.get("events", []):
        ev_id = str(ev.get("id", ""))
        name = ev.get("shortName") or ev.get("name", "NFL Game")
        date_str = ev.get("date", "")
        kickoff_ct = None
        if date_str:
            try:
                kickoff_ct = datetime.fromisoformat(date_str.replace("Z", "+00:00")).astimezone(CT_TZ)
            except Exception:
                pass

        st_type = ev.get("status", {}).get("type", {})
        state = st_type.get("state", "pre")  # 'pre', 'in', 'post'
        detail = st_type.get("shortDetail") or st_type.get("description", "")

        games.append({
            "id": ev_id,
            "name": name,
            "kickoff_ct": kickoff_ct,
            "weekday": kickoff_ct.weekday() if kickoff_ct else -1,  # Mon=0 .. Thu=3 .. Sun=6
            "state": state,
            "detail": detail,
        })

    games.sort(key=lambda g: g["kickoff_ct"] or datetime.now(CT_TZ))
    return {
        "season": season_yr,
        "season_type": season_type,
        "week": week_num,
        "games": games,
    }


def load_scheduler_state(season: int, week: int) -> dict:
    path = STATE_DIR / f"scheduler_state_{season}_week_{week}.json"
    if path.exists():
        try:
            with open(path, "r") as f:
                return json.load(f)
        except Exception:
            pass

    # Auto-detect if artifacts were already generated earlier in the week
    out_dir = SCRIPT_DIR / "output"
    thu_done = (out_dir / f"thursday_preview_week_{week}_{season}.md").exists()
    po_done = (out_dir / f"playoff_odds_entering_week_{week}_{season}.md").exists()

    mnf_board_done = False
    alerts_log = STATE_DIR / f"alerts_log_{season}_week_{week}.jsonl"
    if alerts_log.exists():
        try:
            with open(alerts_log, "r") as lf:
                for line in lf:
                    if '"MNF_BOARD"' in line:
                        mnf_board_done = True
                        break
        except Exception:
            pass

    return {
        "season": season,
        "week": week,
        "thursday_preview_posted": bool(thu_done and po_done),
        "mnf_board_posted": mnf_board_done,
        "post_mnf_payload_generated": False,
        "tuesday_morning_payload_refreshed": False,
        "last_live_tick_at": None,
        "all_games_final_first_seen_epoch": None,
    }


def save_scheduler_state(season: int, week: int, st: dict):
    path = STATE_DIR / f"scheduler_state_{season}_week_{week}.json"
    with open(path, "w") as f:
        json.dump(st, f, indent=2)


def is_lock_active() -> bool:
    if not LOCK_FILE.exists():
        return False
    try:
        pid = int(LOCK_FILE.read_text().strip())
        os.kill(pid, 0)
        return True
    except Exception:
        try:
            LOCK_FILE.unlink()
        except Exception:
            pass
        return False


def acquire_lock() -> bool:
    if is_lock_active():
        return False
    LOCK_FILE.write_text(str(os.getpid()))
    return True


def release_lock():
    try:
        if LOCK_FILE.exists() and LOCK_FILE.read_text().strip() == str(os.getpid()):
            LOCK_FILE.unlink()
    except Exception:
        pass


def run_py_script(args_list: list, label: str) -> bool:
    print(f"🚀 [{datetime.now(CT_TZ).strftime('%a %m/%d %I:%M:%S %p CT')}] Running {label}: {' '.join(args_list)}")
    res = subprocess.run(
        [sys.executable] + args_list,
        cwd=str(SCRIPT_DIR),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    print(res.stdout)
    if res.returncode == 0:
        print(f"✅ Completed: {label}")
        return True
    else:
        print(f"❌ Failed ({res.returncode}): {label}")
        return False


def check_and_run_orchestrator(override_season: int = None, override_week: int = None):
    now_ct = datetime.now(CT_TZ)
    slate = fetch_current_nfl_slate(override_season, override_week)
    season = slate["season"]
    week = slate["week"]
    games = slate["games"]

    # Only run during regular season & fantasy playoffs (Weeks 1 to 17)
    if slate["season_type"] != 2 or week < 1 or week > 17:
        print(f"ℹ️ Outside active BFL fantasy weeks (Season {season}, Type {slate['season_type']}, Week {week}).")
        return

    st = load_scheduler_state(season, week)
    save_scheduler_state(season, week, st)

    # -------------------------------------------------------------------------
    # MILESTONE 1: Thursday Pre-TNF Preview & Playoff Odds (Thu >= 10:00 AM CT)
    # -------------------------------------------------------------------------
    if not st.get("thursday_preview_posted"):
        # Trigger on Thursday at/after 10:00 AM CT (or Friday/Saturday if missed) before Sunday games start
        if (now_ct.weekday() == 3 and now_ct.hour >= 10) or (now_ct.weekday() in (4, 5)):
            ok_po = True
            if 2 <= week <= 14:
                ok_po = run_py_script(
                    ["playoff_odds_simulator.py", "--season", str(season), "--week", str(week - 1), "--discord"],
                    f"Week {week} Playoff Odds & Leverage Board",
                )
            ok_thu = run_py_script(
                ["thursday_lineup_preview.py", "--season", str(season), "--week", str(week), "--discord"],
                f"Week {week} Thursday Lineup Preview & Upset Poll",
            )
            if ok_thu:
                st["thursday_preview_posted"] = True
                save_scheduler_state(season, week, st)

    # -------------------------------------------------------------------------
    # MILESTONE 2: Live Game Desk Polling Whenever ANY NFL Game is LIVE
    # -------------------------------------------------------------------------
    live_games = [g for g in games if g["state"] == "in"]
    imminent_games = [
        g for g in games
        if g["state"] == "pre"
        and g["kickoff_ct"] is not None
        and 0 <= (g["kickoff_ct"] - now_ct).total_seconds() <= 300
    ]

    if live_games or imminent_games:
        names = ", ".join([g["name"] for g in (live_games + imminent_games)])
        print(f"⚡ Active/Imminent NFL Games detected for Week {week} ({names})! Entering 60s Live Game Desk loop...")
        from FantasyRecap.live_game_desk_daemon import run_live_desk_tick

        while True:
            try:
                count = run_live_desk_tick(season, week)
                now_str = datetime.now(CT_TZ).strftime("%Y-%m-%d %H:%M:%S CT")
                print(f"[{now_str}] Live Desk Tick: Monitored {count} matchups.")
                st["last_live_tick_at"] = now_str
                save_scheduler_state(season, week, st)
            except Exception as e:
                print(f"⚠️ Error in live game desk tick: {e}")

            time.sleep(60)
            # Re-check NFL scoreboard to see if games are still in progress
            try:
                cur_slate = fetch_current_nfl_slate(season, week)
                still_live = [g for g in cur_slate["games"] if g["state"] == "in"]
                still_imminent = [
                    g for g in cur_slate["games"]
                    if g["state"] == "pre"
                    and g["kickoff_ct"] is not None
                    and 0 <= (g["kickoff_ct"] - datetime.now(CT_TZ)).total_seconds() <= 300
                ]
                if not still_live and not still_imminent:
                    print("🏁 All active NFL games in this window have reached FINAL! Running one final state sync...")
                    run_live_desk_tick(season, week)
                    st["last_live_tick_at"] = datetime.now(CT_TZ).strftime("%Y-%m-%d %H:%M:%S CT")
                    save_scheduler_state(season, week, st)
                    slate = cur_slate
                    games = slate["games"]
                    break
            except Exception as e:
                print(f"⚠️ Scoreboard re-check error: {e}")

    # Re-evaluate game states after any live polling loop finishes
    non_monday_games = [g for g in games if g["weekday"] in (2, 3, 4, 5, 6)]
    monday_games = [g for g in games if g["weekday"] == 0]

    all_non_monday_final = bool(non_monday_games) and all(g["state"] == "post" for g in non_monday_games)
    any_monday_remaining = any(g["state"] in ("pre", "in") for g in monday_games)
    all_games_final = bool(games) and all(g["state"] == "post" for g in games)

    # -------------------------------------------------------------------------
    # MILESTONE 3: Sunday Night Post-SNF "Entering MNF Miracle & Clinch Board"
    # -------------------------------------------------------------------------
    if all_non_monday_final and any_monday_remaining and not st.get("mnf_board_posted"):
        ok_mnf = run_py_script(
            ["live_game_desk_daemon.py", "--season", str(season), "--week", str(week), "--mnf-board"],
            f"Week {week} Entering-MNF Miracle & Clinch Board",
        )
        if ok_mnf:
            st["mnf_board_posted"] = True
            save_scheduler_state(season, week, st)

    # -------------------------------------------------------------------------
    # MILESTONE 4: Monday Night Post-MNF Podcast Scoreboard (15 mins after MNF)
    # -------------------------------------------------------------------------
    if all_games_final:
        now_epoch = time.time()
        if not st.get("all_games_final_first_seen_epoch"):
            st["all_games_final_first_seen_epoch"] = now_epoch
            save_scheduler_state(season, week, st)
            # Also run one final live_game_desk_daemon tick so state_{season}_week_{week}.json is 100% final
            run_py_script(
                ["live_game_desk_daemon.py", "--season", str(season), "--week", str(week), "--once"],
                f"Week {week} Final Post-MNF State Sync",
            )

        elapsed_since_final = now_epoch - float(st["all_games_final_first_seen_epoch"])
        # Wait ~12 minutes after MNF ends so immediate post-MNF #trash-talk reactions are captured!
        if elapsed_since_final >= 720 and not st.get("post_mnf_payload_generated"):
            ok_pod = run_py_script(
                ["generate_notebook_payload.py", "--season", str(season), "--week", str(week)],
                f"Week {week} Post-MNF Podcast Scoreboard Payload",
            )
            if ok_pod:
                st["post_mnf_payload_generated"] = True
                save_scheduler_state(season, week, st)

        # ---------------------------------------------------------------------
        # MILESTONE 5: Tuesday Morning Final Refresh (Tue >= 6:45 AM CT)
        # ---------------------------------------------------------------------
        if now_ct.weekday() == 1 and (now_ct.hour > 6 or (now_ct.hour == 6 and now_ct.minute >= 45)):
            if not st.get("tuesday_morning_payload_refreshed"):
                ok_tue = run_py_script(
                    ["generate_notebook_payload.py", "--season", str(season), "--week", str(week)],
                    f"Week {week} Tuesday Morning Final Scoreboard Refresh",
                )
                if ok_tue:
                    st["tuesday_morning_payload_refreshed"] = True
                    save_scheduler_state(season, week, st)


def print_status(override_season: int = None, override_week: int = None):
    now_ct = datetime.now(CT_TZ)
    slate = fetch_current_nfl_slate(override_season, override_week)
    season = slate["season"]
    week = slate["week"]
    games = slate["games"]
    st = load_scheduler_state(season, week)
    save_scheduler_state(season, week, st)

    print("=" * 78)
    print(f"🤖 BFL NFL-SCHEDULE-AWARE AUTO-SCHEDULER STATUS (Season {season}, Week {week})")
    print(f"🕒 Current Time: {now_ct.strftime('%A, %b %d, %Y %I:%M:%S %p CT')}")
    print("=" * 78)

    # Check cron status
    res = subprocess.run(["crontab", "-l"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    cron_installed = CRON_MARKER in (res.stdout or "")
    print(f"⚙️  macOS Cron Job Installed: {'✅ YES (Runs every 5 minutes)' if cron_installed else '❌ NO (Run with --install-cron)'}")
    print(f"🔒 Active Live-Game Loop Running Right Now: {'⚡ YES' if is_lock_active() else '💤 Idle (Waiting for next kickoff/milestone)'}")
    print("-" * 78)
    print("📋 WEEKLY MILESTONE CHECKLIST:")
    print(f"  1. Thursday Pre-TNF Preview & Playoff Odds : {'✅ DONE' if st.get('thursday_preview_posted') else '⏳ PENDING (Fires Thu >= 10:00 AM CT)'}")
    print(f"  2. Last Live Game Desk Poll Tick           : {st.get('last_live_tick_at') or '⏳ Waiting for kickoff'}")
    print(f"  3. Sunday Post-SNF Entering-MNF Board      : {'✅ DONE' if st.get('mnf_board_posted') else '⏳ PENDING (Fires immediately after SNF hits FINAL)'}")
    print(f"  4. Monday Post-MNF Podcast Scoreboard      : {'✅ DONE' if st.get('post_mnf_payload_generated') else '⏳ PENDING (Fires 12 mins after MNF hits FINAL)'}")
    print(f"  5. Tuesday 6:45 AM CT Morning Refresh      : {'✅ DONE' if st.get('tuesday_morning_payload_refreshed') else '⏳ PENDING (Fires Tue >= 6:45 AM CT)'}")
    print("-" * 78)
    print(f"🏈 NFL WEEK {week} GAME WINDOWS ({len(games)} Games):")
    for g in games:
        k_str = g["kickoff_ct"].strftime("%a %m/%d %I:%M %p CT") if g["kickoff_ct"] else "TBD"
        icon = "🟢 LIVE" if g["state"] == "in" else ("🏁 FINAL" if g["state"] == "post" else "🗓️ PRE")
        print(f"  • [{k_str}] {icon:8s} | {g['name']:15s} ({g['detail']})")
    print("=" * 78)


def install_cron():
    python_bin = sys.executable
    script_path = str(Path(__file__).resolve())
    log_path = str(STATE_DIR / "auto_scheduler_cron.log")
    cron_line = f"*/5 * * * * {python_bin} {script_path} >> {log_path} 2>&1 {CRON_MARKER}"

    res = subprocess.run(["crontab", "-l"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    existing_lines = [
        line for line in (res.stdout or "").splitlines()
        if CRON_MARKER not in line and line.strip()
    ]
    existing_lines.append(cron_line)
    new_crontab = "\n".join(existing_lines) + "\n"

    proc = subprocess.run(["crontab", "-"], input=new_crontab, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if proc.returncode == 0:
        print("✅ Installed BFL Auto-Scheduler into macOS crontab!")
        print(f"   Schedule : Every 5 minutes (`*/5 * * * *`)")
        print(f"   Command  : {python_bin} {script_path}")
        print(f"   Log File : {log_path}")
    else:
        print(f"❌ Failed to install crontab: {proc.stderr}")


def uninstall_cron():
    res = subprocess.run(["crontab", "-l"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    existing_lines = [
        line for line in (res.stdout or "").splitlines()
        if CRON_MARKER not in line and line.strip()
    ]
    new_crontab = ("\n".join(existing_lines) + "\n") if existing_lines else ""
    proc = subprocess.run(["crontab", "-"], input=new_crontab, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if proc.returncode == 0:
        print("🛑 Removed BFL Auto-Scheduler from macOS crontab.")
    else:
        print(f"❌ Failed to update crontab: {proc.stderr}")


def main():
    parser = argparse.ArgumentParser(description="BFL NFL-Schedule-Aware Automation Orchestrator")
    parser.add_argument("--season", type=int, default=None, help="Override NFL season year")
    parser.add_argument("--week", type=int, default=None, help="Override NFL week number")
    parser.add_argument("--status", action="store_true", help="Show current NFL week, game windows & milestone state")
    parser.add_argument("--install-cron", action="store_true", help="Install automatic */5 * * * * cron job in macOS crontab")
    parser.add_argument("--uninstall-cron", action="store_true", help="Remove automatic cron job from macOS crontab")
    args = parser.parse_args()

    if args.install_cron:
        install_cron()
        print_status(args.season, args.week)
        return
    if args.uninstall_cron:
        uninstall_cron()
        return
    if args.status:
        print_status(args.season, args.week)
        return

    if not acquire_lock():
        # Another instance is already running (e.g., currently polling live NFL games every 60s)
        return

    try:
        check_and_run_orchestrator(args.season, args.week)
    finally:
        release_lock()


if __name__ == "__main__":
    main()
