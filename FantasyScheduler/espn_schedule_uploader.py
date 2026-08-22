#!/usr/bin/env python3
"""
ESPN Fantasy Football Schedule Uploader
Automates entering a 14-week schedule from schedule_by_week.csv into ESPN Fantasy Football LM Tools.
Supports division-aware owner matching, interactive mapping confirmation, and Playwright browser automation.
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

# Automatically derive current year from the system clock
CURRENT_YEAR = str(datetime.now().year)
SEASON_YEAR = os.getenv("SEASON_YEAR") or CURRENT_YEAR

# Automatically load division definitions directly from the scheduler script
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
    "Nasties": ["Nitesh Patel", "Nitesh", "Nasties"],
    "Shooter": ["Alex Kite", "Alex", "Kite", "Shooter"],
    "DTM":     ["Daniel Kruszewski", "Dan", "DTM"],
    "AMO":     ["Adam Olen", "Adam", "AMO"],
    "Thomas":  ["Tommy Ehrlich", "Tommy", "Tom", "Thomas"],
    "Nick":    ["Nick Christus", "Nick"],
    "Blake":   ["Blake Whitehouse", "Blake"],
    "Nael":    ["Nael Ahmed", "Nael"],
    "Saagar":  ["Saagar Gupta", "Saagar"],
    "Abe":     ["Abe Thomas", "Abe"],
    "Lukose":  ["Shawn Lukose", "Lukose"],
    "Rej":     ["rej hoxha", "Rej"],
    "Samran":  ["Samran Mirza", "Samran"],
    "Dino":    ["Dino Davros", "Dino"],
    "Sydney":  ["Sydney Miller", "Sydney"],
    "Thor":    ["Shawn Ullenbrauck", "Thor"],
}

def get_espn_data(league_id: str, season: str, espn_s2: str, swid: str):
    """
    Fetch current teams, owners, and division setup from ESPN API.
    """
    url = f"https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/{season}/segments/0/leagues/{league_id}?view=mTeam&view=mSettings&view=mMembers"
    cookies = {}
    if espn_s2 and swid:
        cookies = {"espn_s2": espn_s2, "SWID": swid}
    
    try:
        resp = requests.get(url, cookies=cookies, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            
            # Map division IDs to names
            divisions = {}
            for d in data.get('settings', {}).get('scheduleSettings', {}).get('divisions', []):
                divisions[d['id']] = d.get('name', f"Division {d['id']}")
            
            # Map owner IDs to owner full names and usernames
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
                div_id = t.get('divisionId', 0)
                div_name = divisions.get(div_id, f"Division {div_id}")
                
                teams.append({
                    'id': t['id'],
                    'name': t_name,
                    'abbrev': t_abbrev,
                    'division_id': div_id,
                    'division_name': div_name,
                    'owner': owner_info['name'],
                    'owner_display': owner_info['displayName'],
                    'owner_first': owner_info['firstName'],
                    'owner_last': owner_info['lastName']
                })
            return teams, divisions
    except Exception as e:
        print(f"⚠️ Could not automatically fetch league data from ESPN API: {e}")
    return [], {}

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
    Create and validate mapping from script names to ESPN teams using Division & Owner Name matching.
    """
    mapping_path = Path(__file__).resolve().parent / cache_file
    if mapping_path.exists():
        try:
            with open(mapping_path, 'r') as f:
                saved_mapping = json.load(f)
            # Ensure saved mapping is rich dict format
            if saved_mapping and isinstance(list(saved_mapping.values())[0], dict):
                print(f"\n📂 Found saved team mapping in {cache_file}.")
                use_saved = input("Do you want to use the saved mapping? (Y/n): ").strip().lower()
                if use_saved != 'n':
                    return saved_mapping
        except Exception:
            pass

    mapping = {}
    print("\n" + "="*75)
    print("📋 DIVISION & OWNER NAME VALIDATION")
    print("="*75)
    
    if not espn_teams:
        print("⚠️ No ESPN teams retrieved from API. Defaulting to exact names.")
        for code in csv_teams:
            mapping[code] = {'id': code, 'owner': code, 'name': code}
        return mapping

    # Build division lookup for CSV teams
    csv_team_div = {}
    for div, members in teams_by_div.items():
        for m in members:
            csv_team_div[m] = div

    unmatched_csv_by_div = {div: [] for div in teams_by_div}
    unmatched_espn_by_div = {div: [] for div in teams_by_div}

    # Group ESPN teams by division matching
    for espn_t in espn_teams:
        div_name = espn_t['division_name']
        matched_div_key = next((d for d in teams_by_div if d.lower() in div_name.lower()), None)
        if matched_div_key:
            unmatched_espn_by_div[matched_div_key].append(espn_t)

    # 1. Match within each division against Owner Names & Aliases
    for div, div_members in teams_by_div.items():
        espn_div_teams = unmatched_espn_by_div.get(div, [])
        for code in div_members:
            aliases = CUSTOM_OWNER_ALIASES.get(code, [code])
            matched_espn = None
            
            for t in espn_div_teams:
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
                    'name': matched_espn['name'] or matched_espn['owner']
                }
                espn_div_teams.remove(matched_espn)
            else:
                unmatched_csv_by_div[div].append(code)

    # 2. Handle manual clarification for any unmatched teams per division
    for div, codes in unmatched_csv_by_div.items():
        remaining_espn = unmatched_espn_by_div.get(div, [])
        for code in codes:
            print(f"\n❓ Manual match needed for '{code}' in the [{div}] Division:")
            if remaining_espn:
                for idx, t in enumerate(remaining_espn):
                    print(f"  [{idx+1}] ID {t['id']:2d}: Owner: {t['owner']} ({t['owner_display']})")
                while True:
                    try:
                        choice = int(input(f"Select ESPN team for '{code}' (1-{len(remaining_espn)}): "))
                        if 1 <= choice <= len(remaining_espn):
                            selected = remaining_espn.pop(choice-1)
                            mapping[code] = {
                                'id': selected['id'],
                                'owner': selected['owner'],
                                'name': selected['name'] or selected['owner']
                            }
                            break
                    except ValueError:
                        pass
            else:
                for idx, t in enumerate(espn_teams):
                    print(f"  [{idx+1}] ID {t['id']:2d}: [{t['division_name']}] Owner: {t['owner']}")
                while True:
                    try:
                        choice = int(input(f"Select ESPN team for '{code}' (1-{len(espn_teams)}): "))
                        if 1 <= choice <= len(espn_teams):
                            selected = espn_teams[choice-1]
                            mapping[code] = {
                                'id': selected['id'],
                                'owner': selected['owner'],
                                'name': selected['name'] or selected['owner']
                            }
                            break
                    except ValueError:
                        pass

    # 3. Present full validation table for confirmation
    print("\n" + "="*75)
    print("📊 CONFIRM TEAM & OWNER MAPPINGS BEFORE PROCEEDING")
    print("="*75)
    print(f"{'Division':<10} | {'Script Code':<12} | {'Team ID':<8} | {'ESPN Owner Name':<25}")
    print("-" * 75)
    
    for div, members in teams_by_div.items():
        for code in members:
            info = mapping.get(code, {'id': 'N/A', 'owner': 'MISSING', 'name': 'MISSING'})
            print(f"{div:<10} | {code:<12} | ID {info['id']:<5} | {info['owner']:<25}")
    print("-" * 75)

    confirm = input("\nDoes this mapping look 100% correct? (Y/n): ").strip().lower()
    if confirm == 'n':
        print("Aborting. Please check names and re-run.")
        sys.exit(0)

    # Save mapping for reuse
    try:
        with open(mapping_path, 'w') as f:
            json.dump(mapping, f, indent=2)
        print(f"💾 Mapping saved to {cache_file} for future runs.")
    except Exception:
        pass

    return mapping

def upload_schedule(league_id: str, season: str, schedule_by_week: dict, team_mapping: dict, dry_run: bool = False):
    """
    Uses Playwright in a visible browser window to populate the ESPN LM Schedule editor.
    """
    schedule_url = f"https://fantasy.espn.com/football/tools/schedulesettings?leagueId={league_id}&seasonId={season}"
    
    print("\n" + "="*75)
    print("🚀 LAUNCHING BROWSER AUTOMATION")
    print("="*75)
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
            print(f"\n📅 Setting Week {week_num} ({len(schedule_by_week[week_num])} matchups)...")
            
            # Select the week tab or dropdown on ESPN if present
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
                    away_info = team_mapping.get(game['Away'], {})
                    home_info = team_mapping.get(game['Home'], {})
                    
                    away_id = str(away_info.get('id', ''))
                    home_id = str(home_info.get('id', ''))
                    away_owner = away_info.get('owner', game['Away'])
                    home_owner = home_info.get('owner', game['Home'])
                    
                    away_select = selects.nth(g_idx * 2)
                    home_select = selects.nth(g_idx * 2 + 1)
                    
                    print(f"  Game {g_idx+1}: {game['Away']} ({away_owner}) @ {game['Home']} ({home_owner})")
                    
                    if not dry_run:
                        try:
                            away_select.select_option(value=away_id)
                        except Exception:
                            away_select.select_option(label=away_owner)
                            
                        try:
                            home_select.select_option(value=home_id)
                        except Exception:
                            home_select.select_option(label=home_owner)
            else:
                rows = page.locator("tr.Table__TR, .schedule-item, .matchupRow")
                row_count = rows.count()
                for g_idx, game in enumerate(matchups):
                    if g_idx < row_count:
                        row = rows.nth(g_idx)
                        row_selects = row.locator("select")
                        if row_selects.count() >= 2:
                            away_info = team_mapping.get(game['Away'], {})
                            home_info = team_mapping.get(game['Home'], {})
                            away_id = str(away_info.get('id', ''))
                            home_id = str(home_info.get('id', ''))
                            away_owner = away_info.get('owner', game['Away'])
                            home_owner = home_info.get('owner', game['Home'])
                            
                            print(f"  Game {g_idx+1}: {away_owner} @ {home_owner}")
                            if not dry_run:
                                try:
                                    row_selects.nth(0).select_option(value=away_id)
                                except Exception:
                                    row_selects.nth(0).select_option(label=away_owner)
                                try:
                                    row_selects.nth(1).select_option(value=home_id)
                                except Exception:
                                    row_selects.nth(1).select_option(label=home_owner)
            
            if dry_run:
                print(f"  [DRY RUN] Week {week_num} previewed.")
            else:
                save_button = page.locator("button:has-text('Save'), button:has-text('Save Changes'), button.btn-save")
                if save_button.is_visible() and save_button.is_enabled():
                    print(f"  💾 Saving Week {week_num}...")
                    save_button.click()
                    time.sleep(2)
        
        print("\n" + "="*75)
        print("🎉 SCHEDULE UPLOAD COMPLETE!")
        print("="*75)
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
    
    espn_teams, divisions = get_espn_data(league_id, args.season, ESPN_S2, ESPN_SWID)
    team_mapping = build_team_mapping(all_csv_teams, DEFAULT_DIVISIONS, espn_teams)
    
    upload_schedule(league_id, args.season, schedule_by_week, team_mapping, dry_run=args.dry_run)

if __name__ == "__main__":
    main()
