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
from typing import Dict, List, Optional

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from FantasyRecap.core.manifest import load_manifest, load_project_env
load_project_env()

def _get_manifest_teams() -> list:
    return load_manifest().get("teams", [])

from FantasyRecap.bfl_history_db import (
    DB_PATH,
    answer_natural_language_question,
    compute_position_normalized_draft_value,
    sync_history_db,
)
from FantasyRecap.config import CURRENT_YEAR
from FantasyRecap.core.ops import get_logger, notify_ops
from FantasyRecap.league_history_intelligence import BFL_CHAMPIONSHIPS, standardize_name

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


def resolve_owner_name(query: str) -> Optional[str]:
    """Resolves a manager's full name, first name, last name, or BFL nickname to canonical owner name."""
    raw = (query or "").strip()
    if not raw:
        return None
    low = raw.lower()
    teams = _get_manifest_teams()
    # Direct exact/nickname/trigger match against manifest
    for t in teams:
        owner = t["owner"]
        if low == owner.lower() or low == t.get("code", "").lower():
            return owner
        if low in [u.lower() for u in t.get("discord_usernames", [])]:
            return owner
        if low in [m.strip().lower() for m in t.get("mention_triggers", [])]:
            return owner
    # Try standardize_name()
    std = standardize_name(raw)
    for t in teams:
        if std.lower() == t["owner"].lower():
            return t["owner"]
    # Prefix / substring match on first or last name
    for t in teams:
        owner = t["owner"]
        parts = owner.lower().split()
        if any(p.startswith(low) for p in parts) or low in owner.lower():
            return owner
    return None


def build_rivalry_report(owner1_query: str, owner2_query: str, db_path: Path = DB_PATH) -> dict:
    """
    Instant zero-LLM head-to-head rivalry card directly from `bfl_history.db` (2008-present).
    Returns `{"title": str, "description": str, "fields": list, "color": int}` or raises ValueError.
    """
    if not db_path.exists():
        raise ValueError("Historical database not initialized yet.")
    o1 = resolve_owner_name(owner1_query)
    o2 = resolve_owner_name(owner2_query)
    if not o1:
        raise ValueError(f"Could not match BFL manager '{owner1_query}'.")
    if not o2:
        raise ValueError(f"Could not match BFL manager '{owner2_query}'.")
    if o1 == o2:
        raise ValueError(f"Pick two different managers (both matched **{o1}**).")

    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    rows = conn.execute(
        """
        SELECT season, week, is_regular, is_playoffs, is_consolation, score, opp_score, win, margin
        FROM games
        WHERE LOWER(owner) = LOWER(?) AND LOWER(opp_owner) = LOWER(?)
        ORDER BY season ASC, week ASC
        """,
        (o1, o2),
    ).fetchall()
    conn.close()

    if not rows:
        return {
            "title": f"Head-to-Head: {o1} vs. {o2}",
            "description": "These two franchises have never met in BFL history (2008–present).",
            "fields": [],
            "color": 0x95A5A6,
        }

    total = len(rows)
    w1 = sum(1 for r in rows if r[7] == 1)
    w2 = total - w1
    pf1 = sum(float(r[5]) for r in rows)
    pf2 = sum(float(r[6]) for r in rows)
    avg1 = pf1 / total
    avg2 = pf2 / total

    po_rows = [r for r in rows if r[3] == 1]
    po_w1 = sum(1 for r in po_rows if r[7] == 1)
    po_w2 = len(po_rows) - po_w1

    # Current streak
    streak_owner = o1 if rows[-1][7] == 1 else o2
    streak_len = 0
    last_flag = rows[-1][7]
    for r in reversed(rows):
        if r[7] == last_flag:
            streak_len += 1
        else:
            break

    closest = min(rows, key=lambda r: abs(float(r[8])))
    blowout = max(rows, key=lambda r: abs(float(r[8])))
    latest = rows[-1]

    def _fmt_game(r) -> str:
        win_nm = o1 if r[7] == 1 else o2
        hi, lo = max(float(r[5]), float(r[6])), min(float(r[5]), float(r[6]))
        stage = " (Playoffs)" if r[3] == 1 else (" (Consolation)" if r[4] == 1 else "")
        return f"{r[0]} Wk {r[1]}{stage}: **{win_nm}** won `{hi:.2f}` – `{lo:.2f}` (+{abs(float(r[8])):.2f})"

    sb_hits = [sb for sb in BFL_CHAMPIONSHIPS if {sb["champ"].lower(), sb["runner_up"].lower()} == {o1.lower(), o2.lower()}]

    leader_line = (
        f"**{o1}** leads all-time **`{w1}–{w2}`**"
        if w1 > w2
        else (f"**{o2}** leads all-time **`{w2}–{w1}`**" if w2 > w1 else f"All-time series deadlocked **`{w1}–{w2}`**")
    )
    desc = (
        f"{leader_line} across **{total}** meetings ({rows[0][0]}–{rows[-1][0]})\n"
        f"• **Avg Score:** {o1} `{avg1:.1f}` PPG vs. {o2} `{avg2:.1f}` PPG (`{pf1:,.1f}` – `{pf2:,.1f}` total)\n"
        f"• **Current Streak:** **{streak_owner}** has won **{streak_len}** straight"
    )
    if po_rows:
        desc += f"\n• **Championship Playoffs:** {o1} `{po_w1}–{po_w2}` {o2}"
    if sb_hits:
        sb_txt = "; ".join(f"{sb['year']} ({sb['champ']} def. {sb['runner_up']}, {sb['score']})" for sb in sb_hits)
        desc += f"\n• **Super Bowl History:** {sb_txt}"

    fields = [
        {"name": "Last Meeting", "value": _fmt_game(latest), "inline": False},
        {"name": "Closest Thriller", "value": _fmt_game(closest), "inline": True},
        {"name": "Biggest Blowout", "value": _fmt_game(blowout), "inline": True},
    ]
    return {
        "title": f"All-Time Rivalry: {o1} vs. {o2}",
        "description": desc,
        "fields": fields,
        "color": 0xE67E22,
    }


def build_draft_steals_report(season: Optional[int] = None, owner_query: str = "", db_path: Path = DB_PATH) -> dict:
    """
    Instant zero-LLM draft steals & busts report using position-normalized value
    over draft slot (`actual_pts - expected_pts[pos_draft_rank]`) so QBs never skew the list.
    Pass `season=0` for all-time (2018-present), or omit for active season.
    """
    if not db_path.exists():
        raise ValueError("Historical database not initialized yet.")

    owner_filter = resolve_owner_name(owner_query) if owner_query else None
    if owner_query and not owner_filter:
        raise ValueError(f"Could not match BFL manager '{owner_query}'.")

    eff_season = int(os.getenv("SEASON_YEAR") or CURRENT_YEAR) if season is None else int(season)
    res = compute_position_normalized_draft_value(
        db_path=db_path,
        season=eff_season if eff_season > 0 else None,
        owner=owner_filter,
        limit=5,
    )
    steals = res["steals"]
    busts = res["busts"]

    scope = f"{eff_season} Draft" if eff_season > 0 else "All-Time (2018–Present)"
    if owner_filter:
        scope = f"{owner_filter} • {scope}"

    def _short_mgr(own: str) -> str:
        if own == "Shawn Lukose":
            return "Lukose"
        if own == "Shawn Ullenbrauck":
            return "Thor"
        if own == "Adam Olen":
            return "AMO"
        if own.lower() == "rej hoxha":
            return "Rej"
        return own.split()[0]

    def _fmt_rows(rows) -> str:
        if not rows:
            return "No qualifying picks found."
        out = []
        for idx, r in enumerate(rows, 1):
            yr_tag = f"[{r['season']}] " if eff_season == 0 else ""
            owner_tag = "" if owner_filter else f" ({_short_mgr(r['owner'])})"
            pr_str = f"{r['pos']}{r['pos_draft_rank']} → {r['pos']}{r['pos_finish_rank']}" if r["pos_draft_rank"] and r["pos_finish_rank"] else r["pos"]
            out.append(
                f"**{idx}. {yr_tag}{r['player']}**{owner_tag} — Rd {r['round']} (#{r['pick']}) • `{pr_str}` • `{r['pts']:.1f} pts` (`{r['delta']:+.1f}` vs pos slot)"
            )
        return "\n".join(out)

    return {
        "title": f"BFL Draft Steals & Busts — {scope}",
        "description": "Ranked by position-normalized points above/below the expected return for that exact position & draft slot.",
        "fields": [
            {"name": "Biggest Steals (Round 4+)", "value": _fmt_rows(steals), "inline": False},
            {"name": "Biggest Busts (Rounds 1–5)", "value": _fmt_rows(busts), "inline": False},
        ],
        "color": 0x9B59B6,
    }


def build_playoff_odds_report(week: int = 0, season: Optional[int] = None) -> dict:
    """Runs the 5,000-sim BFL Monte Carlo Playoff Engine live from ESPN for `/playoffodds`."""
    from FantasyRecap.league_recap_generator import (
        ESPN_LEAGUE_ID,
        ESPN_S2,
        ESPN_SWID,
        fetch_espn_week_data,
    )
    from FantasyRecap.playoff_odds_simulator import simulate_league_playoff_odds_from_espn

    eff_season = int(season or os.getenv("SEASON_YEAR") or CURRENT_YEAR)
    eff_week = int(week or 0)
    if eff_week <= 0:
        summary = get_warehouse_summary()
        eff_week = int(summary.get("current_week") or 11)

    raw_data = fetch_espn_week_data(ESPN_LEAGUE_ID, str(eff_season), eff_week, ESPN_S2, ESPN_SWID)
    results = simulate_league_playoff_odds_from_espn(raw_data, eff_week, n_sims=5000)

    ranked = sorted(results.values(), key=lambda x: (x["playoff_pct"], x["bye_pct"], x["pf"]), reverse=True)
    in_lines = [
        f"**{i}. {r['owner']}** ({r['record']}, {r['division']}) — **`{r['playoff_pct']}%`** PO | `{r['div_title_pct']}%` Div | `{r['bye_pct']}%` Bye"
        for i, r in enumerate(ranked[:7], 1)
    ]
    bubble_lines = [
        f"**{i}. {r['owner']}** ({r['record']}, {r['division']}) — **`{r['playoff_pct']}%`** PO | `{r['div_title_pct']}%` Div"
        for i, r in enumerate(ranked[7:12], 8)
    ]
    top_lev = sorted(results.values(), key=lambda x: x["leverage_swing"], reverse=True)[:4]
    lev_lines = [
        f"• **{r['owner']}** vs {r['next_opp']}: Win `{r['win_po_pct']}%` / Loss `{r['loss_po_pct']}%` (**±{r['leverage_swing']}%**)"
        for r in top_lev
    ]

    return {
        "title": f"BFL Playoff Odds & Leverage (Through Week {eff_week}, {eff_season})",
        "description": "5,000 Monte Carlo sims • 4 Division Champs (#1 Bye) + 3 Wild Cards • Tiebreakers: Record → H2H → PF",
        "fields": [
            {"name": "Projected Playoff Field (Top 7)", "value": "\n".join(in_lines), "inline": False},
            {"name": "On the Bubble (#8–#12)", "value": "\n".join(bubble_lines), "inline": False},
            {"name": f"Week {eff_week + 1} Highest-Leverage Swings", "value": "\n".join(lev_lines), "inline": False},
        ],
        "color": 0x1ABC9C,
    }


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
        embed.set_footer(text="Try `/ask`, `/rivalry`, `/draftsteals`, or `/playoffodds`")
        await interaction.response.send_message(embed=embed)

    async def _owner_autocomplete(interaction: discord.Interaction, current: str):
        q = (current or "").lower()
        choices = []
        for t in _get_manifest_teams():
            label = f"{t['owner']} ({t['code']})"
            if not q or q in label.lower():
                choices.append(app_commands.Choice(name=label[:100], value=t["owner"]))
        return choices[:25]

    @bot.tree.command(name="rivalry", description="Instant 18-year head-to-head rivalry breakdown between any two BFL managers")
    @app_commands.describe(owner1="First manager (name or nickname)", owner2="Second manager (name or nickname)")
    @app_commands.autocomplete(owner1=_owner_autocomplete, owner2=_owner_autocomplete)
    async def rivalry_slash(interaction: discord.Interaction, owner1: str, owner2: str):
        try:
            card = build_rivalry_report(owner1, owner2)
            embed = discord.Embed(title=card["title"], description=card["description"], color=card["color"])
            for f in card["fields"]:
                embed.add_field(name=f["name"], value=f["value"], inline=f["inline"])
            embed.set_footer(text="BFL Historical Warehouse (2008–Present) • Instant SQLite Lookup")
            await interaction.response.send_message(embed=embed)
        except ValueError as exc:
            await interaction.response.send_message(f"⚠️ {exc}", ephemeral=True)
        except Exception as exc:
            log.error("/rivalry failed: %s", exc)
            await interaction.response.send_message("❌ Failed to look up that rivalry.", ephemeral=True)

    @bot.tree.command(name="draftsteals", description="Top BFL draft steals & busts by round-adjusted value (2018–present)")
    @app_commands.describe(
        season="Draft year (e.g. 2026; pass 0 for All-Time 2018–present)",
        owner="Optional: filter to a specific manager",
    )
    @app_commands.autocomplete(owner=_owner_autocomplete)
    async def draftsteals_slash(interaction: discord.Interaction, season: Optional[int] = None, owner: str = ""):
        try:
            card = build_draft_steals_report(season=season, owner_query=owner)
            embed = discord.Embed(title=card["title"], description=card["description"], color=card["color"])
            for f in card["fields"]:
                embed.add_field(name=f["name"], value=f["value"], inline=f["inline"])
            embed.set_footer(text="BFL Draft Value Warehouse (2018–Present) • Instant SQLite Lookup")
            await interaction.response.send_message(embed=embed)
        except ValueError as exc:
            await interaction.response.send_message(f"⚠️ {exc}", ephemeral=True)
        except Exception as exc:
            log.error("/draftsteals failed: %s", exc)
            await interaction.response.send_message("❌ Failed to query draft steals.", ephemeral=True)

    @bot.tree.command(name="playoffodds", description="Live 5,000-sim BFL Monte Carlo Playoff Odds, Bye Race & Next-Week Leverage")
    @app_commands.describe(week="Completed week to simulate through (defaults to latest synced week)")
    async def playoffodds_slash(interaction: discord.Interaction, week: int = 0):
        await interaction.response.defer(thinking=True)
        try:
            card = await asyncio.to_thread(build_playoff_odds_report, week)
            embed = discord.Embed(title=card["title"], description=card["description"], color=card["color"])
            for f in card["fields"]:
                embed.add_field(name=f["name"], value=f["value"], inline=f["inline"])
            embed.set_footer(text="BFL Playoff Engine • Overall Record → H2H → Points For")
            await interaction.followup.send(embed=embed)
        except Exception as exc:
            log.error("/playoffodds failed: %s", exc)
            await interaction.followup.send("❌ Could not compute live playoff odds right now.")

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
