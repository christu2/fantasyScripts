"""
3-Tier Fantasy Manager Identity Resolver (ESPN `member.id` & Sleeper `user_id`)
===============================================================================
Solves longitudinal franchise & manager identity across multi-year league history:

  1. **Manager Replacement (Franchise Takeover):**
     Different platform GUID (`member.id` / `user_id`) on the same `team_id`/`roster_id`
     -> Strictly separated! New manager NEVER inherits predecessor's wins, losses,
     rings, trades, or draft busts. Predecessor is preserved in historical tables
     with `is_active = False` ("Alumni").

  2. **Marriage / Last-Name / Handle Change on Same Account (Tier 1 — 100% Confidence):**
     Identical immutable platform GUID (`{SWID}` on ESPN or `user_id` on Sleeper)
     with a new name in a later season (`"Emily Smith"` -> `"Emily Johnson"`, or
     `"@Destroyer99"` -> `"Kyle Miller"`) -> Automatically unified under the manager's
     latest canonical name, recording `former_names: ["Emily Smith"]`!

  3. **Same Person, Duplicate Account (Tier 2 — High Confidence):**
     Different GUIDs on the same franchise with matching surname + nickname/prefix
     via `_are_same_person` (`"Tom Ehrlich"` -> `"Tommy Ehrlich"`), OR explicit
     commissioner `owner_aliases` mapping (`{"Emily Smith": "Emily Johnson"}`).

  4. **Ambiguous Takeover vs. New-Account Name Change (Tier 3 — Flagged, Never Guessed):**
     Different GUIDs on the same `team_id` in adjacent seasons sharing the exact same
     first name (`"Emily Smith"` -> `"Emily Davis"`) or identical custom `team_name` ->
     Kept separate by default to prevent false history attribution, and emitted in
     `identity_review_candidates` so a commissioner can merge in 1 click if needed.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Callable, Dict, List, Optional, Set, Tuple

from FantasyRecap.core.manifest import _are_same_person


class LeagueIdentityResolver:
    """ Resolves `(season, raw_member_id, raw_name, team_id, team_name)` -> canonical owner name. """

    def __init__(
        self,
        *,
        owner_aliases: Optional[Dict[str, str]] = None,
        name_normalizer: Optional[Callable[[str], str]] = None,
    ) -> None:
        self._norm = name_normalizer or (lambda s: " ".join(str(s or "").strip().split()))
        self.explicit_aliases: Dict[str, str] = {}
        for k, v in (owner_aliases or {}).items():
            nk, nv = self._norm(k), self._norm(v)
            if nk and nv:
                self.explicit_aliases[nk.lower()] = nv

        # Observations per immutable platform GUID: guid -> list of (season, norm_name, team_id, team_name)
        self._guid_obs: Dict[str, List[Tuple[int, str, int, str]]] = defaultdict(list)
        # Franchise slot history: team_id -> dict[season, (guid, norm_name, team_name)]
        self._slot_history: Dict[int, Dict[int, Tuple[str, str, str]]] = defaultdict(dict)

        self._guid_to_canon: Dict[str, str] = {}
        self._former_names: Dict[str, Set[str]] = defaultdict(set)
        self._active_owners: Set[str] = set()
        self._review_candidates: List[dict] = []
        self._finalized = False

    def observe(
        self,
        season: int,
        member_id: Optional[str],
        raw_name: str,
        *,
        team_id: int = 0,
        team_name: str = "",
        is_current_season: bool = False,
    ) -> None:
        """Records an owner observation during chronological or multi-season discovery."""
        norm_name = self._apply_explicit(self._norm(raw_name))
        guid = str(member_id or "").strip()
        if not norm_name:
            norm_name = f"Team {team_id} ({season})" if team_id else f"Manager ({season})"
        if not guid:
            # Unowned / orphan team slot: scope synthetic GUID to (season, team_id) so two different
            # orphan seasons never falsely merge into one "Team 3" career!
            guid = f"__orphan_{season}_{team_id}_{norm_name.lower()}__"

        self._guid_obs[guid].append((int(season), norm_name, int(team_id), str(team_name or "").strip()))
        if team_id > 0:
            self._slot_history[int(team_id)][int(season)] = (guid, norm_name, str(team_name or "").strip())
        if is_current_season:
            self._active_owners.add(guid)

    def _apply_explicit(self, name: str) -> str:
        seen = set()
        cur = name
        while cur.lower() in self.explicit_aliases and cur.lower() not in seen:
            seen.add(cur.lower())
            cur = self.explicit_aliases[cur.lower()]
        return cur

    def finalize(self) -> None:
        """Computes canonical names per GUID, Tier-2 duplicate-account links, former names, and review candidates."""
        # Step 1 (Tier 1): For each immutable GUID, pick the name from its MOST RECENT season!
        guid_latest_name: Dict[str, str] = {}
        guid_all_names: Dict[str, List[Tuple[int, str]]] = {}
        for guid, obs in self._guid_obs.items():
            obs_sorted = sorted(obs, key=lambda x: x[0])
            latest_name = self._apply_explicit(obs_sorted[-1][1])
            guid_latest_name[guid] = latest_name
            guid_all_names[guid] = [(yr, self._apply_explicit(nm)) for yr, nm, _, _ in obs_sorted]

        # Disambiguate if two genuinely DIFFERENT active GUIDs happen to have the exact same display name
        # (e.g. two different guys both named "Mike" on Sleeper with no last name)
        name_to_guids: Dict[str, List[str]] = defaultdict(list)
        for guid, nm in guid_latest_name.items():
            name_to_guids[nm.lower()].append(guid)

        for _low, guids in name_to_guids.items():
            if len(guids) <= 1:
                continue
            # Check if any pair is actually Tier-2 same person on same franchise slot; otherwise disambiguate!
            for idx, g in enumerate(guids):
                # Keep primary if only 1, else append subtle team/slot tag if truly distinct accounts
                obs_last = sorted(self._guid_obs[g], key=lambda x: x[0])[-1]
                tid = obs_last[2]
                # Check if they co-existed in the SAME season -> definitely 2 different people!
                seasons_g = {x[0] for x in self._guid_obs[g]}
                coexisted = any(
                    bool(seasons_g & {x[0] for x in self._guid_obs[other]})
                    for other in guids if other != g
                )
                if coexisted and idx > 0:
                    guid_latest_name[g] = f"{guid_latest_name[g]} (#{tid})" if tid else f"{guid_latest_name[g]} ({idx + 1})"

        # Step 2 (Tier 2 & Tier 3): Inspect franchise slot transitions `(yr_prev -> yr_next)` where GUID changed!
        guid_merge_target: Dict[str, str] = {}
        for tid, yr_map in self._slot_history.items():
            yrs = sorted(yr_map.keys())
            for i in range(len(yrs) - 1):
                y1, y2 = yrs[i], yrs[i + 1]
                g1, n1, tn1 = yr_map[y1]
                g2, n2, tn2 = yr_map[y2]
                if g1 == g2:
                    continue
                c1, c2 = guid_latest_name[g1], guid_latest_name[g2]
                # Ensure the two GUIDs never played AGAINST each other in the same season!
                s1 = {x[0] for x in self._guid_obs[g1]}
                s2 = {x[0] for x in self._guid_obs[g2]}
                if s1 & s2:
                    continue

                if c1.lower() == c2.lower() or _are_same_person(c1, c2):
                    # Tier 2: Same franchise slot, non-overlapping seasons, same person (e.g. Tom -> Tommy Ehrlich)
                    guid_merge_target[g1] = g2
                else:
                    # Tier 3: Check if suspiciously similar (same first name OR identical non-generic team name)
                    f1 = c1.split()[0].lower() if c1.split() else ""
                    f2 = c2.split()[0].lower() if c2.split() else ""
                    same_first = len(f1) >= 3 and f1 == f2
                    same_custom_team = (
                        bool(tn1 and tn2)
                        and tn1.lower() == tn2.lower()
                        and not tn1.lower().startswith("team ")
                    )
                    if same_first or same_custom_team:
                        reason = "same first name on same franchise slot" if same_first else f"identical team name '{tn1}'"
                        self._review_candidates.append({
                            "team_id": tid,
                            "from_season": y1,
                            "to_season": y2,
                            "prior_owner": c1,
                            "new_owner": c2,
                            "reason": reason,
                        })

        # Resolve transitive merges
        def _root_guid(g: str) -> str:
            seen_g = set()
            cur = g
            while cur in guid_merge_target and cur not in seen_g:
                seen_g.add(cur)
                cur = guid_merge_target[cur]
            return cur

        active_canon: Set[str] = set()
        for guid in self._guid_obs:
            rg = _root_guid(guid)
            canon = self._apply_explicit(guid_latest_name[rg])
            self._guid_to_canon[guid] = canon
            for _yr, nm in guid_all_names[guid]:
                if nm and nm != canon:
                    self._former_names[canon].add(nm)
            if guid in self._active_owners or rg in self._active_owners:
                active_canon.add(canon)

        self._active_owners = active_canon
        self._finalized = True

    def resolve(self, member_id: Optional[str], raw_name: str = "", *, season: int = 0, team_id: int = 0) -> str:
        """Returns the canonical owner name for a `(member_id, raw_name)` observation."""
        if not self._finalized:
            self.finalize()
        guid = str(member_id or "").strip()
        if guid and guid in self._guid_to_canon:
            return self._guid_to_canon[guid]
        norm_name = self._apply_explicit(self._norm(raw_name))
        if not norm_name:
            norm_name = f"Team {team_id} ({season})" if (team_id and season) else "Unknown"
        orphan_key = f"__orphan_{season}_{team_id}_{norm_name.lower()}__"
        if orphan_key in self._guid_to_canon:
            return self._guid_to_canon[orphan_key]
        return norm_name

    def is_active(self, canonical_owner: str) -> bool:
        if not self._finalized:
            self.finalize()
        return canonical_owner in self._active_owners

    def former_names_for(self, canonical_owner: str) -> List[str]:
        if not self._finalized:
            self.finalize()
        return sorted(self._former_names.get(canonical_owner, set()))

    def review_candidates(self) -> List[dict]:
        if not self._finalized:
            self.finalize()
        return list(self._review_candidates)
