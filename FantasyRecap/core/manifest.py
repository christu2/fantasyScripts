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


_NICKNAME_GROUPS = [
    {"tom", "tommy", "thomas"},
    {"dan", "danny", "daniel"},
    {"nick", "nicky", "nicholas"},
    {"mike", "mikey", "michael"},
    {"alex", "alexander", "alexandra"},
    {"sam", "sammy", "samuel"},
    {"chris", "christopher"},
    {"matt", "matthew"},
    {"rob", "robbie", "bob", "bobby", "robert"},
    {"will", "bill", "billy", "william"},
    {"joe", "joey", "joseph"},
    {"dave", "david"},
    {"steve", "steven", "stephen"},
    {"jim", "jimmy", "james"},
    {"ben", "benny", "benjamin"},
    {"abe", "abraham"},
    {"jon", "john", "jonathan"},
    {"jake", "jacob"},
    {"zach", "zack", "zachary"},
    {"josh", "joshua"},
    {"nate", "nathan", "nathaniel"},
    {"tim", "timmy", "timothy"},
    {"tony", "anthony"},
    {"pat", "patrick"},
    {"ed", "eddie", "edward"},
    {"andy", "andrew"},
]


def _are_same_person(name_a: str, name_b: str) -> bool:
    """
    Detects duplicate ESPN accounts belonging to the same human
    (e.g., 'Tom Ehrlich' vs 'Tommy Ehrlich', 'Dan Kruszewski' vs 'Daniel Kruszewski',
    'Nick Christus' vs 'Nicholas Christus') while keeping distinct co-owners
    (e.g., 'Nael Ahmed' vs 'Nitesh Patel', 'Sydney Miller' vs 'Emelie Lovasko') separate.
    """
    pa = [w for w in name_a.lower().strip().split() if w]
    pb = [w for w in name_b.lower().strip().split() if w]
    if not pa or not pb:
        return False
    if " ".join(pa) == " ".join(pb):
        return True
    # Must share the same last name (at least 2 tokens each)
    if len(pa) < 2 or len(pb) < 2 or pa[-1] != pb[-1]:
        return False
    fa, fb = pa[0], pb[0]
    if fa == fb:
        return True
    # Prefix match of >= 3 chars (e.g. "tom" in "tommy", "dan" in "daniel", "nick" in "nicholas")
    if len(fa) >= 3 and len(fb) >= 3 and (fa.startswith(fb) or fb.startswith(fa)):
        return True
    for grp in _NICKNAME_GROUPS:
        if fa in grp and fb in grp:
            return True
    return False


def dedupe_co_owners(raw_names: list, *, canon_fn=None) -> list:
    """
    Normalizes a list of co-owner names and collapses duplicate ESPN accounts
    belonging to the same person (preserving the first/canonical name).
    """
    out = []
    for raw in raw_names:
        s = (raw or "").strip()
        if not s:
            continue
        if canon_fn is not None:
            s = canon_fn(s)
        if not s:
            continue
        if any(_are_same_person(s, existing) for existing in out):
            continue
        out.append(s)
    return out


def extract_credited_owners(primary_owner: str, co_owners_str: str = "", *, canon_fn=None) -> list:
    """
    Returns all distinct people credited with owning a team in a given season/game
    (e.g., ['Nael Ahmed', 'Nitesh Patel'] for 2022–2025 Team 39, while collapsing
    duplicate accounts like 'Tommy Ehrlich, Tom Ehrlich' into ['Tommy Ehrlich']).
    """
    parts = [primary_owner]
    if co_owners_str:
        parts.extend(p.strip() for p in str(co_owners_str).split(",") if p.strip())
    return dedupe_co_owners(parts, canon_fn=canon_fn)
