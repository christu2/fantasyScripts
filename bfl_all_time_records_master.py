#!/usr/bin/env python3
"""
BFL All-Time Records Master Script
==================================
This script handles all scenarios for updating BFL all-time records:
- Current season updates (2024 and earlier)
- Future seasons (2025+) with Sydney/Emelie split
- All name variations and synonyms
- Complete Google Sheets integration

Run this script anytime to update all records with latest data.
"""

import pandas as pd
from espn_api.football import League
import sys
import time
import os
import pickle
import gspread
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# League credentials from environment
league_id = int(os.getenv('ESPN_LEAGUE_ID', '157057'))
espn_s2 = os.getenv('ESPN_S2')
swid = os.getenv('ESPN_SWID')

# Validate credentials are loaded
if not espn_s2 or not swid:
    print("❌ ESPN credentials not found!")
    print("📝 Make sure .env file exists with ESPN_S2 and ESPN_SWID")
    sys.exit(1)

SPREADSHEET_NAME = 'BFL Owner Records'
SCOPES = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']

# Get current year to determine behavior
CURRENT_YEAR = 2024  # Update this each season

# Owner name synonyms (complete historical mapping)
synDict = {
    'thomas ehrlich': 'tom ehrlich',
    'tommy ehrlich': 'tom ehrlich', 
    'gabriel zbaala': 'gabriel zabala',
    'ali bhujwala': 'daniel kruszewski',
    'dan kruszewski': 'daniel kruszewski',
    'matt rosato': 'austin russell',
    'bubba franks': 'austin russell',
    'alexandra christus': 'alex christus',
    'georgia batman': 'georgia christus',
    # Sydney/Emelie handling - they split starting 2025
    'sydney christus': 'emelie lovasko',  # Through 2024
    'sydney kite': 'emelie lovasko'      # Through 2024
}

def standardize_name(name, year=None):
    """
    Standardize owner names using the synonym dictionary
    Special handling for Sydney/Emelie split starting 2025
    """
    name_lower = name.lower()
    
    # Handle Sydney/Emelie split starting 2025
    if year and year >= 2025:
        if name_lower in ['sydney christus', 'sydney kite', 'sydney miller']:
            return 'Sydney Miller'
        if name_lower in ['emelie lovasko']:
            return 'Emelie Lovasko'
    
    # Apply regular synonyms
    for old_name, new_name in synDict.items():
        if old_name == name_lower:
            return new_name.title()
    
    return name

def authenticate_google_sheets():
    """Authenticate with Google Sheets using existing token or OAuth"""
    creds = None
    token_file = 'token.pickle'
    
    # Look for credentials.json
    credentials_locations = [
        'credentials.json',
        'FantasyRecords/credentials.json',
        '/Users/nick/Development/fantasyScripts/FantasyRecords/credentials.json'
    ]
    
    credentials_file = None
    for location in credentials_locations:
        if os.path.exists(location):
            credentials_file = location
            break
    
    if not credentials_file:
        print("❌ credentials.json not found!")
        return None
    
    # Load existing token
    if os.path.exists(token_file):
        try:
            with open(token_file, 'rb') as token:
                creds = pickle.load(token)
        except:
            creds = None
    
    # Refresh or get new credentials
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except:
                creds = None
        
        if not creds:
            flow = InstalledAppFlow.from_client_secrets_file(credentials_file, SCOPES)
            creds = flow.run_local_server(port=8080, open_browser=True)
        
        # Save credentials
        with open(token_file, 'wb') as token:
            pickle.dump(creds, token)
    
    try:
        gc = gspread.authorize(creds)
        return gc
    except Exception as e:
        print(f"❌ Google Sheets connection failed: {e}")
        return None

def get_current_season_year():
    """Get the current season year from ESPN"""
    try:
        test_league = League(league_id, CURRENT_YEAR, espn_s2, swid)
        return CURRENT_YEAR
    except:
        # Try previous year if current year fails
        try:
            test_league = League(league_id, CURRENT_YEAR - 1, espn_s2, swid)
            return CURRENT_YEAR - 1
        except:
            return CURRENT_YEAR

def get_tab_owners(current_season_year):
    """Get list of owners who should have tabs"""
    try:
        current_league = League(league_id, current_season_year, espn_s2, swid)
        tab_owners = []
        
        # Get current owners
        for team in current_league.teams:
            owner_name = team.owners[0].get('firstName').title() + ' ' + team.owners[0].get('lastName').title()
            standardized = standardize_name(owner_name, current_season_year)
            name_variations = [standardized]
            
            # Add historical variations
            for key, val in synDict.items():
                if val.lower() == standardized.lower():
                    name_variations.append(key.title())
            
            tab_owners.append(name_variations)
        
        # Add Ryan Olen (always included for historical tracking)
        ryan_found = False
        for name_list in tab_owners:
            if 'Ryan Olen' in name_list:
                ryan_found = True
                break
        if not ryan_found:
            tab_owners.append(['Ryan Olen'])
        
        # Add Sydney Miller if we're in 2025+ (when she splits from Emelie)
        if current_season_year >= 2025:
            sydney_found = False
            for name_list in tab_owners:
                if 'Sydney Miller' in name_list:
                    sydney_found = True
                    break
            if not sydney_found:
                tab_owners.append(['Sydney Miller'])
        
        return tab_owners
    
    except Exception as e:
        print(f"❌ Error getting current owners: {e}")
        return []

def collect_all_games(tab_owners, current_season_year):
    """Collect all games for tab owners from 2008 to current season"""
    print(f"🔍 Collecting games from 2008-{current_season_year}...")
    
    all_games = []
    successful_years = []
    
    for year in range(2008, current_season_year + 1):
        try:
            print(f"  📅 {year}...", end=" ", flush=True)
            league = League(league_id, year, espn_s2, swid)
            year_games = 0
            
            for team in league.teams:
                owner_name = team.owners[0].get('firstName').title() + ' ' + team.owners[0].get('lastName').title()
                standardized_owner = standardize_name(owner_name, year)
                
                # Check if this owner should have a tab
                owner_match = None
                for name_list in tab_owners:
                    if standardized_owner in name_list or owner_name in name_list:
                        owner_match = name_list[0]
                        break
                
                if owner_match:
                    for week_idx, score in enumerate(team.scores):
                        if week_idx < len(team.schedule) and team.schedule[week_idx]:
                            opponent_team = team.schedule[week_idx]
                            if week_idx < len(opponent_team.scores):
                                opponent_name = opponent_team.owners[0].get('firstName').title() + ' ' + opponent_team.owners[0].get('lastName').title()
                                standardized_opponent = standardize_name(opponent_name, year)
                                
                                if standardized_opponent != owner_match:
                                    game_record = {
                                        'year': year,
                                        'week': week_idx + 1,
                                        'owner': owner_match,
                                        'opponent': standardized_opponent,
                                        'score': score,
                                        'opponent_score': opponent_team.scores[week_idx],
                                        'win': score > opponent_team.scores[week_idx]
                                    }
                                    all_games.append(game_record)
                                    year_games += 1
            
            print(f"{year_games} games")
            successful_years.append(year)
            time.sleep(0.1)
            
        except Exception as e:
            print(f"❌ Error in {year}: {str(e)}")
            continue
    
    print(f"\n📊 Successfully processed years: {successful_years}")
    print(f"📊 Total games collected: {len(all_games)}")
    
    return all_games

def update_google_sheets(gc, all_games, tab_owners, current_season_year):
    """Update all Google Sheets with current data"""
    print(f"\n📊 Updating Google Spreadsheet...")
    
    try:
        # Open or create spreadsheet
        try:
            spreadsheet = gc.open(SPREADSHEET_NAME)
        except gspread.SpreadsheetNotFound:
            spreadsheet = gc.create(SPREADSHEET_NAME)
            spreadsheet.share('', perm_type='anyone', role='reader')
        
        print(f"🔗 Spreadsheet URL: {spreadsheet.url}")
        
        # Process each owner
        summary_data = []
        
        for i, name_list in enumerate(tab_owners):
            owner = name_list[0]
            print(f"  📈 Processing {owner} ({i+1}/{len(tab_owners)})...")
            
            # Special handling for Sydney Miller sharing Emelie's record through 2024
            if owner == 'Sydney Miller' and current_season_year <= 2024:
                emelie_games = [g for g in all_games if g['owner'] == 'Emelie Lovasko']
                owner_games = emelie_games
                print(f"      (Sharing Emelie's record through 2024: {len(emelie_games)} games)")
            else:
                owner_games = [g for g in all_games if g['owner'] == owner]
            
            # Calculate records
            opponent_records = {}
            for game in owner_games:
                opponent = game['opponent']
                if opponent not in opponent_records:
                    opponent_records[opponent] = {'wins': 0, 'losses': 0}
                
                if game['win']:
                    opponent_records[opponent]['wins'] += 1
                else:
                    opponent_records[opponent]['losses'] += 1
            
            # Create records list
            owner_records = []
            for opponent, record in opponent_records.items():
                if opponent != owner:
                    wins = record['wins']
                    losses = record['losses']
                    total = wins + losses
                    pct = round(wins / total, 3) if total > 0 else 0.0
                    owner_records.append([opponent, wins, losses, pct])
            
            # Sort by winning percentage
            owner_records.sort(key=lambda x: x[3], reverse=True)
            
            # Update Google Sheet
            if owner_records:
                try:
                    # Find or create worksheet
                    worksheet = None
                    for ws in spreadsheet.worksheets():
                        if ws.title == owner:
                            worksheet = ws
                            break
                    
                    if not worksheet:
                        worksheet = spreadsheet.add_worksheet(title=owner, rows=100, cols=10)
                    
                    # Clear and update
                    worksheet.clear()
                    
                    # Add note for Sydney/Emelie if relevant
                    if owner in ['Sydney Miller', 'Emelie Lovasko'] and current_season_year <= 2024:
                        note_text = f"SHARED RECORDS (2008-2024): {owner.split()[0]} & {'Emelie' if 'Sydney' in owner else 'Sydney'} were co-owners until 2025"
                        worksheet.update('A1', [[note_text, '', '', '']])
                        worksheet.format('A1:D1', {
                            'textFormat': {'bold': True, 'italic': True},
                            'backgroundColor': {'red': 1.0, 'green': 0.9, 'blue': 0.7}
                        })
                        worksheet.merge_cells('A1:D1')
                        start_row = 2
                    else:
                        start_row = 1
                    
                    # Add headers and data
                    headers = [['Opponent', 'Wins', 'Losses', 'Win Pct']]
                    all_data = headers + owner_records
                    
                    worksheet.update(f'A{start_row}:D{start_row + len(all_data) - 1}', all_data)
                    
                    # Format headers
                    worksheet.format(f'A{start_row}:D{start_row}', {
                        'textFormat': {'bold': True},
                        'backgroundColor': {'red': 0.9, 'green': 0.9, 'blue': 0.9}
                    })
                    
                    # Format percentage column
                    if len(owner_records) > 0:
                        worksheet.format(f'D{start_row + 1}:D{start_row + len(owner_records)}', {
                            'numberFormat': {'type': 'NUMBER', 'pattern': '0.000'}
                        })
                    
                    print(f"    ✅ Updated sheet: {owner} ({len(owner_records)} opponents)")
                    
                except Exception as e:
                    print(f"    ❌ Error updating {owner}: {e}")
            
            # Add to summary data
            total_wins = sum(r[1] for r in owner_records)
            total_losses = sum(r[2] for r in owner_records)
            overall_pct = total_wins / (total_wins + total_losses) if (total_wins + total_losses) > 0 else 0.0
            
            summary_data.append([
                owner,
                total_wins,
                total_losses,
                overall_pct,
                len(opponent_records)
            ])
            
            # Rate limiting
            time.sleep(1)
        
        # Create summary sheet
        print(f"\n📊 Creating summary sheet...")
        
        try:
            # Find or create summary sheet
            summary_ws = None
            for ws in spreadsheet.worksheets():
                if "SUMMARY" in ws.title:
                    summary_ws = ws
                    break
            
            if not summary_ws:
                summary_ws = spreadsheet.add_worksheet(title="📊 SUMMARY", rows=50, cols=10, index=0)
            
            # Sort and update summary
            summary_data.sort(key=lambda x: x[3], reverse=True)
            
            summary_ws.clear()
            
            summary_headers = [['Owner', 'Total Wins', 'Total Losses', 'Win %', 'Opponents']]
            all_summary_data = summary_headers + summary_data
            
            summary_ws.update(f'A1:E{len(all_summary_data)}', all_summary_data)
            
            # Format summary
            summary_ws.format('A1:E1', {
                'textFormat': {'bold': True, 'fontSize': 12},
                'backgroundColor': {'red': 0.2, 'green': 0.6, 'blue': 1.0}
            })
            
            summary_ws.format(f'D2:D{len(summary_data)+1}', {
                'numberFormat': {'type': 'PERCENT', 'pattern': '0.0%'}
            })
            
            print(f"    ✅ Updated summary sheet")
            
        except Exception as e:
            print(f"    ❌ Error creating summary: {e}")
        
        return True, spreadsheet.url, summary_data
        
    except Exception as e:
        print(f"❌ Error updating Google Sheets: {e}")
        return False, None, []

def main():
    """Main function to update all BFL records"""
    print("🏈 BFL All-Time Records Master Updater")
    print("=" * 60)
    
    # Get current season
    current_season_year = get_current_season_year()
    print(f"📅 Current season: {current_season_year}")
    
    # Test ESPN API
    try:
        test_league = League(league_id, current_season_year, espn_s2, swid)
        print("✅ ESPN API connection successful!")
    except Exception as e:
        print(f"❌ ESPN API connection failed: {e}")
        return
    
    # Authenticate Google Sheets
    print("\n🔐 Authenticating with Google Sheets...")
    gc = authenticate_google_sheets()
    if not gc:
        print("❌ Cannot proceed without Google Sheets authentication")
        return
    
    print("✅ Google Sheets authentication successful!")
    
    # Get tab owners
    tab_owners = get_tab_owners(current_season_year)
    if not tab_owners:
        print("❌ Could not determine tab owners")
        return
    
    print(f"\n📋 Will update {len(tab_owners)} owner tabs:")
    for name_list in tab_owners:
        print(f"  • {name_list[0]}")
    
    # Special note for Sydney/Emelie split
    if current_season_year >= 2025:
        print(f"\n🔄 Starting {current_season_year}: Sydney and Emelie track separately")
    else:
        print(f"\n🤝 Through {current_season_year}: Sydney and Emelie share co-ownership records")
    
    # Collect all games
    all_games = collect_all_games(tab_owners, current_season_year)
    
    if not all_games:
        print("❌ No games collected")
        return
    
    # Update Google Sheets
    success, url, summary_data = update_google_sheets(gc, all_games, tab_owners, current_season_year)
    
    if success:
        print(f"\n🎯 All records updated successfully!")
        print(f"🔗 Spreadsheet: {url}")
        print(f"📊 Updated {len(tab_owners)} individual sheets + summary")
        print(f"📈 Based on {len(all_games)} games from 2008-{current_season_year}")
        
        # Show top 5 standings
        print(f"\n🏆 Current Top 5 Standings:")
        for i, data in enumerate(summary_data[:5]):
            print(f"  {i+1}. {data[0]:20} {data[1]:3}-{data[2]:3} ({data[3]:.3f})")
        
        print(f"\n💡 This script will automatically handle:")
        print(f"   • Sydney/Emelie split starting 2025")
        print(f"   • All name variations and synonyms")
        print(f"   • Complete historical records vs all opponents")
        print(f"   • Current season updates throughout the year")
    else:
        print(f"\n❌ Update failed")

if __name__ == "__main__":
    main()