#!/usr/bin/env python3
"""
Fantasy Football Schedule Generator using Google OR-Tools
More robust constraint solver with comprehensive scheduling constraints
"""

from ortools.sat.python import cp_model
import csv

# Team and division setup
teams_by_div = {
    "North": ["Thor", "Blake", "Abe", "DTM"],
    "South": ["Samran", "Nasties", "Thomas", "Shooter"],
    "East":  ["Emelie", "AMO", "Lukose", "Sydney"],
    "West":  ["Nick", "Rej", "Saagar", "Dino"],
}

div_of = {t: d for d, ts in teams_by_div.items() for t in ts}
opposite = {"North": "South", "South": "North", "East": "West", "West": "East"}
teams = [t for ts in teams_by_div.values() for t in ts]
T = len(teams)
W = 14  # weeks
G = 8   # games per week

# Create team index mapping
idx = {t: i for i, t in enumerate(teams)}
idx_to_team = {i: t for t, i in idx.items()}

# Rivalry pairs
rivals = [
    ("DTM", "Sydney"),
    ("Thomas", "Emelie"),
    ("Thor", "Nick"),
    ("Blake", "Rej"),
    ("Samran", "Dino"),
    ("Abe", "Lukose"),
    ("Nasties", "Saagar"),
    ("Shooter", "AMO"),
]

print(f"Setting up schedule for {T} teams over {W} weeks...")
print(f"Teams: {teams}")
print(f"Divisions: {teams_by_div}")

# Create the model
model = cp_model.CpModel()

# Variables: matches[i][j][w] = 1 if team i plays at home against team j in week w
matches = {}
for i in range(T):
    matches[i] = {}
    for j in range(T):
        if i != j:  # No self-play
            matches[i][j] = {}
            for w in range(W):
                matches[i][j][w] = model.NewBoolVar(f'match_{i}_{j}_{w}')

print("Adding constraints...")

# Constraint 1: Each team plays exactly once per week
for w in range(W):
    for i in range(T):
        # Sum of home games + away games = 1
        home_games = [matches[i][j][w] for j in range(T) if j != i]
        away_games = [matches[j][i][w] for j in range(T) if j != i]
        model.Add(sum(home_games + away_games) == 1)

# Constraint 2: Each week has exactly G games
for w in range(W):
    total_games = []
    for i in range(T):
        for j in range(T):
            if i != j:
                total_games.append(matches[i][j][w])
    model.Add(sum(total_games) == G)

# Constraint 3: Division double round-robin (each division pair plays exactly twice)
for div_teams in teams_by_div.values():
    div_indices = [idx[team] for team in div_teams]
    for i in range(len(div_indices)):
        for j in range(i + 1, len(div_indices)):
            team_i, team_j = div_indices[i], div_indices[j]
            # Teams i and j play exactly twice (once home, once away)
            total_games = []
            for w in range(W):
                total_games.extend([matches[team_i][team_j][w], matches[team_j][team_i][w]])
            model.Add(sum(total_games) == 2)

# Constraint 4: Opposite division single round with balanced home/away
for div1, div2 in [("North", "South"), ("East", "West")]:
    div1_indices = [idx[team] for team in teams_by_div[div1]]
    div2_indices = [idx[team] for team in teams_by_div[div2]]
    
    for team_i in div1_indices:
        for team_j in div2_indices:
            # Teams i and j play exactly once
            total_games = []
            for w in range(W):
                total_games.extend([matches[team_i][team_j][w], matches[team_j][team_i][w]])
            model.Add(sum(total_games) == 1)

# Constraint 4b: Opposite division home/away balance (2 home, 2 away per team)
for div1, div2 in [("North", "South"), ("East", "West")]:
    div1_indices = [idx[team] for team in teams_by_div[div1]]
    div2_indices = [idx[team] for team in teams_by_div[div2]]
    
    # Each team in div1 hosts exactly 2 games against div2 teams
    for team_i in div1_indices:
        home_games_vs_div2 = []
        for team_j in div2_indices:
            for w in range(W):
                home_games_vs_div2.append(matches[team_i][team_j][w])
        model.Add(sum(home_games_vs_div2) == 2)
    
    # Each team in div2 hosts exactly 2 games against div1 teams  
    for team_j in div2_indices:
        home_games_vs_div1 = []
        for team_i in div1_indices:
            for w in range(W):
                home_games_vs_div1.append(matches[team_j][team_i][w])
        model.Add(sum(home_games_vs_div1) == 2)

# Constraint 5: Home/Away balance (7 home, 7 away per team)
for i in range(T):
    home_games = []
    away_games = []
    for w in range(W):
        for j in range(T):
            if i != j:
                home_games.append(matches[i][j][w])
                away_games.append(matches[j][i][w])
    
    model.Add(sum(home_games) == 7)
    model.Add(sum(away_games) == 7)

# Constraint 5b: Each divisional pair plays exactly once at each location
for div_teams in teams_by_div.values():
    div_indices = [idx[team] for team in div_teams]
    for i in range(len(div_indices)):
        for j in range(i + 1, len(div_indices)):
            team_i, team_j = div_indices[i], div_indices[j]
            
            # Team i hosts team j exactly once
            home_games_i_vs_j = []
            for w in range(W):
                home_games_i_vs_j.append(matches[team_i][team_j][w])
            model.Add(sum(home_games_i_vs_j) == 1)
            
            # Team j hosts team i exactly once  
            home_games_j_vs_i = []
            for w in range(W):
                home_games_j_vs_i.append(matches[team_j][team_i][w])
            model.Add(sum(home_games_j_vs_i) == 1)

# Constraint 6: Rivalry week (Week 10, index 9)
rivalry_week = 9
for team1, team2 in rivals:
    team1_idx, team2_idx = idx[team1], idx[team2]
    # This rivalry pair plays exactly once in week 9
    model.Add(matches[team1_idx][team2_idx][rivalry_week] + matches[team2_idx][team1_idx][rivalry_week] == 1)

# Constraint 7: Minimum separation between games (division teams only)
# Division teams that play twice should have at least 3 weeks between games
separation_weeks = 3
for div_teams in teams_by_div.values():
    div_indices = [idx[team] for team in div_teams]
    for i in range(len(div_indices)):
        for j in range(i + 1, len(div_indices)):
            team_i, team_j = div_indices[i], div_indices[j]
            
            # For any two weeks within separation_weeks of each other,
            # these teams can play at most once
            for w1 in range(W):
                for w2 in range(w1 + 1, min(w1 + separation_weeks, W)):
                    model.Add(
                        matches[team_i][team_j][w1] + matches[team_j][team_i][w1] +
                        matches[team_i][team_j][w2] + matches[team_j][team_i][w2] <= 1
                    )

# Constraint 8: At most 2 consecutive division games for any team
for i in range(T):
    team_div_indices = [idx[teammate] for teammate in teams_by_div[div_of[idx_to_team[i]]] if teammate != idx_to_team[i]]
    
    # For any 3 consecutive weeks, team i can play at most 2 division games
    for w1 in range(W - 2):  # w1, w1+1, w1+2
        division_games_in_3_weeks = []
        for w in [w1, w1 + 1, w1 + 2]:
            for div_opponent in team_div_indices:
                if div_opponent in matches[i] and w in matches[i][div_opponent]:
                    division_games_in_3_weeks.append(matches[i][div_opponent][w])
                if i in matches[div_opponent] and w in matches[div_opponent][i]:
                    division_games_in_3_weeks.append(matches[div_opponent][i][w])
        
        # At most 2 division games in any 3 consecutive weeks
        if division_games_in_3_weeks:
            model.Add(sum(division_games_in_3_weeks) <= 2)

# Constraint 9: At least 3 division games in the last 8 weeks (weeks 7-14)
last_8_weeks = list(range(6, 14))  # weeks 7-14 (0-indexed: 6-13)
for i in range(T):
    team_div_indices = [idx[teammate] for teammate in teams_by_div[div_of[idx_to_team[i]]] if teammate != idx_to_team[i]]
    
    division_games_last_8 = []
    for w in last_8_weeks:
        for div_opponent in team_div_indices:
            if div_opponent in matches[i] and w in matches[i][div_opponent]:
                division_games_last_8.append(matches[i][div_opponent][w])
            if i in matches[div_opponent] and w in matches[div_opponent][i]:
                division_games_last_8.append(matches[div_opponent][i][w])
    
    # At least 3 division games in the last 8 weeks
    if division_games_last_8:
        model.Add(sum(division_games_last_8) >= 3)

# Constraint 10: No team plays any non-division opponent more than once
# This includes rivals (who should only meet in rivalry week) and all cross-division teams
for i in range(T):
    team_name = idx_to_team[i]
    team_div = div_of[team_name]
    
    # Get all teams that are NOT in the same division
    non_division_teams = [j for j in range(T) if j != i and div_of[idx_to_team[j]] != team_div]
    
    for j in non_division_teams:
        # Teams i and j (non-division) can play at most once across all weeks
        total_games = []
        for w in range(W):
            if j in matches[i] and w in matches[i][j]:
                total_games.append(matches[i][j][w])
            if i in matches[j] and w in matches[j][i]:
                total_games.append(matches[j][i][w])
        
        if total_games:
            model.Add(sum(total_games) <= 1)

# Constraint 11: Force all games in Week 14 to be divisional
final_week = W - 1  # Week 14 (0-indexed: 13)
for i in range(T):
    team_name = idx_to_team[i]
    team_div = div_of[team_name]
    
    # Get all teams that are NOT in the same division
    non_division_teams = [j for j in range(T) if j != i and div_of[idx_to_team[j]] != team_div]
    
    # No non-divisional games in final week
    for j in non_division_teams:
        if j in matches[i] and final_week in matches[i][j]:
            model.Add(matches[i][j][final_week] == 0)
        if i in matches[j] and final_week in matches[j][i]:
            model.Add(matches[j][i][final_week] == 0)

# Constraint 12: No more than 3 consecutive home or away games
for i in range(T):
    # Check every 4-week window for consecutive home games
    for w1 in range(W - 3):  # weeks w1, w1+1, w1+2, w1+3
        home_games_in_4_weeks = []
        for w in [w1, w1 + 1, w1 + 2, w1 + 3]:
            for j in range(T):
                if i != j:
                    home_games_in_4_weeks.append(matches[i][j][w])
        
        # At most 3 home games in any 4 consecutive weeks
        model.Add(sum(home_games_in_4_weeks) <= 3)
    
    # Check every 4-week window for consecutive away games
    for w1 in range(W - 3):  # weeks w1, w1+1, w1+2, w1+3
        away_games_in_4_weeks = []
        for w in [w1, w1 + 1, w1 + 2, w1 + 3]:
            for j in range(T):
                if i != j:
                    away_games_in_4_weeks.append(matches[j][i][w])
        
        # At most 3 away games in any 4 consecutive weeks
        model.Add(sum(away_games_in_4_weeks) <= 3)

print("Solving with OR-Tools...")

# Create solver and solve
solver = cp_model.CpSolver()
solver.parameters.max_time_in_seconds = 300  # 5 minute timeout

status = solver.Solve(model)

if status == cp_model.OPTIMAL or status == cp_model.FEASIBLE:
    print("✅ Solution found!")
    
    # Extract the schedule
    games_by_week = []
    team_schedules = {team: [] for team in teams}
    
    for w in range(W):
        print(f"\nWeek {w+1}:")
        week_games = []
        
        for i in range(T):
            for j in range(T):
                if i != j and solver.Value(matches[i][j][w]) == 1:
                    home_team = idx_to_team[i]
                    away_team = idx_to_team[j]
                    
                    # Determine game type
                    home_div = div_of[home_team]
                    away_div = div_of[away_team]
                    
                    if home_div == away_div:
                        game_type = "Division"
                    elif away_div == opposite[home_div]:
                        game_type = "Opposite"
                    elif w == 9 and any((home_team, away_team) == rival or (away_team, home_team) == rival for rival in rivals):
                        game_type = "Rivalry"
                    else:
                        game_type = "Cross-Division"
                    
                    game_info = {
                        'Week': w+1,
                        'Away': away_team,
                        'Home': home_team,
                        'Type': game_type
                    }
                    
                    week_games.append(game_info)
                    games_by_week.append(game_info)
                    print(f"  {home_team} vs {away_team} ({game_type})")
                    
                    # Add to team schedules
                    team_schedules[home_team].append({
                        'Week': w+1,
                        'Opponent': away_team,
                        'Venue': 'Home',
                        'Type': game_type
                    })
                    team_schedules[away_team].append({
                        'Week': w+1,
                        'Opponent': home_team,
                        'Venue': 'Away',
                        'Type': game_type
                    })
    
    # Create CSV files
    print("\n📄 Creating CSV files...")
    
    # 1. Schedule by week (Away team first, then Home team)
    with open('schedule_by_week.csv', 'w', newline='') as csvfile:
        fieldnames = ['Week', 'Away', 'Home', 'Type']
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(games_by_week)
    print("✅ schedule_by_week.csv created")
    
    # 2. Schedule by team
    team_rows = []
    for team in teams:
        for game in team_schedules[team]:
            team_rows.append({
                'Team': team,
                'Division': div_of[team],
                'Week': game['Week'],
                'Opponent': game['Opponent'],
                'Opponent_Division': div_of[game['Opponent']],
                'Venue': game['Venue'],
                'Type': game['Type']
            })
    
    with open('schedule_by_team.csv', 'w', newline='') as csvfile:
        fieldnames = ['Team', 'Division', 'Week', 'Opponent', 'Opponent_Division', 'Venue', 'Type']
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(team_rows)
    print("✅ schedule_by_team.csv created")
    
    # 3. Team schedule grid
    team_grid = []
    for team in teams:
        row = {'Team': team, 'Division': div_of[team]}
        for week in range(1, 15):
            game = next((g for g in team_schedules[team] if g['Week'] == week), None)
            if game:
                opponent = game['Opponent']
                venue = 'vs' if game['Venue'] == 'Home' else '@'
                row[f'Week_{week}'] = f"{venue} {opponent}"
            else:
                row[f'Week_{week}'] = "BYE"
        team_grid.append(row)
    
    with open('team_schedule_grid.csv', 'w', newline='') as csvfile:
        fieldnames = ['Team', 'Division'] + [f'Week_{i}' for i in range(1, 15)]
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(team_grid)
    print("✅ team_schedule_grid.csv created")
    
    # Validation
    print("\n" + "="*60)
    print("📊 VALIDATION SUMMARY")
    print("="*60)
    
    # Check game counts per team
    print("\n🏠 Total Home/Away Balance:")
    for team in teams:
        home_games = len([g for g in team_schedules[team] if g['Venue'] == 'Home'])
        away_games = len([g for g in team_schedules[team] if g['Venue'] == 'Away'])
        total_games = home_games + away_games
        status_check = "✅" if home_games == 7 and away_games == 7 else "❌"
        print(f"  {team:8}: {home_games}H / {away_games}A / {total_games}T {status_check}")
    
    # Check divisional home/away balance
    print("\n🏠 Divisional Home/Away Balance:")
    for team in teams:
        div_home_games = len([g for g in team_schedules[team] if g['Venue'] == 'Home' and g['Type'] == 'Division'])
        div_away_games = len([g for g in team_schedules[team] if g['Venue'] == 'Away' and g['Type'] == 'Division'])
        div_total_games = div_home_games + div_away_games
        status_check = "✅" if div_home_games == 3 and div_away_games == 3 else "❌"
        print(f"  {team:8}: {div_home_games}H / {div_away_games}A / {div_total_games}T {status_check}")
    
    # Check rivalry week
    print(f"\n🏆 Rivalry Week 10:")
    week_10_games = [g for g in games_by_week if g['Week'] == 10]
    rivalry_found = []
    for game in week_10_games:
        home, away = game['Home'], game['Away']
        for rival_pair in rivals:
            if (home, away) == rival_pair or (away, home) == rival_pair:
                rivalry_found.append(rival_pair)
                print(f"  ✅ {home} vs {away}")
                break
    
    if len(rivalry_found) == len(rivals):
        print(f"  🎉 All {len(rivals)} rivalry games scheduled correctly!")
    else:
        print(f"  ❌ Only {len(rivalry_found)}/{len(rivals)} rivalry games found")
    
    # Check that each divisional pair plays once at each location
    print(f"\n🏠 Divisional Matchup Balance (each opponent once home, once away):")
    for div, div_teams in teams_by_div.items():
        print(f"\n{div} Division:")
        all_correct = True
        for i in range(4):
            for j in range(i+1, 4):
                team1, team2 = div_teams[i], div_teams[j]
                
                # Find games between these teams
                team1_home = None
                team2_home = None
                
                for game in team_schedules[team1]:
                    if game['Opponent'] == team2:
                        if game['Venue'] == 'Home':
                            team1_home = game['Week']
                        else:
                            team2_home = game['Week']
                
                # Verify each team hosts once
                if team1_home and team2_home:
                    status_check = "✅"
                    print(f"  {team1:8} vs {team2:8}: {team1} hosts Week {team1_home}, {team2} hosts Week {team2_home} {status_check}")
                else:
                    status_check = "❌"
                    all_correct = False
                    print(f"  {team1:8} vs {team2:8}: Missing home/away balance {status_check}")
        
        if all_correct:
            print(f"  🎉 Perfect divisional home/away balance in {div}!")

    # Check division game separation  
    print(f"\n📅 Division Game Separation (min 3 weeks):")
    for div, div_teams in teams_by_div.items():
        print(f"\n{div} Division:")
        for i in range(4):
            for j in range(i+1, 4):
                team1, team2 = div_teams[i], div_teams[j]
                game_weeks = []
                for game in team_schedules[team1]:
                    if game['Opponent'] == team2:
                        game_weeks.append(game['Week'])
                game_weeks.sort()
                if len(game_weeks) == 2:
                    separation = abs(game_weeks[1] - game_weeks[0])
                    status_check = "✅" if separation >= 3 else "❌"
                    print(f"  {team1:8} vs {team2:8}: Weeks {game_weeks[0]:2}, {game_weeks[1]:2} (gap: {separation}) {status_check}")

    # Check consecutive division games constraint (max 2 in a row)
    print(f"\n📅 Consecutive Division Games (max 2 in any 3 weeks):")
    for team in teams:
        max_consecutive = 0
        violations = []
        
        # Get division games for this team
        div_games = [g for g in team_schedules[team] if g['Type'] == 'Division']
        div_weeks = sorted([g['Week'] for g in div_games])
        
        # Check every 3-week window
        for w1 in range(1, 13):  # weeks 1-12 (checking windows 1-3, 2-4, ..., 12-14)
            window_weeks = [w1, w1+1, w1+2]
            div_games_in_window = len([w for w in div_weeks if w in window_weeks])
            max_consecutive = max(max_consecutive, div_games_in_window)
            
            if div_games_in_window > 2:
                violations.append(f"weeks {w1}-{w1+2}")
        
        status_check = "✅" if max_consecutive <= 2 else "❌"
        violation_text = f" (violations: {', '.join(violations)})" if violations else ""
        print(f"  {team:8}: max {max_consecutive} in any 3-week window {status_check}{violation_text}")

    # Check last 8 weeks division games constraint (min 3)
    print(f"\n📅 Division Games in Last 8 Weeks (min 3):")
    for team in teams:
        last_8_div_games = len([g for g in team_schedules[team] 
                               if g['Type'] == 'Division' and g['Week'] >= 7])
        status_check = "✅" if last_8_div_games >= 3 else "❌"
        print(f"  {team:8}: {last_8_div_games} division games in weeks 7-14 {status_check}")

    # Check that all Week 14 games are divisional
    print(f"\n🏆 Final Week Drama (all Week 14 games must be divisional):")
    week_14_games = [g for g in games_by_week if g['Week'] == 14]
    all_divisional = True
    divisional_count = 0
    
    for game in week_14_games:
        home, away = game['Home'], game['Away']
        game_type = game['Type']
        if game_type == 'Division':
            status_check = "✅"
            divisional_count += 1
        else:
            status_check = "❌"
            all_divisional = False
        print(f"  {away} @ {home}: {game_type} {status_check}")
    
    if all_divisional:
        print(f"  🎉 Perfect! All {divisional_count}/8 Week 14 games are divisional!")
    else:
        print(f"  ❌ Only {divisional_count}/8 Week 14 games are divisional")

    # Check consecutive home/away games constraint (max 3 in a row)
    print(f"\n🏠 Consecutive Home/Away Balance (max 3 in a row):")
    all_valid = True
    for team in teams:
        violations = []
        
        # Get home/away pattern for this team
        team_games = sorted([g for g in team_schedules[team]], key=lambda x: x['Week'])
        venues = [g['Venue'] for g in team_games]
        
        # Check for streaks of 4 or more
        current_streak = 1
        current_venue = venues[0] if venues else None
        max_home_streak = 0
        max_away_streak = 0
        
        for i in range(1, len(venues)):
            if venues[i] == current_venue:
                current_streak += 1
            else:
                # End of streak, record it
                if current_venue == 'Home':
                    max_home_streak = max(max_home_streak, current_streak)
                else:
                    max_away_streak = max(max_away_streak, current_streak)
                
                if current_streak > 3:
                    violations.append(f"{current_streak} {current_venue.lower()} in a row")
                
                # Start new streak
                current_venue = venues[i]
                current_streak = 1
        
        # Check final streak
        if current_venue == 'Home':
            max_home_streak = max(max_home_streak, current_streak)
        else:
            max_away_streak = max(max_away_streak, current_streak)
        
        if current_streak > 3:
            violations.append(f"{current_streak} {current_venue.lower()} in a row")
        
        status_check = "✅" if not violations else "❌"
        violation_text = f" (violations: {', '.join(violations)})" if violations else ""
        print(f"  {team:8}: max {max_home_streak}H/{max_away_streak}A streaks {status_check}{violation_text}")
        
        if violations:
            all_valid = False
    
    if all_valid:
        print("  🎉 All teams have proper home/away distribution!")

    # Check non-division teams play at most once
    print(f"\n📅 Non-Division Matchups (max 1 per opponent):")
    all_valid = True
    for team in teams:
        violations = []
        team_games = team_schedules[team]
        
        # Count games against each opponent
        opponent_counts = {}
        for game in team_games:
            opponent = game['Opponent']
            opponent_counts[opponent] = opponent_counts.get(opponent, 0) + 1
        
        # Check non-division opponents
        team_div = div_of[team]
        for opponent, count in opponent_counts.items():
            opponent_div = div_of[opponent]
            
            # If not same division, should play at most once
            if opponent_div != team_div:
                if count > 1:
                    violations.append(f"{opponent}({count}x)")
                    all_valid = False
        
        status_check = "✅" if not violations else "❌"
        violation_text = f" (violations: {', '.join(violations)})" if violations else ""
        print(f"  {team:8}: Non-division opponents played once {status_check}{violation_text}")
    
    if all_valid:
        print("  🎉 All non-division teams play each other at most once!")

    # Check opponent variety
    print(f"\n🎯 Opponent Breakdown by Team:")
    for team in teams:
        div_games = len([g for g in team_schedules[team] if g['Type'] == 'Division'])
        opp_games = len([g for g in team_schedules[team] if g['Type'] == 'Opposite'])
        cross_games = len([g for g in team_schedules[team] if g['Type'] in ['Cross-Division', 'Rivalry']])
        
        status_check = "✅" if div_games == 6 and opp_games == 4 and cross_games == 4 else "❌"
        print(f"  {team:8}: {div_games}D + {opp_games}O + {cross_games}C = {div_games+opp_games+cross_games}T {status_check}")
    
    print(f"\n📁 Files created:")
    print(f"  • schedule_by_week.csv")
    print(f"  • schedule_by_team.csv") 
    print(f"  • team_schedule_grid.csv")
    print(f"\n🎯 Schedule ready for ESPN import!")

elif status == cp_model.INFEASIBLE:
    print("❌ No solution exists with the current constraints.")
    print("The problem is infeasible - try relaxing some constraints.")
elif status == cp_model.MODEL_INVALID:
    print("❌ The model is invalid.")
else:
    print(f"❌ Solver returned status: {status}")
    print("Try running again or relaxing constraints.")

print(f"\nSolver statistics:")
print(f"  Status: {solver.StatusName(status)}")
print(f"  Runtime: {solver.WallTime():.2f}s")
print(f"  Conflicts: {solver.NumConflicts()}")
print(f"  Branches: {solver.NumBranches()}")