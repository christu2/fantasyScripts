"""
Canonical League Manifest & Owner Identity Resolver
===================================================
Single source of truth loaded from `FantasyRecap/league_manifest.json`.
Eliminates hardcoded owner lists, division rosters, Discord ID maps,
and league IDs duplicated across individual scripts.
"""

import json
import os
from functools import lru_cache
from pathlib import Path
from dotenv import load_dotenv

RECAP_DIR = Path(__file__).resolve().parent.parent
PROJECT_ROOT = RECAP_DIR.parent
MANIFEST_PATH = RECAP_DIR / "league_manifest.json"

def load_project_env(*, override: bool = False) -> None:
    """Safely loads `.env` from project root (or CWD) without crashing on restricted parent dirs."""
    try:
        env_path = PROJECT_ROOT / ".env"
        if env_path.exists():
            load_dotenv(dotenv_path=env_path, override=override)
            return
    except OSError:
        pass
    try:
        load_dotenv(override=override)
    except OSError:
        pass


load_project_env()


@lru_cache(maxsize=1)
def load_manifest(path: str = None) -> dict:
    """Loads and caches the canonical league manifest JSON."""
    target = Path(path) if path else MANIFEST_PATH
    with open(target, "r", encoding="utf-8") as f:
        return json.load(f)


def get_league_config() -> dict:
    m = load_manifest()
    return {
        "league_id": m["league_id"],
        "legacy_league_ids": m.get("legacy_league_ids", []),
        "current_year": m["current_year"],
        "league_name": m["league_name"],
        "league_short_name": m.get("league_short_name", "BFL"),
        "divisions": m["divisions"],
        "regular_season_weeks": m["regular_season_weeks"],
        "total_playoff_teams": m["total_playoff_teams"],
        "discord_bot_enabled": m.get("discord_bot_enabled", True),
        "timezone": m.get("timezone", "America/Chicago"),
    }


def get_espn_credentials() -> dict:
    """Returns canonical ESPN league_id, legacy_league_ids, s2, swid, cookies, and current_year."""
    m = load_manifest()
    league_id = str(os.getenv("ESPN_LEAGUE_ID") or os.getenv("LEAGUE_ID") or m["league_id"])
    s2 = os.getenv("ESPN_S2", "")
    swid = os.getenv("ESPN_SWID") or os.getenv("SWID", "")
    season_year = int(os.getenv("SEASON_YEAR") or m["current_year"])
    legacy_ids = [str(lid) for lid in m.get("legacy_league_ids", [])]
    all_league_ids = list(dict.fromkeys([league_id] + legacy_ids))
    cookies = {"espn_s2": s2, "SWID": swid} if (s2 and swid) else {}
    return {
        "league_id": league_id,
        "legacy_league_ids": legacy_ids,
        "all_league_ids": all_league_ids,
        "espn_s2": s2,
        "espn_swid": swid,
        "cookies": cookies,
        "season_year": season_year,
    }


def get_manager_synonyms() -> dict:
    return dict(load_manifest().get("manager_synonyms", {}))


def standardize_name(name: str) -> str:
    """Canonicalizes any historical/display owner name to the league's standard spelling."""
    if not name:
        return "Unknown"
    clean = name.lower().strip()
    synonyms = load_manifest().get("manager_synonyms", {})
    if clean in synonyms:
        return synonyms[clean]
    if clean == "rej hoxha":
        return "rej hoxha"
    return name.strip().title()


def get_team_details() -> dict:
    """Returns `{code: {'owner': ..., 'team_name': ..., 'division': ...}}`."""
    return {
        t["code"]: {
            "owner": t["owner"],
            "team_name": t["team_name"],
            "division": t["division"],
        }
        for t in load_manifest().get("teams", [])
    }


def get_owner_to_code() -> dict:
    return {t["owner"]: t["code"] for t in load_manifest().get("teams", [])}


def get_divisions() -> dict:
    """Returns `{division_name: [owner_name, ...]}` preserving manifest order."""
    m = load_manifest()
    divs = {d: [] for d in m.get("divisions", [])}
    for t in m.get("teams", []):
        divs.setdefault(t["division"], []).append(t["owner"])
    return divs


def get_owner_to_division() -> dict:
    return {t["owner"]: t["division"] for t in load_manifest().get("teams", [])}


def get_owner_profiles() -> dict:
    return {
        t["owner"]: dict(t["profile"])
        for t in load_manifest().get("teams", [])
        if "profile" in t
    }


def get_championships() -> list:
    return [dict(c) for c in load_manifest().get("championships", [])]


def get_discord_id_to_owner() -> dict:
    out = {}
    for t in load_manifest().get("teams", []):
        for did in t.get("discord_ids", []):
            out[str(did)] = t["owner"]
    return out


def get_discord_user_map() -> dict:
    out = {}
    for t in load_manifest().get("teams", []):
        for uname in t.get("discord_usernames", []):
            out[uname.lower()] = t["owner"]
    return out


def get_owner_team_keywords() -> dict:
    return {
        t["owner"]: list(t.get("team_keywords", [t["owner"].lower()]))
        for t in load_manifest().get("teams", [])
    }


def get_owner_mention_triggers() -> list:
    triggers = []
    for t in load_manifest().get("teams", []):
        for trig in t.get("mention_triggers", []):
            triggers.append((trig, t["owner"]))
    return triggers


def get_podcast_metadata() -> dict:
    return dict(load_manifest().get("podcast", {}))
