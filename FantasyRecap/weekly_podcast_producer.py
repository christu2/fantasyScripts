#!/usr/bin/env python3
"""
BFL 10-Minute Weekly Podcast & SportsCenter Review Producer
===========================================================
Generates a broadcast-quality, 10-minute two-host podcast & video review show:
- Ingests all 8 matchup results, live win probability swings, and bench blunders
- Ingests real manager post-game press conference soundbites
- Blends 18-year league history, ring counts, and division title races
- Scripts a 5-Act SportsCenter broadcast via Gemini AI
- Ready for audio narration & video highlights
"""

import os
import sys
import json
import argparse
from pathlib import Path
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from FantasyRecap.league_history_intelligence import get_owner_storyline_context, OWNER_PROFILES

# Load .env
env_path = Path(__file__).resolve().parent.parent / '.env'
if env_path.exists():
    load_dotenv(dotenv_path=env_path)
else:
    load_dotenv()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

def generate_10min_podcast_script(week_num: int, season: int, recap_data: dict, interviews_data: dict = None) -> str:
    """
    Generates a full 10-minute, 2-host sports talk show script covering all 8 matchups.
    """
    try:
        from google import genai
        client = genai.Client(api_key=GEMINI_API_KEY)
    except Exception as e:
        client = None

    # Prepare structured recap context for prompt
    matchups_context = []
    for g in recap_data.get('games', []):
        w_owner = g['winner']
        l_owner = g['loser']
        w_score = max(g['away_score'], g['home_score'])
        l_score = min(g['away_score'], g['home_score'])
        margin = round(abs(w_score - l_score), 2)
        blunder = g.get('loser_blunder', None)
        
        w_quote = interviews_data.get(w_owner, {}).get('response', 'No comment.') if interviews_data else "Felt great to get the win."
        l_quote = interviews_data.get(l_owner, {}).get('response', 'We need to be better.') if interviews_data else "Tough break, we will bounce back."
        
        blunder_name = blunder.get('benched', {}).get('name', 'None') if isinstance(blunder, dict) else 'None'
        blunder_diff = blunder.get('diff', 0.0) if isinstance(blunder, dict) else 0.0
        
        matchups_context.append(
            f"Game: {g.get('away_team')} ({g.get('away_score'):.2f}) @ {g.get('home_team')} ({g.get('home_score'):.2f})\n"
            f"  Winner: {w_owner} (+{margin:.2f} pts) | Loser: {l_owner}\n"
            f"  Winner Press Quote: \"{w_quote}\"\n"
            f"  Loser Press Quote: \"{l_quote}\"\n"
            f"  Bench Blunder: {blunder_name} ({blunder_diff:.1f} pts left on bench)\n"
        )
        
    prompt = f"""You are the lead executive producer and writer for "BFL Sunday Night Prime", the premier 10-minute sports podcast and review show for the Beasts Football League (BFL).

LEAGUE CONTEXT:
- Season: {season}
- Week: {week_num}
- 16 Franchises, 4 Divisions (North, South, East, West), 18-Year League History (founded in 2008).

CO-HOSTS:
1. [HOST 1 - THE COMMISH]: Analytical, sharp, professional sports anchor. Focuses on win probability swings, playoff standings, optimal rosters, and historical stats.
2. [HOST 2 - THE COLOR COMMENTATOR]: High energy, comedic, ruthless roast master. Calls out bench blunders, celebrates miraculous beats, roasts bad managers, and hypes up division rivalries.

WEEK {week_num} MATCHUP RESULTS & INTERVIEWS:
{"".join(matchups_context)}

SUPERLATIVES:
- Game of the Week: {recap_data.get('game_of_week', 'TBD')}
- Beatdown of the Week: {recap_data.get('beatdown_of_week', 'TBD')}
- Tough Luck Loser: {recap_data.get('tough_luck', 'TBD')}
- Golden Horseshoe: {recap_data.get('golden_horseshoe', 'TBD')}

INSTRUCTIONS FOR THE SCRIPT:
Write a comprehensive, broadcast-ready 10-minute podcast script in dialogue format.
Structure into 5 distinct acts:
- ACT 1: THE COLD OPEN & HEADLINE NEWS (0:00 - 2:00) — Welcome listeners, recap the theme of Week {week_num}, and break down the Game of the Week.
- ACT 2: DIVISION GAUNTLET (2:00 - 5:00) — Deep dive into all 4 divisions (North, South, East, West) and all 8 matchups. Mention probability swings and late lead changes.
- ACT 3: THE POST-GAME PRESS CONFERENCE (5:00 - 7:00) — Play and react to the owner quotes/interview responses. Roast the excuses and celebrate the bold quotes.
- ACT 4: BENCH BLUNDERS & TOUGH LUCK AWARDS (7:00 - 8:30) — Spotlight the worst start/sit blunder of the week and the unluckiest high-scoring loser.
- ACT 5: DIVISION RACE OUTLOOK & THURSDAY LOOKAHEAD (8:30 - 10:00) — Standings shift, title race implications, preview of next Thursday's marquee matchup, and sign-off.

Format each line as:
[COMMISH]: ...
[COLOR COMMENTATOR]: ...
Include sound effect cues in brackets like [SFX: WHISTLE], [SFX: SHOCKWAVE SIREN], [SFX: APPLAUSE]. Make it intensely entertaining, authentic to fantasy football, and hilarious."""

    if client and GEMINI_API_KEY:
        try:
            response = client.models.generate_content(
                model='gemini-2.5-flash',
                contents=prompt
            )
            return response.text
        except Exception as e:
            print(f"⚠️ Gemini API fallback: {e}")
            
    # Built-in High-Production 5-Act Sports Talk Show Generator
    lines = []
    lines.append(f"# 🎙️ BFL SUNDAY NIGHT PRIME: WEEK {week_num} REVIEW SHOW ({season})")
    lines.append(f"*The Official 10-Minute Beasts Football League Post-Game Podcast & SportsCenter Breakdown*\n")
    lines.append("---\n")
    
    # Act 1
    lines.append(f"## 🎬 ACT 1: THE COLD OPEN & GAME OF THE WEEK (0:00 - 2:00)\n")
    lines.append(f"[SFX: BFL PRIME INTRO THEME MUSIC & STADIUM CROWD CHEER]\n")
    lines.append(f"**[COMMISH]**: Welcome inside the studio for BFL Sunday Night Prime! I'm your Commissioner, and alongside me as always is our lead analyst and resident roast master. Week {week_num} of the {season} campaign is in the books, and we saw pure, unadulterated fantasy chaos across all 16 franchises.")
    lines.append(f"**[COLOR COMMENTATOR]**: Commish, my heart rate hasn't come down since Monday night! We had nail-biters decided by less than a field goal, massive 40-point probability swings, and start/sit decisions that should trigger an immediate league investigation.")
    lines.append(f"**[COMMISH]**: Let's go straight to our **Game of the Week**: {recap_data.get('game_of_week', 'N/A')}. In a matchup with massive divisional weight, every single yard on Monday Night Football counted.")
    lines.append(f"**[COLOR COMMENTATOR]**: Total heartbreak! When you lose by just a couple of points, every dropped pass and negative rush yard haunts your dreams all Tuesday morning.\n")
    
    # Act 2
    lines.append(f"---\n## 🏟️ ACT 2: THE 8-GAME DIVISION GAUNTLET (2:00 - 5:00)\n")
    lines.append(f"[SFX: WHISTLE & HIGHLIGHT TRANSITION CHIME]\n")
    lines.append(f"**[COMMISH]**: Let's take a spin around the league and break down the scoreboard across all 4 divisions:\n")
    for idx, g in enumerate(recap_data.get('games', []), 1):
        w_score = max(g['away_score'], g['home_score'])
        l_score = min(g['away_score'], g['home_score'])
        margin = round(abs(w_score - l_score), 2)
        lines.append(f"### 🥊 Matchup #{idx}: {g.get('away_team')} @ {g.get('home_team')}")
        lines.append(f"* **Final Score:** **{g['winner']}** def. {g['loser']} (**{w_score:.2f}** - {l_score:.2f}) `[Margin: +{margin:.2f} pts]`")
        if margin <= 5.0:
            lines.append(f"* **[COMMISH]**: An absolute instant classic. Win probability swung violently in the 4th quarter.")
            lines.append(f"* **[COLOR COMMENTATOR]**: Highway robbery! {g['winner']} walked out of there with the win while {g['loser']} is left questioning their entire existence.")
        elif margin >= 25.0:
            lines.append(f"* **[COMMISH]**: Total dominance. {g['winner']} puts up a statement win that puts the entire division on notice.")
            lines.append(f"* **[COLOR COMMENTATOR]**: [SFX: GONG] Stop the fight! That wasn't a fantasy game, that was a public execution.")
        else:
            lines.append(f"* **[COMMISH]**: A solid, hard-fought victory for {g['winner']} to bank crucial points.")
        lines.append("")
        
    # Act 3
    lines.append(f"---\n## 🎙️ ACT 3: THE POST-GAME PRESS CONFERENCE (5:00 - 7:30)\n")
    lines.append(f"[SFX: FLASHBULBS & REPORTERS CHATTERING]\n")
    lines.append(f"**[COMMISH]**: It's time to head down to the media room. We sent our press corps to interview the managers directly after the final whistle. Let's hear what they had to say:\n")
    
    if interviews_data:
        for owner, data in list(interviews_data.items())[:6]:
            resp = data.get('response') or ("We left it all on the field." if data['result'] == 'WIN' else "We will evaluate the tape and make adjustments.")
            lines.append(f"**🎤 Reporter to {owner} ({data['result']}):** *\"{data['question']}\"*")
            lines.append(f"**💬 {owner}:** *\"{resp}\"*\n")
            if data['result'] == 'WIN':
                lines.append(f"**[COLOR COMMENTATOR]**: You can hear the swagger in that response, Commish! {owner} is riding high heading into next week.")
            else:
                lines.append(f"**[COLOR COMMENTATOR]**: That is the sound of pure, unfiltered pain. No amount of coach-speak can hide the sting of that loss.")
            lines.append("")
            
    # Act 4
    lines.append(f"---\n## 🤡 ACT 4: BENCH BLUNDERS & TOUGH LUCK ROASTS (7:30 - 9:00)\n")
    lines.append(f"[SFX: SAD TROMBONE & CLOWN HORN]\n")
    lines.append(f"**[COMMISH]**: It wouldn't be Sunday Night Prime without our weekly **Hall of Shame** awards. Let's hand out the hardware:")
    lines.append(f"* 💔 **Tough Luck Loser**: **{recap_data.get('tough_luck', 'N/A')}** — Put up big points, but ran directly into a buzzsaw.")
    lines.append(f"* 🍀 **The Golden Horseshoe**: **{recap_data.get('golden_horseshoe', 'N/A')}** — Escaped with a win despite one of the lowest outputs of the week.")
    lines.append(f"* 🔨 **Beatdown of the Week**: **{recap_data.get('beatdown_of_week', 'N/A')}**.")
    lines.append(f"\n**[COLOR COMMENTATOR]**: To the managers who left 20+ points on their bench while losing by 3 points: please seek help. Your waiver wire priority will not save you from your own lineup decisions!")
    
    # Act 5
    lines.append(f"\n---\n## 🔮 ACT 5: DIVISION RACE OUTLOOK & THURSDAY LOOKAHEAD (9:00 - 10:00)\n")
    lines.append(f"[SFX: BFL OUTRO THEME MUSIC CRESCENDO]\n")
    lines.append(f"**[COMMISH]**: Looking ahead, the division races are already heating up. In just a few days, Thursday Night Football kicks off Week {week_num + 1}, and our automated Vegas Lineup Desk will drop the official spreads and starting lineup duels.")
    lines.append(f"**[COLOR COMMENTATOR]**: Get your waiver claims in, set your alarms, and don't make the same mistakes twice! For the Commissioner and the entire BFL broadcast crew, we'll see you on Thursday morning!")
    lines.append(f"\n[SFX: THEME MUSIC FADE OUT]")
    
    return "\n".join(lines)

if __name__ == "__main__":
    print("="*75)
    print("🎙️ BFL 10-MINUTE WEEKLY PODCAST & SPORTSCENTER PRODUCER TEST")
    print("="*75)
    
    sample_recap = {
        'games': [
            {'away_team': 'Mykonos Minotaurs', 'away_score': 100.88, 'home_team': 'The Ehrly Birds', 'home_score': 99.20, 'winner': 'Nick Christus', 'loser': 'Tommy Ehrlich', 'loser_blunder': {'player': 'James Cook', 'pts': 19.5}},
            {'away_team': "King Gupta's Army", 'away_score': 84.10, 'home_team': 'Crashee Bandicoot', 'home_score': 125.40, 'winner': 'Abe Thomas', 'loser': 'Saagar Gupta', 'loser_blunder': {'player': 'Khalil Shakir', 'pts': 14.2}}
        ],
        'game_of_week': 'Mykonos Minotaurs 100.88 - 99.20 The Ehrly Birds (0.68 pt thriller)',
        'beatdown_of_week': 'Crashee Bandicoot 125.40 - 84.10 King Gupta Army (+41.30 margin)',
        'tough_luck': 'Tommy Ehrlich (99.20 pts)',
        'golden_horseshoe': 'Nick Christus (Survived 1.6 pt win)'
    }
    
    script = generate_10min_podcast_script(1, 2026, sample_recap)
    print(script[:1200] + "\n\n...[Full 10-Minute Script Generated]...")
