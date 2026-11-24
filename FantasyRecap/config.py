import sys
from pathlib import Path

_project_root = str(Path(__file__).resolve().parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from FantasyRecap.core.manifest import get_league_config

LEAGUE_CONFIG = get_league_config()

CURRENT_YEAR = LEAGUE_CONFIG["current_year"]
LEAGUE_ID = LEAGUE_CONFIG["league_id"]
LEGACY_LEAGUE_IDS = LEAGUE_CONFIG.get("legacy_league_ids", [])
REGULAR_SEASON_WEEKS = LEAGUE_CONFIG["regular_season_weeks"]
TOTAL_PLAYOFF_TEAMS = LEAGUE_CONFIG["total_playoff_teams"]
