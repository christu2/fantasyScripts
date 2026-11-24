#!/usr/bin/env python3
"""
BFL Discord `/ask` Historian Bot
================================
Exposes the 18-year BFL SQLite Historical Analytics Warehouse (`bfl_history.db`)
and Gemini Text-to-SQL engine directly inside your league's Discord server:

  • `/ask <question> [show_sql]` — Ask anything about BFL history (2008–present)
  • `/bflstats`                  — Show database coverage & latest synced week
  • `!ask <question>`            — Prefix command fallback (if Message Content Intent enabled)

Designed to run continuously on your always-on Mac Mini alongside your cron job
using macOS LaunchAgent (`--install-launchd`).

Setup:
  1. pip install discord.py
  2. Ensure `DISCORD_BOT_TOKEN` and `GEMINI_API_KEY` are set in `.env`
     (Optional: set `DISCORD_GUILD_ID` in `.env` for instant slash-command sync!)
  3. Test locally without Discord:
       python3 bfl_historian_bot.py --dry-run "Who has the highest single-week score since 2018?"
  4. Run interactively:
       python3 bfl_historian_bot.py
  5. Install as an auto-restarting Mac Mini LaunchAgent:
       python3 bfl_historian_bot.py --install-launchd
"""

import argparse
import asyncio
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from FantasyRecap.core.manifest import load_project_env
load_project_env()

from FantasyRecap.bfl_history_db import (
    DB_PATH,
    answer_natural_language_question,
    sync_history_db,
)
from FantasyRecap.config import CURRENT_YEAR
from FantasyRecap.core.ops import get_logger, notify_ops

log = get_logger("bfl.historian_bot")

LAUNCHD_LABEL = "com.bfl.historian_bot"
LAUNCHD_PLIST_PATH = Path.home() / "Library" / "LaunchAgents" / f"{LAUNCHD_LABEL}.plist"
SYNC_TTL_SECONDS = 3 * 3600  # Refresh active season from ESPN at most once every 3 hours
USER_COOLDOWN_SECONDS = 12   # Prevent accidental spam bursts per user

_last_sync_epoch = 0.0
_user_last_ask: Dict[int, float] = {}


def maybe_refresh_current_season() -> None:
    """Incrementally syncs the current season if DB is stale (>3h old)."""
    global _last_sync_epoch
    now = time.time()
    if not DB_PATH.exists():
        sync_history_db(start_year=2008, quiet=True)
        _last_sync_epoch = now
        return
    if (now - _last_sync_epoch) >= SYNC_TTL_SECONDS:
        try:
            sync_history_db(start_year=int(os.getenv("SEASON_YEAR") or CURRENT_YEAR), quiet=True)
            _last_sync_epoch = now
        except Exception as exc:
            log.warning("Background ESPN sync skipped: %s", exc)


def get_warehouse_summary() -> dict:
    """Reads quick coverage metrics from `bfl_history.db`."""
    if not DB_PATH.exists():
        return {"exists": False}
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    cur = conn.cursor()
    g_cnt = cur.execute("SELECT COUNT(*)/2, MIN(season), MAX(season) FROM games").fetchone()
    pb_cnt = cur.execute("SELECT COUNT(*), MIN(season), MAX(season) FROM player_boxscores").fetchone()
    has_ts = cur.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='team_seasons'").fetchone()[0]
    ts_cnt = cur.execute("SELECT COUNT(*), MIN(season), MAX(season) FROM team_seasons").fetchone() if has_ts else (0, None, None)
    has_dp = cur.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='draft_picks'").fetchone()[0]
    dp_cnt = cur.execute("SELECT COUNT(*), MIN(season), MAX(season) FROM draft_picks").fetchone() if has_dp else (0, None, None)
    last_sync = cur.execute("SELECT MAX(synced_at) FROM sync_log").fetchone()[0]
    cur_yr = int(os.getenv("SEASON_YEAR") or CURRENT_YEAR)
    max_wk = cur.execute("SELECT MAX(week) FROM games WHERE season = ?", (cur_yr,)).fetchone()[0]
    conn.close()
    return {
        "exists": True,
        "matchups": int(g_cnt[0] or 0),
        "games_span": f"{g_cnt[1]}–{g_cnt[2]}",
        "team_seasons": int(ts_cnt[0] or 0),
        "standings_span": f"{ts_cnt[1]}–{ts_cnt[2]}" if ts_cnt[0] else "Not synced",
        "draft_picks": int(dp_cnt[0] or 0),
        "drafts_span": f"{dp_cnt[1]}–{dp_cnt[2]}" if dp_cnt[0] else "Not synced",
        "player_rows": int(pb_cnt[0] or 0),
        "boxscore_span": f"{pb_cnt[1]}–{pb_cnt[2]}",
        "current_year": cur_yr,
        "current_week": max_wk or 0,
        "last_sync": last_sync or "Unknown",
    }


def format_discord_answer(question: str, result: dict, show_sql: bool = False) -> tuple:
    """
    Formats the grounded Gemini Text-to-SQL result into a Discord embed payload
    (title, description, fields) safely within Discord's character limits.
    """
    answer = (result.get("answer") or "No answer returned.").strip()
    if len(answer) > 3900:
        answer = answer[:3880] + "\n… *(truncated)*"

    fields = []
    if show_sql:
        for q in result.get("queries", [])[:2]:
            sql_snippet = (q.get("sql") or "").strip()
            if len(sql_snippet) > 950:
                sql_snippet = sql_snippet[:940] + " ..."
            fields.append({
                "name": f"🧮 SQL — {q.get('label', 'Query')[:100]}",
                "value": f"```sql\n{sql_snippet}\n```",
                "inline": False,
            })

    q_title = f"📚 BFL Historian: {question[:220]}"
    return q_title, answer, fields


def install_launchd() -> None:
    """Installs a macOS LaunchAgent so the Mac Mini auto-starts & keeps the bot alive."""
    python_bin = sys.executable
    script_path = str(Path(__file__).resolve())
    log_out = str(SCRIPT_DIR / "live_state" / "historian_bot.log")
    log_err = str(SCRIPT_DIR / "live_state" / "historian_bot.err.log")

    plist = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{LAUNCHD_LABEL}</string>
    <key>ProgramArguments</key>
    <array>
        <string>{python_bin}</string>
        <string>-u</string>
        <string>{script_path}</string>
    </array>
    <key>WorkingDirectory</key>
    <string>{PROJECT_ROOT}</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>{log_out}</string>
    <key>StandardErrorPath</key>
    <string>{log_err}</string>
</dict>
</plist>
"""
    LAUNCHD_PLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    LAUNCHD_PLIST_PATH.write_text(plist, encoding="utf-8")
    subprocess.run(["launchctl", "unload", str(LAUNCHD_PLIST_PATH)], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    res = subprocess.run(["launchctl", "load", "-w", str(LAUNCHD_PLIST_PATH)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if res.returncode == 0:
        print(f"✅ Installed & started macOS LaunchAgent: {LAUNCHD_PLIST_PATH}")
        print(f"   Logs: {log_out}")
    else:
        print(f"⚠️ Wrote plist to {LAUNCHD_PLIST_PATH}, but launchctl returned: {res.stderr.strip()}")


def uninstall_launchd() -> None:
    if LAUNCHD_PLIST_PATH.exists():
        subprocess.run(["launchctl", "unload", "-w", str(LAUNCHD_PLIST_PATH)], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        LAUNCHD_PLIST_PATH.unlink()
        print(f"🛑 Unloaded & removed {LAUNCHD_PLIST_PATH}")
    else:
        print(f"ℹ️ No LaunchAgent found at {LAUNCHD_PLIST_PATH}")


def run_discord_bot() -> None:
    try:
        import discord
        from discord import app_commands
        from discord.ext import commands
    except ImportError:
        print("❌ `discord.py` is not installed yet.")
        print("   Install it with:  pip install discord.py")
        print("   Or test queries locally via:  python3 bfl_historian_bot.py --dry-run \"Who has the most career wins?\"")
        sys.exit(1)

    token = os.getenv("DISCORD_BOT_TOKEN", "").strip()
    if not token:
        print("❌ DISCORD_BOT_TOKEN not found in .env!")
        sys.exit(1)

    guild_id_str = os.getenv("DISCORD_GUILD_ID", "").strip()
    guild_obj = discord.Object(id=int(guild_id_str)) if guild_id_str.isdigit() else None

    intents = discord.Intents.default()
    intents.message_content = True
    bot = commands.Bot(command_prefix="!", intents=intents)

    @bot.event
    async def on_ready():
        log.info("Logged in as %s (ID: %s)", bot.user, bot.user.id)
        try:
            if guild_obj:
                bot.tree.copy_global_to(guild=guild_obj)
                synced = await bot.tree.sync(guild=guild_obj)
                log.info("Synced %d guild slash commands to guild %s", len(synced), guild_obj.id)
            else:
                synced = await bot.tree.sync()
                log.info("Synced %d global slash commands", len(synced))
        except Exception as exc:
            log.error("Slash command sync failed: %s", exc)

        # Kick off a non-blocking background sync loop so `/ask` NEVER waits on ESPN HTTP calls
        if not getattr(bot, "_sync_loop_started", False):
            bot._sync_loop_started = True

            async def _bg_sync_loop():
                while not bot.is_closed():
                    try:
                        await asyncio.to_thread(maybe_refresh_current_season)
                        log.info("Background SQLite sync check complete.")
                    except Exception as exc:
                        log.warning("Background sync error: %s", exc)
                    await asyncio.sleep(SYNC_TTL_SECONDS)

            bot.loop.create_task(_bg_sync_loop())

    async def _execute_ask(question: str, show_sql: bool = False, requester: str = "user") -> discord.Embed:
        t0 = time.time()
        log.info("Received /ask from %s: %s", requester, question)

        def _worker():
            return answer_natural_language_question(question, auto_sync=False)

        result = await asyncio.wait_for(asyncio.to_thread(_worker), timeout=50.0)
        elapsed = time.time() - t0
        log.info("Answered /ask from %s in %.2fs (%d queries)", requester, elapsed, len(result.get("queries", [])))

        title, desc, fields = format_discord_answer(question, result, show_sql=show_sql)
        embed = discord.Embed(title=title, description=desc, color=0x2ECC71)
        for f in fields:
            embed.add_field(name=f["name"], value=f["value"], inline=f["inline"])
        embed.set_footer(text=f"BFL Historical Warehouse (2008–Present) • Answered in {elapsed:.1f}s")
        return embed

    @bot.tree.command(name="ask", description="Ask the BFL Historian anything about 18 years of league stats!")
    @app_commands.describe(
        question="Your question (e.g., 'Who has the worst all-time record in Week 14?')",
        show_sql="Include the verified SQL query in the reply",
    )
    async def ask_slash(interaction: discord.Interaction, question: str, show_sql: bool = False):
        now = time.time()
        last = _user_last_ask.get(interaction.user.id, 0.0)
        if now - last < USER_COOLDOWN_SECONDS:
            wait = int(USER_COOLDOWN_SECONDS - (now - last)) + 1
            await interaction.response.send_message(f"⏳ Give the Historian {wait}s to catch its breath!", ephemeral=True)
            return
        _user_last_ask[interaction.user.id] = now

        await interaction.response.defer(thinking=True)
        try:
            embed = await _execute_ask(question, show_sql=show_sql, requester=str(interaction.user))
            await interaction.followup.send(embed=embed)
        except asyncio.TimeoutError:
            log.error("/ask timed out after 50s for '%s'", question)
            await interaction.followup.send("⏱️ The Historian timed out waiting on Gemini—please try asking again!")
        except Exception as exc:
            log.error("/ask failed for '%s': %s", question, exc)
            notify_ops("⚠️ BFL Historian /ask Error", f"Question: `{question}`\nError: `{exc}`", level="warning")
            err_str = str(exc)
            if "HTTP 503" in err_str or "UNAVAILABLE" in err_str or "timed out" in err_str.lower():
                friendly = "⚠️ Gemini is experiencing a temporary traffic spike—please run `/ask` again in a few seconds!"
            elif "HTTP 429" in err_str or "quota" in err_str.lower():
                friendly = "⚠️ The Historian hit its Gemini API rate limit for the moment—please try again in a minute!"
            else:
                friendly = "❌ Sorry, the Historian hit an unexpected snag answering that—try rephrasing or ask again shortly."
            await interaction.followup.send(friendly)

    @bot.tree.command(name="bflstats", description="Show BFL Historical Warehouse database coverage & sync status")
    async def bflstats_slash(interaction: discord.Interaction):
        s = get_warehouse_summary()
        if not s.get("exists"):
            await interaction.response.send_message("❌ Historical database not initialized yet.", ephemeral=True)
            return
        embed = discord.Embed(
            title="🗄️ BFL Historical Analytics Warehouse",
            color=0x3498DB,
            description=(
                f"• **Matchups Stored:** `{s['matchups']:,}` games ({s['games_span']})\n"
                f"• **Divisions & Standings:** `{s['team_seasons']:,}` team-seasons ({s['standings_span']})\n"
                f"• **Draft Picks & Value:** `{s['draft_picks']:,}` picks ({s['drafts_span']})\n"
                f"• **Weekly Rosters & Boxscores:** `{s['player_rows']:,}` player-weeks ({s['boxscore_span']})\n"
                f"• **Active Season Synced:** `{s['current_year']}` through Week `{s['current_week']}`\n"
                f"• **Last Synced:** `{s['last_sync']}`"
            ),
        )
        embed.set_footer(text="Try `/ask Who has the most 150+ point games all-time?`")
        await interaction.response.send_message(embed=embed)

    @bot.command(name="ask")
    async def ask_prefix(ctx, *, question: str):
        async with ctx.typing():
            try:
                embed = await _execute_ask(question, show_sql=False, requester=str(ctx.author))
                await ctx.reply(embed=embed)
            except asyncio.TimeoutError:
                await ctx.reply("⏱️ The Historian timed out waiting on Gemini—please try again!")
            except Exception as exc:
                log.error("!ask failed for '%s': %s", question, exc)
                await ctx.reply("⚠️ Gemini is temporarily busy right now—please try again in a few seconds!")

    bot.run(token)


def main():
    parser = argparse.ArgumentParser(description="BFL Discord `/ask` Historian Bot")
    parser.add_argument("--dry-run", type=str, default="", help="Test a question locally without connecting to Discord")
    parser.add_argument("--show-sql", action="store_true", help="Show SQL in --dry-run output")
    parser.add_argument("--install-launchd", action="store_true", help="Install macOS LaunchAgent on Mac Mini")
    parser.add_argument("--uninstall-launchd", action="store_true", help="Remove macOS LaunchAgent")
    args = parser.parse_args()

    if args.install_launchd:
        install_launchd()
        return
    if args.uninstall_launchd:
        uninstall_launchd()
        return
    if args.dry_run:
        res = answer_natural_language_question(args.dry_run, auto_sync=False)
        title, desc, fields = format_discord_answer(args.dry_run, res, show_sql=args.show_sql)
        print(f"\n{title}\n{'=' * 72}\n{desc}\n")
        for f in fields:
            print(f"\n{f['name']}\n{f['value']}")
        return

    run_discord_bot()


if __name__ == "__main__":
    main()
