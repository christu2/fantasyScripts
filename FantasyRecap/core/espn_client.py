"""
Resilient ESPN Fantasy & NFL Scoreboard HTTP Client
===================================================
Provides a shared `requests.Session` with exponential backoff and structured
exceptions (`ESPNClientError`) instead of `sys.exit(1)` calls that kill daemons.
"""

import time
from typing import Dict, List, Optional, Tuple
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from FantasyRecap.core.manifest import get_espn_credentials

POSITION_MAP = {
    1: "QB",
    2: "RB",
    3: "WR",
    4: "TE",
    5: "K",
    16: "D/ST",
}

SLOT_MAP = {
    0: "QB",
    2: "RB",
    4: "WR",
    6: "TE",
    16: "D/ST",
    17: "K",
    20: "Bench",
    21: "IR",
    23: "FLEX",
}

PRO_TEAM_MAP = {
    0: "FA", 22: "ARI", 1: "ATL", 33: "BAL", 2: "BUF", 29: "CAR", 3: "CHI", 4: "CIN",
    5: "CLE", 6: "DAL", 7: "DEN", 8: "DET", 9: "GB", 34: "HOU", 11: "IND", 30: "JAX",
    12: "KC", 13: "LV", 24: "LAC", 14: "LAR", 15: "MIA", 16: "MIN", 17: "NE", 18: "NO",
    19: "NYG", 20: "NYJ", 21: "PHI", 23: "PIT", 25: "SF", 26: "SEA", 27: "TB", 10: "TEN",
    28: "WSH",
}


class ESPNClientError(RuntimeError):
    """Raised when an ESPN API request fails after retries."""

    def __init__(self, message: str, status_code: Optional[int] = None, url: str = ""):
        super().__init__(message)
        self.status_code = status_code
        self.url = url


_SESSION: Optional[requests.Session] = None


def get_http_session() -> requests.Session:
    """Returns a process-wide pooled requests.Session with retry on transient 429/5xx."""
    global _SESSION
    if _SESSION is None:
        session = requests.Session()
        retry = Retry(
            total=3,
            connect=3,
            read=3,
            backoff_factor=0.6,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset(["GET", "HEAD"]),
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retry, pool_connections=16, pool_maxsize=16)
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        session.headers.update({"User-Agent": "BFL-Analytics-Suite/2.0"})
        _SESSION = session
    return _SESSION


def _build_cookies(s2: str = "", swid: str = "") -> dict:
    if s2 and swid:
        return {"espn_s2": s2, "SWID": swid}
    return {}


def fetch_json(
    url: str,
    *,
    params: Optional[dict] = None,
    cookies: Optional[dict] = None,
    headers: Optional[dict] = None,
    timeout: float = 12.0,
    max_attempts: int = 3,
) -> dict:
    """
    Fetches JSON with connection/read retries and raises `ESPNClientError`
    on failure instead of terminating the interpreter.
    """
    session = get_http_session()
    last_err = None
    for attempt in range(1, max_attempts + 1):
        try:
            resp = session.get(
                url,
                params=params,
                cookies=cookies,
                headers=headers,
                timeout=timeout,
            )
            if resp.status_code == 200:
                return resp.json()
            if resp.status_code in (429, 500, 502, 503, 504) and attempt < max_attempts:
                time.sleep(0.5 * (2 ** (attempt - 1)))
                continue
            raise ESPNClientError(
                f"ESPN API returned HTTP {resp.status_code}",
                status_code=resp.status_code,
                url=url,
            )
        except ESPNClientError:
            raise
        except Exception as exc:
            last_err = exc
            if attempt < max_attempts:
                time.sleep(0.5 * (2 ** (attempt - 1)))
                continue
            raise ESPNClientError(
                f"ESPN API request failed after {max_attempts} attempts: {exc}",
                url=url,
            ) from last_err
    raise ESPNClientError(f"ESPN API request exhausted retries: {last_err}", url=url)


def fetch_espn_week_data(
    league_id: str,
    season: str,
    week: int,
    s2: str = "",
    swid: str = "",
    views: Tuple[str, ...] = ("mMatchupScore", "mBoxscore", "mRoster", "mTeam", "mSettings"),
    timeout: float = 12.0,
) -> dict:
    """Fetch boxscores, rosters, matchups, and members from ESPN Fantasy API."""
    view_qs = "&".join(f"view={v}" for v in views)
    url = (
        f"https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/{season}"
        f"/segments/0/leagues/{league_id}?{view_qs}&scoringPeriodId={week}"
    )
    return fetch_json(url, cookies=_build_cookies(s2, swid), timeout=timeout)


def fetch_nfl_scoreboard(
    season: Optional[int] = None,
    week: Optional[int] = None,
    season_type: int = 2,
    timeout: float = 10.0,
) -> dict:
    """Fetches ESPN's public NFL Scoreboard API (HTTPS)."""
    url = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard"
    params = {}
    if season and week:
        params = {"dates": season, "seasontype": season_type, "week": week}
    return fetch_json(url, params=params, timeout=timeout)


def fetch_pro_team_schedules(season: int, timeout: float = 10.0) -> dict:
    """Fetches NFL pro-team schedules mapped by scoring period."""
    url = f"https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/{season}?view=proTeamSchedules_wl"
    return fetch_json(url, timeout=timeout)


def fetch_season_summary(
    season: int,
    *,
    league_id: Optional[str] = None,
    cookies: Optional[dict] = None,
    is_current: bool = False,
    timeout: float = 10.0,
) -> dict:
    """Fetches a season's matchup/team/member summary across current and legacy league IDs."""
    creds = get_espn_credentials()
    lid_candidates: List[str] = list(
        dict.fromkeys(([str(league_id)] if league_id else []) + creds["all_league_ids"])
    )
    eff_cookies = cookies if cookies is not None else creds["cookies"]

    for lid in lid_candidates:
        if is_current or season >= 2018:
            url = (
                f"https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/{season}"
                f"/segments/0/leagues/{lid}?view=mMatchupScore&view=mTeam&view=mSettings&view=mMembers"
            )
        else:
            url = (
                f"https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/leagueHistory/{lid}"
                f"?seasonId={season}&view=mMatchupScore&view=mTeam&view=mSettings&view=mMembers"
            )
        try:
            raw = fetch_json(url, cookies=eff_cookies, timeout=timeout, max_attempts=2)
            return raw[0] if isinstance(raw, list) and raw else raw
        except ESPNClientError:
            continue
    return {}


def parse_league_members_and_teams(data: dict) -> Dict[int, dict]:
    """Map team IDs to owners, team names, divisions, and season records."""
    members = {}
    for m in data.get("members", []):
        disp = m.get("displayName", "")
        first = m.get("firstName", "").strip()
        last = m.get("lastName", "").strip()
        full = f"{first} {last}".strip()
        members[m["id"]] = full if full else disp

    divisions = {}
    for d in data.get("settings", {}).get("scheduleSettings", {}).get("divisions", []):
        divisions[d["id"]] = d.get("name", f"Division {d['id']}")

    teams = {}
    for t in data.get("teams", []):
        tid = t["id"]
        t_name = t.get("name")
        if not t_name:
            loc = t.get("location") or ""
            nick = t.get("nickname") or ""
            t_name = f"{loc} {nick}".strip()

        owner_id = t.get("primaryOwner") or (t.get("owners", [None])[0])
        owner_name = members.get(owner_id, "Unknown")

        if not t_name or t_name.lower() in ("none none", "none", ""):
            t_name = owner_name

        div_id = t.get("divisionId", 0)
        div_name = divisions.get(div_id, "General")
        overall = t.get("record", {}).get("overall", {})

        teams[tid] = {
            "id": tid,
            "name": t_name.strip(),
            "owner": owner_name,
            "abbrev": t.get("abbrev", ""),
            "division": div_name,
            "wins": overall.get("wins", 0),
            "losses": overall.get("losses", 0),
            "ties": overall.get("ties", 0),
            "points_for": overall.get("pointsFor", 0.0),
            "points_against": overall.get("pointsAgainst", 0.0),
            "playoff_seed": t.get("playoffSeed", 99),
        }
    return teams
