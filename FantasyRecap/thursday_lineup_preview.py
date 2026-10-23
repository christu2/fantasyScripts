#!/usr/bin/env python3
"""
BFL Thursday Morning Matchup & Starting Lineup Preview
======================================================
Runs every Thursday morning before TNF to provide an in-depth pre-game breakdown:
- Dynamic In-Season Standings, Current Records (W-L), and Division Ranks
- Matchup Stakes & Importance (1st Place Showdowns, Playoff Bubble Battles)
- Active Starting Lineup Comparison (QB, RB, WR, TE, FLEX, K, D/ST)
- Positional Corps Advantages (RB Corps, WR Depth, QB Duels)
- Simulated Vegas Betting Lines (Spread, Over/Under)
- 18+ Year Lifetime Head-to-Head Context (2008-2026)
- Discord Webhook Embeds & Markdown Report
"""

import os
import sys
import csv
import json
import argparse
import requests
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from FantasyRecap.league_history_intelligence import get_owner_storyline_context, BFL_CHAMPIONSHIPS, compile_all_time_h2h, load_week_matchups, TEAM_DETAILS_2026, standardize_name
from FantasyRecap.config import LEAGUE_ID, CURRENT_YEAR as CONFIG_YEAR

# Load .env
env_path = Path(__file__).resolve().parent.parent / '.env'
if env_path.exists():
    load_dotenv(dotenv_path=env_path)
else:
    load_dotenv()

ESPN_LEAGUE_ID = os.getenv("ESPN_LEAGUE_ID") or os.getenv("LEAGUE_ID") or str(LEAGUE_ID)
ESPN_S2 = os.getenv("ESPN_S2", "")
ESPN_SWID = os.getenv("ESPN_SWID") or os.getenv("SWID", "")
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "")
CURRENT_YEAR = os.getenv("SEASON_YEAR") or str(CONFIG_YEAR)

POS_MAP = {1: 'QB', 2: 'RB', 3: 'WR', 4: 'TE', 5: 'K', 16: 'D/ST'}
SLOT_MAP = {0: 'QB', 2: 'RB', 4: 'WR', 6: 'TE', 16: 'D/ST', 17: 'K', 23: 'FLEX', 20: 'Bench', 21: 'IR'}

def fetch_lineup_boxscores(league_id: str, season: str, week_num: int, s2: str, swid: str):
    """Fetches active rosters, player projections, and injury statuses from ESPN."""
    url = f"https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/{season}/segments/0/leagues/{league_id}?view=mMatchupScore&view=mBoxscore&view=mRoster&view=mTeam&view=mSettings&scoringPeriodId={week_num}"
    cookies = {}
    if s2 and swid:
        cookies = {"espn_s2": s2, "SWID": swid}
    
    try:
        resp = requests.get(url, cookies=cookies, timeout=10)
        if resp.status_code == 200:
            return resp.json()
    except Exception as e:
        print(f"⚠️ Could not fetch live ESPN rosters: {e}")
    return None

def compute_in_season_standings(espn_data: dict, target_week: int):
    """
    Computes current win-loss record, division rank, and points for entering target_week.
    """
    owner_records = {info['owner']: {'wins': 0, 'losses': 0, 'pf': 0.0, 'division': info['division']} 
                     for code, info in TEAM_DETAILS_2026.items()}
    
    if not espn_data or target_week <= 1:
        return {info['owner']: {'record': '0-0', 'rank_str': f"{info['division']} Division", 'wins': 0, 'losses': 0, 'div_rank': 1, 'pf': 0.0}
                for code, info in TEAM_DETAILS_2026.items()}
                
    members_map = {}
    for m in espn_data.get('members', []):
        f = m.get('firstName', '').strip()
        l = m.get('lastName', '').strip()
        full = f"{f} {l}".strip()
        disp = m.get('displayName', '').strip()
        members_map[m['id']] = standardize_name(full if full else disp)
        
    teams_map = {}
    for t in espn_data.get('teams', []):
        tid = t['id']
        oids = t.get('owners', []) or ([t['primaryOwner']] if t.get('primaryOwner') else [])
        for oid in oids:
            if oid in members_map:
                teams_map[tid] = members_map[oid]
                break
        if tid not in teams_map:
            tname = f"{t.get('location', '')} {t.get('nickname', '')}".strip()
            teams_map[tid] = standardize_name(tname)
            
    # Process completed weeks 1 to target_week - 1
    for g in espn_data.get('schedule', []):
        w = g.get('matchupPeriodId', 0)
        if 1 <= w < target_week:
            away = g.get('away', {})
            home = g.get('home', {})
            a_pts = away.get('totalPoints', 0.0)
            h_pts = home.get('totalPoints', 0.0)
            if a_pts > 0 and h_pts > 0:
                a_own = teams_map.get(away.get('teamId'))
                h_own = teams_map.get(home.get('teamId'))
                if a_own in owner_records:
                    if a_pts > h_pts:
                        owner_records[a_own]['wins'] += 1
                    else:
                        owner_records[a_own]['losses'] += 1
                    owner_records[a_own]['pf'] += a_pts
                if h_own in owner_records:
                    if h_pts > a_pts:
                        owner_records[h_own]['wins'] += 1
                    else:
                        owner_records[h_own]['losses'] += 1
                    owner_records[h_own]['pf'] += h_pts

    # Calculate division ranks
    div_groups = {'North': [], 'South': [], 'East': [], 'West': []}
    for o, stat in owner_records.items():
        div = stat['division']
        if div in div_groups:
            div_groups[div].append((o, stat['wins'], stat['losses'], stat['pf']))
            
    standings_output = {}
    for div, group in div_groups.items():
        group.sort(key=lambda x: (x[1], x[3]), reverse=True)
        for rank, (o, w, l, pf) in enumerate(group, 1):
            suffix = {1: 'st', 2: 'nd', 3: 'rd', 4: 'th'}.get(rank, 'th')
            standings_output[o] = {
                'record': f"{w}-{l}",
                'rank_str': f"{rank}{suffix} in {div}",
                'div_rank': rank,
                'wins': w,
                'losses': l,
                'pf': round(pf, 2)
            }
            
    return standings_output

def determine_game_stakes(a_stat: dict, h_stat: dict, m_type: str, target_week: int) -> str:
    """Dynamically calculates the game's importance, stakes, and playoff implications."""
    if target_week == 1:
        return "🌴 Division Grudge Match" if m_type == "Division" else "🚀 Season Kickoff Clash"
        
    a_w, a_l, a_rank = a_stat['wins'], a_stat['losses'], a_stat['div_rank']
    h_w, h_l, h_rank = h_stat['wins'], h_stat['losses'], h_stat['div_rank']
    
    if m_type == 'Division':
        if a_rank in [1, 2] and h_rank in [1, 2] and abs(a_w - h_w) <= 1:
            return "👑 1ST PLACE SHOWDOWN: Winner takes top spot in the division!"
        elif a_rank in [3, 4] and h_rank in [3, 4]:
            return "🚨 BASEMENT BRAWL: Crucial battle to climb out of the cellar!"
        else:
            return "⚔️ DIVISION CLASH: Critical matchup with divisional tiebreaker weight!"
    else:
        if a_w >= (target_week // 2) and h_w >= (target_week // 2):
            return "💥 CONTENDER SHOWDOWN: Playoff contenders fighting for top overall seeding!"
        elif abs(a_w - h_w) <= 1:
            return "🔥 PLAYOFF BUBBLE: High-stakes battle with major postseason implications!"
        else:
            return "🏈 CROSS-DIVISION BATTLE"

def parse_team_starting_lineup(roster_entries: list, week_num: int):
    """Parses starters, projections, and injury statuses for a roster."""
    starters = {'QB': [], 'RB': [], 'WR': [], 'TE': [], 'FLEX': [], 'D/ST': [], 'K': []}
    bench = []
    total_proj = 0.0
    injuries = []
    
    for e in roster_entries:
        p = e.get('playerPoolEntry', {}).get('player', {})
        slot_id = e.get('lineupSlotId', 20)
        slot_name = SLOT_MAP.get(slot_id, 'Bench')
        name = p.get('fullName', 'Unknown')
        pos_id = p.get('defaultPositionId', 0)
        pos_name = POS_MAP.get(pos_id, 'FLEX')
        injury = p.get('injuryStatus', 'ACTIVE')
        
        proj_pts = 0.0
        for s in p.get('stats', []):
            if s.get('scoringPeriodId') == week_num and s.get('statSourceId') == 1:
                proj_pts = round(s.get('appliedTotal', 0.0), 2)
                break
                
        p_info = {
            'name': name,
            'pos': pos_name,
            'slot': slot_name,
            'proj': proj_pts,
            'injury': injury
        }
        
        if slot_name in starters:
            starters[slot_name].append(p_info)
            total_proj += proj_pts
            if injury in ['QUESTIONABLE', 'DOUBTFUL', 'OUT', 'IR']:
                injuries.append(f"{name} ({pos_name} - {injury[0]})")
        else:
            bench.append(p_info)
            
    return starters, bench, round(total_proj, 2), injuries

def generate_thursday_preview_report(target_week: int, target_season: int, matchups: list, h2h: dict, espn_data: dict):
    """Generates the comprehensive Thursday Morning Lineup, Standings, and Stakes preview."""
    lines = []
    lines.append(f"# 🏈 BFL WEEK {target_week} THURSDAY MATCHUP & STARTING LINEUP PREVIEW ({target_season})")
    lines.append(f"*Pre-Game Tactical Breakdown, Positional Battles & Division Stakes*\n")
    lines.append("---")
    lines.append("## 🎙️ THURSDAY COMMISSIONER'S REPORT\n")
    if target_week == 1:
        lines.append(f"Thursday Night Football is here! Week 1 of the {target_season} BFL campaign officially begins. Here is your tactical Tale of the Tape:\n")
    else:
        lines.append(f"Week {target_week} action is underway! Current standings, division stakes, and tactical lineup battles are set for kickoff:\n")
    lines.append("---\n")
    
    matchup_summaries = []
    standings = compute_in_season_standings(espn_data, target_week)
    
    # Map ESPN teams by owner name
    espn_lineups_by_owner = {}
    if espn_data:
        members_map = {}
        for m_obj in espn_data.get('members', []):
            f = m_obj.get('firstName', '').strip()
            l = m_obj.get('lastName', '').strip()
            full = f"{f} {l}".strip()
            disp = m_obj.get('displayName', '').strip()
            members_map[m_obj['id']] = standardize_name(full if full else disp)
            
        teams_map = {}
        for t in espn_data.get('teams', []):
            tid = t['id']
            oids = t.get('owners', []) or ([t['primaryOwner']] if t.get('primaryOwner') else [])
            for oid in oids:
                if oid in members_map:
                    teams_map[tid] = members_map[oid]
                    break
            if tid not in teams_map:
                tname = f"{t.get('location', '')} {t.get('nickname', '')}".strip()
                teams_map[tid] = standardize_name(tname)
                
        sched = [g for g in espn_data.get('schedule', []) if g.get('matchupPeriodId') == target_week]
        for g in sched:
            away_data = g.get('away', {})
            home_data = g.get('home', {})
            if away_data and home_data:
                aid = away_data.get('teamId')
                hid = home_data.get('teamId')
                a_own = teams_map.get(aid)
                h_own = teams_map.get(hid)
                if a_own:
                    espn_lineups_by_owner[a_own] = away_data
                if h_own:
                    espn_lineups_by_owner[h_own] = home_data
    
    # Check if draft has actually occurred (all teams have rosters populated)
    draft_completed = False
    if espn_data:
        total_rostered = sum(len(t.get('roster', {}).get('entries', [])) for t in espn_data.get('teams', []))
        if total_rostered >= 160:
            draft_completed = True

    for idx, m in enumerate(matchups):
        a_owner = m['away_info']['owner']
        h_owner = m['home_info']['owner']
        a_team = m['away_info']['team_name']
        h_team = m['home_info']['team_name']
        m_type = m['type']
        
        a_stat = standings.get(a_owner, {'record': '0-0', 'rank_str': m['away_info']['division'], 'div_rank': 1, 'wins': 0, 'losses': 0})
        h_stat = standings.get(h_owner, {'record': '0-0', 'rank_str': m['home_info']['division'], 'div_rank': 1, 'wins': 0, 'losses': 0})
        stakes_str = determine_game_stakes(a_stat, h_stat, m_type, target_week)
        
        pair = tuple(sorted([a_owner, h_owner]))
        history = h2h.get(pair, [])
        
        a_wins = len([g for g in history if g['winner'] == a_owner])
        h_wins = len([g for g in history if g['winner'] == h_owner])
        total_meetings = len(history)
        
        # Check for live lineup projections in ESPN data
        has_live_rosters = False
        a_proj = 0.0
        h_proj = 0.0
        a_starters, h_starters = None, None
        a_injuries, h_injuries = [], []
        
        if draft_completed:
            adata = espn_lineups_by_owner.get(a_owner)
            hdata = espn_lineups_by_owner.get(h_owner)
            
            if adata and hdata:
                a_entries = adata.get('rosterForCurrentScoringPeriod', {}).get('entries', [])
                h_entries = hdata.get('rosterForCurrentScoringPeriod', {}).get('entries', [])
                if a_entries and h_entries:
                    a_s, a_b, a_p, a_inj = parse_team_starting_lineup(a_entries, target_week)
                    h_s, h_b, h_p, h_inj = parse_team_starting_lineup(h_entries, target_week)
                    if a_p > 0 or h_p > 0:
                        has_live_rosters = True
                        a_starters, h_starters = a_s, h_s
                        a_proj, h_proj = a_p, h_p
                        a_injuries, h_injuries = a_inj, h_inj
        
        lines.append(f"### 🥊 Game {idx+1}: {a_team} ({a_owner}, {a_stat['record']}) @ {h_team} ({h_owner}, {h_stat['record']})")
        lines.append(f"**Division Standing:** {a_stat['rank_str']} vs {h_stat['rank_str']} | **Type:** `{m_type}`")
        lines.append(f"* 🎯 **Matchup Stakes:** `{stakes_str}`\n")
        
        # 1. Vegas Betting Lines
        if has_live_rosters and (a_proj > 0 or h_proj > 0):
            fav = a_owner if a_proj > h_proj else h_owner
            spread = abs(a_proj - h_proj)
            total_ou = a_proj + h_proj
            lines.append(f"* 🎰 **Vegas Line:** **{fav} -{spread:.1f}** | **O/U:** `{total_ou:.1f}` | Projections: {a_owner} ({a_proj:.1f}) @ {h_owner} ({h_proj:.1f})")
            
            a_qb = a_starters['QB'][0] if a_starters.get('QB') else {'name': 'TBD', 'proj': 0}
            h_qb = h_starters['QB'][0] if h_starters.get('QB') else {'name': 'TBD', 'proj': 0}
            lines.append(f"* 🏈 **Starting QB Duel:** **{a_qb['name']}** ({a_qb['proj']} pts) vs **{h_qb['name']}** ({h_qb['proj']} pts)")
            
            a_rb_pts = sum(p['proj'] for p in a_starters.get('RB', []))
            h_rb_pts = sum(p['proj'] for p in h_starters.get('RB', []))
            a_wr_pts = sum(p['proj'] for p in a_starters.get('WR', []))
            h_wr_pts = sum(p['proj'] for p in h_starters.get('WR', []))
            
            rb_adv = f"{a_owner} +{a_rb_pts - h_rb_pts:.1f}" if a_rb_pts > h_rb_pts else f"{h_owner} +{h_rb_pts - a_rb_pts:.1f}"
            wr_adv = f"{a_owner} +{a_wr_pts - h_wr_pts:.1f}" if a_wr_pts > h_wr_pts else f"{h_owner} +{h_wr_pts - a_wr_pts:.1f}"
            lines.append(f"* 📈 **Positional Advantages:** RB Corps: `{rb_adv}` | WR Corps: `{wr_adv}`")
            
            if a_injuries or h_injuries:
                all_inj = a_injuries + h_injuries
                lines.append(f"* 🩺 **Injury Watch:** {', '.join(all_inj)}")
        else:
            lines.append(f"* 🎰 **Projected Spread:** `Pre-Draft Baseline` (Rosters unlock post-draft)")
            
        # 2. Historical Context
        lead_name = a_owner if a_wins > h_wins else h_owner
        lead_str = f"**{lead_name} leads {max(a_wins, h_wins)}-{min(a_wins, h_wins)}**" if a_wins != h_wins else f"**Series Deadlocked {a_wins}-{h_wins}**"
        lines.append(f"* 📜 **18-Year Series:** {lead_str} across **{total_meetings} meetings** (2008–{target_season})")
        
        # 3. Dynamic Narrative (100% Data-Driven from 2008-Present History & In-Season Context)
        sorted_hist = sorted(history, key=lambda x: (x['year'], x['week']))
        last_game = sorted_hist[-1] if sorted_hist else None
        champ_playoffs = [g for g in sorted_hist if g.get('is_champ_playoff')]
        in_season_games = [g for g in sorted_hist if g['year'] == target_season]

        # Calculate current H2H winning streak
        streak_winner = None
        streak_len = 0
        for g in reversed(sorted_hist):
            if streak_winner is None:
                streak_winner = g['winner']
                streak_len = 1
            elif g['winner'] == streak_winner:
                streak_len += 1
            else:
                break

        # Check if they met in an official BFL Super Bowl
        sb_meetings = [
            sb for sb in BFL_CHAMPIONSHIPS
            if {sb['champ'], sb['runner_up']} == {a_owner, h_owner}
        ]

        if total_meetings == 0:
            narrative = f"🆕 Inaugural franchise showdown! {a_owner} and {h_owner} meet for the very first time in BFL history."
        elif in_season_games:
            ig = in_season_games[-1]
            rev_owner = a_owner if ig['winner'] == h_owner else h_owner
            ig_hi = max(ig['score_a'], ig['score_b'])
            ig_lo = min(ig['score_a'], ig['score_b'])
            extra_flav = ""
            if streak_len >= 3:
                extra_flav = f" {streak_winner} has now won {streak_len} straight in this series!"
            elif a_wins == h_wins:
                extra_flav = f" The victory knotted their all-time series at {a_wins}-{h_wins}!"
            narrative = (
                f"🔁 **In-Season Division Rematch!** **{ig['winner']}** took their Week {ig['week']} clash earlier this season "
                f"(`{ig_hi:.1f}` - `{ig_lo:.1f}`, +{ig['margin']:.2f} pts); **{rev_owner}** looks to even the {target_season} season series.{extra_flav}"
            )
        else:
            lg_hi = max(last_game['score_a'], last_game['score_b'])
            lg_lo = min(last_game['score_a'], last_game['score_b'])
            lg_stage = " in the Championship Playoffs" if last_game.get('is_champ_playoff') else (" in Consolation play" if last_game.get('is_consolation') else "")
            last_str = f"**{last_game['winner']}** won their last meeting in {last_game['year']} Week {last_game['week']}{lg_stage} (`{lg_hi:.1f}` - `{lg_lo:.1f}`, +{last_game['margin']:.2f} pts)."

            if sb_meetings:
                sb = sb_meetings[-1]
                narrative = (
                    f"🏆 **Super Bowl Rematch Pedigree!** Faced off in the {sb['year']} BFL Championship (won by **{sb['champ']}**, {sb['score']}). "
                    f"{last_str}"
                )
            elif pair == ('Shawn Lukose', 'Shawn Ullenbrauck'):
                narrative = f"👑 **The Battle of the Shawns ({total_meetings + 1}th Meeting):** {last_str}"
            elif streak_len >= 3:
                loser_streak = h_owner if streak_winner == a_owner else a_owner
                narrative = (
                    f"🔥 **Series Dominance:** **{streak_winner}** has won **{streak_len} straight** against {loser_streak}! {last_str}"
                )
            elif a_wins == h_wins:
                playoff_note = f" (including {len(champ_playoffs)} playoff clashes)" if champ_playoffs else ""
                narrative = (
                    f"⚖️ **All-Time Deadlock ({a_wins}-{h_wins}):** Perfectly tied across {total_meetings} lifetime meetings{playoff_note}—winner takes the series lead! {last_str}"
                )
            elif last_game['margin'] <= 5.0:
                narrative = (
                    f"💓 **Cardiac Rivalry:** Their last clash ({last_game['year']} Week {last_game['week']}{lg_stage}) was decided by a razor-thin **{last_game['margin']:.2f} pts** "
                    f"(`{lg_hi:.1f}` - `{lg_lo:.1f}` in favor of **{last_game['winner']}**)!"
                )
            elif champ_playoffs:
                a_pw = sum(1 for g in champ_playoffs if g['winner'] == a_owner)
                h_pw = sum(1 for g in champ_playoffs if g['winner'] == h_owner)
                pw_str = f"{a_owner} {a_pw}-{h_pw} {h_owner}" if a_pw != h_pw else f"tied {a_pw}-{h_pw}"
                m_word = "Meeting" if len(champ_playoffs) == 1 else "Meetings"
                narrative = (
                    f"⚔️ **Postseason History ({len(champ_playoffs)} Playoff {m_word}, {pw_str}):** {last_str}"
                )
            else:
                narrative = f"📜 **Matchup # {total_meetings + 1} Since 2008:** {last_str}"
            
        lines.append(f"* ⚔️ **Key Storyline:** {narrative}\n")
        
        matchup_summaries.append({
            'game_num': idx+1,
            'away': f"{a_team} ({a_owner})",
            'home': f"{h_team} ({h_owner})",
            'away_owner': a_owner,
            'home_owner': h_owner,
            'away_rec': f"{a_owner} ({a_stat['record']})",
            'home_rec': f"{h_owner} ({h_stat['record']})",
            'standings': f"{a_stat['rank_str']} @ {h_stat['rank_str']}",
            'stakes': stakes_str,
            'series': f"{a_owner} {a_wins}-{h_wins} {h_owner}" if total_meetings > 0 else "First Meeting",
            'type': m_type,
            'spread': f"{fav} -{spread:.1f}" if has_live_rosters else "Pre-Draft",
            'spread_val': spread if has_live_rosters else 0.0,
            'fav_owner': fav if has_live_rosters else a_owner,
            'underdog_owner': (h_owner if fav == a_owner else a_owner) if has_live_rosters else h_owner,
            'narrative': narrative
        })
        
    lines.append("---\n## 📋 THURSDAY BETTING BOARD & MATCHUP MATRIX\n")
    lines.append("| Game | Away Team | Home Team | Standings | Stakes | Spread | 18-Year H2H |")
    lines.append("|:---:|:---|:---|:---:|:---|:---:|:---:|")
    for s in matchup_summaries:
        lines.append(f"| #{s['game_num']} | {s['away_rec']} | {s['home_rec']} | `{s['standings']}` | **{s['stakes']}** | `{s['spread']}` | **{s['series']}** |")
        
    lines.append(f"\n---\n💡 *Generated by BFL Thursday Lineup Intelligence Engine. Broadcasted to Discord.*")
    return "\n".join(lines), matchup_summaries

def post_thursday_preview_to_discord(summaries: list, week_num: int = 1, season: int = 2026):
    """Broadcasts Thursday Lineup Preview & interactive 'Upset of the Week' Poll to #commissioner-desk."""
    from FantasyRecap.discord_channels import send_to_channel
    
    fields = []
    for s in summaries:
        fields.append({
            "name": f"Game #{s['game_num']}: {s['away']} @ {s['home']}",
            "value": f"📊 **Standings:** `{s['standings']}` | 🎯 **Stakes:** `{s['stakes']}`\n🎰 **Spread:** `{s['spread']}` | 📜 **H2H:** `{s['series']}`\n⚔️ **Storyline:** {s['narrative']}",
            "inline": False
        })
        
    payload = {
        "username": "BFL Vegas & Lineup Desk",
        "avatar_url": "https://a.espncdn.com/combiner/i?img=/i/headshots/nfl/players/full/3918298.png",
        "embeds": [{
            "title": f"🏈 BFL Week {week_num} Thursday Lineup Preview & Vegas Odds ({season})",
            "description": f"**Pre-Game Tactical Matchup, Division Stakes & Lineup Projections**\nTNF is here! Standings, division implications, and 18-year storylines:",
            "color": 0x27ae60,  # Emerald Green
            "fields": fields,
            "footer": {"text": f"Beasts Football League • Thursday Lineup Preview • Week {week_num}"}
        }]
    }

    # Build native Discord Poll for "Upset / Dog of the Week" from matchups with real spreads
    poll_candidates = [s for s in summaries if s.get('spread_val', 0) > 0]
    if not poll_candidates:
        poll_candidates = summaries[:]
    poll_candidates.sort(key=lambda x: x.get('spread_val', 0), reverse=True)

    poll_answers = []
    for s in poll_candidates[:8]:
        dog = s.get('underdog_owner', s['away_owner'])
        fav = s.get('fav_owner', s['home_owner'])
        sp = s.get('spread_val', 0.0)
        ans_text = f"{dog} (+{sp:.1f}) over {fav}" if sp > 0 else f"{s['away_owner']} vs {s['home_owner']}"
        poll_answers.append({
            "poll_media": {
                "text": ans_text[:55],
                "emoji": {"name": "🐶"}
            }
        })

    if len(poll_answers) >= 2:
        payload["poll"] = {
            "question": {"text": f"🎰 BFL Week {week_num} Sportsbook: Which underdog pulls off the Upset of the Week?"},
            "answers": poll_answers,
            "duration": 72,
            "allow_multiselect": True
        }
    
    success = send_to_channel('commish', payload)
    if success:
        print("🚀 Successfully broadcasted Thursday Lineup Preview & Upset Poll to #commissioner-desk!")

def main():
    parser = argparse.ArgumentParser(description="Generate BFL Thursday Starting Lineup & Vegas Odds Preview")
    parser.add_argument("--week", type=int, default=7, help="Week number to preview (default: 7)")
    parser.add_argument("--season", type=int, default=int(CURRENT_YEAR), help="Season year")
    parser.add_argument("--league-id", default=ESPN_LEAGUE_ID, help="ESPN League ID")
    parser.add_argument("--csv", default="FantasyScheduler/schedule_by_week.csv", help="Schedule CSV")
    parser.add_argument("--discord", action="store_true", help="Broadcast to Discord webhook")
    args = parser.parse_args()
    
    csv_path = args.csv
    if not os.path.exists(csv_path):
        script_dir_csv = Path(__file__).resolve().parent.parent / "FantasyScheduler" / "schedule_by_week.csv"
        if script_dir_csv.exists():
            csv_path = str(script_dir_csv)
            
    espn_data = fetch_lineup_boxscores(args.league_id, str(args.season), args.week, ESPN_S2, ESPN_SWID)
    matchups = load_week_matchups(csv_path, args.week, espn_data=espn_data)
    h2h, career_stats = compile_all_time_h2h(args.league_id, ESPN_S2, ESPN_SWID, current_season=args.season, up_to_week=args.week)
    
    report_md, summaries = generate_thursday_preview_report(args.week, args.season, matchups, h2h, espn_data)
    
    print("\n" + "="*75)
    print(f"🏈 BFL WEEK {args.week} THURSDAY LINEUP PREVIEW ({args.season})")
    print("="*75)
    print(report_md)
    
    out_dir = Path(__file__).resolve().parent / "output"
    out_dir.mkdir(exist_ok=True)
    out_file = out_dir / f"thursday_preview_week_{args.week}_{args.season}.md"
    with open(out_file, 'w') as f:
        f.write(report_md)
        
    print("\n" + "="*75)
    print(f"💾 Report saved to: {out_file}")
    print("="*75)
    
    if args.discord:
        post_thursday_preview_to_discord(summaries, args.week, args.season)

if __name__ == "__main__":
    main()
