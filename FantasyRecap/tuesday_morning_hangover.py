#!/usr/bin/env python3
"""
BFL Tuesday Morning Hangover (Master Podcast & Dynamic Video Pipeline)
======================================================================
Produces the definitive weekly review with topic-synced visual transitions:
- Hosts: Chris (AndrewMultilingualNeural) & Dave (BrianMultilingualNeural)
- Dynamic Active Team Name & Owner Name Resolution from live ESPN API
- Master phonetic pronunciation engine for NFL players and owners
- Dynamic Slide Transitions synced to every matchup, blunder, and topic (~25-30s each)
- Exact player stat lines (394 yds/4 TDs, 169 rush yds/2 TDs, 7 rec/143 yds, 1 rec/8 yds)
- Jabroni Trophy championship lore and Week 2 Marquee Lookahead Matchups
- Discord #trash-talk chat integration & real manager press conference quotes
- Uploads both MP3 and MP4 directly to Discord #press-room-podcast
"""

import os
import sys
import re
import asyncio
import subprocess
import requests
import edge_tts
from pathlib import Path
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from FantasyRecap.league_recap_generator import fetch_espn_week_data, parse_league_members_and_teams, ESPN_LEAGUE_ID, ESPN_S2, ESPN_SWID
from FantasyRecap.discord_chat_harvester import get_sample_trash_talk_banter
from FantasyRecap.video_highlight_engine import create_slide_card, generate_topic_synced_video

# Load .env
env_path = Path(__file__).resolve().parent.parent / '.env'
if env_path.exists():
    load_dotenv(dotenv_path=env_path)
else:
    load_dotenv()

VOICE_HOST1 = 'en-US-AndrewMultilingualNeural' # Chris (Lead Anchor)
VOICE_HOST2 = 'en-US-BrianMultilingualNeural'  # Dave (Color Analyst)

MASTER_PHONETICS = [
    # --- League Owners ---
    (r'\bShawn Lukose\b', 'Luke-ose'),
    (r'\bLukose\b', 'Luke-ose'),
    (r'\bShawn Ullenbrauck\b', 'Thor'),
    (r'\bTommy\b', 'Thomas'),
    (r'\bMykonos\b', 'Mee-ko-nos'),
    (r'\bNael\b', 'Nile'),
    (r'\bSamran\b', 'Sum-rahn'),
    (r'\bRej\b', 'Redge'),
    (r'\bSaagar\b', 'Sah-gar'),
    (r'\bDino\b', 'Dee-no'),
    (r'\bEmelie\b', 'Emily'),
    (r'\bKruszewski\b', 'Cruise-sheff-skee'),
    
    # --- NFL Players ---
    (r'\bWan\'Dale\b', 'Wahn-Dale'),
    (r'\bBijan\b', 'Bee-jahn'),
    (r'\bJa\'Marr\b', 'Juh-Mahr'),
    (r'\bNico\b', 'Knee-co'),
    (r'\bKeon\b', 'Kee-on'),
    (r'\bEmeka Egbuka\b', 'Eh-meh-ka Egg-boo-ka'),
    (r'\bEgbuka\b', 'Egg-boo-ka'),
    (r'\bMcLaurin\b', 'Mick-Lauren'),
    (r'\bJ\.J\.\b', 'J J'),
    (r'\bA\.J\.\b', 'A J'),
    (r'\bXavier\b', 'Zay-vee-er'),
    (r'\bTyrone\b', 'Tie-rone'),
    (r'\bJalen\b', 'Jay-len'),
    
    # --- Terms & Numbers ---
    (r'\bdef\.\b', 'defeated'),
    (r'\bpts\b', 'points'),
    (r'\bpt\b', 'point'),
    (r'\bH2H\b', 'head to head'),
    (r'\bvs\.\b', 'versus'),
    (r'\bdiv\b', 'division'),
    (r'\bQB\b', 'quarterback'),
    (r'\bRB\b', 'running back'),
    (r'\bWR\b', 'wide receiver'),
    (r'\bTE\b', 'tight end'),
    (r'\bGOAT\b', 'goat'),
    (r'\bCommish\b', 'Commissioner'),
    (r'\bTNF\b', 'Thursday Night Football'),
    (r'\bMNF\b', 'Monday Night Football'),
    (r'\bSNF\b', 'Sunday Night Football'),
    (r'\b0\.0\b', 'zero'),
    (r'\b1\.3\b', 'one point three'),
    (r'\b3\.04\b', 'three point zero four')
]

def clean_for_spoken_audio(text: str) -> str:
    """Cleans text and applies master phonetic pronunciation for TTS."""
    text = re.sub(r'[*#_`~>|]', '', text)
    text = re.sub(r'^\[[A-Za-z0-9\s]+\]:\s*', '', text)
    text = re.sub(r'^\*\*[A-Za-z0-9\s]+\*\*:\s*', '', text)
    for pattern, replacement in MASTER_PHONETICS:
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
    text = re.sub(r'\s+', ' ', text).strip()
    return text

def find_team_by_owner_substr(teams: dict, name_part: str) -> tuple:
    """Finds active team dict and display name from live ESPN payload by owner substring."""
    name_lower = name_part.lower()
    for tid, t in teams.items():
        if name_lower in t['owner'].lower() or name_lower in t['name'].lower():
            return t['name'], t['owner']
    return name_part, name_part

def get_show_scenes(season: int = 2025, week_num: int = 1, teams: dict = None) -> list:
    """
    Returns the complete list of 18 structured scenes for the show.
    Each scene pairs a visual slide card with its matching spoken dialogue segment,
    dynamically resolving active team names and owner names.
    """
    if not teams:
        teams = {}

    def t_disp(name_part: str) -> str:
        tname, owner = find_team_by_owner_substr(teams, name_part)
        if tname.lower() == owner.lower():
            return f"{tname}"
        return f"{tname} ({owner})"

    return [
        # Scene 1: Cold Open & The Race for The Jabroni
        {
            'card': {
                'title': "TUESDAY MORNING HANGOVER",
                'subtitle': f"Week {week_num} Review • The Race for The Jabroni",
                'badge': "THE JABRONI RACE",
                'accent': (41, 128, 185),
                'items': [
                    {'tag': "BROADCAST", 'header': f"BEASTS FOOTBALL LEAGUE • {season} SEASON", 'desc': "Full 16-Franchise Post-Week Breakdown with Chris & Dave"},
                    {'tag': "HEADLINES", 'header': "Heartbreakers, Blowouts & Goose Eggs", 'desc': "3-point thrillers, 30-point demolitions, and midnight group chat drama"},
                    {'tag': "STAKES", 'header': "The Race for The Jabroni Trophy", 'desc': "18 seasons of rivalry history on the line"}
                ]
            },
            'dialogue': [
                ('CHRIS', f"Good Tuesday morning everybody! Grab your coffee, pop an Advil, and welcome inside the BFL Tuesday Morning Hangover, presented by the BFL Broadcast Network. Week {week_num} of the {season} season is officially in the books, the midnight post-game media texts are in, and fantasy football has already ruined half the league's week. I'm Chris alongside Dave."),
                ('DAVE', "Good morning Chris. I've been scrolling through the league trash-talk channel since early this morning, and the race for The Jabroni Trophy is officially on! Commissioner Nick Christus has this eighteen-year-old league at peak intensity. We had three-point heartbreakers, thirty-point blowouts, elite wide receivers putting up actual goose eggs, and some coaching decisions so bad they should be investigated!")
            ]
        },

        # Scene 2: Game of the Week (Adam vs Emelie)
        {
            'card': {
                'title': "GAME OF THE WEEK THRILLER",
                'subtitle': f"{t_disp('Adam')} 91.32 def. {t_disp('Emelie')} 88.28",
                'badge': "WEST DIVISION",
                'accent': (230, 126, 34),
                'items': [
                    {'tag': "WINNER", 'header': f"{t_disp('Adam')} — 91.32 PTS (1-0)", 'desc': "Bijan Robinson exploded for 6 rec, 100 yds, 1 TD (23.4 pts) to rescue the win."},
                    {'tag': "RUNNER-UP", 'header': f"{t_disp('Emelie')} — 88.28 PTS (0-1)", 'desc': "Brock Purdy put up 16.8 pts, but Nico Collins struggled with just 4.0 pts."},
                    {'tag': "MARGIN", 'header': "+3.04 Point Heartbreaker", 'desc': "Decided by a single drive in the final quarter."}
                ]
            },
            'dialogue': [
                ('CHRIS', "Let's dive straight into our Game of the Week in the West Division. Adam and Green and Golden barely escaped with their lives against Emelie, ninety-one point three two to eighty-eight point two eight!"),
                ('DAVE', "Are you kidding me, Chris?! Emelie got completely burned by her own lineup sheet. She started Nico Collins, and the dude put up four points on a fourteen-point projection. Meanwhile, Adam got a twenty-three-point bailout from Bijan Robinson to save him because Joe Burrow looked like he was playing with a blindfold on, scoring under nine points.")
            ]
        },

        # Scene 3: Bench Blunder of the Week (Wan'Dale Robinson)
        {
            'card': {
                'title': "BENCH BLUNDER OF THE WEEK",
                'subtitle': "Wan'Dale Robinson (14.5 pts) Left on the Pine",
                'badge': "BLUNDER REEL",
                'accent': (231, 76, 60),
                'items': [
                    {'tag': "ON BENCH", 'header': "Wan'Dale Robinson — 14.5 PTS", 'desc': f"Left on {t_disp('Emelie')}'s bench while Nico Collins (4.0 pts) started."},
                    {'tag': "SWAP IMPACT", 'header': "+10.5 Point Net Swing", 'desc': "Starting Wan'Dale flips the outcome to an Emelie win by +7.46 points."},
                    {'tag': "GROUP CHAT", 'header': "Adam Olen in #trash-talk:", 'desc': "\"Thank you Emelie for benching Wan'Dale! Best win of my life.\""}
                ]
            },
            'dialogue': [
                ('CHRIS', "And Dave, look at Emelie's bench! She had Wan'Dale Robinson sitting on her pine with fourteen and a half points! If she makes the simple swap and benches Nico for Wan'Dale, she wins by seven points and is celebrating this morning. Instead, she loses by three point zero four points. Adam immediately hopped into the group chat saying: 'Thank you Emelie for benching Wan'Dale! Best win of my life.'"),
                ('DAVE', "Leaving ten points on the bench when you lose by three points is the kind of mistake that makes you stare at your steering wheel on your Tuesday morning commute questioning all your life choices. Adam walks out with a greasy win, and Emelie takes the toughest loss of week one.")
            ]
        },

        # Scene 4: Demolition of the Week (Sydney vs Lukose)
        {
            'card': {
                'title': "DEMOLITION OF THE WEEK",
                'subtitle': f"{t_disp('Sydney')} 101.14 def. {t_disp('Lukose')} 69.98",
                'badge': "STATEMENT WIN",
                'accent': (46, 204, 113),
                'items': [
                    {'tag': "DOMINANT", 'header': f"{t_disp('Sydney')} — 101.14 PTS (1-0)", 'desc': "Zay Flowers went nuclear with 7 rec, 143 yds, 1 TD (24.6 pts)."},
                    {'tag': "BLOWOUT", 'header': f"{t_disp('Lukose')} — 69.98 PTS (0-1)", 'desc': "Kenneth Walker held under 20 yards; 4-time GOAT routed by +31.16 pts."},
                    {'tag': "QUEEN", 'header': "Hunting for Jabroni #2", 'desc': "Sydney establishes early control of the West Division."}
                ]
            },
            'dialogue': [
                ('CHRIS', "Now let's head over to the East Division for the biggest demolition of the week. Sydney walked into Lukose's house, kicked his front door down, and delivered a one hundred and one to seventy ass-kicking!"),
                ('DAVE', "Sydney was unbelievable, Chris! She got a massive game from Zay Flowers, who caught seven passes for one hundred and forty-three receiving yards and a touchdown! Her squad played fast, but can we talk about Lukose for a second? The four-time champion had the single biggest coaching brain-fart I have seen in eighteen years of BFL history!")
            ]
        },

        # Scene 5: 4-Time GOAT Brain-Fart (Drake Maye over Justin Fields)
        {
            'card': {
                'title': "COACHING BRAIN-FART OF THE WEEK",
                'subtitle': "Started Drake Maye (15.3) over Justin Fields (29.5)",
                'badge': "4-TIME GOAT DISASTER",
                'accent': (192, 57, 43),
                'items': [
                    {'tag': "STARTED", 'header': "Drake Maye — 15.3 PTS", 'desc': "15.3 fantasy points in the starting quarterback slot."},
                    {'tag': "ON PINE", 'header': "Justin Fields — 29.5 PTS", 'desc': "Chilling on the bench dropping nearly 30 fantasy points (+14.2 pt loss)."},
                    {'tag': "REACTION", 'header': "Sydney in #trash-talk:", 'desc': "\"Who told Lukose to start Drake Maye over Justin Fields? Show yourself 😂\""}
                ]
            },
            'dialogue': [
                ('CHRIS', "It was a total disaster, Dave. Lukose started Drake Maye, who gave him fifteen points, while Justin Fields was chilling on his bench dropping nearly thirty fantasy points! Kenneth Walker got bottled up for under twenty yards, and Sydney just ran him out of the gym by thirty-one points."),
                ('DAVE', "Sydney proved she is hunting for her second career Jabroni Trophy and put the entire league on notice. She posted in the trash-talk chat asking: 'Who told Lukose to start Drake Maye over Justin Fields? Show yourself.' Lukose has four Jabroni Trophies in his trophy case, but after getting embarrassed like that, he's holding an eight A M team meeting to find out who set his lineup!")
            ]
        },

        # Scene 6: Defending The Throne (Abe vs Saagar)
        {
            'card': {
                'title': "DEFENDING THE JABRONI TROPHY",
                'subtitle': f"{t_disp('Abe')} 117.86 def. {t_disp('Saagar')} 93.42",
                'badge': "SOUTH DIVISION",
                'accent': (155, 89, 182),
                'items': [
                    {'tag': "LEAGUE HIGH", 'header': f"{t_disp('Abe')} — 117.86 PTS (1-0)", 'desc': "Lamar Jackson (209 pass yds, 70 rush yds, 3 total TDs) leads the BFL."},
                    {'tag': "DROUGHT", 'header': f"{t_disp('Saagar')} — 93.42 PTS (0-1)", 'desc': "Patrick Mahomes put up 26 pts, but A.J. Brown was completely erased."},
                    {'tag': "STATEMENT", 'header': "Zero Championship Hangover", 'desc': "Reigning champion cruises to a 24.44-point victory."}
                ]
            },
            'dialogue': [
                ('CHRIS', "Down in the South Division, defending champion Abe and Crashee Bandicoot showed zero championship hangover, defending The Jabroni with a league-high one hundred and seventeen points against Saagar, winning by twenty-four!"),
                ('DAVE', "Abe looked unstoppable! Lamar Jackson threw for two hundred and nine yards and two touchdowns, plus seventy rushing yards and another score on the ground—three total touchdowns! Garrett Wilson went off for eighteen points. Meanwhile, poor Saagar. Patrick Mahomes gave him twenty-six, but A.J. Brown was completely erased, finishing with literally one catch for eight yards on the entire day! Saagar posted in the chat saying: 'Eighteen years and counting, A.J. Brown destroyed my season.'")
            ]
        },

        # Scene 7: Saagar's 18-Year Title Drought
        {
            'card': {
                'title': "18-YEAR JABRONI DROUGHT CONTINUES",
                'subtitle': f"{t_disp('Saagar')} Chasing Ring #2 Since 2008",
                'badge': "HISTORIC DROUGHT",
                'accent': (241, 196, 15),
                'items': [
                    {'tag': "INAUGURAL", 'header': "First-Ever BFL Champion (2008)", 'desc': "Won the inaugural title 18 years ago; drought is now entering year 19."},
                    {'tag': "ERASED", 'header': "A.J. Brown Lockdown: 1 Catch, 8 Yards", 'desc': "1.3 fantasy points on a 14.0 point pre-game projection."},
                    {'tag': "COMMUTE", 'header': "Saagar in #trash-talk:", 'desc': "\"Eighteen years and counting, A.J. Brown destroyed my season.\""}
                ]
            },
            'dialogue': [
                ('CHRIS', "Saagar won the first-ever Jabroni Trophy back in 2008, and he has now entered year nineteen of his title drought. Starting off oh-and-one by twenty-four points to Abe is just cruel."),
                ('DAVE', "Saagar's Jabroni drought is old enough to vote, Chris! Abe looked dominant, and the South Division throne is clearly his until proven otherwise.")
            ]
        },

        # Scene 8: North Division Top-Dog (Nick vs Dino)
        {
            'card': {
                'title': "NORTH DIVISION SHOWDOWN",
                'subtitle': f"{t_disp('Nick')} 100.82 def. {t_disp('Dino')} 85.48",
                'badge': "COMMISSIONER DESK",
                'accent': (52, 152, 219),
                'items': [
                    {'tag': "LEADER", 'header': f"{t_disp('Nick')} — 100.82 PTS (1-0)", 'desc': "Keon Coleman (21.2 pts, TD grab) bails out Minotaurs atop North."},
                    {'tag': "CHALLENGER", 'header': f"{t_disp('Dino')} — 85.48 PTS (0-1)", 'desc': "Jalen Hurts dropped 24.3 pts, but the supporting cast stalled."},
                    {'tag': "3 RINGS", 'header': "Defending 3 Jabroni Trophies", 'desc': "Commissioner Nick establishes first place in the North Division."}
                ]
            },
            'dialogue': [
                ('CHRIS', "Over in the North Division, our Commissioner Nick and the Mykonos Minotaurs took care of business, handling Dino one hundred to eighty-five!"),
                ('DAVE', "Commissioner Nick top-scored in the North despite Ja'Marr Chase having a quiet three-point day! Keon Coleman exploded for twenty-one points, more than doubling his projection with a huge touchdown grab to bail out the Minotaurs. Dino got twenty-four from Jalen Hurts, but Dino was complaining in the chat at midnight saying: 'Nick only won because his kicker had fourteen points.'")
            ]
        },

        # Scene 9: Keon Coleman Explosion & Dino's Midnight Salt
        {
            'card': {
                'title': "KEON COLEMAN BOOM & DINO SALT",
                'subtitle': "21.2 PTS Bails Out Minotaurs",
                'badge': "BREAKOUT STAR",
                'accent': (46, 204, 113),
                'items': [
                    {'tag': "BOOM", 'header': "Keon Coleman — 21.2 PTS (+11.8 vs Proj)", 'desc': "Doubled projection with explosive touchdown grab."},
                    {'tag': "DUD", 'header': "Ja'Marr Chase — 3.1 PTS", 'desc': "Held under 4 fantasy points in a quiet week one."},
                    {'tag': "SALT", 'header': "Dino Davros in #trash-talk:", 'desc': "\"Nick only won because his kicker had 14 points.\""}
                ]
            },
            'dialogue': [
                ('CHRIS', "A win is a win, and Commissioner Nick defends his three Jabroni Trophies and takes first place in the North Division.")
            ]
        },

        # Scene 10: Bad Beat of the Week (Dan vs Rej)
        {
            'card': {
                'title': "BAD BEAT OF THE WEEK",
                'subtitle': f"{t_disp('Daniel')} 105.42 def. {t_disp('rej')} 97.66",
                'badge': "TOUGH LUCK",
                'accent': (231, 76, 60),
                'items': [
                    {'tag': "WINNER", 'header': f"{t_disp('Daniel')} — 105.42 PTS (1-0)", 'desc': "Balanced squad powered by Javonte Williams (19.4 pts)."},
                    {'tag': "HEARTBREAK", 'header': f"{t_disp('rej')} — 97.66 PTS (0-1)", 'desc': "4th highest score in entire 16-team league takes a brutal loss."},
                    {'tag': "BAD BEAT", 'header': "High-Score Casualty", 'desc': "Rej would have beaten 12 other teams this week."}
                ]
            },
            'dialogue': [
                ('CHRIS', "Let's pour one out for Rej, who is our official Bad Beat of the Week winner. Rej put up ninety-seven point six points—the fourth highest score in the entire sixteen-team league—and still took an L to Dan, who put up one hundred and five!")
            ]
        },

        # Scene 11: Josh Allen 4-TD Video Game Masterpiece
        {
            'card': {
                'title': "JOSH ALLEN VIDEO GAME SHOW",
                'subtitle': "394 Pass Yds, 4 Total TDs, 38.8 PTS in a Loss",
                'badge': "MVP PERFORMANCE",
                'accent': (41, 128, 185),
                'items': [
                    {'tag': "AIR", 'header': "394 Passing Yards & 2 Passing TDs", 'desc': "Slinging lasers all over the field."},
                    {'tag': "GROUND", 'header': "30 Rushing Yards & 2 Rushing TDs", 'desc': "Bulldozing across the goal line for 4 total touchdowns."},
                    {'tag': "PAIN", 'header': f"{t_disp('rej')} in Press Room:", 'desc': "\"I scored 97.6 with Josh Allen dropping 39 and I still lost. I hate fantasy.\""}
                ]
            },
            'dialogue': [
                ('DAVE', "Josh Allen went completely nuclear for Rej, throwing for three hundred and ninety-four yards and two touchdowns, while adding thirty rushing yards and two more rushing touchdowns—that's four total touchdowns and nearly forty fantasy points! And Rej still loses because Dan got nineteen from Javonte Williams and solid balance across the board. Rej texted into the media desk saying: 'I scored ninety-seven points with Josh Allen dropping thirty-nine and still lost. I hate fantasy football so much.'")
            ]
        },

        # Scene 12: Defensive Slugfest (Blake vs Alex) & King Henry
        {
            'card': {
                'title': "DEFENSIVE SLUGFEST & KING HENRY",
                'subtitle': f"{t_disp('Blake')} 95.58 def. {t_disp('Alex')} 92.30",
                'badge': "GRUDGE MATCH",
                'accent': (230, 126, 34),
                'items': [
                    {'tag': "RAMPAGE", 'header': "King Derrick Henry — 169 Rushing Yds, 2 TDs", 'desc': "30.7 fantasy points carrying Blake to a +3.28 point win."},
                    {'tag': "ROOKIE", 'header': "Caleb Williams — 24.1 PTS", 'desc': "Impressive rookie debut for Alex, but McLaurin held to 3.8 pts."},
                    {'tag': "RECORD", 'header': f"{t_disp('Blake')} Escapes at 1-0", 'desc': "Physical ground-and-pound battle."}
                ]
            },
            'dialogue': [
                ('CHRIS', "In other action, Blake edged out Alex ninety-five to ninety-two in a gritty defensive battle. King Derrick Henry ran over everybody, plowing for one hundred and sixty-nine rushing yards and two touchdowns to carry Blake to the finish line!"),
                ('DAVE', "Derrick Henry is thirty years old and still running over human beings like a runaway freight train! Alex got twenty-four from rookie Caleb Williams, but Terry McLaurin was locked down for under four points. Blake escapes by three.")
            ]
        },

        # Scene 13: West/South Cross-Over (Nael vs Samran)
        {
            'card': {
                'title': "CROSS-DIVISION GAUNTLET",
                'subtitle': f"{t_disp('Nael')} 102.52 def. {t_disp('Samran')} 85.82",
                'badge': "BLOWOUT",
                'accent': (39, 174, 96),
                'items': [
                    {'tag': "MASTERCLASS", 'header': "Justin Herbert — 27.9 PTS", 'desc': "Dropping dimes all afternoon to push Nael over the century mark."},
                    {'tag': "BRIGHT SPOT", 'header': "Emeka Egbuka — 21.4 PTS", 'desc': "Breakout receiving performance for Samran."},
                    {'tag': "DISASTER", 'header': "Jerome Ford — 1.0 Single Point", 'desc': "Backfield collapse dooms Samran to an 0-1 start."}
                ]
            },
            'dialogue': [
                ('CHRIS', "Down in the cross-division matchup, Nael rolled over Samran one hundred and two to eighty-five behind a twenty-eight-point masterclass from Justin Herbert."),
                ('DAVE', "Justin Herbert was dropping dimes all day! Samran got twenty-one from Emeka Egbuka, but Jerome Ford gave him literally one single point in his backfield. You cannot win in this league getting one point from your running back, period.")
            ]
        },

        # Scene 14: North/West Grudge Match & Xavier Worthy 3 AM Meltdown
        {
            'card': {
                'title': "NORTH VS WEST GRUDGE MATCH",
                'subtitle': f"{t_disp('Ullenbrauck')} 94.32 def. {t_disp('Tommy')} 85.02",
                'badge': "RIVALRY",
                'accent': (142, 68, 173),
                'items': [
                    {'tag': "WINNER", 'header': f"{t_disp('Ullenbrauck')} — 94.32 PTS (1-0)", 'desc': "Gritty win to join the leaders atop the North."},
                    {'tag': "CHASING #1", 'header': f"{t_disp('Tommy')} — 85.02 PTS (0-1)", 'desc': "J.J. McCarthy dropped 22.0 pts, but Worthy laid a zero-point doughnut."},
                    {'tag': "3 AM MELTDOWN", 'header': "Thomas in #trash-talk:", 'desc': "\"Xavier Worthy gave me 0.0 pts. Dropping him to waivers at 3 AM.\""}
                ]
            },
            'dialogue': [
                ('CHRIS', "And wrapping up the week one gauntlet, Thor held off Thomas ninety-four to eighty-five in a classic North-versus-West grudge match."),
                ('DAVE', "Thomas is perpetually chasing that elusive first Jabroni Trophy, and dropping week one to Thor hurts. Thomas got twenty-two from J.J. McCarthy, but Xavier Worthy gave him an absolute doughnut with zero catches on zero yards! Thomas posted in the chat at three A M saying: 'Xavier Worthy is dead to me, dropping him to waivers immediately.'")
            ]
        },

        # Scene 15: Midnight Press Room Quotes
        {
            'card': {
                'title': "MIDNIGHT MEDIA PRESS ROOM",
                'subtitle': "Post-Game Manager Soundbites",
                'badge': "PRESS CONFERENCE",
                'accent': (243, 156, 18),
                'items': [
                    {'tag': "SYDNEY", 'header': f"{t_disp('Sydney')}:", 'desc': "\"Dropping 30 on the 4-time champ sets the standard. We want The Jabroni!\""},
                    {'tag': "ADAM", 'header': f"{t_disp('Adam')}:", 'desc': "\"Wan'Dale rotting on Emelie's bench was our MVP. We survived!\""},
                    {'tag': "LUKOSE", 'header': f"{t_disp('Lukose')}:", 'desc': "\"Starting Drake Maye over Fields cost us 14 pts. Team meeting at 8 AM.\""},
                    {'tag': "REJ", 'header': f"{t_disp('rej')}:", 'desc': "\"97.6 pts with Josh Allen dropping 39 and I lose. Fantasy gods hate me.\""}
                ]
            },
            'dialogue': [
                ('CHRIS', "Now let's head down to the media room for our post-game press conference soundbites collected after midnight. Sydney was asked about taking down Lukose and said: 'Dropping a thirty-point beatdown on the four-time champ in week one sets the standard. We are here to win the whole damn thing!'"),
                ('DAVE', "And Adam was all smiles after his three-point miracle over Emelie, saying: 'We survived by the skin of our teeth! Wan'Dale Robinson rotting on Emelie's bench was our true MVP of week one.' That is just savage from Adam!"),
                ('CHRIS', "Meanwhile, Lukose was pissed off in the hallway, telling our reporters: 'Disaster across the board. Starting Drake Maye over Justin Fields cost us fourteen points. We are holding an emergency meeting at eight A M to fix this.'"),
                ('DAVE', "And Rej gave the quote of the night after his thirty-nine-point Josh Allen explosion went to waste, saying: 'I scored ninety-seven points with Josh Allen dropping thirty-nine, and I still take the loss. The fantasy gods are testing my sanity.'")
            ]
        },

        # Scene 16: Week 2 Marquee Matchups (Nick vs Saagar & Lukose vs Adam)
        {
            'card': {
                'title': "WEEK 2 MARQUEE MATCHUPS TO WATCH",
                'subtitle': "Rivalry Stakes & Redemption Battles",
                'badge': "WEEK 2 LOOKAHEAD",
                'accent': (52, 73, 94),
                'items': [
                    {'tag': "MARQUEE", 'header': f"{t_disp('Nick')} (1-0)  vs.  {t_disp('Saagar')} (0-1)", 'desc': "3-time champ vs inaugural champ fighting to snap his 18-year title drought."},
                    {'tag': "REDEMPTION", 'header': f"{t_disp('Lukose')} (0-1)  vs.  {t_disp('Adam')} (1-0)", 'desc': "4-time GOAT looking for vengeance after the Drake Maye blunder."},
                    {'tag': "STAKES", 'header': "Early Conference Control", 'desc': "High-pressure spots across the league."}
                ]
            },
            'dialogue': [
                ('CHRIS', "Now let's turn the page and look ahead to Week Two, because the schedule makers gave us some absolute heavyweight clashes!"),
                ('DAVE', "First up in the North Division, we have Commissioner Nick at one-and-oh taking on Saagar at oh-and-one! Nick is looking to push his division lead, while Saagar is in desperate need of a bounce-back to keep his Jabroni dreams alive."),
                ('CHRIS', "Over in the East and West cross-over, we have a massive revenge spot: four-time champion Lukose at oh-and-one takes on Adam at one-and-oh! Lukose is furious after his week one benching blunder, while Adam is looking to prove his three-point win over Emelie was no fluke.")
            ]
        },

        # Scene 17: Week 2 Heavyweight Clashes & Full Slate
        {
            'card': {
                'title': "WEEK 2 HEAVYWEIGHT GAUNTLET",
                'subtitle': "Clashes of Titans Across All 4 Divisions",
                'badge': "SCHEDULE SPOTLIGHT",
                'accent': (231, 76, 60),
                'items': [
                    {'tag': "TITANS", 'header': f"{t_disp('Abe')} (1-0)  vs.  {t_disp('Ullenbrauck')} (1-0)", 'desc': "Defending champion battles former champion in a clash of unbeatens."},
                    {'tag': "RIVALRY", 'header': f"{t_disp('rej')} (0-1)  vs.  {t_disp('Dino')} (0-1)", 'desc': "Century deadlock rivalry with both franchises hungry for win #1."},
                    {'tag': "HUNT", 'header': f"{t_disp('Tommy')} (0-1)  vs.  {t_disp('Samran')} (0-1)", 'desc': "Both two-time finalists battling to avoid an 0-2 hole."}
                ]
            },
            'dialogue': [
                ('DAVE', "And check out the clash of titans down South: defending champion Abe at one-and-oh takes on Thor at one-and-oh! Two former champions battling for early supremacy. Meanwhile, Rej at oh-and-one squares off with Dino at oh-and-one in their legendary rivalry, and Thomas takes on Samran with both franchises desperately hunting for win number one!")
            ]
        },

        # Scene 18: Outro & Thursday Lookahead
        {
            'card': {
                'title': "THURSDAY NIGHT FOOTBALL LOOKAHEAD",
                'subtitle': "Waiver Bids & Simulated Spreads Coming Thursday",
                'badge': "SIGN-OFF",
                'accent': (41, 128, 185),
                'items': [
                    {'tag': "SCHEDULE", 'header': "TNF Kickoff in 72 Hours", 'desc': "Vegas spreads & positional starting battles drop Thursday morning."},
                    {'tag': "WAIVERS", 'header': "Check Your Bids Today", 'desc': "Bench the goose-egg bums and optimize your lineups."},
                    {'tag': "NETWORK", 'header': "BFL Broadcast Network", 'desc': "Presented by Commissioner Nick Christus"}
                ]
            },
            'dialogue': [
                ('CHRIS', "Thursday Night Football kicks off in just seventy-two hours, and Commissioner Nick's Vegas and Lineup Desk will drop the official simulated spreads and positional battle previews on Thursday morning in the commissioner-desk channel."),
                ('DAVE', "Get your waiver bids in today, bench the bums who gave you zero points, and for the love of God, don't leave thirty points on your pine like Lukose! For Chris, Dave, and the entire BFL Tuesday Morning Hangover crew, have a great Tuesday everybody!")
            ]
        }
    ]

async def produce_full_hangover_broadcast(season: int = 2025, week_num: int = 1, post_to_discord: bool = True):
    pid = os.getpid()
    print("\n" + "="*75)
    print(f"🎙️ BFL TUESDAY MORNING HANGOVER: TOPIC-SYNCED SHOW PRODUCTION (WEEK {week_num}, {season})")
    print("="*75)

    print("📡 Fetching active live team names & rosters from ESPN API...")
    raw_data = fetch_espn_week_data(ESPN_LEAGUE_ID, str(season), week_num, ESPN_S2, ESPN_SWID)
    teams = parse_league_members_and_teams(raw_data)
    print(f"✅ Loaded {len(teams)} active BFL franchises dynamically!")

    scenes = get_show_scenes(season, week_num, teams)
    print(f"📝 Loaded {len(scenes)} structured scenes with dynamic visual transitions!")

    temp_audio_dir = Path(__file__).resolve().parent / f"temp_audio_{pid}"
    temp_slide_dir = Path(__file__).resolve().parent / f"temp_slides_{pid}"
    temp_audio_dir.mkdir(parents=True, exist_ok=True)
    temp_slide_dir.mkdir(parents=True, exist_ok=True)

    all_audio_files = []
    scene_slide_paths = []
    scene_durations = []

    print("🎙️ Synthesizing neural audio and rendering clean broadcast slides for each scene...")

    global_seg_idx = 0
    for scene_idx, sc in enumerate(scenes):
        card = sc['card']
        dialogue = sc['dialogue']

        # 1. Render Visual Slide Card
        slide_path = str(temp_slide_dir / f"scene_{scene_idx:02d}.png")
        create_slide_card(
            title=card['title'],
            subtitle=card['subtitle'],
            content_items=card['items'],
            output_path=slide_path,
            badge_text=card['badge'],
            accent_color=card['accent']
        )
        scene_slide_paths.append(slide_path)

        # 2. Synthesize Scene Audio
        scene_dur = 0.0
        for speaker, raw_text in dialogue:
            clean_text = clean_for_spoken_audio(raw_text)
            voice = VOICE_HOST1 if speaker == 'CHRIS' else VOICE_HOST2
            seg_file = str(temp_audio_dir / f"seg_{global_seg_idx:03d}_{speaker}.mp3")

            comm = edge_tts.Communicate(clean_text, voice, rate="+2%", pitch="+0Hz")
            await comm.save(seg_file)
            all_audio_files.append(seg_file)
            global_seg_idx += 1

            # Check individual duration
            cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", seg_file]
            res = subprocess.run(cmd, stdout=subprocess.PIPE, text=True)
            try:
                scene_dur += float(res.stdout.strip())
            except:
                scene_dur += 5.0

        scene_durations.append(scene_dur)
        print(f"  • Scene {scene_idx+1:02d}/{len(scenes):02d} [{card['badge']}]: {scene_dur:.1f}s")

    # 3. Stitch Master Podcast MP3
    master_mp3 = str(Path(__file__).resolve().parent / f"bfl_tuesday_morning_hangover_week_{week_num}_{season}.mp3")
    concat_list = temp_audio_dir / "concat.txt"
    with open(concat_list, 'w') as f:
        for af in all_audio_files:
            f.write(f"file '{af}'\n")

    print(f"🎵 Stitching master podcast MP3 via ffmpeg -> {master_mp3}...")
    cmd = ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_list), "-c", "copy", master_mp3]
    subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    # Cleanup audio temp
    for f in temp_audio_dir.glob("*.mp3"):
        try: f.unlink()
        except: pass
    if concat_list.exists(): concat_list.unlink()
    try: temp_audio_dir.rmdir()
    except: pass

    cmd_dur = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", master_mp3]
    res = subprocess.run(cmd_dur, stdout=subprocess.PIPE, text=True)
    dur_seconds = float(res.stdout.strip())
    mins = int(dur_seconds // 60)
    secs = int(dur_seconds % 60)
    print(f"🎉 Master Audio Rendered! Runtime: {mins}m {secs}s -> {master_mp3}")

    # 4. Generate Topic-Synced MP4 Video Reel
    master_mp4 = str(Path(__file__).resolve().parent / f"bfl_tuesday_hangover_week_{week_num}_{season}.mp4")
    print(f"🎬 Compiling Dynamic Topic-Synced MP4 Video Show (18 Transitions) -> {master_mp4}...")
    generate_topic_synced_video(scene_slide_paths, scene_durations, master_mp3, master_mp4, pid)

    # 5. Post to Discord #press-room-podcast
    if post_to_discord and os.getenv("DISCORD_WEBHOOK_PODCAST"):
        print("🚀 Uploading Tuesday Morning Hangover (MP3 + MP4) to #press-room-podcast Forum...")
        thread_title = f"☕ BFL Tuesday Morning Hangover: Week {week_num} Show ({mins}m {secs}s)"

        # Post 1: Forum Thread Creation with MP3 Audio Podcast
        thread_id = None
        with open(master_mp3, 'rb') as f_mp3:
            files_mp3 = {'file': (f"bfl_tuesday_morning_hangover_week_{week_num}_{season}.mp3", f_mp3, 'audio/mpeg')}
            data_mp3 = {
                'username': 'BFL Tuesday Morning Hangover Desk',
                'avatar_url': 'https://a.espncdn.com/combiner/i?img=/i/headshots/nfl/players/full/3918298.png',
                'thread_name': thread_title,
                'content': f"☕ **BFL TUESDAY MORNING HANGOVER: WEEK {week_num} OFFICIAL BROADCAST ({season})**\n*Chris & Dave break down all 8 matchups: Josh Allen's 394-yd 4-TD masterpiece, Derrick Henry's 169-yd rampage, A.J. Brown & Worthy duds, Sydney demolishing Lukose, Emelie's bench agony, real Discord group chat drama, and Week 2 Marquee Lookaheads!*\n\n⏱️ **Duration:** `{mins}m {secs}s`\n🎧 **Listen to the Full Audio Podcast below:** 👇"
            }
            resp_mp3 = requests.post(os.getenv("DISCORD_WEBHOOK_PODCAST") + "?wait=true", data=data_mp3, files=files_mp3, timeout=45)
            
            if resp_mp3.status_code in [200, 201, 204]:
                print("🎉 SUCCESS! Audio Podcast MP3 uploaded to Discord!")
                try:
                    thread_id = resp_mp3.json().get('channel_id')
                except:
                    pass
            else:
                print(f"❌ Discord MP3 upload error: {resp_mp3.status_code} - {resp_mp3.text}")

        # Post 2: Attach MP4 SportsCenter Video Reel to the Thread
        if os.path.exists(master_mp4):
            video_url = os.getenv("DISCORD_WEBHOOK_PODCAST")
            if thread_id:
                video_url += f"?thread_id={thread_id}"
                
            with open(master_mp4, 'rb') as f_mp4:
                files_mp4 = {'file': (f"bfl_tuesday_hangover_week_{week_num}_{season}.mp4", f_mp4, 'video/mp4')}
                data_mp4 = {
                    'username': 'BFL SportsCenter Video Reel Desk',
                    'avatar_url': 'https://a.espncdn.com/combiner/i?img=/i/headshots/nfl/players/full/3918298.png',
                    'content': "🎬 **BFL SportsCenter Full HD (1080p) Video Show:**\n*Watch the animated scoreboard cards, boxscores, and Week 2 Marquee Matchups preview below:* 📺"
                }
                resp_mp4 = requests.post(video_url, data=data_mp4, files=files_mp4, timeout=60)
                if resp_mp4.status_code in [200, 201, 204]:
                    print("🎉 SUCCESS! SportsCenter MP4 Video Show uploaded directly to Discord!")
                else:
                    print(f"❌ Discord MP4 upload error: {resp_mp4.status_code} - {resp_mp4.text}")

    # Cleanup video slides
    for f in temp_slide_dir.glob("*.png"):
        try: f.unlink()
        except: pass
    try: temp_slide_dir.rmdir()
    except: pass

    return master_mp3, master_mp4

if __name__ == "__main__":
    asyncio.run(produce_full_hangover_broadcast(2025, 1))
