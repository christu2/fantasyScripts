import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from FantasyRecap.core.espn_client import ESPNClientError, fetch_json
from FantasyRecap.core.manifest import (
    get_championships,
    get_discord_id_to_owner,
    get_divisions,
    get_league_config,
    get_owner_profiles,
    get_owner_to_division,
    get_team_details,
    standardize_name,
)


class TestManifestAndIdentity(unittest.TestCase):
    def test_league_config_canonical_values(self):
        cfg = get_league_config()
        self.assertEqual(cfg["league_id"], 1693766)
        self.assertIn("157057", cfg["legacy_league_ids"])
        self.assertEqual(cfg["regular_season_weeks"], 14)
        self.assertEqual(cfg["total_playoff_teams"], 7)

    def test_sixteen_teams_four_by_four_divisions(self):
        divs = get_divisions()
        self.assertEqual(set(divs.keys()), {"North", "South", "East", "West"})
        for d, members in divs.items():
            self.assertEqual(len(members), 4, f"Division {d} should have 4 members")
        self.assertEqual(len(get_owner_to_division()), 16)
        self.assertEqual(len(get_team_details()), 16)
        self.assertEqual(len(get_owner_profiles()), 16)

    def test_standardize_name_synonyms_and_rej_case(self):
        self.assertEqual(standardize_name("Thomas Ehrlich"), "Tommy Ehrlich")
        self.assertEqual(standardize_name("dan kruszewski"), "Daniel Kruszewski")
        self.assertEqual(standardize_name("sydney kite"), "Sydney Miller")
        self.assertEqual(standardize_name("REJ HOXHA"), "rej hoxha")
        self.assertEqual(standardize_name("rej hoxha"), "rej hoxha")
        self.assertEqual(standardize_name(""), "Unknown")

    def test_discord_id_mapping_and_championships(self):
        dmap = get_discord_id_to_owner()
        self.assertEqual(dmap["1540793260610560070"], "Nick Christus")
        self.assertEqual(dmap["1320585267849199619"], "rej hoxha")
        champs = get_championships()
        self.assertGreaterEqual(len(champs), 18)
        self.assertEqual(champs[0]["year"], 2008)
        self.assertEqual(champs[-1]["year"], 2025)


class TestESPNClientResilience(unittest.TestCase):
    @patch("FantasyRecap.core.espn_client.time.sleep", return_value=None)
    @patch("FantasyRecap.core.espn_client.get_http_session")
    def test_retries_transient_502_then_succeeds(self, mock_session_fn, _mock_sleep):
        r502 = MagicMock(status_code=502)
        r200 = MagicMock(status_code=200)
        r200.json.return_value = {"ok": True}
        session = MagicMock()
        session.get.side_effect = [r502, r200]
        mock_session_fn.return_value = session

        res = fetch_json("https://example.test/api", max_attempts=3)
        self.assertEqual(res, {"ok": True})
        self.assertEqual(session.get.call_count, 2)

    @patch("FantasyRecap.core.espn_client.time.sleep", return_value=None)
    @patch("FantasyRecap.core.espn_client.get_http_session")
    def test_raises_espn_client_error_instead_of_sys_exit(self, mock_session_fn, _mock_sleep):
        r503 = MagicMock(status_code=503)
        session = MagicMock()
        session.get.return_value = r503
        mock_session_fn.return_value = session

        with self.assertRaises(ESPNClientError) as ctx:
            fetch_json("https://example.test/api", max_attempts=2)
        self.assertEqual(ctx.exception.status_code, 503)


if __name__ == "__main__":
    unittest.main()
