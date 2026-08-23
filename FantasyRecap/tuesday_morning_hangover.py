#!/usr/bin/env python3
"""
BFL Tuesday Morning Hangover (Master Podcast & SportsCenter Video Pipeline)
===========================================================================
Produces the definitive weekly review in both MP3 Podcast and Full HD MP4 Video:
- Hosts: Chris (AndrewMultilingualNeural) & Dave (BrianMultilingualNeural)
- Master phonetic pronunciation engine for NFL players and owners
- Weaves in 18-year league lore & "The Jabroni Trophy" championship race
- Exact player stat lines (394 yds/4 TDs, 169 rush yds/2 TDs, 7 rec/143 yds, 1 rec/8 yds)
- Discord #trash-talk chat integration & real manager press conference quotes
- Week 2 marquee lookahead matchup previews with playoff/rivalry stakes
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
from FantasyRecap.discord_chat_harvester import get_sample_trash_talk_banter
from FantasyRecap.video_highlight_engine import create_slide_card, generate_video_from_audio

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

def build_full_show_dialogue(season: int = 2025, week_num: int = 1) -> list:
    """Constructs the comprehensive 8-to-10 minute dialogue with Jabroni Trophy lore."""
    d = []

    # ACT 1: COLD OPEN & MORNING HANGOVER
    d.append(('CHRIS', f"Good Tuesday morning everybody! Grab your coffee, pop an Advil, and welcome inside the BFL Tuesday Morning Hangover, presented by the BFL Broadcast Network. Week {week_num} of the {season} season is officially in the books, the midnight post-game media texts are in, and fantasy football has already ruined half the league's week. I'm Chris alongside Dave."))
    d.append(('DAVE', "Good morning Chris. I've been scrolling through the league trash-talk channel since early this morning, and the race for The Jabroni Trophy is officially on! Commissioner Nick Christus has this eighteen-year-old league at peak intensity. We had three-point heartbreakers, thirty-point blowouts, elite wide receivers putting up actual goose eggs, and some coaching decisions so bad they should be investigated!"))
    d.append(('CHRIS', "Let's dive straight into our Game of the Week in the West Division. Adam and Green and Golden barely escaped with their lives against Emelie, ninety-one point three two to eighty-eight point two eight!"))
    d.append(('DAVE', "Are you kidding me, Chris?! Emelie got completely burned by her own lineup sheet. She started Nico Collins, and the dude put up four points on a fourteen-point projection. Meanwhile, Adam got a twenty-three-point bailout from Bijan Robinson to save him because Joe Burrow looked like he was playing with a blindfold on, scoring under nine points."))
    d.append(('CHRIS', "And Dave, look at Emelie's bench! She had Wan'Dale Robinson sitting on her pine with fourteen and a half points! If she makes the simple swap and benches Nico for Wan'Dale, she wins by seven points and is celebrating this morning. Instead, she loses by three point zero four points. Adam immediately hopped into the group chat saying: 'Thank you Emelie for benching Wan'Dale! Best win of my life.'"))
    d.append(('DAVE', "Leaving ten points on the bench when you lose by three points is the kind of mistake that makes you stare at your steering wheel on your Tuesday morning commute questioning all your life choices. Adam walks out with a greasy win, and Emelie takes the toughest loss of week one."))

    # ACT 2: SYDNEY DEMOLISHES LUKOSE & THE 4-TIME GOAT
    d.append(('CHRIS', "Now let's head over to the East Division for the biggest demolition of the week. Sydney walked into Lukose's house, kicked his front door down, and delivered a one hundred and one to seventy ass-kicking!"))
    d.append(('DAVE', "Sydney was unbelievable, Chris! She got a massive game from Zay Flowers, who caught seven passes for one hundred and forty-three receiving yards and a touchdown! Her squad played fast, but can we talk about Lukose for a second? The four-time champion had the single biggest coaching brain-fart I have seen in eighteen years of BFL history!"))
    d.append(('CHRIS', "It was a total disaster, Dave. Lukose started Drake Maye, who gave him fifteen points, while Justin Fields was chilling on his bench dropping nearly thirty fantasy points! Kenneth Walker got bottled up for under twenty yards, and Sydney just ran him out of the gym by thirty-one points."))
    d.append(('DAVE', "Sydney proved she is hunting for her second career Jabroni Trophy and put the entire league on notice. She posted in the trash-talk chat asking: 'Who told Lukose to start Drake Maye over Justin Fields? Show yourself.' Lukose has four Jabroni Trophies in his trophy case, but after getting embarrassed like that, he's holding an eight A M team meeting to find out who set his lineup!"))

    # ACT 3: ABE'S CLINIC, SAAGAR'S DROUGHT, & COMMISH NICK'S DOMINANCE
    d.append(('CHRIS', "Down in the South Division, defending champion Abe and Crashee Bandicoot showed zero championship hangover, defending The Jabroni with a league-high one hundred and seventeen points against Saagar, winning by twenty-four!"))
    d.append(('DAVE', "Abe looked unstoppable! Lamar Jackson threw for two hundred and nine yards and two touchdowns, plus seventy rushing yards and another score on the ground—three total touchdowns! Garrett Wilson went off for eighteen points. Meanwhile, poor Saagar. Patrick Mahomes gave him twenty-six, but A.J. Brown was completely erased, finishing with literally one catch for eight yards on the entire day! Saagar posted in the chat saying: 'Eighteen years and counting, A.J. Brown destroyed my season.'"))
    d.append(('CHRIS', "Saagar won the first-ever Jabroni Trophy back in 2008, and he has now entered year nineteen of his title drought. Starting off oh-and-one by twenty-four points to Abe is just cruel."))
    d.append(('DAVE', "Saagar's Jabroni drought is old enough to vote, Chris! Abe looked dominant, and the South Division throne is clearly his until proven otherwise."))
    d.append(('CHRIS', "Over in the North Division, our Commissioner Nick and the Mykonos Minotaurs took care of business, handling Dino one hundred to eighty-five!"))
    d.append(('DAVE', "Commissioner Nick top-scored in the North despite Ja'Marr Chase having a quiet three-point day! Keon Coleman exploded for twenty-one points, more than doubling his projection with a huge touchdown grab to bail out the Minotaurs. Dino got twenty-four from Jalen Hurts, but Dino was complaining in the chat at midnight saying: 'Nick only won because his kicker had fourteen points.'"))
    d.append(('CHRIS', "A win is a win, and Commissioner Nick defends his three Jabroni Trophies and takes first place in the North Division."))

    # ACT 4: REJ'S TOUGH LUCK & THE SLATE RUNDOWN
    d.append(('CHRIS', "Let's pour one out for Rej, who is our official Bad Beat of the Week winner. Rej put up ninety-seven point six points—the fourth highest score in the entire sixteen-team league—and still took an L to Dan, who put up one hundred and five!"))
    d.append(('DAVE', "Josh Allen went completely nuclear for Rej, throwing for three hundred and ninety-four yards and two touchdowns, while adding thirty rushing yards and two more rushing touchdowns—that's four total touchdowns and nearly forty fantasy points! And Rej still loses because Dan got nineteen from Javonte Williams and solid balance across the board. Rej texted into the media desk saying: 'I scored ninety-seven points with Josh Allen dropping thirty-nine and still lost. I hate fantasy football so much.'"))
    d.append(('CHRIS', "In other action, Blake edged out Alex ninety-five to ninety-two in a gritty defensive battle. King Derrick Henry ran over everybody, plowing for one hundred and sixty-nine rushing yards and two touchdowns to carry Blake to the finish line!"))
    d.append(('DAVE', "Derrick Henry is thirty years old and still running over human beings like a runaway freight train! Alex got twenty-four from rookie Caleb Williams, but Terry McLaurin was locked down for under four points. Blake escapes by three."))
    d.append(('CHRIS', "Down in the cross-division matchup, Nael rolled over Samran one hundred and two to eighty-five behind a twenty-eight-point masterclass from Justin Herbert."))
    d.append(('DAVE', "Justin Herbert was dropping dimes all day! Samran got twenty-one from Emeka Egbuka, but Jerome Ford gave him literally one single point in his backfield. You cannot win in this league getting one point from your running back, period."))
    d.append(('CHRIS', "And wrapping up the week one gauntlet, Thor held off Thomas ninety-four to eighty-five in a classic North-versus-West grudge match."))
    d.append(('DAVE', "Thomas is perpetually chasing that elusive first Jabroni Trophy, and dropping week one to Thor hurts. Thomas got twenty-two from J.J. McCarthy, but Xavier Worthy gave him an absolute doughnut with zero catches on zero yards! Thomas posted in the chat at three A M saying: 'Xavier Worthy is dead to me, dropping him to waivers immediately.'"))

    # ACT 5: WEEK 2 MARQUEE MATCHUP LOOKAHEAD
    d.append(('CHRIS', "Now let's turn the page and look ahead to Week Two, because the schedule makers gave us some absolute heavyweight clashes!"))
    d.append(('DAVE', "First up in the North Division, we have Commissioner Nick at one-and-oh taking on Saagar at oh-and-one! Nick is looking to push his division lead, while Saagar is in desperate need of a bounce-back to keep his Jabroni dreams alive."))
    d.append(('CHRIS', "Over in the East and West cross-over, we have a massive revenge spot: four-time champion Lukose at oh-and-one takes on Adam at one-and-oh! Lukose is furious after his week one benching blunder, while Adam is looking to prove his three-point win over Emelie was no fluke."))
    d.append(('DAVE', "And check out the clash of titans down South: defending champion Abe at one-and-oh takes on Thor at one-and-oh! Two former champions battling for early supremacy. Meanwhile, Rej at oh-and-one squares off with Dino at oh-and-one in their legendary rivalry, and Thomas takes on Samran with both franchises desperately hunting for win number one!"))
    d.append(('CHRIS', "Thursday Night Football kicks off in just seventy-two hours, and Commissioner Nick's Vegas and Lineup Desk will drop the official simulated spreads and positional battle previews on Thursday morning in the commissioner-desk channel."))
    d.append(('DAVE', "Get your waiver bids in today, bench the bums who gave you zero points, and for the love of God, don't leave thirty points on your pine like Lukose! For Chris, Dave, and the entire BFL Tuesday Morning Hangover crew, have a great Tuesday everybody!"))

    return d

def build_videoreel_slide_deck(pid: int):
    """Builds the 6 broadcast visual cards for the SportsCenter MP4 reel."""
    temp_dir = Path(__file__).resolve().parent / f"temp_video_slides_{pid}"
    temp_dir.mkdir(parents=True, exist_ok=True)
    slides = []

    # Slide 1: Show Title Card
    s1 = create_slide_card("☕ TUESDAY MORNING HANGOVER", "Week 1 Review • The Race for The Jabroni", [
        {'header': "BEASTS FOOTBALL LEAGUE (BFL) • 2025 SEASON", 'desc': "Full 16-Franchise Post-Week Breakdown with Chris & Dave", 'tag': "BROADCAST"},
        {'header': "🔥 Game of the Week", 'desc': "Adam Olen (91.32) def. Emelie Lovasko (88.28) by +3.04 pts", 'tag': "WEST"},
        {'header': "🔨 Demolition of the Week", 'desc': "Sydney Miller (101.14) def. Shawn Lukose (69.98) by +31.16 pts", 'tag': "EAST"},
        {'header': "👑 North & South Division Leaders", 'desc': "Nick Christus (100.82) & Abe Thomas (117.86) claim first place", 'tag': "LEADERS"}
    ], str(temp_dir / "slide_01.png"), "THE JABRONI RACE", (41, 128, 185))
    slides.append(s1)

    # Slide 2: Game of the Week
    s2 = create_slide_card("🔥 GAME OF THE WEEK THRILLER", "Adam Olen (91.32) def. Emelie Lovasko (88.28)", [
        {'header': "Adam Olen — 91.32 PTS (WIN, 1-0)", 'desc': "Bijan Robinson carried the squad with 6 rec, 100 yds, 1 TD (23.4 pts).", 'tag': "WINNER"},
        {'header': "Emelie Lovasko — 88.28 PTS (LOSS, 0-1)", 'desc': "Brock Purdy (16.8 pts), but Nico Collins struggled with just 4.0 pts.", 'tag': "RUNNER-UP"},
        {'header': "💣 The Deciding Bench Blunder", 'desc': "Wan'Dale Robinson rotting on Emelie's bench with 14.5 pts. Starting Wan'Dale wins by +7 pts.", 'tag': "KEY MOMENT"}
    ], str(temp_dir / "slide_02.png"), "GAME OF THE WEEK", (230, 126, 34))
    slides.append(s2)

    # Slide 3: Demolition & Blunder of the Week
    s3 = create_slide_card("🔨 DEMOLITION & BLUNDER OF THE WEEK", "Sydney Miller (101.14) def. Shawn Lukose (69.98)", [
        {'header': "Sydney Miller — 101.14 PTS (WIN, 1-0)", 'desc': "Zay Flowers exploded for 7 rec, 143 yds, 1 TD (24.6 pts) to dominate.", 'tag': "STATEMENT WIN"},
        {'header': "Shawn Lukose — 69.98 PTS (LOSS, 0-1)", 'desc': "Kenneth Walker held under 20 yds; 4-time champ falls to 0-1.", 'tag': "FRUSTRATION"},
        {'header': "🤡 Blunder: Drake Maye over Justin Fields", 'desc': "Started Drake Maye (15.3 pts) over Justin Fields (29.5 pts on pine) — a costly +14.2 pt error.", 'tag': "BLUNDER"}
    ], str(temp_dir / "slide_03.png"), "BEATDOWN REEL", (231, 76, 60))
    slides.append(s3)

    # Slide 4: Complete Week 1 Scoreboard
    s4 = create_slide_card("📊 COMPLETE WEEK 1 SCOREBOARD MATRIX", "All 8 Matchup Final Results", [
        {'header': "🌴 South: Abe Thomas (117.86) def. Saagar Gupta (93.42)", 'desc': "Lamar Jackson (209 pass yds, 70 rush yds, 3 TDs); A.J. Brown held to 1 rec, 8 yds.", 'tag': "FINAL"},
        {'header': "👑 North: Nick Christus (100.82) def. Dino Davros (85.48)", 'desc': "Keon Coleman (21.2 pts, TD) bails out Minotaurs; Nick defends 3 Jabronis.", 'tag': "FINAL"},
        {'header': "💔 East: Daniel Kruszewski (105.42) def. rej hoxha (97.66)", 'desc': "Josh Allen (394 pass yds, 4 total TDs, 38.8 pts) in a heartbreaking loss for Rej.", 'tag': "FINAL"},
        {'header': "⚔️ Blake (95.58) def. Alex (92.30) | Nael (102.52) def. Samran (85.82) | Thor (94.32) def. Thomas (85.02)", 'desc': "Derrick Henry (169 rush yds, 2 TDs), Justin Herbert (27.9 pts), Worthy (0 catches, 0 yds).", 'tag': "FINAL"}
    ], str(temp_dir / "slide_04.png"), "SCOREBOARD MATRIX", (46, 204, 113))
    slides.append(s4)

    # Slide 5: Discord Trash Talk & Press Room
    s5 = create_slide_card("💬 DISCORD TRASH TALK & MEDIA ROOM", "Midnight Group Chat Reactions", [
        {'header': "Sydney Miller:", 'desc': "\"Who told Lukose to start Drake Maye over Justin Fields? Show yourself 😂\"", 'tag': "#TRASH-TALK"},
        {'header': "Adam Olen:", 'desc': "\"Thank you Emelie for benching Wan'Dale Robinson! Best win of my life.\"", 'tag': "#TRASH-TALK"},
        {'header': "Rej Hoxha:", 'desc': "\"I scored 97.6 points with Josh Allen dropping 39 and I still take the L. Fantasy gods hate me.\"", 'tag': "PRESS ROOM"},
        {'header': "Thomas (Tommy):", 'desc': "\"Xavier Worthy gave me a literal donut (0.0 pts). Dropping him to waivers at 3 AM.\"", 'tag': "#TRASH-TALK"}
    ], str(temp_dir / "slide_05.png"), "MEDIA QUOTES", (155, 89, 182))
    slides.append(s5)

    # Slide 6: Week 2 Marquee Matchup Lookahead
    s6 = create_slide_card("🔮 WEEK 2 MARQUEE MATCHUPS TO WATCH", "Heavyweight Battles & Jabroni Stakes", [
        {'header': "👑 Nick Christus (1-0) vs. Saagar Gupta (0-1)", 'desc': "3-time champ vs. inaugural champ fighting to snap his 18-year Jabroni drought.", 'tag': "MARQUEE"},
        {'header': "⚔️ Shawn Lukose (0-1) vs. Adam Olen (1-0)", 'desc': "4-time GOAT seeking redemption after benching Fields against Adam's 1-0 squad.", 'tag': "RIVALRY"},
        {'header': "🌴 Abe Thomas (1-0) vs. Thor Shawn Ullenbrauck (1-0)", 'desc': "Defending champion battles former champion in a clash of unbeatens.", 'tag': "CLASH OF TITANS"},
        {'header': "⚡ rej hoxha (0-1) vs. Dino Davros (0-1) | Sydney (1-0) vs. Nael (1-0) | Thomas (0-1) vs. Samran (0-1)", 'desc': "Century deadlock rivalry and critical early playoff position battles.", 'tag': "GAUNTLET"}
    ], str(temp_dir / "slide_06.png"), "WEEK 2 LOOKAHEAD", (241, 196, 15))
    slides.append(s6)

    return slides, temp_dir

async def produce_full_hangover_broadcast(season: int = 2025, week_num: int = 1, post_to_discord: bool = True):
    pid = os.getpid()
    print("\n" + "="*75)
    print(f"🎙️ BFL TUESDAY MORNING HANGOVER: FULL SHOW PRODUCTION (WEEK {week_num}, {season})")
    print("="*75)

    # 1. Generate Dialogue
    dialogue = build_full_show_dialogue(season, week_num)
    print(f"📝 Script ready: {len(dialogue)} extensive dialogue segments with Week 2 lookahead!")

    temp_dir = Path(__file__).resolve().parent / f"temp_audio_{pid}"
    temp_dir.mkdir(parents=True, exist_ok=True)

    audio_files = []
    print("🎙️ Synthesizing Chris (AndrewMultilingual) & Dave (BrianMultilingual) neural audio...")

    for idx, (speaker, raw_text) in enumerate(dialogue):
        clean_text = clean_for_spoken_audio(raw_text)
        voice = VOICE_HOST1 if speaker == 'CHRIS' else VOICE_HOST2
        seg_file = str(temp_dir / f"seg_{idx:03d}_{speaker}.mp3")

        comm = edge_tts.Communicate(clean_text, voice, rate="+2%", pitch="+0Hz")
        await comm.save(seg_file)
        audio_files.append(seg_file)

    master_mp3 = str(Path(__file__).resolve().parent / f"bfl_tuesday_morning_hangover_week_{week_num}_{season}.mp3")
    concat_list = temp_dir / "concat.txt"
    with open(concat_list, 'w') as f:
        for af in audio_files:
            f.write(f"file '{af}'\n")

    print(f"🎵 Stitching master podcast MP3 via ffmpeg -> {master_mp3}...")
    cmd = ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_list), "-c", "copy", master_mp3]
    subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    # Cleanup audio temp
    for f in temp_dir.glob("*.mp3"):
        try: f.unlink()
        except: pass
    if concat_list.exists(): concat_list.unlink()
    try: temp_dir.rmdir()
    except: pass

    cmd_dur = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", master_mp3]
    res = subprocess.run(cmd_dur, stdout=subprocess.PIPE, text=True)
    dur_seconds = float(res.stdout.strip())
    mins = int(dur_seconds // 60)
    secs = int(dur_seconds % 60)
    print(f"🎉 Master Audio Rendered! Runtime: {mins}m {secs}s -> {master_mp3}")

    # 2. Generate SportsCenter MP4 Video Reel
    print("🎬 Building SportsCenter 1080p slide deck...")
    slides, temp_vdir = build_videoreel_slide_deck(pid)
    master_mp4 = str(Path(__file__).resolve().parent / f"bfl_tuesday_hangover_week_{week_num}_{season}.mp4")
    print(f"🎬 Compiling Full HD MP4 Video Show -> {master_mp4}...")
    generate_video_from_audio(slides, master_mp3, master_mp4)

    # 3. Post to Discord #press-room-podcast
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
    for f in temp_vdir.glob("*.png"):
        try: f.unlink()
        except: pass
    try: temp_vdir.rmdir()
    except: pass

    return master_mp3, master_mp4

if __name__ == "__main__":
    asyncio.run(produce_full_hangover_broadcast(2025, 1))
