#!/usr/bin/env python3
"""
BFL All-Time Trade Vault & VORP Outcome Engine (2018–Present)
=============================================================
Syncs every verified executed trade from ESPN `mTransactions2` (2018–present)
into SQLite (`trades` + `trade_items` inside `live_state/bfl_history.db`) and
grades every trade on actual post-trade outcome using 16-team positional VORP.

Supports two complementary lenses (both exported for interactive toggling):
  1. Actual Lineup Impact (`started_vorp`):
     Only counts weeks the acquiring manager actually STARTED the player in
     regular season (1.0x) or Championship Playoffs (1.5x; consolation = 0x).
     Weekly contribution = weight * (points - pos_replacement_baseline).
  2. Pure Player Outcome (`ros_vorp`):
     Counts all rest-of-season weeks from the trade effective week onward
     across any roster (even if benched, dropped, or flipped), using
     weight * max(0.0, points - pos_replacement_baseline), with 1.5x for
     playoff weeks (Weeks 15–17 in 2021+, Weeks 14–16 in 2018–2020).

Also computes:
  - Exact Matchups Flipped (wins where acquired starters' VORP > final margin)
  - Trade Trees (detects when an acquired player was later flipped in the same season)
  - Automatic Story Tags (Title Deal, Playoff Hero, Flipped Wins, Injury Curse,
    Cut Quickly, Win-Win, Lose-Lose)
  - Per-Owner Career & 2026 Live Report Cards (Trade W-L, Net VORP, Best Fleece,
    Worst Regret, Favorite Trade Partner, Biggest Mark, Worst Nemesis)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import requests

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from FantasyRecap.core.manifest import get_espn_credentials, load_manifest, load_project_env
load_project_env()

from FantasyRecap.bfl_history_db import DB_PATH, standardize_owner_for_season
from FantasyRecap.config import CURRENT_YEAR
from FantasyRecap.league_history_intelligence import standardize_name

# 16-team BFL marginal starter ranks (exact waiver-wire replacement threshold)
REPLACEMENT_RANKS = {
    "QB": 16,
    "RB": 36,
    "WR": 38,
    "TE": 16,
    "K": 16,
    "D/ST": 16,
}

FALLBACK_REPLACEMENT_PPG = {
    "QB": 14.5,
    "RB": 8.2,
    "WR": 8.0,
    "TE": 5.8,
    "K": 7.2,
    "D/ST": 5.0,
    "FLEX": 8.0,
}

PLAYOFF_WEIGHT = 1.5
TIE_MARGIN_VORP = 3.0  # Trades within +/- 3.0 VORP count as a Push / Even swap


def _canon_owner(name: str, season: int = 2026) -> str:
    s = standardize_owner_for_season(name, season)
    if s.lower() == "rej hoxha":
        return "rej hoxha"
    return s


def init_trade_tables(conn: sqlite3.Connection) -> None:
    """Ensures `trades` and `trade_items` tables exist in `bfl_history.db`."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS trades (
            trade_id TEXT PRIMARY KEY,
            season INTEGER NOT NULL,
            trade_week INTEGER NOT NULL,
            effective_week INTEGER NOT NULL,
            team_a_id INTEGER NOT NULL,
            owner_a TEXT NOT NULL,
            team_b_id INTEGER NOT NULL,
            owner_b TEXT NOT NULL,
            process_date_ms INTEGER NOT NULL DEFAULT 0,
            source TEXT NOT NULL DEFAULT 'espn_tx'
        );

        CREATE TABLE IF NOT EXISTS trade_items (
            trade_id TEXT NOT NULL,
            season INTEGER NOT NULL,
            from_team_id INTEGER NOT NULL,
            from_owner TEXT NOT NULL,
            to_team_id INTEGER NOT NULL,
            to_owner TEXT NOT NULL,
            player_id INTEGER NOT NULL,
            player_name TEXT NOT NULL,
            position TEXT NOT NULL,
            PRIMARY KEY (trade_id, player_id, from_team_id, to_team_id)
        );

        CREATE INDEX IF NOT EXISTS idx_trades_season ON trades(season, trade_week);
        CREATE INDEX IF NOT EXISTS idx_trades_owners ON trades(owner_a, owner_b);
        CREATE INDEX IF NOT EXISTS idx_trade_items_season ON trade_items(season, player_id);
        """
    )
    conn.commit()


def _fetch_season_transactions(league_id: str, cookies: dict, season: int, max_sp: int = 16) -> List[dict]:
    """Fetches ESPN `mTransactions2` across scoring periods 1..max_sp in parallel."""
    all_txs: List[dict] = []
    seen_ids = set()

    def _get_sp(sp: int) -> List[dict]:
        url = (
            f"https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/{season}"
            f"/segments/0/leagues/{league_id}?scoringPeriodId={sp}&view=mTransactions2"
        )
        try:
            r = requests.get(url, cookies=cookies, timeout=10)
            if r.status_code == 200:
                return r.json().get("transactions", []) or []
        except Exception:
            pass
        return []

    with ThreadPoolExecutor(max_workers=6) as ex:
        futs = [ex.submit(_get_sp, sp) for sp in range(1, max_sp + 1)]
        for fut in as_completed(futs):
            for tx in fut.result():
                tid = tx.get("id")
                if tid and tid not in seen_ids:
                    seen_ids.add(tid)
                    all_txs.append(tx)
    return all_txs


def _extract_executed_trades_from_txs(all_txs: List[dict], season: int) -> List[dict]:
    """
    Reconciles ESPN's TRADE_PROPOSAL / TRADE_ACCEPT / TRADE_UPHOLD transaction records
    into deduplicated executed player-for-player trades.
    """
    tx_by_id = {tx["id"]: tx for tx in all_txs if tx.get("id")}
    tx_by_related: Dict[str, List[dict]] = defaultdict(list)
    for tx in all_txs:
        rel = tx.get("relatedTransactionId")
        if rel:
            tx_by_related[rel].append(tx)

    # Identify every executed trade confirmation
    executed_groups: Dict[str, List[dict]] = defaultdict(list)
    for tx in all_txs:
        ttype = str(tx.get("type") or "")
        status = str(tx.get("status") or "")
        if status == "EXECUTED" and ttype in ("TRADE_ACCEPT", "TRADE_UPHOLD", "TRADE_PROCESSED"):
            gkey = tx.get("relatedTransactionId") or tx.get("id")
            executed_groups[gkey].append(tx)

    parsed: List[dict] = []
    seen_signatures = set()

    for gkey, exec_list in executed_groups.items():
        # Gather candidate transactions that may carry the `items` list
        candidates = list(exec_list)
        if gkey in tx_by_id:
            candidates.append(tx_by_id[gkey])
        candidates.extend(tx_by_related.get(gkey, []))

        trade_items_raw = []
        seen_item_keys = set()
        for cand in candidates:
            for it in (cand.get("items") or []):
                if it.get("type") != "TRADE":
                    continue
                pid = int(it.get("playerId") or 0)
                ft = int(it.get("fromTeamId") or 0)
                tt = int(it.get("toTeamId") or 0)
                if pid == 0 or ft <= 0 or tt <= 0 or ft == tt:
                    continue
                ik = (ft, tt, pid)
                if ik not in seen_item_keys:
                    seen_item_keys.add(ik)
                    trade_items_raw.append({"from_team_id": ft, "to_team_id": tt, "player_id": pid})

        if not trade_items_raw:
            continue

        # Must involve exactly 2 teams sending players (standard BFL 2-team trade)
        teams_involved = sorted({it["from_team_id"] for it in trade_items_raw} | {it["to_team_id"] for it in trade_items_raw})
        if len(teams_involved) < 2:
            continue

        # Choose the executed scoringPeriodId & processDate
        sp_vals = [int(t.get("scoringPeriodId") or 0) for t in exec_list if t.get("scoringPeriodId")]
        trade_week = min(sp_vals) if sp_vals else 1
        proc_ms = max(int(t.get("processDate") or t.get("proposedDate") or 0) for t in exec_list)

        sig = (season, frozenset((it["from_team_id"], it["to_team_id"], it["player_id"]) for it in trade_items_raw))
        if sig in seen_signatures:
            continue
        seen_signatures.add(sig)

        team_a_id, team_b_id = teams_involved[0], teams_involved[1]
        sig_hash = hashlib.sha1(
            json.dumps(sorted(list(sig[1]))).encode("utf-8")
        ).hexdigest()[:10]
        trade_id = f"{season}_w{trade_week:02d}_{sig_hash}"

        parsed.append({
            "trade_id": trade_id,
            "season": season,
            "trade_week": trade_week,
            "team_a_id": team_a_id,
            "team_b_id": team_b_id,
            "process_date_ms": proc_ms,
            "items": trade_items_raw,
        })

    parsed.sort(key=lambda x: (x["trade_week"], x["process_date_ms"], x["trade_id"]))
    return parsed


def _resolve_player_metadata(conn: sqlite3.Connection, season: int, player_ids: List[int]) -> Dict[int, Tuple[str, str]]:
    """Looks up (player_name, position) from SQLite first, falling back to ESPN Core API only if missing."""
    meta: Dict[int, Tuple[str, str]] = {}
    if not player_ids:
        return meta

    cur = conn.cursor()
    placeholders = ",".join("?" for _ in player_ids)

    # 1. Same-season player_boxscores
    for pid, pname, pos in cur.execute(
        f"SELECT player_id, player_name, position FROM player_boxscores WHERE season = ? AND player_id IN ({placeholders}) GROUP BY player_id",
        [season, *player_ids],
    ).fetchall():
        meta[int(pid)] = (pname, pos or "FLEX")

    # 2. Same-season or any-season draft_picks / boxscores
    missing = [pid for pid in player_ids if pid not in meta]
    if missing:
        ph2 = ",".join("?" for _ in missing)
        for pid, pname, pos in cur.execute(
            f"SELECT player_id, player_name, position FROM draft_picks WHERE player_id IN ({ph2}) GROUP BY player_id",
            missing,
        ).fetchall():
            meta[int(pid)] = (pname, pos or "FLEX")

    missing = [pid for pid in player_ids if pid not in meta]
    if missing:
        ph3 = ",".join("?" for _ in missing)
        for pid, pname, pos in cur.execute(
            f"SELECT player_id, player_name, position FROM player_boxscores WHERE player_id IN ({ph3}) GROUP BY player_id",
            missing,
        ).fetchall():
            meta[int(pid)] = (pname, pos or "FLEX")

    # 3. Rare fallback: ESPN public athlete endpoint (never happens for active players)
    still_missing = [pid for pid in player_ids if pid not in meta]
    for pid in still_missing:
        if pid < 0:
            meta[pid] = (f"D/ST #{pid}", "D/ST")
            continue
        try:
            r = requests.get(
                f"https://sports.core.api.espn.com/v2/sports/football/leagues/nfl/athletes/{pid}",
                timeout=3,
            )
            if r.status_code == 200:
                j = r.json()
                name = j.get("fullName") or f"Player {pid}"
                pos = (j.get("position") or {}).get("abbreviation") or "FLEX"
                if pos in ("PK",):
                    pos = "K"
                meta[pid] = (name, pos)
                continue
        except Exception:
            pass
        meta[pid] = (f"Player {pid}", "FLEX")

    return meta


def _determine_effective_week(conn: sqlite3.Connection, season: int, trade_week: int, items: List[dict]) -> int:
    """
    Determines the exact first week post-trade where acquired players could score for
    their new teams. If any traded player was still on `from_team_id` in `trade_week`
    (e.g., trade accepted/processed after games locked), effective_week = trade_week + 1.
    """
    cur = conn.cursor()
    still_on_old = 0
    already_on_new = 0
    for it in items:
        row = cur.execute(
            "SELECT team_id FROM player_boxscores WHERE season = ? AND week = ? AND player_id = ?",
            (season, trade_week, it["player_id"]),
        ).fetchone()
        if row:
            if int(row[0]) == it["from_team_id"]:
                still_on_old += 1
            elif int(row[0]) == it["to_team_id"]:
                already_on_new += 1
    if already_on_new > 0 and still_on_old == 0:
        return trade_week
    if still_on_old > 0:
        return trade_week + 1
    return trade_week


def _discover_roster_diff_trades(
    conn: sqlite3.Connection,
    season: int,
    existing_tx_trades: List[dict],
) -> List[dict]:
    """
    Reconciles weekly rosters in `player_boxscores` (Week w-1 -> Week w, plus Draft -> Week 1)
    to recover ALL executed league trades — including trades between non-commissioner teams
    whose `items` ESPN strips from `mTransactions2` due to API visibility limits.
    Deduplicates cleanly against exact `mTransactions2` trades for the same team pair within +/-1 week.
    """
    cur = conn.cursor()
    # Index existing mTransactions2 trades by (min_tid, max_tid, week)
    covered_pairs = []
    for tr in existing_tx_trades:
        pair = (min(tr["team_a_id"], tr["team_b_id"]), max(tr["team_a_id"], tr["team_b_id"]))
        covered_pairs.append((pair, tr["trade_week"]))

    def _is_covered(tid1: int, tid2: int, wk: int) -> bool:
        pair = (min(tid1, tid2), max(tid1, tid2))
        for p, twk in covered_pairs:
            if p == pair and abs(twk - wk) <= 1:
                return True
        return False

    # Load weekly rosters: week -> team_id -> {player_id: (player_name, position)}
    rosters: Dict[int, Dict[int, Dict[int, Tuple[str, str]]]] = defaultdict(lambda: defaultdict(dict))
    for wk, tid, pid, pname, pos in cur.execute(
        """
        SELECT week, team_id, player_id, player_name, position
        FROM player_boxscores
        WHERE season = ?
        """,
        (season,),
    ).fetchall():
        rosters[int(wk)][int(tid)][int(pid)] = (pname, pos or "FLEX")

    # Also build Week 0 ("Draft") roster from draft_picks to detect pre-Week-1 post-draft trades
    draft_roster: Dict[int, Dict[int, Tuple[str, str]]] = defaultdict(dict)
    for tid, pid, pname, pos in cur.execute(
        "SELECT team_id, player_id, player_name, position FROM draft_picks WHERE season = ?",
        (season,),
    ).fetchall():
        draft_roster[int(tid)][int(pid)] = (pname, pos or "FLEX")
    if draft_roster:
        rosters[0] = draft_roster

    weeks_sorted = sorted(w for w in rosters.keys() if w >= 1)
    discovered: List[dict] = []

    for wk in weeks_sorted:
        prev_wk = wk - 1
        if prev_wk not in rosters:
            continue
        r_prev = rosters[prev_wk]
        r_curr = rosters[wk]
        tids = sorted(set(r_prev.keys()) & set(r_curr.keys()))

        for i in range(len(tids)):
            for j in range(i + 1, len(tids)):
                t1, t2 = tids[i], tids[j]
                t1_to_t2 = [
                    (pid, r_prev[t1][pid])
                    for pid in r_prev[t1]
                    if pid in r_curr[t2]
                ]
                t2_to_t1 = [
                    (pid, r_prev[t2][pid])
                    for pid in r_prev[t2]
                    if pid in r_curr[t1]
                ]
                if not t1_to_t2 or not t2_to_t1:
                    continue
                # Guard against rare playoff/consolation K/DST waiver churn after trade deadline (Week 14+)
                if wk >= 14:
                    continue
                if _is_covered(t1, t2, wk):
                    continue

                items = []
                for pid, (pname, pos) in t1_to_t2:
                    items.append({
                        "from_team_id": t1,
                        "to_team_id": t2,
                        "player_id": pid,
                        "player_name": pname,
                        "position": pos,
                    })
                for pid, (pname, pos) in t2_to_t1:
                    items.append({
                        "from_team_id": t2,
                        "to_team_id": t1,
                        "player_id": pid,
                        "player_name": pname,
                        "position": pos,
                    })

                sig_tuples = sorted((it["from_team_id"], it["to_team_id"], it["player_id"]) for it in items)
                sig_hash = hashlib.sha1(json.dumps(sig_tuples).encode("utf-8")).hexdigest()[:10]
                trade_id = f"{season}_w{wk:02d}_{sig_hash}"
                discovered.append({
                    "trade_id": trade_id,
                    "season": season,
                    "trade_week": wk,
                    "effective_week": wk,
                    "team_a_id": t1,
                    "team_b_id": t2,
                    "process_date_ms": 0,
                    "source": "roster_diff",
                    "items": items,
                })
                covered_pairs.append(((min(t1, t2), max(t1, t2)), wk))

    return discovered


def sync_trades_from_espn(
    db_path: Path = DB_PATH,
    seasons: Optional[List[int]] = None,
    force: bool = False,
    quiet: bool = False,
) -> Dict[int, int]:
    """
    Syncs executed trades via Hybrid Discovery (ESPN `mTransactions2` + SQLite
    `player_boxscores` weekly roster reconciliation) into `trades` & `trade_items`
    in `bfl_history.db`. Past completed seasons are skipped if already populated unless
    `force=True`; the active season (`CURRENT_YEAR`) is always refreshed.
    """
    if seasons is None:
        seasons = list(range(2018, int(CURRENT_YEAR) + 1))

    creds = get_espn_credentials()
    league_id = creds["league_id"]
    cookies = creds["cookies"]

    conn = sqlite3.connect(db_path)
    init_trade_tables(conn)
    cur = conn.cursor()

    counts: Dict[int, int] = {}
    for season in seasons:
        existing_cnt = cur.execute(
            "SELECT COUNT(*) FROM trades WHERE season = ?", (season,)
        ).fetchone()[0]
        is_active = int(season) >= int(CURRENT_YEAR)
        if existing_cnt > 0 and not force and not is_active:
            counts[season] = int(existing_cnt)
            continue

        # Team ID -> standardized owner map from team_seasons (or games fallback)
        team_owner: Dict[int, str] = {}
        for tid, own in cur.execute(
            "SELECT team_id, owner FROM team_seasons WHERE season = ?", (season,)
        ).fetchall():
            team_owner[int(tid)] = _canon_owner(own, season)
        if not team_owner:
            for tid, own in cur.execute(
                "SELECT DISTINCT team_id, owner FROM games WHERE season = ?", (season,)
            ).fetchall():
                team_owner[int(tid)] = _canon_owner(own, season)

        all_txs = _fetch_season_transactions(league_id, cookies, season, max_sp=16)
        tx_trades = _extract_executed_trades_from_txs(all_txs, season)
        for tr in tx_trades:
            tr["source"] = "espn_tx"
            tr["effective_week"] = _determine_effective_week(conn, season, tr["trade_week"], tr["items"])

        diff_trades = _discover_roster_diff_trades(conn, season, tx_trades)
        trades = tx_trades + diff_trades
        trades.sort(key=lambda x: (x["trade_week"], x["process_date_ms"], x["trade_id"]))

        all_pids = sorted({it["player_id"] for tr in trades for it in tr["items"]})
        pmeta = _resolve_player_metadata(conn, season, all_pids)

        # Replace this season's trades atomically
        cur.execute("DELETE FROM trade_items WHERE season = ?", (season,))
        cur.execute("DELETE FROM trades WHERE season = ?", (season,))

        for tr in trades:
            eff_wk = tr["effective_week"]
            own_a = team_owner.get(tr["team_a_id"], f"Team {tr['team_a_id']}")
            own_b = team_owner.get(tr["team_b_id"], f"Team {tr['team_b_id']}")
            cur.execute(
                """
                INSERT OR REPLACE INTO trades
                (trade_id, season, trade_week, effective_week, team_a_id, owner_a, team_b_id, owner_b, process_date_ms, source)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    tr["trade_id"],
                    season,
                    tr["trade_week"],
                    eff_wk,
                    tr["team_a_id"],
                    own_a,
                    tr["team_b_id"],
                    own_b,
                    tr["process_date_ms"],
                    tr.get("source", "espn_tx"),
                ),
            )
            for it in tr["items"]:
                if "player_name" in it and "position" in it:
                    pname, pos = it["player_name"], it["position"]
                else:
                    pname, pos = pmeta.get(it["player_id"], (f"Player {it['player_id']}", "FLEX"))
                cur.execute(
                    """
                    INSERT OR REPLACE INTO trade_items
                    (trade_id, season, from_team_id, from_owner, to_team_id, to_owner, player_id, player_name, position)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        tr["trade_id"],
                        season,
                        it["from_team_id"],
                        team_owner.get(it["from_team_id"], f"Team {it['from_team_id']}"),
                        it["to_team_id"],
                        team_owner.get(it["to_team_id"], f"Team {it['to_team_id']}"),
                        it["player_id"],
                        pname,
                        pos,
                    ),
                )
        conn.commit()
        counts[season] = len(trades)
        if not quiet:
            print(
                f"🤝 Synced {len(trades):2d} verified trades for Season {season} "
                f"({len(tx_trades)} ESPN tx + {len(diff_trades)} roster-diff)"
            )

    conn.close()
    return counts


def compute_season_replacement_baselines(conn: sqlite3.Connection, season: int) -> Dict[str, float]:
    """
    Computes exact 16-team positional replacement PPG for a season directly from
    `player_boxscores` (e.g., QB16, RB36, WR38, TE16, K16, D/ST16 season active PPG).
    """
    cur = conn.cursor()
    max_wk = cur.execute(
        "SELECT COALESCE(MAX(week), 0) FROM player_boxscores WHERE season = ?", (season,)
    ).fetchone()[0]
    min_games = 4 if max_wk >= 13 else 3

    rows = cur.execute(
        """
        SELECT position, player_id, AVG(points) AS avg_pts, COUNT(*) AS gp
        FROM player_boxscores
        WHERE season = ?
          AND is_consolation = 0
          AND (points > 0 OR is_starter = 1)
        GROUP BY position, player_id
        HAVING gp >= ?
        """,
        (season, min_games),
    ).fetchall()

    by_pos: Dict[str, List[float]] = defaultdict(list)
    for pos, _pid, avg_pts, _gp in rows:
        by_pos[pos or "FLEX"].append(float(avg_pts or 0.0))

    baselines = dict(FALLBACK_REPLACEMENT_PPG)
    for pos, target_rank in REPLACEMENT_RANKS.items():
        vals = sorted(by_pos.get(pos, []), reverse=True)
        if len(vals) >= max(4, target_rank // 2):
            idx = min(len(vals) - 1, target_rank - 1)
            lo = max(0, idx - 1)
            hi = min(len(vals), idx + 2)
            baselines[pos] = round(sum(vals[lo:hi]) / (hi - lo), 2)
    baselines["FLEX"] = round((baselines["RB"] + baselines["WR"]) / 2.0, 2)
    return baselines


def _compute_tags(
    *,
    abs_swing: float,
    win_vorp: float,
    lose_vorp: float,
    win_playoff_vorp: float,
    win_won_champ: bool,
    win_runner_up: bool,
    win_games_swung: int,
    lose_avg_starts: float,
    lose_dropped_fast: bool,
    has_flipped_item: bool,
    effective_week: int,
) -> List[str]:
    tags: List[str] = []
    if win_won_champ and win_vorp >= 10.0 and abs_swing >= 5.0:
        tags.append("Title Deal")
    elif win_runner_up and win_vorp >= 12.0 and abs_swing >= 8.0:
        tags.append("Finals Run")

    if win_playoff_vorp >= 15.0:
        tags.append("Playoff Hero")

    if win_games_swung >= 2:
        tags.append(f"Flipped {win_games_swung} Wins")
    elif win_games_swung == 1 and win_playoff_vorp >= 10.0:
        tags.append("Flipped Playoff Game")

    if abs_swing < 12.0 and win_vorp >= 18.0 and lose_vorp >= 18.0:
        tags.append("Win-Win")
    elif win_vorp < 6.0 and lose_vorp < 6.0:
        tags.append("Lose-Lose")
    else:
        if lose_avg_starts <= 2.0 and effective_week <= 11 and abs_swing >= 15.0:
            tags.append("Injury / Bust Curse")
        if lose_dropped_fast and abs_swing >= 12.0:
            tags.append("Cut Quickly")

    if effective_week >= 8 and abs_swing >= 15.0 and win_vorp >= 18.0:
        tags.append("Deadline Steal")

    if has_flipped_item:
        if len(tags) >= 5:
            tags = tags[:4] + ["Trade Tree"]
        else:
            tags.append("Trade Tree")

    return tags[:5]


def _build_owner_report_cards(
    trades_list: List[dict],
    active_owners: List[str],
    lens_key: str,
) -> List[dict]:
    """
    Computes per-owner Trade Report Cards (`started` or `ros` lens):
    trades_count, wins, losses, pushes, win_pct, net_vorp, avg_swing,
    best_trade, worst_trade, favorite_partner, biggest_mark, worst_nemesis.
    """
    active_set = {o.lower() for o in active_owners}
    stats: Dict[str, dict] = {}
    partner_counts: Dict[str, Counter] = defaultdict(Counter)
    partner_net: Dict[str, Dict[str, float]] = defaultdict(lambda: defaultdict(float))
    owner_trades: Dict[str, List[Tuple[float, dict]]] = defaultdict(list)

    for own in active_owners:
        stats[own] = {
            "owner": own,
            "trades": 0,
            "wins": 0,
            "losses": 0,
            "pushes": 0,
            "net_vorp": 0.0,
        }

    for tr in trades_list:
        oa, ob = tr["owner_a"], tr["owner_b"]
        swing_a = float(tr[lens_key]["swing_a"])  # + favors A, - favors B
        swing_b = -swing_a

        for own, opp, my_swing in ((oa, ob, swing_a), (ob, oa, swing_b)):
            if own.lower() not in active_set:
                continue
            st = stats[own]
            st["trades"] += 1
            st["net_vorp"] += my_swing
            if my_swing > TIE_MARGIN_VORP:
                st["wins"] += 1
            elif my_swing < -TIE_MARGIN_VORP:
                st["losses"] += 1
            else:
                st["pushes"] += 1
            partner_counts[own][opp] += 1
            partner_net[own][opp] += my_swing
            owner_trades[own].append((my_swing, tr))

    cards = []
    for own, st in stats.items():
        n = st["trades"]
        decided = st["wins"] + st["losses"]
        win_pct = round((st["wins"] / decided) * 100.0, 1) if decided > 0 else 0.0
        avg_swing = round(st["net_vorp"] / n, 1) if n > 0 else 0.0

        tlist = owner_trades[own]
        best_tr = max(tlist, key=lambda x: x[0]) if tlist else None
        worst_tr = min(tlist, key=lambda x: x[0]) if tlist else None

        fav_partner = None
        if partner_counts[own]:
            p_own, p_cnt = partner_counts[own].most_common(1)[0]
            fav_partner = {"owner": p_own, "count": p_cnt, "net_vorp": round(partner_net[own][p_own], 1)}

        biggest_mark = None
        worst_nemesis = None
        if partner_net[own]:
            mark_own, mark_val = max(partner_net[own].items(), key=lambda kv: kv[1])
            if mark_val > 5.0:
                biggest_mark = {"owner": mark_own, "net_vorp": round(mark_val, 1), "count": partner_counts[own][mark_own]}
            nem_own, nem_val = min(partner_net[own].items(), key=lambda kv: kv[1])
            if nem_val < -5.0:
                worst_nemesis = {"owner": nem_own, "net_vorp": round(nem_val, 1), "count": partner_counts[own][nem_own]}

        def _brief(pair: Optional[Tuple[float, dict]]) -> Optional[dict]:
            if not pair:
                return None
            sw, t = pair
            return {
                "trade_id": t["trade_id"],
                "season": t["season"],
                "week": t["trade_week"],
                "partner": t["owner_b"] if t["owner_a"] == own else t["owner_a"],
                "swing": round(sw, 1),
                "received": [p["name"] for p in (t["a_received"] if t["owner_a"] == own else t["b_received"])],
                "sent": [p["name"] for p in (t["b_received"] if t["owner_a"] == own else t["a_received"])],
            }

        cards.append({
            "owner": own,
            "trades": n,
            "wins": st["wins"],
            "losses": st["losses"],
            "pushes": st["pushes"],
            "win_pct": win_pct,
            "net_vorp": round(st["net_vorp"], 1),
            "avg_swing": avg_swing,
            "best_trade": _brief(best_tr),
            "worst_trade": _brief(worst_tr),
            "favorite_partner": fav_partner,
            "biggest_mark": biggest_mark,
            "worst_nemesis": worst_nemesis,
        })

    cards.sort(key=lambda c: (-c["net_vorp"], -c["win_pct"], -c["trades"]))
    return cards


def evaluate_all_trades(db_path: Path = DB_PATH, current_season: int = int(CURRENT_YEAR)) -> dict:
    """
    Evaluates every trade in SQLite (`trades` + `trade_items`) under both VORP lenses
    (`started` = Actual Lineup Impact, `ros` = Pure Player Outcome) with 1.5x
    Championship Playoff weighting, Matchups Flipped, Trade Trees, and Owner Report Cards.
    Splits results cleanly into `all_time` (2018..current_season-1) and `live` (current_season).
    """
    manifest = load_manifest()
    active_owners = [t["owner"] for t in manifest.get("teams", [])]

    empty_pack = {
        "methodology": {
            "playoff_multiplier": PLAYOFF_WEIGHT,
            "tie_margin_vorp": TIE_MARGIN_VORP,
            "replacement_ranks": REPLACEMENT_RANKS,
        },
        "replacement_baselines": {},
        "all_time": {
            "seasons": [],
            "trade_count": 0,
            "trades": [],
            "owner_cards": {"started": [], "ros": []},
        },
        "live": {
            "season": current_season,
            "trade_count": 0,
            "trades": [],
            "owner_cards": {"started": [], "ros": []},
        },
    }

    if not db_path.exists():
        return empty_pack

    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    cur = conn.cursor()
    has_trades = cur.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='trades'"
    ).fetchone()[0]
    if not has_trades:
        conn.close()
        return empty_pack

    trade_rows = cur.execute(
        """
        SELECT trade_id, season, trade_week, effective_week, team_a_id, owner_a, team_b_id, owner_b
        FROM trades
        ORDER BY season DESC, trade_week DESC, trade_id ASC
        """
    ).fetchall()
    if not trade_rows:
        conn.close()
        return empty_pack

    item_rows = cur.execute(
        """
        SELECT trade_id, season, from_team_id, from_owner, to_team_id, to_owner, player_id, player_name, position
        FROM trade_items
        """
    ).fetchall()
    items_by_trade: Dict[str, List[dict]] = defaultdict(list)
    for r in item_rows:
        items_by_trade[r[0]].append({
            "trade_id": r[0],
            "season": int(r[1]),
            "from_team_id": int(r[2]),
            "from_owner": _canon_owner(r[3], int(r[1])),
            "to_team_id": int(r[4]),
            "to_owner": _canon_owner(r[5], int(r[1])),
            "player_id": int(r[6]),
            "player_name": r[7],
            "position": r[8] or "FLEX",
        })

    seasons_present = sorted({int(r[1]) for r in trade_rows})
    baselines_by_season = {s: compute_season_replacement_baselines(conn, s) for s in seasons_present}

    # Preload playoff start week per season
    playoff_start_by_season: Dict[int, int] = {}
    for s in seasons_present:
        po_min = cur.execute(
            "SELECT MIN(week) FROM games WHERE season = ? AND is_playoffs = 1", (s,)
        ).fetchone()[0]
        playoff_start_by_season[s] = int(po_min) if po_min else (14 if s <= 2020 else 15)

    # Preload team_seasons outcomes
    ts_outcomes: Dict[Tuple[int, int], dict] = {}
    for r in cur.execute(
        """
        SELECT season, team_id, wins, losses, made_playoffs, won_championship, runner_up, final_rank
        FROM team_seasons
        """
    ).fetchall():
        ts_outcomes[(int(r[0]), int(r[1]))] = {
            "record": f"{int(r[2])}-{int(r[3])}",
            "made_playoffs": bool(r[4]),
            "won_champ": bool(r[5]),
            "runner_up": bool(r[6]),
            "final_rank": int(r[7]),
        }

    # Preload non-consolation games for Matchups Flipped detection: (season, team_id, week) -> (win, margin, is_playoffs)
    games_lookup: Dict[Tuple[int, int, int], Tuple[int, float, int]] = {}
    for r in cur.execute(
        "SELECT season, team_id, week, win, margin, is_playoffs FROM games WHERE is_consolation = 0"
    ).fetchall():
        games_lookup[(int(r[0]), int(r[1]), int(r[2]))] = (int(r[3]), float(r[4]), int(r[5]))

    # Preload all weekly boxscores for traded player_ids
    traded_pids = sorted({it["player_id"] for its in items_by_trade.values() for it in its})
    pb_by_season_player: Dict[Tuple[int, int], List[dict]] = defaultdict(list)
    if traded_pids:
        # Query in chunks to stay well below SQLite variable limit
        for i in range(0, len(traded_pids), 400):
            chunk = traded_pids[i : i + 400]
            ph = ",".join("?" for _ in chunk)
            for r in cur.execute(
                f"""
                SELECT season, week, team_id, player_id, position, is_starter, is_regular, is_playoffs, is_consolation, points
                FROM player_boxscores
                WHERE player_id IN ({ph})
                ORDER BY week ASC
                """,
                chunk,
            ).fetchall():
                pb_by_season_player[(int(r[0]), int(r[3]))].append({
                    "week": int(r[1]),
                    "team_id": int(r[2]),
                    "position": r[4] or "FLEX",
                    "is_starter": int(r[5]) == 1,
                    "is_regular": int(r[6]) == 1,
                    "is_playoffs": int(r[7]) == 1,
                    "is_consolation": int(r[8]) == 1,
                    "points": float(r[9] or 0.0),
                })

    # Pre-index later trades in the same season to detect Trade Trees ("Flipped Wk X")
    later_flips: Dict[Tuple[int, int, int], List[Tuple[int, str]]] = defaultdict(list)
    trade_eff_map = {r[0]: int(r[3]) for r in trade_rows}
    for tid, its in items_by_trade.items():
        ew = trade_eff_map.get(tid, 1)
        for it in its:
            later_flips[(it["season"], it["from_team_id"], it["player_id"])].append((ew, it["to_owner"]))
    for k in later_flips:
        later_flips[k].sort(key=lambda x: x[0])

    evaluated_all_time: List[dict] = []
    evaluated_live: List[dict] = []

    for r in trade_rows:
        trade_id, season, trade_week, eff_wk, tid_a, own_a_raw, tid_b, own_b_raw = r
        season = int(season)
        trade_week = int(trade_week)
        eff_wk = int(eff_wk)
        tid_a, tid_b = int(tid_a), int(tid_b)
        own_a = _canon_owner(own_a_raw, season)
        own_b = _canon_owner(own_b_raw, season)

        its = items_by_trade.get(trade_id, [])
        if not its:
            continue

        baselines = baselines_by_season[season]
        po_start = playoff_start_by_season[season]

        a_received_items = [it for it in its if it["to_team_id"] == tid_a]
        b_received_items = [it for it in its if it["to_team_id"] == tid_b]
        if not a_received_items or not b_received_items:
            continue

        def _eval_side(side_items: List[dict], acq_team_id: int) -> Tuple[List[dict], dict]:
            players_out = []
            tot_started_vorp = 0.0
            tot_ros_vorp = 0.0
            tot_po_started_vorp = 0.0
            tot_po_ros_vorp = 0.0
            weekly_started_vorp: Dict[int, float] = defaultdict(float)
            starts_list = []
            any_dropped_fast = False
            any_flipped = False

            for it in side_items:
                pid = it["player_id"]
                pos = it["position"] or "FLEX"
                repl = baselines.get(pos, FALLBACK_REPLACEMENT_PPG.get(pos, 8.0))
                rows = [
                    row for row in pb_by_season_player.get((season, pid), [])
                    if row["week"] >= eff_wk
                ]

                # Check if acquiring team later flipped this player in the same season
                flipped_info = None
                for f_wk, f_to in later_flips.get((season, acq_team_id, pid), []):
                    if f_wk > eff_wk:
                        flipped_info = {"week": f_wk, "to": f_to}
                        any_flipped = True
                        break

                st_vorp = 0.0
                po_st_vorp = 0.0
                st_pts = 0.0
                st_wks = 0
                ros_vorp = 0.0
                po_ros_vorp = 0.0
                ros_pts = 0.0
                ros_wks = 0
                rostered_wks = 0

                # Track unique weeks for Pure ROS outcome
                seen_ros_wks = set()
                for row in rows:
                    wk = row["week"]
                    pts = row["points"]
                    if row["team_id"] == acq_team_id:
                        rostered_wks += 1
                        if row["is_starter"] and not row["is_consolation"]:
                            w_mult = PLAYOFF_WEIGHT if row["is_playoffs"] else 1.0
                            delta = pts - repl
                            w_delta = w_mult * delta
                            st_vorp += w_delta
                            if row["is_playoffs"]:
                                po_st_vorp += w_delta
                            st_pts += pts
                            st_wks += 1
                            weekly_started_vorp[wk] += delta

                    if wk not in seen_ros_wks:
                        seen_ros_wks.add(wk)
                        if pts > 0 or row["is_starter"]:
                            w_mult_ros = PLAYOFF_WEIGHT if wk >= po_start else 1.0
                            pos_delta = max(0.0, pts - repl)
                            w_pos_delta = w_mult_ros * pos_delta
                            ros_vorp += w_pos_delta
                            if wk >= po_start:
                                po_ros_vorp += w_pos_delta
                            ros_pts += pts
                            ros_wks += 1

                dropped_fast = (rostered_wks <= 3 and eff_wk <= 10 and flipped_info is None)
                if dropped_fast:
                    any_dropped_fast = True

                starts_list.append(st_wks)
                tot_started_vorp += st_vorp
                tot_ros_vorp += ros_vorp
                tot_po_started_vorp += po_st_vorp
                tot_po_ros_vorp += po_ros_vorp

                players_out.append({
                    "player_id": pid,
                    "name": it["player_name"],
                    "pos": pos,
                    "repl": repl,
                    "started_wks": st_wks,
                    "started_pts": round(st_pts, 1),
                    "started_ppg": round(st_pts / st_wks, 1) if st_wks > 0 else 0.0,
                    "started_vorp": round(st_vorp, 1),
                    "po_started_vorp": round(po_st_vorp, 1),
                    "ros_wks": ros_wks,
                    "ros_pts": round(ros_pts, 1),
                    "ros_ppg": round(ros_pts / ros_wks, 1) if ros_wks > 0 else 0.0,
                    "ros_vorp": round(ros_vorp, 1),
                    "po_ros_vorp": round(po_ros_vorp, 1),
                    "rostered_wks": rostered_wks,
                    "dropped_fast": dropped_fast,
                    "flipped": flipped_info,
                })

            # Exact Matchups Flipped for acquiring team
            games_swung = 0
            po_games_swung = 0
            for wk, net_v in weekly_started_vorp.items():
                if net_v <= 0:
                    continue
                ginfo = games_lookup.get((season, acq_team_id, wk))
                if ginfo and ginfo[0] == 1 and net_v > abs(ginfo[1]):
                    games_swung += 1
                    if ginfo[2] == 1:
                        po_games_swung += 1

            summary = {
                "started_vorp": round(tot_started_vorp, 1),
                "ros_vorp": round(tot_ros_vorp, 1),
                "po_started_vorp": round(tot_po_started_vorp, 1),
                "po_ros_vorp": round(tot_po_ros_vorp, 1),
                "games_swung": games_swung,
                "po_games_swung": po_games_swung,
                "avg_starts": round(sum(starts_list) / len(starts_list), 1) if starts_list else 0.0,
                "dropped_fast": any_dropped_fast,
                "any_flipped": any_flipped,
            }
            return players_out, summary

        a_players, a_sum = _eval_side(a_received_items, tid_a)
        b_players, b_sum = _eval_side(b_received_items, tid_b)

        a_out = ts_outcomes.get((season, tid_a), {"record": "", "made_playoffs": False, "won_champ": False, "runner_up": False, "final_rank": 0})
        b_out = ts_outcomes.get((season, tid_b), {"record": "", "made_playoffs": False, "won_champ": False, "runner_up": False, "final_rank": 0})
        any_flipped = a_sum["any_flipped"] or b_sum["any_flipped"]

        def _lens_block(a_v: float, b_v: float, a_po: float, b_po: float) -> dict:
            swing_a = round(a_v - b_v, 1)
            abs_sw = round(abs(swing_a), 1)
            if abs_sw <= TIE_MARGIN_VORP:
                winner = "PUSH"
                loser = "PUSH"
                w_v, l_v = max(a_v, b_v), min(a_v, b_v)
                w_po = max(a_po, b_po)
                w_champ = a_out["won_champ"] or b_out["won_champ"]
                w_ru = a_out["runner_up"] or b_out["runner_up"]
                w_swung = max(a_sum["games_swung"], b_sum["games_swung"])
                l_avg_st = min(a_sum["avg_starts"], b_sum["avg_starts"])
                l_drop = a_sum["dropped_fast"] or b_sum["dropped_fast"]
            elif swing_a > 0:
                winner, loser = own_a, own_b
                w_v, l_v = a_v, b_v
                w_po = a_po
                w_champ, w_ru = a_out["won_champ"], a_out["runner_up"]
                w_swung = a_sum["games_swung"]
                l_avg_st, l_drop = b_sum["avg_starts"], b_sum["dropped_fast"]
            else:
                winner, loser = own_b, own_a
                w_v, l_v = b_v, a_v
                w_po = b_po
                w_champ, w_ru = b_out["won_champ"], b_out["runner_up"]
                w_swung = b_sum["games_swung"]
                l_avg_st, l_drop = a_sum["avg_starts"], a_sum["dropped_fast"]

            tags = _compute_tags(
                abs_swing=abs_sw,
                win_vorp=w_v,
                lose_vorp=l_v,
                win_playoff_vorp=w_po,
                win_won_champ=w_champ,
                win_runner_up=w_ru,
                win_games_swung=w_swung,
                lose_avg_starts=l_avg_st,
                lose_dropped_fast=l_drop,
                has_flipped_item=any_flipped,
                effective_week=eff_wk,
            )
            return {
                "a_vorp": round(a_v, 1),
                "b_vorp": round(b_v, 1),
                "swing_a": swing_a,
                "abs_swing": abs_sw,
                "winner": winner,
                "loser": loser,
                "win_playoff_vorp": round(w_po, 1),
                "tags": tags,
            }

        started_lens = _lens_block(
            a_sum["started_vorp"], b_sum["started_vorp"],
            a_sum["po_started_vorp"], b_sum["po_started_vorp"],
        )
        ros_lens = _lens_block(
            a_sum["ros_vorp"], b_sum["ros_vorp"],
            a_sum["po_ros_vorp"], b_sum["po_ros_vorp"],
        )

        # Sort players within each side by highest ROS VORP so headliners appear first
        a_players.sort(key=lambda p: (-p["ros_vorp"], -p["started_pts"]))
        b_players.sort(key=lambda p: (-p["ros_vorp"], -p["started_pts"]))

        trade_obj = {
            "trade_id": trade_id,
            "season": season,
            "trade_week": trade_week,
            "effective_week": eff_wk,
            "owner_a": own_a,
            "owner_b": own_b,
            "a_outcome": a_out,
            "b_outcome": b_out,
            "a_games_swung": a_sum["games_swung"],
            "b_games_swung": b_sum["games_swung"],
            "a_received": a_players,
            "b_received": b_players,
            "started": started_lens,
            "ros": ros_lens,
        }

        if season < current_season:
            evaluated_all_time.append(trade_obj)
        elif season == current_season:
            evaluated_live.append(trade_obj)

    conn.close()

    # Default sort: most lopsided first under Actual Lineup Impact
    evaluated_all_time.sort(key=lambda t: (-t["started"]["abs_swing"], -t["ros"]["abs_swing"], -t["season"]))
    evaluated_live.sort(key=lambda t: (-t["started"]["abs_swing"], -t["ros"]["abs_swing"], -t["trade_week"]))

    return {
        "methodology": {
            "playoff_multiplier": PLAYOFF_WEIGHT,
            "tie_margin_vorp": TIE_MARGIN_VORP,
            "replacement_ranks": REPLACEMENT_RANKS,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        },
        "replacement_baselines": {str(k): v for k, v in baselines_by_season.items()},
        "all_time": {
            "seasons": sorted({t["season"] for t in evaluated_all_time}, reverse=True),
            "trade_count": len(evaluated_all_time),
            "trades": evaluated_all_time,
            "owner_cards": {
                "started": _build_owner_report_cards(evaluated_all_time, active_owners, "started"),
                "ros": _build_owner_report_cards(evaluated_all_time, active_owners, "ros"),
            },
        },
        "live": {
            "season": current_season,
            "trade_count": len(evaluated_live),
            "trades": evaluated_live,
            "owner_cards": {
                "started": _build_owner_report_cards(evaluated_live, active_owners, "started"),
                "ros": _build_owner_report_cards(evaluated_live, active_owners, "ros"),
            },
        },
    }


def main():
    parser = argparse.ArgumentParser(description="Sync & evaluate BFL Trade Vault (2018–present)")
    parser.add_argument("--sync", action="store_true", help="Sync trades from ESPN mTransactions2 into SQLite")
    parser.add_argument("--force", action="store_true", help="Force re-sync of all past seasons (2018–present)")
    parser.add_argument("--season", type=int, action="append", help="Specific season(s) to sync")
    parser.add_argument("--top", type=int, default=10, help="Print top N most lopsided all-time trades")
    args = parser.parse_args()

    if args.sync or args.force:
        sync_trades_from_espn(seasons=args.season, force=args.force)

    vault = evaluate_all_trades(DB_PATH, current_season=int(CURRENT_YEAR))
    at = vault["all_time"]
    lv = vault["live"]
    print(f"\n🏛️  ALL-TIME TRADE VAULT ({min(at['seasons']) if at['seasons'] else '?'}–{max(at['seasons']) if at['seasons'] else '?'}): {at['trade_count']} trades")
    for idx, t in enumerate(at["trades"][: args.top], 1):
        st = t["started"]
        rs = t["ros"]
        a_names = ", ".join(p["name"] for p in t["a_received"])
        b_names = ", ".join(p["name"] for p in t["b_received"])
        print(
            f"  {idx:2d}. {t['season']} Wk {t['trade_week']:2d} | {st['winner']} fleeced {st['loser']} "
            f"(Started: +{st['abs_swing']} | Pure ROS: +{rs['abs_swing']}) {' '.join('['+x+']' for x in st['tags'])}"
        )
        print(f"      {t['owner_a']} got [{a_names}] ({st['a_vorp']:+.1f} st / {rs['a_vorp']:+.1f} ros)")
        print(f"      {t['owner_b']} got [{b_names}] ({st['b_vorp']:+.1f} st / {rs['b_vorp']:+.1f} ros)")

    print(f"\n⚡ {lv['season']} LIVE TRACKER: {lv['trade_count']} trades")
    for idx, t in enumerate(lv["trades"][:5], 1):
        st = t["started"]
        rs = t["ros"]
        a_names = ", ".join(p["name"] for p in t["a_received"])
        b_names = ", ".join(p["name"] for p in t["b_received"])
        print(
            f"  {idx:2d}. Wk {t['trade_week']:2d} | {st['winner']} leads {st['loser']} "
            f"(Started: +{st['abs_swing']} | Pure ROS: +{rs['abs_swing']})"
        )
        print(f"      {t['owner_a']} got [{a_names}] | {t['owner_b']} got [{b_names}]")


if __name__ == "__main__":
    main()
