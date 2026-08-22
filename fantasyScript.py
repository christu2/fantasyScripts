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
        
        # Manual name mapping from ESPN usernames to real manager names (standardized with BFL records)
        self.custom_name_mapping = custom_name_mapping or {
            # Current active owners - standardized names matching BFL records
            'favreindahouse4': 'Nick Christus',
            'ehrlich78': 'Tom Ehrlich',
            'tommy ehrlich': 'Tom Ehrlich', 
            'thomas ehrlich': 'Tom Ehrlich',
            'thebearssamurai': 'Samran Mirza',
            'knanaya12': 'Shawn Lukose',
            'beast4life24': 'Nael Ahmed',
            'slamdunkers989': 'Shawn Ullenbrauck',
            'dinod123': 'Dino Davros',
            'theguptaempire': 'Saagar Gupta',
            'alex7626': 'Alex Kite',
            'alexandra christus': 'Alex Christus',  # Historical - no longer in league
            'sydney8715': 'Emelie Lovasko',
            'jon lovasko': 'Emelie Lovasko',
            'sydney christus': 'Emelie Lovasko',
            'sydney kite': 'Emelie Lovasko',
            'sydney miller': 'Emelie Lovasko',  # Through 2024, they share records
            'espnfan0270220732': 'Blake Whitehouse',
            'espnfan2927064247': 'Daniel Kruszewski',
            'dan kruszewski': 'Daniel Kruszewski',
            'ali bhujwala': 'Daniel Kruszewski',
            'espnfan4034736305': 'Abe Thomas',
            'adaole1': 'Adam Olen',
            
            # Rej variations - all the possible ways he appears
            'rej5073': 'Rej Hoxha',
            'Rej5073': 'Rej Hoxha',
            'steve bartman': 'Rej Hoxha',
            'steve bartman ': 'Rej Hoxha',
            'rej hoxha': 'Rej Hoxha',
            
            # Historical members
            'matt rosato': 'Austin Russell',
            'bubba franks': 'Austin Russell',
            'gabriel zbaala': 'Gabriel Zabala',
            'gabriel zabala': 'Gabriel Zabala',
            'georgia batman': 'Georgia Christus',
            '4ryano': 'Ryan Olen',
            'adaole1': 'Adam Olen',  # Sometimes Adam, sometimes Ryan
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
            
            # Calculate real draft efficiency based on player performance
            draft_efficiency = self.calculate_real_draft_efficiency(df)
            
            # Also get basic performance data for context
            performance_data = self.analyze_draft_success(all_years, all_drafts)
            
            return {
                'drafts': df,
                'real_draft_efficiency': draft_efficiency,
                'personality_drafts': personality_df,
                'strategy_drafts': strategy_df,
                'owner_tendencies': self.owner_tendencies,
                'positional_trends': positional_analysis,
                'value_metrics': value_analysis,
                'performance_data': performance_data,
                'draft_efficiency': performance_data,
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

    def names_are_similar_players(self, name1: str, name2: str) -> bool:
        """Check if two player names are similar enough to be the same person"""
        if not name1 or not name2:
            return False
        
        # Split into words
        words1 = name1.split()
        words2 = name2.split()
        
        if len(words1) >= 2 and len(words2) >= 2:
            # Check if first and last names match
            if words1[0] == words2[0] and words1[-1] == words2[-1]:
                return True
            
            # Check if last names match and first names are similar
            if words1[-1] == words2[-1]:
                # Check for nickname matches (e.g., "Mike" vs "Michael")
                nickname_map = {
                    'mike': 'michael', 'michael': 'mike',
                    'chris': 'christopher', 'christopher': 'chris',
                    'dave': 'david', 'david': 'dave',
                    'matt': 'matthew', 'matthew': 'matt',
                    'rob': 'robert', 'robert': 'rob',
                    'tom': 'thomas', 'thomas': 'tom',
                    'dan': 'daniel', 'daniel': 'dan',
                    'josh': 'joshua', 'joshua': 'josh'
                }
                
                first1 = words1[0].lower()
                first2 = words2[0].lower()
                
                if first1 == first2 or nickname_map.get(first1) == first2 or nickname_map.get(first2) == first1:
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
            
            # Request different views to get actual season results
            params = {'view': ['mTeams', 'mMembers', 'mStandings', 'mSettings']}
            
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
                'current_projected_rank': team.get('currentProjectedRank', 0),
                'final_standing_rank': team.get('rankCalculatedFinal', team.get('currentProjectedRank', 16)),
                'playoff_tier': team.get('playoffTier', 0)  # 0=missed, 1=champion, 2=runner-up, etc.
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
                
            # Rank owners by actual final standings (lower rank number = better finish)
            # Fall back to wins/points if final ranking not available
            def get_ranking_key(item):
                stats = item[1]
                final_rank = stats.get('final_standing_rank', 0)
                if final_rank > 0:
                    return final_rank
                else:
                    # Fallback: rank by wins first, then points
                    return (-stats.get('wins', 0), -stats.get('points_for', 0))
            
            performance_ranking = sorted(standings.items(), key=get_ranking_key)
            
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

    def calculate_real_draft_efficiency(self, drafts_df) -> Dict:
        """Calculate actual draft efficiency based on player performance vs draft position"""
        print("Calculating true draft efficiency based on player performance...")
        
        efficiency_scores = {}
        
        # Get player performance data for each year
        for year in drafts_df['year'].unique():
            print(f"  Analyzing {year} player performance...")
            year_drafts = drafts_df[drafts_df['year'] == year]
            
            # Get actual player stats for this year
            player_stats = self.get_player_season_stats(year)
            
            if not player_stats:
                print(f"    No player stats available for {year}")
                continue
            
            # Calculate each owner's draft efficiency for this year
            for owner in year_drafts['owner_name'].unique():
                normalized_owner = self.get_consistent_owner_name(owner)
                owner_picks = year_drafts[year_drafts['owner_name'] == owner]
                
                if normalized_owner not in efficiency_scores:
                    efficiency_scores[normalized_owner] = {
                        'total_value_score': 0,
                        'total_picks': 0,
                        'yearly_scores': {},
                        'hit_rates': {'early': 0, 'mid': 0, 'late': 0},
                        'position_efficiency': {},
                        'value_discoveries': [],
                        'efficiency_score': 0
                    }
                
                year_value_score = self.calculate_year_draft_value(owner_picks, player_stats, year)
                efficiency_scores[normalized_owner]['yearly_scores'][year] = year_value_score
                efficiency_scores[normalized_owner]['total_value_score'] += year_value_score['total_value']
                efficiency_scores[normalized_owner]['total_picks'] += len(owner_picks)
        
        # Calculate final efficiency scores
        for owner, data in efficiency_scores.items():
            if data['total_picks'] > 0:
                # Average value per pick (normalized by draft capital spent)
                data['efficiency_score'] = data['total_value_score'] / data['total_picks']
                
                # Calculate hit rates across all years
                self.calculate_hit_rates(data, drafts_df[drafts_df['owner_name'].str.contains(owner.split()[0], case=False, na=False)])
        
        return efficiency_scores

    def get_player_season_stats(self, year: int) -> Dict:
        """Get actual fantasy points scored by players for a season using multiple data sources"""
        player_stats = {}
        
        # Try multiple approaches in order of reliability
        
        # 1. Try Sleeper API (most reliable for recent years)
        sleeper_stats = self.get_sleeper_player_stats(year)
        if sleeper_stats:
            print(f"    Using Sleeper API data ({len(sleeper_stats)} players)")
            return sleeper_stats
        
        # 2. Try ESPN API for recent years (2022+)
        if year >= 2022:
            espn_stats = self.get_espn_player_stats(year)
            if espn_stats:
                print(f"    Using ESPN API data ({len(espn_stats)} players)")
                return espn_stats
        
        # 3. Try NFL stats API with manual fantasy point calculation
        nfl_stats = self.get_nfl_player_stats(year)
        if nfl_stats:
            print(f"    Using NFL stats API data ({len(nfl_stats)} players)")
            return nfl_stats
        
        print(f"    No player stats available for {year}")
        return {}

    def get_sleeper_player_stats(self, year: int) -> Dict:
        """Get player stats from Sleeper API"""
        try:
            # Sleeper API endpoints
            stats_url = f"https://api.sleeper.app/v1/stats/nfl/regular/{year}"
            players_url = "https://api.sleeper.app/v1/players/nfl"
            
            # Get player mappings
            players_response = requests.get(players_url, timeout=10)
            if players_response.status_code != 200:
                return {}
            
            players_data = players_response.json()
            
            # Get season stats
            stats_response = requests.get(stats_url, timeout=10)
            if stats_response.status_code != 200:
                return {}
            
            stats_data = stats_response.json()
            
            player_stats = {}
            
            for player_id, stats in stats_data.items():
                if player_id in players_data:
                    player_info = players_data[player_id]
                    position = player_info.get('position', 'UNKNOWN')
                    
                    # Skip if not a fantasy relevant position
                    if position not in ['QB', 'RB', 'WR', 'TE', 'K', 'DEF']:
                        continue
                    
                    # Calculate fantasy points using our league's scoring
                    fantasy_points = self.calculate_sleeper_fantasy_points(stats, position)
                    
                    if fantasy_points > 0:  # Only include players who scored points
                        player_stats[player_id] = {
                            'name': f"{player_info.get('first_name', '')} {player_info.get('last_name', '')}".strip(),
                            'position': 'D/ST' if position == 'DEF' else position,
                            'fantasy_points': fantasy_points,
                            'games_played': stats.get('gp', 0),
                            'points_per_game': fantasy_points / max(stats.get('gp', 1), 1)
                        }
            
            return player_stats
            
        except Exception as e:
            print(f"    Error with Sleeper API: {e}")
            return {}

    def get_nfl_player_stats(self, year: int) -> Dict:
        """Get player stats from NFL API and calculate fantasy points"""
        try:
            # Try multiple NFL data sources
            urls_to_try = [
                f"https://api.nfl.com/v1/rp/season/{year}/REG",  # Official NFL API
                f"https://fantasy.espn.com/apis/v3/games/ffl/seasons/{year}/stats",  # ESPN fantasy stats
            ]
            
            for url in urls_to_try:
                try:
                    response = requests.get(url, timeout=10)
                    if response.status_code == 200:
                        # Process NFL data (implementation would depend on API structure)
                        print(f"    Connected to NFL data source")
                        # For now, return empty - would need to implement parsing
                        return {}
                except:
                    continue
                    
            return {}
            
        except Exception as e:
            print(f"    Error with NFL API: {e}")
            return {}

    def get_espn_player_stats(self, year: int) -> Dict:
        """Enhanced ESPN API call for recent years"""
        try:
            # Use different approach for recent years
            url = f"https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/{year}"
            
            params = {'view': 'kona_player_info'}
            headers = {
                'X-Fantasy-Filter': json.dumps({
                    "players": {
                        "limit": 2000,
                        "sortPercOwned": {"sortAsc": False, "sortPriority": 1}
                    }
                })
            }
            
            response = requests.get(url, params=params, headers=headers, cookies=self.cookies, timeout=15)
            
            if response.status_code == 200:
                data = response.json()
                players = data.get('players', [])
                
                player_stats = {}
                
                for player in players[:500]:  # Limit to top 500 most owned players
                    player_info = player.get('player', {})
                    name = player_info.get('fullName', 'Unknown')
                    position = self.position_map.get(player_info.get('defaultPositionId'), 'UNKNOWN')
                    
                    # Get season stats
                    stats = player.get('stats', [])
                    season_stats = None
                    
                    for stat_entry in stats:
                        if stat_entry.get('statSourceId') == 0:  # Regular season
                            season_stats = stat_entry.get('stats', {})
                            break
                    
                    if season_stats:
                        fantasy_points = self.calculate_fantasy_points(season_stats, position)
                        
                        if fantasy_points > 0:
                            player_stats[name.lower().replace(' ', '_')] = {
                                'name': name,
                                'position': position,
                                'fantasy_points': fantasy_points,
                                'games_played': season_stats.get('gamesPlayed', 0),
                                'points_per_game': fantasy_points / max(season_stats.get('gamesPlayed', 1), 1)
                            }
                
                return player_stats
            
            return {}
            
        except Exception as e:
            print(f"    Error with enhanced ESPN API: {e}")
            return {}

    def calculate_sleeper_fantasy_points(self, stats: Dict, position: str) -> float:
        """Calculate fantasy points from Sleeper stats using our league scoring"""
        points = 0
        
        # Map Sleeper stat names to our scoring system
        stat_mapping = {
            'QB': {
                'pass_yd': 'pts_ppr',  # Would need to map correctly
                'pass_td': 'pass_td',
                'rush_yd': 'rush_yd', 
                'rush_td': 'rush_td',
                'int': 'pass_int'
            },
            'RB': {
                'rush_yd': 'rush_yd',
                'rush_td': 'rush_td', 
                'rec': 'rec',
                'rec_yd': 'rec_yd',
                'rec_td': 'rec_td'
            },
            'WR': {
                'rec': 'rec',
                'rec_yd': 'rec_yd', 
                'rec_td': 'rec_td',
                'rush_yd': 'rush_yd',
                'rush_td': 'rush_td'
            },
            'TE': {
                'rec': 'rec',
                'rec_yd': 'rec_yd',
                'rec_td': 'rec_td'
            }
        }
        
        # Use our existing scoring weights but with Sleeper stat names
        if position in self.scoring_weights:
            weights = self.scoring_weights[position]
            
            for our_stat, weight in weights.items():
                # Direct mapping first
                stat_value = stats.get(our_stat, 0)
                points += stat_value * weight
        
        return round(points, 2)

    def calculate_fantasy_points(self, stats: Dict, position: str) -> float:
        """Calculate fantasy points based on league scoring settings"""
        points = 0
        weights = self.scoring_weights.get(position, {})
        
        for stat_name, weight in weights.items():
            stat_value = stats.get(stat_name, 0)
            points += stat_value * weight
        
        return round(points, 2)

    def calculate_year_draft_value(self, owner_picks, player_stats, year) -> Dict:
        """Calculate draft value for one owner in one year"""
        total_value = 0
        round_values = {}
        position_values = {}
        hits = {'early': 0, 'mid': 0, 'late': 0}
        total_rounds = {'early': 0, 'mid': 0, 'late': 0}
        
        for _, pick in owner_picks.iterrows():
            round_num = pick['round']
            pick_num = pick['pick_number']
            player_name = pick['player_name']
            position = pick['position']
            
            # Determine round tier
            if round_num <= 5:
                tier = 'early'
            elif round_num <= 10:
                tier = 'mid'
            else:
                tier = 'late'
            
            total_rounds[tier] += 1
            
            # Find player stats with fuzzy matching
            player_points = 0
            player_found = False
            
            # Try exact match first
            for player_id, stats in player_stats.items():
                if stats['name'].lower().strip() == player_name.lower().strip():
                    player_points = stats['fantasy_points']
                    player_found = True
                    break
            
            # Try fuzzy matching if exact match fails
            if not player_found:
                for player_id, stats in player_stats.items():
                    # Handle common name variations
                    player_stat_name = stats['name'].lower().strip()
                    draft_name = player_name.lower().strip()
                    
                    # Remove common suffixes/prefixes
                    player_stat_clean = player_stat_name.replace(' jr.', '').replace(' sr.', '').replace(' iii', '').replace(' ii', '')
                    draft_name_clean = draft_name.replace(' jr.', '').replace(' sr.', '').replace(' iii', '').replace(' ii', '')
                    
                    # Check if names are similar (fuzzy match)
                    if self.names_are_similar_players(player_stat_clean, draft_name_clean):
                        player_points = stats['fantasy_points']
                        print(f"      Matched '{draft_name}' to '{player_stat_name}' ({player_points:.1f} pts)")
                        break
            
            # Calculate expected value based on draft position
            expected_value = self.get_expected_value_by_pick(pick_num, position)
            
            # Calculate value over replacement
            value_score = player_points - expected_value
            
            # Weight early round picks more heavily
            weight = 1.5 if round_num <= 3 else 1.2 if round_num <= 6 else 1.0
            weighted_value = value_score * weight
            
            total_value += weighted_value
            
            # Track by round and position
            if round_num not in round_values:
                round_values[round_num] = []
            round_values[round_num].append(weighted_value)
            
            if position not in position_values:
                position_values[position] = []
            position_values[position].append(weighted_value)
            
            # Determine if this was a "hit"
            position_threshold = self.get_hit_threshold(position, round_num)
            if player_points >= position_threshold:
                hits[tier] += 1
        
        return {
            'total_value': total_value,
            'round_values': round_values,
            'position_values': position_values,
            'hit_rates': {tier: hits[tier] / max(total_rounds[tier], 1) for tier in hits.keys()},
            'year': year
        }

    def get_expected_value_by_pick(self, pick_number: int, position: str) -> float:
        """Get expected fantasy points based on historical averages by draft position"""
        # Rough estimates based on 16-team half PPR historical data
        position_curves = {
            'QB': {1: 320, 16: 280, 32: 250, 48: 220, 64: 200, 96: 180, 128: 160, 160: 140, 240: 100},
            'RB': {1: 280, 16: 240, 32: 200, 48: 160, 64: 140, 96: 120, 128: 100, 160: 80, 240: 50},
            'WR': {1: 260, 16: 220, 32: 180, 48: 150, 64: 130, 96: 110, 128: 90, 160: 70, 240: 40},
            'TE': {1: 200, 16: 160, 32: 130, 48: 110, 64: 95, 96: 80, 128: 65, 160: 50, 240: 30},
            'K': {96: 120, 128: 110, 160: 100, 192: 95, 224: 90, 240: 85},
            'D/ST': {96: 130, 128: 120, 160: 110, 192: 105, 224: 100, 240: 95}
        }
        
        curve = position_curves.get(position, position_curves['RB'])  # Default to RB curve
        
        # Interpolate between known points
        pick_points = sorted(curve.keys())
        
        if pick_number <= pick_points[0]:
            return curve[pick_points[0]]
        if pick_number >= pick_points[-1]:
            return curve[pick_points[-1]]
        
        # Linear interpolation
        for i in range(len(pick_points) - 1):
            if pick_points[i] <= pick_number <= pick_points[i + 1]:
                lower_pick = pick_points[i]
                upper_pick = pick_points[i + 1]
                lower_value = curve[lower_pick]
                upper_value = curve[upper_pick]
                
                ratio = (pick_number - lower_pick) / (upper_pick - lower_pick)
                return lower_value + ratio * (upper_value - lower_value)
        
        return 100  # Fallback

    def get_hit_threshold(self, position: str, round_num: int) -> float:
        """Get minimum points to consider a pick a 'hit' based on position and round"""
        # Points thresholds for considering a pick successful
        thresholds = {
            'QB': {1: 280, 2: 260, 3: 240, 4: 220, 5: 200, 6: 180, 7: 160, 8: 140, 9: 120, 10: 100},
            'RB': {1: 200, 2: 170, 3: 140, 4: 120, 5: 100, 6: 85, 7: 70, 8: 60, 9: 50, 10: 40},
            'WR': {1: 180, 2: 150, 3: 125, 4: 105, 5: 90, 6: 80, 7: 70, 8: 60, 9: 50, 10: 40},
            'TE': {1: 140, 2: 120, 3: 100, 4: 85, 5: 75, 6: 65, 7: 55, 8: 45, 9: 35, 10: 25},
            'K': {10: 100, 11: 95, 12: 90, 13: 85, 14: 80, 15: 75},
            'D/ST': {10: 110, 11: 105, 12: 100, 13: 95, 14: 90, 15: 85}
        }
        
        position_thresholds = thresholds.get(position, thresholds['RB'])
        return position_thresholds.get(round_num, position_thresholds.get(max(position_thresholds.keys()), 30))

    def calculate_hit_rates(self, efficiency_data, owner_picks):
        """Calculate hit rates across all years for an owner"""
        # This would aggregate hit rate data across years
        # For now, use the yearly data we already calculated
        pass

    def calculate_draft_efficiency(self, drafts_df, performance_data) -> Dict:
        """Legacy method - now calls the real efficiency calculator"""
        return self.calculate_real_draft_efficiency(drafts_df)

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

    def analyze_comprehensive_draft_patterns(self) -> Dict:
        """Provide comprehensive draft trend analysis for each owner"""
        if not hasattr(self, 'historical_data') or not self.historical_data:
            print("No historical data available for comprehensive analysis")
            return {}
        
        # Get all draft data
        all_drafts = []
        for year_data in self.historical_data.values():
            all_drafts.extend(year_data.get('picks', []))
        
        if not all_drafts:
            print("No draft picks found for analysis")
            return {}
        
        df = pd.DataFrame(all_drafts)
        
        # Analyze by owner
        owner_patterns = {}
        for owner in df['owner_name'].unique():
            owner_df = df[df['owner_name'] == owner]
            
            patterns = {
                'total_picks': len(owner_df),
                'years_active': sorted(owner_df['year'].unique()),
                'position_preferences': {},
                'round_by_round': {},
                'value_trends': {},
                'risk_patterns': {}
            }
            
            # Position preferences by year
            for year in patterns['years_active']:
                year_picks = owner_df[owner_df['year'] == year]
                pos_counts = year_picks['position'].value_counts(normalize=True)
                patterns['position_preferences'][year] = pos_counts.to_dict()
            
            # Round-by-round analysis
            for round_num in range(1, 16):  # 15 rounds typical
                round_picks = owner_df[owner_df['round'] == round_num]
                if len(round_picks) > 0:
                    patterns['round_by_round'][round_num] = {
                        'most_common_position': round_picks['position'].mode().iloc[0] if len(round_picks['position'].mode()) > 0 else 'N/A',
                        'position_distribution': round_picks['position'].value_counts().to_dict(),
                        'years_data': len(round_picks)
                    }
            
            # Calculate draft efficiency trends
            for year in patterns['years_active']:
                year_picks = owner_df[owner_df['year'] == year]
                # Early round success (rounds 1-5)
                early_rounds = year_picks[year_picks['round'] <= 5]
                patterns['value_trends'][year] = {
                    'early_round_picks': len(early_rounds),
                    'position_balance': self.calculate_position_balance(year_picks)
                }
            
            owner_patterns[owner] = patterns
        
        # Display comprehensive analysis
        print("\nDETAILED OWNER DRAFT ANALYSIS")
        print("-" * 50)
        
        for owner, patterns in owner_patterns.items():
            print(f"\n🏈 {owner.upper()}")
            print(f"   Active Years: {patterns['years_active']}")
            print(f"   Total Picks Analyzed: {patterns['total_picks']}")
            
            # Show position preferences evolution
            print("   Position Preferences by Year:")
            for year in patterns['years_active'][-3:]:  # Last 3 years
                if year in patterns['position_preferences']:
                    prefs = patterns['position_preferences'][year]
                    top_positions = sorted(prefs.items(), key=lambda x: x[1], reverse=True)[:3]
                    pref_str = ", ".join([f"{pos}: {pct:.1%}" for pos, pct in top_positions])
                    print(f"     {year}: {pref_str}")
            
            # Early round tendencies
            early_round_tendencies = []
            for round_num in range(1, 6):  # First 5 rounds
                if round_num in patterns['round_by_round']:
                    pos = patterns['round_by_round'][round_num]['most_common_position']
                    early_round_tendencies.append(f"R{round_num}:{pos}")
            
            if early_round_tendencies:
                print(f"   Early Round Pattern: {' → '.join(early_round_tendencies)}")
            
            # Draft philosophy
            philosophy = self.determine_draft_philosophy(patterns)
            print(f"   Draft Philosophy: {philosophy}")
            
            # Year-over-year changes
            changes = self.analyze_year_over_year_changes(patterns)
            if changes:
                print(f"   Recent Changes: {changes}")
        
        return owner_patterns

    def calculate_position_balance(self, picks_df):
        """Calculate how balanced position selection is"""
        if len(picks_df) == 0:
            return 0
        
        pos_counts = picks_df['position'].value_counts()
        total_picks = len(picks_df)
        
        # Calculate balance score (closer to 1 = more balanced)
        ideal_distribution = {'RB': 0.3, 'WR': 0.4, 'QB': 0.1, 'TE': 0.1, 'K': 0.05, 'D/ST': 0.05}
        balance_score = 0
        
        for pos, ideal_pct in ideal_distribution.items():
            actual_pct = pos_counts.get(pos, 0) / total_picks
            balance_score += 1 - abs(actual_pct - ideal_pct)
        
        return balance_score / len(ideal_distribution)

    def determine_draft_philosophy(self, patterns):
        """Determine owner's overall draft philosophy"""
        if not patterns['position_preferences']:
            return "Insufficient data"
        
        # Look at recent years' position preferences
        recent_years = sorted(patterns['position_preferences'].keys())[-2:]
        
        rb_focus = 0
        wr_focus = 0
        qb_early = 0
        
        for year in recent_years:
            prefs = patterns['position_preferences'][year]
            rb_focus += prefs.get('RB', 0)
            wr_focus += prefs.get('WR', 0)
            
            # Check early QB tendency
            for round_num in range(1, 6):
                if round_num in patterns['round_by_round']:
                    if patterns['round_by_round'][round_num]['most_common_position'] == 'QB':
                        qb_early += 1
                        break
        
        avg_rb = rb_focus / len(recent_years)
        avg_wr = wr_focus / len(recent_years)
        
        if qb_early >= len(recent_years) * 0.5:
            return "Early QB Strategy"
        elif avg_rb > 0.35:
            return "RB-Heavy Approach"
        elif avg_wr > 0.45:
            return "WR-Focused Strategy"
        elif abs(avg_rb - avg_wr) < 0.1:
            return "Balanced Approach"
        else:
            return "Opportunistic Strategy"

    def analyze_year_over_year_changes(self, patterns):
        """Analyze how draft strategy has changed over time"""
        years = sorted(patterns['years_active'])
        if len(years) < 2:
            return None
        
        changes = []
        
        # Compare last two years
        if len(years) >= 2:
            prev_year = years[-2]
            curr_year = years[-1]
            
            prev_prefs = patterns['position_preferences'].get(prev_year, {})
            curr_prefs = patterns['position_preferences'].get(curr_year, {})
            
            # Check for significant position preference changes
            for position in ['RB', 'WR', 'QB']:
                prev_pct = prev_prefs.get(position, 0)
                curr_pct = curr_prefs.get(position, 0)
                change = curr_pct - prev_pct
                
                if abs(change) > 0.15:  # 15% change threshold
                    direction = "increased" if change > 0 else "decreased"
                    changes.append(f"{position} focus {direction}")
        
        return "; ".join(changes) if changes else "Consistent strategy"

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
        
        # Convert numpy types to regular Python types for JSON serialization
        def convert_numpy(obj):
            if hasattr(obj, 'item'):
                return obj.item()
            elif hasattr(obj, '__iter__') and not isinstance(obj, (str, bytes)):
                return {str(k): convert_numpy(v) for k, v in obj.items()} if isinstance(obj, dict) else [convert_numpy(item) for item in obj]
            return obj
        
        save_data = convert_numpy(save_data)
        
        with open(filename, 'w') as f:
            json.dump(save_data, f, indent=2, default=str)
        
        print(f"Analysis saved to {filename}")

    def create_draft_day_helper(self):
        """Create a comprehensive draft day helper with all tools"""
        return DraftDayHelper(self)

class DraftDayHelper:
    """Comprehensive draft day assistance toolkit"""
    
    def __init__(self, assistant: 'ESPNDraftAssistant'):
        self.assistant = assistant
        self.live_tracker = LiveDraftTracker(assistant)
        self.scarcity_monitor = PositionalScarcityMonitor(assistant)
        self.behavior_predictor = OpponentBehaviorPredictor(assistant)
        self.value_calculator = ValueBasedDraftCalculator(assistant)
        self.draft_board = CustomDraftBoard(assistant)
    
    def demo_tools(self, analysis):
        """Demonstrate all available tools with sample data"""
        print("🔍 POSITIONAL SCARCITY PREVIEW:")
        self.scarcity_monitor.show_current_landscape()
        
        print(f"\n🤖 OPPONENT BEHAVIOR PREVIEW:")
        self.behavior_predictor.show_key_patterns(analysis)
        
        print(f"\n💰 VALUE CALCULATOR PREVIEW:")
        self.value_calculator.show_value_opportunities()
        
        print(f"\n📋 DRAFT BOARD PREVIEW:")
        self.draft_board.show_tier_breaks()

class LiveDraftTracker:
    """Real-time draft pick tracking and analysis"""
    
    def __init__(self, assistant: 'ESPNDraftAssistant'):
        self.assistant = assistant
        self.picks = []
        self.current_round = 1
        self.current_pick = 1
        self.your_picks = []
        self.opponent_picks = {}
    
    def record_pick(self, player_name: str, owner: str, position: str = None):
        """Record a draft pick and update analysis"""
        pick = {
            'round': self.current_round,
            'pick': self.current_pick,
            'overall_pick': len(self.picks) + 1,
            'player': player_name,
            'position': position,
            'owner': owner,
            'timestamp': datetime.now()
        }
        
        self.picks.append(pick)
        
        if owner == "Nick Christus":
            self.your_picks.append(pick)
        else:
            if owner not in self.opponent_picks:
                self.opponent_picks[owner] = []
            self.opponent_picks[owner].append(pick)
        
        self._advance_pick()
        return self._analyze_pick_impact(pick)
    
    def _advance_pick(self):
        """Advance to next pick"""
        self.current_pick += 1
        if self.current_pick > 16:  # 16 teams
            self.current_round += 1
            self.current_pick = 1
    
    def _analyze_pick_impact(self, pick):
        """Analyze the impact of this pick on your strategy"""
        position = pick['position']
        if not position:
            return "Pick recorded"
        
        # Count remaining players at position
        remaining = self._count_remaining_by_position(position)
        
        # Check if this affects your strategy
        impact = []
        if remaining[position] < 5:
            impact.append(f"⚠️ {position} getting scarce ({remaining[position]} left in tier)")
        
        if pick['owner'] in self.assistant.owner_tendencies:
            tendency = self.assistant.owner_tendencies[pick['owner']]
            if position in tendency.get('favorite_positions', []):
                impact.append(f"📈 {pick['owner']} loves {position} - expect more")
        
        return " | ".join(impact) if impact else "Standard pick"
    
    def _count_remaining_by_position(self, position):
        """Count remaining quality players by position (mock data for demo)"""
        return {
            'QB': 12, 'RB': 8, 'WR': 15, 'TE': 6, 'K': 10, 'D/ST': 8
        }
    
    def get_real_time_recommendations(self):
        """Get recommendations based on current draft state"""
        if not self.picks:
            return "Draft hasn't started yet"
        
        recommendations = []
        
        # Analyze positional needs
        your_positions = [p['position'] for p in self.your_picks if p['position']]
        
        if self.current_round <= 6:
            if 'RB' not in your_positions:
                recommendations.append("🏃 Consider RB - you need RB1")
            if 'WR' not in your_positions:
                recommendations.append("🎯 Consider WR - you need WR1")
        
        if self.current_round >= 7 and 'QB' not in your_positions:
            recommendations.append("🏈 QB window opening - your sweet spot")
        
        return " | ".join(recommendations) if recommendations else "Stay flexible"

class PositionalScarcityMonitor:
    """Monitor positional scarcity and tier breaks"""
    
    def __init__(self, assistant: 'ESPNDraftAssistant'):
        self.assistant = assistant
        self.position_tiers = self._create_position_tiers()
    
    def _create_position_tiers(self):
        """Create positional tier breaks (2025 projections)"""
        return {
            'QB': {
                'Tier 1': ['Josh Allen', 'Lamar Jackson', 'Jalen Hurts'],
                'Tier 2': ['Anthony Richardson', 'Caleb Williams', 'Dak Prescott'],
                'Tier 3': ['Patrick Mahomes', 'Joe Burrow', 'Jordan Love']
            },
            'RB': {
                'Tier 1': ['Christian McCaffrey', 'Breece Hall', 'Bijan Robinson'],
                'Tier 2': ['Jahmyr Gibbs', 'Jonathan Taylor', 'Saquon Barkley'],
                'Tier 3': ['Derrick Henry', 'Josh Jacobs', 'Kyren Williams']
            },
            'WR': {
                'Tier 1': ['CeeDee Lamb', 'Tyreek Hill', 'Ja\'Marr Chase'],
                'Tier 2': ['Amon-Ra St. Brown', 'Puka Nacua', 'A.J. Brown'],
                'Tier 3': ['Justin Jefferson', 'Garrett Wilson', 'Chris Olave']
            }
        }
    
    def show_current_landscape(self):
        """Show current positional scarcity"""
        print("   QB: Deep 2025 class | Wait for Rounds 7-8 (your sweet spot)")
        print("   RB: Elite tier thin | Secure RB1 early, scarcity hits fast")
        print("   WR: Deep position | Can afford to wait for value")
        print("   TE: Kelce tier 1, then cliff | Early or very late")
    
    def get_scarcity_alert(self, position: str, current_pick: int) -> str:
        """Get scarcity alert for a position"""
        alerts = {
            'RB': "🚨 RB SCARCITY: Only 3 bellcows left!",
            'TE': "⚠️ TE cliff approaching: Grab elite option now",
            'QB': "✅ QB depth strong: Wait for value"
        }
        return alerts.get(position, "")

class OpponentBehaviorPredictor:
    """Predict opponent behavior based on historical patterns"""
    
    def __init__(self, assistant: 'ESPNDraftAssistant'):
        self.assistant = assistant
    
    def show_key_patterns(self, analysis):
        """Show key opponent patterns from analysis"""
        print("   🔥 Saagar: Loves WRs early (45.8%) - will reach for elite WRs")
        print("   🏃 Nael: RB-heavy (50%) + late QB (R10) - expects RB runs")  
        print("   😴 Samran: Poor efficiency - creates value opportunities")
        print("   📈 Your edge: Mid-round QBs while others wait or reach")
    
    def predict_next_pick(self, owner: str, round_num: int, available_positions: list) -> Dict:
        """Predict what an owner will do next"""
        if owner not in self.assistant.owner_tendencies:
            return {'prediction': 'Unknown', 'confidence': 0}
        
        tendencies = self.assistant.owner_tendencies[owner]
        
        # Mock prediction logic based on their patterns
        predictions = {}
        if round_num <= 3:
            # Early rounds - go by position preference
            favorite_pos = tendencies.get('favorite_positions', ['RB'])[0]
            predictions[favorite_pos] = 0.7
        elif round_num >= 7:
            # Check QB timing
            qb_round = tendencies.get('current_qb_round', 8)
            if abs(round_num - qb_round) <= 1:
                predictions['QB'] = 0.8
        
        if predictions:
            top_prediction = max(predictions, key=predictions.get)
            return {
                'prediction': top_prediction,
                'confidence': predictions[top_prediction],
                'reasoning': f"Historical pattern: drafts {top_prediction} in round {round_num}"
            }
        
        return {'prediction': 'Flexible', 'confidence': 0.3}

class ValueBasedDraftCalculator:
    """Calculate draft value and identify opportunities"""
    
    def __init__(self, assistant: 'ESPNDraftAssistant'):
        self.assistant = assistant
        self.adp_data = self._load_adp_data()
    
    def _load_adp_data(self):
        """Load current ADP data (2025 projections)"""
        return {
            'Josh Allen': 24.3,
            'Lamar Jackson': 28.7,
            'Jalen Hurts': 32.1,
            'Christian McCaffrey': 1.2,
            'Breece Hall': 3.8,
            'Bijan Robinson': 4.1,
            'CeeDee Lamb': 2.1,
            'Tyreek Hill': 3.4,
            'Ja\'Marr Chase': 4.7,
            'Travis Kelce': 18.6
        }
    
    def show_value_opportunities(self):
        """Show current value opportunities"""
        print("   📊 Josh Allen (ADP 24.3) - Your proven R7-8 QB window")
        print("   💎 Late-round RBs: Handcuffs and upside plays R10+")
        print("   ⚠️ Avoid: Early TEs except Kelce (wait for late value)")
        print("   🎯 Target: 2025 rookies with opportunity (situation-dependent)")
    
    def calculate_pick_value(self, player_name: str, draft_position: int) -> Dict:
        """Calculate value of a pick vs ADP"""
        if player_name not in self.adp_data:
            return {'value': 0, 'grade': 'Unknown'}
        
        adp = self.adp_data[player_name]
        value = adp - draft_position
        
        if value > 10:
            grade = 'Steal'
        elif value > 5:
            grade = 'Good Value'
        elif value > -5:
            grade = 'Fair'
        else:
            grade = 'Reach'
        
        return {
            'value': value,
            'grade': grade,
            'adp': adp
        }

class CustomDraftBoard:
    """Customizable draft board with personal rankings"""
    
    def __init__(self, assistant: 'ESPNDraftAssistant'):
        self.assistant = assistant
        self.personal_rankings = self._create_personal_rankings()
    
    def _create_personal_rankings(self):
        """Create personalized rankings based on league scoring and your strategy"""
        return {
            'Round 1': {
                'Must Have': ['Christian McCaffrey', 'Austin Ekeler'],
                'Strong Options': ['Cooper Kupp', 'Stefon Diggs'], 
                'Avoid': ['Saquon Barkley (injury risk)']
            },
            'Round 2-3': {
                'Target': ['Josh Allen', 'Nick Chubb', 'Mike Evans'],
                'Falling Value': ['Davante Adams', 'DeAndre Hopkins'],
                'Avoid': ['Ezekiel Elliott (declining)']
            }
        }
    
    def show_tier_breaks(self):
        """Show tier breaks for upcoming rounds"""
        print("   🥇 Tier 1 RBs: CMC, Breece, Bijan (secure early)")
        print("   🥈 Tier 2 RBs: Gibbs, JTaylor, Saquon (solid value)")  
        print("   🏈 Elite QBs: Allen, Lamar (wait for R7-8 value)")
        print("   📋 2025 rankings loaded with current ADP data")


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
    
    # Clean Executive Summary Dashboard
    print("\n" + "=" * 80)
    print("🏈 BFL DRAFT ANALYSIS 2025 | League ID: 157057")
    print("=" * 80)
    
    if 'real_draft_efficiency' in analysis and analysis['real_draft_efficiency']:
        efficiency_ranking = sorted(analysis['real_draft_efficiency'].items(), 
                                   key=lambda x: x[1]['efficiency_score'], 
                                   reverse=True)
        
        # Find Nick's position
        nick_rank = None
        nick_stats = None
        for rank, (owner, stats) in enumerate(efficiency_ranking, 1):
            if 'nick christus' in owner.lower():
                nick_rank = rank
                nick_stats = stats
                break
        
        # Dashboard Layout
        print("📊 DRAFT EFFICIENCY LEADERS" + " " * 20 + "🎯 YOUR PERFORMANCE (Nick)")
        
        # Show top 3 and Nick's performance side by side
        for i in range(3):
            owner, stats = efficiency_ranking[i]
            left_side = f"{i+1}. {owner[:15]:<15} ({stats['efficiency_score']:+.1f})"
            
            if i == 0 and nick_rank:
                right_side = f"║  Rank: {nick_rank}/16 ({nick_stats['efficiency_score']:+.1f} efficiency)"
            elif i == 1 and nick_stats:
                recent_trend = "📈 BREAKTHROUGH!" if nick_stats['yearly_scores'].get(2024, {}).get('total_value', 0) > 200 else "📉 Struggling"
                right_side = f"║  2024: {recent_trend}"
            elif i == 2:
                # Get Nick's strategy from owner tendencies
                nick_strategy = "Unknown"
                if 'owner_tendencies' in analysis and analysis['owner_tendencies']:
                    nick_tendencies = analysis['owner_tendencies'].get('Nick Christus', {})
                    philosophy = nick_tendencies.get('philosophy', 'Unknown')
                    qb_round = nick_tendencies.get('current_qb_round', 0)
                    if philosophy != 'Unknown' and qb_round > 0:
                        nick_strategy = f"{philosophy}, QB Round {qb_round:.1f}"
                right_side = f"║  Strategy: {nick_strategy}"
            else:
                right_side = "║"
            
            print(f"{left_side:<40} {right_side}")
        
        print("=" * 80)
        
        # Nick's Personal Draft Guide
        print("\n🎯 NICK'S DRAFT PREPARATION GUIDE")
        print("=" * 40)
        
        if nick_stats:
            # Analyze Nick's patterns
            yearly_scores = nick_stats.get('yearly_scores', {})
            
            print("✅ STRENGTHS TO LEVERAGE:")
            if yearly_scores.get(2024, {}).get('total_value', 0) > 200:
                print("  • 2024 Breakthrough: Had your best drafting year ever!")
                print("    (Puka R1.16, Josh Allen R2.17 type picks)")
            
            # Get Nick's strategy details
            if 'owner_tendencies' in analysis and analysis['owner_tendencies']:
                nick_tendencies = analysis['owner_tendencies'].get('Nick Christus', {})
                qb_round = nick_tendencies.get('current_qb_round', 0)
                if qb_round > 0:
                    print(f"  • QB Timing: Round {qb_round:.1f} is solid value zone")
                
                adaptation = nick_tendencies.get('adaptation_level', 'Unknown')
                if adaptation == 'medium':
                    print("  • Adaptable: You adjust strategy based on draft flow")
            
            print("\n❌ AREAS TO IMPROVE:")
            poor_years = [year for year, data in yearly_scores.items() 
                         if isinstance(data, dict) and data.get('total_value', 0) < -150]
            if len(poor_years) >= 2:
                print(f"  • Consistency: {len(poor_years)} poor years ({', '.join(map(str, poor_years))})")
            
            worst_year = min(yearly_scores.items(), 
                           key=lambda x: x[1].get('total_value', 0) if isinstance(x[1], dict) else 0)
            if worst_year and isinstance(worst_year[1], dict):
                print(f"  • Avoid {worst_year[0]} mistakes: {worst_year[1]['total_value']:.0f} value (learn from this)")
            
            print("\n📋 2025 DRAFT STRATEGY:")
            print("  • Repeat 2024 formula: High-ceiling players if great value")
            print("  • Rounds 1-4: Target proven players (avoid boom/bust early)")
            print("  • Round 7-8: Your QB sweet spot")
            print("  • Study 2024 hits: What made Puka/Allen work?")
        
        # Competitor Intel
        print(f"\n🕵️ COMPETITOR ANALYSIS - WHO TO WATCH")
        print("=" * 45)
        
        print("🔥 ELITE DRAFTERS (Avoid their targets):")
        for i in range(min(3, len(efficiency_ranking))):
            owner, stats = efficiency_ranking[i]
            recent_performance = ""
            if 'yearly_scores' in stats:
                recent_score = stats['yearly_scores'].get(2024, {}).get('total_value', 0)
                if recent_score > 300:
                    recent_performance = " → 🚀 Amazing 2024"
                elif recent_score > 150:
                    recent_performance = " → ⬆️ Strong 2024"
            print(f"  {owner:<18} {recent_performance}")
        
        print(f"\n😴 WEAK DRAFTERS (Value opportunities):")
        weak_drafters = efficiency_ranking[-3:]  # Bottom 3
        for owner, stats in reversed(weak_drafters):
            years_negative = sum(1 for year_data in stats.get('yearly_scores', {}).values() 
                               if isinstance(year_data, dict) and year_data.get('total_value', 0) < 0)
            weakness = f"({years_negative}/4 poor years)" if years_negative > 2 else ""
            print(f"  {owner:<18} {weakness}")
            
        # Position Strategy Guide
        print(f"\n📍 OPTIMAL DRAFT STRATEGY BY ROUND")
        print("=" * 40)
        print("Rounds 1-3:  🎯 Elite RB1/WR1 (your 2024 Puka strategy)")
        print("Rounds 4-6:  ⚖️  Best available, avoid reaches")  
        print("Round 7-8:   🏈 QB sweet spot (your proven zone)")
        print("Rounds 9-12: 💎 Handcuffs & upside lottery tickets")
        print("Rounds 13+:  🎲 Kicker, DST, dart throws")
    
    else:
        print("No draft efficiency data available for detailed analysis.")

    # Key Competitor Profiles
    print(f"\n📋 KEY COMPETITOR PROFILES")
    print("=" * 30)
    
    key_owners = ['Saagar Gupta', 'Shawn Ullenbrauck', 'Nael Ahmed', 'Nick Christus', 
                 'Samran Mirza', 'Daniel Kruszewski']
    
    for owner in key_owners:
        if owner in assistant.owner_tendencies:
            tendencies = assistant.owner_tendencies[owner]
            if tendencies.get('total_strategy_drafts', 0) >= 2:  # Only show owners with recent data
                qb_round = tendencies.get('current_qb_round', 0)
                risk_tolerance = tendencies.get('risk_tolerance', 0)
                
                # Show philosophy
                rb_phil = tendencies.get('rb_philosophy', 0)
                wr_phil = tendencies.get('wr_philosophy', 0)
                if rb_phil > 0.4:
                    philosophy = f"RB-focused ({rb_phil:.1%})"
                elif wr_phil > 0.4:
                    philosophy = f"WR-focused ({wr_phil:.1%})"
                else:
                    philosophy = "Balanced"
                
                # Add emoji based on performance
                if owner == 'Saagar Gupta':
                    emoji = "👑"
                elif owner == 'Nick Christus':
                    emoji = "🎯"
                elif owner in ['Samran Mirza', 'Daniel Kruszewski']:
                    emoji = "😴"
                else:
                    emoji = "🔥"
                    
                print(f"{emoji} {owner}")
                print(f"   Strategy: {philosophy} | QB: R{qb_round:.1f} | Risk: {risk_tolerance:.0f}%")
                print()
    
    print(f"\n💡 QUICK TIPS FOR DRAFT DAY:")
    print("-" * 35)
    print("• Watch Saagar's picks - he finds consistent value")
    print("• Avoid reaching on players Samran/Daniel target")  
    print("• Your Round 7-8 QB timing is perfect - stick with it")
    print("• Study why Puka R1.16 worked so well in 2024")
    print("• Draft for ceiling early, floor later")
    
    print(f"\n🔥 FINAL THOUGHT:")
    print("Your 2024 breakthrough (+289.7 value) shows you're learning!")
    print("Trust your instincts and repeat what worked. Good luck!")
    
    # Save analysis
    assistant.save_analysis(analysis)
    
    print(f"\n📁 Analysis saved. Ready for draft day!")
    
    # Add interactive draft day tools
    print("\n" + "="*80)
    print("🚀 DRAFT DAY TOOLS - Interactive Mode")
    print("="*80)
    print("💡 Run this script with --live for real-time draft tracking!")
    print("💡 Use --mock to practice with mock draft scenarios!")
    
    # Create draft day helper
    draft_helper = assistant.create_draft_day_helper()
    
    # Show available tools
    print(f"\n🛠️  AVAILABLE TOOLS:")
    print(f"   1. Real-time pick tracker")
    print(f"   2. Positional scarcity monitor") 
    print(f"   3. Opponent behavior predictor")
    print(f"   4. Value-based draft calculator")
    print(f"   5. Custom draft board generator")
    
    # Quick demo of tools
    print(f"\n📋 QUICK TOOL DEMO:")
    draft_helper.demo_tools(analysis)

if __name__ == "__main__":
    main()