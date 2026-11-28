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

    def test_mixed_snake_to_auction_rule_evolution_across_seasons(self):
        from FantasyRecap.multi_league_hub import score_draft_picks_multi_format

        # League ran 10-team SNAKE in 2023 (all bids = 0), then switched to 12-team AUCTION in 2026!
        rows = [
            # 2023 Snake Era
            (2023, 2, 1, "Alice", "2023 Rd1 Bust RB", "RB", 1, 20, 80.0, 0, 0),
            (2023, 12, 2, "Bob", "2023 Solid RB1", "RB", 4, 3, 220.0, 0, 0),
            (2023, 24, 3, "Carol", "2023 Solid RB2", "RB", 8, 5, 175.0, 0, 0),
            (2023, 36, 4, "Dave", "2023 Flex RB", "RB", 12, 10, 130.0, 0, 0),
            (2023, 95, 10, "Eve", "2023 Rd10 Snake Gem", "RB", 30, 1, 295.0, 0, 0),
            # 2026 Auction Era
            (2026, 1, 1, "Alice", "2026 $62 Auction Bust", "RB", 1, 24, 75.0, 62, 0),
            (2026, 10, 2, "Bob", "2026 $38 Solid RB", "RB", 4, 4, 215.0, 38, 0),
            (2026, 20, 3, "Carol", "2026 $20 RB2", "RB", 8, 6, 165.0, 20, 0),
            (2026, 30, 4, "Dave", "2026 $10 Depth RB", "RB", 12, 12, 125.0, 10, 0),
            (2026, 80, 8, "Frank", "2026 $2 Auction Gem", "RB", 28, 1, 305.0, 2, 0),
        ]
        drafts, seasons, _ = score_draft_picks_multi_format(
            rows,
            {"RB": 5},
            team_count=12,
            draft_type="AUCTION",
            season_draft_types={2023: "SNAKE", 2026: "AUCTION"},
            season_repl_ranks={2023: {"RB": 4}, 2026: {"RB": 5}},
        )
        self.assertEqual(seasons, [2026, 2023])
        # 2023 must remain SNAKE (bid=0), NOT corrupted by 2026 AUCTION!
        self.assertEqual(drafts["2023"]["modes"], ["SNAKE"])
        self.assertEqual(drafts["2023"]["steals"][0]["player"], "2023 Rd10 Snake Gem")
        self.assertEqual(drafts["2023"]["steals"][0]["bid"], 0)
        self.assertEqual(drafts["2023"]["busts"][0]["player"], "2023 Rd1 Bust RB")

        # 2026 must be AUCTION ($2 steal, $62 bust)
        self.assertEqual(drafts["2026"]["modes"], ["AUCTION"])
        self.assertEqual(drafts["2026"]["steals"][0]["player"], "2026 $2 Auction Gem")
        self.assertEqual(drafts["2026"]["steals"][0]["bid"], 2)
        self.assertEqual(drafts["2026"]["busts"][0]["player"], "2026 $62 Auction Bust")

        # All-Time ranks BOTH eras side-by-side on the unified +VORP scale!
        self.assertEqual(drafts["all"]["modes"], ["AUCTION", "SNAKE"])
        all_steal_names = {s["player"] for s in drafts["all"]["steals"]}
        self.assertIn("2023 Rd10 Snake Gem", all_steal_names)
        self.assertIn("2026 $2 Auction Gem", all_steal_names)

    def test_sleeper_zero_auth_config_parser_dynasty_superflex_tep_median_idp(self):
        from FantasyRecap.sleeper_adapter import _norm_pos, _parse_sleeper_league_config, _stable_int_pid

        self.assertEqual(_norm_pos("DEF"), "D/ST")
        self.assertEqual(_norm_pos("DE"), "DL")
        self.assertEqual(_stable_int_pid("4046"), 4046)
        self.assertGreater(_stable_int_pid("PHI"), 900_000_000)

        lg_raw = {
            "league_id": "1313213902351273984",
            "name": "Sunday Funday Dynasty",
            "season": "2026",
            "total_rosters": 12,
            "status": "in_season",
            "settings": {
                "type": 2,  # Dynasty
                "playoff_teams": 6,
                "playoff_week_start": 15,
                "playoff_round_type": 2,  # 2-wk rounds
                "playoff_seed_type": 0,   # Fixed bracket
                "league_average_match": 1,  # League Median!
                "divisions": 0,
                "leg": 12,
            },
            "scoring_settings": {"rec": 1.0, "bonus_rec_te": 0.5},
            "roster_positions": ["QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "SUPER_FLEX", "DL", "LB", "BN", "BN"],
        }
        users = [{"user_id": "u1", "display_name": "cooper powell", "metadata": {"team_name": "Gridiron Kings"}}]
        rosters = [{"roster_id": 1, "owner_id": "u1", "players": ["4046"], "starters": ["4046"], "settings": {}}]
        drafts = [{"draft_id": "d1", "type": "linear", "status": "complete"}]

        cfg = _parse_sleeper_league_config(lg_raw, users, rosters, drafts, "sf-dyn")
        self.assertEqual(cfg["format_label"], "Dynasty")
        self.assertEqual(cfg["draft_type"], "LINEAR")
        self.assertEqual(cfg["scoring_label"], "Full PPR + TEP")
        self.assertTrue(cfg["uses_league_median"])
        self.assertFalse(cfg["playoff_reseed"])
        self.assertEqual(cfg["playoff_round_weeks"], 2)
        self.assertEqual(cfg["active_positions"], ["QB", "RB", "WR", "TE", "DL", "LB"])  # No K / No D/ST!
        self.assertEqual(cfg["replacement_ranks"]["QB"], 21)  # 12 * (1 + 0.75) Superflex!
        self.assertEqual(cfg["teams_by_id"][1]["owner"], "Cooper Powell")


class TestLeagueIdentityResolver(unittest.TestCase):
    def test_manager_replacement_stays_strictly_separate_and_tags_alumni(self):
        from FantasyRecap.core.identity_resolver import LeagueIdentityResolver

        res = LeagueIdentityResolver()
        # Team 4 owned by John Doe (2022–2023), then replaced by Sarah Lee (2024–2026)
        res.observe(2022, "{GUID-JOHN}", "John Doe", team_id=4, team_name="John's Squad")
        res.observe(2023, "{GUID-JOHN}", "John Doe", team_id=4, team_name="John's Squad")
        res.observe(2024, "{GUID-SARAH}", "Sarah Lee", team_id=4, team_name="Sarah's Sharks")
        res.observe(2026, "{GUID-SARAH}", "Sarah Lee", team_id=4, team_name="Sarah's Sharks", is_current_season=True)
        res.finalize()

        self.assertEqual(res.resolve("{GUID-JOHN}", "John Doe", season=2022, team_id=4), "John Doe")
        self.assertEqual(res.resolve("{GUID-SARAH}", "Sarah Lee", season=2026, team_id=4), "Sarah Lee")
        self.assertFalse(res.is_active("John Doe"))  # Alumni!
        self.assertTrue(res.is_active("Sarah Lee"))  # Current manager!
        self.assertEqual(res.review_candidates(), [])

    def test_marriage_or_display_name_change_on_same_account_unifies_automatically(self):
        from FantasyRecap.core.identity_resolver import LeagueIdentityResolver

        res = LeagueIdentityResolver()
        # Emily gets married between 2023 and 2025 on the SAME ESPN/Sleeper account GUID
        res.observe(2022, "{GUID-EMILY}", "Emily Smith", team_id=2, team_name="Gridiron Queens")
        res.observe(2023, "{GUID-EMILY}", "Emily Smith", team_id=2, team_name="Gridiron Queens")
        res.observe(2025, "{GUID-EMILY}", "Emily Johnson", team_id=2, team_name="Gridiron Queens")
        res.observe(2026, "{GUID-EMILY}", "Emily Johnson", team_id=2, team_name="Gridiron Queens", is_current_season=True)
        res.finalize()

        # 2022 history automatically resolves to Emily Johnson, recording Emily Smith in former_names!
        self.assertEqual(res.resolve("{GUID-EMILY}", "Emily Smith", season=2022, team_id=2), "Emily Johnson")
        self.assertEqual(res.former_names_for("Emily Johnson"), ["Emily Smith"])
        self.assertTrue(res.is_active("Emily Johnson"))

    def test_duplicate_account_merges_and_ambiguous_takeover_flags_without_false_merge(self):
        from FantasyRecap.core.identity_resolver import LeagueIdentityResolver

        # Tier 2: Tom -> Tommy Ehrlich on new account, same franchise -> auto-merges!
        # Tier 3: Emily Smith (old GUID) -> Emily Davis (new GUID) on same franchise -> kept separate & flagged!
        res = LeagueIdentityResolver()
        res.observe(2022, "{GUID-TOM-1}", "Tom Ehrlich", team_id=7, team_name="Bears")
        res.observe(2026, "{GUID-TOM-2}", "Tommy Ehrlich", team_id=7, team_name="Bears", is_current_season=True)
        res.observe(2023, "{GUID-EM-1}", "Emily Smith", team_id=5, team_name="Victorious Secret")
        res.observe(2026, "{GUID-EM-2}", "Emily Davis", team_id=5, team_name="Victorious Secret", is_current_season=True)
        res.finalize()

        self.assertEqual(res.resolve("{GUID-TOM-1}", "Tom Ehrlich", season=2022, team_id=7), "Tommy Ehrlich")
        self.assertTrue(res.is_active("Tommy Ehrlich"))

        # Ambiguous Emily Smith vs Emily Davis on different GUIDs MUST NOT blindly merge!
        self.assertEqual(res.resolve("{GUID-EM-1}", "Emily Smith", season=2023, team_id=5), "Emily Smith")
        self.assertEqual(res.resolve("{GUID-EM-2}", "Emily Davis", season=2026, team_id=5), "Emily Davis")
        cands = res.review_candidates()
        self.assertEqual(len(cands), 1)
        self.assertEqual(cands[0]["prior_owner"], "Emily Smith")
        self.assertEqual(cands[0]["new_owner"], "Emily Davis")

        # When commissioner confirms via owner_aliases, they unify cleanly!
        res2 = LeagueIdentityResolver(owner_aliases={"Emily Smith": "Emily Davis"})
        res2.observe(2023, "{GUID-EM-1}", "Emily Smith", team_id=5, team_name="Victorious Secret")
        res2.observe(2026, "{GUID-EM-2}", "Emily Davis", team_id=5, team_name="Victorious Secret", is_current_season=True)
        res2.finalize()
        self.assertEqual(res2.resolve("{GUID-EM-1}", "Emily Smith", season=2023, team_id=5), "Emily Davis")

    def test_orphan_teams_across_different_seasons_do_not_collide(self):
        from FantasyRecap.core.identity_resolver import LeagueIdentityResolver

        res = LeagueIdentityResolver()
        res.observe(2023, None, "", team_id=3, team_name="")
        res.observe(2025, None, "", team_id=3, team_name="")
        res.finalize()
        o23 = res.resolve(None, "", season=2023, team_id=3)
        o25 = res.resolve(None, "", season=2025, team_id=3)
        self.assertNotEqual(o23, o25)


class TestPublicAPIAndAnonymizerSecurity(unittest.TestCase):
    def test_league_id_extractor_username_validator_and_bounded_rate_limiter(self):
        from FantasyRecap.core.security import (
            SlidingWindowRateLimiter,
            extract_league_id_from_input,
            validate_sleeper_username,
        )

        self.assertEqual(extract_league_id_from_input("755112359"), "755112359")
        self.assertEqual(
            extract_league_id_from_input("https://fantasy.espn.com/football/league?leagueId=755112359&seasonId=2026"),
            "755112359",
        )
        self.assertEqual(
            extract_league_id_from_input("https://sleeper.com/leagues/1313213902351273984/matchup"),
            "1313213902351273984",
        )
        for bad in ("../../etc/passwd", "http://169.254.169.254/leagues/abc", "javascript:alert(1)", ""):
            with self.assertRaises(ValueError):
                extract_league_id_from_input(bad)

        self.assertEqual(validate_sleeper_username("@Valid_User_99"), "Valid_User_99")
        for bad_u in ("../admin", "user?foo=1", "a" * 40, "bad space", ""):
            with self.assertRaises(ValueError):
                validate_sleeper_username(bad_u)

        now = [1000.0]
        lim = SlidingWindowRateLimiter(max_requests=2, window_seconds=60.0, max_keys=64, clock=lambda: now[0])
        self.assertEqual(lim.allow("1.2.3.4"), (True, 0))
        self.assertEqual(lim.allow("1.2.3.4"), (True, 0))
        allowed, retry = lim.allow("1.2.3.4")
        self.assertFalse(allowed)
        self.assertGreaterEqual(retry, 1)
        now[0] += 61.0
        self.assertEqual(lim.allow("1.2.3.4"), (True, 0))

        # Memory-bound check against spoofed-IP flood
        for idx in range(200):
            lim.allow(f"10.0.0.{idx}")
        self.assertLessEqual(len(lim._hits), 64)

    def test_public_api_confused_deputy_rate_limit_singleflight_and_owasp_headers(self):
        import gzip
        import json
        import threading
        import time
        import urllib.error
        import urllib.request
        import requests
        from FantasyRecap.public_api_server import PublicAPIService, create_server

        espn_calls = []
        sleeper_calls = []

        def fake_espn_builder(lid, slug, season, **kwargs):
            espn_calls.append((lid, kwargs))
            if lid == "157057":
                resp = requests.Response()
                resp.status_code = 401
                raise requests.HTTPError("401 Unauthorized", response=resp)
            return {"league_config": {"name": "Public ESPN", "badge": "ESP"}, "teams": []}, None

        def fake_sleeper_builder(lid, slug, **kwargs):
            sleeper_calls.append((lid, kwargs))
            time.sleep(0.06)
            return {"league_config": {"name": f"Sleeper {lid}", "badge": "SLP"}, "teams": []}, None

        svc = PublicAPIService(
            max_builds_per_min_ip=2,
            max_lookups_per_min_ip=3,
            max_concurrent_builds=2,
            sleeper_builder=fake_sleeper_builder,
            espn_builder=fake_espn_builder,
            sleeper_user_lookup=lambda u, yr: {"username": u, "season": yr, "leagues": [{"league_id": "111", "name": "Test"}]},
        )
        srv = create_server(host="127.0.0.1", port=0, service=svc)
        port = srv.server_address[1]
        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()
        base = f"http://127.0.0.1:{port}"
        try:
            # 1. OWASP headers + POST blocked (405) + personal `hub_data.json` blocked (404)!
            r_health = urllib.request.urlopen(f"{base}/api/v1/health")
            self.assertEqual(r_health.headers.get("X-Content-Type-Options"), "nosniff")
            self.assertEqual(r_health.headers.get("X-Frame-Options"), "DENY")

            req_post = urllib.request.Request(f"{base}/api/v1/health", data=b"{}", method="POST")
            with self.assertRaises(urllib.error.HTTPError) as ctx_post:
                urllib.request.urlopen(req_post)
            self.assertEqual(ctx_post.exception.code, 405)

            for forbidden_path in ("/hub_data.json", "/../.env", "/leagues/jff/hub_data.json"):
                with self.assertRaises(urllib.error.HTTPError) as ctx_404:
                    urllib.request.urlopen(f"{base}{forbidden_path}")
                self.assertEqual(ctx_404.exception.code, 404)

            # 2. Confused-Deputy proof: private ESPN league returns 403 and strictly sets public_only=True & ephemeral=True
            with self.assertRaises(urllib.error.HTTPError) as ctx_priv:
                urllib.request.urlopen(f"{base}/api/v1/hub/espn/157057")
            self.assertEqual(ctx_priv.exception.code, 403)
            err_body = json.loads(ctx_priv.exception.read().decode("utf-8"))
            self.assertEqual(err_body["error"], "private_league")
            self.assertTrue(espn_calls[0][1]["public_only"])
            self.assertTrue(espn_calls[0][1]["ephemeral"])

            # 3. Singleflight coalescing + gzip edge-cacheable response!
            results = []
            def _fetch_slp():
                req = urllib.request.Request(
                    f"{base}/api/v1/hub/sleeper/999001",
                    headers={"Accept-Encoding": "gzip", "CF-Connecting-IP": "203.0.113.10"},
                )
                with urllib.request.urlopen(req) as resp:
                    raw = gzip.decompress(resp.read()) if resp.headers.get("Content-Encoding") == "gzip" else resp.read()
                    results.append((resp.status, resp.headers.get("Cache-Control"), json.loads(raw)))

            threads = [threading.Thread(target=_fetch_slp) for _ in range(3)]
            for th in threads:
                th.start()
            for th in threads:
                th.join()
            self.assertEqual(len(results), 3)
            self.assertEqual(len(sleeper_calls), 1)  # Coalesced 3 concurrent misses into 1 upstream build!
            self.assertIn("s-maxage=900", results[0][1])

            # 4. Cached HITs do not burn rate limit, whereas uncached floods trigger HTTP 429 Retry-After!
            for _ in range(5):
                req_hit = urllib.request.Request(f"{base}/api/v1/hub/sleeper/999001", headers={"CF-Connecting-IP": "198.51.100.7"})
                with urllib.request.urlopen(req_hit) as r_hit:
                    self.assertEqual(r_hit.headers.get("X-Hub-Cache"), "HIT")

            for lid_miss in ("8001", "8002"):
                req_m = urllib.request.Request(f"{base}/api/v1/hub/sleeper/{lid_miss}", headers={"CF-Connecting-IP": "198.51.100.7"})
                urllib.request.urlopen(req_m).read()
            with self.assertRaises(urllib.error.HTTPError) as ctx_429:
                req_flood = urllib.request.Request(f"{base}/api/v1/hub/sleeper/8003", headers={"CF-Connecting-IP": "198.51.100.7"})
                urllib.request.urlopen(req_flood)
            self.assertEqual(ctx_429.exception.code, 429)
            self.assertIsNotNone(ctx_429.exception.headers.get("Retry-After"))
        finally:
            srv.shutdown()
            srv.server_close()

    def test_bfl_anonymizer_zero_pii_and_h2h_key_invariants(self):
        from pathlib import Path
        import tempfile
        from FantasyRecap.anonymize_bfl_demo import build_anonymized_demo

        src = Path(__file__).resolve().parent.parent / "web_hub" / "hub_data.json"
        if not src.exists():
            self.skipTest("Local BFL hub_data.json not present in CI")
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "demo_hub_data.json"
            demo = build_anonymized_demo(src_path=src, out_path=out)
            self.assertTrue(demo["league_config"]["is_demo"])
            self.assertEqual(demo["podcast"]["episodes"], [])
            self.assertEqual(demo["podcast"]["rss_url"], "")
            for key, rec in demo["history"]["h2h"].items():
                o1, o2 = key.split("|||")
                self.assertLessEqual(o1, o2)
                self.assertEqual(rec["o1"], o1)
                self.assertEqual(rec["o2"], o2)

    def test_dynasty_pick_vorp_monotonicity_superflex_boost_and_time_discount(self):
        from FantasyRecap.sleeper_adapter import estimate_dynasty_pick_vorp, slot_tier_from_slot

        self.assertEqual(slot_tier_from_slot(1, 12), "early")
        self.assertEqual(slot_tier_from_slot(4, 12), "early")
        self.assertEqual(slot_tier_from_slot(6, 12), "mid")
        self.assertEqual(slot_tier_from_slot(12, 12), "late")

        r1_early_1qb = estimate_dynasty_pick_vorp(1, "early", is_superflex=False, years_out=1)
        r1_early_sf = estimate_dynasty_pick_vorp(1, "early", is_superflex=True, years_out=1)
        r1_mid_sf = estimate_dynasty_pick_vorp(1, "mid", is_superflex=True, years_out=1)
        r1_late_sf = estimate_dynasty_pick_vorp(1, "late", is_superflex=True, years_out=1)
        r2_mid_sf = estimate_dynasty_pick_vorp(2, "mid", is_superflex=True, years_out=1)
        r3_mid_sf = estimate_dynasty_pick_vorp(3, "mid", is_superflex=True, years_out=1)
        r1_2yrs_out = estimate_dynasty_pick_vorp(1, "mid", is_superflex=True, years_out=2)

        self.assertGreater(r1_early_sf, r1_early_1qb)
        self.assertGreater(r1_early_sf, r1_mid_sf)
        self.assertGreater(r1_mid_sf, r1_late_sf)
        self.assertGreater(r1_late_sf, r2_mid_sf)
        self.assertGreater(r2_mid_sf, r3_mid_sf)
        self.assertGreater(r3_mid_sf, 0.0)
        self.assertGreater(r1_mid_sf, r1_2yrs_out)


if __name__ == "__main__":
    unittest.main()
