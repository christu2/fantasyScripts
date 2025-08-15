# Fantasy Football Tools

A collection of Python scripts for managing fantasy football leagues, including advanced scheduling and comprehensive record tracking.

## 🏈 What's Included

### 📊 BFL All-Time Records Tracker
**Main Script:** `bfl_all_time_records_master.py`

Generates comprehensive head-to-head records for all league members from 2008-present.

**Features:**
- **Complete historical tracking** - Records against every opponent ever faced
- **Google Sheets integration** - Automatic spreadsheet updates with formatting
- **Name variation handling** - Tracks players across name changes (Tom/Tommy/Thomas, etc.)
- **Co-ownership support** - Handles Sydney/Emelie shared records through 2024, automatic split starting 2025
- **Smart synonym mapping** - Consolidates historical data across all name variations
- **Individual owner sheets** - Dedicated tab for each player with win/loss vs all opponents
- **Summary standings** - Overall win percentages and rankings
- **Future-proof** - Automatically handles new seasons and roster changes

### 🗓️ Advanced Fantasy Scheduler
**Main Script:** `FantasyScheduler/fantasy_scheduler_ortools.py`

Generates optimal 14-week schedules for 16-team leagues using constraint satisfaction.

**Features:**
- **13 comprehensive constraints** ensuring fair, competitive schedules
- **Perfect balance** - 7 home/7 away, 3H/3A divisional, 2H/2A cross-divisional
- **Rivalry week** - Dedicated week 10 for predetermined matchups
- **Smart scheduling** - Minimum separation between rematches, optimal timing
- **Final week drama** - All week 14 games are divisional for maximum competition
- **CSV output** - Ready for ESPN import with multiple formats
- **Extensive validation** - Automated verification of all constraints

#### Scheduling Constraints:
1. Each team plays exactly once per week
2. Each week has exactly 8 games
3. Division double round-robin (6 games per team)
4. Opposite division single round (4 games per team)
5. **Opposite division 2H/2A balance** (North/South, East/West)
6. Overall 7H/7A balance per team
7. Divisional 3H/3A balance per team
8. Rivalry week scheduling (Week 10)
9. Minimum 3-week separation between division rematches
10. Maximum 2 consecutive division games in any 3-week window
11. Minimum 3 division games in final 8 weeks
12. Non-division opponents play maximum once
13. All Week 14 games are divisional
14. Maximum 3 consecutive home or away games

## 🚀 Quick Start

### Prerequisites
```bash
pip install espn-api pandas gspread google-auth-oauthlib python-dotenv ortools
```

### Setup
1. **Clone the repository**
   ```bash
   git clone <repository-url>
   cd fantasyScripts
   ```

2. **Configure ESPN credentials**
   ```bash
   cp .env.template .env
   # Edit .env with your ESPN credentials (see instructions in file)
   ```

3. **Setup Google Sheets (for records tracking)**
   - Create OAuth credentials at [Google Cloud Console](https://console.cloud.google.com)
   - Enable Google Sheets API and Google Drive API
   - Download credentials as `FantasyRecords/credentials.json`

### Usage

#### Generate All-Time Records
```bash
python3 bfl_all_time_records_master.py
```
- Updates Google Sheets with current records
- Handles current season and all historical data
- Automatically manages Sydney/Emelie co-ownership transition

#### Generate New Schedule
```bash
cd FantasyScheduler
python3 fantasy_scheduler_ortools.py
```
- Creates optimized 14-week schedule
- Outputs CSV files ready for ESPN import
- Validates all constraints automatically

## 📁 Repository Structure

```
fantasyScripts/
├── bfl_all_time_records_master.py    # Main records tracking script
├── FantasyScheduler/
│   ├── fantasy_scheduler_ortools.py  # Advanced scheduler
│   └── *.csv                         # Generated schedule files
├── FantasyRecords/
│   └── credentials.json              # Google OAuth credentials
├── .env.template                     # ESPN credentials template
└── README.md                         # This file

# Protected files (in .gitignore):
├── .env                              # ESPN credentials
├── token.pickle                      # Google auth tokens
└── bfl_records_*.csv                 # Generated records
```

## 🔧 Configuration

### ESPN Credentials
Required for accessing league data. Get these from your browser when logged into ESPN Fantasy:

1. Open Developer Tools (F12)
2. Go to Application/Storage → Cookies → fantasy.espn.com
3. Copy values for `espn_s2` and `SWID`
4. Add to `.env` file

### Google Sheets Setup
Required for automatic record updates:

1. Create project at [Google Cloud Console](https://console.cloud.google.com)
2. Enable Google Sheets API and Google Drive API
3. Create OAuth 2.0 credentials (Desktop Application)
4. Download as `FantasyRecords/credentials.json`

## 🎯 Key Features

### Records Tracking
- **17 years of data** (2008-2024+)
- **3,400+ games** tracked
- **40+ historical opponents** per active player
- **Automatic name standardization**
- **Co-ownership handling**
- **Real-time Google Sheets updates**

### Schedule Generation
- **Constraint satisfaction optimization**
- **Sub-second solve times**
- **Perfect balance across all game types**
- **ESPN-ready output formats**
- **Comprehensive validation**

## 🔒 Security

All sensitive data is properly protected:
- ESPN credentials in environment variables
- Google OAuth secrets gitignored
- Authentication tokens excluded from repository
- Safe for public sharing

## 📈 Future Enhancements

The scripts are designed to handle:
- **New seasons automatically** (just update CURRENT_YEAR)
- **Roster changes** (automatic detection from ESPN)
- **Rule modifications** (easily adjustable constraints)
- **Additional leagues** (configurable league IDs)

---

**Beasts Football League** - Advanced Fantasy Football Management Tools
