#!/usr/bin/env python3
"""
BFL Weekly League Recap & Manager IQ Generator
==============================================
Fetches live ESPN Fantasy Football boxscores and generates an entertaining,
deeply analytical weekly recap containing:
- Matchup Results & Box Score Summaries
- Manager IQ / Optimal Lineup Efficiency
- Costliest Bench Blunders (who sat a 25pt player)
- Weekly Awards: Game of the Week, Beatdown of the Week, Tough Luck, Golden Horseshoe
- All-Play Standings & Power Rankings
- Exportable Markdown newsletter for Facebook/Chat
"""

import os
import sys
import json
import argparse
import requests
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv

# Load .env
env_path = Path(__file__).resolve().parent.parent / '.env'
if env_path.exists():
    load_dotenv(dotenv_path=env_path)
else:
    load_dotenv()

ESPN_LEAGUE_ID = os.getenv("ESPN_LEAGUE_ID", "157057")
ESPN_S2 = os.getenv("ESPN_S2", "")
ESPN_SWID = os.getenv("ESPN_SWID", "")
CURRENT_YEAR = str(datetime.now().year)

POSITION_MAP = {
    1: 'QB',
    2: 'RB',
    3: 'WR',
    4: 'TE',
    5: 'K',
    16: 'D/ST'
}

SLOT_MAP = {
    0: 'QB',
    2: 'RB',
    4: 'WR',
    6: 'TE',
    16: 'D/ST',
    17: 'K',
    20: 'Bench',
    21: 'IR',
    23: 'FLEX'
}

def fetch_espn_week_data(league_id: str, season: str, week: int, s2: str, swid: str):
    """Fetch boxscores, rosters, matchups, and members from ESPN API."""
    url = f"https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/{season}/segments/0/leagues/{league_id}?view=mMatchupScore&view=mBoxscore&view=mRoster&view=mTeam&view=mSettings&scoringPeriodId={week}"
    cookies = {}
    if s2 and swid:
        cookies = {"espn_s2": s2, "SWID": swid}
    
    try:
        resp = requests.get(url, cookies=cookies, timeout=12)
        if resp.status_code == 200:
            return resp.json()
        else:
            print(f"❌ ESPN API returned status code {resp.status_code}")
            sys.exit(1)
    except Exception as e:
        print(f"❌ Error connecting to ESPN API: {e}")
        sys.exit(1)

def parse_league_members_and_teams(data):
    """Map team IDs to owners, team names, and divisions."""
    members = {}
    for m in data.get('members', []):
        disp = m.get('displayName', '')
        first = m.get('firstName', '').strip()
        last = m.get('lastName', '').strip()
        full = f"{first} {last}".strip()
        members[m['id']] = full if full else disp
        
    divisions = {}
    for d in data.get('settings', {}).get('scheduleSettings', {}).get('divisions', []):
        divisions[d['id']] = d.get('name', f"Division {d['id']}")
        
    teams = {}
    for t in data.get('teams', []):
        tid = t['id']
        t_name = f"{t.get('location', '')} {t.get('nickname', '')}".strip()
        owner_id = t.get('primaryOwner') or (t.get('owners', [None])[0])
        owner_name = members.get(owner_id, 'Unknown')
        div_id = t.get('divisionId', 0)
        div_name = divisions.get(div_id, 'General')
        
        teams[tid] = {
            'id': tid,
            'name': t_name if t_name else owner_name,
            'owner': owner_name,
            'abbrev': t.get('abbrev', ''),
            'division': div_name,
            'wins': t.get('record', {}).get('overall', {}).get('wins', 0),
            'losses': t.get('record', {}).get('overall', {}).get('losses', 0),
            'ties': t.get('record', {}).get('overall', {}).get('ties', 0),
            'points_for': t.get('record', {}).get('overall', {}).get('pointsFor', 0.0),
        }
    return teams

def calculate_optimal_lineup(roster_entries):
    """
    Computes the maximum theoretical score a team could have produced with their roster.
    Returns: (optimal_score, optimal_lineup, starters, bench, bench_blunders)
    """
    all_players = []
    for entry in roster_entries:
        p = entry.get('playerPoolEntry', {}).get('player', {})
        pts = entry.get('playerPoolEntry', {}).get('appliedStatTotal', 0.0)
        pos_id = p.get('defaultPositionId', 0)
        slot_id = entry.get('lineupSlotId', 20)
        name = p.get('fullName', 'Unknown')
        pro_team = p.get('proTeamId', 0)
        
        all_players.append({
            'name': name,
            'pos_id': pos_id,
            'pos_name': POSITION_MAP.get(pos_id, 'FLEX'),
            'pts': pts,
            'slot_id': slot_id,
            'slot_name': SLOT_MAP.get(slot_id, 'Bench'),
            'is_starter': slot_id not in [20, 21]
        })
    
    starters = [p for p in all_players if p['is_starter']]
    bench = [p for p in all_players if not p['is_starter']]
    
    qbs = sorted([p for p in all_players if p['pos_id'] == 1], key=lambda x: x['pts'], reverse=True)
    rbs = sorted([p for p in all_players if p['pos_id'] == 2], key=lambda x: x['pts'], reverse=True)
    wrs = sorted([p for p in all_players if p['pos_id'] == 3], key=lambda x: x['pts'], reverse=True)
    tes = sorted([p for p in all_players if p['pos_id'] == 4], key=lambda x: x['pts'], reverse=True)
    ks  = sorted([p for p in all_players if p['pos_id'] == 5], key=lambda x: x['pts'], reverse=True)
    dsts = sorted([p for p in all_players if p['pos_id'] == 16], key=lambda x: x['pts'], reverse=True)
    
    optimal_lineup = []
    if qbs: optimal_lineup.append(qbs[0])
    if len(rbs) >= 2: optimal_lineup.extend(rbs[:2])
    elif rbs: optimal_lineup.extend(rbs)
    if len(wrs) >= 2: optimal_lineup.extend(wrs[:2])
    elif wrs: optimal_lineup.extend(wrs)
    if tes: optimal_lineup.append(tes[0])
    if ks: optimal_lineup.append(ks[0])
    if dsts: optimal_lineup.append(dsts[0])
    
    flex_pool = rbs[2:] + wrs[2:] + tes[1:]
    flex_pool = sorted(flex_pool, key=lambda x: x['pts'], reverse=True)
    if flex_pool: optimal_lineup.append(flex_pool[0])
    
    optimal_score = sum(p['pts'] for p in optimal_lineup)
    
    # Identify bench blunders: bench players who scored more than starters at their position
    bench_blunders = []
    for b in bench:
        worse_starters = [s for s in starters if (s['pos_id'] == b['pos_id'] or s['slot_id'] == 23) and b['pts'] > s['pts'] + 4.0]
        if worse_starters:
            worst = min(worse_starters, key=lambda x: x['pts'])
            bench_blunders.append({
                'benched': b,
                'started': worst,
                'diff': round(b['pts'] - worst['pts'], 2)
            })
            
    bench_blunders = sorted(bench_blunders, key=lambda x: x['diff'], reverse=True)
    return round(optimal_score, 2), optimal_lineup, starters, bench, bench_blunders

def analyze_week(data, week_num: int):
    """Perform full analytical breakdown of the week's matchups and efficiency."""
    teams = parse_league_members_and_teams(data)
    matchups_raw = [g for g in data.get('schedule', []) if g.get('matchupPeriodId') == week_num]
    
    matchups = []
    team_performances = {}
    
    for g in matchups_raw:
        away_data = g.get('away', {})
        home_data = g.get('home', {})
        if not away_data or not home_data:
            continue
            
        away_id = away_data.get('teamId')
        home_id = home_data.get('teamId')
        away_score = round(away_data.get('totalPoints', 0.0), 2)
        home_score = round(home_data.get('totalPoints', 0.0), 2)
        
        # Calculate optimal lineups
        away_opt, away_opt_lineup, away_starters, away_bench, away_blunders = calculate_optimal_lineup(away_data.get('rosterForCurrentScoringPeriod', {}).get('entries', []))
        home_opt, home_opt_lineup, home_starters, home_bench, home_blunders = calculate_optimal_lineup(home_data.get('rosterForCurrentScoringPeriod', {}).get('entries', []))
        
        away_eff = round((away_score / away_opt * 100), 1) if away_opt > 0 else 100.0
        home_eff = round((home_score / home_opt * 100), 1) if home_opt > 0 else 100.0
        
        away_lost = round(max(0.0, away_opt - away_score), 2)
        home_lost = round(max(0.0, home_opt - home_score), 2)
        
        away_won = away_score > home_score
        home_won = home_score > away_score
        margin = round(abs(away_score - home_score), 2)
        
        team_performances[away_id] = {
            'team': teams[away_id],
            'score': away_score,
            'optimal': away_opt,
            'efficiency': away_eff,
            'bench_lost': away_lost,
            'won': away_won,
            'margin': margin,
            'opponent_id': home_id,
            'opponent_score': home_score,
            'blunders': away_blunders,
            'top_scorer': max(away_starters, key=lambda x: x['pts']) if away_starters else None
        }
        
        team_performances[home_id] = {
            'team': teams[home_id],
            'score': home_score,
            'optimal': home_opt,
            'efficiency': home_eff,
            'bench_lost': home_lost,
            'won': home_won,
            'margin': margin,
            'opponent_id': away_id,
            'opponent_score': away_score,
            'blunders': home_blunders,
            'top_scorer': max(home_starters, key=lambda x: x['pts']) if home_starters else None
        }
        
        matchups.append({
            'away': teams[away_id],
            'home': teams[home_id],
            'away_score': away_score,
            'home_score': home_score,
            'winner': teams[away_id] if away_won else (teams[home_id] if home_won else 'Tie'),
            'loser': teams[home_id] if away_won else (teams[away_id] if home_won else 'Tie'),
            'margin': margin,
            'away_eff': away_eff,
            'home_eff': home_eff,
            'away_lost': away_lost,
            'home_lost': home_lost
        })
        
    # All-Play Record: Compare each team's score against all other 15 teams
    all_scores = sorted([(tid, perf['score']) for tid, perf in team_performances.items()], key=lambda x: x[1], reverse=True)
    
    for rank, (tid, score) in enumerate(all_scores):
        wins = len([s for s in all_scores if score > s[1]])
        losses = len([s for s in all_scores if score < s[1]])
        ties = len([s for s in all_scores if score == s[1] and tid != s[0]])
        team_performances[tid]['all_play'] = f"{wins}-{losses}" + (f"-{ties}" if ties else "")
        team_performances[tid]['weekly_rank'] = rank + 1
        
    return matchups, team_performances, teams

def generate_weekly_awards(matchups, team_performances):
    """Compute all weekly superlatives and roasts."""
    # 1. Game of the Week (Closest Margin)
    valid_games = [m for m in matchups if m['margin'] > 0]
    game_of_week = min(valid_games, key=lambda x: x['margin']) if valid_games else None
    
    # 2. Beatdown of the Week (Largest Margin)
    blowout_of_week = max(valid_games, key=lambda x: x['margin']) if valid_games else None
    
    # 3. Tough Luck Award (Highest scoring loser)
    losers = [p for p in team_performances.values() if not p['won']]
    tough_luck = max(losers, key=lambda x: x['score']) if losers else None
    
    # 4. Golden Horseshoe (Lowest scoring winner)
    winners = [p for p in team_performances.values() if p['won']]
    lucky_winner = min(winners, key=lambda x: x['score']) if winners else None
    
    # 5. Galaxy Brain of the Week (Highest Manager IQ / Lineup Efficiency)
    galaxy_brain = max(team_performances.values(), key=lambda x: (x['efficiency'], x['score']))
    
    # 6. Bench Mob of the Week (Most points wasted on bench)
    bench_mob = max(team_performances.values(), key=lambda x: x['bench_lost'])
    
    # 7. Worst Single Bench Blunder
    all_blunders = []
    for tid, perf in team_performances.items():
        for b in perf['blunders']:
            all_blunders.append({'owner': perf['team']['owner'], **b})
    worst_blunder = max(all_blunders, key=lambda x: x['diff']) if all_blunders else None
    
    return {
        'game_of_week': game_of_week,
        'blowout_of_week': blowout_of_week,
        'tough_luck': tough_luck,
        'lucky_winner': lucky_winner,
        'galaxy_brain': galaxy_brain,
        'bench_mob': bench_mob,
        'worst_blunder': worst_blunder
    }

def format_markdown_report(week_num: int, season: str, matchups, team_performances, awards):
    """Formats the entire recap as clean, copy-pasteable Markdown."""
    lines = []
    lines.append(f"# 🏈 BFL WEEK {week_num} RECAP & POWER INDEX ({season})")
    lines.append(f"*Automated League Analysis & Manager Performance Report*\n")
    lines.append("---")
    
    # Superlatives / Awards
    lines.append("## 🏆 WEEKLY SUPERLATIVES & ROASTS\n")
    
    if awards['game_of_week']:
        g = awards['game_of_week']
        lines.append(f"* 🔥 **Game of the Week (Thriller)**: **{g['winner']['owner']}** ({max(g['away_score'], g['home_score'])}) edged out **{g['loser']['owner']}** ({min(g['away_score'], g['home_score'])}) by just **{g['margin']} pts**!")
        
    if awards['blowout_of_week']:
        b = awards['blowout_of_week']
        lines.append(f"* 🔨 **Beatdown of the Week**: **{b['winner']['owner']}** dismantled **{b['loser']['owner']}** by **{b['margin']} pts** ({max(b['away_score'], b['home_score'])} - {min(b['away_score'], b['home_score'])}).")
        
    if awards['tough_luck']:
        tl = awards['tough_luck']
        lines.append(f"* 💔 **The Tough Luck Heartbreak Award**: **{tl['team']['owner']}** put up **{tl['score']} pts** (#{tl['weekly_rank']} in the league, All-Play: {tl['all_play']}) and STILL took the L.")
        
    if awards['lucky_winner']:
        lw = awards['lucky_winner']
        lines.append(f"* 🍀 **The Golden Horseshoe (Highway Robbery)**: **{lw['team']['owner']}** scored only **{lw['score']} pts** (#{lw['weekly_rank']} in the league) but escaped with a victory against {lw['opponent_score']:.2f}.")
        
    if awards['galaxy_brain']:
        gb = awards['galaxy_brain']
        lines.append(f"* 🧠 **Galaxy Brain of the Week**: **{gb['team']['owner']}** operated at **{gb['efficiency']}% Lineup Efficiency** (Optimal: {gb['optimal']} pts, Bench Loss: {gb['bench_lost']} pts).")
        
    if awards['bench_mob']:
        bm = awards['bench_mob']
        lines.append(f"* 🤡 **Bench Mob Disaster of the Week**: **{bm['team']['owner']}** left **{bm['bench_lost']} points** rotting on the bench (Actual: {bm['score']} | Optimal: {bm['optimal']}).")
        
    if awards['worst_blunder']:
        wb = awards['worst_blunder']
        lines.append(f"* 💣 **Single Worst Bench Decision**: **{wb['owner']}** started {wb['started']['name']} ({wb['started']['pts']} pts) while {wb['benched']['name']} exploded for **{wb['benched']['pts']} pts** on the bench (+{wb['diff']} pt blunder).")
        
    lines.append("\n---\n")
    
    # Matchup Scores Table
    lines.append("## 📊 MATCHUP SCOREBOARD\n")
    lines.append("| Matchup | Score | Margin | Winner Efficiency | Loser Efficiency |")
    lines.append("|:---|:---:|:---:|:---:|:---:|")
    for m in matchups:
        w_name = m['winner']['owner'] if isinstance(m['winner'], dict) else 'Tie'
        l_name = m['loser']['owner'] if isinstance(m['loser'], dict) else 'Tie'
        w_score = max(m['away_score'], m['home_score'])
        l_score = min(m['away_score'], m['home_score'])
        w_eff = m['away_eff'] if m['away_score'] >= m['home_score'] else m['home_eff']
        l_eff = m['home_eff'] if m['away_score'] >= m['home_score'] else m['away_eff']
        lines.append(f"| **{w_name}** def. {l_name} | **{w_score:.2f}** - {l_score:.2f} | +{m['margin']:.2f} | {w_eff:.1f}% | {l_eff:.1f}% |")
        
    lines.append("\n---\n")
    
    # Manager IQ Index Table
    lines.append("## 🧠 MANAGER IQ & LINEUP EFFICIENCY INDEX\n")
    lines.append("| Rank | Manager | Actual Score | Optimal Score | Efficiency | Pts Left on Bench | All-Play Record |")
    lines.append("|:---:|:---|:---:|:---:|:---:|:---:|:---:|")
    
    sorted_by_eff = sorted(team_performances.values(), key=lambda x: x['efficiency'], reverse=True)
    for idx, p in enumerate(sorted_by_eff):
        lines.append(f"| {idx+1} | **{p['team']['owner']}** | {p['score']:.2f} | {p['optimal']:.2f} | **{p['efficiency']:.1f}%** | {p['bench_lost']:.2f} | {p['all_play']} |")
        
    lines.append("\n---\n")
    
    # Power Rankings / League Standings
    lines.append("## ⚡ ALL-PLAY POWER STANDINGS\n")
    lines.append("*True strength ranking if every team played every other team this week:*\n")
    lines.append("| Rank | Manager | Division | Week Score | All-Play | Result | Top Starter |")
    lines.append("|:---:|:---|:---:|:---:|:---:|:---:|:---|")
    
    sorted_by_score = sorted(team_performances.values(), key=lambda x: x['score'], reverse=True)
    for idx, p in enumerate(sorted_by_score):
        res = "✅ W" if p['won'] else "❌ L"
        top_p = f"{p['top_scorer']['name']} ({p['top_scorer']['pts']:.1f})" if p['top_scorer'] else "N/A"
        lines.append(f"| {idx+1} | **{p['team']['owner']}** | {p['team']['division']} | **{p['score']:.2f}** | {p['all_play']} | {res} | {top_p} |")
        
    lines.append("\n---\n")
    lines.append(f"💡 *Generated automatically via BFL Analytics Suite. Ready for group chat & Facebook posting.*")
    
    return "\n".join(lines)

def main():
    parser = argparse.ArgumentParser(description="Generate Weekly Fantasy Recap and Manager IQ Report")
    parser.add_argument("--week", type=int, default=1, help="Week number to recap (default: 1)")
    parser.add_argument("--season", default=CURRENT_YEAR, help="Season Year (default: current calendar year)")
    parser.add_argument("--league-id", default=ESPN_LEAGUE_ID, help="ESPN League ID")
    parser.add_argument("--save", action="store_true", default=True, help="Save report to Markdown file")
    args = parser.parse_args()
    
    print("\n" + "="*75)
    print(f"📊 GENERATING BFL WEEK {args.week} RECAP ({args.season})")
    print("="*75)
    
    data = fetch_espn_week_data(args.league_id, args.season, args.week, ESPN_S2, ESPN_SWID)
    matchups, team_performances, teams = analyze_week(data, args.week)
    awards = generate_weekly_awards(matchups, team_performances)
    
    report_md = format_markdown_report(args.week, args.season, matchups, team_performances, awards)
    
    # Print to console
    print(report_md)
    
    # Save to file
    if args.save:
        output_file = Path(__file__).resolve().parent / f"weekly_recap_week_{args.week}_{args.season}.md"
        with open(output_file, 'w') as f:
            f.write(report_md)
        print("\n" + "="*75)
        print(f"💾 Report saved successfully to: {output_file.name}")
        print("="*75)

if __name__ == "__main__":
    main()
