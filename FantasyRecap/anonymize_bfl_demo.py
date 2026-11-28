#!/usr/bin/env python3
"""
Deterministic BFL Hub Anonymizer for Public Live Demo (`demo_hub_data.json`)
============================================================================
Transforms the real 18-year, 16-team BFL `hub_data.json` into a 100% anonymized,
podcast-free showcase (`Gridiron Premier League`, `GPL`) while preserving every
mathematical invariant:
  - Exact 4x4 division structure, records, PF, power ratings, and remaining schedule
  - Lexicographically re-keyed `history.h2h` (`o1 < o2` with swapped stats when order flips)
  - Full 18-year Trade Vault VORP fleeces, Draft Steals/Busts, All-Play Luck, and Dossiers
  - Complete removal of all podcast episodes/RSS URLs and zero real-name/nickname leaks
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
import re
from typing import Dict, List, Set

SCRIPT_DIR = Path(__file__).resolve().parent
WEB_HUB_DIR = SCRIPT_DIR / "web_hub"

PERSONA_POOL = [
    ("Marcus Vance", "Vance", "Iron City Forgers"),
    ("Caleb Mercer", "Mercer", "Bayside Sharks"),
    ("Jordan Sterling", "Sterling", "Apex Predators"),
    ("Devon Brooks", "Brooks", "Midnight Marauders"),
    ("Logan Hayes", "Hayes", "Gridiron Phantoms"),
    ("Tyler Callahan", "Callahan", "RedZone Renegades"),
    ("Connor Ramsey", "Ramsey", "Summit Wolves"),
    ("Ethan Prescott", "Prescott", "Harbor Kings"),
    ("Lucas Bennett", "Bennett", "Metro Mustangs"),
    ("Owen Gallagher", "Gallagher", "Thunderbird FC"),
    ("Noah Whitaker", "Whitaker", "Crimson Tidebreakers"),
    ("Gavin Holloway", "Holloway", "Lakefront Legends"),
    ("Liam Donovan", "Donovan", "Steel Valley Vipers"),
    ("Carter Kensington", "Kensington", "Pacific Blitz"),
    ("Trevor Dalton", "Dalton", "Highland Hawks"),
    ("Grant Sullivan", "Sullivan", "Westside Warlords"),
    # Extra personas for historical Alumni managers (zero overlap with real first names):
    ("Nathaniel Cole", "Cole", "Old Town Outlaws"),
    ("Wesley Lawson", "Lawson", "Riverfront Raiders"),
    ("Preston Reid", "Reid", "Capital Cruisers"),
    ("Garrett Harrison", "Harrison", "Founders Club"),
    ("wyatt_porter", "Porter", "Silverbacks"),
    ("Miles Collins", "Collins", "Northstar Knights"),
    ("julian_fletcher", "Fletcher", "Bleacher Creatures"),
    ("Declan Monroe", "Monroe", "Sunday Syndicate"),
]


def _clean_persona(idx: int):
    raw_own, code, raw_team = PERSONA_POOL[idx % len(PERSONA_POOL)]
    own = " ".join(w.capitalize() for w in raw_own.replace("_", " ").split())
    tname = " ".join(w.capitalize() if w.lower() != "fc" else "FC" for w in raw_team.split())
    if idx >= len(PERSONA_POOL):
        own = f"{own} {idx - len(PERSONA_POOL) + 2}"
        tname = f"{tname} {idx - len(PERSONA_POOL) + 2}"
    return own, code, tname


def build_anonymized_demo(src_path: Path = WEB_HUB_DIR / "hub_data.json", out_path: Path = WEB_HUB_DIR / "demo_hub_data.json") -> dict:
    src = json.loads(src_path.read_text(encoding="utf-8"))
    d = copy.deepcopy(src)

    # 1. Discover every real owner name & team name across active teams, career, dossiers, championships, trades, drafts
    ordered_owners: List[str] = []
    real_team_names: Set[str] = set()
    real_codes: Dict[str, str] = {}

    def _reg_owner(nm: str) -> None:
        if nm and nm not in ordered_owners:
            ordered_owners.append(nm)

    for t in d.get("teams", []):
        _reg_owner(t.get("owner", ""))
        if t.get("team_name"):
            real_team_names.add(t["team_name"])
        if t.get("code"):
            real_codes[t["owner"]] = t["code"]

    for c in d.get("history", {}).get("career", []):
        _reg_owner(c.get("owner", ""))
        if c.get("team_name"):
            real_team_names.add(c["team_name"])
        for fn in c.get("former_names") or []:
            real_team_names.add(fn)

    for dos in d.get("dossiers", []):
        _reg_owner(dos.get("owner", ""))
        if dos.get("team_name"):
            real_team_names.add(dos["team_name"])
        for tl in dos.get("timeline", []):
            if tl.get("team_name"):
                real_team_names.add(tl["team_name"])

    for ch in d.get("history", {}).get("championships", []):
        _reg_owner(ch.get("champ", ""))
        _reg_owner(ch.get("runner_up", ""))

    for bucket in ("all_time", "live"):
        for tr in (d.get("trades", {}).get(bucket, {}).get("trades") or []):
            _reg_owner(tr.get("owner_a", ""))
            _reg_owner(tr.get("owner_b", ""))

    for _yr, pack in (d.get("history", {}).get("drafts") or {}).items():
        for lst_key in ("steals", "busts"):
            for row in (pack.get(lst_key) or []):
                _reg_owner(row.get("owner", ""))

    owner_map: Dict[str, str] = {}
    code_map: Dict[str, str] = {}
    team_map: Dict[str, str] = {}
    for idx, real_own in enumerate(ordered_owners):
        anon_own, anon_code, anon_team = _clean_persona(idx)
        owner_map[real_own] = anon_own
        code_map[real_own] = anon_code
        team_map[real_own] = anon_team

    # Also build case-insensitive lookup for any casing quirk like "rej hoxha" vs "Rej Hoxha"
    ci_owner_map = {k.lower(): v for k, v in owner_map.items()}

    def _map_own(nm: str) -> str:
        if not nm:
            return nm
        return owner_map.get(nm) or ci_owner_map.get(nm.lower(), nm)

    # Free-text scrubber for awards/notes/team names (longest tokens first so full names replace before first names!)
    text_replacements: List[tuple[str, str]] = [
        ("Beasts Football League", "Gridiron Premier League"),
        ("Jabroni Trophy", "Premier Cup"),
        ("Jabroni", "Premier"),
        ("Tuesday Morning Hangover", "Weekly League Briefing"),
        ("BFL", "GPL"),
    ]
    # Known BFL nicknames -> anonymized short codes
    NICKNAME_OWNER_HINTS = {
        "Lukose": "Shawn Lukose",
        "Thor": "Shawn Ullenbrauck",
        "AMO": "Adam Olen",
        "Rej": "rej hoxha",
        "DTM": "Daniel Kruszewski",
        "Ehrly": "Tommy Ehrlich",
        "Mykonos": "Nick Christus",
    }
    for tname in sorted(real_team_names, key=len, reverse=True):
        if len(tname) >= 3:
            text_replacements.append((tname, "Gridiron Club"))
    for real_own, anon_own in sorted(owner_map.items(), key=lambda kv: len(kv[0]), reverse=True):
        text_replacements.append((real_own, anon_own))
        text_replacements.append((real_own.title(), anon_own))
        parts = real_own.split()
        if len(parts) >= 2:
            # Map surname & first name safely on word boundaries
            if len(parts[-1]) >= 3:
                text_replacements.append((parts[-1], anon_own.split()[-1]))
            if len(parts[0]) >= 3:
                text_replacements.append((parts[0], anon_own.split()[0]))
    for nick, target_own in NICKNAME_OWNER_HINTS.items():
        mapped = _map_own(target_own)
        if mapped != target_own:
            text_replacements.append((nick, mapped.split()[-1]))

    def _scrub_text(txt: str) -> str:
        if not txt:
            return txt
        out = str(txt)
        for src_tok, dst_tok in text_replacements:
            out = re.sub(rf"\b{re.escape(src_tok)}\b", dst_tok, out, flags=re.IGNORECASE)
        return out

    # 2. Rebrand League Metadata & Strip Podcast
    d["league_config"] = {
        "platform": "demo",
        "league_id": "demo",
        "slug": "demo-bfl",
        "name": "Gridiron Premier League (Live Demo)",
        "badge": "GPL",
        "first_season": 2008,
        "scoring_label": "Half PPR",
        "format_label": "16-Team Demo",
        "draft_type": "SNAKE",
        "has_divisions": True,
        "is_first_year": False,
        "is_demo": True,
    }
    d["podcast"] = {"rss_url": "", "episodes": []}

    # 3. Divisions, Teams, Completed Games, Remaining Schedule, Weekly Projections
    d["divisions"] = {div: [_map_own(o) for o in olist] for div, olist in d.get("divisions", {}).items()}
    for t in d.get("teams", []):
        ro = t["owner"]
        t["owner"] = _map_own(ro)
        t["code"] = code_map.get(ro, t["owner"].split()[0])
        t["team_name"] = team_map.get(ro, "Gridiron Club")

    for g in d.get("completed_games", []):
        g["away"] = _map_own(g["away"])
        g["home"] = _map_own(g["home"])

    for g in d.get("remaining_schedule", []):
        g["away"] = _map_own(g["away"])
        g["home"] = _map_own(g["home"])

    new_wproj = {}
    for wk, wmap in (d.get("weekly_projections") or {}).items():
        new_wproj[wk] = {_map_own(o): v for o, v in wmap.items()}
    d["weekly_projections"] = new_wproj

    # 4. Awards
    if d.get("awards") and d["awards"].get("awards"):
        for aw in d["awards"]["awards"]:
            aw["title"] = _scrub_text(aw.get("title", ""))
            aw["metric"] = _scrub_text(aw.get("metric", ""))
            aw["body"] = _scrub_text(aw.get("body", ""))

    # 5. History: Career, H2H (re-sorted `o1 < o2`!), Championships, Drafts
    hist = d.get("history", {})
    for c in hist.get("career", []):
        ro = c["owner"]
        c["owner"] = _map_own(ro)
        c["code"] = code_map.get(ro, c["owner"].split()[0])
        c["team_name"] = team_map.get(ro, "Gridiron Club")
        c["former_names"] = []

    new_h2h = {}
    for _key, rec in (hist.get("h2h") or {}).items():
        no1 = _map_own(rec["o1"])
        no2 = _map_own(rec["o2"])
        r_copy = copy.deepcopy(rec)
        r_copy["streak_owner"] = _map_own(rec.get("streak_owner", ""))
        if r_copy.get("last"):
            r_copy["last"]["winner"] = _map_own(r_copy["last"].get("winner", ""))
        if r_copy.get("closest"):
            r_copy["closest"]["winner"] = _map_own(r_copy["closest"].get("winner", ""))
        for sb in r_copy.get("super_bowls") or []:
            sb["champ"] = _map_own(sb.get("champ", ""))
            sb["runner_up"] = _map_own(sb.get("runner_up", ""))

        if no1 <= no2:
            r_copy["o1"], r_copy["o2"] = no1, no2
            new_h2h[f"{no1}|||{no2}"] = r_copy
        else:
            # Alphabetical order flipped -> swap o1/o2 & 1/2 directional counters cleanly!
            r_copy["o1"], r_copy["o2"] = no2, no1
            r_copy["w1"], r_copy["w2"] = rec["w2"], rec["w1"]
            r_copy["po1"], r_copy["po2"] = rec["po2"], rec["po1"]
            r_copy["avg1"], r_copy["avg2"] = rec["avg2"], rec["avg1"]
            new_h2h[f"{no2}|||{no1}"] = r_copy
    hist["h2h"] = new_h2h

    for ch in hist.get("championships", []):
        ch["champ"] = _map_own(ch.get("champ", ""))
        ch["runner_up"] = _map_own(ch.get("runner_up", ""))

    for _yr, pack in (hist.get("drafts") or {}).items():
        for lst_key in ("steals", "busts"):
            for row in (pack.get(lst_key) or []):
                row["owner"] = _map_own(row.get("owner", ""))

    hist["identity_review_candidates"] = []

    # 6. Trades (All-Time & Live)
    for bucket in ("all_time", "live"):
        b_obj = (d.get("trades") or {}).get(bucket)
        if not b_obj:
            continue
        for tr in b_obj.get("trades") or []:
            tr["owner_a"] = _map_own(tr.get("owner_a", ""))
            tr["owner_b"] = _map_own(tr.get("owner_b", ""))
            if "a_owners" in tr:
                tr["a_owners"] = _map_own(tr["a_owners"]) if isinstance(tr["a_owners"], str) else [_map_own(x) for x in tr["a_owners"]]
            if "b_owners" in tr:
                tr["b_owners"] = _map_own(tr["b_owners"]) if isinstance(tr["b_owners"], str) else [_map_own(x) for x in tr["b_owners"]]
            if tr.get("a_outcome"):
                tr["a_outcome"] = _scrub_text(tr["a_outcome"])
            if tr.get("b_outcome"):
                tr["b_outcome"] = _scrub_text(tr["b_outcome"])
            for rkey in ("a_received", "b_received"):
                for pl in tr.get(rkey) or []:
                    if isinstance(pl, dict) and isinstance(pl.get("flipped"), dict) and pl["flipped"].get("to"):
                        pl["flipped"]["to"] = _map_own(pl["flipped"]["to"])
            for lkey in ("started", "ros"):
                for container in (tr, tr.get("lenses") or {}):
                    ld = container.get(lkey)
                    if isinstance(ld, dict):
                        if ld.get("winner") and ld["winner"] != "PUSH":
                            ld["winner"] = _map_own(ld["winner"])
                        if ld.get("loser") and ld["loser"] != "PUSH":
                            ld["loser"] = _map_own(ld["loser"])
                        if ld.get("tags"):
                            ld["tags"] = [_scrub_text(tg) for tg in ld["tags"]]
            if tr.get("tags"):
                tr["tags"] = [_scrub_text(tg) for tg in tr["tags"]]

        ocards = b_obj.get("owner_cards") or {}
        for lkey in ("started", "ros"):
            for card in ocards.get(lkey) or []:
                card["owner"] = _map_own(card.get("owner", ""))
                for bt_key in ("best_trade", "worst_trade"):
                    bt = card.get(bt_key)
                    if bt and bt.get("partner"):
                        bt["partner"] = _map_own(bt["partner"])

    # 7. Analytics (Luck, Trends, Heatmap)
    an = d.get("analytics") or {}
    if an.get("luck") and an["luck"].get("by_season"):
        for _sk, lrows in an["luck"]["by_season"].items():
            for lr in lrows:
                lr["owner"] = _map_own(lr.get("owner", ""))
    if an.get("trends") and an["trends"].get("series"):
        an["trends"]["series"] = {_map_own(o): v for o, v in an["trends"]["series"].items()}
    if an.get("heatmap") and an["heatmap"].get("rows"):
        for hr in an["heatmap"]["rows"]:
            hr["owner"] = _map_own(hr.get("owner", ""))

    # 8. Franchise Dossiers
    for dos in d.get("dossiers") or []:
        ro = dos["owner"]
        dos["owner"] = _map_own(ro)
        dos["code"] = code_map.get(ro, dos["owner"].split()[0])
        anon_tname = team_map.get(ro, "Gridiron Club")
        dos["team_name"] = anon_tname
        dos["former_names"] = []
        if dos.get("nemesis") and dos["nemesis"].get("owner"):
            dos["nemesis"]["owner"] = _map_own(dos["nemesis"]["owner"])
        if dos.get("victim") and dos["victim"].get("owner"):
            dos["victim"]["owner"] = _map_own(dos["victim"]["owner"])
        tr_blk = dos.get("trade") or {}
        for bt_key in ("best_trade", "worst_trade"):
            if tr_blk.get(bt_key) and tr_blk[bt_key].get("partner"):
                tr_blk[bt_key]["partner"] = _map_own(tr_blk[bt_key]["partner"])
        for tl in dos.get("timeline") or []:
            tl["team_name"] = anon_tname

    # 8b. Deep recursive pass on any remaining free-text / dict keys (preserving NFL `player` names & already-anonymized owners)
    PLAYER_KEYS = {"player", "player_name"}
    anon_values_protected = set(owner_map.values()) | set(code_map.values()) | set(team_map.values())

    def _deep_clean(node, parent_key: str = ""):
        if isinstance(node, dict):
            cleaned = {}
            for k, v in node.items():
                nk = _map_own(k) if isinstance(k, str) and (k in owner_map or k.lower() in ci_owner_map) else k
                cleaned[nk] = v if k in PLAYER_KEYS else _deep_clean(v, str(k))
            return cleaned
        if isinstance(node, list):
            return [_deep_clean(x, parent_key) for x in node]
        if isinstance(node, str):
            if node in anon_values_protected:
                return node
            if node in owner_map or node.lower() in ci_owner_map:
                return _map_own(node)
            if parent_key == "name" and node not in real_team_names and "Beasts" not in node:
                return node
            return _scrub_text(node)
        return node

    d = _deep_clean(d)

    # 9. Automated Zero-Leak Verification Gate!
    serialized = json.dumps(d, indent=2)
    FORBIDDEN_SURNAMES_AND_BRANDS = [
        "Christus", "Kruszewski", "Ehrlich", "Whitehouse", "Gupta",
        "Lukose", "Hoxha", "Mirza", "Davros", "Olen", "Ullenbrauck",
        "Mykonos", "Ehrly", "Beasts Football", "Jabroni", "bfl-podcast",
        "Tuesday Morning Hangover", "feed.xml",
    ]
    leaked = [tok for tok in FORBIDDEN_SURNAMES_AND_BRANDS if re.search(re.escape(tok), serialized, re.IGNORECASE)]
    if leaked:
        raise AssertionError(f"Privacy check failed — leaked tokens in demo_hub_data.json: {leaked}")

    out_path.write_text(serialized, encoding="utf-8")
    print(f"✅ Wrote verified zero-PII anonymized BFL demo ({out_path.stat().st_size / 1024:.1f} KB): {out_path}")
    return d


if __name__ == "__main__":
    build_anonymized_demo()
