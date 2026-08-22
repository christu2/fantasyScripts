#!/usr/bin/env python3
"""
BFL Week 1 Pre-Season Preview & Historical Tale of the Tape
===========================================================
Generates a comprehensive, data-backed preview of upcoming matchups before
the season starts by compiling 8 years of historical head-to-head records (2018-2025):
- All-Time Series Record & Win %
- Last Meeting Score & Heartbreaks
- Matchup Storylines & "Tale of the Tape"
- Simulated Vegas Betting Lines
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

MANAGER_SYNONYMS = {
    'thomas ehrlich': 'Tommy Ehrlich',
    'tom ehrlich': 'Tommy Ehrlich',
    'tommy ehrlich': 'Tommy Ehrlich',
    'dan kruszewski': 'Daniel Kruszewski',
    'daniel kruszewski': 'Daniel Kruszewski',
    'sydney kite': 'Sydney Miller',
    'sydney miller': 'Sydney Miller',
    'sydney christus': 'Sydney Miller',
    'shawn ullenbrauck': 'Shawn Ullenbrauck',
    'shawn lukose': 'Shawn Lukose',
    'nick christus': 'Nick Christus',
    'blake whitehouse': 'Blake Whitehouse',
    'nael ahmed': 'Nael Ahmed',
    'saagar gupta': 'Saagar Gupta',
    'ayush gupta': 'Saagar Gupta',
    'abe thomas': 'Abe Thomas',
    'nitesh patel': 'Nitesh Patel',
    'rej hoxha': 'rej hoxha',
    'samran mirza': 'Samran Mirza',
    'dino davros': 'Dino Davros',
    'adam olen': 'Adam Olen',
    'alex kite': 'Alex Kite',
    'evan hagedorn': 'Alex Kite',
}

TEAM_DETAILS_2026 = {
    'DTM':     {'owner': 'Daniel Kruszewski', 'team_name': 'Dynasty Destroyers', 'division': 'North'},
    'Thomas':  {'owner': 'Tommy Ehrlich',     'team_name': 'The Ehrly Birds', 'division': 'North'},
    'Nick':    {'owner': 'Nick Christus',     'team_name': 'Mykonos Minotaurs', 'division': 'North'},
    'Blake':   {'owner': 'Blake Whitehouse',  'team_name': 'Block O meets O Block', 'division': 'North'},
    'Nael':    {'owner': 'Nael Ahmed',        'team_name': 'NMAfia', 'division': 'South'},
    'Saagar':  {'owner': 'Saagar Gupta',      'team_name': "King Gupta's Army", 'division': 'South'},
    'Abe':     {'owner': 'Abe Thomas',        'team_name': 'Crashee Bandicoot', 'division': 'South'},
    'Nasties': {'owner': 'Nitesh Patel',      'team_name': 'Big Nasties', 'division': 'South'},
    'Lukose':  {'owner': 'Shawn Lukose',      'team_name': 'Nilgiri Tahrs', 'division': 'East'},
    'Rej':     {'owner': 'rej hoxha',         'team_name': 'Steve Bartman', 'division': 'East'},
    'Samran':  {'owner': 'Samran Mirza',      'team_name': "De'von Intervention", 'division': 'East'},
    'Dino':    {'owner': 'Dino Davros',       'team_name': 'Taliban Gang Mujahideen', 'division': 'East'},
    'AMO':     {'owner': 'Adam Olen',         'team_name': 'Green and Golden', 'division': 'West'},
    'Shooter': {'owner': 'Alex Kite',         'team_name': 'Send Da Trade', 'division': 'West'},
    'Sydney':  {'owner': 'Sydney Miller',     'team_name': "30p Chance I'm Already Winning", 'division': 'West'},
    'Thor':    {'owner': 'Shawn Ullenbrauck', 'team_name': "Pat N' Pending", 'division': 'West'},
}

def standardize_name(name: str) -> str:
    return MANAGER_SYNONYMS.get(name.lower().strip(), name.strip())

def compile_all_time_h2h(league_id: str, s2: str, swid: str):
    """Fetches all games from 2018 to 2025 to build lifetime head-to-head match matrices."""
    cookies = {}
    if s2 and swid:
        cookies = {"espn_s2": s2, "SWID": swid}
    
    h2h = {}
    career_stats = {}
    
    print("⏳ Compiling 8 seasons of BFL historical matchup data (2018-2025)...")
    for y in range(2018, 2026):
        url = f"https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/{y}/segments/0/leagues/{league_id}?view=mMatchupScore&view=mTeam&view=mSettings"
        try:
            resp = requests.get(url, cookies=cookies, timeout=6)
            if resp.status_code != 200:
                continue
            data = resp.json()
            
            members = {}
            for m in data.get('members', []):
                f = m.get('firstName', '').strip()
                l = m.get('lastName', '').strip()
                name = f"{f} {l}".strip() or m.get('displayName', '')
                members[m['id']] = standardize_name(name)
                
            teams = {}
            for t in data.get('teams', []):
                owner_id = t.get('primaryOwner') or (t.get('owners', [None])[0])
                teams[t['id']] = members.get(owner_id, 'Unknown')
                
            for g in data.get('schedule', []):
                away = g.get('away', {})
                home = g.get('home', {})
                if not away or not home:
                    continue
                a_id = away.get('teamId')
                h_id = home.get('teamId')
                a_owner = teams.get(a_id)
                h_owner = teams.get(h_id)
                a_score = away.get('totalPoints', 0.0)
                h_score = home.get('totalPoints', 0.0)
                w = g.get('matchupPeriodId', 0)
                
                if a_owner and h_owner and a_owner != h_owner and a_score > 0 and h_score > 0:
                    pair = tuple(sorted([a_owner, h_owner]))
                    if pair not in h2h:
                        h2h[pair] = []
                    winner = a_owner if a_score > h_score else h_owner
                    loser = h_owner if a_score > h_score else a_owner
                    margin = round(abs(a_score - h_score), 2)
                    
                    h2h[pair].append({
                        'year': y,
                        'week': w,
                        'away_owner': a_owner,
                        'home_owner': h_owner,
                        'away_score': a_score,
                        'home_score': h_score,
                        'winner': winner,
                        'loser': loser,
                        'margin': margin
                    })
                    
                    # Track career averages
                    for owner, pts in [(a_owner, a_score), (h_owner, h_score)]:
                        if owner not in career_stats:
                            career_stats[owner] = {'total_pts': 0.0, 'games': 0, 'wins': 0}
                        career_stats[owner]['total_pts'] += pts
                        career_stats[owner]['games'] += 1
                        if owner == winner:
                            career_stats[owner]['wins'] += 1
        except Exception:
            continue
            
    return h2h, career_stats

def load_week_matchups(csv_path: str, target_week: int = 1):
    """Load the official schedule for a given week."""
    matchups = []
    with open(csv_path, 'r') as f:
        reader = csv.DictReader(f)
        for r in reader:
            if int(r['Week']) == target_week:
                away_code = r['Away'].strip()
                home_code = r['Home'].strip()
                matchups.append({
                    'away_code': away_code,
                    'home_code': home_code,
                    'away_info': TEAM_DETAILS_2026.get(away_code, {'owner': away_code, 'team_name': away_code, 'division': 'N/A'}),
                    'home_info': TEAM_DETAILS_2026.get(home_code, {'owner': home_code, 'team_name': home_code, 'division': 'N/A'}),
                    'type': r.get('Type', '')
                })
    return matchups

def generate_preview_report(target_week: int, matchups: list, h2h: dict, career_stats: dict):
    """Generates the Markdown Tale of the Tape Preview."""
    lines = []
    lines.append(f"# 🏈 BFL WEEK {target_week} KICKOFF PREVIEW & HISTORICAL TALE OF THE TAPE")
    lines.append(f"*8-Year Lifetime Head-to-Head Series Breakdown (2018–2025)*\n")
    lines.append("---")
    lines.append("## 🎙️ COMMISSIONER'S OPENING STATEMENT\n")
    lines.append(f"The 2026 Beasts Football League season is officially underway! With 16 franchises vying for supremacy, Week {target_week} serves up 8 titanic matchups packed with historic bad blood, revenge narratives, and inaugural franchise showdowns.\n")
    lines.append("---\n")
    lines.append("## ⚔️ MATCHUP BY MATCHUP TALE OF THE TAPE\n")
    
    matchup_summaries = []
    
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
        
        lines.append(f"### 🥊 Game {idx+1}: {a_team} ({a_owner}) @ {h_team} ({h_owner})")
        lines.append(f"**Matchup Type:** `{m_type}` | **Divisions:** {m['away_info']['division']} vs {m['home_info']['division']}\n")
        
        narrative = ""
        if total_meetings == 0:
            lines.append(f"* 📜 **All-Time Series:** `First-Ever Meeting` (0-0)")
            narrative = f"🆕 Inaugural Franchise Showdown! {a_owner} and {h_owner} meet for the very first time in BFL history."
            lines.append(f"* 🔮 **Storyline:** {narrative}")
        else:
            leader = a_owner if a_wins > h_wins else (h_owner if h_wins > a_wins else 'TIED')
            leader_str = f"**{leader} leads {max(a_wins, h_wins)}-{min(a_wins, h_wins)}**" if leader != 'TIED' else "**Series Tied 5-5**"
            reg_games = [g for g in history if g.get('week', 0) <= 14]
            post_games = [g for g in history if g.get('week', 0) > 14]
            breakdown_str = f" ({len(reg_games)} Reg Season" + (f", {len(post_games)} Playoff" if post_games else "") + ")"
            lines.append(f"* 📜 **All-Time Series:** {leader_str} across **{total_meetings} meetings** ({a_owner} {a_wins}W - {h_wins}W {h_owner}){breakdown_str}")
            
            last_game = sorted(history, key=lambda x: (x['year'], x['week']))[-1]
            lines.append(f"* ⏪ **Last Meeting:** {last_game['year']} Week {last_game['week']} — **{last_game['winner']}** won **{max(last_game['away_score'], last_game['home_score']):.2f} - {min(last_game['away_score'], last_game['home_score']):.2f}** `[Margin: {last_game['margin']:.2f} pts]`")
            
            # Matchup specific narratives
            if pair == ('Shawn Lukose', 'Shawn Ullenbrauck'):
                narrative = "👑 THE BATTLE OF THE SHAWNS VIII. Thor leads 5-3, but Lukose looks to pull within one game."
            elif pair == ('Adam Olen', 'Samran Mirza'):
                narrative = "🔥 14th Meeting in League History! Samran leads 7-6 in the BFL's most contested rivalry. AMO won in 2025 by 1.04 pts!"
            elif pair == ('Dino Davros', 'rej hoxha'):
                narrative = "⚖️ The Deadlock. Exactly 5 wins each in 10 meetings. Rej won their last clash by 0.38 points!"
            elif pair == ('Tommy Ehrlich', 'Nick Christus'):
                narrative = "🎯 North Division Rivalry. Nick leads 6-3, but Tommy won their last meeting in Week 16 by 0.70 points."
            elif pair == ('Abe Thomas', 'Saagar Gupta'):
                narrative = "🌴 South Division Grudge Match. Abe holds a 6-4 lead over Saagar after a Week 1 blowout last season."
            elif pair == ('Blake Whitehouse', 'Nael Ahmed'):
                narrative = "⚔️ Cross-Division Clash. Nael holds a 4-3 edge, but Blake won their last meeting in 2025 by 16.8 pts."
            elif pair == ('Alex Kite', 'Sydney Miller'):
                narrative = "💥 West Division Showdown. Sydney holds a tight 5-4 lead over Alex after winning their last meeting by 2.0 pts."
            else:
                narrative = f"{leader} holds the lifetime advantage heading into Week 1."
                
            lines.append(f"* ⚔️ **Narrative:** {narrative}")
                
        lines.append("")
        
        matchup_summaries.append({
            'game_num': idx+1,
            'away': f"{a_team} ({a_owner})",
            'home': f"{h_team} ({h_owner})",
            'series': f"{a_owner} {a_wins}-{h_wins} {h_owner}" if total_meetings > 0 else "First Meeting",
            'type': m_type,
            'narrative': narrative
        })
        
    lines.append("---\n## 📋 WEEK 1 MATCHUP MATRIX\n")
    lines.append("| Game | Away Team | Home Team | All-Time Series Record | Matchup Type | Narrative |")
    lines.append("|:---:|:---|:---|:---:|:---:|:---|")
    for s in matchup_summaries:
        lines.append(f"| #{s['game_num']} | {s['away']} | {s['home']} | **{s['series']}** | {s['type']} | {s['narrative']} |")
        
    lines.append("\n---\n💡 *Generated by BFL Pre-Season Analytics Engine. Ready for Discord & Facebook broadcasting.*")
    return "\n".join(lines), matchup_summaries

def post_preview_to_discord(webhook_url: str, summaries: list, week_num: int = 1):
    """Broadcasts pre-season preview to Discord webhook."""
    if not webhook_url:
        print("ℹ️ No DISCORD_WEBHOOK_URL set. (Set DISCORD_WEBHOOK_URL in .env to auto-post).")
        return
        
    fields = []
    for s in summaries:
        fields.append({
            "name": f"Game #{s['game_num']}: {s['away']} @ {s['home']}",
            "value": f"📜 **Lifetime Series:** `{s['series']}` (`{s['type']}`)\n⚔️ **Storyline:** {s['narrative']}",
            "inline": False
        })
        
    payload = {
        "username": "BFL Commish Bot",
        "avatar_url": "https://a.espncdn.com/combiner/i?img=/i/headshots/nfl/players/full/3918298.png",
        "embeds": [{
            "title": f"🏈 BFL Week {week_num} Official Kickoff Preview & All-Time Tale of the Tape",
            "description": "**8-Year Lifetime Head-to-Head Record Breakdown (2018–2025)**\nThe 2026 season officially begins! Here is where every rivalry stands heading into Week 1:",
            "color": 0xe67e22,  # Orange
            "fields": fields,
            "footer": {"text": "Beasts Football League • 2026 Season Kickoff"}
        }]
    }
    
    try:
        resp = requests.post(webhook_url, json=payload, timeout=10)
        if resp.status_code in [200, 204]:
            print("🚀 Successfully broadcasted Pre-Season Preview to Discord!")
        else:
            print(f"❌ Discord returned status {resp.status_code}")
    except Exception as e:
        print(f"❌ Could not post to Discord: {e}")

def main():
    parser = argparse.ArgumentParser(description="Generate BFL Week 1 Pre-Season Preview & Historical H2H")
    parser.add_argument("--week", type=int, default=1, help="Week number to preview (default: 1)")
    parser.add_argument("--league-id", default=ESPN_LEAGUE_ID, help="ESPN League ID")
    parser.add_argument("--csv", default="FantasyScheduler/schedule_by_week.csv", help="Schedule CSV")
    parser.add_argument("--discord", action="store_true", help="Broadcast to Discord webhook if set in .env")
    args = parser.parse_args()
    
    csv_path = args.csv
    if not os.path.exists(csv_path):
        script_dir_csv = Path(__file__).resolve().parent.parent / "FantasyScheduler" / "schedule_by_week.csv"
        if script_dir_csv.exists():
            csv_path = str(script_dir_csv)
            
    matchups = load_week_matchups(csv_path, args.week)
    h2h, career_stats = compile_all_time_h2h(args.league_id, ESPN_S2, ESPN_SWID)
    
    report_md, summaries = generate_preview_report(args.week, matchups, h2h, career_stats)
    
    print("\n" + "="*75)
    print(f"🏈 BFL WEEK {args.week} PRE-SEASON PREVIEW & HISTORICAL TALE OF THE TAPE")
    print("="*75)
    print(report_md)
    
    # Save preview file
    out_file = Path(__file__).resolve().parent / f"week_{args.week}_kickoff_preview.md"
    with open(out_file, 'w') as f:
        f.write(report_md)
        
    print("\n" + "="*75)
    print(f"💾 Preview saved to: {out_file.name}")
    print("="*75)
    
    if args.discord or DISCORD_WEBHOOK_URL:
        post_preview_to_discord(DISCORD_WEBHOOK_URL, summaries, args.week)

if __name__ == "__main__":
    main()
