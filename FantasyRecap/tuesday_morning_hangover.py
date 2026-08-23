#!/usr/bin/env python3
"""
BFL Tuesday Morning Hangover (The Official Morning Commute Podcast)
===================================================================
A hilarious, profane, high-energy 8-to-10 minute post-week podcast in the style
of Pardon My Take (PMT):
- Aired every Tuesday morning after midnight post-game owner interviews
- Hosts: Chris (ChristopherNeural) & Dave (BrianMultilingualNeural)
- Casual banter, swearing, roasts, bad beat agony, and 18-year historical lore
- Phonetic accuracy:
    * Shawn Lukose -> "Luke-ose"
    * Shawn Ullenbrauck -> "Thor"
    * Tommy -> "Thomas"
    * Mykonos -> "MEE-ko-nos"
    * Nael -> "Nile"
    * Samran -> "Sum-rahn"
    * Sydney: she/her, Emelie: she/her, all others: he/him
    * Nick: Commissioner Nick
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

# Load .env
env_path = Path(__file__).resolve().parent.parent / '.env'
if env_path.exists():
    load_dotenv(dotenv_path=env_path)
else:
    load_dotenv()

VOICE_HOST1 = 'en-US-ChristopherNeural'       # Chris (Big Cat vibe)
VOICE_HOST2 = 'en-US-BrianMultilingualNeural' # Dave (PFT vibe)

PHONETIC_RULES = [
    (r'\bShawn Lukose\b', 'Luke-ose'),
    (r'\bLukose\b', 'Luke-ose'),
    (r'\bShawn Ullenbrauck\b', 'Thor'),
    (r'\bTommy\b', 'Thomas'),
    (r'\bMykonos\b', 'MEE-ko-nos'),
    (r'\bNael\b', 'Nile'),
    (r'\bSamran\b', 'Sum-rahn'),
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
    """Cleans text and applies strict phonetic pronunciation."""
    text = re.sub(r'[*#_`~>|]', '', text)
    text = re.sub(r'^\[[A-Za-z0-9\s]+\]:\s*', '', text)
    text = re.sub(r'^\*\*[A-Za-z0-9\s]+\*\*:\s*', '', text)
    for pattern, replacement in PHONETIC_RULES:
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
    text = re.sub(r'\s+', ' ', text).strip()
    return text

def build_hangover_dialogue(season: int = 2025, week_num: int = 1) -> list:
    """
    Constructs the extensive, funny, unfiltered 8-to-10 minute Tuesday Morning Hangover episode.
    """
    d = []

    # ACT 1: COLD OPEN & MORNING HANGOVER
    d.append(('CHRIS', f"Good Tuesday morning everybody! Grab your coffee, pop an Advil, and welcome inside the BFL Tuesday Morning Hangover, presented by the BFL Podcast Network. Week {week_num} of the {season} season is officially in the books, the midnight post-game media texts are in, and fantasy football has already ruined half the league's week. I'm Chris alongside Dave, and holy shit Dave, what an absolute circus of an opening week!"))
    d.append(('DAVE', "Chris, my head is pounding and it's only week one! Commissioner Nick Christus has this eighteen-year-old league operating at peak degeneracy. We had sickening bad beats on Monday night, thirty-point public executions, elite wide receivers putting up actual goose eggs, and coaching decisions so criminally stupid they should be investigated by the federal government!"))
    d.append(('CHRIS', "Let's start right off the top with our Game of the Week in the West Division. Adam and Green and Golden barely escaped with their lives against Emelie, ninety-one point three two to eighty-eight point two eight!"))
    d.append(('DAVE', "Are you kidding me, Chris?! Emelie got completely screwed by the fantasy gods, but mostly by her own damn lineup sheet! She starts Nico Collins, and the dude puts up four points on a fourteen-point projection. Meanwhile, Adam gets a twenty-three-point bailout from Bijan Robinson to save his ass because Joe Burrow looked like he was playing with a blindfold on, scoring under nine points."))
    d.append(('CHRIS', "And Dave, look at Emelie's bench! She had Wan'Dale Robinson rotting on her pine with fourteen and a half points! If she makes the simple swap and benches Nico for Wan'Dale, she wins by seven points and is celebrating with a morning mimosa. Instead, she loses by three point zero four points. That is pure, unfiltered pain."))
    d.append(('DAVE', "That is a sickening beat! Leaving points on the bench when you lose by three points is the kind of mistake that makes you stare at your steering wheel on your Tuesday morning commute questioning all your life choices. Adam walks out with a greasy win, and Emelie takes the toughest L of week one."))

    # ACT 2: SYDNEY DESTROYS LUKOSE & THE 4-TIME GOAT
    d.append(('CHRIS', "Now let's head over to the East Division for the absolute demolition of the week. Sydney walked into Lukose's house, kicked his front door down, and delivered a one hundred and one to sixty-nine ass-kicking!"))
    d.append(('DAVE', "Sydney was an absolute savage, Chris! She got twenty-five points from Zay Flowers, who went totally nuclear, and her squad looked like a legitimate championship juggernaut. But can we talk about Lukose for a second? The four-time champion had the single biggest coaching brain-fart I have seen in eighteen years of BFL history!"))
    d.append(('CHRIS', "It was a total disaster, Dave. Lukose started Drake Maye, who gave him fifteen points, while Justin Fields was chilling on his bench dropping nearly thirty fantasy points! A fourteen-point quarterback blunder! Kenneth Walker laid an egg with under four points, and Sydney just ran him out of the gym by thirty-one points."))
    d.append(('DAVE', "Sydney proved she is the undisputed queen of the West Division and put the entire league on notice. Lukose has four championship rings in his trophy case, but after getting embarrassed like that on opening week, he's holding an eight A M team meeting to find out who forgot to set the lineup!"))

    # ACT 3: ABE'S CLINIC, SAAGAR'S DROUGHT, & COMMISH NICK'S DOMINANCE
    d.append(('CHRIS', "Down in the South Division, defending champion Abe and Crashee Bandicoot showed zero championship hangover, dropping a league-high one hundred and seventeen points on Saagar, winning by twenty-four!"))
    d.append(('DAVE', "Abe looked unstoppable! Lamar Jackson was running around like a video game cheat code with nearly thirty fantasy points, and Garrett Wilson went off for eighteen. Meanwhile, poor Saagar. Patrick Mahomes gave him twenty-six, but A.J. Brown put up a pathetic one point three points on a fourteen-point projection!"))
    d.append(('CHRIS', "Saagar won the inaugural title back in 2008, and he has now entered year nineteen of his title drought. Starting off oh-and-one by twenty-four points to Abe is just cruel."))
    d.append(('DAVE', "Saagar's championship drought is old enough to vote and buy a lottery ticket, Chris! Abe looked dominant, and the South Division throne is clearly his until proven otherwise."))
    d.append(('CHRIS', "Over in the North Division, our fearless Commissioner Nick and the Mykonos Minotaurs took care of business, handling Dino one hundred to eighty-five!"))
    d.append(('DAVE', "Commissioner Nick top-scored in the North despite Ja'Marr Chase having a weird three-point dud! Keon Coleman exploded for twenty-one points, more than doubling his projection, to bail out the Minotaurs. Dino got twenty-four from Jalen Hurts, but Tee Higgins was locked up for under five points. Nick defends his three rings and reminds Dino why he leads their lifetime series!"))

    # ACT 4: REJ'S TOUGH LUCK & THE SLATE RUNDOWN
    d.append(('CHRIS', "Let's pour one out for Rej, who is our official Bad Beat of the Century winner for week one. Rej put up ninety-seven point six six points—the fourth highest score in the entire sixteen-team league—and still took an L to Dan, who put up one hundred and five!"))
    d.append(('DAVE', "Josh Allen went completely thermonuclear for Rej with thirty-nine fantasy points, the highest individual score in the NFL this week! And Rej still loses because Dan got nineteen from Javonte Williams and solid balance across the board. If Rej played fourteen other teams this week he wins easily, but he ran straight into Dan's buzzsaw. God truly hates Rej on opening week."))
    d.append(('CHRIS', "Total heartbreak for Rej. In other action, Blake edged out Alex ninety-five to ninety-two in a gritty defensive battle. King Derrick Henry ran for over thirty fantasy points to carry Blake to the finish line!"))
    d.append(('DAVE', "Derrick Henry is thirty years old and still running over human beings like a runaway freight train! Alex got twenty-four from rookie Caleb Williams, but Terry McLaurin was locked in a phone booth for three points. Blake escapes by three."))
    d.append(('CHRIS', "Down in the cross-division matchup, Nael rolled over Samran one hundred and two to eighty-five behind a twenty-eight-point masterclass from Justin Herbert."))
    d.append(('DAVE', "Justin Herbert was dropping dimes all day! Samran got twenty-one from Emeka Egbuka, but Jerome Ford gave him literally one single point in his backfield. You cannot win in this league getting one point from your running back, period."))
    d.append(('CHRIS', "And wrapping up the week one gauntlet, Thor held off Thomas ninety-four to eighty-five in a classic North-versus-West grudge match."))
    d.append(('DAVE', "Thomas is perpetually chasing that elusive first championship ring, and dropping week one to Thor hurts. Thomas got twenty-two from J.J. McCarthy, but Xavier Worthy gave him an absolute doughnut with zero point zero points on a fourteen-point projection! A zero in your starting lineup will kill you every single time."))

    # ACT 5: POST-GAME PRESS ROOM & THURSDAY LOOKAHEAD
    d.append(('CHRIS', "Now let's head down to the media room for our post-game press conference soundbites collected after midnight. Sydney was asked about taking down Lukose and said: 'Dropping a thirty-point beatdown on the four-time champ in week one sets the standard. We are here to win the whole damn thing!'"))
    d.append(('DAVE', "And Adam was all smiles after his three-point miracle over Emelie, saying: 'We survived by the skin of our teeth! Wan'Dale Robinson rotting on Emelie's bench was our true MVP of week one.' That is just savage from Adam!"))
    d.append(('CHRIS', "Meanwhile, Lukose was pissed off in the hallway, telling our reporters: 'Disaster across the board. Starting Drake Maye over Justin Fields cost us fourteen points. We are holding an emergency meeting at eight A M to fix this shit.'"))
    d.append(('DAVE', "And Rej gave the quote of the night after his thirty-nine-point Josh Allen explosion went to waste, saying: 'I scored ninety-seven points with Josh Allen dropping thirty-nine, and I still take the loss. The fantasy gods are testing my sanity.'"))
    d.append(('CHRIS', "Looking ahead, week two is just three days away! Thursday Night Football kicks off in seventy-two hours, and Commissioner Nick's Vegas and Lineup Desk will drop the official simulated spreads and positional battle previews on Thursday morning in the commissioner-desk channel."))
    d.append(('DAVE', "Get your waiver bids in tonight, bench the bums who gave you zero points, and for the love of God, don't leave thirty points on your pine like Lukose! For Chris, Dave, and the entire BFL Tuesday Morning Hangover crew, have a great Tuesday everybody!"))

    return d

async def produce_hangover_show(season: int = 2025, week_num: int = 1, post_to_discord: bool = True):
    print("\n" + "="*75)
    print(f"🎙️ PRODUCING BFL TUESDAY MORNING HANGOVER: WEEK {week_num} ({season})")
    print("="*75)

    dialogue = build_hangover_dialogue(season, week_num)
    print(f"📝 Script ready: {len(dialogue)} hilarious PMT-style dialogue segments!")

    temp_dir = Path(__file__).resolve().parent / "temp_audio_hangover"
    temp_dir.mkdir(exist_ok=True)

    audio_files = []
    print("🎙️ Synthesizing Christopher (Chris) & Brian (Dave) neural voices...")

    for idx, (speaker, raw_text) in enumerate(dialogue):
        clean_text = clean_for_spoken_audio(raw_text)
        voice = VOICE_HOST1 if speaker == 'CHRIS' else VOICE_HOST2
        seg_file = str(temp_dir / f"seg_{idx:03d}_{speaker}.mp3")

        comm = edge_tts.Communicate(clean_text, voice, rate="+3%", pitch="+0Hz")
        await comm.save(seg_file)
        audio_files.append(seg_file)

    master_mp3 = str(Path(__file__).resolve().parent / f"bfl_tuesday_morning_hangover_week_{week_num}_{season}.mp3")
    concat_list = temp_dir / "concat.txt"
    with open(concat_list, 'w') as f:
        for af in audio_files:
            f.write(f"file '{af}'\n")

    print(f"🎵 Stitching master Tuesday Morning Hangover MP3 via ffmpeg -> {master_mp3}...")
    cmd = ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_list), "-c", "copy", master_mp3]
    subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    # Cleanup temp
    for f in temp_dir.glob("*.mp3"):
        try: f.unlink()
        except: pass
    if concat_list.exists(): concat_list.unlink()
    try: temp_dir.rmdir()
    except: pass

    # Duration check
    cmd_dur = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", master_mp3]
    res = subprocess.run(cmd_dur, stdout=subprocess.PIPE, text=True)
    dur_seconds = float(res.stdout.strip())
    mins = int(dur_seconds // 60)
    secs = int(dur_seconds % 60)
    print(f"🎉 Master Hangover Episode Created! Runtime: {mins}m {secs}s -> {master_mp3}")

    if post_to_discord and os.getenv("DISCORD_WEBHOOK_PODCAST"):
        print("🚀 Uploading Tuesday Morning Hangover to Discord #press-room-podcast Forum...")
        thread_title = f"☕ BFL Tuesday Morning Hangover: Week {week_num} ({mins}m {secs}s)"

        with open(master_mp3, 'rb') as f:
            files = {'file': (f"bfl_tuesday_morning_hangover_week_{week_num}_{season}.mp3", f, 'audio/mpeg')}
            data = {
                'username': 'BFL Tuesday Morning Hangover (PMT Desk)',
                'avatar_url': 'https://a.espncdn.com/combiner/i?img=/i/headshots/nfl/players/full/3918298.png',
                'thread_name': thread_title,
                'content': f"☕ **BFL TUESDAY MORNING HANGOVER: WEEK {week_num} OFFICIAL EPISODE ({season})**\n*Chris & Dave drop the hilarious, unfiltered, 8-minute morning commute breakdown: Josh Allen's 39-pt bad beat, Xavier Worthy & A.J. Brown goose eggs, Sydney demolishing Luke-ose, Emelie's bench agony, and midnight press room quotes!*\n\n⏱️ **Duration:** `{mins}m {secs}s`\n🎧 **Listen to the full episode below:** 👇"
            }
            resp = requests.post(os.getenv("DISCORD_WEBHOOK_PODCAST"), data=data, files=files, timeout=45)
            if resp.status_code in [200, 204]:
                print("🎉 SUCCESS! Tuesday Morning Hangover Episode uploaded directly to Discord!")
            else:
                print(f"❌ Discord error: {resp.status_code} - {resp.text}")

    return master_mp3

if __name__ == "__main__":
    asyncio.run(produce_hangover_show(2025, 1))
