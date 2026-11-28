#!/usr/bin/env python3
"""
Multi-League "League-in-a-Box" Hub Builder & GCP Deployer
=========================================================
Auto-discovers ANY ESPN Fantasy Football league directly from ESPN `mSettings`
(8–16 teams, 0/2/4 divisions, 4/6/7/8 playoff spots, 0/1/2 first-round byes,
H2H_RECORD vs TOTAL_POINTS_SCORED tiebreakers, Standard/Half-PPR/Full-PPR,
Year-1 or multi-year history), syncs an isolated SQLite warehouse, computes
all dynamic N-team analytics (Live Mid-Week Playoff Odds, Optimal Weekly Lineups
+ FA Fills, Weekly Trends, All-Play Schedule Luck + PF+/PA+, Positional VORP
Heatmap, Draft Hall VORP, Dual-Lens Trade Vault, Franchise Dossiers, Weekly Awards),
and deploys to a dedicated subpath on Google Cloud Storage Free Tier!

Example:
  python3 FantasyRecap/multi_league_hub.py --league-id 755112359 --slug jff --season 2026 --deploy-gcs bfl-hub-7x9k2
  -> Live at: https://storage.googleapis.com/bfl-hub-7x9k2/jff/index.html
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import re
import sqlite3
import statistics
import subprocess
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

from FantasyRecap.core.identity_resolver import LeagueIdentityResolver
from FantasyRecap.core.manifest import dedupe_co_owners, extract_credited_owners, get_espn_credentials, load_project_env
from FantasyRecap.core.security import validate_league_id, validate_slug
load_project_env()

from FantasyRecap.bfl_history_db import init_db
from FantasyRecap.config import CURRENT_YEAR
from FantasyRecap.core.espn_client import POSITION_MAP, PRO_TEAM_MAP, SLOT_MAP
from FantasyRecap.league_recap_generator import analyze_week, fetch_espn_week_data
from FantasyRecap.playoff_odds_simulator import build_future_week_optimal_projections
from FantasyRecap.trade_vault_engine import (
    FALLBACK_REPLACEMENT_PPG,
    PLAYOFF_WEIGHT,
    TIE_MARGIN_VORP,
    _discover_roster_diff_trades,
    _extract_executed_trades_from_txs,
    _fetch_season_transactions,
    _resolve_player_metadata,
)
from FantasyRecap.weekly_awards_engine import compute_weekly_awards

WEB_HUB_DIR = SCRIPT_DIR / "web_hub"
LEAGUES_DB_DIR = SCRIPT_DIR / "live_state" / "leagues"
LEAGUES_DB_DIR.mkdir(parents=True, exist_ok=True)
POSITIONS = ("QB", "RB", "WR", "TE", "K", "D/ST")


def normalize_generic_owner_name(raw_name: str) -> str:
    """
    Cleans ESPN member names cleanly for any league:
    - Preserves mixed-case surnames like 'OHern', 'McDonald', and uppercase initials like 'Scott WK'
    - Title-cases all-lowercase names like 'cooper powell' -> 'Cooper Powell'
    """
    s = re.sub(r"\s+", " ", (raw_name or "").strip())
    if not s:
        return "Unknown"
    if s == s.lower() or s == s.upper():
        return " ".join(w.capitalize() if len(w) > 2 else w.upper() for w in s.split())
    return s


def make_league_badge(league_name: str, slug: str) -> str:
    words = [w for w in re.split(r"[^A-Za-z0-9]+", league_name or "") if w]
    if len(words) >= 2:
        initials = "".join(w[0].upper() for w in words[:4])
        if 2 <= len(initials) <= 4:
            return initials
    return re.sub(r"[^A-Za-z0-9]", "", slug).upper()[:4] or "FFL"


def compute_dynamic_replacement_ranks(team_count: int, roster_slots: Dict[str, int]) -> Dict[str, int]:
    """Scales VORP waiver-wire replacement ranks cleanly to ANY league size & roster format (incl. Superflex & IDP)."""
    n = max(6, int(team_count))
    qb = max(1, int(roster_slots.get("QB", 1)))
    rb = max(1, int(roster_slots.get("RB", 2)))
    wr = max(1, int(roster_slots.get("WR", 2)))
    te = max(1, int(roster_slots.get("TE", 1)))
    flx = max(0, int(roster_slots.get("FLEX", 1)))
    op = max(0, int(roster_slots.get("OP", 0)))  # Superflex
    ranks = {
        "QB": round(n * (qb + 0.75 * op)),
        "RB": round(n * (rb + 0.25 * flx)),
        "WR": round(n * (wr + 0.375 * flx)),
        "TE": round(n * te),
        "K": max(n, round(n * max(1, int(roster_slots.get("K", 1))))),
        "D/ST": max(n, round(n * max(1, int(roster_slots.get("D/ST", 1))))),
    }
    for idp_pos in ("DL", "LB", "DB"):
        cnt = int(roster_slots.get(idp_pos, 0))
        if cnt > 0:
            ranks[idp_pos] = round(n * cnt)
    return ranks


def score_draft_picks_multi_format(
    dp_rows: List[tuple],
    repl_ranks: Dict[str, int],
    team_count: int,
    *,
    draft_type: str = "SNAKE",
    season_draft_types: Optional[Dict[int, str]] = None,
    season_repl_ranks: Optional[Dict[int, Dict[str, int]]] = None,
) -> Tuple[dict, List[int], List[dict]]:
    """
    Scores Draft Hall steals & busts across Snake, Auction, Keeper, Dynasty Linear,
    AND Mixed-History leagues that changed rules over time (e.g., Snake in 2021–2023
    then Auction + Superflex in 2024–2026!):
      - Evaluates draft mode (`AUCTION` vs `SNAKE` vs short `LINEAR` rookie draft) and
        positional replacement baselines **independently per season `yr`**.
      - Excludes keeper picks (`keeper == 1`) so late-round keepers never skew steals.
      - Because every era is normalized to `+VORP points above expectation`, All-Time
        Steals & Busts rank Snake-era picks (`Rd 10`) and Auction-era picks (`$3`)
        side-by-side on the exact same value scale!
    """
    valid_rows = [r for r in dp_rows if len(r) >= 9]
    rows_by_yr: Dict[int, List[tuple]] = defaultdict(list)
    max_round_by_yr: Dict[int, int] = defaultdict(int)
    for r in valid_rows:
        yr = int(r[0])
        rows_by_yr[yr].append(r)
        max_round_by_yr[yr] = max(max_round_by_yr[yr], int(r[2] or 0))

    # Determine draft mode PER SEASON so Snake -> Auction rule transitions never corrupt prior years!
    mode_by_yr: Dict[int, str] = {}
    all_yrs = sorted(rows_by_yr.keys())
    for yr in all_yrs:
        has_bids = any(int(r[9] or 0) > 0 for r in rows_by_yr[yr] if len(r) >= 10)
        explicit = str((season_draft_types or {}).get(yr) or "").upper()
        if has_bids or explicit == "AUCTION" or (not season_draft_types and len(all_yrs) == 1 and str(draft_type).upper() == "AUCTION"):
            mode_by_yr[yr] = "AUCTION"
        elif explicit == "LINEAR" or (0 < max_round_by_yr[yr] <= 6):
            mode_by_yr[yr] = "LINEAR"
        else:
            mode_by_yr[yr] = "SNAKE"

    # Replacement baseline per (season, pos) using that exact season's replacement ranks!
    curves = defaultdict(list)
    for r in valid_rows:
        curves[(int(r[0]), r[5])].append(float(r[8] or 0.0))
    repl_pts_map = {}
    for (yr, pos), vals in curves.items():
        vals.sort(reverse=True)
        yr_ranks = (season_repl_ranks or {}).get(yr) or repl_ranks
        r_idx = min(yr_ranks.get(pos, team_count) - 1, len(vals) - 1)
        repl_pts_map[(yr, pos)] = vals[r_idx] if vals else 0.0

    # Auction expected-VORP curve & spend rank computed only for AUCTION seasons
    auction_vorp_curves = defaultdict(list)
    spend_rank_map = {}
    by_yr_bid = defaultdict(list)
    for idx, r in enumerate(valid_rows):
        yr = int(r[0])
        if mode_by_yr.get(yr) != "AUCTION":
            continue
        is_keeper = int(r[10] or 0) if len(r) >= 11 else 0
        if is_keeper:
            continue
        pos, pts = r[5], float(r[8] or 0.0)
        bid = int(r[9] or 0) if len(r) >= 10 else 0
        rp = repl_pts_map.get((yr, pos), 0.0)
        auction_vorp_curves[yr].append(max(0.0, pts - rp))
        by_yr_bid[yr].append((bid, -int(r[1]), idx))
    for yr in auction_vorp_curves:
        auction_vorp_curves[yr].sort(reverse=True)
    for yr, items in by_yr_bid.items():
        items.sort(reverse=True)
        for rank_i, (_, _, row_idx) in enumerate(items, 1):
            spend_rank_map[row_idx] = rank_i

    scored_dp = []
    for idx, r in enumerate(valid_rows):
        yr, pick, rnd = int(r[0]), int(r[1]), int(r[2])
        own, pname, pos = r[3], r[4], r[5]
        pdr, pfr, pts = int(r[6] or 0), int(r[7] or 0), float(r[8] or 0.0)
        bid = int(r[9] or 0) if len(r) >= 10 else 0
        is_keeper = int(r[10] or 0) if len(r) >= 11 else 0
        if is_keeper:
            continue
        c = curves.get((yr, pos), [])
        if not c or pdr <= 0:
            continue
        rp = repl_pts_map[(yr, pos)]
        actual_vorp = max(0.0, pts - rp)
        dmode = mode_by_yr.get(yr, "SNAKE")

        if dmode == "AUCTION":
            avc = auction_vorp_curves.get(yr, [])
            srank = spend_rank_map.get(idx, pick)
            exp_vorp = avc[min(srank - 1, len(avc) - 1)] if avc else 0.0
            delta = round(actual_vorp - exp_vorp, 1)
        else:
            exp_pts = c[min(pdr - 1, len(c) - 1)]
            delta = round(actual_vorp - max(0.0, exp_pts - rp), 1)

        scored_dp.append({
            "season": yr,
            "pick": pick,
            "round": rnd,
            "bid": bid if dmode == "AUCTION" else 0,
            "draft_mode": dmode,
            "owner": own,
            "player": pname,
            "pos": pos,
            "pos_draft_rank": pdr,
            "pos_finish_rank": pfr,
            "pos_leap": pdr - pfr,
            "pts": round(pts, 1),
            "delta": delta,
        })

    def _is_steal(x: dict) -> bool:
        if x["delta"] <= 0:
            return False
        if x["draft_mode"] == "AUCTION":
            return x["bid"] <= 15
        if x["draft_mode"] == "LINEAR":
            return x["round"] >= 2 and x["pos_leap"] >= 1
        return x["round"] >= 4 and x["pos_leap"] >= 3

    def _is_bust(x: dict) -> bool:
        if x["delta"] >= 0:
            return False
        if x["draft_mode"] == "AUCTION":
            return x["bid"] >= 20
        if x["draft_mode"] == "LINEAR":
            return x["round"] == 1
        return x["round"] <= 5

    draft_seasons = sorted({x["season"] for x in scored_dp}, reverse=True)
    drafts = {}
    for label, sub in [("all", scored_dp)] + [(str(y), [x for x in scored_dp if x["season"] == y]) for y in draft_seasons]:
        steals = sorted([x for x in sub if _is_steal(x)], key=lambda x: (-x["delta"], -x["pos_leap"]))[:15]
        busts = sorted([x for x in sub if _is_bust(x)], key=lambda x: (x["delta"], x["pos_leap"]))[:15]
        modes_present = sorted({x["draft_mode"] for x in sub})
        drafts[label] = {"steals": steals, "busts": busts, "modes": modes_present}
    return drafts, draft_seasons, scored_dp


def discover_league_settings(league_id: str, cookies: dict, season: int, slug: str) -> Tuple[dict, dict]:
    """Fetches ESPN `mSettings`, `mTeam`, `mMembers`, `mMatchupScore` and derives full league config."""
    league_id = validate_league_id(league_id)
    slug = validate_slug(slug)
    url = (
        f"https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/{season}"
        f"/segments/0/leagues/{league_id}?view=mSettings&view=mTeam&view=mMembers&view=mMatchupScore"
    )
    r = requests.get(url, cookies=cookies, timeout=12)
    r.raise_for_status()
    raw = r.json()

    settings = raw.get("settings", {})
    sched = settings.get("scheduleSettings", {})
    roster = settings.get("rosterSettings", {})
    scoring = settings.get("scoringSettings", {})
    draft_cfg = settings.get("draftSettings", {})
    status = raw.get("status", {})

    league_name = (settings.get("name") or f"League {league_id}").strip()
    team_count = int(settings.get("size") or len(raw.get("teams", [])) or 12)
    reg_weeks = int(sched.get("matchupPeriodCount") or 14)
    po_teams = int(sched.get("playoffTeamCount") or 6)
    po_rule = str(sched.get("playoffSeedingRule") or "TOTAL_POINTS_SCORED")
    po_reseed = bool(sched.get("playoffReseed", False))
    po_round_weeks = max(1, int(sched.get("playoffMatchupPeriodLength") or 1))
    uses_league_median = bool(sched.get("usesLeagueMedianScoring", False))
    po_byes = (8 - po_teams) if po_teams in (6, 7) else 0

    draft_type = str(draft_cfg.get("type") or "SNAKE").upper()
    keeper_count = int(draft_cfg.get("keeperCount") or 0)
    if keeper_count >= 10:
        format_label = "Dynasty"
    elif keeper_count > 0:
        format_label = "Keeper"
    elif draft_type == "AUCTION":
        format_label = "Auction"
    else:
        format_label = "Redraft"

    raw_divs = sched.get("divisions") or []
    has_divisions = len(raw_divs) > 1
    div_id_to_name = {int(d["id"]): (d.get("name") or f"Div {d['id']}").strip() for d in raw_divs}

    # Detect PPR scoring label (statId 53 = receptions)
    ppr_val = 0.0
    for it in (scoring.get("scoringItems") or []):
        if it.get("statId") == 53:
            ppr_val = float(it.get("points") or 0.0)
            break
    scoring_label = "Full PPR" if ppr_val >= 0.9 else ("Half PPR" if ppr_val >= 0.4 else "Standard")

    slot_counts_raw = roster.get("lineupSlotCounts") or {}
    dl_cnt = int(slot_counts_raw.get("8", 0)) + int(slot_counts_raw.get("9", 0)) + int(slot_counts_raw.get("11", 0))
    lb_cnt = int(slot_counts_raw.get("10", 0))
    db_cnt = int(slot_counts_raw.get("12", 0)) + int(slot_counts_raw.get("13", 0)) + int(slot_counts_raw.get("14", 0))
    roster_slots = {
        "QB": int(slot_counts_raw.get("0", 1)),
        "RB": int(slot_counts_raw.get("2", 2)),
        "WR": int(slot_counts_raw.get("4", 2)),
        "TE": int(slot_counts_raw.get("6", 1)),
        "FLEX": int(slot_counts_raw.get("23", 1)),
        "OP": int(slot_counts_raw.get("7", 0)),
        "D/ST": int(slot_counts_raw.get("16", 1)),
        "K": int(slot_counts_raw.get("17", 1)),
        "DL": dl_cnt,
        "LB": lb_cnt,
        "DB": db_cnt,
    }
    active_positions = [p for p in ("QB", "RB", "WR", "TE") if roster_slots.get(p, 0) > 0]
    if roster_slots.get("K", 0) > 0:
        active_positions.append("K")
    if roster_slots.get("D/ST", 0) > 0:
        active_positions.append("D/ST")
    for idp_p in ("DL", "LB", "DB"):
        if roster_slots.get(idp_p, 0) > 0:
            active_positions.append(idp_p)

    prev_seasons = sorted(int(y) for y in (status.get("previousSeasons") or []) if int(y) >= 2018)
    first_season = min(prev_seasons) if prev_seasons else int(season)

    # Extract members & teams cleanly (with duplicate-account deduplication!)
    members = {}
    for m in raw.get("members", []):
        full = f"{m.get('firstName', '').strip()} {m.get('lastName', '').strip()}".strip() or m.get("displayName", "").strip()
        members[m["id"]] = normalize_generic_owner_name(full)

    teams_by_id = {}
    divisions_map: Dict[str, List[str]] = {}
    for t in raw.get("teams", []):
        tid = int(t["id"])
        tname = (t.get("name") or f"{t.get('location', '')} {t.get('nickname', '')}").strip() or f"Team {tid}"
        primary_oid = t.get("primaryOwner") or (t.get("owners") or [None])[0]
        primary_own = members.get(primary_oid, tname)
        co_list = dedupe_co_owners([primary_own] + [members.get(oid, "") for oid in (t.get("owners") or [])])
        div_id = int(t.get("divisionId", 0))
        div_name = div_id_to_name.get(div_id, "") if has_divisions else ""
        teams_by_id[tid] = {
            "id": tid,
            "name": tname,
            "owner": primary_own,
            "co_owners": ", ".join(co_list),
            "division_id": div_id,
            "division": div_name,
        }
        if has_divisions:
            divisions_map.setdefault(div_name, []).append(primary_own)

    # Detect latest COMPLETED week (avg team score >= 40 and no 0-0 games)
    by_wk = defaultdict(list)
    for g in raw.get("schedule", []):
        wk = int(g.get("matchupPeriodId") or 0)
        if wk < 1 or wk > reg_weeks:
            continue
        a_sc = float((g.get("away") or {}).get("totalPoints", 0.0) or 0.0)
        h_sc = float((g.get("home") or {}).get("totalPoints", 0.0) or 0.0)
        by_wk[wk].append((a_sc, h_sc))

    completed_week = 0
    for wk in sorted(by_wk.keys()):
        pairs = by_wk[wk]
        if not pairs:
            continue
        all_sc = [x for p in pairs for x in p]
        if (sum(all_sc) / len(all_sc)) >= 40.0 and not any(a == 0.0 and h == 0.0 for a, h in pairs):
            completed_week = wk

    config = {
        "league_id": str(league_id),
        "slug": slug,
        "name": league_name,
        "badge": make_league_badge(league_name, slug),
        "team_count": team_count,
        "regular_season_weeks": reg_weeks,
        "playoff_teams": po_teams,
        "playoff_byes": po_byes,
        "playoff_reseed": po_reseed,
        "playoff_round_weeks": po_round_weeks,
        "uses_league_median": uses_league_median,
        "draft_type": draft_type,
        "keeper_count": keeper_count,
        "format_label": format_label,
        "tiebreaker_rule": po_rule,
        "has_divisions": has_divisions,
        "divisions": divisions_map,
        "scoring_label": scoring_label,
        "roster_slots": roster_slots,
        "active_positions": active_positions,
        "replacement_ranks": compute_dynamic_replacement_ranks(team_count, roster_slots),
        "previous_seasons": prev_seasons,
        "first_season": first_season,
        "is_first_year": len(prev_seasons) == 0,
        "completed_week": completed_week,
        "teams_by_id": teams_by_id,
    }
    return config, raw


def _load_optional_owner_aliases(slug: str) -> Dict[str, str]:
    cfg_p = SCRIPT_DIR / "leagues" / f"{slug}_podcast_config.json"
    if cfg_p.exists():
        try:
            return dict(json.loads(cfg_p.read_text(encoding="utf-8")).get("owner_aliases") or {})
        except Exception:
            pass
    return {}


def sync_generic_league_db(
    db_path: Path, config: dict, cookies: dict, season: int
) -> Tuple[Dict[int, dict], LeagueIdentityResolver]:
    """
    Syncs games, weekly player_boxscores, team_seasons, draft_picks, and trades for ANY ESPN league.
    Uses 3-tier `LeagueIdentityResolver` across seasons so:
      - Replaced managers stay strictly separated (tagged Alumni).
      - Marriage / name changes on the same ESPN GUID unify automatically.
    """
    conn = sqlite3.connect(db_path)
    init_db(conn)
    cur = conn.cursor()

    lid = config["league_id"]
    seasons_to_sync = sorted(set(config["previous_seasons"] + [int(season)]))
    season_rules_by_yr: Dict[int, dict] = {}
    resolver = LeagueIdentityResolver(
        owner_aliases=_load_optional_owner_aliases(config["slug"]),
        name_normalizer=normalize_generic_owner_name,
    )

    # Pass 1: Fetch all season summaries & register every `(season, member_id, raw_name, team_id)` observation
    raw_by_yr: Dict[int, dict] = {}
    for yr in seasons_to_sync:
        url = (
            f"https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/{yr}"
            f"/segments/0/leagues/{lid}?view=mMatchupScore&view=mTeam&view=mSettings&view=mMembers&view=mDraftDetail"
        )
        r = requests.get(url, cookies=cookies, timeout=12)
        if r.status_code != 200:
            continue
        data = r.json()
        raw_by_yr[yr] = data
        is_curr = (yr == int(season))
        m_raw = {}
        for m in data.get("members", []):
            full = f"{m.get('firstName', '').strip()} {m.get('lastName', '').strip()}".strip() or m.get("displayName", "").strip()
            m_raw[m["id"]] = normalize_generic_owner_name(full)
        for t in data.get("teams", []):
            tid = int(t["id"])
            tname = (t.get("name") or f"{t.get('location', '')} {t.get('nickname', '')}").strip() or f"Team {tid}"
            p_oid = t.get("primaryOwner") or (t.get("owners") or [None])[0]
            resolver.observe(yr, p_oid, m_raw.get(p_oid, ""), team_id=tid, team_name=tname, is_current_season=is_curr)
            for oid in (t.get("owners") or []):
                if oid and oid != p_oid and oid in m_raw:
                    resolver.observe(yr, oid, m_raw[oid], team_id=0, team_name="", is_current_season=is_curr)

    resolver.finalize()

    for yr, data in sorted(raw_by_yr.items()):
        is_curr = (yr == int(season))
        settings = data.get("settings", {})
        sched_set = settings.get("scheduleSettings", {})
        roster_set = settings.get("rosterSettings", {})
        scoring_set = settings.get("scoringSettings", {})
        draft_set = settings.get("draftSettings", {})
        reg_wks = int(sched_set.get("matchupPeriodCount") or config["regular_season_weeks"])
        po_teams = int(sched_set.get("playoffTeamCount") or config["playoff_teams"])
        raw_divs = sched_set.get("divisions") or []
        div_id_map = {int(d["id"]): (d.get("name") or f"Div {d['id']}").strip() for d in raw_divs}
        has_divs = len(raw_divs) > 1

        yr_tcount = int(settings.get("size") or len(data.get("teams", [])) or config["team_count"])
        yr_ppr = 0.0
        for it in (scoring_set.get("scoringItems") or []):
            if it.get("statId") == 53:
                yr_ppr = float(it.get("points") or 0.0)
                break
        yr_scoring = "Full PPR" if yr_ppr >= 0.9 else ("Half PPR" if yr_ppr >= 0.4 else "Standard")
        yr_slots_raw = roster_set.get("lineupSlotCounts") or {}
        yr_slots = {
            "QB": int(yr_slots_raw.get("0", 1)),
            "RB": int(yr_slots_raw.get("2", 2)),
            "WR": int(yr_slots_raw.get("4", 2)),
            "TE": int(yr_slots_raw.get("6", 1)),
            "FLEX": int(yr_slots_raw.get("23", 1)),
            "OP": int(yr_slots_raw.get("7", 0)),
            "D/ST": int(yr_slots_raw.get("16", 1)),
            "K": int(yr_slots_raw.get("17", 1)),
        }
        yr_dtype = str(draft_set.get("type") or "SNAKE").upper()
        yr_keepers = int(draft_set.get("keeperCount") or 0)
        yr_fmt = "Dynasty" if yr_keepers >= 10 else ("Keeper" if yr_keepers > 0 else ("Auction" if yr_dtype == "AUCTION" else "Snake"))
        sf_tag = " • SF" if yr_slots["OP"] > 0 else ""
        season_rules_by_yr[yr] = {
            "teams": yr_tcount,
            "scoring": yr_scoring,
            "draft_type": yr_dtype,
            "format": yr_fmt,
            "superflex": yr_slots["OP"] > 0,
            "repl_ranks": compute_dynamic_replacement_ranks(yr_tcount, yr_slots),
            "summary": f"{yr_tcount}T • {yr_fmt} • {yr_scoring}{sf_tag}",
        }

        members = {}
        for m in data.get("members", []):
            full = f"{m.get('firstName', '').strip()} {m.get('lastName', '').strip()}".strip() or m.get("displayName", "").strip()
            members[m["id"]] = normalize_generic_owner_name(full)

        tmap = {}
        for t in data.get("teams", []):
            tid = int(t["id"])
            tname = (t.get("name") or f"{t.get('location', '')} {t.get('nickname', '')}").strip() or f"Team {tid}"
            p_oid = t.get("primaryOwner") or (t.get("owners") or [None])[0]
            p_own = resolver.resolve(p_oid, members.get(p_oid, ""), season=yr, team_id=tid)
            co = dedupe_co_owners([p_own] + [resolver.resolve(oid, members.get(oid, ""), season=yr) for oid in (t.get("owners") or []) if oid in members])
            tmap[tid] = {
                "id": tid,
                "name": tname,
                "owner": p_own,
                "co_owners": ", ".join(co),
                "div_id": int(t.get("divisionId", 0)),
                "div_name": div_id_map.get(int(t.get("divisionId", 0)), "") if has_divs else "",
                "raw": t,
            }
            if is_curr and tid in config["teams_by_id"]:
                config["teams_by_id"][tid]["owner"] = p_own
                config["teams_by_id"][tid]["co_owners"] = ", ".join(co)

        # 1. Games table
        cur.execute("DELETE FROM games WHERE season = ?", (yr,))
        max_completed_wk = 0
        for g in data.get("schedule", []):
            wk = int(g.get("matchupPeriodId") or 0)
            away = g.get("away") or {}
            home = g.get("home") or {}
            a_id, h_id = away.get("teamId"), home.get("teamId")
            if not a_id or not h_id or a_id not in tmap or h_id not in tmap:
                continue
            a_sc = float(away.get("totalPoints", 0.0) or 0.0)
            h_sc = float(home.get("totalPoints", 0.0) or 0.0)
            if a_sc == 0.0 and h_sc == 0.0:
                continue
            if wk <= reg_wks:
                max_completed_wk = max(max_completed_wk, wk)
            tier = str(g.get("playoffTierType") or "NONE")
            is_reg = 1 if tier == "NONE" else 0
            is_po = 1 if tier == "WINNERS_BRACKET" else 0
            is_con = 1 if ("CONSOLATION" in tier) else 0
            mid = int(g.get("id") or 0)
            for (tid1, sc1, tid2, sc2) in ((a_id, a_sc, h_id, h_sc), (h_id, h_sc, a_id, a_sc)):
                t1, t2 = tmap[tid1], tmap[tid2]
                win = 1 if sc1 > sc2 else 0
                cur.execute(
                    """
                    INSERT OR REPLACE INTO games VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (yr, wk, mid, tier, is_reg, is_po, is_con, tid1, t1["name"], t1["owner"], t1["owner"],
                     t1["co_owners"], sc1, tid2, t2["name"], t2["owner"], t2["owner"], sc2, win, round(sc1 - sc2, 2)),
                )

        # 2. Team Seasons table
        cur.execute("DELETE FROM team_seasons WHERE season = ?", (yr,))
        is_done = 0 if is_curr else 1
        for tid, tinfo in tmap.items():
            traw = tinfo["raw"]
            rec = traw.get("record", {}).get("overall", {})
            drec = traw.get("record", {}).get("division", {})
            w, l, tie = int(rec.get("wins", 0)), int(rec.get("losses", 0)), int(rec.get("ties", 0))
            gp = max(1, w + l + tie)
            seed = int(traw.get("playoffSeed", 0) or 0)
            frank = int(traw.get("rankCalculatedFinal", 0) or 0)
            made_po = 1 if (is_done and 1 <= seed <= po_teams) else 0
            won_div = 1 if (is_done and has_divs and int(traw.get("divisionRank", 0) or 0) == 1) else 0
            won_ch = 1 if (is_done and frank == 1) else 0
            ru = 1 if (is_done and frank == 2) else 0
            cur.execute(
                """
                INSERT OR REPLACE INTO team_seasons VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (yr, tid, tinfo["name"], tinfo["owner"], tinfo["owner"], tinfo["co_owners"],
                 tinfo["div_id"], tinfo["div_name"], w, l, tie, round(w / gp, 4),
                 float(rec.get("pointsFor", 0.0) or 0.0), float(rec.get("pointsAgainst", 0.0) or 0.0),
                 int(drec.get("wins", 0)), int(drec.get("losses", 0)), int(drec.get("ties", 0)),
                 int(traw.get("divisionRank", 0) or 0), seed, frank, made_po, won_div, won_ch, ru, is_done),
            )

        # 3. Weekly Player Boxscores (parallel fetch for all completed weeks!)
        target_wks = list(range(1, (config["completed_week"] if is_curr else (reg_wks + 3)) + 1))

        def _fetch_box(wk_num: int):
            burl = (
                f"https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/{yr}"
                f"/segments/0/leagues/{lid}?scoringPeriodId={wk_num}&view=mBoxscore&view=mMatchupScore"
            )
            br = requests.get(burl, cookies=cookies, timeout=10)
            return wk_num, (br.json() if br.status_code == 200 else {})

        cur.execute("DELETE FROM player_boxscores WHERE season = ?", (yr,))
        player_season_totals: Dict[int, float] = defaultdict(float)
        player_season_gp: Dict[int, int] = defaultdict(int)
        player_pos_map: Dict[int, str] = {}
        player_name_map: Dict[int, str] = {}
        player_pro_map: Dict[int, str] = {}

        with ThreadPoolExecutor(max_workers=6) as ex:
            for wk_num, bdata in ex.map(_fetch_box, target_wks):
                for g in bdata.get("schedule", []):
                    if int(g.get("matchupPeriodId") or 0) != wk_num:
                        continue
                    tier = str(g.get("playoffTierType") or "NONE")
                    is_reg = 1 if tier == "NONE" else 0
                    is_po = 1 if tier == "WINNERS_BRACKET" else 0
                    is_con = 1 if ("CONSOLATION" in tier) else 0
                    mid = int(g.get("id") or 0)
                    away, home = g.get("away") or {}, g.get("home") or {}
                    a_id, h_id = away.get("teamId"), home.get("teamId")
                    for side_obj, tid_s, opp_id in ((away, a_id, h_id), (home, h_id, a_id)):
                        if not tid_s or tid_s not in tmap:
                            continue
                        tinfo = tmap[tid_s]
                        opp_own = tmap.get(opp_id, {}).get("owner", "")
                        entries = (side_obj.get("rosterForCurrentScoringPeriod") or {}).get("entries") or []
                        for e in entries:
                            pl = (e.get("playerPoolEntry") or {}).get("player") or {}
                            pid = int(pl.get("id") or 0)
                            if not pid:
                                continue
                            pname = pl.get("fullName") or f"Player {pid}"
                            pos = POSITION_MAP.get(int(pl.get("defaultPositionId") or 0), "FLEX")
                            pro = PRO_TEAM_MAP.get(int(pl.get("proTeamId") or 0), "FA")
                            slot_id = int(e.get("lineupSlotId", 20))
                            slot_nm = SLOT_MAP.get(slot_id, "Bench")
                            is_st = 0 if slot_id in (20, 21) else 1
                            pts = float((e.get("playerPoolEntry") or {}).get("appliedStatTotal", 0.0) or 0.0)
                            proj = 0.0
                            for st_item in (pl.get("stats") or []):
                                if st_item.get("statSourceId") == 1 and st_item.get("scoringPeriodId") == wk_num:
                                    proj = float(st_item.get("appliedTotal", 0.0) or 0.0)
                                    break
                            cur.execute(
                                """
                                INSERT OR REPLACE INTO player_boxscores VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                                """,
                                (yr, wk_num, mid, tid_s, tinfo["name"], tinfo["owner"], tinfo["co_owners"],
                                 opp_id or 0, opp_own, tier, is_reg, is_po, is_con, pid, pname, pro, pos,
                                 slot_id, slot_nm, is_st, round(pts, 2), round(proj, 2)),
                            )
                            player_name_map[pid] = pname
                            player_pos_map[pid] = pos
                            player_pro_map[pid] = pro
                            if is_con == 0:
                                player_season_totals[pid] += pts
                                if pts != 0.0 or is_st:
                                    player_season_gp[pid] += 1

        # 4. Draft Picks table
        picks_raw = (data.get("draftDetail") or {}).get("picks") or []
        if picks_raw:
            cur.execute("DELETE FROM draft_picks WHERE season = ?", (yr,))
            # Resolve missing drafted player names from player_boxscores or ESPN athlete API
            missing_pids = sorted({int(p.get("playerId") or 0) for p in picks_raw if int(p.get("playerId") or 0) not in player_name_map and int(p.get("playerId") or 0) > 0})
            if missing_pids:
                extra_meta = _resolve_player_metadata(conn, yr, missing_pids)
                for pid, (nm, ps) in extra_meta.items():
                    player_name_map[pid] = nm
                    player_pos_map[pid] = ps

            # Compute positional finish ranks & draft ranks
            pos_draft_cnt: Counter = Counter()
            by_pos_pts: Dict[str, List[Tuple[float, int]]] = defaultdict(list)
            for p in picks_raw:
                pid = int(p.get("playerId") or 0)
                pos = player_pos_map.get(pid, "FLEX")
                by_pos_pts[pos].append((player_season_totals.get(pid, 0.0), pid))
            pos_finish_rank_map: Dict[Tuple[str, int], int] = {}
            for pos, plist in by_pos_pts.items():
                plist.sort(key=lambda x: -x[0])
                for r_idx, (_pts, pid) in enumerate(plist, 1):
                    pos_finish_rank_map[(pos, pid)] = r_idx

            for p in sorted(picks_raw, key=lambda x: int(x.get("overallPickNumber") or 0)):
                opick = int(p.get("overallPickNumber") or 0)
                rnd = int(p.get("roundId") or 0)
                rpick = int(p.get("roundPickNumber") or 0)
                tid = int(p.get("teamId") or 0)
                pid = int(p.get("playerId") or 0)
                if opick <= 0 or tid not in tmap:
                    continue
                tinfo = tmap[tid]
                pname = player_name_map.get(pid, f"Player {pid}")
                pos = player_pos_map.get(pid, "FLEX")
                pro = player_pro_map.get(pid, "FA")
                pos_draft_cnt[pos] += 1
                pdr = pos_draft_cnt[pos]
                pfr = pos_finish_rank_map.get((pos, pid), 0)
                spts = round(player_season_totals.get(pid, 0.0), 2)
                gp = max(1, player_season_gp.get(pid, 0))
                cur.execute(
                    """
                    INSERT OR REPLACE INTO draft_picks VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (yr, opick, rnd, rpick, tid, tinfo["name"], tinfo["owner"], tinfo["co_owners"],
                     pid, pname, pro, pos, pdr, int(p.get("bidAmount") or 0),
                     1 if p.get("keeper") else 0, 1 if p.get("autoDraftTypeId") else 0,
                     spts, round(spts / gp, 2), 0, pfr, 0, pdr - pfr, 0.0, 0.0, 0.0, 0, 1),
                )

        # 5. Hybrid Trades sync (ESPN mTransactions2 + SQLite roster-diff)
        all_txs = _fetch_season_transactions(lid, cookies, yr, max_sp=reg_wks + 1)
        tx_trades = _extract_executed_trades_from_txs(all_txs, yr)
        for tr in tx_trades:
            tr["source"] = "espn_tx"
            tr["effective_week"] = tr["trade_week"]
        diff_trades = _discover_roster_diff_trades(conn, yr, tx_trades)
        trades = tx_trades + diff_trades
        trades.sort(key=lambda x: (x["trade_week"], x["process_date_ms"], x["trade_id"]))
        all_tpids = sorted({it["player_id"] for tr in trades for it in tr["items"]})
        pmeta = _resolve_player_metadata(conn, yr, all_tpids)
        cur.execute("DELETE FROM trade_items WHERE season = ?", (yr,))
        cur.execute("DELETE FROM trades WHERE season = ?", (yr,))
        for tr in trades:
            own_a = tmap.get(tr["team_a_id"], {}).get("owner", f"Team {tr['team_a_id']}")
            own_b = tmap.get(tr["team_b_id"], {}).get("owner", f"Team {tr['team_b_id']}")
            cur.execute(
                "INSERT OR REPLACE INTO trades VALUES (?,?,?,?,?,?,?,?,?,?)",
                (tr["trade_id"], yr, tr["trade_week"], tr["effective_week"], tr["team_a_id"], own_a,
                 tr["team_b_id"], own_b, tr["process_date_ms"], tr.get("source", "espn_tx")),
            )
            for it in tr["items"]:
                pname, pos = (it["player_name"], it["position"]) if "player_name" in it else pmeta.get(it["player_id"], (f"Player {it['player_id']}", "FLEX"))
                cur.execute(
                    "INSERT OR REPLACE INTO trade_items VALUES (?,?,?,?,?,?,?,?,?)",
                    (tr["trade_id"], yr, it["from_team_id"], tmap.get(it["from_team_id"], {}).get("owner", ""),
                     it["to_team_id"], tmap.get(it["to_team_id"], {}).get("owner", ""), it["player_id"], pname, pos),
                )
        conn.commit()
        print(f"✅ Synced {config['name']} Season {yr}: {len(trades)} trades, {len(picks_raw)} draft picks, {len(target_wks)} boxscore weeks")

    conn.close()
    return season_rules_by_yr, resolver


def run_generic_playoff_simulation(
    config: dict,
    completed_games: List[dict],
    full_schedule: List[dict],
    team_power: dict,
    *,
    weekly_projections: dict = None,
    n_sims: int = 3000,
    seed: int = 2026,
) -> dict:
    """Rule-aware Monte Carlo playoff simulator supporting ANY team count, 0/N divisions, 4/6/7/8 playoff teams, H2H vs PF tiebreakers."""
    rng = random.Random(seed)
    owners = [t["owner"] for t in config["teams_by_id"].values()]
    has_divs = config["has_divisions"]
    divisions = config["divisions"]
    po_teams = config["playoff_teams"]
    po_byes = config["playoff_byes"]
    use_pf_only = config["tiebreaker_rule"] == "TOTAL_POINTS_SCORED"
    use_median = bool(config.get("uses_league_median", False))

    base_rec = {o: {"wins": 0, "losses": 0, "pf": 0.0} for o in owners}
    base_h2h = defaultdict(int)
    max_done_wk = 0
    done_by_wk = defaultdict(list)
    for g in completed_games:
        gwk = int(g["week"])
        max_done_wk = max(max_done_wk, gwk)
        a, h, sa, sh = g["away"], g["home"], float(g["away_score"]), float(g["home_score"])
        base_rec[a]["pf"] += sa
        base_rec[h]["pf"] += sh
        done_by_wk[gwk].append((a, sa))
        done_by_wk[gwk].append((h, sh))
        if sa >= sh:
            base_rec[a]["wins"] += 1; base_rec[h]["losses"] += 1; base_h2h[(a, h)] += 1
        else:
            base_rec[h]["wins"] += 1; base_rec[a]["losses"] += 1; base_h2h[(h, a)] += 1
    if use_median:
        for _gwk, scores_wk in done_by_wk.items():
            if len(scores_wk) >= 2:
                med = statistics.median([s for _, s in scores_wk])
                for own, sc in scores_wk:
                    if sc >= med:
                        base_rec[own]["wins"] += 1
                    else:
                        base_rec[own]["losses"] += 1

    rem_games = [g for g in full_schedule if int(g["week"]) > max_done_wk]
    next_wk = min((int(g["week"]) for g in rem_games), default=max_done_wk + 1)

    po_cnt = Counter()
    div_cnt = Counter()
    bye_cnt = Counter()
    win_trials, win_po, loss_trials, loss_po = Counter(), Counter(), Counter(), Counter()
    next_opp = {}
    for g in rem_games:
        if int(g["week"]) == next_wk:
            next_opp[g["away"]] = g["home"]
            next_opp[g["home"]] = g["away"]

    def _rank_group(group, rec, h2h):
        by_w = defaultdict(list)
        for m in group:
            by_w[rec[m]["wins"]].append(m)
        out = []
        for w in sorted(by_w.keys(), reverse=True):
            tied = by_w[w]
            if len(tied) == 2 and not use_pf_only:
                a, b = tied
                wa, wb = h2h[(a, b)], h2h[(b, a)]
                if wa > wb:
                    out.extend([a, b]); continue
                if wb > wa:
                    out.extend([b, a]); continue
            out.extend(sorted(tied, key=lambda x: -rec[x]["pf"]))
        return out

    for _ in range(n_sims):
        rec = {o: dict(base_rec[o]) for o in owners}
        h2h = defaultdict(int, base_h2h)
        won_next = {}
        sim_wk_scores = defaultdict(list) if use_median else None
        for g in rem_games:
            a, h, wk = g["away"], g["home"], int(g["week"])
            wk_p = (weekly_projections or {}).get(wk, {})
            ai, hi = wk_p.get(a, {}), wk_p.get(h, {})
            a_mu = float(ai["live_proj"]) if (ai.get("is_live") and ai.get("live_proj", 0) > 0) else (0.75 * float(ai["optimal_proj"]) + 0.25 * team_power[a]["mean"] if ai.get("optimal_proj") else team_power[a]["mean"])
            h_mu = float(hi["live_proj"]) if (hi.get("is_live") and hi.get("live_proj", 0) > 0) else (0.75 * float(hi["optimal_proj"]) + 0.25 * team_power[h]["mean"] if hi.get("optimal_proj") else team_power[h]["mean"])
            a_std = team_power[a]["std"] * math.sqrt(max(0.02, float(ai.get("rem_frac", 1.0)))) if ai.get("is_live") else team_power[a]["std"]
            h_std = team_power[h]["std"] * math.sqrt(max(0.02, float(hi.get("rem_frac", 1.0)))) if hi.get("is_live") else team_power[h]["std"]
            sa = rng.gauss(a_mu, a_std)
            sh = rng.gauss(h_mu, h_std)
            rec[a]["pf"] += sa; rec[h]["pf"] += sh
            if sim_wk_scores is not None:
                sim_wk_scores[wk].append((a, sa))
                sim_wk_scores[wk].append((h, sh))
            if sa >= sh:
                rec[a]["wins"] += 1; rec[h]["losses"] += 1; h2h[(a, h)] += 1
                if wk == next_wk:
                    won_next[a] = True; won_next[h] = False
            else:
                rec[h]["wins"] += 1; rec[a]["losses"] += 1; h2h[(h, a)] += 1
                if wk == next_wk:
                    won_next[h] = True; won_next[a] = False
        if sim_wk_scores:
            for _swk, s_list in sim_wk_scores.items():
                if len(s_list) >= 2:
                    med = statistics.median([s for _, s in s_list])
                    for own, sc in s_list:
                        if sc >= med:
                            rec[own]["wins"] += 1
                        else:
                            rec[own]["losses"] += 1

        if has_divs:
            dchamps, wcpool = [], []
            for d_mems in divisions.values():
                ord_d = _rank_group(d_mems, rec, h2h)
                if ord_d:
                    dchamps.append(ord_d[0]); div_cnt[ord_d[0]] += 1
                    wcpool.extend(ord_d[1:])
            full = _rank_group(dchamps, rec, h2h) + _rank_group(wcpool, rec, h2h)
        else:
            full = _rank_group(owners, rec, h2h)

        po_set = set(full[:po_teams])
        for o in po_set:
            po_cnt[o] += 1
        for o in full[:po_byes]:
            bye_cnt[o] += 1
        for o, dw in won_next.items():
            if dw:
                win_trials[o] += 1
                if o in po_set: win_po[o] += 1
            else:
                loss_trials[o] += 1
                if o in po_set: loss_po[o] += 1

    out = {}
    for o in owners:
        wp = round((win_po[o] / max(1, win_trials[o])) * 100.0, 1) if win_trials[o] else 0.0
        lp = round((loss_po[o] / max(1, loss_trials[o])) * 100.0, 1) if loss_trials[o] else 0.0
        out[o] = {
            "owner": o,
            "record": f"{base_rec[o]['wins']}-{base_rec[o]['losses']}",
            "next_opp": next_opp.get(o, ""),
            "playoff_pct": round((po_cnt[o] / n_sims) * 100.0, 1),
            "div_title_pct": round((div_cnt[o] / n_sims) * 100.0, 1),
            "bye_pct": round((bye_cnt[o] / n_sims) * 100.0, 1),
            "win_po_pct": wp,
            "loss_po_pct": lp,
            "leverage_swing": round(abs(wp - lp), 1),
        }
    return out


def _compute_generic_repl_baselines(conn: sqlite3.Connection, season: int, repl_ranks: Dict[str, int]) -> Dict[str, float]:
    cur = conn.cursor()
    max_wk = cur.execute("SELECT COALESCE(MAX(week), 0) FROM player_boxscores WHERE season = ?", (season,)).fetchone()[0]
    min_g = 4 if max_wk >= 13 else 3
    rows = cur.execute(
        """
        SELECT position, player_id, AVG(points), COUNT(*) AS gp
        FROM player_boxscores
        WHERE season = ? AND is_consolation = 0 AND (points > 0 OR is_starter = 1)
        GROUP BY position, player_id
        HAVING gp >= ?
        """,
        (season, min_g),
    ).fetchall()
    by_pos = defaultdict(list)
    for pos, _pid, avg_p, _gp in rows:
        by_pos[pos or "FLEX"].append(float(avg_p or 0.0))
    out = dict(FALLBACK_REPLACEMENT_PPG)
    for pos, rank in repl_ranks.items():
        vals = sorted(by_pos.get(pos, []), reverse=True)
        if len(vals) >= max(4, rank // 2):
            idx = min(len(vals) - 1, rank - 1)
            lo, hi = max(0, idx - 1), min(len(vals), idx + 2)
            out[pos] = round(sum(vals[lo:hi]) / (hi - lo), 2)
    out["FLEX"] = round((out["RB"] + out["WR"]) / 2.0, 2)
    return out


def build_generic_hub_data(league_id: str, slug: str, season: int, force_week: int = 0) -> Tuple[dict, Path]:
    """Builds the complete `hub_data.json` for ANY ESPN league using dynamic N-team rules."""
    creds = get_espn_credentials()
    cookies = creds["cookies"]

    print(f"🔍 Auto-discovering ESPN League {league_id} ({slug}, Season {season})...")
    config, raw = discover_league_settings(league_id, cookies, season, slug)
    if force_week > 0:
        config["completed_week"] = force_week
    completed_week = config["completed_week"]
    print(
        f"   → {config['name']} ({config['badge']}) | {config['team_count']} teams | {config['scoring_label']} | "
        f"{'Divisions: ' + ', '.join(config['divisions'].keys()) if config['has_divisions'] else 'No Divisions'} | "
        f"{config['playoff_teams']} Playoff Teams ({config['playoff_byes']} Byes) | Tiebreaker: {config['tiebreaker_rule']} | Completed Wk {completed_week}"
    )

    db_path = LEAGUES_DB_DIR / f"{slug}_history.db"
    season_rules_by_yr, resolver = sync_generic_league_db(db_path, config, cookies, season)

    tmap = config["teams_by_id"]
    owners = [t["owner"] for t in tmap.values()]
    reg_wks = config["regular_season_weeks"]
    repl_ranks = config["replacement_ranks"]

    # Extract completed games, full schedule, and empirical team power
    completed_games, full_schedule = [], []
    scores_by_own = defaultdict(list)
    for g in raw.get("schedule", []):
        wk = int(g.get("matchupPeriodId") or 0)
        if wk < 1 or wk > reg_wks:
            continue
        a_id = (g.get("away") or {}).get("teamId")
        h_id = (g.get("home") or {}).get("teamId")
        if a_id not in tmap or h_id not in tmap:
            continue
        a_own, h_own = tmap[a_id]["owner"], tmap[h_id]["owner"]
        full_schedule.append({"week": wk, "away": a_own, "home": h_own})
        if wk <= completed_week:
            a_sc = float((g.get("away") or {}).get("totalPoints", 0.0) or 0.0)
            h_sc = float((g.get("home") or {}).get("totalPoints", 0.0) or 0.0)
            completed_games.append({"week": wk, "away": a_own, "home": h_own, "away_score": a_sc, "home_score": h_sc})
            scores_by_own[a_own].append(a_sc)
            scores_by_own[h_own].append(h_sc)

    all_pts = [p for plist in scores_by_own.values() for p in plist]
    lg_avg = statistics.mean(all_pts) if all_pts else 110.0
    team_power = {}
    for o in owners:
        pl = scores_by_own[o]
        raw_m = statistics.mean(pl) if pl else lg_avg
        reg_m = (sum(pl) + 4.0 * lg_avg) / (len(pl) + 4.0)
        std_v = max(12.0, min(26.0, statistics.stdev(pl) if len(pl) >= 3 else 18.0))
        team_power[o] = {"mean": round(reg_m, 2), "std": round(std_v, 2), "raw_avg": round(raw_m, 2)}

    future_weeks = list(range(completed_week + 1, reg_wks + 1))
    print(f"🧠 Solving optimal healthy weekly lineups (+Live Mid-Week & FA fills) for Weeks {future_weeks}...")
    weekly_proj = build_future_week_optimal_projections(
        season,
        future_weeks,
        teams_by_id=tmap,
        league_id=league_id,
        owner_canon_fn=normalize_generic_owner_name,
    )
    baseline_odds = run_generic_playoff_simulation(
        config, completed_games, full_schedule, team_power, weekly_projections=weekly_proj, n_sims=4000
    )

    # Weekly Awards for completed_week
    wk_raw = fetch_espn_week_data(str(league_id), str(season), completed_week, creds["espn_s2"], creds["espn_swid"])
    matchups_wk, perfs_wk, _ = analyze_week(wk_raw, completed_week)
    # Normalize owner names inside matchups_wk so awards match config owners cleanly
    for m in matchups_wk:
        for side in ("winner", "loser"):
            tid = m[side].get("id")
            if tid in tmap:
                m[side]["owner"] = tmap[tid]["owner"]
    awards_data = compute_weekly_awards(matchups_wk, perfs_wk, week_num=completed_week, season=season, playoff_odds=baseline_odds)

    # Query SQLite for History, Drafts (N-team Draft VORP), Trades (N-team VORP), Luck, Heatmap, Dossiers
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    cur = conn.cursor()

    # Career + H2H
    g_rows = cur.execute(
        "SELECT season, week, owner, co_owners, opp_owner, is_playoffs, score, opp_score, win, margin FROM games ORDER BY season, week"
    ).fetchall()
    career_agg = {}
    pair_games = defaultdict(list)
    for r in g_rows:
        s_yr, wk, own, _co, opp, is_po, sc, osc, win, margin = r
        sc_f, osc_f = float(sc), float(osc)
        ca = career_agg.setdefault(own, {"w": 0, "l": 0, "pw": 0, "pl": 0, "pf": 0.0, "pa": 0.0, "hi": 0.0, "seasons": set(), "first": int(s_yr)})
        ca["w"] += int(win); ca["l"] += 1 - int(win)
        if int(is_po): ca["pw"] += int(win); ca["pl"] += 1 - int(win)
        ca["pf"] += sc_f; ca["pa"] += osc_f; ca["hi"] = max(ca["hi"], sc_f)
        ca["seasons"].add(int(s_yr)); ca["first"] = min(ca["first"], int(s_yr))
        if own < opp:
            pair_games[f"{own}|||{opp}"].append({"season": int(s_yr), "week": int(wk), "is_playoffs": bool(is_po), "s1": round(sc_f, 2), "s2": round(osc_f, 2), "w1": int(win) == 1, "margin": round(abs(float(margin)), 2)})

    owner_div = {t["owner"]: t["division"] for t in tmap.values()}
    owner_tname = {t["owner"]: t["name"] for t in tmap.values()}
    career = []
    for own, ca in career_agg.items():
        gp = max(1, ca["w"] + ca["l"])
        career.append({
            "owner": own, "code": own.split()[0], "division": owner_div.get(own, ""), "team_name": owner_tname.get(own, ""),
            "is_active": own in owner_tname, "former_names": resolver.former_names_for(own),
            "seasons": len(ca["seasons"]), "first_season": ca["first"], "wins": ca["w"], "losses": ca["l"],
            "win_pct": round((ca["w"] / gp) * 100.0, 1), "po_wins": ca["pw"], "po_losses": ca["pl"],
            "avg_pf": round(ca["pf"] / gp, 2), "avg_pa": round(ca["pa"] / gp, 2), "high_score": round(ca["hi"], 2),
            "rings": 0, "champ_years": [], "runner_up_years": [],
        })
    career.sort(key=lambda x: (-x["win_pct"], -x["avg_pf"]))

    h2h_summary = {}
    for key, glist in pair_games.items():
        o1, o2 = key.split("|||")
        w1 = sum(1 for g in glist if g["w1"]); w2 = len(glist) - w1
        po1 = sum(1 for g in glist if g["is_playoffs"] and g["w1"])
        po2 = sum(1 for g in glist if g["is_playoffs"] and not g["w1"])
        st_w = o1 if glist[-1]["w1"] else o2
        st_n = 0
        for g in reversed(glist):
            if g["w1"] == glist[-1]["w1"]: st_n += 1
            else: break
        closest = min(glist, key=lambda g: g["margin"])
        last_g = glist[-1]
        h2h_summary[key] = {
            "o1": o1, "o2": o2, "w1": w1, "w2": w2, "games": len(glist), "po1": po1, "po2": po2,
            "avg1": round(sum(g["s1"] for g in glist) / len(glist), 1), "avg2": round(sum(g["s2"] for g in glist) / len(glist), 1),
            "streak_owner": st_w, "streak_len": st_n,
            "last": {"season": last_g["season"], "week": last_g["week"], "winner": o1 if last_g["w1"] else o2, "hi": max(last_g["s1"], last_g["s2"]), "lo": min(last_g["s1"], last_g["s2"])},
            "closest": {"season": closest["season"], "week": closest["week"], "winner": o1 if closest["w1"] else o2, "margin": closest["margin"]},
            "super_bowls": [],
        }

    # Draft Hall (Multi-Format & Rule-Evolution Aware: Snake, Auction $, Keeper/Dynasty per season!)
    dp_rows = cur.execute(
        "SELECT season, overall_pick, round_id, owner, player_name, position, pos_draft_rank, pos_finish_rank, season_points, bid_amount, keeper FROM draft_picks WHERE position IN ('QB','RB','WR','TE')"
    ).fetchall()
    season_dtypes = {yr: info["draft_type"] for yr, info in season_rules_by_yr.items()}
    season_rranks = {yr: info["repl_ranks"] for yr, info in season_rules_by_yr.items()}
    drafts, draft_seasons, scored_dp = score_draft_picks_multi_format(
        dp_rows,
        repl_ranks,
        config["team_count"],
        draft_type=config.get("draft_type", "SNAKE"),
        season_draft_types=season_dtypes,
        season_repl_ranks=season_rranks,
    )

    # Analytics Lab: All-Play Luck + PF+/PA+
    by_sw = defaultdict(list)
    for r in cur.execute("SELECT season, week, owner, score, opp_score, win FROM games WHERE is_regular = 1").fetchall():
        by_sw[(int(r[0]), int(r[1]))].append((r[2], float(r[3]), float(r[4]), int(r[5])))
    s_scores = defaultdict(list)
    valid_w = []
    for (s_yr, wk), ents in by_sw.items():
        if len(ents) < 2: continue
        scs = [e[1] for e in ents]
        if (sum(scs) / len(scs)) < 40.0 or any(e[1] == 0.0 and e[2] == 0.0 for e in ents): continue
        valid_w.append((s_yr, wk, ents, scs))
        s_scores[s_yr].extend(scs)
    s_avg = {y: sum(v) / len(v) for y, v in s_scores.items() if v}

    def _calc_luck(filter_yr: Optional[int]):
        st = {}
        for s_yr, _wk, ents, scs in valid_w:
            if filter_yr is not None and s_yr != filter_yr: continue
            sm = s_avg.get(s_yr, 100.0)
            n_t = len(ents)
            for own, sc, osc, win in ents:
                d = st.setdefault(own, {"owner": own, "division": owner_div.get(own, ""), "g": 0, "w": 0, "l": 0, "apw": 0.0, "apg": 0, "pf": 0.0, "pa": 0.0, "pfp": 0.0, "pap": 0.0})
                beat = sum(1 for x in scs if sc > x); tied = sum(1 for x in scs if sc == x) - 1
                d["g"] += 1; d["w"] += win; d["l"] += 1 - win
                d["apw"] += beat + 0.5 * max(0, tied); d["apg"] += (n_t - 1)
                d["pf"] += sc; d["pa"] += osc; d["pfp"] += 100.0 * sc / sm; d["pap"] += 100.0 * osc / sm
        rows = []
        for own, d in st.items():
            gp = max(1, d["g"]); ap_pct = d["apw"] / max(1, d["apg"]); exp_w = ap_pct * d["g"]; ld = d["w"] - exp_w
            v = "Schedule Blessed" if ld >= 1.5 else ("Slightly Lucky" if ld >= 0.7 else ("Schedule Cursed" if ld <= -1.5 else ("Unlucky" if ld <= -0.7 else "Fair Schedule")))
            rows.append({
                "owner": own, "division": d["division"], "games": d["g"], "wins": d["w"], "losses": d["l"],
                "win_pct": round((d["w"] / gp) * 100.0, 1), "ap_wins": round(d["apw"], 1), "ap_losses": round(d["apg"] - d["apw"], 1),
                "ap_pct": round(ap_pct * 100.0, 1), "expected_wins": round(exp_w, 2), "luck_delta": round(ld, 2),
                "avg_pf": round(d["pf"] / gp, 1), "avg_pa": round(d["pa"] / gp, 1),
                "pf_plus": round(d["pfp"] / gp, 1), "pa_plus": round(d["pap"] / gp, 1), "verdict": v,
            })
        for idx, r in enumerate(sorted(rows, key=lambda x: -x["pa_plus"]), 1): r["sos_rank"] = idx
        rows.sort(key=lambda x: (-x["ap_pct"], -x["avg_pf"]))
        for idx, r in enumerate(rows, 1): r["true_rank"] = idx
        return rows

    luck_2026 = _calc_luck(season)
    luck_all = _calc_luck(None)

    # Weekly Trends (Weeks 1..completed_week with James-Stein prior_n=10.0!)
    weeks_list = list(range(1, max(1, completed_week) + 1))
    series = {o: {"po": [], "rank": [], "pf": [], "wins": [], "ap_pct": []} for o in owners}
    cum_sc, cum_w, cum_apw, cum_apg = {o: [] for o in owners}, Counter(), defaultdict(float), Counter()
    by_wk_g = defaultdict(list)
    for g in completed_games:
        by_wk_g[int(g["week"])].append(g)
    for wk in weeks_list:
        wk_sc = {}
        for g in by_wk_g.get(wk, []):
            a, h, sa, sh = g["away"], g["home"], float(g["away_score"]), float(g["home_score"])
            wk_sc[a] = sa; wk_sc[h] = sh
            cum_sc[a].append(sa); cum_sc[h].append(sh)
            if sa >= sh: cum_w[a] += 1
            else: cum_w[h] += 1
        vals = list(wk_sc.values())
        if len(vals) >= 2:
            for o, sc in wk_sc.items():
                cum_apw[o] += sum(1 for x in vals if sc > x) + 0.5 * max(0, sum(1 for x in vals if sc == x) - 1)
                cum_apg[o] += (len(vals) - 1)
        flat = [p for pl in cum_sc.values() for p in pl]
        l_avg = statistics.mean(flat) if flat else 110.0
        pwr_w = {}
        for o in owners:
            pl = cum_sc[o]
            pwr_w[o] = {"mean": round((sum(pl) + 10.0 * l_avg) / (len(pl) + 10.0), 2), "std": round(max(12.0, min(26.0, statistics.stdev(pl) if len(pl) >= 4 else 18.0)), 2)}
        if wk == completed_week:
            odds_w = baseline_odds
        else:
            odds_w = run_generic_playoff_simulation(config, [g for g in completed_games if int(g["week"]) <= wk], full_schedule, pwr_w, n_sims=600, seed=season * 100 + wk)
        rt = []
        for o in owners:
            ap = cum_apw[o] / max(1, cum_apg[o])
            wp = cum_w[o] / max(1, len(cum_sc[o]))
            pf_t = sum(cum_sc[o])
            rt.append((o, 0.55 * ap + 0.35 * wp + 0.10 * (pf_t / max(1.0, l_avg * wk)), pf_t))
        rt.sort(key=lambda x: (-x[1], -x[2]))
        rmap = {o: idx for idx, (o, _, _) in enumerate(rt, 1)}
        for o in owners:
            series[o]["po"].append(odds_w[o]["playoff_pct"])
            series[o]["rank"].append(rmap[o])
            series[o]["pf"].append(round(sum(cum_sc[o]), 1))
            series[o]["wins"].append(cum_w[o])
            series[o]["ap_pct"].append(round((cum_apw[o] / max(1, cum_apg[o])) * 100.0, 1))

    # Positional Strength Heatmap (N-team replacement baselines & active positions incl. IDP/no-K!)
    active_pos = config.get("active_positions") or list(POSITIONS)
    baselines = _compute_generic_repl_baselines(conn, season, repl_ranks)
    pb_rows = cur.execute(
        "SELECT owner, position, week, points FROM player_boxscores WHERE season = ? AND is_starter = 1 AND is_consolation = 0 AND week <= ?",
        (season, completed_week),
    ).fetchall()
    own_wks, pos_agg = defaultdict(set), defaultdict(list)
    for own, pos, wk, pts in pb_rows:
        own_wks[own].add(int(wk))
        pos_agg[(own, pos)].append(float(pts or 0.0))
    hm_rows, pos_lists = [], {p: [] for p in active_pos}
    for o in owners:
        nw = max(1, len(own_wks.get(o, set())))
        pd_map, tot_v, sk = {}, 0.0, []
        for p in active_pos:
            vs = pos_agg.get((o, p), [])
            rp = baselines.get(p, 8.0)
            tv = sum(x - rp for x in vs)
            vw = round(tv / nw, 1)
            tot_v += tv
            pos_lists[p].append(vw)
            pd_map[p] = {"starts": len(vs), "ppg": round(sum(vs) / len(vs), 1) if vs else 0.0, "vorp_wk": vw, "total_vorp": round(tv, 1)}
            if p in ("QB", "RB", "WR", "TE"): sk.append((p, vw))
        hm_rows.append({
            "owner": o, "division": owner_div.get(o, ""), "weeks": nw,
            "total_vorp_wk": round(tot_v / nw, 1), "total_vorp": round(tot_v, 1),
            "best_pos": max(sk, key=lambda x: x[1])[0] if sk else "QB",
            "worst_pos": min(sk, key=lambda x: x[1])[0] if sk else "TE",
            "positions": pd_map,
        })
    hm_rows.sort(key=lambda r: -r["total_vorp_wk"])
    hm_bounds = {p: {"min": min(pos_lists[p] or [0.0]), "max": max(pos_lists[p] or [0.0])} for p in active_pos}

    # Trade Vault (reuses our dual-lens VORP logic with per-season N-team baselines so historical rule changes stay accurate!)
    trade_rows = cur.execute("SELECT trade_id, season, trade_week, effective_week, team_a_id, owner_a, team_b_id, owner_b FROM trades ORDER BY season DESC, trade_week DESC").fetchall()
    trade_seasons_set = {int(r[1]) for r in trade_rows}
    baselines_by_yr = {
        yr: (baselines if yr == int(season) else _compute_generic_repl_baselines(conn, yr, season_rranks.get(yr, repl_ranks)))
        for yr in trade_seasons_set
    }
    titems_by_id = defaultdict(list)
    for r in cur.execute("SELECT trade_id, season, from_team_id, from_owner, to_team_id, to_owner, player_id, player_name, position FROM trade_items").fetchall():
        titems_by_id[r[0]].append({"from_tid": int(r[2]), "to_tid": int(r[4]), "pid": int(r[6]), "name": r[7], "pos": r[8] or "FLEX"})
    pb_all = defaultdict(list)
    for r in cur.execute("SELECT season, week, team_id, player_id, is_starter, is_playoffs, is_consolation, points FROM player_boxscores ORDER BY week").fetchall():
        pb_all[(int(r[0]), int(r[3]))].append({"wk": int(r[1]), "tid": int(r[2]), "st": int(r[4]) == 1, "po": int(r[5]) == 1, "con": int(r[6]) == 1, "pts": float(r[7] or 0.0)})

    eval_live, eval_all = [], []
    for r in trade_rows:
        tid_s, yr, twk, ewk, ta, oa, tb, ob = r[0], int(r[1]), int(r[2]), int(r[3]), int(r[4]), r[5], int(r[6]), r[7]
        yr_base = baselines_by_yr.get(yr, baselines)
        its = titems_by_id.get(tid_s, [])
        a_its = [x for x in its if x["to_tid"] == ta]
        b_its = [x for x in its if x["to_tid"] == tb]
        if not a_its or not b_its: continue
        def _side(s_its, acq_tid):
            pout, sv_tot, rv_tot = [], 0.0, 0.0
            for it in s_its:
                rp = yr_base.get(it["pos"], 8.0)
                rows_p = [x for x in pb_all.get((yr, it["pid"]), []) if x["wk"] >= ewk]
                sv, sp, sw, rv_raw, rp_pts, rw = 0.0, 0.0, 0, 0.0, 0.0, 0
                seen_w = set()
                for x in rows_p:
                    if x["tid"] == acq_tid and x["st"] and not x["con"]:
                        sv += (PLAYOFF_WEIGHT if x["po"] else 1.0) * (x["pts"] - rp)
                        sp += x["pts"]; sw += 1
                    if x["wk"] not in seen_w:
                        seen_w.add(x["wk"])
                        if x["pts"] > 0 or x["st"]:
                            rv_raw += (PLAYOFF_WEIGHT if x["wk"] > reg_wks else 1.0) * (x["pts"] - rp)
                            rp_pts += x["pts"]; rw += 1
                rv = max(0.0, rv_raw)
                sv_tot += sv; rv_tot += rv
                pout.append({"player_id": it["pid"], "name": it["name"], "pos": it["pos"], "repl": rp, "started_wks": sw, "started_pts": round(sp, 1), "started_ppg": round(sp / sw, 1) if sw else 0.0, "started_vorp": round(sv, 1), "po_started_vorp": 0.0, "ros_wks": rw, "ros_pts": round(rp_pts, 1), "ros_ppg": round(rp_pts / rw, 1) if rw else 0.0, "ros_vorp": round(rv, 1), "po_ros_vorp": 0.0, "rostered_wks": rw, "dropped_fast": False, "flipped": None})
            return pout, round(sv_tot, 1), round(rv_tot, 1)
        ap, asv, arv = _side(a_its, ta)
        bp, bsv, brv = _side(b_its, tb)
        def _lb(av, bv):
            sw = round(av - bv, 1); ab = round(abs(sw), 1)
            w, l = ("PUSH", "PUSH") if ab <= TIE_MARGIN_VORP else ((oa, ob) if sw > 0 else (ob, oa))
            tags = ["Win-Win"] if (ab < 18 and min(av, bv) >= 18) else (["Lose-Lose"] if max(av, bv) < 6 else [])
            return {"a_vorp": av, "b_vorp": bv, "swing_a": sw, "abs_swing": ab, "winner": w, "loser": l, "win_playoff_vorp": 0.0, "tags": tags}
        tobj = {"trade_id": tid_s, "season": yr, "trade_week": twk, "effective_week": ewk, "owner_a": oa, "owner_b": ob, "a_owners": [oa], "b_owners": [ob], "a_outcome": {"is_complete": False}, "b_outcome": {"is_complete": False}, "a_games_swung": 0, "b_games_swung": 0, "a_received": ap, "b_received": bp, "started": _lb(asv, bsv), "ros": _lb(arv, brv)}
        (eval_live if yr == season else eval_all).append(tobj)

    def _owner_cards(tlist, lkey):
        st = {o: {"owner": o, "trades": 0, "wins": 0, "losses": 0, "pushes": 0, "net_vorp": 0.0} for o in owners}
        pc, pn, ot = defaultdict(Counter), defaultdict(lambda: defaultdict(float)), defaultdict(list)
        for tr in tlist:
            oa, ob, sw = tr["owner_a"], tr["owner_b"], float(tr[lkey]["swing_a"])
            for o, opp, ms in ((oa, ob, sw), (ob, oa, -sw)):
                if o not in st: continue
                st[o]["trades"] += 1; st[o]["net_vorp"] += ms
                if ms > TIE_MARGIN_VORP: st[o]["wins"] += 1
                elif ms < -TIE_MARGIN_VORP: st[o]["losses"] += 1
                else: st[o]["pushes"] += 1
                pc[o][opp] += 1; pn[o][opp] += ms; ot[o].append((ms, tr))
        out = []
        for o, d in st.items():
            dec = d["wins"] + d["losses"]
            tl = ot[o]
            bt = max(tl, key=lambda x: x[0]) if tl else None
            wt = min(tl, key=lambda x: x[0]) if tl else None
            if bt and wt and bt[1]["trade_id"] == wt[1]["trade_id"]:
                if bt[0] >= 0: wt = None
                else: bt = None
            def _br(pair):
                if not pair: return None
                s, t = pair
                return {"trade_id": t["trade_id"], "season": t["season"], "week": t["trade_week"], "partner": t["owner_b"] if t["owner_a"] == o else t["owner_a"], "swing": round(s, 1), "received": [p["name"] for p in (t["a_received"] if t["owner_a"] == o else t["b_received"])], "sent": [p["name"] for p in (t["b_received"] if t["owner_a"] == o else t["a_received"])]}
            fp = pc[o].most_common(1)[0] if pc[o] else None
            out.append({
                "owner": o, "trades": d["trades"], "wins": d["wins"], "losses": d["losses"], "pushes": d["pushes"],
                "win_pct": round((d["wins"] / dec) * 100.0, 1) if dec else 0.0,
                "net_vorp": round(d["net_vorp"], 1), "avg_swing": round(d["net_vorp"] / d["trades"], 1) if d["trades"] else 0.0,
                "best_trade": _br(bt), "worst_trade": _br(wt),
                "favorite_partner": {"owner": fp[0], "count": fp[1], "net_vorp": round(pn[o][fp[0]], 1)} if fp else None,
                "biggest_mark": None, "worst_nemesis": None,
            })
        out.sort(key=lambda x: (-x["net_vorp"], -x["trades"]))
        return out

    live_cards = {"started": _owner_cards(eval_live, "started"), "ros": _owner_cards(eval_live, "ros")}
    all_cards = {"started": _owner_cards(eval_all, "started"), "ros": _owner_cards(eval_all, "ros")}

    # Franchise Dossiers
    luck_map = {r["owner"]: r for r in luck_all}
    tc_map = {c["owner"]: c for c in (all_cards["started"] if eval_all else live_cards["started"])}
    dossiers = []
    for c in career:
        o = c["owner"]
        lk = luck_map.get(o, {})
        tc = tc_map.get(o, {})
        my_dp = [x for x in scored_dp if x["owner"] == o]
        bs = max((x for x in my_dp if ((x.get("bid", 0) <= 15) if x.get("draft_mode") == "AUCTION" else (x["round"] >= 2 if x.get("draft_mode") == "LINEAR" else x["round"] >= 4)) and x["delta"] > 0), key=lambda x: x["delta"], default=None)
        wb = min((x for x in my_dp if ((x.get("bid", 0) >= 20) if x.get("draft_mode") == "AUCTION" else (x["round"] == 1 if x.get("draft_mode") == "LINEAR" else x["round"] <= 5)) and x["delta"] < 0), key=lambda x: x["delta"], default=None)
        badges = []
        if (lk.get("pf_plus") or 100.0) >= 105.0: badges.append("Era Scorer")
        if (lk.get("luck_delta") or 0.0) >= 1.5: badges.append("Schedule Blessed")
        elif (lk.get("luck_delta") or 0.0) <= -1.5: badges.append("Schedule Cursed")
        if tc.get("net_vorp", 0.0) >= 35.0: badges.append("Trade Shark")
        if not badges: badges.append(f"{config['badge']} Contender")
        distinct_eras = len({info["summary"] for info in season_rules_by_yr.values()}) > 1
        ts_tl = [
            {
                "season": int(r[0]), "team_name": r[1], "division": r[2],
                "era_note": season_rules_by_yr.get(int(r[0]), {}).get("summary", "") if distinct_eras else "",
                "wins": int(r[3]), "losses": int(r[4]), "pf": round(float(r[5]), 1),
                "final_rank": int(r[6]), "made_playoffs": False, "won_division": False,
                "won_champ": False, "runner_up": False, "is_complete": bool(r[7]),
            }
            for r in cur.execute("SELECT season, team_name, division_name, wins, losses, points_for, final_rank, is_season_complete FROM team_seasons WHERE owner = ? ORDER BY season DESC", (o,)).fetchall()
        ]
        is_act = o in owner_tname
        if not is_act:
            badges.insert(0, "Alumni")
        dossiers.append({
            "owner": o, "code": c["code"], "division": c["division"], "team_name": c["team_name"] or (ts_tl[0]["team_name"] if ts_tl else o),
            "is_active": is_act, "former_names": resolver.former_names_for(o),
            "first_season": c["first_season"], "seasons_played": c["seasons"], "completed_seasons": sum(1 for s in ts_tl if s["is_complete"]),
            "rings": 0, "champ_years": [], "runner_up_years": [], "div_titles": 0, "po_apps": 0,
            "reg_w": c["wins"], "reg_l": c["losses"], "reg_pct": c["win_pct"], "po_w": 0, "po_l": 0, "po_pct": 0.0,
            "ap_pct": lk.get("ap_pct", 50.0), "luck_delta": lk.get("luck_delta", 0.0),
            "pf_plus": lk.get("pf_plus", 100.0), "pa_plus": lk.get("pa_plus", 100.0), "sos_rank": lk.get("sos_rank", 1),
            "high_game": {"score": c["high_score"], "season": season, "week": completed_week}, "low_game": None,
            "badges": badges, "nemesis": None, "victim": None,
            "draft": {"best_steal": bs, "worst_bust": wb},
            "trade": {"deals": tc.get("trades", 0), "wins": tc.get("wins", 0), "losses": tc.get("losses", 0), "pushes": tc.get("pushes", 0), "net_vorp": tc.get("net_vorp", 0.0), "best_trade": tc.get("best_trade"), "worst_trade": tc.get("worst_trade")},
            "timeline": ts_tl,
        })
    dossiers.sort(key=lambda d: (0 if d["is_active"] else 1, -d["rings"], -d["reg_pct"]))
    conn.close()

    teams_info = []
    for t in tmap.values():
        o = t["owner"]
        p = team_power[o]
        bo = baseline_odds[o]
        teams_info.append({
            "owner": o, "code": o.split()[0], "division": t["division"], "team_name": t["name"], "rings": 0,
            "power_mean": p["mean"], "power_std": p["std"], "raw_avg": p["raw_avg"],
            "baseline_playoff_pct": bo["playoff_pct"], "baseline_div_pct": bo["div_title_pct"],
            "baseline_bye_pct": bo["bye_pct"], "baseline_leverage": bo["leverage_swing"],
        })

    weekly_proj_export = {
        str(wk): {
            own: {
                "proj": info["optimal_proj"], "fa_fills": info["fa_fills"],
                "is_live": bool(info.get("is_live", False)),
                "live_pts": float(info.get("live_pts", 0.0)),
                "rem_frac": float(info.get("rem_frac", 1.0)),
            }
            for own, info in wk_map.items()
        }
        for wk, wk_map in weekly_proj.items()
    }

    tb_order = "Overall Record -> Total Points For (PF)" if config["tiebreaker_rule"] == "TOTAL_POINTS_SCORED" else "Overall Record -> Head-to-Head -> Total Points For (PF)"
    # Auto-check if this league has an isolated per-league Podcast RSS feed on GCS
    rss_url = f"https://storage.googleapis.com/bfl-podcast-7x9k2/{slug}/feed.xml"
    episodes = []
    try:
        from FantasyRecap.build_league_hub import fetch_rss_episodes
        episodes = fetch_rss_episodes(rss_url)
    except Exception:
        episodes = []

    hub_data = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "season": season,
        "completed_week": completed_week,
        "regular_season_weeks": reg_wks,
        "league_config": {
            "league_id": str(league_id),
            "slug": slug,
            "name": config["name"],
            "badge": config["badge"],
            "first_season": config["first_season"],
            "scoring_label": config["scoring_label"],
            "format_label": config.get("format_label", "Redraft"),
            "draft_type": config.get("draft_type", "SNAKE"),
            "has_divisions": config["has_divisions"],
            "is_first_year": config["is_first_year"],
        },
        "divisions": config["divisions"],
        "tiebreaker_rules": {
            "playoff_teams": config["playoff_teams"],
            "playoff_byes": config["playoff_byes"],
            "playoff_reseed": config["playoff_reseed"],
            "playoff_round_weeks": config.get("playoff_round_weeks", 1),
            "uses_league_median": config.get("uses_league_median", False),
            "division_champions": len(config["divisions"]) if config["has_divisions"] else 0,
            "wild_cards": config["playoff_teams"] - (len(config["divisions"]) if config["has_divisions"] else 0),
            "first_round_bye": f"Seeds #1–#{config['playoff_byes']}" if config["playoff_byes"] > 1 else ("Seed #1" if config["playoff_byes"] == 1 else "None"),
            "tiebreaker_rule": config["tiebreaker_rule"],
            "order": tb_order,
            "reseed": "NFL Dynamic Re-Seeding" if config["playoff_reseed"] else "Fixed Bracket (No Re-Seeding)",
        },
        "teams": teams_info,
        "completed_games": completed_games,
        "remaining_schedule": [g for g in full_schedule if int(g["week"]) > completed_week],
        "weekly_projections": weekly_proj_export,
        "awards": awards_data,
        "history": {
            "career": career,
            "h2h": h2h_summary,
            "championships": [],
            "drafts": drafts,
            "draft_seasons": draft_seasons,
            "season_rules": {str(yr): {k: v for k, v in info.items() if k != "repl_ranks"} for yr, info in sorted(season_rules_by_yr.items())},
            "identity_review_candidates": resolver.review_candidates(),
        },
        "trades": {
            "all_time": {"seasons": [], "trade_count": len(eval_all), "trades": eval_all, "owner_cards": all_cards},
            "live": {"season": season, "trade_count": len(eval_live), "trades": eval_live, "owner_cards": live_cards},
        },
        "analytics": {
            "luck": {"seasons": [season], "by_season": {str(season): luck_2026, "all": luck_all}},
            "trends": {"season": season, "weeks": weeks_list, "series": series},
            "heatmap": {"season": season, "positions": list(active_pos), "baselines": baselines, "bounds": hm_bounds, "rows": hm_rows},
        },
        "dossiers": dossiers,
        "podcast": {"rss_url": rss_url if episodes else "", "episodes": episodes},
    }

    out_dir = WEB_HUB_DIR / "leagues" / slug
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "hub_data.json"
    out_path.write_text(json.dumps(hub_data, indent=2), encoding="utf-8")
    print(f"✅ Wrote {config['name']} hub data ({out_path.stat().st_size / 1024:.1f} KB): {out_path}")
    return hub_data, out_path


def render_league_index_html(hub_data: dict, out_dir: Path) -> Path:
    """
    Renders a league-branded `index.html` from the shared template so link-preview
    scrapers (iMessage, Discord, Slack — which do not run JS) show the exact league
    name, badge, and OpenGraph metadata instead of 'BFL League Hub'.
    """
    import html as _html

    cfg = hub_data.get("league_config", {})
    tb = hub_data.get("tiebreaker_rules", {})
    name = _html.escape(cfg.get("name", "Fantasy League"))
    badge = _html.escape(cfg.get("badge", "FFL"))
    n_teams = len(hub_data.get("teams", []))
    scoring = _html.escape(cfg.get("scoring_label", ""))
    po_teams = tb.get("playoff_teams", 6)
    title_txt = f"{name} Hub — {badge} Fantasy Football"
    desc_txt = (
        f"{n_teams}-Team{(' ' + scoring) if scoring else ''} Fantasy Hub: "
        f"Live {po_teams}-Team Playoff Machine, Schedule Luck, Positional VORP Heatmap, Draft Hall &amp; Trade Vault."
    )

    tpl = (WEB_HUB_DIR / "index.html").read_text(encoding="utf-8")
    tpl = re.sub(r"<title>.*?</title>", f"<title>{title_txt}</title>", tpl, count=1)
    tpl = re.sub(r'property="og:title" content=".*?"', f'property="og:title" content="{title_txt}"', tpl, count=1)
    tpl = re.sub(r'property="og:description" content=".*?"', f'property="og:description" content="{desc_txt}"', tpl, count=1)
    tpl = tpl.replace('<span class="brand-badge">BFL</span>', f'<span class="brand-badge">{badge}</span>', 1)
    tpl = tpl.replace("<h1>Beasts Football League Hub</h1>", f"<h1>{name} Hub</h1>", 1)

    idx_path = out_dir / "index.html"
    idx_path.write_text(tpl, encoding="utf-8")
    return idx_path


def deploy_multi_league_to_gcs(
    slug: str,
    data_path: Path,
    bucket_name: str,
    project_id: str = "bfl-fantasy",
    hub_data: Optional[dict] = None,
) -> str:
    """Uploads league-branded `index.html`, shared CSS/JS, and `hub_data.json` to `gs://<bucket>/<slug>/`."""
    slug = validate_slug(slug)
    if hub_data is None:
        hub_data = json.loads(data_path.read_text(encoding="utf-8"))
    idx_path = render_league_index_html(hub_data, data_path.parent)

    uploads = [
        (idx_path, "index.html", "text/html; charset=utf-8", "no-cache, max-age=60"),
        (WEB_HUB_DIR / "styles.css", "styles.css", "text/css; charset=utf-8", "public, max-age=300"),
        (WEB_HUB_DIR / "app.js", "app.js", "application/javascript; charset=utf-8", "no-cache, max-age=60"),
        (data_path, "hub_data.json", "application/json; charset=utf-8", "no-cache, max-age=60"),
    ]
    for src, dest_name, ctype, cctl in uploads:
        blob_path = f"{slug}/{dest_name}"
        uploaded = False
        try:
            from google.cloud import storage as gcs_storage  # type: ignore
            client = gcs_storage.Client(project=project_id)
            blob = client.bucket(bucket_name).blob(blob_path)
            blob.cache_control = cctl
            blob.upload_from_filename(str(src), content_type=ctype)
            uploaded = True
        except Exception:
            uploaded = False
        if not uploaded:
            subprocess.run(
                [
                    "gcloud", "storage", "cp", str(src), f"gs://{bucket_name}/{blob_path}",
                    f"--project={project_id}",
                    f"--content-type={ctype}",
                    f"--cache-control={cctl}",
                ],
                check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
    url = f"https://storage.googleapis.com/{bucket_name}/{slug}/index.html"
    print(f"🚀 Deployed {slug} League Hub live to GCP Free Tier: {url}")
    return url


def main():
    parser = argparse.ArgumentParser(description="Build & deploy ANY ESPN League Hub (League-in-a-Box)")
    parser.add_argument("--league-id", type=str, required=True, help="ESPN League ID (e.g. 755112359)")
    parser.add_argument("--slug", type=str, required=True, help="Short URL slug (e.g. jff)")
    parser.add_argument("--season", type=int, default=int(CURRENT_YEAR))
    parser.add_argument("--week", type=int, default=0, help="Override completed week (0 = auto-detect)")
    parser.add_argument("--deploy-gcs", type=str, default="", help="GCS bucket name (e.g. bfl-hub-7x9k2)")
    parser.add_argument("--gcp-project", type=str, default="bfl-fantasy")
    args = parser.parse_args()

    hub_data, data_path = build_generic_hub_data(args.league_id, args.slug, args.season, force_week=args.week)
    if args.deploy_gcs:
        deploy_multi_league_to_gcs(args.slug, data_path, args.deploy_gcs, project_id=args.gcp_project, hub_data=hub_data)


if __name__ == "__main__":
    main()
