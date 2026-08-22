#!/usr/bin/env python3
"""
BFL Week-by-Week Matchup Preview & Historical Tale of the Tape (2008-2026)
==========================================================================
Generates a comprehensive preview of upcoming matchups by compiling
18+ seasons of historical head-to-head records (2008-2026) from ESPN API:
- Full co-owner resolution across all seasons
- Strict filtering of TRUE Championship Playoffs (excluding consolation/loser's bracket)
- Dynamic in-season updates (incorporates completed weeks of the current season)
- In-season rematch alerts and revenge narratives
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
CURRENT_YEAR = os.getenv("SEASON_YEAR") or str(datetime.now().year)

MANAGER_SYNONYMS = {
    'thomas ehrlich': 'Tommy Ehrlich',
    'tom ehrlich': 'Tommy Ehrlich',
    'tommy ehrlich': 'Tommy Ehrlich',
    'dan kruszewski': 'Daniel Kruszewski',
    'daniel kruszewski': 'Daniel Kruszewski',
    'ali bhujwala': 'Daniel Kruszewski',
    'sydney kite': 'Sydney Miller',
    'sydney miller': 'Sydney Miller',
    'sydney christus': 'Sydney Miller',
    'emelie lovasko': 'Emelie Lovasko',
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
    'ryan olen': 'Ryan Olen',
    'alex kite': 'Alex Kite',
    'evan hagedorn': 'Alex Kite',
    'matt rosato': 'Austin Russell',
    'bubba franks': 'Austin Russell',
    'austin russell': 'Austin Russell',
    'alexandra christus': 'Alex Christus',
    'georgia batman': 'Georgia Christus',
    'georgia christus': 'Georgia Christus',
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
    if not name:
        return 'Unknown'
    return MANAGER_SYNONYMS.get(name.lower().strip(), name.strip().title())

def compile_all_time_h2h(league_id: str, s2: str, swid: str, current_season: int = 2026, up_to_week: int = 1):
    """
    Fetches all historical seasons (2008 to current) via ESPN leagueHistory and season APIs.
    Distinguishes True Championship Playoffs from Consolation / Regular Season.
    """
    cookies = {}
    if s2 and swid:
        cookies = {"espn_s2": s2, "SWID": swid}
    
    h2h = {}
    career_stats = {}
    
    print(f"⏳ Compiling 18+ seasons of BFL historical matchup data (2008-{current_season})...")
    
    # 1. Historical past years via leagueHistory (2008 to current_season - 1)
    for y in range(2008, current_season):
        url = f"https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/leagueHistory/{league_id}?seasonId={y}&view=mMatchupScore&view=mTeam&view=mSettings&view=mMembers"
        try:
            resp = requests.get(url, cookies=cookies, timeout=8)
            if resp.status_code != 200:
                continue
            raw = resp.json()
            data = raw[0] if isinstance(raw, list) and len(raw) > 0 else raw
            _process_season_games(data, y, h2h, career_stats)
        except Exception:
            continue
            
    # 2. Current season in-progress games (weeks 1 to up_to_week - 1)
    if up_to_week > 1:
        url_curr = f"https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/{current_season}/segments/0/leagues/{league_id}?view=mMatchupScore&view=mTeam&view=mSettings&view=mMembers"
        try:
            resp = requests.get(url_curr, cookies=cookies, timeout=8)
            if resp.status_code == 200:
                data = resp.json()
                _process_season_games(data, current_season, h2h, career_stats, max_week=up_to_week - 1)
        except Exception:
            pass
            
    return h2h, career_stats

def _process_season_games(data, year: int, h2h: dict, career_stats: dict, max_week: int = 99):
    """Parses season data and attributes games across co-owners with playoff tier classification."""
    members = {}
    for m in data.get('members', []):
        f = m.get('firstName', '').strip()
        l = m.get('lastName', '').strip()
        full = f"{f} {l}".strip()
        disp = m.get('displayName', '').strip()
        members[m['id']] = standardize_name(full if full else disp)
        
    teams = {}
    for t in data.get('teams', []):
        tid = t['id']
        owner_ids = t.get('owners', [])
        if not owner_ids and t.get('primaryOwner'):
            owner_ids = [t['primaryOwner']]
            
        owners = set()
        for oid in owner_ids:
            if oid in members:
                owners.add(members[oid])
                
        if not owners:
            tname = f"{t.get('location', '')} {t.get('nickname', '')}".strip()
            if tname:
                owners.add(standardize_name(tname))
            else:
                owners.add(f"Team {tid}")
                
        teams[tid] = list(owners)
        
    for g in data.get('schedule', []):
        w = g.get('matchupPeriodId', 0)
        if w > max_week:
            continue
            
        away = g.get('away', {})
        home = g.get('home', {})
        if not away or not home:
            continue
            
        a_id = away.get('teamId')
        h_id = home.get('teamId')
        a_owners = teams.get(a_id, [])
        h_owners = teams.get(h_id, [])
        a_score = away.get('totalPoints', 0.0)
        h_score = home.get('totalPoints', 0.0)
        tier = g.get('playoffTierType', 'NONE')
        
        # True Championship Playoff vs Consolation vs Regular Season
        is_champ_playoff = (tier == 'WINNERS_BRACKET')
        is_consolation = ('CONSOLATION' in str(tier) or 'LOSER' in str(tier))
        is_regular = (tier == 'NONE' and w <= 14)
        
        if a_score > 0 and h_score > 0 and a_owners and h_owners:
            margin = round(abs(a_score - h_score), 2)
            
            for own_a in a_owners:
                for own_b in h_owners:
                    if own_a == own_b:
                        continue
                    pair = tuple(sorted([own_a, own_b]))
                    if pair not in h2h:
                        h2h[pair] = []
                        
                    winner = own_a if a_score > h_score else own_b
                    loser = own_b if a_score > h_score else own_a
                    
                    h2h[pair].append({
                        'year': year,
                        'week': w,
                        'owner_a': own_a,
                        'owner_b': own_b,
                        'score_a': a_score,
                        'score_b': h_score,
                        'winner': winner,
                        'loser': loser,
                        'margin': margin,
                        'is_champ_playoff': is_champ_playoff,
                        'is_consolation': is_consolation,
                        'is_regular': is_regular
                    })
                    
                    for own, pts, is_win in [(own_a, a_score, a_score > h_score), (own_b, h_score, h_score > a_score)]:
                        if own not in career_stats:
                            career_stats[own] = {'total_pts': 0.0, 'games': 0, 'wins': 0}
                        career_stats[own]['total_pts'] += pts
                        career_stats[own]['games'] += 1
                        if is_win:
                            career_stats[own]['wins'] += 1

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

def generate_preview_report(target_week: int, target_season: int, matchups: list, h2h: dict, career_stats: dict):
    """Generates the Markdown Tale of the Tape Preview."""
    lines = []
    lines.append(f"# 🏈 BFL WEEK {target_week} PREVIEW & HISTORICAL TALE OF THE TAPE ({target_season})")
    lines.append(f"*Complete 18+ Year Franchise History & Head-to-Head Records (2008–{target_season})*\n")
    lines.append("---")
    lines.append("## 🎙️ COMMISSIONER'S OPENING STATEMENT\n")
    if target_week == 1:
        lines.append(f"The {target_season} Beasts Football League season is officially underway! With 18 seasons of rich history since our 2008 inception, Week {target_week} serves up 8 titanic clashes loaded with championship pedigree, long-standing grudges, and brand-new franchise chapters.\n")
    else:
        lines.append(f"Welcome to Week {target_week} of the {target_season} BFL campaign! Rivalries heat up as division rematches, playoff seeding battles, and historic cross-division clashes take center stage.\n")
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
        
        reg_games = [g for g in history if g.get('is_regular')]
        champ_playoffs = [g for g in history if g.get('is_champ_playoff')]
        consolation_games = [g for g in history if g.get('is_consolation')]
        
        a_champ_wins = len([g for g in champ_playoffs if g['winner'] == a_owner])
        h_champ_wins = len([g for g in champ_playoffs if g['winner'] == h_owner])
        
        lines.append(f"### 🥊 Game {idx+1}: {a_team} ({a_owner}) @ {h_team} ({h_owner})")
        lines.append(f"**Matchup Type:** `{m_type}` | **Divisions:** {m['away_info']['division']} vs {m['home_info']['division']}\n")
        
        narrative = ""
        if total_meetings == 0:
            lines.append(f"* 📜 **All-Time Series:** `First-Ever Meeting` (0-0)")
            narrative = f"🆕 Inaugural Showdown! {a_owner} and {h_owner} meet for the very first time in franchise history."
            lines.append(f"* 🔮 **Storyline:** {narrative}")
        else:
            leader = a_owner if a_wins > h_wins else (h_owner if h_wins > a_wins else 'TIED')
            leader_str = f"**{leader} leads {max(a_wins, h_wins)}-{min(a_wins, h_wins)}**" if leader != 'TIED' else f"**Series Deadlocked {a_wins}-{h_wins}**"
            
            # Format breakdown string
            parts = []
            if reg_games:
                parts.append(f"{len(reg_games)} Reg Season")
            if champ_playoffs:
                parts.append(f"🏆 {len(champ_playoffs)} Championship Playoff")
            if consolation_games:
                parts.append(f"{len(consolation_games)} Consolation")
            breakdown_str = f" ({', '.join(parts)})" if parts else ""
            
            lines.append(f"* 📜 **All-Time Series:** {leader_str} across **{total_meetings} lifetime meetings** ({a_owner} {a_wins}W - {h_wins}W {h_owner}){breakdown_str}")
            
            if champ_playoffs:
                champ_leader = a_owner if a_champ_wins > h_champ_wins else (h_owner if h_champ_wins > a_champ_wins else 'Tied')
                lines.append(f"* 🏆 **Championship Playoff History:** {champ_leader} {max(a_champ_wins, h_champ_wins)}-{min(a_champ_wins, h_champ_wins)} ({len(champ_playoffs)} meetings)")
            
            last_game = sorted(history, key=lambda x: (x['year'], x['week']))[-1]
            last_game_label = "Championship Playoffs" if last_game.get('is_champ_playoff') else ("Consolation" if last_game.get('is_consolation') else "Regular Season")
            lines.append(f"* ⏪ **Last Meeting:** {last_game['year']} Week {last_game['week']} ({last_game_label}) — **{last_game['winner']}** won **{max(last_game['score_a'], last_game['score_b']):.2f} - {min(last_game['score_a'], last_game['score_b']):.2f}** `[Margin: {last_game['margin']:.2f} pts]`")
            
            # Dynamic narratives with in-season rematch awareness
            is_in_season_rematch = (last_game['year'] == target_season and target_week > 1)
            
            if is_in_season_rematch:
                rev_owner = a_owner if last_game['winner'] == h_owner else h_owner
                narrative = f"🔁 IN-SEASON REMATCH! {last_game['winner']} won their Week {last_game['week']} clash by {last_game['margin']:.2f} pts. {rev_owner} seeks immediate revenge!"
            elif pair == ('Shawn Lukose', 'Shawn Ullenbrauck'):
                lead_str = f"Lukose leads {max(a_wins, h_wins)}-{min(a_wins, h_wins)}" if a_wins > h_wins else f"Thor leads {max(a_wins, h_wins)}-{min(a_wins, h_wins)}"
                narrative = f"👑 THE BATTLE OF THE SHAWNS. {lead_str} across {total_meetings} lifetime meetings since 2008! Thor won their last clash in {last_game['year']} by {last_game['margin']:.2f} pts."
            elif pair == ('Adam Olen', 'Samran Mirza'):
                if a_wins == h_wins:
                    narrative = f"🔥 Deadlocked at {a_wins}-{h_wins}! Heading into their {total_meetings + 1}th all-time clash, AMO won their 2025 meeting by a razor-thin {last_game['margin']:.2f} pts."
                else:
                    lead_name = a_owner if a_wins > h_wins else h_owner
                    narrative = f"🔥 {lead_name} leads {max(a_wins, h_wins)}-{min(a_wins, h_wins)} in {total_meetings} meetings. Their 2025 clash was decided by just {last_game['margin']:.2f} pts!"
            elif pair == ('Dino Davros', 'rej hoxha'):
                lead_name = a_owner if a_wins > h_wins else h_owner
                narrative = f"⚖️ Century Rivalry. {lead_name} leads {max(a_wins, h_wins)}-{min(a_wins, h_wins)} in {total_meetings} clashes since 2008. Rej won their last meeting by {last_game['margin']:.2f} pts!"
            elif pair == ('Tommy Ehrlich', 'Nick Christus'):
                lead_name = a_owner if a_wins > h_wins else h_owner
                narrative = f"🎯 North Division Showdown. {lead_name} commands a {max(a_wins, h_wins)}-{min(a_wins, h_wins)} series lead across {total_meetings} meetings since 2008."
            elif pair == ('Abe Thomas', 'Saagar Gupta'):
                lead_name = a_owner if a_wins > h_wins else h_owner
                narrative = f"🌴 South Division Grudge Match. {lead_name} leads {max(a_wins, h_wins)}-{min(a_wins, h_wins)} across {total_meetings} lifetime meetings since 2008."
            elif pair == ('Daniel Kruszewski', 'Nitesh Patel'):
                lead_name = a_owner if a_wins > h_wins else h_owner
                narrative = f"⚡ Nitesh's first game as solo owner of Big Nasties against Dan Kruszewski (Dan leads {max(a_wins, h_wins)}-{min(a_wins, h_wins)} from past co-owner matchups)."
            elif pair == ('Blake Whitehouse', 'Nael Ahmed'):
                lead_name = a_owner if a_wins > h_wins else h_owner
                champ_note = f" (including {len(champ_playoffs)} championship playoff clashes)" if champ_playoffs else ""
                narrative = f"⚔️ Cross-Division Clash. {lead_name} holds a {max(a_wins, h_wins)}-{min(a_wins, h_wins)} edge across {total_meetings} meetings{champ_note}."
            elif pair == ('Alex Kite', 'Sydney Miller'):
                lead_name = a_owner if a_wins > h_wins else h_owner
                narrative = f"💥 West Division Showdown. {lead_name} holds a tight {max(a_wins, h_wins)}-{min(a_wins, h_wins)} edge in {total_meetings} meetings."
            else:
                lead_name = a_owner if a_wins > h_wins else h_owner
                narrative = f"{lead_name} leads {max(a_wins, h_wins)}-{min(a_wins, h_wins)} across {total_meetings} lifetime meetings since 2008."
                
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
        
    lines.append("---\n## 📋 MATCHUP MATRIX\n")
    lines.append("| Game | Away Team | Home Team | All-Time Series Record | Matchup Type | Key Storyline |")
    lines.append("|:---:|:---|:---|:---:|:---:|:---|")
    for s in matchup_summaries:
        lines.append(f"| #{s['game_num']} | {s['away']} | {s['home']} | **{s['series']}** | {s['type']} | {s['narrative']} |")
        
    lines.append(f"\n---\n💡 *Generated by BFL 18+ Year Analytics Engine (2008–{target_season}). Ready for Discord & Facebook broadcasting.*")
    return "\n".join(lines), matchup_summaries

def post_preview_to_discord(webhook_url: str, summaries: list, week_num: int = 1, season: int = 2026):
    """Broadcasts historical preview to Discord webhook with rich narrative cards."""
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
            "title": f"🏈 BFL Week {week_num} Official Preview & 18-Year Tale of the Tape ({season})",
            "description": f"**Complete 18-Year Head-to-Head Franchise History (2008–{season})**\nDistinguishing true championship playoff battles from regular season meetings:",
            "color": 0xe67e22,  # Orange
            "fields": fields,
            "footer": {"text": f"Beasts Football League • Week {week_num} Preview"}
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
    parser = argparse.ArgumentParser(description="Generate BFL Week-by-Week Matchup Preview & Historical H2H")
    parser.add_argument("--week", type=int, default=1, help="Week number to preview (default: 1)")
    parser.add_argument("--season", type=int, default=int(CURRENT_YEAR), help="Season year (default: current year)")
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
    h2h, career_stats = compile_all_time_h2h(args.league_id, ESPN_S2, ESPN_SWID, current_season=args.season, up_to_week=args.week)
    
    report_md, summaries = generate_preview_report(args.week, args.season, matchups, h2h, career_stats)
    
    print("\n" + "="*75)
    print(f"🏈 BFL WEEK {args.week} PREVIEW & 18-YEAR TALE OF THE TAPE ({args.season})")
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
        post_preview_to_discord(DISCORD_WEBHOOK_URL, summaries, args.week, args.season)

if __name__ == "__main__":
    main()
