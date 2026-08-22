#!/usr/bin/env python3
"""
ESPN Fantasy Football Schedule Uploader
Automates updating matchups on ESPN League Schedule page (fantasy.espn.com/football/league/schedule).
Uses Playwright browser automation to click [Edit], swap teams into exact Away @ Home slots, and save.
"""

import os
import sys
import csv
import time
import json
import argparse
import requests
from datetime import datetime
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

ESPN_LEAGUE_ID = os.getenv("ESPN_LEAGUE_ID", "157057")
ESPN_S2 = os.getenv("ESPN_S2", "")
ESPN_SWID = os.getenv("ESPN_SWID", "")

# Automatically derive current year from system clock
CURRENT_YEAR = str(datetime.now().year)
SEASON_YEAR = os.getenv("SEASON_YEAR") or CURRENT_YEAR

# Automatically load division definitions from the scheduler
try:
    from FantasyScheduler.fantasy_scheduler_ortools import teams_by_div as DEFAULT_DIVISIONS
except ImportError:
    try:
        from fantasy_scheduler_ortools import teams_by_div as DEFAULT_DIVISIONS
    except ImportError:
        DEFAULT_DIVISIONS = {
            "North": ["DTM", "Thomas", "Nick", "Blake"],
            "South": ["Nael", "Saagar", "Abe", "Nasties"],
            "East":  ["Lukose", "Rej", "Samran", "Dino"],
            "West":  ["AMO", "Shooter", "Sydney", "Thor"],
        }

# Known aliases mapping script shorthand names to real 2026 owner names
CUSTOM_OWNER_ALIASES = {
    "Nasties": ["Nitesh Patel", "Nitesh", "Nasties", "Big Nasties"],
    "Shooter": ["Alex Kite", "Alex", "Kite", "Shooter", "Send Da Trade"],
    "DTM":     ["Daniel Kruszewski", "Dan", "DTM", "Dynasty Destroyers"],
    "AMO":     ["Adam Olen", "Adam", "AMO", "Green and Golden"],
    "Thomas":  ["Tommy Ehrlich", "Tommy", "Tom", "Thomas", "The Ehrly Birds"],
    "Nick":    ["Nick Christus", "Nick", "Mykonos Minotaurs"],
    "Blake":   ["Blake Whitehouse", "Blake", "Block O meets O Block"],
    "Nael":    ["Nael Ahmed", "Nael", "NMAfia"],
    "Saagar":  ["Saagar Gupta", "Saagar", "King Gupta's Army"],
    "Abe":     ["Abe Thomas", "Abe", "Crashee Bandicoot"],
    "Lukose":  ["Shawn Lukose", "Lukose", "Nilgiri Tahrs"],
    "Rej":     ["rej hoxha", "Rej", "Steve Bartman"],
    "Samran":  ["Samran Mirza", "Samran", "De'von Intervention"],
    "Dino":    ["Dino Davros", "Dino", "Taliban Gang"],
    "Sydney":  ["Sydney Miller", "Sydney", "30p Chance"],
    "Thor":    ["Shawn Ullenbrauck", "Thor", "Pat N' Pending"],
}

def get_espn_data(league_id: str, season: str, espn_s2: str, swid: str):
    """
    Fetch current teams and owners from ESPN API.
    """
    url = f"https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/{season}/segments/0/leagues/{league_id}?view=mTeam&view=mSettings&view=mMembers"
    cookies = {}
    if espn_s2 and swid:
        cookies = {"espn_s2": espn_s2, "SWID": swid}
    
    try:
        resp = requests.get(url, cookies=cookies, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            members = {}
            for m in data.get('members', []):
                disp = m.get('displayName', '')
                first = m.get('firstName', '').strip()
                last = m.get('lastName', '').strip()
                full = f"{first} {last}".strip()
                name = full if full else disp
                members[m['id']] = {
                    'name': name,
                    'displayName': disp,
                    'firstName': first,
                    'lastName': last
                }
            
            teams = []
            for t in data.get('teams', []):
                t_name = f"{t.get('location', '')} {t.get('nickname', '')}".strip()
                t_abbrev = t.get('abbrev', '')
                primary_owner_id = t.get('primaryOwner') or (t.get('owners', [None])[0])
                owner_info = members.get(primary_owner_id, {'name': 'Unknown', 'displayName': '', 'firstName': '', 'lastName': ''})
                teams.append({
                    'id': t['id'],
                    'name': t_name,
                    'abbrev': t_abbrev,
                    'owner': owner_info['name'],
                    'owner_display': owner_info['displayName'],
                    'owner_first': owner_info['firstName'],
                    'owner_last': owner_info['lastName']
                })
            return teams
    except Exception as e:
        print(f"⚠️ Could not automatically fetch league data from ESPN API: {e}")
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

def build_team_mapping(csv_teams, teams_by_div, espn_teams, cache_file="team_mapping.json"):
    """
    Create and validate mapping from script names to ESPN teams.
    """
    mapping = {}
    print("\n" + "="*75)
    print("📋 OWNER & TEAM VALIDATION")
    print("="*75)
    
    for div, members in teams_by_div.items():
        for code in members:
            aliases = CUSTOM_OWNER_ALIASES.get(code, [code])
            matched_espn = None
            
            for t in espn_teams:
                owner_full = t['owner'].lower()
                owner_first = t['owner_first'].lower()
                owner_disp = t['owner_display'].lower()
                team_name = t['name'].lower()
                team_abbr = t['abbrev'].lower()
                
                matched = any(
                    a.lower() in owner_full or
                    a.lower() in owner_first or
                    a.lower() in owner_disp or
                    (team_name and a.lower() in team_name) or
                    (team_abbr and a.lower() in team_abbr)
                    for a in aliases
                )
                if matched:
                    matched_espn = t
                    break
            
            if matched_espn:
                mapping[code] = {
                    'id': matched_espn['id'],
                    'owner': matched_espn['owner'],
                    'team_name': matched_espn['name'] or matched_espn['owner'],
                    'aliases': aliases
                }
            else:
                mapping[code] = {
                    'id': 'N/A',
                    'owner': code,
                    'team_name': code,
                    'aliases': [code]
                }

    print(f"{'Division':<10} | {'Script Code':<12} | {'Team ID':<8} | {'ESPN Owner Name':<25}")
    print("-" * 75)
    for div, members in teams_by_div.items():
        for code in members:
            info = mapping[code]
            print(f"{div:<10} | {code:<12} | ID {info['id']:<5} | {info['owner']:<25}")
    print("-" * 75)

    confirm = input("\nDoes this mapping look 100% correct? (Y/n): ").strip().lower()
    if confirm == 'n':
        print("Aborting. Please check names and re-run.")
        sys.exit(0)

    return mapping

def slot_matches_team(slot_text: str, team_code: str, team_info: dict) -> bool:
    """Check if the text in a slot corresponds to the target team."""
    text_lower = slot_text.lower()
    for a in team_info.get('aliases', [team_code]):
        if a.lower() in text_lower:
            return True
    return False

def upload_schedule(league_id: str, season: str, schedule_by_week: dict, team_mapping: dict, dry_run: bool = False):
    """
    Uses Playwright to edit matchups week by week on ESPN Schedule page.
    """
    schedule_url = f"https://fantasy.espn.com/football/league/schedule?leagueId={league_id}"
    
    print("\n" + "="*75)
    print("🚀 LAUNCHING BROWSER AUTOMATION")
    print("="*75)
    print(f"Target URL: {schedule_url}")
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, slow_mo=150)
        context = browser.new_context(viewport={'width': 1366, 'height': 900})
        
        if ESPN_S2 and ESPN_SWID:
            print("🔑 Injecting ESPN authentication cookies from environment...")
            context.add_cookies([
                {"name": "espn_s2", "value": ESPN_S2, "domain": ".espn.com", "path": "/"},
                {"name": "SWID", "value": ESPN_SWID, "domain": ".espn.com", "path": "/"}
            ])
        
        page = context.new_page()
        print("🌐 Navigating to League Schedule page...")
        page.goto(schedule_url, wait_until="domcontentloaded")
        
        print("\n" + "="*75)
        print("👤 ESPN BROWSER LOGIN & VERIFICATION")
        print("="*75)
        print("Please check the opened browser window:")
        print("  1. Log into ESPN if you see a login prompt.")
        print("  2. Make sure you can see the 'League Schedule' page with [Edit] buttons.")
        input("\n👉 Press [Enter] here once you are logged in and looking at the League Schedule page...")
        
        weeks = sorted(schedule_by_week.keys())
        for week_num in weeks:
            print(f"\n" + "="*70)
            print(f"📅 Setting Week {week_num} ({len(schedule_by_week[week_num])} matchups)...")
            print("="*70)
            
            # 1. Click the [Edit] button for this specific week
            # First ensure we are on the main schedule page
            if f"NFL Week {week_num}" not in page.title() and not page.locator(f"h1:has-text('NFL Week {week_num}'), h2:has-text('NFL Week {week_num}'), .headline:has-text('Week {week_num}')").is_visible():
                # Locate edit button for Week {week_num}
                edit_btn = page.locator(f"div:has-text('NFL Week {week_num}') button:has-text('Edit'), section:has-text('Week {week_num}') button:has-text('Edit'), button:has-text('Edit')").nth(0)
                # Find all edit buttons
                all_edits = page.locator("button:has-text('Edit'), a:has-text('Edit')")
                if all_edits.count() >= week_num:
                    edit_btn = all_edits.nth(week_num - 1)
                
                print(f"  🖱️ Clicking [Edit] for NFL Week {week_num}...")
                edit_btn.click()
                time.sleep(2)
            
            # Build target order for the 16 slots (8 games: Left=Away, Right=Home)
            matchups = schedule_by_week[week_num]
            targets = []
            for g in matchups:
                targets.append(g['Away'])
                targets.append(g['Home'])
            
            # Print planned matchups
            for idx, g in enumerate(matchups):
                away_owner = team_mapping[g['Away']]['owner']
                home_owner = team_mapping[g['Home']]['owner']
                print(f"  Game {idx+1}: {g['Away']:<8} ({away_owner:<18}) @ {g['Home']:<8} ({home_owner:<18})")
            
            if dry_run:
                print(f"  [DRY RUN] Week {week_num} previewed.")
                # Click Cancel if visible
                cancel_btn = page.locator("button:has-text('Cancel')")
                if cancel_btn.is_visible():
                    cancel_btn.click()
                    time.sleep(1)
                continue
            
            # Perform in-place swaps until all 16 slots match targets
            max_iterations = 25
            iteration = 0
            
            while iteration < max_iterations:
                iteration += 1
                
                # Fetch all 16 checkboxes and their row text
                checkboxes = page.locator("input[type='checkbox']")
                checkbox_count = checkboxes.count()
                
                if checkbox_count < 16:
                    print(f"  ⚠️ Waiting for checkboxes to load (found {checkbox_count}/16)...")
                    time.sleep(1)
                    continue
                
                # Read text associated with each slot
                rows = page.locator("table tr:has(input[type='checkbox']), tr.Table__TR:has(input[type='checkbox'])")
                current_slots = []
                
                for r_idx in range(rows.count()):
                    r = rows.nth(r_idx)
                    tds = r.locator("td")
                    if tds.count() >= 2:
                        left_text = tds.nth(0).inner_text()
                        right_text = tds.nth(1).inner_text()
                        current_slots.append({'slot': len(current_slots), 'cb': r.locator("input[type='checkbox']").nth(0), 'text': left_text})
                        current_slots.append({'slot': len(current_slots), 'cb': r.locator("input[type='checkbox']").nth(1), 'text': right_text})
                    else:
                        cbs = r.locator("input[type='checkbox']")
                        for c_idx in range(cbs.count()):
                            current_slots.append({'slot': len(current_slots), 'cb': cbs.nth(c_idx), 'text': r.inner_text()})
                
                if len(current_slots) < 16:
                    # Fallback to direct checkbox index mapping
                    current_slots = [{'slot': i, 'cb': checkboxes.nth(i), 'text': checkboxes.nth(i).locator("xpath=..").inner_text()} for i in range(16)]
                
                # Check which slot is out of place
                misplaced_slot = None
                target_team_code = None
                
                for slot_idx in range(16):
                    t_code = targets[slot_idx]
                    t_info = team_mapping[t_code]
                    if not slot_matches_team(current_slots[slot_idx]['text'], t_code, t_info):
                        misplaced_slot = slot_idx
                        target_team_code = t_code
                        break
                
                if misplaced_slot is None:
                    print(f"  ✅ All 16 teams correctly positioned for Week {week_num}!")
                    break
                
                # Find where target_team_code currently is
                target_curr_slot = None
                t_info = team_mapping[target_team_code]
                for s_idx in range(16):
                    if slot_matches_team(current_slots[s_idx]['text'], target_team_code, t_info):
                        target_curr_slot = s_idx
                        break
                
                if target_curr_slot is None:
                    print(f"  ⚠️ Could not find current position for '{target_team_code}'. Skipping auto-swap for this slot.")
                    break
                
                # Swap misplaced_slot and target_curr_slot
                slot_a = current_slots[misplaced_slot]
                slot_b = current_slots[target_curr_slot]
                
                # Uncheck any currently checked boxes first
                for s in current_slots:
                    if s['cb'].is_checked():
                        s['cb'].uncheck()
                
                # Check the two boxes to swap
                slot_a['cb'].check()
                slot_b['cb'].check()
                
                # Click [Switch Teams]
                switch_btn = page.locator("button:has-text('Switch Teams')")
                switch_btn.click()
                time.sleep(0.6)
            
            # Save Week Changes
            save_btn = page.locator("button:has-text('Save Changes')")
            if save_btn.is_visible():
                print(f"  💾 Saving changes for Week {week_num}...")
                save_btn.click()
                time.sleep(3)
        
        print("\n" + "="*75)
        print("🎉 SCHEDULE UPLOAD COMPLETE!")
        print("="*75)
        print("Please review your schedule on ESPN in the opened browser.")
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
        
    schedule_by_week = load_schedule_csv(csv_path)
    all_csv_teams = sorted(list({game['Away'] for games in schedule_by_week.values() for game in games}))
    
    print(f"Loaded schedule with {len(schedule_by_week)} weeks and {len(all_csv_teams)} teams.")
    
    espn_teams = get_espn_data(args.league_id, args.season, ESPN_S2, ESPN_SWID)
    team_mapping = build_team_mapping(all_csv_teams, DEFAULT_DIVISIONS, espn_teams)
    
    upload_schedule(args.league_id, args.season, schedule_by_week, team_mapping, dry_run=args.dry_run)

if __name__ == "__main__":
    main()
