import os
import requests
from pathlib import Path
from dotenv import load_dotenv
from FantasyRecap.config import LEAGUE_ID, CURRENT_YEAR

env_path = Path(__file__).resolve().parent.parent / '.env'
if env_path.exists():
    load_dotenv(dotenv_path=env_path)
else:
    load_dotenv()

ESPN_S2 = os.getenv("ESPN_S2", "")
ESPN_SWID = os.getenv("ESPN_SWID", "")

class ESPNClient:
    """Centralized client for ESPN Fantasy Football API requests."""
    
    BASE_URL = "https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons"
    
    @staticmethod
    def get_cookies():
        return {"espn_s2": ESPN_S2, "SWID": ESPN_SWID}
        
    @classmethod
    def fetch_league_data(cls, season: int = None, week: int = None, views: list = None) -> dict:
        """
        Fetches league data. If season is not provided, defaults to CURRENT_YEAR.
        """
        season = season or CURRENT_YEAR
        url = f"{cls.BASE_URL}/{season}/segments/0/leagues/{LEAGUE_ID}"
        
        params = {}
        if week:
            params['scoringPeriodId'] = week
        if views:
            # requests handles list params like: ?view=mMatchup&view=mMatchupScore
            params['view'] = views
            
        response = requests.get(url, params=params, cookies=cls.get_cookies(), timeout=10)
        
        if response.status_code != 200:
            print(f"❌ ESPN API Error {response.status_code}: {response.text}")
            return {}
            
        return response.json()
