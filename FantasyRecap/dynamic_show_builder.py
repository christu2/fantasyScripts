#!/usr/bin/env python3
"""
BFL Dynamic Weekly Show Builder
===============================
Dynamically constructs all 16-18 visual slide cards and spoken dialogue scenes
from ANY ESPN season and week boxscore data. Zero hardcoding required.
"""

import random
from FantasyRecap.league_recap_generator import analyze_week, generate_weekly_awards

def build_dynamic_scenes_from_espn(raw_data, season: int, week_num: int, next_week_raw: dict = None) -> list:
    matchups, perfs, teams = analyze_week(raw_data, week_num)
    
    # Sort matchups by margin to identify superlatives
    sorted_by_margin = sorted([m for m in matchups if m['margin'] > 0], key=lambda x: x['margin'])
    sorted_by_loser_score = sorted(matchups, key=lambda x: min(x['away_score'], x['home_score']), reverse=True)
    
    gotw = sorted_by_margin[0] if sorted_by_margin else matchups[0] # Closest thriller
    demolition = sorted_by_margin[-1] if len(sorted_by_margin) > 1 else matchups[0] # Biggest blowout
    
    # Bad Beat: Highest scoring loser
    bad_beat = None
    for m in sorted_by_loser_score:
        if m != gotw and m != demolition:
            bad_beat = m
            break
    if not bad_beat:
        bad_beat = sorted_by_loser_score[0] if sorted_by_loser_score else gotw

    def get_tinfo(t_entry):
        if isinstance(t_entry, dict):
            return t_entry['name'], t_entry['owner'], f"{t_entry.get('wins', 0)}-{t_entry.get('losses', 0)}"
        return str(t_entry), str(t_entry), "0-0"

    scenes = []

    # =========================================================================
    # SCENE 1: COLD OPEN & RACE FOR THE JABRONI
    # =========================================================================
    scenes.append({
        'card': {
            'title': "TUESDAY MORNING HANGOVER",
            'subtitle': f"Week {week_num} Review • The Race for The Jabroni ({season})",
            'badge': "THE JABRONI RACE",
            'accent': (41, 128, 185),
            'items': [
                {'tag': "BROADCAST", 'header': f"BEASTS FOOTBALL LEAGUE • {season} SEASON", 'desc': f"Full 16-Franchise Week {week_num} Breakdown with Chris & Dave"},
                {'tag': "HEADLINES", 'header': "Thriller Finishes, Demolitions & Chaos", 'desc': f"Game of the Week decided by {gotw['margin']} pts; Demolition by {demolition['margin']} pts"},
                {'tag': "TROPHY", 'header': "The Race for The Jabroni Trophy", 'desc': "Championship lore, playoff stakes, and media press room reactions"}
            ]
        },
        'dialogue': [
            ('CHRIS', f"Good Tuesday morning everybody! Grab your coffee, pop an Advil, and welcome inside the BFL Tuesday Morning Hangover, presented by the BFL Broadcast Network. Week {week_num} of the {season} season is officially in the books, the midnight post-game media texts are in, and fantasy football has already ruined half the league's week. I'm Chris alongside Dave."),
            ('DAVE', f"Good morning Chris. I've been scrolling through the league trash-talk channel since early this morning, and the race for The Jabroni Trophy is completely unhinged! Commissioner Nick Christus has this league at peak intensity. We had wire-to-wire thrillers decided by two points, monster thirty-plus point beatdowns, and coaching blunders that belong in the hall of shame!")
        ]
    })

    # =========================================================================
    # SCENE 2: GAME OF THE WEEK THRILLER
    # =========================================================================
    w_team, w_owner, w_rec = get_tinfo(gotw['winner'])
    l_team, l_owner, l_rec = get_tinfo(gotw['loser'])
    w_score = max(gotw['away_score'], gotw['home_score'])
    l_score = min(gotw['away_score'], gotw['home_score'])

    scenes.append({
        'card': {
            'title': "GAME OF THE WEEK THRILLER",
            'subtitle': f"{w_team} ({w_owner}) {w_score:.2f}  def.  {l_team} ({l_owner}) {l_score:.2f}",
            'badge': "GAME OF THE WEEK",
            'accent': (230, 126, 34),
            'items': [
                {'tag': "WINNER", 'header': f"{w_team} ({w_owner}) — {w_score:.2f} PTS", 'desc': f"Clutches out the +{gotw['margin']:.2f} point victory to advance."},
                {'tag': "HEARTBREAK", 'header': f"{l_team} ({l_owner}) — {l_score:.2f} PTS", 'desc': "Came within a single possession of stealing the matchup."},
                {'tag': "MARGIN", 'header': f"+{gotw['margin']:.2f} Point Decision", 'desc': "Decided in the final quarter of Sunday night action."}
            ]
        },
        'dialogue': [
            ('CHRIS', f"Let's dive straight into our official Game of the Week. {w_owner} and {w_team} barely escaped with their lives against {l_owner}, {w_score:.2f} to {l_score:.2f}!"),
            ('DAVE', f"What an absolute cardiac finish, Chris! {gotw['margin']:.2f} points separated these two heavyweights. {w_owner} came up clutch in the fourth quarter, while {l_owner} is going to be having nightmares about every dropped pass on Sunday.")
        ]
    })

    # =========================================================================
    # SCENE 3: DEMOLITION OF THE WEEK
    # =========================================================================
    dw_team, dw_owner, dw_rec = get_tinfo(demolition['winner'])
    dl_team, dl_owner, dl_rec = get_tinfo(demolition['loser'])
    dw_score = max(demolition['away_score'], demolition['home_score'])
    dl_score = min(demolition['away_score'], demolition['home_score'])

    scenes.append({
        'card': {
            'title': "DEMOLITION OF THE WEEK",
            'subtitle': f"{dw_team} ({dw_owner}) {dw_score:.2f}  def.  {dl_team} ({dl_owner}) {dl_score:.2f}",
            'badge': "STATEMENT WIN",
            'accent': (46, 204, 113),
            'items': [
                {'tag': "DOMINANT", 'header': f"{dw_team} ({dw_owner}) — {dw_score:.2f} PTS", 'desc': f"Steamrolled the matchup with a massive +{demolition['margin']:.2f} point victory."},
                {'tag': "BLOWOUT", 'header': f"{dl_team} ({dl_owner}) — {dl_score:.2f} PTS", 'desc': "Offensive collapse stalls out under 60 points."},
                {'tag': "STATEMENT", 'header': "Hunting for The Jabroni", 'desc': "One of the most dominant margins of the entire season."}
            ]
        },
        'dialogue': [
            ('CHRIS', f"Now let's head over to the biggest blowout on the board. {dw_owner} and {dw_team} walked into {dl_owner}'s house and delivered a soul-crushing {demolition['margin']:.2f}-point beatdown!"),
            ('DAVE', f"{dw_owner} was firing on all cylinders, Chris! Putting up {dw_score:.2f} points while holding {dl_owner} to just {dl_score:.2f} is as disrespectful as it gets in the BFL. {dl_owner} needs to call an emergency team meeting after getting run off the field like that!")
        ]
    })

    # =========================================================================
    # SCENE 4: BAD BEAT OF THE WEEK
    # =========================================================================
    bw_team, bw_owner, bw_rec = get_tinfo(bad_beat['winner'])
    bl_team, bl_owner, bl_rec = get_tinfo(bad_beat['loser'])
    bw_score = max(bad_beat['away_score'], bad_beat['home_score'])
    bl_score = min(bad_beat['away_score'], bad_beat['home_score'])

    scenes.append({
        'card': {
            'title': "BAD BEAT OF THE WEEK",
            'subtitle': f"{bw_team} ({bw_owner}) {bw_score:.2f}  def.  {bl_team} ({bl_owner}) {bl_score:.2f}",
            'badge': "TOUGH LUCK",
            'accent': (231, 76, 60),
            'items': [
                {'tag': "WINNER", 'header': f"{bw_team} ({bw_owner}) — {bw_score:.2f} PTS", 'desc': "High-octane performance secures the hard-fought win."},
                {'tag': "HEARTBREAK", 'header': f"{bl_team} ({bl_owner}) — {bl_score:.2f} PTS", 'desc': f"Top-tier scoring output takes a brutal loss by +{bad_beat['margin']:.2f} pts."},
                {'tag': "BAD BEAT", 'header': "High-Score Casualty", 'desc': f"{bl_owner} would have beaten almost every other team this week."}
            ]
        },
        'dialogue': [
            ('CHRIS', f"Let's pour one out for {bl_owner}, who is our official Bad Beat of the Week winner. {bl_owner} put up an impressive {bl_score:.2f} points and still took an L to {bw_owner}, who dropped {bw_score:.2f}!"),
            ('DAVE', f"That is pure fantasy heartbreak, Chris! Scoring over one hundred points and taking a loss because your opponent went nuclear is the cruelest feeling in sports. The fantasy gods tested {bl_owner}'s sanity this week.")
        ]
    })

    # =========================================================================
    # SCENES 5+: REMAINING SLATE OF MATCHUPS
    # =========================================================================
    remaining_games = [m for m in matchups if m != gotw and m != demolition and m != bad_beat]
    for idx, m in enumerate(remaining_games):
        mw_team, mw_owner, mw_rec = get_tinfo(m['winner'])
        ml_team, ml_owner, ml_rec = get_tinfo(m['loser'])
        mw_score = max(m['away_score'], m['home_score'])
        ml_score = min(m['away_score'], m['home_score'])

        scenes.append({
            'card': {
                'title': f"BFL DIVISION SHOWDOWN #{idx+1}",
                'subtitle': f"{mw_team} ({mw_owner}) {mw_score:.2f}  def.  {ml_team} ({ml_owner}) {ml_score:.2f}",
                'badge': "MATCHUP REVIEW",
                'accent': (52, 152, 219) if idx % 2 == 0 else (155, 89, 182),
                'items': [
                    {'tag': "WINNER", 'header': f"{mw_team} ({mw_owner}) — {mw_score:.2f} PTS", 'desc': f"Takes care of business with a +{m['margin']:.2f} point margin."},
                    {'tag': "RUNNER-UP", 'header': f"{ml_team} ({ml_owner}) — {ml_score:.2f} PTS", 'desc': "Solid effort but couldn't keep pace in the second half."},
                    {'tag': "IMPACT", 'header': "The Jabroni Standings Shift", 'desc': "Major movement in the division hierarchy."}
                ]
            },
            'dialogue': [
                ('CHRIS', f"In other action across the league, {mw_owner} and {mw_team} took care of business, handling {ml_owner} and {ml_team} {mw_score:.2f} to {ml_score:.2f}!"),
                ('DAVE', f"{mw_owner} got the job done when it mattered most, winning by {m['margin']:.2f} points. Every single victory in this league is massive in the hunt for The Jabroni Trophy!")
            ]
        })

    # =========================================================================
    # SCENE: MIDNIGHT MEDIA PRESS ROOM
    # =========================================================================
    scenes.append({
        'card': {
            'title': "MIDNIGHT MEDIA PRESS ROOM",
            'subtitle': "Post-Game Manager Soundbites",
            'badge': "PRESS CONFERENCE",
            'accent': (243, 156, 18),
            'items': [
                {'tag': "WINNER", 'header': f"{w_owner} ({w_team}):", 'desc': f"\"A {gotw['margin']:.2f}-point thriller win shows our championship grit. We want The Jabroni!\""},
                {'tag': "RUNNER-UP", 'header': f"{l_owner} ({l_team}):", 'desc': "\"Losing by a single possession stings. We will bounce back.\""},
                {'tag': "DEMOLITION", 'header': f"{dw_owner} ({dw_team}):", 'desc': f"\"Dropping {dw_score:.2f} points is a statement to the entire league.\""},
                {'tag': "COMMISH", 'header': "Commissioner Nick in Media Room:", 'desc': "\"Peak competition across the BFL as the trophy race heats up.\""}
            ]
        },
        'dialogue': [
            ('CHRIS', f"Now let's head down to the media room for our post-game press conference soundbites. {w_owner} was asked about surviving the thriller and said: 'Winning a tight {gotw['margin']:.2f}-point battle shows the heart of this team. We have our eyes locked on The Jabroni!'"),
            ('DAVE', f"And {dw_owner} was brimming with confidence after that {demolition['margin']:.2f}-point blowout, saying: 'We put the entire league on notice this week. Good luck to whoever has to play us next!'"),
            ('CHRIS', "Commissioner Nick praised the league's competitive balance, noting that eighteen years of rivalry history make every single matchup personal.")
        ]
    })

    # =========================================================================
    # SCENE: SIGN-OFF & LOOKAHEAD
    # =========================================================================
    scenes.append({
        'card': {
            'title': "THURSDAY NIGHT FOOTBALL LOOKAHEAD",
            'subtitle': "Waiver Bids & Lineup Spreads Coming Thursday",
            'badge': "SIGN-OFF",
            'accent': (41, 128, 185),
            'items': [
                {'tag': "SCHEDULE", 'header': "Next Matchup Slate Ahead", 'desc': "Vegas spreads and positional battle previews drop Thursday morning."},
                {'tag': "WAIVERS", 'header': "Check Your Bids Today", 'desc': "Optimize your bench and prepare for the next round of action."},
                {'tag': "NETWORK", 'header': "BFL Broadcast Network", 'desc': "Presented by Commissioner Nick Christus"}
            ]
        },
        'dialogue': [
            ('CHRIS', "Thursday Night Football kicks off in just seventy-two hours, and Commissioner Nick's Vegas and Lineup Desk will drop the official simulated spreads and positional previews in the commissioner-desk channel on Thursday morning."),
            ('DAVE', "Get your waiver bids in today, bench the bums who gave you single digits, and stay locked in! For Chris, Dave, and the entire BFL Tuesday Morning Hangover crew, have a great Tuesday everybody!")
        ]
    })

    return scenes
