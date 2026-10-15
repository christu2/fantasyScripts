import json
from pathlib import Path

_config_file = Path(__file__).resolve().parent / "league_config.json"
with open(_config_file, "r") as f:
    LEAGUE_CONFIG = json.load(f)

CURRENT_YEAR = LEAGUE_CONFIG["current_year"]
LEAGUE_ID = LEAGUE_CONFIG["league_id"]
REGULAR_SEASON_WEEKS = LEAGUE_CONFIG["regular_season_weeks"]
TOTAL_PLAYOFF_TEAMS = LEAGUE_CONFIG["total_playoff_teams"]
