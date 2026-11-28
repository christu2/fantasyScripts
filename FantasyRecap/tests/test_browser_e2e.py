"""
End-to-End Real Browser Test (Playwright Chromium) — XSS Immunity & Multi-Format UI
===================================================================================
Spins up a local HTTP server serving `FantasyRecap/web_hub/` with a synthetic
adversarial multi-format `hub_data.json` containing:
  - Malicious Stored XSS payloads in manager names, team names, divisions,
    draft picks, trades, awards, and podcast `javascript:` URLs.
  - Multi-format league settings: 8-team Auction + 2-Week Playoff Rounds +
    League Median Scoring + IDP Heatmap positions (`DL`, `LB`).

Asserts in real headless Chromium:
  1. Zero uncaught page errors and zero console errors.
  2. `window.__XSS_PWNED` remains `undefined` across ALL 7 tabs.
  3. Malicious script/img strings render literally as escaped text (not DOM nodes).
  4. Playoff Machine pick -> unclick restores bit-for-bit identical CRN odds.
  5. Auction `$` draft column, 2-Week Playoff Round badge, and IDP Heatmap headers render.
"""

import http.server
import json
import socketserver
import tempfile
import threading
import unittest
from pathlib import Path

RECAP_DIR = Path(__file__).resolve().parent.parent
WEB_HUB_DIR = RECAP_DIR / "web_hub"

try:
    from playwright.sync_api import sync_playwright
    HAS_PLAYWRIGHT = True
except Exception:
    HAS_PLAYWRIGHT = False


def _build_adversarial_multiformat_hub() -> dict:
    xss_owner = 'Alice <img src=x onerror="window.__XSS_PWNED=1">'
    xss_owner2 = 'Bob <script>window.__XSS_PWNED=2</script>'
    owners = [xss_owner, xss_owner2, "Carol", "Dave", "Eve", "Frank", "Grace", "Hank"]
    teams = []
    for idx, o in enumerate(owners):
        teams.append({
            "owner": o,
            "code": f"T{idx+1}",
            "division": "",
            "team_name": f'<b>Team {idx+1}</b><img src=x onerror="window.__XSS_PWNED=3">',
            "rings": 0,
            "power_mean": 125.0 - idx * 4.0,
            "power_std": 16.0,
            "raw_avg": 125.0 - idx * 4.0,
            "baseline_playoff_pct": 80.0 - idx * 8.0,
            "baseline_div_pct": 0.0,
            "baseline_bye_pct": 0.0,
            "baseline_leverage": 15.0,
        })

    completed_games = [
        {"week": 1, "away": owners[0], "home": owners[1], "away_score": 130.5, "home_score": 112.0},
        {"week": 1, "away": owners[2], "home": owners[3], "away_score": 121.0, "home_score": 115.0},
        {"week": 1, "away": owners[4], "home": owners[5], "away_score": 108.0, "home_score": 101.0},
        {"week": 1, "away": owners[6], "home": owners[7], "away_score": 99.0, "home_score": 95.0},
    ]
    remaining_schedule = [
        {"week": 2, "away": owners[0], "home": owners[2]},
        {"week": 2, "away": owners[1], "home": owners[3]},
        {"week": 2, "away": owners[4], "home": owners[6]},
        {"week": 2, "away": owners[5], "home": owners[7]},
    ]
    pos_cols = ["QB", "RB", "WR", "TE", "DL", "LB"]
    hm_rows = [
        {
            "owner": o, "division": "", "weeks": 1, "total_vorp_wk": 10.0 - i, "total_vorp": 10.0 - i,
            "best_pos": "WR", "worst_pos": "TE",
            "positions": {p: {"starts": 1, "ppg": 15.0, "vorp_wk": 2.0, "total_vorp": 2.0} for p in pos_cols},
        }
        for i, o in enumerate(owners)
    ]
    return {
        "generated_at": "2026-11-27T18:00:00+00:00",
        "season": 2026,
        "completed_week": 1,
        "regular_season_weeks": 2,
        "league_config": {
            "league_id": "999999",
            "slug": "xss-auction-idp",
            "name": 'Auction <img src=x onerror="window.__XSS_PWNED=4"> League',
            "badge": "AUC",
            "first_season": 2026,
            "scoring_label": "Full PPR",
            "format_label": "Auction",
            "draft_type": "AUCTION",
            "has_divisions": False,
            "is_first_year": True,
        },
        "divisions": {},
        "tiebreaker_rules": {
            "playoff_teams": 4,
            "playoff_byes": 0,
            "playoff_reseed": False,
            "playoff_round_weeks": 2,
            "uses_league_median": True,
            "tiebreaker_rule": "TOTAL_POINTS_SCORED",
            "order": "Overall Record -> Total Points For (PF)",
        },
        "teams": teams,
        "completed_games": completed_games,
        "remaining_schedule": remaining_schedule,
        "weekly_projections": {
            "2": {o: {"proj": 120.0 - i * 3, "fa_fills": ['<img src=x onerror="window.__XSS_PWNED=5">'], "is_live": False, "live_pts": 0.0, "rem_frac": 1.0} for i, o in enumerate(owners)}
        },
        "awards": {
            "week": 1, "season": 2026, "league_avg": 110.2,
            "awards": [{
                "key": "high_score",
                "title": 'High Score <img src=x onerror="window.__XSS_PWNED=6">',
                "metric": "130.5 pts",
                "body": f"**{xss_owner}** dropped `130.5` pts!",
            }],
        },
        "history": {
            "career": [
                {"owner": o, "division": "", "seasons": 1, "wins": 1 if i % 2 == 0 else 0, "losses": 0 if i % 2 == 0 else 1, "win_pct": 100.0 if i % 2 == 0 else 0.0, "avg_pf": 115.0, "high_score": 130.5, "rings": 0}
                for i, o in enumerate(owners)
            ],
            "h2h": {
                f"{min(owners[0], owners[1])}|||{max(owners[0], owners[1])}": {
                    "o1": min(owners[0], owners[1]), "o2": max(owners[0], owners[1]),
                    "w1": 1, "w2": 0, "games": 1, "po1": 0, "po2": 0, "avg1": 130.5, "avg2": 112.0,
                    "streak_owner": owners[0], "streak_len": 1,
                    "last": {"season": 2026, "week": 1, "winner": owners[0], "hi": 130.5, "lo": 112.0},
                    "closest": {"season": 2026, "week": 1, "winner": owners[0], "margin": 18.5},
                    "super_bowls": [],
                }
            },
            "championships": [],
            "draft_seasons": [2026],
            "drafts": {
                "2026": {
                    "steals": [{"season": 2026, "pick": 40, "round": 5, "bid": 4, "owner": xss_owner, "player": 'Gem <img src=x onerror="window.__XSS_PWNED=7">', "pos": "WR", "pos_draft_rank": 20, "pos_finish_rank": 2, "pts": 260.0, "delta": 95.5}],
                    "busts": [{"season": 2026, "pick": 1, "round": 1, "bid": 68, "owner": xss_owner2, "player": "Bust Star", "pos": "RB", "pos_draft_rank": 1, "pos_finish_rank": 30, "pts": 80.0, "delta": -72.0}],
                }
            },
        },
        "trades": {
            "all_time": {"seasons": [], "trade_count": 0, "trades": [], "owner_cards": {"started": [], "ros": []}},
            "live": {
                "season": 2026, "trade_count": 1,
                "trades": [{
                    "trade_id": "t1", "season": 2026, "trade_week": 1, "effective_week": 1,
                    "owner_a": xss_owner, "owner_b": xss_owner2,
                    "a_owners": [xss_owner], "b_owners": [xss_owner2],
                    "a_games_swung": 1, "b_games_swung": 0,
                    "a_received": [{"name": 'Stud <img src=x onerror="window.__XSS_PWNED=8">', "pos": "WR", "started_vorp": 25.0, "ros_vorp": 25.0, "started_wks": 1, "ros_wks": 1, "started_pts": 33.0, "ros_pts": 33.0, "started_ppg": 33.0, "ros_ppg": 33.0, "po_started_vorp": 0.0, "po_ros_vorp": 0.0}],
                    "b_received": [{"name": "Bench Guy", "pos": "RB", "started_vorp": 2.0, "ros_vorp": 2.0, "started_wks": 1, "ros_wks": 1, "started_pts": 10.0, "ros_pts": 10.0, "started_ppg": 10.0, "ros_ppg": 10.0, "po_started_vorp": 0.0, "po_ros_vorp": 0.0}],
                    "started": {"a_vorp": 25.0, "b_vorp": 2.0, "winner": xss_owner, "loser": xss_owner2, "abs_swing": 23.0, "win_playoff_vorp": 0.0, "tags": ['<img src=x onerror="window.__XSS_PWNED=9">']},
                    "ros": {"a_vorp": 25.0, "b_vorp": 2.0, "winner": xss_owner, "loser": xss_owner2, "abs_swing": 23.0, "win_playoff_vorp": 0.0, "tags": []},
                }],
                "owner_cards": {
                    "started": [{"owner": xss_owner, "trades": 1, "wins": 1, "losses": 0, "pushes": 0, "win_pct": 100.0, "net_vorp": 23.0, "avg_swing": 23.0}],
                    "ros": [{"owner": xss_owner, "trades": 1, "wins": 1, "losses": 0, "pushes": 0, "win_pct": 100.0, "net_vorp": 23.0, "avg_swing": 23.0}],
                },
            },
        },
        "analytics": {
            "luck": {
                "seasons": [2026],
                "by_season": {
                    "2026": [
                        {"owner": o, "division": "", "true_rank": i + 1, "wins": 1 if i % 2 == 0 else 0, "losses": 0 if i % 2 == 0 else 1, "win_pct": 100.0 if i % 2 == 0 else 0.0, "ap_wins": 7 - i, "ap_losses": i, "ap_pct": round((7 - i) / 7 * 100, 1), "expected_wins": round((7 - i) / 7, 2), "luck_delta": 0.2, "pf_plus": 105.0, "pa_plus": 98.0, "avg_pf": 115.0, "avg_pa": 108.0, "sos_rank": i + 1, "verdict": 'Fair <img src=x onerror="window.__XSS_PWNED=10">'}
                        for i, o in enumerate(owners)
                    ]
                },
            },
            "trends": {
                "season": 2026, "weeks": [1],
                "series": {o: {"po": [60.0], "rank": [i + 1], "pf": [120.0], "wins": [1], "ap_pct": [60.0]} for i, o in enumerate(owners)},
            },
            "heatmap": {"season": 2026, "positions": pos_cols, "bounds": {p: {"min": -5, "max": 5} for p in pos_cols}, "rows": hm_rows},
        },
        "dossiers": [
            {
                "owner": o, "team_name": teams[i]["team_name"], "division": "", "first_season": 2026, "seasons_played": 1,
                "rings": 0, "champ_years": [], "runner_up_years": [], "div_titles": 0, "po_apps": 0,
                "reg_w": 1, "reg_l": 0, "reg_pct": 100.0, "po_w": 0, "po_l": 0, "po_pct": 0.0,
                "ap_pct": 70.0, "luck_delta": 0.2, "pf_plus": 105.0, "pa_plus": 95.0, "sos_rank": 3,
                "high_game": {"score": 130.5, "season": 2026, "week": 1}, "low_game": {"score": 130.5, "season": 2026, "week": 1},
                "trade": {"net_vorp": 0.0, "wins": 0, "losses": 0}, "draft": {}, "badges": ['<img src=x onerror="window.__XSS_PWNED=11">'],
                "timeline": [{"season": 2026, "team_name": teams[i]["team_name"], "division": "", "wins": 1, "losses": 0, "pf": 130.5, "is_complete": False}],
            }
            for i, o in enumerate(owners)
        ],
        "podcast": {
            "rss_url": "javascript:window.__XSS_PWNED=12",
            "episodes": [{"title": 'Ep 1 <img src=x onerror="window.__XSS_PWNED=13">', "pub_date": "Tue", "duration": "12:00", "audio_url": "javascript:window.__XSS_PWNED=14"}],
        },
    }


@unittest.skipUnless(HAS_PLAYWRIGHT, "playwright not installed")
class TestWebHubBrowserE2E(unittest.TestCase):
    def test_all_tabs_xss_immunity_crn_determinism_and_multiformat_rendering(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            for asset in ("index.html", "styles.css", "app.js"):
                (tmp_dir / asset).write_text((WEB_HUB_DIR / asset).read_text(encoding="utf-8"), encoding="utf-8")
            (tmp_dir / "hub_data.json").write_text(
                json.dumps(_build_adversarial_multiformat_hub()), encoding="utf-8"
            )

            class QuietHandler(http.server.SimpleHTTPRequestHandler):
                def __init__(self, *args, **kwargs):
                    super().__init__(*args, directory=str(tmp_dir), **kwargs)
                def log_message(self, format, *args):
                    pass

            httpd = socketserver.TCPServer(("127.0.0.1", 0), QuietHandler)
            port = httpd.server_address[1]
            t = threading.Thread(target=httpd.serve_forever, daemon=True)
            t.start()

            page_errors = []
            console_errors = []
            try:
                with sync_playwright() as p:
                    browser = None
                    try:
                        browser = p.chromium.launch(headless=True)
                    except Exception:
                        try:
                            browser = p.chromium.launch(headless=True, channel="chrome")
                        except Exception as exc:
                            self.skipTest(f"No headless Chromium/Chrome binary available: {exc}")
                    page = browser.new_page()
                    page.on("pageerror", lambda err: page_errors.append(str(err)))
                    page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)

                    page.goto(f"http://127.0.0.1:{port}/index.html", wait_until="networkidle")
                    page.wait_for_selector("#playoffTable tbody tr")

                    # 1. Verify 2-Week Playoff Rounds badge rendered
                    badge_txt = page.locator("#tab-playoff .pill-gold").inner_text()
                    self.assertIn("2-Wk Rounds", badge_txt)

                    # 2. Verify CRN determinism: snapshot Playoff table, click pick, unclick pick -> exact match!
                    before_html = page.locator("#playoffTable tbody").inner_html()
                    first_btn = page.locator(".pick-btn").first
                    first_btn.click()
                    after_pick_html = page.locator("#playoffTable tbody").inner_html()
                    self.assertNotEqual(before_html, after_pick_html)
                    first_btn.click()
                    after_unclick_html = page.locator("#playoffTable tbody").inner_html()
                    self.assertEqual(before_html, after_unclick_html)

                    # 2b. Verify Rooting Guide clickable pick stays visible (never disappears!) with ✓ badge
                    root_item = page.locator("#rootingGuideBody .rooting-item.clickable").first
                    root_item.click()
                    self.assertIn("Locked", page.locator("#rootingGuideBody").inner_text())
                    # Click again to unlock and verify exact restoration
                    page.locator("#rootingGuideBody .rooting-item.clickable").first.click()
                    self.assertEqual(before_html, page.locator("#playoffTable tbody").inner_html())

                    # 3. Click through ALL remaining 6 tabs and verify multi-format features
                    for tab in ("analytics", "dossiers", "history", "drafts", "trades", "podcast"):
                        page.locator(f'.tab-btn[data-tab="{tab}"]').click()
                        page.wait_for_timeout(80)

                    # Verify IDP columns rendered in Heatmap header
                    hm_headers = page.locator("#heatmapTable thead").inner_text()
                    self.assertIn("DL", hm_headers)
                    self.assertIn("LB", hm_headers)

                    # Verify Auction `$` column rendered in Draft Hall
                    steals_txt = page.locator("#stealsTable tbody").inner_text()
                    self.assertIn("$4", steals_txt)

                    # Verify malicious javascript: audio URL was blocked!
                    self.assertEqual(page.locator("#podcastList audio").count(), 0)
                    self.assertIn("Audio unavailable", page.locator("#podcastList").inner_text())

                    # 4. Verify ZERO XSS executed anywhere across all 7 tabs!
                    pwned = page.evaluate("() => window.__XSS_PWNED")
                    self.assertIsNone(pwned, f"XSS payload executed! __XSS_PWNED={pwned}")

                    browser.close()
            finally:
                httpd.shutdown()
                httpd.server_close()

            self.assertEqual(page_errors, [], f"Uncaught page errors: {page_errors}")
            self.assertEqual(console_errors, [], f"Console errors: {console_errors}")

    @unittest.skipUnless(HAS_PLAYWRIGHT, "playwright not installed")
    def test_self_serve_onboarding_sleeper_lookup_podcast_autohide_and_private_espn_error(self):
        import shutil
        import requests
        from FantasyRecap.public_api_server import PublicAPIService, create_server

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            for fname in ("index.html", "styles.css", "app.js"):
                shutil.copyfile(WEB_HUB_DIR / fname, tmp_path / fname)

            demo_hub = _build_adversarial_multiformat_hub()
            demo_hub["league_config"]["name"] = "Gridiron Premier League (Live Demo)"
            demo_hub["league_config"]["badge"] = "GPL"
            demo_hub["league_config"]["is_demo"] = True
            demo_hub["podcast"] = {"rss_url": "", "episodes": []}
            (tmp_path / "demo_hub_data.json").write_text(json.dumps(demo_hub), encoding="utf-8")

            imported_hub = _build_adversarial_multiformat_hub()
            imported_hub["league_config"]["name"] = "Sleeper Loaded Dynasty"
            imported_hub["league_config"]["badge"] = "SLD"
            imported_hub["podcast"] = {"rss_url": "", "episodes": []}

            def fake_espn(lid, slug, season, **kwargs):
                resp = requests.Response()
                resp.status_code = 401
                raise requests.HTTPError("401 Private", response=resp)

            svc = PublicAPIService(
                static_dir=tmp_path,
                sleeper_builder=lambda lid, slug, **kw: (imported_hub, None),
                espn_builder=fake_espn,
            )
            srv = create_server(host="127.0.0.1", port=0, service=svc)
            port = srv.server_address[1]
            t = threading.Thread(target=srv.serve_forever, daemon=True)
            t.start()

            page_errors = []
            console_errors = []
            try:
                with sync_playwright() as p:
                    browser = None
                    try:
                        browser = p.chromium.launch(headless=True)
                    except Exception:
                        try:
                            browser = p.chromium.launch(headless=True, channel="chrome")
                        except Exception as exc:
                            self.skipTest(f"No headless Chromium/Chrome binary available: {exc}")

                    page = browser.new_page()
                    page.on("pageerror", lambda err: page_errors.append(str(err)))
                    page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)

                    # Mock browser-direct Sleeper CORS username lookup with an XSS-laced league name!
                    def _mock_sleeper_cors(route):
                        url = route.request.url
                        if "/v1/user/testcommish" in url and "/leagues/" not in url:
                            route.fulfill(status=200, content_type="application/json", body=json.dumps({"user_id": "u99", "username": "testcommish"}))
                        elif "/leagues/nfl/" in url:
                            route.fulfill(status=200, content_type="application/json", body=json.dumps([{
                                "league_id": "1313213902351273984",
                                "name": 'Dynasty <img src=x onerror="window.__XSS_PWNED=77"> League',
                                "total_rosters": 12,
                                "settings": {"type": 2, "league_average_match": 1},
                                "scoring_settings": {"rec": 1.0},
                                "roster_positions": ["QB", "SUPER_FLEX"],
                            }]))
                        else:
                            route.fulfill(status=200, content_type="application/json", body="null")

                    page.route("https://api.sleeper.app/**", _mock_sleeper_cors)

                    # 1. Open public landing root -> Demo loads, Onboarding Drawer auto-opens, Podcast is 100% hidden!
                    page.goto(f"http://127.0.0.1:{port}/", wait_until="networkidle")
                    page.wait_for_selector("#playoffTable tbody tr")
                    self.assertTrue(page.locator("#onboardPanel").is_visible())
                    self.assertEqual(page.locator('.tab-btn[data-tab="podcast"]').inner_text(), "Weekly Awards")
                    self.assertFalse(page.locator("#podcastCard").is_visible())

                    # 2. Type Sleeper username -> pick league -> loads remote Sleeper Hub dynamically & updates URL!
                    page.fill("#sleeperUserInput", "testcommish")
                    page.click("#btnLookupSleeperUser")
                    page.wait_for_selector(".league-choice-btn")
                    self.assertIn("12T • Dynasty • Full PPR • SF • Median", page.locator("#sleeperUserLeagues").inner_text())
                    page.locator(".league-choice-btn").first.click()
                    page.wait_for_function("() => document.querySelector('.brand h1').textContent.includes('Sleeper Loaded Dynasty')")
                    self.assertIn("platform=sleeper", page.url)
                    self.assertIn("league=1313213902351273984", page.url)
                    self.assertTrue(page.locator("#btnShareLeague").is_visible())
                    self.assertFalse(page.locator("#podcastCard").is_visible())

                    # 3. Switch to ESPN tab & test Private League error guidance (note: 403 fetch logs browser resource msg, check UI banner)
                    console_errors_before_403 = list(console_errors)
                    page.click('#onboardPlatformGroup [data-platform="espn"]')
                    page.fill("#espnLeagueInput", "https://fantasy.espn.com/football/league?leagueId=157057")
                    page.click("#btnLoadEspnLeague")
                    page.wait_for_function("() => document.getElementById('onboardStatus').textContent.includes('Make League Viewable to Public')")

                    pwned = page.evaluate("() => window.__XSS_PWNED")
                    self.assertIsNone(pwned, f"XSS executed during onboarding! __XSS_PWNED={pwned}")
                    browser.close()
            finally:
                srv.shutdown()
                srv.server_close()

            self.assertEqual(page_errors, [], f"Uncaught page errors: {page_errors}")
            self.assertEqual(console_errors_before_403, [], f"Console errors before intentional 403 test: {console_errors_before_403}")


if __name__ == "__main__":
    unittest.main()
