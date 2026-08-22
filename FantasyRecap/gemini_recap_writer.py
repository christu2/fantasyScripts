#!/usr/bin/env python3
"""
BFL Gemini AI Recap & Narrative Writer
======================================
Takes the mathematical analysis from league_recap_generator.py and uses
Google Gemini to generate witty, deeply analytical sports commentary:
- Headlines & 3-Act Narrative Recap
- Context-Aware Manager Roasts (with intelligent bench blunder awareness)
- 60-Second Video / Podcast Script
- Discord Webhook payload generator
"""

import os
import sys
import json
import argparse
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from dotenv import load_dotenv

# Load .env
env_path = Path(__file__).resolve().parent.parent / '.env'
if env_path.exists():
    load_dotenv(dotenv_path=env_path)
else:
    load_dotenv()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")

try:
    from google import genai
    HAS_GENAI = True
except ImportError:
    HAS_GENAI = False

def build_gemini_prompt(week_num: int, season: str, matchups: list, team_performances: dict, awards: dict) -> str:
    """Constructs a rich contextual prompt for Gemini with all statistical facts."""
    
    # Format matchup summary
    matchup_text = []
    for m in matchups:
        w_name = m['winner']['owner'] if isinstance(m['winner'], dict) else 'Tie'
        l_name = m['loser']['owner'] if isinstance(m['loser'], dict) else 'Tie'
        w_score = max(m['away_score'], m['home_score'])
        l_score = min(m['away_score'], m['home_score'])
        matchup_text.append(f"- {w_name} ({w_score:.2f} pts) defeated {l_name} ({l_score:.2f} pts) by {m['margin']:.2f} pts.")

    # Format efficiency summary
    eff_text = []
    sorted_eff = sorted(team_performances.values(), key=lambda x: x['efficiency'], reverse=True)
    for p in sorted_eff:
        eff_text.append(f"- {p['team']['owner']}: Actual: {p['score']:.2f}, Optimal: {p['optimal']:.2f}, Efficiency: {p['efficiency']}%, Legitimate Points Left On Bench: {p['bench_lost']:.2f} pts, All-Play: {p['all_play']}")

    # Format actionable bench mistakes
    blunders_text = []
    for tid, perf in team_performances.items():
        for b in perf['blunders'][:2]:
            blunders_text.append(f"- {perf['team']['owner']}: Started {b['started']['name']} ({b['started']['pts']} pts) while leaving {b['benched']['name']} ({b['benched']['pts']} pts) on bench (Cost: {b['diff']} pts).")

    prompt = f"""
You are the commissioner and lead analyst of the Beasts Football League (BFL), a competitive 16-team fantasy football league.
Your tone is witty, authoritative, sharp, and humorous—reminiscent of Scott Van Pelt, Pardon My Take, and Nate Silver.

CRITICAL ANALYTICAL RULES:
1. Bench calculations: True 'Points Left on Bench' is STRICTLY defined as (Optimal Lineup Score - Actual Score).
   Do NOT simply add up raw bench scores, because a roster with 4 backup QBs could never start all 4. Only highlight legally actionable substitution errors.
2. Be authentic to the numbers provided. Do not hallucinate scores or matchups.

LEAGUE DATA FOR WEEK {week_num} ({season}):

MATCHUPS:
{chr(10).join(matchup_text)}

MANAGER IQ & EFFICIENCY (Optimal Lineup %):
{chr(10).join(eff_text)}

NOTABLE START/SIT BLUNDERS:
{chr(10).join(blunders_text) if blunders_text else 'None'}

SUPERLATIVE HIGHLIGHTS:
- Game of the Week: {awards['game_of_week']['winner']['owner'] if awards['game_of_week'] else 'N/A'} vs {awards['game_of_week']['loser']['owner'] if awards['game_of_week'] else 'N/A'} (Margin: {awards['game_of_week']['margin'] if awards['game_of_week'] else 0} pts)
- Beatdown: {awards['blowout_of_week']['winner']['owner'] if awards['blowout_of_week'] else 'N/A'} over {awards['blowout_of_week']['loser']['owner'] if awards['blowout_of_week'] else 'N/A'} (+{awards['blowout_of_week']['margin'] if awards['blowout_of_week'] else 0} pts)
- Tough Luck (Highest Scoring Loser): {awards['tough_luck']['team']['owner'] if awards['tough_luck'] else 'N/A'} ({awards['tough_luck']['score'] if awards['tough_luck'] else 0} pts, All-Play {awards['tough_luck']['all_play'] if awards['tough_luck'] else 'N/A'})
- Lucky Winner (Golden Horseshoe): {awards['lucky_winner']['team']['owner'] if awards['lucky_winner'] else 'N/A'} ({awards['lucky_winner']['score'] if awards['lucky_winner'] else 0} pts)
- Galaxy Brain: {awards['galaxy_brain']['team']['owner'] if awards['galaxy_brain'] else 'N/A'} ({awards['galaxy_brain']['efficiency'] if awards['galaxy_brain'] else 100}% efficiency)
- Bench Mob of the Week: {awards['bench_mob']['team']['owner'] if awards['bench_mob'] else 'N/A'} ({awards['bench_mob']['bench_lost'] if awards['bench_mob'] else 0} actionable pts left on bench)

TASK:
Generate a 3-part publication-ready weekly recap:
1. 📰 **COMMISSIONER'S COLUMN**: An entertaining, narrative breakdown of the week's biggest storylines, game of the week thriller, and heartbreaks.
2. 🎭 **MANAGER ROASTS & SUPERLATIVES**: Personalized, humorous awards breaking down the best managerial masterclasses and worst bench blunders.
3. 🎙️ **60-SECOND PODCAST / VIDEO SCRIPT**: A high-energy teleprompter script for an automated sports anchor video with timestamped visual cues.
"""
    return prompt

def generate_ai_commentary(week_num: int, season: str, matchups: list, team_performances: dict, awards: dict) -> str:
    """Generates the AI narrative using Gemini with fallback."""
    prompt = build_gemini_prompt(week_num, season, matchups, team_performances, awards)
    
    if HAS_GENAI and GEMINI_API_KEY:
        try:
            client = genai.Client(api_key=GEMINI_API_KEY)
            for model_name in ['gemini-2.5-flash', 'gemini-flash-latest', 'gemini-2.5-pro']:
                try:
                    response = client.models.generate_content(
                        model=model_name,
                        contents=prompt
                    )
                    if response and response.text:
                        return response.text.strip()
                except Exception:
                    continue
        except Exception as e:
            print(f"⚠️ Gemini API note: {e}. Using built-in analytics commentary engine.")

    # Built-in Intelligent Fallback Engine
    return fallback_commentary_engine(week_num, season, matchups, team_performances, awards)

def fallback_commentary_engine(week_num: int, season: str, matchups: list, team_performances: dict, awards: dict) -> str:
    """High-quality statistical commentary engine when offline."""
    lines = []
    lines.append(f"## 📰 COMMISSIONER'S COLUMN: WEEK {week_num} IN REVIEW\n")
    
    gow = awards.get('game_of_week')
    blowout = awards.get('blowout_of_week')
    tough_luck = awards.get('tough_luck')
    lucky = awards.get('lucky_winner')
    gb = awards.get('galaxy_brain')
    bm = awards.get('bench_mob')
    
    if gow:
        lines.append(f"Week {week_num} of the {season} BFL campaign delivered instant drama, headlined by a nail-biter where **{gow['winner']['owner']}** edged out **{gow['loser']['owner']}** by a razor-thin margin of **{gow['margin']:.2f} points**. Both managers traded blows all Sunday, but tactical execution proved decisive in the clutch.")
    
    if tough_luck and lucky:
        lines.append(f"\nThe fantasy gods were in full chaotic form: **{tough_luck['team']['owner']}** racked up a monstrous **{tough_luck['score']:.2f} points** (All-Play {tough_luck['all_play']}) only to suffer heartbreak, while **{lucky['team']['owner']}** snuck away with a robbery of a victory with just **{lucky['score']:.2f} points**.")

    lines.append("\n---\n## 🎭 MANAGER ROASTS & SUPERLATIVES\n")
    if gb:
        lines.append(f"* 🧠 **Galaxy Brain Award**: Hat tip to **{gb['team']['owner']}**, posting a clinical **{gb['efficiency']:.1f}% Lineup Efficiency** with zero wasted roster optimization.")
    if bm:
        lines.append(f"* 🤡 **The Bench Meltdown**: **{bm['team']['owner']}** left **{bm['bench_lost']:.2f} legally startable points** on the pine. That is not just bad luck—that is managerial sabotage.")
    if awards.get('worst_blunder'):
        wb = awards['worst_blunder']
        lines.append(f"* 💣 **Blunder of the Week**: **{wb['owner']}** started {wb['started']['name']} ({wb['started']['pts']} pts) while {wb['benched']['name']} posted **{wb['benched']['pts']} pts** on the bench (+{wb['diff']:.1f} pt swing).")

    lines.append("\n---\n## 🎙️ 60-SECOND SPORTSCENTER VIDEO / PODCAST SCRIPT\n")
    lines.append("**[0:00 - 0:10] HOST INTRO:**")
    lines.append(f"\"Welcome back to the BFL Weekly Report! Week {week_num} is in the books, and we saw pure chaos across all 8 matchups!\"")
    lines.append("\n**[0:10 - 0:30] GAME OF THE WEEK & BLOWOUTS:**")
    if gow:
        lines.append(f"\"Matchup of the week goes to {gow['winner']['owner']}, holding off {gow['loser']['owner']} by a mere {gow['margin']:.2f} points! Meanwhile, {blowout['winner']['owner']} put on an absolute clinic.\"")
    lines.append("\n**[0:30 - 0:50] MANAGER IQ ROAST:**")
    if bm:
        lines.append(f"\"Managerial shame of the week goes to {bm['team']['owner']}, who left {bm['bench_lost']:.2f} actionable points on the bench!\"")
    lines.append("\n**[0:50 - 1:00] SIGN OFF:**")
    lines.append("\"Check the standings, set your waiver claims, and we'll see you for Week " + str(week_num + 1) + "!\"")
    
    return "\n".join(lines)

def main():
    parser = argparse.ArgumentParser(description="Generate AI-Powered Fantasy League Recap")
    parser.add_argument("--week", type=int, default=1, help="Week number to recap")
    parser.add_argument("--season", default="2024", help="Season year")
    parser.add_argument("--league-id", default=os.getenv("ESPN_LEAGUE_ID", "157057"), help="ESPN League ID")
    args = parser.parse_args()

    from FantasyRecap.league_recap_generator import fetch_espn_week_data, analyze_week, generate_weekly_awards

    data = fetch_espn_week_data(args.league_id, args.season, args.week, os.getenv("ESPN_S2"), os.getenv("ESPN_SWID"))
    matchups, team_performances, teams = analyze_week(data, args.week)
    awards = generate_weekly_awards(matchups, team_performances)

    print("\n" + "="*75)
    print(f"🤖 GENERATING AI COMMENTARY FOR WEEK {args.week} ({args.season})")
    print("="*75)

    commentary = generate_ai_commentary(args.week, args.season, matchups, team_performances, awards)
    print(commentary)

if __name__ == "__main__":
    main()
