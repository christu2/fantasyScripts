#!/usr/bin/env python3
"""
BFL Thursday Morning Matchup & Starting Lineup Preview
======================================================
Runs every Thursday morning before TNF to provide an in-depth pre-game breakdown:
- Active Starting Lineup Comparison (QB, RB, WR, TE, FLEX, K, D/ST)
- Individual & Positional Projections (QB Battle, RB Advantage, WR Depth)
- Questionable / Injury Watch
- Simulated Vegas Betting Lines (Spread, Over/Under, Win Probability)
- 18+ Year Lifetime Head-to-Head & Championship Context
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
from FantasyRecap.league_history_intelligence import get_owner_storyline_context, BFL_CHAMPIONSHIPS
from FantasyRecap.league_preview_generator import compile_all_time_h2h, load_week_matchups, TEAM_DETAILS_2026, standardize_name

# Load .env
env_path = Path(__file__).resolve().parent.parent / '.env'
if env_path.exists():
    load_dotenv(dotenv_path=env_path)
else:
    load_dotenv()

ESPN_LEAGUE_ID = os.getenv("ESPN_LEAGUE_ID", "157057")
ESPN_S2 = os.getenv("ESPN_S2", "")
ESPN_SWID = os.getenv("ESPN_SWID", "")
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "")
CURRENT_YEAR = os.getenv("SEASON_YEAR") or str(datetime.now().year)

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
        pro_team = p.get('proTeamId', 0)
        
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
    """Generates the comprehensive Thursday Morning Lineup & Vegas Odds report."""
    lines = []
    lines.append(f"# 🏈 BFL WEEK {target_week} THURSDAY MATCHUP & STARTING LINEUP PREVIEW ({target_season})")
    lines.append(f"*Pre-Game Tactical Breakdown, Positional Battles & Simulated Vegas Lines*\n")
    lines.append("---")
    lines.append("## 🎙️ THURSDAY COMMISSIONER'S REPORT\n")
    lines.append(f"Thursday Night Football approaches! Lineups are locked, projections are calculated, and Week {target_week} of the {target_season} BFL season is set for kickoff. Here is your full tactical Tale of the Tape:\n")
    lines.append("---\n")
    
    matchup_summaries = []
    
    # Map ESPN teams if available
    espn_matchups_by_pair = {}
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
    
    for idx, m in enumerate(matchups):
        a_owner = m['away_info']['owner']
        h_owner = m['home_info']['owner']
        a_team = m['away_info']['team_name']
        h_team = m['home_info']['team_name']
        m_type = m['type']
        
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
        
        lines.append(f"### 🥊 Game {idx+1}: {a_team} ({a_owner}) @ {h_team} ({h_owner})")
        lines.append(f"**Division:** {m['away_info']['division']} vs {m['home_info']['division']} | **Type:** `{m_type}`\n")
        
        # 1. Vegas Betting Lines
        if has_live_rosters and (a_proj > 0 or h_proj > 0):
            fav = a_owner if a_proj > h_proj else h_owner
            spread = abs(a_proj - h_proj)
            total_ou = a_proj + h_proj
            lines.append(f"* 🎰 **Vegas Line:** **{fav} -{spread:.1f}** | **O/U:** `{total_ou:.1f}` | Projections: {a_owner} ({a_proj:.1f}) @ {h_owner} ({h_proj:.1f})")
            
            # QB Battle
            a_qb = a_starters['QB'][0] if a_starters.get('QB') else {'name': 'TBD', 'proj': 0}
            h_qb = h_starters['QB'][0] if h_starters.get('QB') else {'name': 'TBD', 'proj': 0}
            lines.append(f"* 🏈 **Starting QB Duel:** **{a_qb['name']}** ({a_qb['proj']} pts) vs **{h_qb['name']}** ({h_qb['proj']} pts)")
            
            # Key Positional Advantage
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
            lines.append(f"* 🎰 **Projected Spread:** `Pre-Draft Baseline` (Rosters will populate post-draft)")
            
        # 2. Historical & Drama Context
        leader_str = f"**{a_owner if a_wins > h_wins else h_owner} leads {max(a_wins, h_wins)}-{min(a_wins, h_wins)}**" if a_wins != h_wins else f"**Deadlocked {a_wins}-{h_wins}**"
        lines.append(f"* 📜 **18-Year Series:** {leader_str} across **{total_meetings} meetings** (2008–{target_season})")
        
        # 3. Dynamic Narrative
        if pair == ('Shawn Lukose', 'Shawn Ullenbrauck'):
            narrative = f"👑 THE BATTLE OF THE SHAWNS. Lukose holds a 10-8 edge across 18 lifetime meetings. Thor seeking a statement win."
        elif pair == ('Adam Olen', 'Samran Mirza'):
            narrative = f"🔥 Deadlocked at 7-7! 15th meeting in league history. AMO took their 2025 clash by 1.04 pts."
        elif pair == ('Dino Davros', 'rej hoxha'):
            narrative = f"⚖️ Century Rivalry. Dino leads 10-6 in 16 clashes since 2008. Rej won their last meeting by 0.38 pts."
        elif pair == ('Tommy Ehrlich', 'Nick Christus'):
            narrative = f"🎯 North Division Showdown. Nick leads 11-6 across 17 meetings since 2008. Tommy searching for his first franchise ring."
        elif pair == ('Abe Thomas', 'Saagar Gupta'):
            narrative = f"🌴 South Division Grudge Match. Defending champ Abe leads 12-6 over inaugural champ Saagar."
        elif pair == ('Daniel Kruszewski', 'Nitesh Patel'):
            narrative = f"⚡ Nitesh's first official game as solo owner of Big Nasties against former champ Dan Kruszewski."
        elif pair == ('Blake Whitehouse', 'Nael Ahmed'):
            narrative = f"⚔️ Cross-Division Clash. Nael holds a 4-3 edge across 7 meetings (including 4 playoff clashes)."
        elif pair == ('Alex Kite', 'Sydney Miller'):
            narrative = f"💥 West Division Showdown. Sydney holds a tight 5-4 edge over Alex in 9 meetings."
        else:
            narrative = f"{a_owner} vs {h_owner} renewal."
            
        lines.append(f"* ⚔️ **Key Storyline:** {narrative}\n")
        
        matchup_summaries.append({
            'game_num': idx+1,
            'away': f"{a_team} ({a_owner})",
            'home': f"{h_team} ({h_owner})",
            'series': f"{a_owner} {a_wins}-{h_wins} {h_owner}" if total_meetings > 0 else "First Meeting",
            'type': m_type,
            'spread': f"{fav} -{spread:.1f}" if has_live_rosters else "TBD",
            'narrative': narrative
        })
        
    lines.append("---\n## 📋 THURSDAY BETTING BOARD & MATCHUP MATRIX\n")
    lines.append("| Game | Away Team | Home Team | Projected Spread | 18-Year H2H | Storyline |")
    lines.append("|:---:|:---|:---|:---:|:---:|:---|")
    for s in matchup_summaries:
        lines.append(f"| #{s['game_num']} | {s['away']} | {s['home']} | `{s['spread']}` | **{s['series']}** | {s['narrative']} |")
        
    lines.append(f"\n---\n💡 *Generated by BFL Thursday Lineup Intelligence Engine. Broadcasted to Discord.*")
    return "\n".join(lines), matchup_summaries

def post_thursday_preview_to_discord(webhook_url: str, summaries: list, week_num: int = 1, season: int = 2026):
    """Broadcasts Thursday Lineup Preview to Discord."""
    if not webhook_url:
        print("ℹ️ No DISCORD_WEBHOOK_URL set in .env.")
        return
        
    fields = []
    for s in summaries:
        fields.append({
            "name": f"Game #{s['game_num']}: {s['away']} @ {s['home']}",
            "value": f"🎰 **Spread:** `{s['spread']}` | 📜 **H2H:** `{s['series']}`\n⚔️ **Storyline:** {s['narrative']}",
            "inline": False
        })
        
    payload = {
        "username": "BFL Vegas & Lineup Desk",
        "avatar_url": "https://a.espncdn.com/combiner/i?img=/i/headshots/nfl/players/full/3918298.png",
        "embeds": [{
            "title": f"🏈 BFL Week {week_num} Thursday Lineup Preview & Vegas Odds ({season})",
            "description": f"**Pre-Game Tactical Matchup & Lineup Projections**\nTNF is here! Positional battles, simulated spreads, and 18-year historical storylines:",
            "color": 0x27ae60,  # Emerald Green
            "fields": fields,
            "footer": {"text": f"Beasts Football League • Thursday Lineup Preview • Week {week_num}"}
        }]
    }
    
    try:
        resp = requests.post(webhook_url, json=payload, timeout=10)
        if resp.status_code in [200, 204]:
            print("🚀 Successfully broadcasted Thursday Lineup Preview to Discord!")
        else:
            print(f"❌ Discord returned status {resp.status_code}")
    except Exception as e:
        print(f"❌ Error sending Discord webhook: {e}")

def main():
    parser = argparse.ArgumentParser(description="Generate BFL Thursday Starting Lineup & Vegas Odds Preview")
    parser.add_argument("--week", type=int, default=1, help="Week number to preview (default: 1)")
    parser.add_argument("--season", type=int, default=int(CURRENT_YEAR), help="Season year")
    parser.add_argument("--league-id", default=ESPN_LEAGUE_ID, help="ESPN League ID")
    parser.add_argument("--csv", default="FantasyScheduler/schedule_by_week.csv", help="Schedule CSV")
    parser.add_argument("--discord", action="store_true", default=True, help="Broadcast to Discord webhook")
    args = parser.parse_args()
    
    csv_path = args.csv
    if not os.path.exists(csv_path):
        script_dir_csv = Path(__file__).resolve().parent.parent / "FantasyScheduler" / "schedule_by_week.csv"
        if script_dir_csv.exists():
            csv_path = str(script_dir_csv)
            
    matchups = load_week_matchups(csv_path, args.week)
    h2h, career_stats = compile_all_time_h2h(args.league_id, ESPN_S2, ESPN_SWID, current_season=args.season, up_to_week=args.week)
    espn_data = fetch_lineup_boxscores(args.league_id, str(args.season), args.week, ESPN_S2, ESPN_SWID)
    
    report_md, summaries = generate_thursday_preview_report(args.week, args.season, matchups, h2h, espn_data)
    
    print("\n" + "="*75)
    print(f"🏈 BFL WEEK {args.week} THURSDAY LINEUP PREVIEW ({args.season})")
    print("="*75)
    print(report_md)
    
    # Save preview file
    out_file = Path(__file__).resolve().parent / f"thursday_preview_week_{args.week}_{args.season}.md"
    with open(out_file, 'w') as f:
        f.write(report_md)
        
    print("\n" + "="*75)
    print(f"💾 Report saved to: {out_file.name}")
    print("="*75)
    
    if args.discord or DISCORD_WEBHOOK_URL:
        post_thursday_preview_to_discord(DISCORD_WEBHOOK_URL, summaries, args.week, args.season)

if __name__ == "__main__":
    main()
