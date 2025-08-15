#!/usr/bin/env python3
"""
ESPN Fantasy Football Draft Assistant
Analyzes historical draft data to provide intelligent draft recommendations
optimized for 16-team half PPR leagues
"""

import requests
import pandas as pd
import numpy as np
from typing import Dict, List, Tuple, Optional
import json
from datetime import datetime
import warnings
import os
from dotenv import load_dotenv

warnings.filterwarnings('ignore')

# Load environment variables
load_dotenv()

class ESPNDraftAssistant:
    def __init__(self, league_id: int, espn_s2: str = None, swid: str = None, 
                 excluded_members: List[str] = None, custom_name_mapping: Dict[str, str] = None):
        """
        Initialize the draft assistant
        
        Args:
            league_id: Your ESPN league ID
            espn_s2: ESPN S2 cookie (required for private leagues)
            swid: ESPN SWID cookie (required for private leagues)
            excluded_members: List of owner names to exclude from current analysis
            custom_name_mapping: Dictionary mapping ESPN usernames to real names
        """
        self.league_id = league_id
        self.excluded_members = excluded_members or []
        self.custom_name_mapping = custom_name_mapping or {}
        self.cookies = {}
        if espn_s2 and swid:
            self.cookies = {'espn_s2': espn_s2, 'SWID': swid}
        
        self.base_url = "https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl"
        self.historical_data = {}
        self.owner_tendencies = {}
        self.current_draft = []
        self.available_players = []
        self.excluded_members = set(excluded_members or [])
        
        # Manual name mapping from ESPN usernames to real manager names (based on current roster)
        self.custom_name_mapping = custom_name_mapping or {
            'favreindahouse4': 'Nick Christus',        # Melbourne Miscre... (you!)
            'ehrlich78': 'Tommy Ehrlich',              # The Ehrly Birds  
            'thebearssamurai': 'Samran Mirza',         # De'von Intervention  
            'knanaya12': 'Shawn Lukose',              # Venezuelan Poodle
            'beast4life24': 'Nael Ahmed',             # Big Nasties
            'slamdunkers989': 'Shawn Ullenbrauck',    # Pat N' Pending
            'dinod123': 'Dino Davros',                # Taliban Gang Mujah...
            'theguptaempire': 'Saagar Gupta',         # King Gupta's Army
            'alex7626': 'Alex Kite',                  # Send Da Trade
            'rej5073': 'rej hoxha',                   # Steve Bartman
            'Rej5073': 'rej hoxha',                   # Steve Bartman (case variation)
            'sydney8715': 'Sydney Miller / Emelie Lovasko',  # The Queens (shared team historically)
            'espnfan0270220732': 'Blake Whitehouse',  # Block O meets O Bl...
            'espnfan2927064247': 'Daniel Kruszewski', # Dorito Dominance
            'espnfan4034736305': 'Abe Thomas',        # Pajama Warriors
            'adaole1': 'Adam Olen',                   # Team 40
            'jon lovasko': 'Sydney Miller / Emelie Lovasko',  # Team 37 - Same efficiency as sydney8715
            'steve bartman': 'rej hoxha',             # Team 34 - Uses team name instead of username
            'steve bartman ': 'rej hoxha',            # Team 34 - With trailing space variation
            '4ryano': '4Ryano (FORMER MEMBER)',       # No longer in league
        }
        
        # Position mappings for ESPN
        self.position_map = {
            1: 'QB', 2: 'RB', 3: 'WR', 4: 'TE', 5: 'K', 16: 'D/ST'
        }
        
        # Standard 16-team half PPR scoring weights
        self.scoring_weights = {
            'QB': {'pass_yd': 0.04, 'pass_td': 4, 'rush_yd': 0.1, 'rush_td': 6, 'int': -2},
            'RB': {'rush_yd': 0.1, 'rush_td': 6, 'rec': 0.5, 'rec_yd': 0.1, 'rec_td': 6},
            'WR': {'rec': 0.5, 'rec_yd': 0.1, 'rec_td': 6, 'rush_yd': 0.1, 'rush_td': 6},
            'TE': {'rec': 0.5, 'rec_yd': 0.1, 'rec_td': 6},
            'K': {'fg': 3, 'xp': 1},
            'D/ST': {'def_td': 6, 'sack': 1, 'int': 2, 'fum_rec': 2}
        }

    def get_league_data(self, year: int) -> Dict:
        """Fetch comprehensive league data for a specific year"""
        try:
            if year >= 2018:
                url = f"{self.base_url}/seasons/{year}/segments/0/leagues/{self.league_id}"
            else:
                url = f"{self.base_url}/leagueHistory/{self.league_id}?seasonId={year}"
            
            # Request multiple views to get complete data including owner information
            params = {
                'view': ['mDraftDetail', 'mSettings', 'mTeams', 'mRoster', 'mOwner', 'kona_player_info']
            }
            
            response = requests.get(url, params=params, cookies=self.cookies, timeout=15)
            
            if response.status_code == 200:
                return response.json()
            else:
                print(f"Error fetching {year} data: Status {response.status_code}")
                return {}
        except Exception as e:
            print(f"Error fetching {year} data: {e}")
            return {}

    def get_player_details(self, player_id: int, year: int) -> Dict:
        """Fetch detailed player information"""
        try:
            if year >= 2018:
                url = f"{self.base_url}/seasons/{year}/segments/0/leagues/{self.league_id}"
            else:
                url = f"{self.base_url}/leagueHistory/{self.league_id}?seasonId={year}"
            
            params = {'view': 'kona_player_info'}
            
            # Add filter to get specific player
            headers = {
                'X-Fantasy-Filter': json.dumps({
                    "players": {
                        "filterIds": {"value": [player_id]},
                        "limit": 1
                    }
                })
            }
            
            response = requests.get(url, params=params, headers=headers, cookies=self.cookies, timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                if 'players' in data and data['players']:
                    return data['players'][0]
            
            return {}
        except Exception as e:
            print(f"Error fetching player {player_id}: {e}")
            return {}

    def get_all_players_for_year(self, year: int) -> Dict:
        """Get all player data for a specific year - try multiple approaches with enhanced filtering"""
        try:
            if year >= 2018:
                url = f"{self.base_url}/seasons/{year}/segments/0/leagues/{self.league_id}"
            else:
                url = f"{self.base_url}/leagueHistory/{self.league_id}?seasonId={year}"
            
            # Try different approaches to get player data with better filters
            approaches = [
                # Approach 1: Enhanced filter with proper limit and offset
                {
                    'params': {'view': 'kona_player_info'},
                    'headers': {
                        'X-Fantasy-Filter': json.dumps({
                            "players": {
                                "limit": 2000,
                                "sortPercOwned": {"sortAsc": False, "sortPriority": 1}
                            }
                        })
                    }
                },
                # Approach 2: Try without league context (global players)
                {
                    'params': {'view': 'kona_player_info'},
                    'headers': {
                        'X-Fantasy-Filter': json.dumps({
                            "players": {
                                "limit": 2000,
                                "filterActive": {"value": True}
                            }
                        })
                    }
                },
                # Approach 3: Use different player info view
                {
                    'params': {'view': 'players_wl'},
                    'headers': {
                        'X-Fantasy-Filter': json.dumps({
                            "players": {"limit": 2000}
                        })
                    }
                },
                # Approach 4: Try with different URL structure for older years
                {
                    'params': {'view': 'kona_player_info'},
                    'headers': {}
                },
                # Approach 5: Global ESPN URL for player data
                {
                    'url_override': f"https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/{year}",
                    'params': {'view': 'kona_player_info'},
                    'headers': {
                        'X-Fantasy-Filter': json.dumps({
                            "players": {"limit": 2000}
                        })
                    }
                }
            ]
            
            for i, approach in enumerate(approaches):
                try:
                    # Use custom URL if provided, otherwise use the standard one
                    request_url = approach.get('url_override', url)
                    
                    response = requests.get(
                        request_url, 
                        params=approach['params'], 
                        headers=approach['headers'], 
                        cookies=self.cookies, 
                        timeout=30
                    )
                    
                    if response.status_code == 200:
                        data = response.json()
                        
                        # Check different possible locations for player data
                        player_sources = ['players', 'player_pool', 'playerPool']
                        
                        for source in player_sources:
                            if source in data and data[source]:
                                players_list = data[source]
                                if isinstance(players_list, list):
                                    print(f"    Found {len(players_list)} players using approach {i+1}, source: {source}")
                                    return {player['id']: player for player in players_list}
                                elif isinstance(players_list, dict) and 'players' in players_list:
                                    players_list = players_list['players']
                                    print(f"    Found {len(players_list)} players using approach {i+1}, source: {source}")
                                    return {player['id']: player for player in players_list}
                    else:
                        print(f"    Approach {i+1} returned status {response.status_code}")
                    
                except Exception as e:
                    print(f"    Approach {i+1} failed: {e}")
                    continue
            
            print(f"    All approaches failed for {year}")
            return {}
            
        except Exception as e:
            print(f"Error in get_all_players_for_year for {year}: {e}")
            return {}

    def get_players_by_batch(self, player_ids: List[int], year: int) -> Dict:
        """Try to get multiple specific players by ID"""
        try:
            if year >= 2018:
                url = f"{self.base_url}/seasons/{year}/segments/0/leagues/{self.league_id}"
            else:
                url = f"{self.base_url}/leagueHistory/{self.league_id}?seasonId={year}"
            
            # Break player IDs into smaller batches to avoid URL length limits
            batch_size = 50
            all_players = {}
            
            for i in range(0, len(player_ids), batch_size):
                batch_ids = player_ids[i:i + batch_size]
                
                headers = {
                    'X-Fantasy-Filter': json.dumps({
                        "players": {
                            "filterIds": {"value": batch_ids},
                            "limit": batch_size
                        }
                    })
                }
                
                response = requests.get(url, params={'view': 'kona_player_info'}, 
                                      headers=headers, cookies=self.cookies, timeout=15)
                
                if response.status_code == 200:
                    data = response.json()
                    if 'players' in data and data['players']:
                        for player in data['players']:
                            all_players[player['id']] = player
                        print(f"    Batch retrieved {len(data['players'])} players")
                
            return all_players
            
        except Exception as e:
            print(f"    Batch retrieval failed: {e}")
            return {}

    def parse_draft_data_fallback(self, data: Dict, year: int, team_owner_map: Dict = None) -> List[Dict]:
        """Enhanced fallback parsing with better player data extraction"""
        if 'draftDetail' not in data or 'picks' not in data['draftDetail']:
            return []
        
        picks = []
        teams = {team['id']: team for team in data.get('teams', [])}
        
        # Try to get all player IDs mentioned in draft picks
        player_ids = [pick.get('playerId') for pick in data['draftDetail']['picks'] 
                     if pick.get('playerId')]
        
        # Attempt batch retrieval of players
        batch_players = {}
        if player_ids:
            print(f"    Attempting batch retrieval of {len(player_ids)} players")
            batch_players = self.get_players_by_batch(player_ids, year)
        
        for pick in data['draftDetail']['picks']:
            player_id = pick.get('playerId')
            team_id = pick.get('teamId')
            team_info = teams.get(team_id, {})
            
            # Initialize defaults
            player_name = "Unknown Player"
            position = "UNKNOWN"
            nfl_team = "UNK"
            
            # Try multiple sources for player data (in order of preference)
            player_info = None
            
            # Source 1: Batch retrieved player data
            if player_id in batch_players:
                batch_player = batch_players[player_id]
                if 'player' in batch_player:
                    player_info = batch_player['player']
                elif isinstance(batch_player, dict):
                    player_info = batch_player
            
            # Source 2: Player data embedded in the pick
            if not player_info and 'player' in pick:
                player_info = pick['player']
            
            # Source 3: Direct pick data (less common)
            if not player_info:
                # Sometimes player data is directly in the pick
                if pick.get('firstName') or pick.get('lastName'):
                    player_info = pick
            
            # Extract player information if we found any
            if player_info:
                first_name = player_info.get('firstName', '')
                last_name = player_info.get('lastName', '')
                if first_name or last_name:
                    player_name = f"{first_name} {last_name}".strip()
                
                # Get position
                position_id = player_info.get('defaultPositionId')
                if position_id:
                    position = self.position_map.get(position_id, 'UNKNOWN')
                
                # Get NFL team
                nfl_team_id = player_info.get('proTeamId')
                if nfl_team_id:
                    nfl_team = self.get_nfl_team_name(nfl_team_id)
                elif player_info.get('proTeam'):
                    nfl_team = str(player_info['proTeam'])
            
            # Extract owner name using team-owner mapping
            owner_name = team_owner_map.get(team_id, f"Team {team_id}") if team_owner_map else f"Team {team_id}"
            owner_name = self.get_consistent_owner_name(owner_name)
            
            pick_info = {
                'year': year,
                'pick_number': pick.get('overallPickNumber', 0),
                'round': pick.get('roundId', 0),
                'round_pick': pick.get('roundPickNumber', 0),
                'player_id': player_id,
                'player_name': player_name,
                'position': position,
                'nfl_team': nfl_team,
                'team_id': team_id,
                'owner_name': owner_name,
                'keeper': pick.get('keeper', False)
            }
            picks.append(pick_info)
        
        return picks

    def analyze_historical_drafts(self, personality_years: List[int], strategy_years: List[int]) -> Dict:
        """Analyze multiple years of draft data with tiered approach"""
        print("Fetching historical draft data...")
        print(f"Personality analysis: {personality_years}")
        print(f"Strategy analysis: {strategy_years}")
        
        # Get current league members to filter analysis
        current_members = self.get_current_league_members(strategy_years)
        if not current_members:
            print("Warning: Could not determine current league members, including all historical owners")
        
        all_drafts = []
        all_years = sorted(set(personality_years + strategy_years))
        
        for year in all_years:
            print(f"  Analyzing {year}...")
            data = self.get_league_data(year)
            if data and 'draftDetail' in data:
                draft_info = self.parse_draft_data(data, year)
                if draft_info:
                    # Check how many valid picks we got
                    valid_picks = [pick for pick in draft_info if pick['player_name'] != 'Unknown Player']
                    unknown_picks = [pick for pick in draft_info if pick['player_name'] == 'Unknown Player']
                    
                    print(f"    Found {len(valid_picks)} valid picks, {len(unknown_picks)} unknown players")
                    
                    # Filter to only include picks from current league members
                    if current_members:
                        filtered_picks = []
                        for pick in draft_info:
                            normalized_owner = self.get_consistent_owner_name(pick['owner_name'])
                            if normalized_owner in current_members:
                                filtered_picks.append(pick)
                        
                        excluded_count = len(draft_info) - len(filtered_picks)
                        if excluded_count > 0:
                            print(f"    Excluded {excluded_count} picks from former league members")
                        
                        all_drafts.extend(filtered_picks)
                    else:
                        # Fallback: include all picks if we can't determine current members
                        all_drafts.extend(draft_info)
                else:
                    print(f"    No draft data found for {year}")
            else:
                print(f"    Could not access draft data for {year}")
        
        if not all_drafts:
            print("No historical draft data found!")
            return {}
        
        # Convert to DataFrame for analysis
        df = pd.DataFrame(all_drafts)
        
        # DEBUG: Show example ESPN API output for one year where rej might be missing
        print("\n" + "="*80)
        print("DEBUG: ESPN API DRAFT DATA EXAMPLE (2024)")
        print("="*80)
        
        # Show raw draft data for 2024
        sample_year = 2024
        year_picks = [p for p in all_drafts if p['year'] == sample_year]
        if year_picks:
            print(f"Found {len(year_picks)} total picks for {sample_year}")
            
            # Group by team to show what teams are represented
            teams_in_draft = {}
            for pick in year_picks:
                team_id = pick['team_id']
                owner_name = pick['owner_name']
                if team_id not in teams_in_draft:
                    teams_in_draft[team_id] = {
                        'owner_name': owner_name,
                        'picks': []
                    }
                teams_in_draft[team_id]['picks'].append({
                    'round': pick['round'],
                    'pick': pick['pick_number'],
                    'player': pick['player_name'],
                    'position': pick['position']
                })
            
            print(f"\nTeams represented in {sample_year} draft data:")
            for team_id in sorted(teams_in_draft.keys()):
                team_info = teams_in_draft[team_id]
                print(f"  Team {team_id}: {team_info['owner_name']} ({len(team_info['picks'])} picks)")
                # Show first 2 picks as example
                for pick in team_info['picks'][:2]:
                    print(f"    R{pick['round']}.{pick['pick']}: {pick['player']} ({pick['position']})")
                if len(team_info['picks']) > 2:
                    print(f"    ... and {len(team_info['picks'])-2} more picks")
            
            print(f"\nMISSING TEAMS:")
            all_expected_teams = set(range(1, 41))  # Teams 1-40 possible
            found_teams = set(teams_in_draft.keys())
            missing_teams = sorted(all_expected_teams - found_teams)
            if missing_teams:
                print(f"  Team IDs not found in draft data: {missing_teams[:10]}..." if len(missing_teams) > 10 else missing_teams)
                if 34 in missing_teams:
                    print(f"  *** Team 34 (rej hoxha) is MISSING from draft data ***")
            else:
                print("  No missing teams found")
                
        print("="*80)
        print()
        
        
        # Try to work with whatever data we have
        total_picks = len(all_drafts)
        known_players = len(df[df['player_name'] != 'Unknown Player'])
        known_positions = len(df[df['position'] != 'UNKNOWN'])
        
        print(f"\nData Quality Summary:")
        print(f"Total picks: {total_picks}")
        print(f"Known player names: {known_players} ({known_players/total_picks:.1%})")
        print(f"Known positions: {known_positions} ({known_positions/total_picks:.1%})")
        print(f"Years with data: {sorted(df['year'].unique())}")
        print(f"Unique owners: {df['owner_name'].nunique()}")
        
        # Show position breakdown (even if some are UNKNOWN)
        position_counts = df['position'].value_counts()
        print(f"Position breakdown: {dict(position_counts)}")
        
        # Split data for tiered analysis
        personality_df = df[df['year'].isin(personality_years)]
        strategy_df = df[df['year'].isin(strategy_years)]
        
        # For analysis, we can work with owner patterns even without complete player data
        if df['owner_name'].nunique() >= 2:  # Need at least 2 owners for meaningful analysis
            # Analyze owner tendencies (personality traits from longer window)
            self.owner_tendencies = self.analyze_owner_patterns(personality_df, strategy_df, current_members)
            
            # Analyze positional trends (strategy from recent window) - only if we have position data
            if known_positions > total_picks * 0.5:  # If we have position data for >50% of picks
                positional_analysis = self.analyze_positional_trends(strategy_df)
            else:
                print("Insufficient position data for positional analysis")
                positional_analysis = {}
            
            # Calculate value metrics (strategy from recent window)
            value_analysis = self.calculate_historical_value(strategy_df)
            
            print(f"\nAnalysis Complete:")
            print(f"Personality data: {len(personality_df)} picks from {len(personality_years)} years")
            print(f"Strategy data: {len(strategy_df)} picks from {len(strategy_years)} years")
            
            # Analyze draft success vs performance
            performance_data = self.analyze_draft_success(all_years, all_drafts)
            efficiency_scores = self.calculate_draft_efficiency(df, performance_data)
            
            return {
                'drafts': df,
                'personality_drafts': personality_df,
                'strategy_drafts': strategy_df,
                'owner_tendencies': self.owner_tendencies,
                'positional_trends': positional_analysis,
                'value_metrics': value_analysis,
                'performance_data': performance_data,
                'draft_efficiency': efficiency_scores,
                'current_members': current_members,
                'data_quality': {
                    'total_picks': total_picks,
                    'known_players': known_players,
                    'known_positions': known_positions,
                    'player_completion': known_players/total_picks,
                    'position_completion': known_positions/total_picks
                }
            }
        else:
            print("Insufficient owner data for meaningful analysis")
            return {}

    def parse_draft_data(self, data: Dict, year: int) -> List[Dict]:
        """Parse draft data from ESPN API response with enhanced player lookup"""
        if 'draftDetail' not in data or 'picks' not in data['draftDetail']:
            return []
        
        print(f"    Getting player details and owner info for {year}...")
        
        # Get owner information for this year
        print(f"    Building team-owner mapping...")
        team_owner_map = self.build_team_owner_mapping(data, year)
        
        # Try enhanced fallback method first (it now includes batch retrieval)
        print(f"    Using enhanced parsing for {year}")
        fallback_picks = self.parse_draft_data_fallback(data, year, team_owner_map)
        
        # Check how many valid picks we got from fallback
        valid_picks = [pick for pick in fallback_picks if pick['player_name'] != 'Unknown Player']
        
        if len(valid_picks) > len(fallback_picks) * 0.7:  # If we got >70% valid picks
            print(f"    Enhanced parsing successful: {len(valid_picks)} valid picks")
            return fallback_picks
        
        # If fallback didn't work well, try the full player database approach
        print(f"    Enhanced parsing got {len(valid_picks)} valid picks, trying full database...")
        all_players = self.get_all_players_for_year(year)
        print(f"    Found {len(all_players)} players in database")
        
        # If we can't get player database, return the fallback results anyway
        if len(all_players) == 0:
            print(f"    No full database available, using enhanced parsing results")
            return fallback_picks
        
        picks = []
        teams = {team['id']: team for team in data.get('teams', [])}
        
        for pick in data['draftDetail']['picks']:
            player_id = pick.get('playerId')
            team_id = pick.get('teamId')
            team_info = teams.get(team_id, {})
            
            # Get player details from our lookup
            player_details = all_players.get(player_id, {})
            
            # Extract player info
            player_name = "Unknown Player"
            position = "UNKNOWN"
            nfl_team = "UNK"
            
            if player_details and 'player' in player_details:
                player_info = player_details['player']
                first_name = player_info.get('firstName', '')
                last_name = player_info.get('lastName', '')
                player_name = f"{first_name} {last_name}".strip()
                
                # Get position
                position_id = player_info.get('defaultPositionId')
                position = self.position_map.get(position_id, 'UNKNOWN')
                
                # Get NFL team
                nfl_team = player_info.get('proTeamId', 'UNK')
                if isinstance(nfl_team, int):
                    nfl_team = self.get_nfl_team_name(nfl_team)
            elif 'player' in pick:
                # Fallback: try to get player data from the pick itself
                player_info = pick['player']
                first_name = player_info.get('firstName', '')
                last_name = player_info.get('lastName', '')
                if first_name or last_name:
                    player_name = f"{first_name} {last_name}".strip()
                
                position_id = player_info.get('defaultPositionId')
                if position_id:
                    position = self.position_map.get(position_id, 'UNKNOWN')
                
                nfl_team_id = player_info.get('proTeamId')
                if nfl_team_id:
                    nfl_team = self.get_nfl_team_name(nfl_team_id)
            
            # Extract owner name using team-owner mapping
            owner_name = team_owner_map.get(team_id, f"Team {team_id}") if team_owner_map else f"Team {team_id}"
            owner_name = self.get_consistent_owner_name(owner_name)
            
            pick_info = {
                'year': year,
                'pick_number': pick.get('overallPickNumber', 0),
                'round': pick.get('roundId', 0),
                'round_pick': pick.get('roundPickNumber', 0),
                'player_id': player_id,
                'player_name': player_name,
                'position': position,
                'nfl_team': nfl_team,
                'team_id': team_id,
                'owner_name': owner_name,
                'keeper': pick.get('keeper', False)
            }
            picks.append(pick_info)
        
        return picks

    def build_team_owner_mapping(self, data: Dict, year: int) -> Dict:
        """Build mapping from team ID to owner name using multiple strategies"""
        team_owner_map = {}
        
        # Strategy 1: Extract owner information directly from teams data
        teams = data.get('teams', [])
        for team in teams:
            team_id = team.get('id')
            if not team_id:
                continue
            
                
            owner_name = None
            
            # Check if primaryOwner exists with user info
            if 'primaryOwner' in team:
                primary_owner = team['primaryOwner']
                if isinstance(primary_owner, dict):
                    display_name = primary_owner.get('displayName', '').strip()
                    first_name = primary_owner.get('firstName', '').strip()
                    last_name = primary_owner.get('lastName', '').strip()
                    
                    if display_name:
                        owner_name = display_name
                    elif first_name or last_name:
                        owner_name = f"{first_name} {last_name}".strip()
            
            # Check if owners array exists (alternative format like Team 34)
            elif 'owners' in team and isinstance(team['owners'], list) and len(team['owners']) > 0:
                owner_id = team['owners'][0]  # Take first owner GUID
                # Try to match with members data
                members = data.get('members', [])
                for member in members:
                    if member.get('id') == owner_id:
                        if 'displayName' in member:
                            owner_name = str(member['displayName']).strip()
                            break
                        elif 'firstName' in member and 'lastName' in member:
                            first = str(member.get('firstName', '')).strip()
                            last = str(member.get('lastName', '')).strip()
                            if first or last:
                                owner_name = f"{first} {last}".strip()
                                break
            
            if not owner_name:
                # Fallback to team name (better than Team X)
                team_name = team.get('name', '').strip()
                team_abbrev = team.get('abbrev', '').strip()
                if team_name:
                    owner_name = team_name
                elif team_abbrev:
                    owner_name = team_abbrev
                else:
                    owner_name = f"Team {team_id}"
            
            team_owner_map[team_id] = owner_name
        
        # Strategy 2: Try to fetch members API as additional information
        try:
            if year >= 2018:
                url = f"{self.base_url}/seasons/{year}/segments/0/leagues/{self.league_id}"
            else:
                url = f"{self.base_url}/leagueHistory/{self.league_id}?seasonId={year}"
            
            # Request member information
            params = {'view': ['mMembers']}
            
            response = requests.get(url, params=params, cookies=self.cookies, timeout=10)
            
            if response.status_code == 200:
                members_data = response.json()
                members = members_data.get('members', [])
                
                # DEBUG: Show member structure for recent years (disabled)
                # if year >= 2023 and members:
                #     print(f"\n=== DEBUG: Member structure for {year} ===")
                #     for i, member in enumerate(members[:2]):  # Show first 2 members
                #         print(f"Member {i+1}:")
                #         for key, value in member.items():
                #             if isinstance(value, (str, int, float)):
                #                 print(f"  {key}: {repr(value)}")
                #             else:
                #                 print(f"  {key}: {type(value)}")
                #         print()
                
                # Try to map members to teams if possible
                # This is tricky because ESPN doesn't always provide clear mapping
                member_info = {}
                for member in members:
                    member_id = member.get('id', '')
                    display_name = member.get('displayName', '')
                    first_name = member.get('firstName', '')
                    last_name = member.get('lastName', '')
                    
                    # Prefer display name, fallback to first+last
                    if display_name:
                        name = display_name.strip()
                    elif first_name or last_name:
                        name = f"{first_name} {last_name}".strip()
                    else:
                        continue
                    
                    member_info[str(member_id)] = name
                    # Also try with curly braces
                    if not str(member_id).startswith('{'):
                        member_info[f"{{{member_id}}}"] = name
                    else:
                        clean_id = str(member_id).replace('{', '').replace('}', '')
                        member_info[clean_id] = name
                
                # print(f"    Found {len(member_info)} member mappings")
                # if member_info:
                #     print(f"    Sample members: {list(member_info.values())[:3]}")
                
                # Update team mapping with member names if we can match them
                for team in teams:
                    team_id = team.get('id')
                    owners = team.get('owners', [])
                    
                    if owners and isinstance(owners, list):
                        primary_owner_id = str(owners[0]).replace('{', '').replace('}', '')
                        
                        # Try to find this owner in our member mapping
                        for member_id, member_name in member_info.items():
                            clean_member_id = str(member_id).replace('{', '').replace('}', '')
                            if clean_member_id == primary_owner_id:
                                team_owner_map[team_id] = member_name
                                # print(f"    Matched Team {team_id} -> {member_name}")
                                break
                    
        except Exception as e:
            print(f"    Could not fetch member info: {e}")
        
        # print(f"    Final team mapping: {len(team_owner_map)} teams")
        return team_owner_map

    def get_current_league_members(self, strategy_years: List[int]) -> set:
        """Get the set of current league members from the most recent year, excluding specified members"""
        if not strategy_years:
            return set()
        
        # Use the most recent year to determine current members
        current_year = max(strategy_years)
        print(f"Determining current league members from {current_year}...")
        
        try:
            # Get league data for the most recent year
            data = self.get_league_data(current_year)
            if not data:
                print(f"Could not get data for {current_year}")
                return set()
            
            # Build team-owner mapping for current year
            current_team_mapping = self.build_team_owner_mapping(data, current_year)
            
            # Get all current owner names and normalize them
            current_owners = set()
            excluded_count = 0
            for owner_name in current_team_mapping.values():
                normalized_name = self.get_consistent_owner_name(owner_name)
                
                # Check if this member should be excluded
                if normalized_name in self.excluded_members:
                    excluded_count += 1
                    print(f"  Excluding {normalized_name} from current analysis")
                else:
                    current_owners.add(normalized_name)
            
            print(f"Found {len(current_owners)} current league members")
            if excluded_count > 0:
                print(f"Excluded {excluded_count} specified members from analysis")
            return current_owners
            
        except Exception as e:
            print(f"Error getting current league members: {e}")
            return set()

    def normalize_owner_name(self, name: str) -> str:
        """Normalize owner names to handle minor spelling differences"""
        if not name or name.startswith('Team '):
            return name
        
        # Basic normalization
        normalized = name.strip().lower()
        
        # Remove common variations
        normalized = normalized.replace('.', '')
        normalized = normalized.replace(',', '')
        normalized = normalized.replace('_', ' ')
        normalized = normalized.replace('-', ' ')
        
        # Handle multiple spaces
        normalized = ' '.join(normalized.split())
        
        # Title case for consistency
        return normalized.title()

    def get_consistent_owner_name(self, owner_name: str) -> str:
        """Get consistent owner name by checking against known variations and custom mapping"""
        if not hasattr(self, 'owner_name_mapping'):
            self.owner_name_mapping = {}
        
        normalized = self.normalize_owner_name(owner_name)
        
        # First check custom name mapping (case-insensitive)
        for espn_name, real_name in self.custom_name_mapping.items():
            if normalized.lower() == espn_name.lower():
                return real_name
        
        # Check if we've seen a similar name before
        for existing_name, canonical_name in self.owner_name_mapping.items():
            if self.names_are_similar(normalized, existing_name):
                return canonical_name
        
        # First time seeing this name - make it canonical
        self.owner_name_mapping[normalized] = normalized
        return normalized

    def names_are_similar(self, name1: str, name2: str) -> bool:
        """Check if two names are similar enough to be the same person"""
        if not name1 or not name2:
            return False
        
        # Exact match after normalization
        if name1 == name2:
            return True
        
        # Split into words and check for substantial overlap
        words1 = set(name1.split())
        words2 = set(name2.split())
        
        # If either name is a subset of the other
        if words1.issubset(words2) or words2.issubset(words1):
            return True
        
        # Check for significant word overlap (>50% of words match)
        if len(words1) > 0 and len(words2) > 0:
            overlap = len(words1.intersection(words2))
            min_words = min(len(words1), len(words2))
            if overlap / min_words > 0.5:
                return True
        
        return False

    def extract_owner_name(self, team_info: Dict, team_id: int, owner_info_map: Dict) -> str:
        """Extract owner name from team info and owner mapping"""
        owner_name = ""
        
        # Strategy 1: Use owner ID mapping to get actual owner names
        if owner_info_map and 'owners' in team_info:
            owners = team_info['owners']
            if isinstance(owners, list) and owners:
                # Get primary owner (first one)
                primary_owner_id = str(owners[0]).replace('{', '').replace('}', '')
                
                # DEBUG: Show owner mapping info for first few teams (disabled)
                # if team_id in [1, 4, 6]:
                #     print(f"    Team {team_id}: owners={owners}, primary_id='{primary_owner_id}'")
                #     print(f"    Available owner IDs: {list(owner_info_map.keys())[:5]}...")
                
                if primary_owner_id in owner_info_map:
                    owner_name = owner_info_map[primary_owner_id]
                    # if team_id in [1, 4, 6]:
                    #     print(f"    Found owner: {owner_name}")
                    return self.get_consistent_owner_name(owner_name)
                # elif team_id in [1, 4, 6]:
                #     print(f"    Owner ID '{primary_owner_id}' not found in mapping")
        
        # Strategy 2: Check if primaryOwner field has owner info
        if 'primaryOwner' in team_info and isinstance(team_info['primaryOwner'], dict):
            owner_info = team_info['primaryOwner']
            if 'displayName' in owner_info:
                owner_name = str(owner_info['displayName']).strip()
                return self.get_consistent_owner_name(owner_name)
            elif 'firstName' in owner_info and 'lastName' in owner_info:
                first = str(owner_info.get('firstName', '')).strip()
                last = str(owner_info.get('lastName', '')).strip()
                if first or last:
                    owner_name = f"{first} {last}".strip()
                    return self.get_consistent_owner_name(owner_name)
        
        # Strategy 3: Use team name as fallback (better than team number)
        fallback_fields = ['name', 'abbrev']
        for field in fallback_fields:
            if field in team_info:
                value = str(team_info[field]).strip()
                if value and value != 'None':
                    owner_name = value
                    break
        
        # Strategy 4: Traditional ESPN team name parts
        if not owner_name:
            location = team_info.get('location', '').strip()
            nickname = team_info.get('nickname', '').strip()
            if location or nickname:
                owner_name = f"{location} {nickname}".strip()
        
        # Final fallback
        if not owner_name:
            owner_name = f"Team {team_id}"
        
        return self.get_consistent_owner_name(owner_name)

    def get_season_standings(self, year: int) -> Dict:
        """Get final standings and performance data for a season"""
        try:
            if year >= 2018:
                url = f"{self.base_url}/seasons/{year}/segments/0/leagues/{self.league_id}"
            else:
                url = f"{self.base_url}/leagueHistory/{self.league_id}?seasonId={year}"
            
            # Request standings, team data, and member data for owner names
            params = {'view': ['mStandings', 'mTeams', 'mMembers']}
            
            response = requests.get(url, params=params, cookies=self.cookies, timeout=15)
            
            if response.status_code == 200:
                data = response.json()
                return self.parse_standings_data(data, year)
            else:
                print(f"Failed to fetch standings for {year}: Status {response.status_code}")
                return {}
        except Exception as e:
            print(f"Error fetching standings for {year}: {e}")
            return {}

    def parse_standings_data(self, data: Dict, year: int) -> Dict:
        """Parse standings data to get team performance metrics"""
        teams = data.get('teams', [])
        team_owner_map = self.build_team_owner_mapping(data, year)
        
        standings = {}
        for team in teams:
            team_id = team.get('id')
            if not team_id:
                continue
                
            owner_name = team_owner_map.get(team_id, f"Team {team_id}")
            
            # Extract performance metrics
            record = team.get('record', {})
            standings[owner_name] = {
                'year': year,
                'wins': record.get('overall', {}).get('wins', 0),
                'losses': record.get('overall', {}).get('losses', 0),
                'points_for': record.get('overall', {}).get('pointsFor', 0),
                'points_against': record.get('overall', {}).get('pointsAgainst', 0),
                'playoff_seed': team.get('playoffSeed', 0),
                'draft_day_projected_rank': team.get('draftDayProjectedRank', 0),
                'current_projected_rank': team.get('currentProjectedRank', 0)
            }
        
        return standings

    def analyze_draft_success(self, all_years: List[int], all_drafts: List[Dict]) -> Dict:
        """Analyze which owners draft best by correlating draft picks with season performance"""
        print("Analyzing draft success vs season performance...")
        
        owner_performance = {}
        
        for year in all_years:
            print(f"  Getting performance data for {year}...")
            standings = self.get_season_standings(year)
            
            if not standings:
                continue
            
            # Build a team-to-owner mapping from our draft data for this year
            year_draft_data = [p for p in all_drafts if p['year'] == year]
            team_to_owner = {}
            for pick in year_draft_data:
                if pick['team_id'] not in team_to_owner:
                    team_to_owner[pick['team_id']] = pick['owner_name']
            
            
            # Update standings to use correct owner names
            corrected_standings = {}
            for owner_key, stats in standings.items():
                # Try to find the actual owner name from draft mapping
                actual_owner = None
                for team_id, draft_owner in team_to_owner.items():
                    # Check if this performance data matches this team
                    if f"Team {team_id}" == owner_key:
                        actual_owner = draft_owner
                        break
                
                if actual_owner:
                    corrected_standings[actual_owner] = stats
                else:
                    corrected_standings[owner_key] = stats
            
            standings = corrected_standings
                
            # Rank owners by performance (higher points = better)
            performance_ranking = sorted(standings.items(), 
                                       key=lambda x: x[1]['points_for'], 
                                       reverse=True)
            
            for rank, (owner, stats) in enumerate(performance_ranking, 1):
                normalized_owner = self.get_consistent_owner_name(owner)
                
                if normalized_owner not in owner_performance:
                    owner_performance[normalized_owner] = {
                        'seasons': [],
                        'avg_finish': 0,
                        'avg_points': 0,
                        'playoff_appearances': 0,
                        'championships': 0
                    }
                
                owner_performance[normalized_owner]['seasons'].append({
                    'year': year,
                    'finish': rank,
                    'points': stats['points_for'],
                    'wins': stats['wins'],
                    'made_playoffs': stats['playoff_seed'] > 0,
                    'champion': rank == 1  # Simplified - could check actual championship
                })
        
        # Calculate aggregate stats
        for owner, data in owner_performance.items():
            if data['seasons']:
                data['avg_finish'] = sum(s['finish'] for s in data['seasons']) / len(data['seasons'])
                data['avg_points'] = sum(s['points'] for s in data['seasons']) / len(data['seasons'])
                data['playoff_appearances'] = sum(1 for s in data['seasons'] if s['made_playoffs'])
                data['championships'] = sum(1 for s in data['seasons'] if s['champion'])
                data['seasons_played'] = len(data['seasons'])
        
        return owner_performance

    def calculate_draft_efficiency(self, drafts_df, performance_data) -> Dict:
        """Calculate how well owners convert draft position to performance"""
        efficiency_scores = {}
        
        for owner in drafts_df['owner_name'].unique():
            normalized_owner = self.get_consistent_owner_name(owner)
            owner_drafts = drafts_df[drafts_df['owner_name'] == owner]
            
            if normalized_owner not in performance_data:
                continue
                
            # Get performance data
            perf_data = performance_data[normalized_owner]
            
            # Calculate draft position quality vs results
            years_data = []
            for season in perf_data['seasons']:
                year = season['year']
                year_drafts = owner_drafts[owner_drafts['year'] == year]
                
                if len(year_drafts) > 0:
                    # Calculate early round (1-6) pick quality
                    early_picks = year_drafts[year_drafts['round'] <= 6]
                    avg_early_pick = early_picks['pick_number'].mean() if len(early_picks) > 0 else 100
                    
                    years_data.append({
                        'year': year,
                        'finish': season['finish'],
                        'points': season['points'],
                        'avg_early_pick_pos': avg_early_pick
                    })
            
            if years_data:
                # Calculate efficiency: better finish with worse draft position = more efficient
                avg_finish = sum(d['finish'] for d in years_data) / len(years_data)
                avg_early_pick = sum(d['avg_early_pick_pos'] for d in years_data) / len(years_data)
                
                # Lower finish rank and higher pick numbers = more efficient
                efficiency = (17 - avg_finish) / (avg_early_pick / 10)  # Normalized score
                
                efficiency_scores[normalized_owner] = {
                    'efficiency_score': efficiency,
                    'avg_finish': avg_finish,
                    'avg_early_pick_position': avg_early_pick,
                    'seasons_analyzed': len(years_data)
                }
        
        return efficiency_scores

    def get_nfl_team_name(self, team_id: int) -> str:
        """Convert NFL team ID to team abbreviation"""
        nfl_teams = {
            1: 'ATL', 2: 'BUF', 3: 'CHI', 4: 'CIN', 5: 'CLE', 6: 'DAL', 7: 'DEN',
            8: 'DET', 9: 'GB', 10: 'TEN', 11: 'IND', 12: 'KC', 13: 'LV', 14: 'LAR',
            15: 'MIA', 16: 'MIN', 17: 'NE', 18: 'NO', 19: 'NYG', 20: 'NYJ',
            21: 'PHI', 22: 'ARI', 23: 'PIT', 24: 'LAC', 25: 'SF', 26: 'SEA',
            27: 'TB', 28: 'WAS', 29: 'CAR', 30: 'JAX', 33: 'BAL', 34: 'HOU'
        }
        return nfl_teams.get(team_id, 'UNK')

    def analyze_owner_patterns(self, personality_df: pd.DataFrame, strategy_df: pd.DataFrame, current_members: set = None) -> Dict:
        """Analyze individual owner drafting tendencies with tiered approach - current members only"""
        tendencies = {}
        
        # Get list of owners to analyze (current members only if specified)
        if current_members:
            all_owners = personality_df['owner_name'].unique()
            owners_to_analyze = []
            for owner in all_owners:
                normalized_owner = self.get_consistent_owner_name(owner)
                if normalized_owner in current_members:
                    owners_to_analyze.append(owner)
            print(f"Analyzing {len(owners_to_analyze)} current owners (excluding {len(all_owners) - len(owners_to_analyze)} former members)")
        else:
            owners_to_analyze = personality_df['owner_name'].unique()
            print("Analyzing all historical owners")
        
        for owner in owners_to_analyze:
            # Personality traits from longer dataset
            personality_picks = personality_df[personality_df['owner_name'] == owner]
            
            # Strategy traits from recent dataset
            strategy_picks = strategy_df[strategy_df['owner_name'] == owner]
            
            # PERSONALITY TRAITS (stable, long-term patterns)
            
            # Risk tolerance - variance in draft picks relative to position
            position_variance = personality_picks.groupby('position')['pick_number'].std().fillna(0)
            early_picks = personality_picks[personality_picks['round'] <= 6]
            
            # QB philosophy - consistent timing preference
            qb_picks = personality_picks[personality_picks['position'] == 'QB']
            qb_timing_consistency = qb_picks['round'].std() if len(qb_picks) > 1 else 0
            
            # Overall position philosophy
            early_rb_rate = early_picks['position'].value_counts(normalize=True).get('RB', 0)
            early_wr_rate = early_picks['position'].value_counts(normalize=True).get('WR', 0)
            
            # STRATEGY TRAITS (recent, adaptive patterns)
            
            # Current position preferences by round
            position_by_round = {}
            for round_num in range(1, 17):
                round_picks = strategy_picks[strategy_picks['round'] == round_num]
                if not round_picks.empty:
                    pos_counts = round_picks['position'].value_counts(normalize=True)
                    position_by_round[round_num] = pos_counts.to_dict()
            
            # Recent trends
            recent_avg_qb = strategy_picks[strategy_picks['position'] == 'QB']['round'].mean()
            recent_avg_te = strategy_picks[strategy_picks['position'] == 'TE']['round'].mean()
            
            # Adaptation to meta - comparing recent vs historical
            historical_qb_avg = qb_picks['round'].mean() if len(qb_picks) > 0 else 0
            qb_trend_shift = recent_avg_qb - historical_qb_avg if not pd.isna(recent_avg_qb) and historical_qb_avg > 0 else 0
            
            tendencies[owner] = {
                # PERSONALITY (long-term, stable)
                'total_personality_drafts': len(personality_picks),
                'risk_tolerance': position_variance.mean(),  # Higher = more variance/risk
                'qb_timing_consistency': qb_timing_consistency,  # Lower = more consistent
                'rb_philosophy': early_rb_rate,  # Historical RB preference
                'wr_philosophy': early_wr_rate,  # Historical WR preference
                'positional_variance': position_variance.to_dict(),
                
                # STRATEGY (recent, adaptive)
                'total_strategy_drafts': len(strategy_picks),
                'position_by_round': position_by_round,
                'current_qb_round': recent_avg_qb,
                'current_te_round': recent_avg_te,
                'qb_trend_shift': qb_trend_shift,  # Positive = drafting QB later than historically
                
                # COMBINED INSIGHTS
                'predictability': 'high' if qb_timing_consistency < 1.5 and len(personality_picks) >= 5 else 'medium' if len(personality_picks) >= 3 else 'low',
                'adaptation_level': 'high' if abs(qb_trend_shift) > 2 else 'medium' if abs(qb_trend_shift) > 1 else 'low'
            }
        
        return tendencies

    def analyze_positional_trends(self, df: pd.DataFrame) -> Dict:
        """Analyze when positions typically get drafted"""
        trends = {}
        
        for position in ['QB', 'RB', 'WR', 'TE', 'K', 'D/ST']:
            pos_data = df[df['position'] == position]
            if not pos_data.empty:
                trends[position] = {
                    'avg_round': pos_data['round'].mean(),
                    'earliest_pick': pos_data['pick_number'].min(),
                    'latest_pick': pos_data['pick_number'].max(),
                    'round_distribution': pos_data['round'].value_counts().to_dict(),
                    'total_drafted': len(pos_data)
                }
        
        # Calculate positional runs (3+ same position in consecutive picks)
        runs = self.detect_positional_runs(df)
        trends['positional_runs'] = runs
        
        return trends

    def detect_positional_runs(self, df: pd.DataFrame) -> Dict:
        """Detect when positional runs occur in drafts"""
        runs = {'QB': [], 'RB': [], 'WR': [], 'TE': []}
        
        for year in df['year'].unique():
            year_data = df[df['year'] == year].sort_values('pick_number')
            positions = year_data['position'].tolist()
            
            current_pos = None
            current_count = 0
            start_pick = 0
            
            for i, pos in enumerate(positions):
                if pos == current_pos:
                    current_count += 1
                else:
                    if current_count >= 3 and current_pos in runs:
                        runs[current_pos].append({
                            'year': year,
                            'start_pick': start_pick,
                            'count': current_count,
                            'round': year_data.iloc[start_pick]['round']
                        })
                    current_pos = pos
                    current_count = 1
                    start_pick = i
        
        return runs

    def calculate_historical_value(self, df: pd.DataFrame) -> Dict:
        """Calculate value metrics based on historical drafts"""
        # This is simplified - in reality you'd want actual player performance data
        value_metrics = {}
        
        for position in ['QB', 'RB', 'WR', 'TE']:
            pos_data = df[df['position'] == position]
            if not pos_data.empty:
                # Calculate scarcity by round
                round_counts = pos_data['round'].value_counts().sort_index()
                
                value_metrics[position] = {
                    'scarcity_by_round': round_counts.to_dict(),
                    'median_draft_round': pos_data['round'].median(),
                    'top_tier_cutoff': pos_data[pos_data['round'] <= 3]['pick_number'].max() if len(pos_data[pos_data['round'] <= 3]) > 0 else 0
                }
        
        return value_metrics

    def predict_owner_behavior(self, owner_name: str, current_round: int, 
                             positions_needed: List[str]) -> Dict:
        """Predict what an owner is likely to draft using tiered analysis"""
        if owner_name not in self.owner_tendencies:
            return {'likelihood': {}, 'confidence': 'low', 'reasoning': 'No historical data'}
        
        tendencies = self.owner_tendencies[owner_name]
        
        # Use recent strategy data for round-specific predictions
        round_prefs = tendencies.get('position_by_round', {}).get(current_round, {})
        
        # Adjust for positions needed
        adjusted_prefs = {}
        for pos in positions_needed:
            base_prob = round_prefs.get(pos, 0.1)  # Default low probability
            
            # Apply personality modifiers
            if pos == 'QB':
                qb_round = tendencies.get('current_qb_round', 10)
                if current_round < qb_round - 2:
                    base_prob *= 0.3  # Much less likely if too early
                elif abs(current_round - qb_round) <= 1:
                    base_prob *= 2.0  # More likely around typical round
            
            elif pos == 'RB':
                rb_philosophy = tendencies.get('rb_philosophy', 0.3)
                if current_round <= 6:
                    base_prob *= (1 + rb_philosophy)  # Boost for RB-heavy owners
            
            elif pos == 'WR':
                wr_philosophy = tendencies.get('wr_philosophy', 0.3)
                if current_round <= 6:
                    base_prob *= (1 + wr_philosophy)  # Boost for WR-heavy owners
            
            adjusted_prefs[pos] = base_prob
        
        # Normalize probabilities
        total = sum(adjusted_prefs.values())
        if total > 0:
            adjusted_prefs = {k: v/total for k, v in adjusted_prefs.items()}
        
        # Determine confidence based on data quality and predictability
        confidence = self.calculate_prediction_confidence(tendencies)
        
        return {
            'likelihood': adjusted_prefs,
            'confidence': confidence,
            'reasoning': self.get_prediction_reasoning(owner_name, current_round, tendencies)
        }

    def calculate_prediction_confidence(self, tendencies: Dict) -> str:
        """Calculate confidence level for predictions"""
        strategy_drafts = tendencies.get('total_strategy_drafts', 0)
        predictability = tendencies.get('predictability', 'low')
        
        if strategy_drafts >= 4 and predictability == 'high':
            return 'high'
        elif strategy_drafts >= 3 and predictability in ['high', 'medium']:
            return 'medium'
        elif strategy_drafts >= 2:
            return 'low'
        else:
            return 'very_low'

    def get_prediction_reasoning(self, owner_name: str, current_round: int, tendencies: Dict) -> List[str]:
        """Generate detailed reasoning for predictions using tiered analysis"""
        reasoning = []
        
        # Personality-based insights (long-term patterns)
        risk_tolerance = tendencies.get('risk_tolerance', 0)
        if risk_tolerance > 20:
            reasoning.append("High risk tolerance - may reach for upside picks")
        elif risk_tolerance < 5:
            reasoning.append("Conservative drafter - sticks to consensus")
        
        predictability = tendencies.get('predictability', 'medium')
        if predictability == 'high':
            reasoning.append("Highly predictable draft pattern")
        elif predictability == 'low':
            reasoning.append("Unpredictable - harder to forecast")
        
        # Strategy-based insights (recent patterns)
        current_qb = tendencies.get('current_qb_round', 0)
        if current_qb > 0:
            if current_round <= current_qb - 2:
                reasoning.append(f"Usually waits until round {current_qb:.1f} for QB")
            elif current_round >= current_qb - 1 and current_round <= current_qb + 1:
                reasoning.append(f"Prime QB round (avg: {current_qb:.1f})")
            elif current_round > current_qb + 2:
                reasoning.append("QB need becoming urgent")
        
        # Philosophy insights
        rb_phil = tendencies.get('rb_philosophy', 0)
        wr_phil = tendencies.get('wr_philosophy', 0)
        if rb_phil > 0.5:
            reasoning.append("Historically RB-focused early")
        elif wr_phil > 0.5:
            reasoning.append("Historically WR-focused early")
        
        # Adaptation insights
        adaptation = tendencies.get('adaptation_level', 'low')
        qb_shift = tendencies.get('qb_trend_shift', 0)
        if adaptation == 'high':
            if qb_shift > 2:
                reasoning.append("Recently drafting QB later than historically")
            elif qb_shift < -2:
                reasoning.append("Recently drafting QB earlier than historically")
            reasoning.append("Adapts strategy year-to-year")
        elif adaptation == 'low':
            reasoning.append("Consistent strategy - doesn't adapt much")
        
        return reasoning

    def generate_recommendations(self, current_pick: int, your_roster: List[Dict],
                               last_few_picks: List[Dict]) -> Dict:
        """Generate draft recommendations based on situation and history"""
        current_round = ((current_pick - 1) // 16) + 1
        
        # Analyze roster needs
        roster_positions = [player['position'] for player in your_roster]
        position_counts = {pos: roster_positions.count(pos) for pos in ['QB', 'RB', 'WR', 'TE']}
        
        # Determine positional needs based on 16-team strategy
        needs = self.calculate_positional_needs(position_counts, current_round)
        
        # Check for positional runs
        recent_positions = [pick['position'] for pick in last_few_picks[-5:]]
        run_warning = self.check_positional_runs(recent_positions)
        
        # Generate strategy recommendations
        recommendations = {
            'primary_targets': self.get_primary_targets(needs, current_round),
            'positional_needs': needs,
            'run_warnings': run_warning,
            'round_strategy': self.get_round_strategy(current_round),
            'next_pick_strategy': self.get_next_pick_strategy(current_pick)
        }
        
        return recommendations

    def create_live_draft_optimizer(self) -> 'LiveDraftOptimizer':
        """Create a live draft optimization tool"""
        return LiveDraftOptimizer(self)

    def calculate_positional_needs(self, position_counts: Dict, current_round: int) -> Dict:
        """Calculate positional needs for 16-team half PPR"""
        # Standard 16-team roster construction targets
        targets = {
            'QB': 1 if current_round <= 10 else 2,
            'RB': 4 if current_round <= 8 else 6,
            'WR': 5 if current_round <= 8 else 7,
            'TE': 1 if current_round <= 12 else 2
        }
        
        needs = {}
        for pos, target in targets.items():
            current = position_counts.get(pos, 0)
            needs[pos] = max(0, target - current)
        
        return needs

    def check_positional_runs(self, recent_positions: List[str]) -> List[str]:
        """Check if a positional run is happening"""
        warnings = []
        
        if len(recent_positions) >= 3:
            last_three = recent_positions[-3:]
            if len(set(last_three)) == 1:
                warnings.append(f"{last_three[0]} run detected - may continue")
        
        return warnings

    def get_primary_targets(self, needs: Dict, current_round: int) -> List[str]:
        """Get primary positional targets based on needs and round"""
        targets = []
        
        # Early rounds (1-6): Focus on RB/WR
        if current_round <= 6:
            if needs.get('RB', 0) > 0:
                targets.append('RB')
            if needs.get('WR', 0) > 0:
                targets.append('WR')
        
        # Middle rounds (7-12): Address all needs
        elif current_round <= 12:
            for pos in ['RB', 'WR', 'QB', 'TE']:
                if needs.get(pos, 0) > 0:
                    targets.append(pos)
        
        # Late rounds (13+): Fill roster
        else:
            targets = ['RB', 'WR', 'QB', 'TE', 'K', 'D/ST']
        
        return targets

    def get_round_strategy(self, current_round: int) -> str:
        """Get strategy advice for current round"""
        if current_round <= 3:
            return "Focus on elite RB/WR. Secure foundational players."
        elif current_round <= 6:
            return "Continue RB/WR focus. Consider QB if elite option falls."
        elif current_round <= 9:
            return "Address QB/TE needs. Look for high-upside RB/WR."
        elif current_round <= 12:
            return "Fill roster holes. Consider lottery tickets."
        else:
            return "Handcuffs, sleepers, and K/D/ST."

    def get_next_pick_strategy(self, current_pick: int) -> str:
        """Strategy for next pick consideration"""
        picks_until_next = 32 - (current_pick % 32) if current_pick % 32 != 0 else 32
        
        if picks_until_next <= 5:
            return f"Next pick in {picks_until_next}. Consider positional runs."
        elif picks_until_next <= 10:
            return f"Next pick in {picks_until_next}. Moderate urgency."
        else:
            return f"Next pick in {picks_until_next}. Can be patient."

    def save_analysis(self, analysis: Dict, filename: str = None):
        """Save analysis to file"""
        if filename is None:
            filename = f"draft_analysis_{self.league_id}_{datetime.now().strftime('%Y%m%d')}.json"
        
        # Convert DataFrames to JSON-serializable format
        save_data = analysis.copy()
        if 'drafts' in save_data:
            save_data['drafts'] = save_data['drafts'].to_dict('records')
        
        with open(filename, 'w') as f:
            json.dump(save_data, f, indent=2, default=str)
        
        print(f"Analysis saved to {filename}")


class LiveDraftOptimizer:
    """Live draft optimization tool for real-time draft assistance"""
    
    def __init__(self, assistant: ESPNDraftAssistant):
        self.assistant = assistant
        self.draft_state = {
            'picked_players': set(),
            'available_players': [],
            'current_round': 1,
            'current_pick': 1,
            'your_roster': [],
            'other_rosters': {},
            'draft_order': []  # List of owner names in draft order
        }
        
        # Default player rankings (would ideally be loaded from external source)
        self.player_rankings = {}  # player_name -> ranking
        
    def initialize_draft(self, draft_order: List[str], player_pool: List[Dict] = None):
        """Initialize draft with owner order and available players"""
        self.draft_state['draft_order'] = draft_order
        self.draft_state['other_rosters'] = {owner: [] for owner in draft_order}
        
        if player_pool:
            self.draft_state['available_players'] = player_pool
            # Create rankings based on ADP or expert rankings
            for i, player in enumerate(player_pool):
                self.player_rankings[player['name']] = i + 1
    
    def record_pick(self, player_name: str, owner: str):
        """Record a pick and update draft state"""
        self.draft_state['picked_players'].add(player_name)
        
        if owner in self.draft_state['other_rosters']:
            self.draft_state['other_rosters'][owner].append(player_name)
        
        # Remove from available players
        self.draft_state['available_players'] = [
            p for p in self.draft_state['available_players'] 
            if p['name'] != player_name
        ]
        
        # Advance draft position
        self.advance_draft_position()
    
    def advance_draft_position(self):
        """Move to next pick in draft"""
        picks_per_round = len(self.draft_state['draft_order'])
        self.draft_state['current_pick'] += 1
        
        # Calculate round (1-indexed)
        self.draft_state['current_round'] = ((self.draft_state['current_pick'] - 1) // picks_per_round) + 1
    
    def get_optimal_pick_recommendations(self, your_owner_name: str) -> Dict:
        """Get optimized pick recommendations for your turn"""
        current_roster = self.draft_state['other_rosters'].get(your_owner_name, [])
        current_round = self.draft_state['current_round']
        available_players = self.draft_state['available_players']
        
        # Analyze roster needs
        position_counts = self._count_roster_positions(current_roster)
        needs = self.assistant.calculate_positional_needs(position_counts, current_round)
        
        # Get predictions for next few picks
        next_pick_predictions = self._predict_next_picks()
        
        # Score available players
        player_scores = []
        for player in available_players[:50]:  # Top 50 available
            score = self._calculate_player_value(player, needs, next_pick_predictions, current_round)
            player_scores.append({
                'player': player,
                'score': score,
                'position': player.get('position', 'UNK'),
                'reasoning': self._get_pick_reasoning(player, needs, current_round)
            })
        
        # Sort by score (highest first)
        player_scores.sort(key=lambda x: x['score'], reverse=True)
        
        return {
            'top_recommendations': player_scores[:10],
            'positional_needs': needs,
            'next_pick_predictions': next_pick_predictions,
            'round_strategy': self.assistant.get_round_strategy(current_round),
            'scarcity_alerts': self._get_scarcity_alerts(available_players, needs)
        }
    
    def _count_roster_positions(self, roster: List[str]) -> Dict:
        """Count positions in current roster"""
        # This would ideally map player names to positions
        # For now, return mock data
        return {'QB': 0, 'RB': len([p for p in roster if 'RB' in p]), 
                'WR': len([p for p in roster if 'WR' in p]), 'TE': 0}
    
    def _predict_next_picks(self) -> List[Dict]:
        """Predict what positions the next few owners will draft"""
        predictions = []
        current_pick_in_round = (self.draft_state['current_pick'] - 1) % len(self.draft_state['draft_order'])
        
        for i in range(1, min(6, len(self.draft_state['draft_order']))):  # Next 5 picks
            pick_idx = (current_pick_in_round + i) % len(self.draft_state['draft_order'])
            owner = self.draft_state['draft_order'][pick_idx]
            
            # Use owner tendencies to predict
            if owner in self.assistant.owner_tendencies:
                tendencies = self.assistant.owner_tendencies[owner]
                current_round = self.draft_state['current_round']
                
                # Get position preferences for this round
                round_prefs = tendencies.get('position_by_round', {}).get(current_round, {})
                likely_position = max(round_prefs, key=round_prefs.get) if round_prefs else 'RB'
                
                predictions.append({
                    'owner': owner,
                    'pick_number': self.draft_state['current_pick'] + i,
                    'likely_position': likely_position,
                    'confidence': self._get_prediction_confidence(tendencies)
                })
        
        return predictions
    
    def _calculate_player_value(self, player: Dict, needs: Dict, predictions: List[Dict], current_round: int) -> float:
        """Calculate overall value score for a player"""
        base_ranking = self.player_rankings.get(player['name'], 999)
        position = player.get('position', 'UNK')
        
        # Start with inverse ranking (lower rank = higher score)
        score = 1000 - base_ranking
        
        # Boost for positional needs
        position_need = needs.get(position, 0)
        score += position_need * 50
        
        # Adjust for predicted picks (scarcity)
        upcoming_position_demand = sum(1 for p in predictions if p['likely_position'] == position)
        score += upcoming_position_demand * 25
        
        # Round-based adjustments
        if current_round <= 3 and position in ['RB', 'WR']:
            score += 30  # Premium positions in early rounds
        elif current_round >= 10 and position in ['QB', 'TE']:
            score += 20  # Value positions in later rounds
            
        return score
    
    def _get_prediction_confidence(self, tendencies: Dict) -> str:
        """Get confidence level for owner predictions"""
        predictability = tendencies.get('predictability', 'medium')
        return 'high' if predictability == 'high' else 'medium'
    
    def _get_pick_reasoning(self, player: Dict, needs: Dict, current_round: int) -> List[str]:
        """Generate reasoning for why to pick this player"""
        reasoning = []
        position = player.get('position', 'UNK')
        
        if needs.get(position, 0) > 0:
            reasoning.append(f"Fills {position} need")
        
        if current_round <= 6 and position in ['RB', 'WR']:
            reasoning.append("Premium position early")
        
        ranking = self.player_rankings.get(player['name'], 999)
        if ranking <= current_round * 16:
            reasoning.append("Good value at current pick")
        
        return reasoning
    
    def _get_scarcity_alerts(self, available_players: List[Dict], needs: Dict) -> List[str]:
        """Alert about position scarcity"""
        alerts = []
        
        for position in ['QB', 'RB', 'WR', 'TE']:
            if needs.get(position, 0) > 0:
                position_players = [p for p in available_players if p.get('position') == position]
                if len(position_players) <= 3:
                    alerts.append(f"Only {len(position_players)} {position}s remaining!")
        
        return alerts


def main():
    """Example usage of the ESPN Draft Assistant with tiered analysis"""
    
    # Load configuration from environment variables
    LEAGUE_ID = int(os.getenv('LEAGUE_ID', 0))
    ESPN_S2 = os.getenv('ESPN_S2', '')
    SWID = os.getenv('SWID', '')
    
    # Parse years from environment (comma-separated strings)
    personality_years_str = os.getenv('PERSONALITY_YEARS', '2021,2022,2023,2024')
    strategy_years_str = os.getenv('STRATEGY_YEARS', '2022,2023,2024')
    
    PERSONALITY_YEARS = [int(year.strip()) for year in personality_years_str.split(',')]
    STRATEGY_YEARS = [int(year.strip()) for year in strategy_years_str.split(',')]
    
    # Validate configuration
    if LEAGUE_ID == 0:
        print("❌ Error: LEAGUE_ID not found in .env file")
        print("Please create a .env file with your league configuration")
        return
    
    if not ESPN_S2 or not SWID:
        print("⚠️  Warning: ESPN_S2 and/or SWID not found in .env file")
        print("This will only work for public leagues")
    
    print("ESPN Fantasy Football Draft Assistant")
    print("Advanced Tiered Analysis")
    print("=" * 50)
    print(f"League ID: {LEAGUE_ID}")
    print(f"Personality Years: {PERSONALITY_YEARS}")
    print(f"Strategy Years: {STRATEGY_YEARS}")
    print()
    
    # Initialize assistant with custom name mapping and excluded members
    excluded_members = ['4Ryano (FORMER MEMBER)']  # Exclude former members from analysis
    
    assistant = ESPNDraftAssistant(LEAGUE_ID, ESPN_S2, SWID, 
                                 excluded_members=excluded_members)
    
    # Test connection first
    print("Testing ESPN connection...")
    test_data = assistant.get_league_data(max(STRATEGY_YEARS))
    if not test_data:
        print("❌ Failed to connect to ESPN API")
        print("Check your league ID and cookies in .env file")
        return
    else:
        print("✅ Successfully connected to ESPN API")
    
    # Analyze historical data with tiered approach
    analysis = assistant.analyze_historical_drafts(PERSONALITY_YEARS, STRATEGY_YEARS)
    
    if not analysis:
        print("No data found. Check your league ID and cookies.")
        return
    
    # Draft Success Analysis
    print("\nDraft Success Rankings:")
    print("-" * 40)
    
    
    if 'draft_efficiency' in analysis and analysis['draft_efficiency']:
        efficiency_ranking = sorted(analysis['draft_efficiency'].items(), 
                                   key=lambda x: x[1]['efficiency_score'], 
                                   reverse=True)
        
        
        for rank, (owner, stats) in enumerate(efficiency_ranking, 1):
            avg_finish = stats['avg_finish']
            efficiency = stats['efficiency_score']
            seasons = stats['seasons_analyzed']
            
            print(f"{rank:2d}. {owner}")
            print(f"    Avg Finish: {avg_finish:.1f}")
            print(f"    Draft Efficiency: {efficiency:.2f}")
            print(f"    Seasons: {seasons}")
            print()
    else:
        print("No draft efficiency data available.")

    # Enhanced owner tendencies display - current members only
    print("Advanced Owner Analysis (Current League Members Only):")
    print("-" * 55)
    
    # Show current vs excluded members for transparency
    if 'current_members' in analysis and analysis['current_members']:
        print(f"Active members in analysis: {len(analysis['current_members'])}")
        print(f"Excluded members: {assistant.excluded_members}")
        print()
    for owner, tendencies in assistant.owner_tendencies.items():
        if tendencies.get('total_strategy_drafts', 0) >= 2:  # Only show owners with recent data
            print(f"\n{owner}:")
            print(f"  Data: {tendencies.get('total_personality_drafts', 0)} personality / {tendencies.get('total_strategy_drafts', 0)} strategy drafts")
            print(f"  Predictability: {tendencies.get('predictability', 'unknown')}")
            print(f"  Adaptation level: {tendencies.get('adaptation_level', 'unknown')}")
            print(f"  Current QB round: {tendencies.get('current_qb_round', 0):.1f}")
            print(f"  QB trend shift: {tendencies.get('qb_trend_shift', 0):+.1f} rounds")
            print(f"  Risk tolerance: {tendencies.get('risk_tolerance', 0):.1f}")
            
            # Show philosophy
            rb_phil = tendencies.get('rb_philosophy', 0)
            wr_phil = tendencies.get('wr_philosophy', 0)
            if rb_phil > 0.4:
                print(f"  Philosophy: RB-focused ({rb_phil:.1%})")
            elif wr_phil > 0.4:
                print(f"  Philosophy: WR-focused ({wr_phil:.1%})")
            else:
                print(f"  Philosophy: Balanced")
    
    # Display positional trends (from strategy years only)
    print(f"\nCurrent Positional Trends ({min(STRATEGY_YEARS)}-{max(STRATEGY_YEARS)}):")
    print("-" * 50)
    for position, trends in analysis['positional_trends'].items():
        if isinstance(trends, dict) and 'avg_round' in trends:
            print(f"{position}: Avg round {trends['avg_round']:.1f}, "
                  f"Range: {trends['earliest_pick']}-{trends['latest_pick']}")
    
    # Enhanced example prediction
    print("\nExample Advanced Prediction (Pick #32, Round 2):")
    print("-" * 55)
    current_roster = [{'position': 'WR', 'player': 'Ja\'Marr Chase'}]
    recent_picks = [{'position': 'RB'}, {'position': 'RB'}, {'position': 'WR'}]
    
    recs = assistant.generate_recommendations(32, current_roster, recent_picks)
    print(f"Primary targets: {', '.join(recs['primary_targets'])}")
    print(f"Strategy: {recs['round_strategy']}")
    if recs['run_warnings']:
        print(f"Warnings: {', '.join(recs['run_warnings'])}")
    
    # Example owner prediction
    if assistant.owner_tendencies:
        sample_owner = list(assistant.owner_tendencies.keys())[0]
        prediction = assistant.predict_owner_behavior(sample_owner, 5, ['QB', 'RB', 'WR'])
        print(f"\nSample Owner Prediction - {sample_owner} (Round 5):")
        print(f"Confidence: {prediction['confidence']}")
        print(f"Most likely: {max(prediction.get('likelihood', {}), key=prediction.get('likelihood', {}).get, default='N/A')}")
        if prediction.get('reasoning'):
            print("Reasoning:")
            for reason in prediction['reasoning'][:3]:  # Show top 3 reasons
                print(f"  • {reason}")
    
    # Save analysis
    assistant.save_analysis(analysis)
    
    # Demonstrate live draft optimizer
    print(f"\nLive Draft Optimizer Demo:")
    print("-" * 30)
    optimizer = assistant.create_live_draft_optimizer()
    
    # Mock draft setup
    draft_order = ["Nick Christus", "Shawn Lukose", "Adam Olen", "Alex Kite"]  # First 4 picks
    mock_players = [
        {'name': 'Christian McCaffrey', 'position': 'RB'},
        {'name': 'Tyreek Hill', 'position': 'WR'},
        {'name': 'Derrick Henry', 'position': 'RB'},
        {'name': 'Cooper Kupp', 'position': 'WR'}
    ]
    
    optimizer.initialize_draft(draft_order, mock_players)
    
    # Simulate first pick (Ja'Marr Chase)
    optimizer.record_pick("Christian McCaffrey", "Nick Christus")
    
    print("After Pick 1 (Christian McCaffrey to Nick):")
    print(f"Current Round: {optimizer.draft_state['current_round']}")
    print(f"Next Pick: {optimizer.draft_state['current_pick']}")
    print(f"Available Players: {len(optimizer.draft_state['available_players'])}")
    
    print(f"\nTiered analysis complete!")
    print(f"Personality insights from {len(PERSONALITY_YEARS)} years")
    print(f"Strategy insights from {len(STRATEGY_YEARS)} years")
    print("Ready for draft day!")

if __name__ == "__main__":
    main()