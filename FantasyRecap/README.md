# 🏈 Beasts Football League (BFL) Broadcast & Analytics Suite

The automated intelligence, NotebookLM podcast payload, and live Discord engagement suite for the 16-team Beasts Football League.

---

## 📡 Discord Channel Architecture

| Channel | Format | What Gets Broadcasted |
|:---|:---:|:---|
| **`#commissioner-desk`** | Text | • Annual Preseason Preview & 18-Year Trophy Room<br>• Tuesday Playoff Odds, Win/Loss Leverage & Draft Stakes Board<br>• Thursday Starting Lineup Duels, Vegas Spreads & Interactive "Upset of the Week" Poll |
| **`#live-game-desk`** | Text | • Live Win Probability Shockwaves ($\ge 25\%$ swings attributed to exact player TDs)<br>• 4th Quarter Lead Changes, Nuclear Player Alerts ($\ge 28$ pts) & Live In-Game Injury Sirens<br>• Sunday Night "Entering MNF" Miracle & Clinch Board |
| **`#press-room-podcast`** | **Forum** | • Weekly Episode Forum Threads (`publish_notebook_audio.py` / `publish_human_podcast.py`)<br>• Monday Night Post-Game Manager Press Conference Calls |
| **`#bot-ops`** *(optional)* | Text | • Private failure & ESPN API health alerts (`DISCORD_WEBHOOK_OPS` in `.env`) |

---

## 🏗️ Core Architecture (`league_manifest.json` & `core/`)
- **[`league_manifest.json`](league_manifest.json)**: Single canonical source of truth for league ID, divisions, all 16 teams/owners, Discord IDs/usernames, mention triggers, phonetic guide, historical synonyms, and championship roll.
- **[`core/espn_client.py`](core/espn_client.py)**: Pooled HTTP client with retry + exponential backoff raising `ESPNClientError` instead of terminating live loops.
- **[`core/ops.py`](core/ops.py)**: Atomic macOS/Linux `fcntl.flock` process locking (`ProcessLock`) and `#bot-ops` Discord alerting (`notify_ops`).
- **SQLite Fast-Path H2H**: `compile_all_time_h2h()` reads 2008–present history directly from `live_state/bfl_history.db` (<10ms) instead of 18+ ESPN HTTP calls.

---

## 🗓️ Weekly Broadcast Workflow

### 1. ☕ Tuesday Morning Hangover Podcast (NotebookLM Workflow)
1. **Generate the Markdown Payload & Season Chronicle** (outputs to `FantasyRecap/output/`):
   ```bash
   python3 FantasyRecap/generate_notebook_payload.py --season 2026 --week 6
   ```
   *Includes: 15-70-15 3-Act Pacing Guide, SportsCenter Cold Open directive, Phonetics & League Nicknames dictionary (`Sah-gur`, `Ray`, `Luke-ose`, `Thor`, `AMO`), Live 10,000-Sim Playoff Odds & Leverage, In-Game Injury protection, 2026 NFL player tenure tags, Rotowire player news, in-depth trade motives, and real Discord quotes.*
2. **Upload both files to Google NotebookLM** (`output/notebook_payload_week_X_2026.md` + `output/season_chronicle_weeks_1_to_X_2026.md`) and download the generated audio overview.
3. **Compress & Publish to Discord `#press-room-podcast`** (optionally also generating a 128 kbps Apple Podcasts / Spotify Private RSS feed in `output/rss/`):
   ```bash
   python3 FantasyRecap/publish_notebook_audio.py --season 2026 --week 6 --audio /path/to/downloaded_audio.mp4 --notes "Optional episode notes" --rss
   ```
   *(Set `PODCAST_RSS_BASE_URL` in `.env` for your Cloudflare R2 / GitHub Pages URL, or use `publish_human_podcast.py` for human-recorded episodes.)*

#### ⚡ Automated Alternative: Gemini Native Multi-Speaker TTS (`gemini_podcast_generator.py`)
Generates Chris & Dave dialogue and renders expressive 2-host audio directly from `output/notebook_payload_week_{W}_{Y}.md`—no manual NotebookLM upload/download:
```bash
# 2–3 minute highlight clip to audition voice quality vs. NotebookLM
python3 FantasyRecap/gemini_podcast_generator.py --season 2026 --week 10 --sample

# Full 15–22 minute episode + auto-update Apple Podcasts Private RSS
python3 FantasyRecap/gemini_podcast_generator.py --season 2026 --week 10 --full --rss
```

---

### 2. 🎲 Tuesday Playoff Odds, Win/Loss Leverage & Draft Stakes (`playoff_odds_simulator.py`)
Runs 10,000 Monte Carlo simulations using empirical 2026 team scoring averages/std-dev, remaining schedule, official BFL tiebreakers (Record $\to$ H2H $\to$ PF), NFL dynamic re-seeding, Top 3 payouts, and **Win/Loss Playoff Leverage swings** for the upcoming week:
```bash
# Print report & save to output/
python3 FantasyRecap/playoff_odds_simulator.py --season 2026 --week 6

# Also broadcast embed to #commissioner-desk
python3 FantasyRecap/playoff_odds_simulator.py --season 2026 --week 6 --discord
```

---

### 3. 🎰 Thursday Lineup Preview, Vegas Spreads & Upset Poll (`thursday_lineup_preview.py`)
Computes simulated Vegas spreads, over/unders, positional corps advantages, 18-year lifetime H2H context, and posts an interactive Discord **Upset of the Week Poll**:
```bash
# Preview only (saves to output/)
python3 FantasyRecap/thursday_lineup_preview.py --season 2026 --week 7

# Broadcast embed + native Upset Poll to #commissioner-desk
python3 FantasyRecap/thursday_lineup_preview.py --season 2026 --week 7 --discord
```

---

### 4. ⚡ Live Game Desk Daemon & Sunday Night MNF Miracle Board (`live_game_desk_daemon.py`)
Monitors live ESPN boxscores during TNF, Sunday slates, and MNF. Fires Discord embeds for $\ge 25\%$ win probability shockwaves (attributing the exact player who scored), lead changes, Nuclear Player eruptions ($\ge 28$ pts), and live in-game starter injury sirens:
```bash
# Continuous live polling during game windows (every 60s)
./FantasyRecap/run_live_game_desk.sh 2026 7 60

# Sunday Night: Post the "Entering MNF" Miracle & Clinch Board to #live-game-desk
python3 FantasyRecap/live_game_desk_daemon.py --season 2026 --week 7 --mnf-board
```

---

### 5. 🎙️ Monday Night Press Conference & Weekly Power Recap
* **Post Monday Night Media Availability Call to Discord:**
  ```bash
  python3 FantasyRecap/press_conference_manager.py
  ```
* **Generate Standalone Markdown Power Rankings & Manager IQ Report:**
  ```bash
  python3 FantasyRecap/league_recap_generator.py --season 2026 --week 6
  ```
* **Annual Preseason State of the League & 18-Year Trophy Room:**
  ```bash
  python3 FantasyRecap/annual_season_preview.py --season 2026
  ```

---

### 6. 📚 Discord `/ask` Historian Bot & Test Suite
* **Discord `/ask` & `/bflstats` Historian Bot** (Gemini Text-to-SQL over `live_state/bfl_history.db` — 2008–present games & 2018–present player boxscores):
  ```bash
  # Install dependency once
  pip install discord.py

  # Test locally without connecting to Discord
  python3 FantasyRecap/bfl_historian_bot.py --dry-run "Who has the highest single-week score since 2018?" --show-sql

  # Run interactively, or install as an auto-restarting Mac Mini LaunchAgent
  python3 FantasyRecap/bfl_historian_bot.py
  python3 FantasyRecap/bfl_historian_bot.py --install-launchd
  ```
* **Run Automated Unit Test Suite:**
  ```bash
  python3 -m unittest discover -s FantasyRecap/tests -v
  ```
