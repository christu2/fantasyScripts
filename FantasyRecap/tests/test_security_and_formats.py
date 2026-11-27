"""
OWASP Top 10 Security & Multi-Format Fantasy League Contract Tests
==================================================================
Verifies:
  1. OWASP input & SSRF guards (`validate_league_id`, `validate_slug`,
     `validate_webhook_url`, `safe_http_url`, `generate_unguessable_slug`).
  2. Multi-format league engines:
     - Auction Draft VORP ($3 steal vs $65 bust)
     - Keeper/Dynasty pick exclusion from redraft steals
     - Superflex (OP) & IDP (DL/LB/DB) dynamic replacement ranks
     - HTML/OpenGraph XSS escaping in `render_league_index_html()`
"""

import tempfile
import unittest
from pathlib import Path


class TestOWASPSecurityGuards(unittest.TestCase):
    def test_league_id_and_slug_block_traversal_and_ssrf(self):
        from FantasyRecap.core.security import (
            generate_unguessable_slug,
            safe_http_url,
            validate_league_id,
            validate_slug,
            validate_webhook_url,
        )

        self.assertEqual(validate_league_id("755112359"), "755112359")
        for bad_lid in ("", "../etc/passwd", "157057?admin=1", "169.254.169.254", "abc123", "1" * 25):
            with self.assertRaises(ValueError):
                validate_league_id(bad_lid)

        self.assertEqual(validate_slug("jff-2026"), "jff-2026")
        for bad_slug in ("", "a", "../bfl", "jff/evil", "JFF SPACE", "bad<script>"):
            with self.assertRaises(ValueError):
                validate_slug(bad_slug)

        # SSRF webhook allowlist
        self.assertTrue(validate_webhook_url("https://discord.com/api/webhooks/12345/abcdef").startswith("https://"))
        self.assertTrue(validate_webhook_url("https://hooks.slack.com/services/T0/B0/XX").startswith("https://"))
        for bad_hook in (
            "http://discord.com/api/webhooks/1/2",  # non-HTTPS
            "https://169.254.169.254/latest/meta-data/",
            "https://evil.example.com/api/webhooks/1/2",
            "https://discord.com/api/v10/users/@me",
        ):
            with self.assertRaises(ValueError):
                validate_webhook_url(bad_hook)

        # Safe URL blocks javascript: and data: URIs
        self.assertEqual(safe_http_url("https://storage.googleapis.com/bfl-podcast-7x9k2/ep.mp3"), "https://storage.googleapis.com/bfl-podcast-7x9k2/ep.mp3")
        self.assertEqual(safe_http_url("javascript:alert(1)"), "")
        self.assertEqual(safe_http_url("data:text/html,<script>alert(1)</script>"), "")

        tok = generate_unguessable_slug("Just for Fun!")
        self.assertTrue(tok.startswith("just-for-fun-"))
        self.assertEqual(validate_slug(tok), tok)

    def test_render_league_index_html_escapes_xss_in_league_metadata(self):
        from FantasyRecap.multi_league_hub import render_league_index_html

        malicious_hub = {
            "league_config": {
                "name": 'Evil <script>alert(1)</script> "League"',
                "badge": "<IMG>",
                "scoring_label": "Full PPR",
            },
            "tiebreaker_rules": {"playoff_teams": 6},
            "teams": [{"owner": "A"}] * 10,
        }
        with tempfile.TemporaryDirectory() as tmp:
            idx = render_league_index_html(malicious_hub, Path(tmp))
            html_out = idx.read_text(encoding="utf-8")
            self.assertNotIn("<script>alert(1)</script>", html_out)
            self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", html_out)
            self.assertIn("Content-Security-Policy", html_out)


class TestMultiFormatLeagueSupport(unittest.TestCase):
    def test_auction_draft_vorp_and_keeper_exclusion(self):
        from FantasyRecap.multi_league_hub import score_draft_picks_multi_format

        # Format: (season, overall_pick, round_id, owner, player_name, position, pos_draft_rank, pos_finish_rank, season_points, bid_amount, is_keeper)
        rows = [
            # Expensive $65 bust WR
            (2026, 1, 1, "Alice", "Bust Star WR", "WR", 1, 25, 95.0, 65, 0),
            # Cheap $3 breakout WR -> must be top Auction Steal!
            (2026, 40, 5, "Bob", "Waiver Gem WR", "WR", 20, 1, 285.0, 3, 0),
            # Mid-tier WRs to form replacement baseline
            (2026, 10, 2, "Carol", "Solid WR1", "WR", 3, 4, 210.0, 35, 0),
            (2026, 20, 3, "Dave", "Solid WR2", "WR", 6, 8, 170.0, 22, 0),
            (2026, 30, 4, "Eve", "Flex WR", "WR", 12, 15, 120.0, 10, 0),
            # Keeper $2 stud -> MUST be excluded from draft steals ranking!
            (2026, 50, 10, "Frank", "Kept Dynasty WR", "WR", 25, 2, 275.0, 2, 1),
        ]
        drafts, seasons, scored_dp = score_draft_picks_multi_format(rows, {"WR": 5}, team_count=10, draft_type="AUCTION")
        self.assertEqual(seasons, [2026])
        self.assertEqual(len(scored_dp), 5)  # 6th row was keeper -> excluded!
        steals = drafts["2026"]["steals"]
        busts = drafts["2026"]["busts"]
        self.assertTrue(steals)
        self.assertEqual(steals[0]["player"], "Waiver Gem WR")
        self.assertEqual(steals[0]["bid"], 3)
        # Kept player must NOT show up as a draft steal
        self.assertFalse(any(s["player"] == "Kept Dynasty WR" for s in steals))
        self.assertTrue(busts)
        self.assertEqual(busts[0]["player"], "Bust Star WR")
        self.assertEqual(busts[0]["bid"], 65)

    def test_superflex_and_idp_dynamic_replacement_ranks(self):
        from FantasyRecap.multi_league_hub import compute_dynamic_replacement_ranks

        # 12-team Superflex + IDP league
        ranks = compute_dynamic_replacement_ranks(
            12,
            {"QB": 1, "OP": 1, "RB": 2, "WR": 3, "TE": 1, "FLEX": 2, "DL": 2, "LB": 2, "DB": 2},
        )
        # Superflex pushes QB replacement from 12 -> 21!
        self.assertEqual(ranks["QB"], 21)
        self.assertEqual(ranks["DL"], 24)
        self.assertEqual(ranks["LB"], 24)
        self.assertEqual(ranks["DB"], 24)

    def test_draft_picks_schema_columns_match_multi_league_query(self):
        import sqlite3
        from FantasyRecap.bfl_history_db import init_db

        conn = sqlite3.connect(":memory:")
        init_db(conn)
        rows = conn.execute(
            "SELECT season, overall_pick, round_id, owner, player_name, position, pos_draft_rank, pos_finish_rank, season_points, bid_amount, keeper FROM draft_picks WHERE position IN ('QB','RB','WR','TE')"
        ).fetchall()
        self.assertEqual(rows, [])
        conn.close()


if __name__ == "__main__":
    unittest.main()
