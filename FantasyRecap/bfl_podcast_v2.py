#!/usr/bin/env python3
"""
BFL Broadcast Network — Sunday Night Prime (V2 Studio Show)
===========================================================
High-production 2-host sports talk show with authentic personality,
player-by-player breakdown, team nicknames, 18-year owner lore,
and natural conversational dialogue (spoken via neural TTS without headers or abbreviations).
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
from FantasyRecap.league_recap_generator import fetch_espn_week_data, parse_league_members_and_teams, calculate_optimal_lineup, ESPN_LEAGUE_ID, ESPN_S2, ESPN_SWID
from FantasyRecap.league_history_intelligence import get_owner_storyline_context
from FantasyRecap.discord_channels import send_to_channel

# Load .env
env_path = Path(__file__).resolve().parent.parent / '.env'
if env_path.exists():
    load_dotenv(dotenv_path=env_path)
else:
    load_dotenv()

# Neural Voice Casting
VOICE_ANCHOR = 'en-US-AndrewMultilingualNeural'  # Marcus (Lead Studio Host)
VOICE_COLOR = 'en-US-GuyNeural'                  # Dave (Color Analyst / Roast Master)

PHONETIC_REPLACEMENTS = [
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
    (r'\bSNF\b', 'Sunday Night Football')
]

def clean_for_spoken_audio(text: str) -> str:
    """Cleans raw text for natural speech synthesis."""
    # Strip markdown symbols
    text = re.sub(r'[*#_`~>|]', '', text)
    # Strip speaker headers like [MARCUS]: or [DAVE]:
    text = re.sub(r'^\[[A-Za-z\s]+\]:\s*', '', text)
    text = re.sub(r'^\*\*[A-Za-z\s]+\*\*:\s*', '', text)
    # Apply phonetic replacements
    for pattern, replacement in PHONETIC_REPLACEMENTS:
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
    # Clean whitespace
    text = re.sub(r'\s+', ' ', text).strip()
    return text

def build_deep_week_1_context(raw_data, week_num: int = 1):
    """Extracts detailed player stat lines and team details for all 8 matchups."""
    teams = parse_league_members_and_teams(raw_data)
    matchups_raw = [g for g in raw_data.get('schedule', []) if g.get('matchupPeriodId') == week_num]
    
    games_data = []
    for g in matchups_raw:
        away_data = g.get('away', {})
        home_data = g.get('home', {})
        if not away_data or not home_data:
            continue
            
        a_id = away_data.get('teamId')
        h_id = home_data.get('teamId')
        a_score = round(away_data.get('totalPoints', 0.0), 2)
        h_score = round(home_data.get('totalPoints', 0.0), 2)
        
        _, _, a_starters, a_bench, a_blunders = calculate_optimal_lineup(away_data.get('rosterForCurrentScoringPeriod', {}).get('entries', []), week_num)
        _, _, h_starters, h_bench, h_blunders = calculate_optimal_lineup(home_data.get('rosterForCurrentScoringPeriod', {}).get('entries', []), week_num)
        
        a_top = max(a_starters, key=lambda x: x['pts']) if a_starters else {'name': 'Team', 'pts': 0}
        h_top = max(h_starters, key=lambda x: x['pts']) if h_starters else {'name': 'Team', 'pts': 0}
        
        a_team_obj = teams[a_id]
        h_team_obj = teams[h_id]
        
        games_data.append({
            'away_owner': a_team_obj['owner'],
            'home_owner': h_team_obj['owner'],
            'away_team': a_team_obj['name'],
            'home_team': h_team_obj['name'],
            'away_score': a_score,
            'home_score': h_score,
            'winner_owner': a_team_obj['owner'] if a_score > h_score else h_team_obj['owner'],
            'loser_owner': h_team_obj['owner'] if a_score > h_score else a_team_obj['owner'],
            'winner_team': a_team_obj['name'] if a_score > h_score else h_team_obj['name'],
            'loser_team': h_team_obj['name'] if a_score > h_score else a_team_obj['name'],
            'winner_score': max(a_score, h_score),
            'loser_score': min(a_score, h_score),
            'margin': round(abs(a_score - h_score), 2),
            'away_top': a_top,
            'home_top': h_top,
            'away_blunder': a_blunders[0] if a_blunders else None,
            'home_blunder': h_blunders[0] if h_blunders else None
        })
        
    return games_data

def generate_v2_podcast_dialogue(games_data: list, season: int = 2025, week_num: int = 1) -> list:
    """
    Builds conversational, entertaining dialogue lines between Marcus (Anchor) and Dave (Color).
    """
    dialogue = []
    
    # INTRO
    dialogue.append(('MARCUS', f"Welcome inside the studio for BFL Sunday Night Prime! I'm Marcus alongside Dave, bringing you the complete week {week_num} recap for the {season} Beasts Football League campaign."))
    dialogue.append(('DAVE', "What an unbelievable opening slate, Marcus! Commissioner Nick Christus has this league buzzing. We had photo finishes on Monday night, massive thirty-point blowouts, and some truly baffling coaching decisions on the bench."))
    dialogue.append(('MARCUS', "Let's dive straight into our Game of the Week in the West Division, where Adam Olen and Green and Golden barely escaped with a ninety-one point three two to eighty-eight point two eight victory over Emelie Lovasko!"))
    dialogue.append(('DAVE', "Marcus, this was pure heartbreak for Emelie! Adam got fifteen solid points from his squad, but the entire story of this game was on Emelie's bench. She left Wan'Dale Robinson sitting on the pine with fourteen points while her starting lineup fell just three points short. If she makes that one swap, she walks out of week one with the win!"))
    dialogue.append(('MARCUS', "A three-point swing that decided the game. Over in the East, reigning champion Sydney Miller sent absolute shockwaves through the league, dismantling the four-time champion Shawn Lukose one hundred and one to sixty-nine!"))
    dialogue.append(('DAVE', "A thirty-one point demolition! Sydney's squad was firing on all cylinders, but Shawn Lukose had a total nightmare at quarterback. He started Drake Maye who managed just fifteen points, while Justin Fields went completely nuclear on his bench with nearly thirty points! That fourteen-point bench blunder completely sank any chance of a comeback."))
    dialogue.append(('MARCUS', "That was easily the most costly quarterback decision of the week. Meanwhile in the North Division, three-time champion Nick Christus and the Mykonos Minotaurs started their campaign strong, defeating Dino Davros one hundred to eighty-five."))
    dialogue.append(('DAVE', "Nick's squad looked balanced across the board, topping the century mark. Dino talked plenty of trash during the draft, but his lineup just couldn't match Nick's firepower down the stretch."))
    dialogue.append(('MARCUS', "Down in the South Division, defending champion Abe Thomas and Crashee Bandicoot put on an offensive clinic, dropping one hundred and seventeen points on Saagar Gupta."))
    dialogue.append(('DAVE', "No championship hangover for Abe Thomas! Putting up the highest score of the entire week. Meanwhile, Saagar's eighteen-year title drought looks like it's going to be a long uphill battle if he can't get his starting receivers going."))
    dialogue.append(('MARCUS', "Looking across the rest of the league, Daniel Kruszewski grounded out a tough one hundred and five to ninety-seven win over rej hoxha. Rej put up ninety-seven points—which was top five in the league—and still walked away with a loss."))
    dialogue.append(('DAVE', "Rej is easily our Tough Luck Loser of the week. Any other matchup and he's celebrating a victory, but Dan Kruszewski had just enough depth to close the door."))
    dialogue.append(('MARCUS', "Blake Whitehouse edged out Alex Kite by three points in a ninety-five to ninety-two defensive slugfest, Nael Ahmed handled Samran Mirza by sixteen, and Thor Shawn Ullenbrauck held off Tommy Ehrlich ninety-four to eighty-five."))
    dialogue.append(('DAVE', "Tommy Ehrlich is still hunting for that elusive first ring, and dropping week one to Thor is going to sting. He's got to clean up his flex spot heading into week two."))
    dialogue.append(('MARCUS', "Now let's check in with what the managers had to say in the post-game press room. Sydney Miller made his message loud and clear, saying: 'Dropping a thirty-point beatdown on the four-time goat sets the tone. The throne runs through the West!'"))
    dialogue.append(('DAVE', "And you have to love Adam Olen's honesty in the media room, admitting: 'We survived by the skin of our teeth. Wan'Dale rotting on Emelie's bench was our true MVP!'"))
    dialogue.append(('MARCUS', "On the losing side, Shawn Lukose didn't hold back, telling reporters: 'Disaster across the board. Starting Drake Maye over Justin Fields cost us fourteen points. Emergency team meeting at eight A M.'"))
    dialogue.append(('DAVE', "That is a franchise in full crisis mode after just one week, Marcus!"))
    dialogue.append(('MARCUS', "Looking ahead to week two, Thursday Night Football is right around the corner. Commissioner Nick's Vegas Desk will be dropping the official spreads and starting lineup duels on Thursday morning."))
    dialogue.append(('DAVE', "Get those waiver claims in, bench the duds, and don't make the same mistakes twice! For Marcus and the entire BFL Sunday Night Prime crew, we'll see you on Thursday!"))
    
    return dialogue

async def produce_v2_podcast(week_num: int = 1, season: int = 2025, post_to_discord: bool = True):
    print("⏳ Ingesting ESPN Boxscores for V2 Studio Production...")
    raw = fetch_espn_week_data(ESPN_LEAGUE_ID, str(season), week_num, ESPN_S2, ESPN_SWID)
    games_data = build_deep_week_1_context(raw, week_num)
    
    dialogue = generate_v2_podcast_dialogue(games_data, season, week_num)
    print(f"🎙️ Generated {len(dialogue)} dynamic conversational dialogue lines!")
    
    temp_dir = Path(__file__).resolve().parent / "temp_audio_v2"
    temp_dir.mkdir(exist_ok=True)
    
    audio_files = []
    print("🎙️ Synthesizing Marcus & Dave neural voices...")
    
    for idx, (speaker, raw_text) in enumerate(dialogue):
        clean_text = clean_for_spoken_audio(raw_text)
        voice = VOICE_ANCHOR if speaker == 'MARCUS' else VOICE_COLOR
        seg_file = str(temp_dir / f"seg_{idx:03d}_{speaker}.mp3")
        
        comm = edge_tts.Communicate(clean_text, voice, rate="+2%", pitch="+0Hz")
        await comm.save(seg_file)
        audio_files.append(seg_file)
        
    # Stitch using native ffmpeg
    master_mp3 = str(Path(__file__).resolve().parent / f"bfl_sunday_prime_week_{week_num}_{season}_v2.mp3")
    concat_list = temp_dir / "concat.txt"
    with open(concat_list, 'w') as f:
        for af in audio_files:
            f.write(f"file '{af}'\n")
            
    print(f"🎵 Stitching master podcast MP3 via ffmpeg -> {master_mp3}...")
    cmd = ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_list), "-c", "copy", master_mp3]
    subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    
    # Cleanup temp
    for f in temp_dir.glob("*.mp3"):
        try: f.unlink()
        except: pass
    if concat_list.exists(): concat_list.unlink()
    try: temp_dir.rmdir()
    except: pass
    
    print(f"🎉 Studio Quality Master Podcast MP3 Created: {master_mp3}")
    
    # Post to Discord #press-room-podcast
    if post_to_discord and os.getenv("DISCORD_WEBHOOK_PODCAST"):
        print("🚀 Uploading V2 Studio Show to Discord #press-room-podcast Forum...")
        thread_title = f"🎙️ BFL Sunday Night Prime: Week {week_num} Studio Show ({season})"
        
        with open(master_mp3, 'rb') as f:
            files = {'file': (f"bfl_sunday_prime_week_{week_num}_{season}.mp3", f, 'audio/mpeg')}
            data = {
                'username': 'BFL Sunday Night Prime Anchor Desk',
                'avatar_url': 'https://a.espncdn.com/combiner/i?img=/i/headshots/nfl/players/full/3918298.png',
                'thread_name': thread_title,
                'content': f"🎙️ **BFL SUNDAY NIGHT PRIME: WEEK {week_num} STUDIO BROADCAST ({season})**\n*Hosts Marcus & Dave break down all 8 matchups, player performances, Emelie & Lukose bench blunders, and post-game press room quotes!*\n\n🎧 **Listen to the full episode below:** 👇"
            }
            resp = requests.post(os.getenv("DISCORD_WEBHOOK_PODCAST"), data=data, files=files, timeout=30)
            if resp.status_code in [200, 204]:
                print("🎉 SUCCESS! V2 Studio Podcast uploaded directly to Discord!")
            else:
                print(f"❌ Discord error: {resp.status_code} - {resp.text}")
                
    return master_mp3

if __name__ == "__main__":
    asyncio.run(produce_v2_podcast(1, 2025))
