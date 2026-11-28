#!/usr/bin/env python3
"""
BFL Live Game Desk Daemon
=========================
Continuously monitors live ESPN fantasy matchups during NFL game windows:
1. Polls ESPN boxscores every 60-120 seconds.
2. Calculates real-time Gaussian win probabilities with game-clock decay.
3. Detects Win Probability Shockwaves (>= 25% swing), 4th Quarter Lead Changes,
   and Nuclear Player Explosions (>= 30.0 pts).
4. Broadcasts formatted alert cards directly to the #live-game-desk Discord channel.
5. Built-in cooldowns & deduplication to prevent chat spam.
"""

import os
import sys
import time
import json
import argparse
import requests
from datetime import datetime, timezone
from pathlib import Path
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from FantasyRecap.core.espn_client import (
    ESPNClientError,
    fetch_nfl_scoreboard,
    fetch_pro_team_schedules,
)
from FantasyRecap.core.ops import notify_ops
from FantasyRecap.league_recap_generator import fetch_espn_week_data, parse_league_members_and_teams, ESPN_LEAGUE_ID, ESPN_S2, ESPN_SWID
from FantasyRecap.live_win_probability_monitor import calculate_matchup_win_probability, detect_probability_shockwaves
from FantasyRecap.config import CURRENT_YEAR as CONFIG_YEAR

from FantasyRecap.core.manifest import load_project_env
load_project_env()

DISCORD_WEBHOOK_LIVE_DESK = (
    os.getenv("DISCORD_WEBHOOK_LIVE")
    or os.getenv("DISCORD_WEBHOOK_LIVE_DESK")
    or os.getenv("DISCORD_WEBHOOK_COMMISH")
    or os.getenv("DISCORD_WEBHOOK_PODCAST")
    or os.getenv("DISCORD_WEBHOOK_URL")
)

import hashlib

STATE_DIR = Path(__file__).resolve().parent / "live_state"
STATE_DIR.mkdir(exist_ok=True)
POS_ABBR = {1: 'QB', 2: 'RB', 3: 'WR', 4: 'TE', 5: 'K', 16: 'D/ST'}

# Position-specific Half-PPR Nuclear Eruption thresholds (strictly league-breaking!)
# Must clear BOTH the raw score floor AND the minimum points-over-projection floor
# so routine 17-pt kickers or modest 22-pt outings never trigger a "Nuclear" siren.
NUCLEAR_THRESHOLDS = {
    1: (34.0, 12.0),   # QB: 34+ pts and +12 over proj
    2: (28.0, 10.0),   # RB: 28+ pts and +10 over proj
    3: (28.0, 10.0),   # WR: 28+ pts and +10 over proj
    4: (24.0, 10.0),   # TE: 24+ pts and +10 over proj
    5: (20.0, 10.0),   # K:  20+ pts and +10 over proj (true 6-FG monster day)
    16: (22.0, 12.0),  # D/ST: 22+ pts and +12 over proj (multi-TD defensive clinic)
}


def _pick_live_copy(seed_key: str, variants: list) -> str:
    """Deterministically picks a varied broadcast line per event so alerts never feel robotic."""
    if not variants:
        return ""
    idx = int(hashlib.md5(seed_key.encode("utf-8"), usedforsecurity=False).hexdigest()[:8], 16) % len(variants)
    return variants[idx]


def format_catalyst_phrase(pname: str, pos: str, owner: str, delta: float, curr_pts: float) -> str:
    """Calibrates play-by-play phrasing strictly to the actual point jump (never calls +2 pts an 'eruption'!)."""
    seed = f"cat:{pname}:{int(curr_pts * 10)}:{int(delta * 10)}"
    if delta >= 13.0:
        verb = _pick_live_copy(seed, [
            "detonated for",
            "blew the game open with",
            "torched the scoreboard for",
            "unleashed a massive",
        ])
        return f"**{pname}** ({pos}, {owner}) {verb} **+{delta:.1f} pts** (now at `{curr_pts:.1f}`)"
    if delta >= 8.0:
        verb = _pick_live_copy(seed, [
            "ripped off a huge",
            "flipped momentum with",
            "delivered a big-time",
            "surged for",
        ])
        return f"**{pname}** ({pos}, {owner}) {verb} **+{delta:.1f} pts** (now at `{curr_pts:.1f}`)"
    if delta >= 4.0:
        verb = _pick_live_copy(seed, [
            "answered with",
            "came through with",
            "tacked on",
            "struck for",
        ])
        return f"**{pname}** ({pos}, {owner}) {verb} **+{delta:.1f} pts** (now at `{curr_pts:.1f}`)"
    verb = _pick_live_copy(seed, [
        "nudged the scoreboard with",
        "chipped in",
        "pushed ahead with",
        "added",
    ])
    return f"**{pname}** ({pos}, {owner}) {verb} **+{delta:.1f} pts** (now at `{curr_pts:.1f}`)"


def is_nuclear_performance(pos_id: int, curr_pts: float, baseline_proj: float) -> bool:
    """Returns True only when a player clears BOTH the position's raw score floor and over-projection floor."""
    min_pts, min_over_proj = NUCLEAR_THRESHOLDS.get(pos_id, (28.0, 10.0))
    over_proj = curr_pts - float(baseline_proj or 0.0)
    return curr_pts >= min_pts and over_proj >= min_over_proj

def log_live_desk_event(season: int, week_num: int, event_type: str, status: str, match_id: str, details: dict):
    """Appends structured telemetry for every fired or suppressed alert to an audit JSONL file."""
    log_file = STATE_DIR / f"alerts_log_{season}_week_{week_num}.jsonl"
    entry = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "epoch": round(time.time(), 2),
        "event_type": event_type,   # SHOCKWAVE, LEAD_CHANGE, NUCLEAR, INJURY, MNF_BOARD
        "status": status,           # FIRED or SUPPRESSED
        "match_id": str(match_id),
        **details
    }
    try:
        with open(log_file, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception as e:
        print(f"⚠️ Failed to write audit log: {e}")

def post_live_desk_embed(title: str, description: str, color: int, fields: list, footer: str = "BFL Live Game Desk • Beasts Football League"):
    """Sends a rich live alert embed to Discord #live-game-desk."""
    if not DISCORD_WEBHOOK_LIVE_DESK:
        print("⚠️ No Discord Webhook configured for Live Game Desk.")
        return False

    payload = {
        "username": "BFL Live Game Desk",
        "avatar_url": "https://a.espncdn.com/combiner/i?img=/i/headshots/nfl/players/full/3918298.png",
        "embeds": [{
            "title": title,
            "description": description,
            "color": color,
            "fields": fields,
            "footer": {"text": footer},
            "timestamp": datetime.now(timezone.utc).isoformat()
        }]
    }

    try:
        resp = requests.post(DISCORD_WEBHOOK_LIVE_DESK, json=payload, timeout=8)
        return resp.status_code in [200, 204]
    except Exception as e:
        print(f"❌ Error sending Live Desk alert: {e}")
        return False

def fetch_live_nfl_game_clocks(season: int, week_num: int) -> dict:
    """
    Maps each ESPN NFL proTeamId to its real-time game status ('PRE', 'IN_PROGRESS', 'FINAL')
    and exact fraction of regulation game clock remaining (pct_remaining in [0.0, 1.0]).
    """
    event_clocks = {}
    try:
        sb_json = fetch_nfl_scoreboard(season=season, week=week_num, season_type=2, timeout=8.0)
        for ev in sb_json.get("events", []):
            ev_id = str(ev.get("id", ""))
            st = ev.get("status", {})
            st_type = st.get("type", {})
            state_str = st_type.get("state", "pre")
            name_str = st_type.get("name", "STATUS_SCHEDULED")
            if state_str == "post" or name_str == "STATUS_FINAL":
                event_clocks[ev_id] = ("FINAL", 0.0)
            elif state_str == "in" or "IN_PROGRESS" in name_str or "HALFTIME" in name_str or "END_PERIOD" in name_str:
                period = int(st.get("period", 1))
                clock = float(st.get("clock", 0.0))
                if period >= 5:
                    pct_rem = 0.05
                else:
                    rem_secs = max(0.0, (4 - period) * 900.0 + clock)
                    pct_rem = round(min(1.0, rem_secs / 3600.0), 4)
                event_clocks[ev_id] = ("IN_PROGRESS", pct_rem)
            else:
                event_clocks[ev_id] = ("PRE", 1.0)
    except Exception as exc:
        print(f"⚠️ Could not fetch live NFL scoreboard clocks: {exc}")

    pro_team_clocks = {}
    try:
        pt_json = fetch_pro_team_schedules(season=season, timeout=8.0)
        now_ms = time.time() * 1000
        for pt in pt_json.get("settings", {}).get("proTeams", []):
            ptid = pt.get("id")
            sched = pt.get("proGamesByScoringPeriod", {}).get(str(week_num), [])
            if not sched:
                # Bye week team
                pro_team_clocks[ptid] = ("FINAL", 0.0)
                continue
            g = sched[0]
            gid = str(g.get("id", ""))
            if g.get("statsOfficial") is True:
                pro_team_clocks[ptid] = ("FINAL", 0.0)
            elif gid in event_clocks:
                pro_team_clocks[ptid] = event_clocks[gid]
            else:
                g_date = g.get("date", 0)
                if g_date > 0 and now_ms > g_date + (4 * 3600 * 1000):
                    pro_team_clocks[ptid] = ("FINAL", 0.0)
                elif g_date > 0 and now_ms >= g_date:
                    elapsed_ratio = min(0.95, max(0.05, (now_ms - g_date) / (3.25 * 3600 * 1000)))
                    pro_team_clocks[ptid] = ("IN_PROGRESS", round(1.0 - elapsed_ratio, 4))
                else:
                    pro_team_clocks[ptid] = ("PRE", 1.0)
    except Exception as exc:
        print(f"⚠️ Could not fetch proTeamSchedules_wl clocks: {exc}")

    return pro_team_clocks


def parse_live_matchup_state(raw_data, week_num: int, season: int = int(CONFIG_YEAR)) -> dict:
    """Parses live boxscores and calculates current win probabilities for all active matchups."""
    teams_dict = parse_league_members_and_teams(raw_data)
    pro_team_clocks = fetch_live_nfl_game_clocks(season, week_num)
    state = {}

    for m in raw_data.get('schedule', []):
        if m.get('matchupPeriodId') != week_num:
            continue

        mid = str(m.get('id', f"{m.get('away', {}).get('teamId')}_{m.get('home', {}).get('teamId')}"))
        away_data = m.get('away', {})
        home_data = m.get('home', {})

        if not away_data or not home_data:
            continue

        a_tid = away_data.get('teamId')
        h_tid = home_data.get('teamId')

        a_info = teams_dict.get(a_tid, {'name': f'Team {a_tid}', 'owner': 'Manager'})
        h_info = teams_dict.get(h_tid, {'name': f'Team {h_tid}', 'owner': 'Manager'})

        # Parse starters & live projections
        def extract_starters(roster_data):
            starters = []
            entries = roster_data.get('rosterForCurrentScoringPeriod', {}).get('entries', [])
            for e in entries:
                slot = e.get('lineupSlotId', 20)
                if slot in [20, 21]: # Bench / IR
                    continue
                p = e.get('playerPoolEntry', {}).get('player', {})
                score = float(e.get('playerPoolEntry', {}).get('appliedStatTotal', 0.0))
                pos_id = p.get('defaultPositionId', 3)
                pro_tid = p.get('proTeamId')
                
                # Determine real-time NFL game status & clock remaining
                if pro_tid in pro_team_clocks:
                    status, pct_rem = pro_team_clocks[pro_tid]
                else:
                    status = 'PRE'
                    pct_rem = 1.0

                base_proj = 12.0
                for stat in p.get('stats', []):
                    if stat.get('statSourceId') == 1 and stat.get('scoringPeriodId') == week_num:
                        base_proj = float(stat.get('appliedTotal', 12.0))
                    if stat.get('statSourceId') == 0 and 'percentComplete' in stat and pro_tid not in pro_team_clocks:
                        pct_rem = 1.0 - (float(stat.get('percentComplete', 0)) / 100.0)
                        if pct_rem <= 0.0:
                            status = 'FINAL'
                        elif pct_rem < 1.0:
                            status = 'IN_PROGRESS'

                starters.append({
                    'id': p.get('id', 0),
                    'name': p.get('fullName', 'Unknown'),
                    'pos_id': pos_id,
                    'pos': POS_ABBR.get(pos_id, 'FLEX'),
                    'injury_status': p.get('injuryStatus', 'ACTIVE'),
                    'baseline_proj': round(base_proj, 2),
                    'current_score': round(score, 2),
                    'game_status': status,
                    'pct_remaining': pct_rem
                })
            return starters

        a_starters = extract_starters(away_data)
        h_starters = extract_starters(home_data)

        # During live action ESPN keeps totalPoints=0.0 and populates totalPointsLive / starter appliedStatTotal
        a_score = round(max(
            float(away_data.get('totalPoints', 0.0) or 0.0),
            float(away_data.get('totalPointsLive', 0.0) or 0.0),
            sum(s['current_score'] for s in a_starters)
        ), 2)
        h_score = round(max(
            float(home_data.get('totalPoints', 0.0) or 0.0),
            float(home_data.get('totalPointsLive', 0.0) or 0.0),
            sum(s['current_score'] for s in h_starters)
        ), 2)

        a_prob, h_prob, a_exp, h_exp = calculate_matchup_win_probability(a_starters, h_starters, a_score, h_score)

        state[mid] = {
            'schema_version': 2,
            'match_id': mid,
            'away_id': a_tid,
            'home_id': h_tid,
            'away_team': a_info['name'],
            'away_owner': a_info['owner'],
            'home_team': h_info['name'],
            'home_owner': h_info['owner'],
            'away_score': a_score,
            'home_score': h_score,
            'away_prob': a_prob,
            'home_prob': h_prob,
            'away_exp': a_exp,
            'home_exp': h_exp,
            'away_starters': a_starters,
            'home_starters': h_starters,
            'leader': a_info['name'] if a_score > h_score else (h_info['name'] if h_score > a_score else 'TIED'),
            'updated_at': time.time()
        }

    return state

def _dst_kickoff_cushion_jump(prev_match: dict, curr_match: dict) -> dict:
    """
    Computes how many points on each side came strictly from a D/ST transitioning from
    PRE (0.0 pts) to IN_PROGRESS (+10.0 starting bonus), so kickoff never fakes a score jump.
    """
    prev_map = {
        s['name']: s
        for s in prev_match.get('away_starters', []) + prev_match.get('home_starters', [])
    }
    cushion = {'away': 0.0, 'home': 0.0}
    for key, starters_key in [('away', 'away_starters'), ('home', 'home_starters')]:
        for s in curr_match.get(starters_key, []):
            if s.get('pos_id') == 16 or s.get('pos') == 'D/ST':
                ps = prev_map.get(s['name'], {})
                if ps.get('game_status', 'PRE') == 'PRE' and float(ps.get('current_score', 0.0)) == 0.0:
                    curr_pts = float(s.get('current_score', 0.0))
                    if s.get('game_status') == 'IN_PROGRESS' and curr_pts > 0.0:
                        cushion[key] += min(10.0, curr_pts)
    return cushion


def _find_swing_catalyst_player(prev_match: dict, curr_match: dict, min_delta: float = 2.5, target_side: str = None) -> tuple:
    """
    Identifies the exact active player whose scoring jump catalyzed a probability shockwave or lead change.
    Returns (catalyst_str, best_delta). Ignores players whose games were already FINAL in prev_match,
    and excludes a D/ST's automatic +10.0 kickoff starting bonus when transitioning from PRE -> IN_PROGRESS.
    """
    prev_scores = {}
    prev_status = {}
    for s in prev_match.get('away_starters', []) + prev_match.get('home_starters', []):
        prev_scores[s['name']] = float(s.get('current_score', 0.0))
        prev_status[s['name']] = s.get('game_status', 'PRE')

    best_delta = 0.0
    catalyst_str = ""
    sides = [('away_starters', 'away_owner'), ('home_starters', 'home_owner')]
    if target_side == 'AWAY':
        sides = [('away_starters', 'away_owner')]
    elif target_side == 'HOME':
        sides = [('home_starters', 'home_owner')]

    for side_key, owner_key in sides:
        owner = curr_match.get(owner_key, 'Manager')
        for s in curr_match.get(side_key, []):
            pname = s['name']
            # Do not attribute live swings to players whose games were already FINAL in the baseline snapshot
            if prev_status.get(pname) == 'FINAL' and s.get('game_status') == 'FINAL':
                continue
            curr_pts = float(s.get('current_score', 0.0))
            prev_pts = prev_scores.get(pname, curr_pts)
            is_dst = (s.get('pos_id') == 16 or s.get('pos') == 'D/ST')
            if is_dst and prev_status.get(pname) == 'PRE' and prev_pts == 0.0 and s.get('game_status') == 'IN_PROGRESS' and curr_pts > 0.0:
                # Discount the automatic +10.0 kickoff bonus; only attribute actual net positive points above 10
                prev_pts = min(10.0, curr_pts)
            delta = round(curr_pts - prev_pts, 2)
            if delta > best_delta and delta >= min_delta:
                best_delta = delta
                catalyst_str = format_catalyst_phrase(pname, s.get('pos', 'FLEX'), owner, delta, curr_pts)
    return catalyst_str, best_delta

def run_live_desk_tick(season: int, week_num: int, shockwave_threshold: float = 0.25):
    """Executes a single monitoring cycle, checks for swings, nuclear games, injuries, and posts alerts."""
    state_file = STATE_DIR / f"state_{season}_week_{week_num}.json"
    prev_state = {}
    if state_file.exists():
        try:
            with open(state_file, 'r') as f:
                prev_state = json.load(f)
        except Exception:
            prev_state = {}

    raw_data = fetch_espn_week_data(ESPN_LEAGUE_ID, str(season), week_num, ESPN_S2, ESPN_SWID)
    curr_state = parse_live_matchup_state(raw_data, week_num, season=season)

    # Check if prev_state is from the pre-fix schema (where away_score/home_score were 0.0)
    prev_is_valid_v2 = bool(prev_state) and all(
        m.get('schema_version', 0) >= 2 for m in prev_state.values() if isinstance(m, dict)
    )

    if prev_state and prev_is_valid_v2:
        # Preserve alert cooldowns, rolling 5-minute history window, & deduplication sets from previous state
        baseline_for_shockwaves = {}
        window_baseline_map = {}

        for mid, c_data in curr_state.items():
            if mid in prev_state:
                p_m = prev_state[mid]
                c_data['last_alert_time'] = p_m.get('last_alert_time', 0)
                c_data['last_lead_alert_time'] = p_m.get('last_lead_alert_time', 0)
                c_data['nuclear_alerted'] = p_m.get('nuclear_alerted', [])
                c_data['injury_alerted'] = p_m.get('injury_alerted', [])

                # Maintain a rolling 15-tick (~15-minute full NFL drive) history buffer
                # so multi-play drives (like Emanuel Wilson's 4Q comeback) are caught cleanly
                # without false-triggering on 3-hour scoreless clock decay!
                rolling_hist = list(p_m.get('rolling_history', []))
                rolling_hist.append({
                    'epoch': p_m.get('updated_at', time.time()),
                    'away_prob': p_m.get('away_prob', c_data['away_prob']),
                    'away_score': p_m.get('away_score', c_data['away_score']),
                    'home_score': p_m.get('home_score', c_data['home_score']),
                    'away_starters': p_m.get('away_starters', []),
                    'home_starters': p_m.get('home_starters', []),
                })
                if len(rolling_hist) > 15:
                    rolling_hist = rolling_hist[-15:]
                c_data['rolling_history'] = rolling_hist

                # Find the snapshot in the rolling 15-minute drive window with the largest probability delta
                best_snap = rolling_hist[-1]
                max_prob_delta = 0.0
                for snap in rolling_hist:
                    d = abs(c_data['away_prob'] - snap.get('away_prob', c_data['away_prob']))
                    if d >= max_prob_delta:
                        max_prob_delta = d
                        best_snap = snap

                window_baseline_map[mid] = best_snap
                baseline_for_shockwaves[mid] = {
                    **p_m,
                    'away_prob': best_snap['away_prob'],
                    'home_prob': round(1.0 - best_snap['away_prob'], 4),
                    'away_starters': best_snap['away_starters'],
                    'home_starters': best_snap['home_starters'],
                }

        # Track matchups that fired a Shockwave on this tick so we don't double-post a separate Lead Change card
        shockwave_fired_mids = set()

        # 1. Check for Win Probability Shockwaves (>= 25% swings within the 15-minute drive window)
        shockwaves = detect_probability_shockwaves(baseline_for_shockwaves, curr_state, threshold=shockwave_threshold)
        for alert in shockwaves:
            mid = alert['match_id']
            c_m = curr_state[mid]
            p_m = prev_state.get(mid, {})
            snap_m = window_baseline_map.get(mid, p_m)

            last_alert_time = c_m.get('last_alert_time', 0)
            if time.time() - last_alert_time < 900:
                log_live_desk_event(season, week_num, "SHOCKWAVE", "SUPPRESSED_COOLDOWN", mid, {
                    "matchup": f"{alert['away_team']} @ {alert['home_team']}",
                    "swing_pct": alert['swing_pct']
                })
                continue

            # Prefer the single-tick catalyst if a big play just happened on this exact tick (e.g. Emanuel Wilson TD);
            # otherwise fall back to the 15-minute drive snapshot catalyst
            target_side = alert.get('direction')
            catalyst, best_delta = _find_swing_catalyst_player(p_m, c_m, min_delta=2.5, target_side=target_side)
            if not catalyst:
                catalyst, best_delta = _find_swing_catalyst_player(snap_m, c_m, min_delta=2.5, target_side=target_side)
            if not catalyst:
                catalyst, best_delta = _find_swing_catalyst_player(snap_m, c_m, min_delta=2.5)

            dst_cushion = _dst_kickoff_cushion_jump(snap_m, c_m)
            team_score_jump = max(
                round(c_m['away_score'] - float(snap_m.get('away_score', c_m['away_score'])) - dst_cushion['away'], 2),
                round(c_m['home_score'] - float(snap_m.get('home_score', c_m['home_score'])) - dst_cushion['home'], 2)
            )
            if best_delta < 2.5 and team_score_jump < 4.0:
                log_live_desk_event(season, week_num, "SHOCKWAVE", "SUPPRESSED_CLOCK_DRIFT", mid, {
                    "matchup": f"{alert['away_team']} @ {alert['home_team']}",
                    "swing_pct": alert['swing_pct'],
                    "team_score_jump": team_score_jump
                })
                continue

            # Check if this play also flipped the lead on this exact tick
            p_leader = p_m.get('leader')
            c_leader = c_m.get('leader')
            lead_flipped = bool(p_leader and c_leader and p_leader != 'TIED' and c_leader != 'TIED' and p_leader != c_leader)
            margin_now = abs(alert['away_score'] - alert['home_score'])
            sw_seed = f"sw:{mid}:{int(alert['swing_pct'])}:{int(c_m['away_score'] + c_m['home_score'])}"

            if lead_flipped:
                title_prefix = _pick_live_copy(sw_seed + ":t", [
                    "LEAD FLIP & PROBABILITY SWING",
                    "MOMENTUM REVERSAL",
                    "MASSIVE SWING & NEW LEADER",
                ])
                lead_desc = _pick_live_copy(sw_seed + ":d", [
                    f"**{c_leader}** just snatched the lead from **{p_leader}** (`+{alert['swing_pct']:.1f}%` win-prob swing).",
                    f"Everything just flipped—**{c_leader}** jumps ahead of **{p_leader}** on a `{alert['swing_pct']:.1f}%` odds swing.",
                    f"Seismic shift on the scoreboard: **{c_leader}** overtakes **{p_leader}** behind a `{alert['swing_pct']:.1f}%` surge.",
                ])
            elif alert['swing_pct'] >= 35.0:
                title_prefix = _pick_live_copy(sw_seed + ":t", [
                    "MONSTER ODDS SWING",
                    "GAME-CHANGING SURGE",
                    "SHOCKWAVE ON THE DESK",
                ])
                lead_desc = _pick_live_copy(sw_seed + ":d", [
                    f"**{alert['favored']}** just swung win probability by **`{alert['swing_pct']:.1f}%`** in a hurry.",
                    f"The win-probability chart just went vertical—a **`{alert['swing_pct']:.1f}%`** swing toward **{alert['favored']}**.",
                    f"Massive momentum swing (`{alert['swing_pct']:.1f}%`) tilting this matchup toward **{alert['favored']}**.",
                ])
            else:
                title_prefix = _pick_live_copy(sw_seed + ":t", [
                    "WIN PROBABILITY SWING",
                    "MOMENTUM SHIFT",
                    "LIVE DESK SWING",
                ])
                tight_note = f" Margin is down to `{margin_now:.2f}` pts." if margin_now <= 5.0 else ""
                lead_desc = _pick_live_copy(sw_seed + ":d", [
                    f"**{alert['favored']}** picks up a **`{alert['swing_pct']:.1f}%`** win-probability jump.{tight_note}",
                    f"The odds board just moved **`{alert['swing_pct']:.1f}%`** in favor of **{alert['favored']}**.{tight_note}",
                    f"Big swing in real time: **{alert['favored']}** climbs `{alert['swing_pct']:.1f}%` on the live model.{tight_note}",
                ])

            print(f"🚨 SHOCKWAVE DETECTED: {alert['favored']} (+{alert['swing_pct']}% swing)!")
            fields = [
                {
                    "name": "Scoreboard",
                    "value": (
                        f"**{alert['away_team']}** ({c_m['away_owner']}): `{alert['away_score']:.2f}`  •  "
                        f"`{alert['prev_away_prob']*100:.1f}% → {alert['curr_away_prob']*100:.1f}%`\n"
                        f"**{alert['home_team']}** ({c_m['home_owner']}): `{alert['home_score']:.2f}`  •  "
                        f"`{alert['prev_home_prob']*100:.1f}% → {alert['curr_home_prob']*100:.1f}%`"
                    ),
                    "inline": False
                }
            ]
            if catalyst:
                fields.append({"name": "Key Play", "value": catalyst, "inline": False})

            post_live_desk_embed(
                title=f"{title_prefix} — {alert['away_team']} at {alert['home_team']}",
                description=lead_desc,
                color=0xe74c3c if alert['swing_pct'] >= 35.0 or lead_flipped else 0xd35400,
                fields=fields
            )
            now_ts = time.time()
            c_m['last_alert_time'] = now_ts
            if lead_flipped:
                c_m['last_lead_alert_time'] = now_ts
                shockwave_fired_mids.add(mid)
            # Reset rolling history after a shockwave fires so the same drive isn't re-counted for a second shockwave,
            # while still allowing a subsequent Lead Change on a later tick (like Emanuel Wilson's TD -> Lead Change!)
            c_m['rolling_history'] = [{
                'epoch': now_ts,
                'away_prob': c_m['away_prob'],
                'away_score': c_m['away_score'],
                'home_score': c_m['home_score'],
                'away_starters': c_m['away_starters'],
                'home_starters': c_m['home_starters'],
            }]
            log_live_desk_event(season, week_num, "SHOCKWAVE", "FIRED", mid, {
                "matchup": f"{alert['away_team']} @ {alert['home_team']}",
                "swing_pct": alert['swing_pct'],
                "away_score": c_m['away_score'],
                "home_score": c_m['home_score'],
                "away_prob": c_m['away_prob'],
                "home_prob": c_m['home_prob'],
                "lead_flipped": lead_flipped,
                "catalyst": catalyst
            })

        # 2. Check for Lead Changes, Position-Specific Nuclear Eruptions, and In-Game Injuries
        for mid, c_data in curr_state.items():
            if mid not in prev_state:
                continue
            p_data = prev_state[mid]
            p_leader = p_data.get('leader')
            c_leader = c_data.get('leader')

            if (
                mid not in shockwave_fired_mids
                and p_leader and c_leader
                and p_leader != 'TIED' and c_leader != 'TIED'
                and p_leader != c_leader
            ):
                margin = round(abs(c_data['away_score'] - c_data['home_score']), 2)
                last_lead_ts = c_data.get('last_lead_alert_time', 0)
                leader_side = 'AWAY' if c_leader == c_data['away_team'] else 'HOME'

                # Find the player on the overtaking team who pushed them into the lead on this tick
                # (or across the current drive if it was a small yardage push after a big TD)
                catalyst, best_delta = _find_swing_catalyst_player(p_data, c_data, min_delta=0.4, target_side=leader_side)
                if not catalyst and mid in window_baseline_map:
                    catalyst, best_delta = _find_swing_catalyst_player(window_baseline_map[mid], c_data, min_delta=1.5, target_side=leader_side)
                if not catalyst:
                    catalyst, best_delta = _find_swing_catalyst_player(p_data, c_data, min_delta=0.5)

                # Suppress fake lead changes caused solely by a D/ST receiving its +10.0 kickoff bonus
                tick_cushion = _dst_kickoff_cushion_jump(p_data, c_data)
                side_cushion = tick_cushion['away'] if leader_side == 'AWAY' else tick_cushion['home']
                if side_cushion > 0 and best_delta < 0.4:
                    log_live_desk_event(season, week_num, "LEAD_CHANGE", "SUPPRESSED_DST_KICKOFF", mid, {
                        "matchup": f"{c_data['away_team']} vs {c_data['home_team']}",
                        "new_leader": c_leader,
                        "margin": margin
                    })
                # Only suppress if the exact same matchup already had a Lead Change alert in the last 5 minutes (300s)
                # AND it's a tiny yardage seesaw (< 3.0 pts), so a real comeback overtake ALWAYS fires!
                elif time.time() - last_lead_ts < 300 and best_delta < 3.0:
                    log_live_desk_event(season, week_num, "LEAD_CHANGE", "SUPPRESSED_COOLDOWN", mid, {
                        "matchup": f"{c_data['away_team']} vs {c_data['home_team']}",
                        "new_leader": c_leader,
                        "margin": margin
                    })
                else:
                    print(f"🔄 LEAD CHANGE: {c_leader} has taken the lead over {p_leader}!")
                    lc_seed = f"lc:{mid}:{int(c_data['away_score']*10)}:{int(c_data['home_score']*10)}"
                    if margin <= 2.5:
                        lc_title = _pick_live_copy(lc_seed + ":t", [
                            "RAZOR-THIN LEAD CHANGE",
                            "NOSE IN FRONT",
                            "SEESAW BATTLE",
                        ])
                        lc_desc = _pick_live_copy(lc_seed + ":d", [
                            f"**{c_leader}** edges past **{p_leader}** by just **`{margin:.2f}` pts**.",
                            f"New leader by a hair: **{c_leader}** slips ahead of **{p_leader}** (`+{margin:.2f}` pts).",
                            f"**{c_leader}** grabs a paper-thin **`{margin:.2f}`-pt** lead over **{p_leader}**.",
                        ])
                    else:
                        lc_title = _pick_live_copy(lc_seed + ":t", [
                            "LEAD CHANGE",
                            "NEW LEADER ON THE BOARD",
                            "SCOREBOARD FLIP",
                        ])
                        lc_desc = _pick_live_copy(lc_seed + ":d", [
                            f"**{c_leader}** overtakes **{p_leader}** and now leads by **`{margin:.2f}` pts**.",
                            f"**{c_leader}** jumps in front of **{p_leader}** (`+{margin:.2f}` pt edge).",
                            f"Tables turned—**{c_leader}** moves ahead of **{p_leader}** by **`{margin:.2f}` pts**.",
                        ])
                    fields = [
                        {
                            "name": "Scoreboard",
                            "value": (
                                f"**{c_data['away_team']}** ({c_data['away_owner']}): `{c_data['away_score']:.2f}` (`{c_data['away_prob']*100:.1f}%` win prob)\n"
                                f"**{c_data['home_team']}** ({c_data['home_owner']}): `{c_data['home_score']:.2f}` (`{c_data['home_prob']*100:.1f}%` win prob)"
                            ),
                            "inline": False
                        }
                    ]
                    if catalyst:
                        fields.append({"name": "Go-Ahead Play", "value": catalyst, "inline": False})
                    post_live_desk_embed(
                        title=f"{lc_title} — {c_data['away_team']} vs. {c_data['home_team']}",
                        description=lc_desc,
                        color=0xf39c12,
                        fields=fields
                    )
                    c_data['last_lead_alert_time'] = time.time()
                    log_live_desk_event(season, week_num, "LEAD_CHANGE", "FIRED", mid, {
                        "matchup": f"{c_data['away_team']} vs {c_data['home_team']}",
                        "new_leader": c_leader,
                        "prev_leader": p_leader,
                        "margin": margin,
                        "away_score": c_data['away_score'],
                        "home_score": c_data['home_score'],
                        "catalyst": catalyst
                    })

            # Check Position-Aware Nuclear Player Explosions & In-Game Injury Sirens
            prev_player_map = {
                s['name']: s for s in p_data.get('away_starters', []) + p_data.get('home_starters', [])
            }
            nuclear_alerted = set(c_data.get('nuclear_alerted', []))
            injury_alerted = set(c_data.get('injury_alerted', []))

            for side_key, owner_key, team_key in [
                ('away_starters', 'away_owner', 'away_team'),
                ('home_starters', 'home_owner', 'home_team')
            ]:
                owner = c_data[owner_key]
                tname = c_data[team_key]
                for s in c_data.get(side_key, []):
                    pname = s['name']
                    pos_id = s.get('pos_id', 3)
                    curr_pts = float(s.get('current_score', 0.0))
                    base_proj = float(s.get('baseline_proj', 0.0))
                    prev_s = prev_player_map.get(pname, {})
                    prev_pts = float(prev_s.get('current_score', 0.0))

                    # Strict Position-Aware Nuclear Alert (clears BOTH raw score floor AND +over-proj floor!)
                    was_nuclear = is_nuclear_performance(pos_id, prev_pts, base_proj)
                    now_nuclear = is_nuclear_performance(pos_id, curr_pts, base_proj)
                    if now_nuclear and not was_nuclear and pname not in nuclear_alerted:
                        over_proj = curr_pts - base_proj
                        nuc_seed = f"nuc:{season}:{week_num}:{pname}"
                        nuc_title = _pick_live_copy(nuc_seed + ":t", [
                            f"MONSTER PERFORMANCE — {pname} ({curr_pts:.1f} pts)",
                            f"WEEK-WINNING EXPLOSION — {pname} ({curr_pts:.1f} pts)",
                            f"NUCLEAR ALERT — {pname} ({curr_pts:.1f} pts)",
                        ])
                        nuc_desc = _pick_live_copy(nuc_seed + ":d", [
                            f"**{pname}** ({s.get('pos', 'FLEX')}) is torching the slate for **{owner}** ({tname})—already **`+{over_proj:.1f}`** over pre-game projection.",
                            f"Absolute clinic from **{pname}** ({s.get('pos', 'FLEX')}) for **{tname}** ({owner}), blowing past his `{base_proj:.1f}` projection by **`+{over_proj:.1f}` pts**.",
                            f"**{owner}** is getting a week-breaking outburst from **{pname}** ({s.get('pos', 'FLEX')}): `{curr_pts:.1f}` live points and counting.",
                        ])
                        print(f"☢️ NUCLEAR ALERT: {pname} ({owner}) crossed {curr_pts:.1f} pts (+{over_proj:.1f} vs proj)!")
                        post_live_desk_embed(
                            title=nuc_title,
                            description=nuc_desc,
                            color=0x9b59b6,
                            fields=[
                                {
                                    "name": "Statline vs. Projection",
                                    "value": f"Live: `{curr_pts:.2f}` pts  •  Proj: `{base_proj:.1f}` pts  •  Delta: `+{over_proj:.1f}`",
                                    "inline": False
                                },
                                {
                                    "name": "Matchup Impact",
                                    "value": (
                                        f"**{c_data['away_team']}** ({c_data['away_owner']}): `{c_data['away_score']:.2f}` (`{c_data['away_prob']*100:.1f}%`)\n"
                                        f"**{c_data['home_team']}** ({c_data['home_owner']}): `{c_data['home_score']:.2f}` (`{c_data['home_prob']*100:.1f}%`)"
                                    ),
                                    "inline": False
                                }
                            ]
                        )
                        nuclear_alerted.add(pname)
                        log_live_desk_event(season, week_num, "NUCLEAR", "FIRED", mid, {
                            "matchup": f"{c_data['away_team']} @ {c_data['home_team']}",
                            "player": pname,
                            "pos": s.get('pos', 'FLEX'),
                            "owner": owner,
                            "score": curr_pts,
                            "proj": base_proj
                        })

                    # Live In-Game Injury Siren
                    prev_inj = prev_s.get('injury_status', 'ACTIVE')
                    curr_inj = s.get('injury_status', 'ACTIVE')
                    if (
                        s.get('game_status') == 'IN_PROGRESS'
                        and prev_inj in ['ACTIVE', 'NORMAL']
                        and curr_inj in ['OUT', 'DOUBTFUL', 'QUESTIONABLE', 'INJURY_RESERVE']
                        and pname not in injury_alerted
                    ):
                        inj_seed = f"inj:{season}:{week_num}:{pname}"
                        inj_desc = _pick_live_copy(inj_seed, [
                            f"Tough break for **{owner}** ({tname}): active starter **{pname}** ({s.get('pos', 'FLEX')}) was just tagged **{curr_inj}** mid-game.",
                            f"Injury watch for **{tname}** ({owner})—**{pname}** ({s.get('pos', 'FLEX')}) just shifted to **{curr_inj}** during live action.",
                        ])
                        print(f"🚑 IN-GAME INJURY SIREN: {pname} ({owner}) status changed to {curr_inj}!")
                        post_live_desk_embed(
                            title=f"INJURY UPDATE — {pname} ({curr_inj})",
                            description=inj_desc,
                            color=0xe67e22,
                            fields=[
                                {
                                    "name": "Live Line",
                                    "value": f"**{pname}**: `{curr_pts:.2f}` pts (Pre-Game Proj: `{base_proj:.1f}`)",
                                    "inline": False
                                },
                                {
                                    "name": "Updated Matchup Odds",
                                    "value": (
                                        f"**{c_data['away_team']}** ({c_data['away_owner']}): `{c_data['away_score']:.2f}` (`{c_data['away_prob']*100:.1f}%`)\n"
                                        f"**{c_data['home_team']}** ({c_data['home_owner']}): `{c_data['home_score']:.2f}` (`{c_data['home_prob']*100:.1f}%`)"
                                    ),
                                    "inline": False
                                }
                            ]
                        )
                        injury_alerted.add(pname)
                        log_live_desk_event(season, week_num, "INJURY", "FIRED", mid, {
                            "matchup": f"{c_data['away_team']} @ {c_data['home_team']}",
                            "player": pname,
                            "owner": owner,
                            "injury_status": curr_inj,
                            "score": curr_pts
                        })

            c_data['nuclear_alerted'] = list(nuclear_alerted)
            c_data['injury_alerted'] = list(injury_alerted)
    else:
        for mid, c_data in curr_state.items():
            c_data['rolling_history'] = [{
                'epoch': time.time(),
                'away_prob': c_data['away_prob'],
                'away_score': c_data['away_score'],
                'home_score': c_data['home_score'],
                'away_starters': c_data['away_starters'],
                'home_starters': c_data['home_starters'],
            }]
            # Pre-seed nuclear_alerted for any player already at or above their positional threshold
            c_data['nuclear_alerted'] = [
                s['name'] for s in (c_data['away_starters'] + c_data['home_starters'])
                if float(s.get('current_score', 0.0)) >= NUCLEAR_THRESHOLDS.get(s.get('pos_id', 3), 25.0)
            ]
            c_data['injury_alerted'] = []

    # Save current state
    with open(state_file, 'w') as f:
        json.dump(curr_state, f, indent=2)

    return len(curr_state)


def run_retro_analysis(season: int, week_num: int):
    """
    Analyzes the full day's state & audit log for a given week to diagnose alert volume,
    repetitive triggers, suppressed micro-seesaws, and top positional eruptions.
    """
    state_file = STATE_DIR / f"state_{season}_week_{week_num}.json"
    log_file = STATE_DIR / f"alerts_log_{season}_week_{week_num}.jsonl"

    print("\n" + "=" * 78)
    print(f"🔍 BFL LIVE GAME DESK RETROSPECTIVE & TELEMETRY REPORT — {season} WEEK {week_num}")
    print("=" * 78)

    if state_file.exists():
        with open(state_file, "r") as f:
            st = json.load(f)
        print(f"\n📊 MATCHUP SNAPSHOT SUMMARY ({len(st)} Matchups Monitored):")
        for mid, m in st.items():
            la = m.get("last_alert_time", 0)
            la_str = datetime.fromtimestamp(la).strftime("%I:%M:%S %p") if la else "None"
            margin = abs(m["away_score"] - m["home_score"])
            print(
                f"  • Match {mid}: {m['away_team']} ({m['away_score']:.2f}) @ {m['home_team']} ({m['home_score']:.2f}) "
                f"| Margin: {margin:.2f} pts | WinProb: {m['away_prob']*100:.1f}%/{m['home_prob']*100:.1f}% "
                f"| Last Shockwave: {la_str} | Nuclear: {m.get('nuclear_alerted', [])}"
            )

    if log_file.exists():
        events = []
        with open(log_file, "r") as f:
            for line in f:
                if line.strip():
                    events.append(json.loads(line))
        fired = [e for e in events if e.get("status") == "FIRED"]
        suppressed = [e for e in events if e.get("status") != "FIRED"]
        print(f"\n📈 AUDIT LOG METRICS ({len(fired)} Fired Alerts | {len(suppressed)} Suppressed Noise Events):")
        for e in events[-25:]:
            print(f"  [{e.get('timestamp')}] {e.get('status'):<22} | {e.get('event_type'):<12} | {e.get('matchup', '')}")
    else:
        print("\nℹ️ Audit log initialized (`alerts_log_{season}_week_{week}.jsonl` will record all future ticks).")
    print("=" * 78 + "\n")


def post_mnf_miracle_board(season: int, week_num: int, print_only: bool = False):
    """
    Generates and broadcasts the Sunday Night 'Entering Monday Night Football' Clinch & Miracle Board:
    - Lists matchups already CLINCHED vs. matchups still LIVE heading into MNF
    - Shows exact points deficit, remaining MNF players, and Gaussian MNF Comeback / Win Probability
    """
    raw_data = fetch_espn_week_data(ESPN_LEAGUE_ID, str(season), week_num, ESPN_S2, ESPN_SWID)
    curr_state = parse_live_matchup_state(raw_data, week_num, season=season)

    live_battles = []
    nailbiters_finished = []
    clinched_list = []

    for mid, m in curr_state.items():
        a_rem = [f"{s['name']} ({s.get('pos', 'FLEX')})" for s in m['away_starters'] if s['game_status'] != 'FINAL' and s['pct_remaining'] > 0]
        h_rem = [f"{s['name']} ({s.get('pos', 'FLEX')})" for s in m['home_starters'] if s['game_status'] != 'FINAL' and s['pct_remaining'] > 0]

        margin = abs(m['away_score'] - m['home_score'])
        if m['away_score'] > m['home_score'] or (m['away_score'] == m['home_score'] and m['away_prob'] >= m['home_prob']):
            leader_own, trailer_own = m['away_owner'], m['home_owner']
            leader_prob, trailer_prob = m['away_prob'] * 100, m['home_prob'] * 100
            leader_rem, trailer_rem = a_rem, h_rem
        else:
            leader_own, trailer_own = m['home_owner'], m['away_owner']
            leader_prob, trailer_prob = m['home_prob'] * 100, m['away_prob'] * 100
            leader_rem, trailer_rem = h_rem, a_rem

        # A matchup is a live MNF sweat if the trailing team still has players left,
        # OR if the leading team has players left and the lead margin is < 5.0 pts (stat correction / negative points risk).
        # If the trailing team has 0 players left and trails by >= 5.0 pts, it is officially clinched.
        is_live_sweat = bool(trailer_rem) or (bool(leader_rem) and margin < 5.0)

        if is_live_sweat:
            a_rem_str = ", ".join(a_rem) if a_rem else "None (All Final)"
            h_rem_str = ", ".join(h_rem) if h_rem else "None (All Final)"
            live_battles.append({
                "name": f"🔥 LIVE MNF SWEAT: {m['away_owner']} ({m['away_score']:.1f}) @ {m['home_owner']} ({m['home_score']:.1f})",
                "value": (
                    f"• **Current Margin:** **{leader_own}** leads by `{margin:.2f}` pts (Proj Final: `{m['away_exp']:.1f}` - `{m['home_exp']:.1f}`)\n"
                    f"• **Remaining for {m['away_owner']}:** {a_rem_str}\n"
                    f"• **Remaining for {m['home_owner']}:** {h_rem_str}\n"
                    f"• **MNF Win Odds:** **{leader_own}** `{leader_prob:.1f}%` | **{trailer_own} Comeback:** `{trailer_prob:.1f}%`"
                ),
                "inline": False
            })
        else:
            victory_lap = f" *(+{', '.join(leader_rem)} left on MNF)*" if leader_rem else ""
            if margin <= 5.0:
                nailbiters_finished.append(f"• 💓 **PHOTO FINISH:** **{leader_own}** survived **{trailer_own}** `{max(m['away_score'], m['home_score']):.2f}` - `{min(m['away_score'], m['home_score']):.2f}` (Margin: **`{margin:.2f}` pts**!){victory_lap}")
            else:
                clinched_list.append(f"• ✅ **{leader_own}** def. **{trailer_own}** (`{max(m['away_score'], m['home_score']):.1f}` - `{min(m['away_score'], m['home_score']):.1f}`){victory_lap}")

    fields = []
    if live_battles:
        fields.extend(live_battles)
    if nailbiters_finished:
        fields.append({
            "name": "💓 Cardiac Finishes (Decided by < 5 Pts)",
            "value": "\n".join(nailbiters_finished),
            "inline": False
        })
    if clinched_list:
        fields.append({
            "name": "🔒 Officially Clinched Matchups",
            "value": "\n".join(clinched_list),
            "inline": False
        })

    print("\n" + "="*75)
    print(f"🌙 BFL WEEK {week_num} SUNDAY NIGHT MNF MIRACLE & CLINCH BOARD ({season})")
    print("="*75)
    for f in fields:
        print(f"\n{f['name']}\n{f['value']}")

    if not print_only:
        ok = post_live_desk_embed(
            title=f"🌙 BFL WEEK {week_num} ENTERING MNF: MIRACLE & CLINCH BOARD",
            description="⚡ **Sunday Night Football is in the books!** Here is who has officially clinched and who needs a Monday Night Football miracle:",
            color=0x3498db,
            fields=fields
        )
        if ok:
            print("\n🚀 Broadcasted MNF Miracle & Clinch Board to Discord #live-game-desk!")
            log_live_desk_event(season, week_num, "MNF_BOARD", "FIRED", "ALL", {"live_battles": len(live_battles)})

def monitor_continuously(season: int, week_num: int, interval_seconds: int = 60):
    """Runs the live game desk polling loop continuously during game windows."""
    print(f"⚡ Starting BFL Live Game Desk Daemon for Season {season}, Week {week_num} (Polling every {interval_seconds}s)...")
    consecutive_errors = 0
    try:
        while True:
            t_start = time.time()
            try:
                count = run_live_desk_tick(season, week_num)
                print(f"[{datetime.now().strftime('%H:%M:%S')}] Live Desk Tick: Monitored {count} matchups.")
                consecutive_errors = 0
            except (ESPNClientError, Exception) as exc:
                consecutive_errors += 1
                print(f"⚠️ [{datetime.now().strftime('%H:%M:%S')}] Live Desk Tick error (#{consecutive_errors}): {exc}")
                if consecutive_errors in (3, 10):
                    notify_ops(
                        f"⚠️ Live Game Desk Poll Failing ({consecutive_errors}x)",
                        f"Season {season} Week {week_num} error: `{exc}`",
                        level="warning",
                    )
            elapsed = time.time() - t_start
            time.sleep(max(10, interval_seconds - elapsed))
    except KeyboardInterrupt:
        print("\n🛑 Live Game Desk Daemon stopped.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run BFL Live Game Desk Monitoring Daemon")
    parser.add_argument("--season", type=int, default=int(CONFIG_YEAR), help="NFL Season (default: 2026)")
    parser.add_argument("--week", type=int, default=7, help="Week number (e.g. 1-17)")
    parser.add_argument("--interval", type=int, default=60, help="Polling interval in seconds")
    parser.add_argument("--once", action="store_true", help="Run once and exit")
    parser.add_argument("--mnf-board", action="store_true", help="Generate and post the Sunday Night Entering-MNF Miracle & Clinch Board")
    parser.add_argument("--print-only", action="store_true", help="Print MNF board without posting to Discord")
    parser.add_argument("--retro", action="store_true", help="Run post-game retrospective & telemetry report")
    args = parser.parse_args()

    if args.retro:
        run_retro_analysis(args.season, args.week)
    elif args.mnf_board:
        post_mnf_miracle_board(args.season, args.week, print_only=args.print_only)
    elif args.once:
        run_live_desk_tick(args.season, args.week)
        print("✅ Single Live Desk tick complete.")
    else:
        monitor_continuously(args.season, args.week, args.interval)
