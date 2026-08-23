# 🏈 Beasts Football League (BFL) Recap & Broadcast Suite

The complete automated intelligence, broadcast, and multimedia production suite for the 16-team Beasts Football League.

---

## 📡 Discord Channel Architecture

| Channel | Format | What Gets Broadcasted |
|:---|:---:|:---|
| **`#commissioner-desk`** | Text | • Annual Preseason Preview & 18-Year Trophy Room<br>• Thursday Starting Lineup Duels & Simulated Vegas Spreads<br>• Weekly Playoff Probability & Draft Stakes Board |
| **`#live-game-desk`** | Text | • In-Game $\ge 25\%$ Win Probability Shockwave Alerts<br>• Sunday Night Entering-MNF Clinch & Panic Boards |
| **`#press-room-podcast`** | **Forum** | • Dedicated Weekly Episode Forum Threads<br>• Tuesday Morning Hangover Audio Podcasts & Video Reels<br>• Post-Game Manager Press Conferences |

---

## 🗓️ Master Weekly Broadcast Schedule

```
MONDAY NIGHT (~11:30 PM) ➡️ Commish Bot DMs all 16 managers for post-game reactions
TUESDAY MORNING (8:00 AM) ➡️ Tuesday Morning Hangover Podcast / Video drops in #press-room-podcast
TUESDAY MORNING (9:00 AM) ➡️ 10,000-Run Monte Carlo Playoff & Draft Board drops in #commissioner-desk
THURSDAY MORNING (10:00 AM) ➡️ Thursday Lineup Preview & Vegas Spreads drops in #commissioner-desk
SUNDAY / MONDAY LIVE ➡️ Live Win Probability Shockwaves (≥25% swings) drop in #live-game-desk
```

---

## 🕹️ Core Modules & Command Line Usage

### 1. ☕ Tuesday Morning Hangover (`tuesday_morning_hangover.py`)
Generates the 8-to-10 minute PMT-style podcast review using **Kokoro-TTS** neural voices (`am_michael` & `am_adam`), ingests real `#trash-talk` reactions, analyzes player stat lines/blunders, and uploads directly to `#press-room-podcast`.
```bash
python3 FantasyRecap/tuesday_morning_hangover.py
```

### 2. 🎲 10,000-Run Playoff & Draft Simulator (`playoff_odds_simulator.py`)
Simulates 10,000 season outcomes using actual remaining schedule, current division standings, official BFL tiebreakers (Record $\to$ H2H $\to$ PF), NFL dynamic re-seeding, 3rd place cash payouts, and draft choice odds.
```bash
python3 FantasyRecap/playoff_odds_simulator.py
```

### 3. 🎰 Thursday Lineup Preview & Vegas Spreads (`thursday_lineup_preview.py`)
Simulates spread, moneyline, over/under, and starting lineup positional battles for all 8 matchups.
```bash
python3 FantasyRecap/thursday_lineup_preview.py --week 1 --season 2026
```

### 4. ⚡ Live Win Probability Shockwave Monitor (`live_win_probability_monitor.py`)
Tracks live in-game scoring against pre-game projection baselines, firing shockwave alerts when win probabilities swing $\ge 25\%$ or upon 4th-quarter lead changes.
```bash
python3 FantasyRecap/live_win_probability_monitor.py
```

### 5. 👑 Annual Season Preview (`annual_season_preview.py`)
Comprehensive 18-year trophy room leaderboard, 4 division deep dives, favorites, dark horses, and marquee rivalry matchups.
```bash
python3 FantasyRecap/annual_season_preview.py --season 2026
```

---

## ⚙️ Environment Variables (`.env`)

```env
LEAGUE_ID=1010041
ESPN_S2="your_espn_s2"
SWID="{your-swid-guid}"
DISCORD_WEBHOOK_COMMISH="https://discord.com/api/webhooks/..."
DISCORD_WEBHOOK_LIVE="https://discord.com/api/webhooks/..."
DISCORD_WEBHOOK_PODCAST="https://discord.com/api/webhooks/..."
GEMINI_API_KEY="your_gemini_key"
```
