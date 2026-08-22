#!/usr/bin/env python3
"""
ESPN Fantasy Football Schedule Uploader
Automates entering a 14-week schedule from schedule_by_week.csv into ESPN Fantasy Football LM Tools.
Supports automatic authentication via .env cookies or interactive browser login.
"""

import os
import sys
import csv
import time
import json
import argparse
import requests
from pathlib import Path
from dotenv import load_dotenv

# Try importing Playwright
try:
    from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
except ImportError:
    print("❌ Playwright is not installed.")
    print("To install, run:")
    print("  pip install playwright")
    print("  playwright install chromium")
    sys.exit(1)

# Load .env from workspace or script directory
env_path = Path(__file__).resolve().parent.parent / '.env'
if env_path.exists():
    load_dotenv(dotenv_path=env_path)
else:
    load_dotenv()

ESPN_LEAGUE_ID = os.getenv("ESPN_LEAGUE_ID", "")
ESPN_S2 = os.getenv("ESPN_S2", "")
ESPN_SWID = os.getenv("ESPN_SWID", "")
SEASON_YEAR = os.getenv("SEASON_YEAR", "2025")

def get_espn_teams(league_id: str, season: str, espn_s2: str, swid: str):
    """
    Fetch current teams and owners from ESPN API to aid in matching names.
    """
    url = f"https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/{season}/segments/0/leagues/{league_id}?view=mTeam&view=mSettings&view=mMembers"
    cookies = {}
    if espn_s2 and swid:
        cookies = {"espn_s2": espn_s2, "SWID": swid}
    
    try:
        resp = requests.get(url, cookies=cookies, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            members = {m['id']: m.get('displayName', m.get('firstName', '') + ' ' + m.get('lastName', '')).strip() 
                       for m in data.get('members', [])}
            teams = []
            for t in data.get('teams', []):
                t_name = f"{t.get('location', '')} {t.get('nickname', '')}".strip()
                t_abbrev = t.get('abbrev', '')
                primary_owner_id = t.get('primaryOwner') or (t.get('owners', [None])[0])
                owner_name = members.get(primary_owner_id, "Unknown")
                teams.append({
                    'id': t['id'],
                    'name': t_name,
                    'abbrev': t_abbrev,
                    'owner': owner_name
                })
            return teams
    except Exception as e:
        print(f"⚠️ Could not automatically fetch teams from ESPN API: {e}")
    return []

def load_schedule_csv(csv_path: str):
    """Load and group matchups by week from schedule_by_week.csv."""
    if not os.path.exists(csv_path):
        raise FileNotFoundError(f"Schedule file not found: {csv_path}")
    
    schedule_by_week = {}
    with open(csv_path, 'r', newline='') as f:
        reader = csv.DictReader(f)
        for row in reader:
            w = int(row['Week'])
            if w not in schedule_by_week:
                schedule_by_week[w] = []
            schedule_by_week[w].append({
                'Away': row['Away'].strip(),
                'Home': row['Home'].strip(),
                'Type': row.get('Type', '')
            })
    return schedule_by_week

def build_team_mapping(csv_teams, espn_teams):
    """
    Create a mapping from CSV team names (e.g. 'DTM', 'Nick') to ESPN dropdown team names.
    """
    mapping = {}
    print("\n" + "="*60)
    print("📋 TEAM NAME MATCHING")
    print("="*60)
    
    if espn_teams:
        print("\nESPN League Teams Found:")
        for t in espn_teams:
            print(f"  • ID {t['id']:2d}: {t['name']} (Abbr: {t['abbrev']}, Owner: {t['owner']})")
        print("\nMatching CSV names to ESPN teams...")
        
        unmatched = []
        for code in csv_teams:
            matched = False
            code_lower = code.lower()
            for t in espn_teams:
                if (code_lower in t['name'].lower() or 
                    code_lower in t['abbrev'].lower() or 
                    code_lower in t['owner'].lower()):
                    mapping[code] = t['name']
                    print(f"  ✅ Matched '{code}' ➔ '{t['name']}' (Owner: {t['owner']})")
                    matched = True
                    break
            if not matched:
                unmatched.append(code)
        
        if unmatched:
            print(f"\n⚠️ Could not automatically match {len(unmatched)} teams: {unmatched}")
            for code in unmatched:
                print(f"\nSelect ESPN team for CSV team '{code}':")
                for idx, t in enumerate(espn_teams):
                    print(f"  [{idx+1}] {t['name']} (Owner: {t['owner']})")
                while True:
                    try:
                        choice = int(input(f"Enter number (1-{len(espn_teams)}): "))
                        if 1 <= choice <= len(espn_teams):
                            mapping[code] = espn_teams[choice-1]['name']
                            break
                    except ValueError:
                        pass
    else:
        for code in csv_teams:
            mapping[code] = code
            
    return mapping

def upload_schedule(league_id: str, season: str, schedule_by_week: dict, team_mapping: dict, dry_run: bool = False):
    """
    Uses Playwright in a visible browser window to populate the ESPN LM Schedule editor.
    """
    schedule_url = f"https://fantasy.espn.com/football/tools/schedulesettings?leagueId={league_id}&seasonId={season}"
    
    print("\n" + "="*60)
    print("🚀 LAUNCHING BROWSER AUTOMATION")
    print("="*60)
    print(f"Target URL: {schedule_url}")
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, slow_mo=100)
        context = browser.new_context(viewport={'width': 1280, 'height': 900})
        
        if ESPN_S2 and ESPN_SWID:
            print("🔑 Injecting ESPN authentication cookies from environment...")
            context.add_cookies([
                {"name": "espn_s2", "value": ESPN_S2, "domain": ".espn.com", "path": "/"},
                {"name": "SWID", "value": ESPN_SWID, "domain": ".espn.com", "path": "/"}
            ])
        
        page = context.new_page()
        print("🌐 Navigating to ESPN Schedule Editor...")
        page.goto(schedule_url, wait_until="domcontentloaded")
        
        time.sleep(3)
        if "login" in page.url.lower() or page.locator("text=Log In").is_visible():
            print("\n⚠️ Authentication required.")
            print("👉 Please log into your ESPN account in the opened browser window.")
            input("Press [Enter] in this terminal once you have logged in and can see the League Manager page...")
            page.goto(schedule_url, wait_until="domcontentloaded")
            time.sleep(3)
            
        print("✅ Connected to Schedule Editor page!")
        
        weeks = sorted(schedule_by_week.keys())
        for week_num in weeks:
            print(f"\n📅 Processing Week {week_num} ({len(schedule_by_week[week_num])} games)...")
            
            week_selector = page.locator("select[name='matchupPeriodId'], select#matchupPeriodId, .week-filter-select")
            if week_selector.is_visible():
                week_selector.select_option(value=str(week_num))
                time.sleep(1)
            else:
                week_tab = page.locator(f"button:has-text('Week {week_num}'), a:has-text('Week {week_num}')")
                if week_tab.is_visible():
                    week_tab.click()
                    time.sleep(1)
            
            matchups = schedule_by_week[week_num]
            selects = page.locator("select.schedule-team-select, table.schedule-table select, .matchup-row select")
            select_count = selects.count()
            
            if select_count >= len(matchups) * 2:
                for g_idx, game in enumerate(matchups):
                    away_name = team_mapping.get(game['Away'], game['Away'])
                    home_name = team_mapping.get(game['Home'], game['Home'])
                    
                    away_select = selects.nth(g_idx * 2)
                    home_select = selects.nth(g_idx * 2 + 1)
                    
                    print(f"  Game {g_idx+1}: {game['Away']} (@) vs {game['Home']} (vs)")
                    
                    if not dry_run:
                        away_select.select_option(label=away_name)
                        home_select.select_option(label=home_name)
            else:
                rows = page.locator("tr.Table__TR, .schedule-item, .matchupRow")
                row_count = rows.count()
                for g_idx, game in enumerate(matchups):
                    if g_idx < row_count:
                        row = rows.nth(g_idx)
                        row_selects = row.locator("select")
                        if row_selects.count() >= 2:
                            away_name = team_mapping.get(game['Away'], game['Away'])
                            home_name = team_mapping.get(game['Home'], game['Home'])
                            print(f"  Game {g_idx+1}: {away_name} @ {home_name}")
                            if not dry_run:
                                row_selects.nth(0).select_option(label=away_name)
                                row_selects.nth(1).select_option(label=home_name)
            
            if dry_run:
                print(f"  [DRY RUN] Week {week_num} previewed (no changes saved).")
            else:
                save_button = page.locator("button:has-text('Save'), button:has-text('Save Changes'), button.btn-save")
                if save_button.is_visible() and save_button.is_enabled():
                    print(f"  💾 Saving Week {week_num}...")
                    save_button.click()
                    time.sleep(2)
        
        print("\n" + "="*60)
        print("🎉 SCHEDULE UPLOAD COMPLETE!")
        print("="*60)
        print("Please review the schedule in the browser window.")
        input("Press [Enter] to close browser...")
        browser.close()

def main():
    parser = argparse.ArgumentParser(description="Upload generated schedule to ESPN Fantasy LM Tools")
    parser.add_argument("--csv", default="schedule_by_week.csv", help="Path to schedule_by_week.csv")
    parser.add_argument("--league-id", default=ESPN_LEAGUE_ID, help="ESPN League ID")
    parser.add_argument("--season", default=SEASON_YEAR, help="Fantasy Season Year")
    parser.add_argument("--dry-run", action="store_true", help="Run without saving changes to ESPN")
    args = parser.parse_args()
    
    csv_path = args.csv
    if not os.path.exists(csv_path):
        script_dir_csv = Path(__file__).resolve().parent / "schedule_by_week.csv"
        if script_dir_csv.exists():
            csv_path = str(script_dir_csv)
    
    if not os.path.exists(csv_path):
        print(f"❌ Error: Could not find '{csv_path}'.")
        print("Please run fantasy_scheduler_ortools.py first to generate schedule_by_week.csv.")
        sys.exit(1)
        
    league_id = args.league_id
    if not league_id:
        league_id = input("Enter your ESPN League ID: ").strip()
        
    schedule_by_week = load_schedule_csv(csv_path)
    all_csv_teams = sorted(list({game['Away'] for games in schedule_by_week.values() for game in games}))
    
    print(f"Loaded schedule with {len(schedule_by_week)} weeks and {len(all_csv_teams)} teams.")
    
    espn_teams = get_espn_teams(league_id, args.season, ESPN_S2, ESPN_SWID)
    team_mapping = build_team_mapping(all_csv_teams, espn_teams)
    
    upload_schedule(league_id, args.season, schedule_by_week, team_mapping, dry_run=args.dry_run)

if __name__ == "__main__":
    main()
