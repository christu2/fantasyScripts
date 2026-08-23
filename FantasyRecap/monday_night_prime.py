#!/usr/bin/env python3
"""
BFL Broadcast Network — Monday Night Prime (The Definitive 10-Minute Show)
========================================================================
The ultimate post-week review show broadcasted every Monday night:
- Multi-host natural neural dialogue: Marcus (AndrewMultilingual) & Dave (BrianMultilingual)
- Deep player breakdowns: Booms, busts, stat lines, and bench blunders
- Strict first-name convention (Nick, Tommy, Abe, Saagar, Dino, Dan, Blake, Nael, Samran, Rej, Alex, Adam, Emelie)
- Explicit Shawn disambiguation (Shawn Lukose vs. Shawn Ullenbrauck)
- Accurate gender pronouns (Sydney: she/her, Emelie: she/her, all others: he/him)
- Respectful recognition of Commissioner Nick
- Natural spoken phonetic cleanup (zero script headers or robotic shorthand)
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

# Load .env
env_path = Path(__file__).resolve().parent.parent / '.env'
if env_path.exists():
    load_dotenv(dotenv_path=env_path)
else:
    load_dotenv()

VOICE_MARCUS = 'en-US-AndrewMultilingualNeural'  # Lead Host
VOICE_DAVE = 'en-US-BrianMultilingualNeural'    # Color Commentator

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
    (r'\bSNF\b', 'Sunday Night Football'),
    (r'\b0\.0\b', 'zero point zero'),
    (r'\b1\.3\b', 'one point three'),
    (r'\b3\.04\b', 'three point zero four')
]

def clean_for_spoken_audio(text: str) -> str:
    """Cleans text so it flows naturally when spoken."""
    text = re.sub(r'[*#_`~>|]', '', text)
    text = re.sub(r'^\[[A-Za-z\s]+\]:\s*', '', text)
    text = re.sub(r'^\*\*[A-Za-z\s]+\*\*:\s*', '', text)
    for pattern, replacement in PHONETIC_REPLACEMENTS:
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
    text = re.sub(r'\s+', ' ', text).strip()
    return text

def build_10min_monday_prime_dialogue(season: int = 2025, week_num: int = 1) -> list:
    """
    Constructs an extensive, detailed 8-to-10 minute sports talk script covering all 8 matchups.
    """
    dialogue = []

    # ACT 1: COLD OPEN & KICKOFF
    dialogue.append(('MARCUS', f"Good evening and welcome inside the studio for BFL Monday Night Prime! I'm Marcus alongside Dave, and the final whistle on Monday Night Football has officially sounded on week {week_num} of the {season} Beasts Football League season."))
    dialogue.append(('DAVE', "Marcus, what an unbelievable opening week of fantasy football! Commissioner Nick has this league in top form, and week one did not disappoint. We had heart-stopping finishes decided by a single reception, thirty-point statement blowouts, elite players laying complete goose eggs, and some agonizing bench decisions that are going to keep coaches awake all night."))
    dialogue.append(('MARCUS', "Let's dive straight into the action with our Game of the Week in the West Division, where Adam and Green and Golden survived a nail-biter against Emelie, winning ninety-one point three two to eighty-eight point two eight!"))
    dialogue.append(('DAVE', "Marcus, you have to feel for Emelie here. She got sixteen points from Brock Purdy, but Nico Collins completely vanished with only four points against a projection of over fourteen. On the other side, Adam got a monster twenty-three-point game from Bijan Robinson to carry his squad, even with Joe Burrow struggling under nine points."))
    dialogue.append(('MARCUS', "And the biggest storyline of that game, Dave, was on Emelie's bench. She had Wan'Dale Robinson sitting on her pine with fourteen and a half points! If she inserts Wan'Dale into her lineup over Nico Collins, she wins this game by over seven points. Instead, Adam walks away with the victory and Emelie is left with the most agonizing three-point loss imaginable."))
    dialogue.append(('DAVE', "Leaving points on the pine when you lose by three points is the absolute worst feeling in fantasy sports, Marcus. Emelie coached a great week, but that one start-sit decision was the difference between one-and-oh and oh-and-one."))

    # ACT 2: HIGH-PROFILE DEMOLITIONS & RIVALRIES
    dialogue.append(('MARCUS', "Let's shift over to the East Division for the biggest beatdown of the week, where Sydney put on an absolute clinic against four-time champion Shawn Lukose, winning one hundred and one to sixty-nine!"))
    dialogue.append(('DAVE', "Sydney was unbelievable, Marcus! She got a massive twenty-four point boom from Zay Flowers, who crushed his twelve-point projection, and her entire lineup played fast and aggressive. But what on earth happened to Shawn Lukose? Kenneth Walker laid a dud with under four points, and Shawn Lukose made the single most disastrous quarterback call of the week."))
    dialogue.append(('MARCUS', "That was shocking, Dave. Shawn Lukose started Drake Maye, who put up just fifteen points, while Justin Fields went completely berserk on his bench with twenty-nine and a half fantasy points! That fourteen-point quarterback blunder turned a competitive game into a thirty-point embarrassment. Sydney proved once again that she is a legitimate championship powerhouse."))
    dialogue.append(('DAVE', "Sydney sent a loud and clear message across the league: the road to the title runs through her squad! Meanwhile, Shawn Lukose has serious questions to answer in his quarterback room heading into week two."))

    # ACT 3: DIVISION RIVALRIES & HIGH-SCORING SHOWDOWNS
    dialogue.append(('MARCUS', "Down in the South Division, defending champion Abe and Crashee Bandicoot showed zero championship hangover, lighting up the scoreboard with a league-high one hundred and seventeen points to take down Saagar by twenty-four!"))
    dialogue.append(('DAVE', "Abe was firing on all cylinders! Lamar Jackson was an absolute cheat code with nearly thirty fantasy points, and Garrett Wilson boomed for eighteen points. Saagar got twenty-six points from Patrick Mahomes, but A.J. Brown had a total nightmare game, finishing with just one point three points on a fourteen-point projection! Saagar's eighteen-year championship drought is officially off to a rough start."))
    dialogue.append(('MARCUS', "Over in the North Division, three-time champion Commissioner Nick and the Mykonos Minotaurs defended home turf with authority, defeating Dino one hundred to eighty-five!"))
    dialogue.append(('DAVE', "Commissioner Nick overcame a weird three-point game from Ja'Marr Chase thanks to an explosive twenty-one-point performance from Keon Coleman, who doubled his weekly projection! Dino got twenty-four points from Jalen Hurts, but Tee Higgins couldn't get going with under five points. Nick tops the century mark and immediately takes control of the North."))
    dialogue.append(('MARCUS', "Now let's talk about the absolute heartbreak of the week, which belongs to Rej. Rej put up ninety-seven point six six points—the fourth highest score in the entire sixteen-team league—and still took a loss to Dan, who scored one hundred and five!"))
    dialogue.append(('DAVE', "Rej is our undisputed Tough Luck Loser of week one! Josh Allen went nuclear for Rej with nearly thirty-nine fantasy points, the highest individual score of the entire week. But Dan had incredible balance, led by nineteen points from Javonte Williams. Dan's depth edged out Rej's superstar power in a high-scoring classic."))

    # ACT 4: DEFENSIVE BATTLES & THE REST OF THE SLATE
    dialogue.append(('MARCUS', "In the other matchups around the league, Blake survived a ninety-five to ninety-two defensive slugfest against Alex. King Derrick Henry ran wild for Blake with thirty point seven fantasy points, overcoming Caleb Williams' twenty-four-point effort for Alex."))
    dialogue.append(('DAVE', "Derrick Henry looked like a man possessed, Marcus! When Henry gives you thirty points, you are almost impossible to beat. Alex fought hard with Caleb Williams, but Terry McLaurin was held to under four points, which ended up costing Alex the game."))
    dialogue.append(('MARCUS', "In the cross-division battle between Nael and Samran, Nael rolled to a one hundred and two to eighty-five victory behind a sensational twenty-eight-point masterclass from Justin Herbert!"))
    dialogue.append(('DAVE', "Herbert was dropping dimes all Sunday afternoon! Samran got twenty-one points from Emeka Egbuka, but Jerome Ford managed only one single point in his backfield, completely stalling Samran's offense."))
    dialogue.append(('MARCUS', "And wrapping up the week one slate, Shawn Ullenbrauck held off Tommy ninety-four to eighty-five in a battle of North-versus-West."))
    dialogue.append(('DAVE', "Tommy's hunt for ring number one hit a huge bump in the road. Tommy got twenty-two points from J.J. McCarthy, but Xavier Worthy put up a devastating zero-point doughnut on a fourteen-point projection! That zero hurt Tommy immensely, and Shawn Ullenbrauck took full advantage behind fifteen points from Courtland Sutton."))

    # ACT 5: PRESS ROOM SOUNDBITES & THURSDAY LOOKAHEAD
    dialogue.append(('MARCUS', "Now let's head into the post-game press room to hear directly from the coaches. Sydney was glowing after her blowout win, telling our reporters: 'Putting up a thirty-point beatdown on the four-time champion in week one sets the standard. We are here to win it all!'"))
    dialogue.append(('DAVE', "And Adam was all smiles after surviving his close call against Emelie, joking: 'We survived by the skin of our teeth! Wan'Dale Robinson sitting on Emelie's bench was our true MVP of week one.'"))
    dialogue.append(('MARCUS', "On the losing side, Shawn Lukose was visibly frustrated, saying: 'Disaster across the board. Starting Drake Maye over Justin Fields cost us fourteen points. We are holding an emergency meeting tomorrow morning to fix our lineup process.'"))
    dialogue.append(('DAVE', "And Rej summed up his high-scoring loss with pure pain, saying: 'I scored ninety-seven points with Josh Allen dropping thirty-nine, and I still take the loss. The fantasy gods tested my patience this week.'"))
    dialogue.append(('MARCUS', "Looking ahead, week two is just three days away! Thursday Night Football will be here before you know it, and Commissioner Nick's Vegas and Lineup Desk will drop the official spreads, point totals, and starting lineup duels on Thursday morning in the commissioner-desk channel."))
    dialogue.append(('DAVE', "Get your waiver claims submitted, bench your duds, and don't leave your best players on the pine! For Marcus and the entire BFL Monday Night Prime broadcast team, have a great night everyone!"))

    return dialogue

async def produce_monday_night_prime(season: int = 2025, week_num: int = 1, post_to_discord: bool = True):
    print("\n" + "="*75)
    print(f"🎙️ PRODUCING BFL MONDAY NIGHT PRIME: WEEK {week_num} ({season})")
    print("="*75)

    dialogue = build_10min_monday_prime_dialogue(season, week_num)
    print(f"📝 Generated {len(dialogue)} extensive dialogue lines (Target runtime: 8-10 mins)!")

    temp_dir = Path(__file__).resolve().parent / "temp_audio_monday_prime"
    temp_dir.mkdir(exist_ok=True)

    audio_files = []
    print("🎙️ Synthesizing Marcus (AndrewMultilingual) & Dave (BrianMultilingual) audio...")

    for idx, (speaker, raw_text) in enumerate(dialogue):
        clean_text = clean_for_spoken_audio(raw_text)
        voice = VOICE_MARCUS if speaker == 'MARCUS' else VOICE_DAVE
        seg_file = str(temp_dir / f"seg_{idx:03d}_{speaker}.mp3")

        # Natural conversational pacing
        comm = edge_tts.Communicate(clean_text, voice, rate="+2%", pitch="+0Hz")
        await comm.save(seg_file)
        audio_files.append(seg_file)

    master_mp3 = str(Path(__file__).resolve().parent / f"bfl_monday_night_prime_week_{week_num}_{season}.mp3")
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

    # Get final audio duration
    cmd_dur = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", master_mp3]
    res = subprocess.run(cmd_dur, stdout=subprocess.PIPE, text=True)
    dur_seconds = float(res.stdout.strip())
    mins = int(dur_seconds // 60)
    secs = int(dur_seconds % 60)
    print(f"🎉 Master Show Rendered Successfully! Runtime: {mins}m {secs}s -> {master_mp3}")

    if post_to_discord and os.getenv("DISCORD_WEBHOOK_PODCAST"):
        print("🚀 Uploading Monday Night Prime Episode to #press-room-podcast Forum...")
        thread_title = f"🎙️ BFL Monday Night Prime: Week {week_num} Full Episode ({mins}m {secs}s)"

        with open(master_mp3, 'rb') as f:
            files = {'file': (f"bfl_monday_night_prime_week_{week_num}_{season}.mp3", f, 'audio/mpeg')}
            data = {
                'username': 'BFL Monday Night Prime Anchor Desk',
                'avatar_url': 'https://a.espncdn.com/combiner/i?img=/i/headshots/nfl/players/full/3918298.png',
                'thread_name': thread_title,
                'content': f"🎙️ **BFL MONDAY NIGHT PRIME: WEEK {week_num} OFFICIAL EPISODE ({season})**\n*Hosts Marcus & Dave break down all 8 matchups in depth: Lamar & Josh Allen explosions, Worthy & A.J. Brown duds, Emelie & Shawn Lukose bench blunders, and post-game press room soundbites!*\n\n⏱️ **Episode Duration:** `{mins}m {secs}s`\n🎧 **Listen to the full broadcast below:** 👇"
            }
            resp = requests.post(os.getenv("DISCORD_WEBHOOK_PODCAST"), data=data, files=files, timeout=45)
            if resp.status_code in [200, 204]:
                print("🎉 SUCCESS! Monday Night Prime Full Episode uploaded directly to Discord!")
            else:
                print(f"❌ Discord error: {resp.status_code} - {resp.text}")

    return master_mp3

if __name__ == "__main__":
    asyncio.run(produce_monday_night_prime(2025, 1))
