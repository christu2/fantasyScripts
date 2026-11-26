import json
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from FantasyRecap.bfl_historian_bot import (
    build_draft_steals_report,
    build_rivalry_report,
    resolve_owner_name,
)
from FantasyRecap.bfl_history_db import DB_PATH, compute_position_normalized_draft_value
from FantasyRecap.build_league_hub import WEB_HUB_DIR, export_history_and_drafts
from FantasyRecap.live_game_desk_daemon import (
    _pick_live_copy,
    format_catalyst_phrase,
    is_nuclear_performance,
)
from FantasyRecap.playoff_odds_simulator import solve_optimal_starting_lineup
from FantasyRecap.thursday_lineup_preview import determine_game_stakes
from FantasyRecap.weekly_awards_engine import (
    compute_weekly_awards,
    format_awards_discord_payload,
)


class TestHistorianBotInstantCommands(unittest.TestCase):
    def test_owner_name_resolver_handles_nicknames_and_first_names(self):
        self.assertEqual(resolve_owner_name("Thor"), "Shawn Ullenbrauck")
        self.assertEqual(resolve_owner_name("Lukose"), "Shawn Lukose")
        self.assertEqual(resolve_owner_name("AMO"), "Adam Olen")
        self.assertEqual(resolve_owner_name("Dan"), "Daniel Kruszewski")
        self.assertEqual(resolve_owner_name("rej"), "rej hoxha")
        self.assertEqual(resolve_owner_name("Nick"), "Nick Christus")

    def test_rivalry_and_draft_steals_reports_from_sqlite(self):
        if not DB_PATH.exists():
            self.skipTest("bfl_history.db not present")
        riv = build_rivalry_report("Nick", "Dan")
        self.assertIn("Nick Christus", riv["title"])
        self.assertIn("Daniel Kruszewski", riv["title"])
        self.assertEqual(len(riv["fields"]), 3)

        dr = build_draft_steals_report(season=2026)
        self.assertEqual(len(dr["fields"]), 2)
        self.assertIn("Biggest Steals", dr["fields"][0]["name"])
        self.assertIn("Biggest Busts", dr["fields"][1]["name"])


class TestDiscordPostQualityUpgrades(unittest.TestCase):
    def test_nuclear_threshold_blocks_routine_scores_and_allows_monsters(self):
        # Routine 17-pt kicker or 22-pt RB or high-proj 31-pt QB must NOT fire nuclear
        self.assertFalse(is_nuclear_performance(5, 17.0, 9.0))
        self.assertFalse(is_nuclear_performance(16, 17.0, 7.0))
        self.assertFalse(is_nuclear_performance(2, 24.5, 16.0))
        self.assertFalse(is_nuclear_performance(1, 31.0, 23.0))
        # True monster explosions clear BOTH raw floor and +over-proj floor
        self.assertTrue(is_nuclear_performance(1, 36.5, 21.0))
        self.assertTrue(is_nuclear_performance(2, 29.4, 15.0))
        self.assertTrue(is_nuclear_performance(4, 25.0, 11.0))

    def test_catalyst_phrase_calibrates_verb_to_point_jump(self):
        small = format_catalyst_phrase("Stefon Diggs", "WR", "Samran", 2.4, 5.7)
        self.assertNotIn("erupted", small.lower())
        self.assertNotIn("detonated", small.lower())
        self.assertIn("+2.4 pts", small)

        big = format_catalyst_phrase("Ashton Jeanty", "RB", "Thor", 15.3, 27.4)
        self.assertIn("+15.3 pts", big)

        # Copy picker returns varied options across distinct seeds
        picks = {_pick_live_copy(f"seed-{i}", ["A", "B", "C", "D"]) for i in range(25)}
        self.assertGreaterEqual(len(picks), 3)

    def test_thursday_game_stakes_intelligence_uses_leverage_bye_and_sb(self):
        s_bye = determine_game_stakes(
            {"wins": 9, "div_rank": 1}, {"wins": 8, "div_rank": 1}, "Cross-Division", 12,
            a_po={"playoff_pct": 100.0, "bye_pct": 55.0, "leverage_swing": 0.0},
            h_po={"playoff_pct": 99.0, "bye_pct": 25.0, "leverage_swing": 1.0},
        )
        self.assertIn("#1 Seed Bye Showdown", s_bye)

        s_lev = determine_game_stakes(
            {"wins": 6, "div_rank": 2}, {"wins": 5, "div_rank": 3}, "Cross-Division", 12,
            a_po={"playoff_pct": 58.0, "bye_pct": 0.0, "leverage_swing": 48.5},
            h_po={"playoff_pct": 32.0, "bye_pct": 0.0, "leverage_swing": 36.2},
            a_owner="Daniel Kruszewski", h_owner="Tommy Ehrlich",
        )
        self.assertIn("Playoff Cutline Pivot", s_lev)
        self.assertIn("±48%", s_lev)

        s_trap = determine_game_stakes(
            {"wins": 2, "div_rank": 4}, {"wins": 6, "div_rank": 2}, "Cross-Division", 12,
            a_po={"playoff_pct": 0.0, "bye_pct": 0.0, "leverage_swing": 0.0},
            h_po={"playoff_pct": 68.0, "bye_pct": 0.0, "leverage_swing": 18.0},
            a_owner="rej hoxha", h_owner="Alex Kite",
        )
        self.assertIn("Trap Game", s_trap)
        self.assertIn("Spoiler", s_trap)

    def test_weekly_awards_engine_computes_all_awards_and_excludes_injury_blunders(self):
        matchups = [
            {
                "winner": {"id": 1, "owner": "Alex Kite", "name": "FGTA"},
                "loser": {"id": 2, "owner": "rej hoxha", "name": "Chase"},
                "away_score": 118.5, "home_score": 74.0, "margin": 44.5,
            },
            {
                "winner": {"id": 3, "owner": "Shawn Ullenbrauck", "name": "Thor"},
                "loser": {"id": 4, "owner": "Samran Mirza", "name": "De'von"},
                "away_score": 92.1, "home_score": 89.5, "margin": 2.6,
            },
        ]
        perfs = {
            1: {"starters": [{"name": "Travis Kelce", "pts": 22.4, "proj": 9.5}], "blunders": []},
            2: {"starters": [{"name": "Jayden Daniels", "pts": 17.0, "proj": 19.9}], "blunders": []},
            3: {"starters": [{"name": "Ashton Jeanty", "pts": 27.4, "proj": 12.0}], "blunders": []},
            4: {
                "starters": [{"name": "Joe Burrow", "pts": 19.9, "proj": 23.4}],
                "blunders": [
                    # Injury exit (0.5 pts on 14 proj) must be skipped; real healthy blunder must be picked
                    {"started": {"name": "Hurt Guy", "pts": 0.5, "proj": 14.0, "pos_id": 2}, "benched": {"name": "Backup", "pts": 15.0}, "diff": 14.5},
                    {"started": {"name": "Healthy Bust", "pts": 4.0, "proj": 11.0, "pos_id": 3}, "benched": {"name": "Bench WR", "pts": 15.0}, "diff": 11.0},
                ],
            },
        }
        po = {
            "Daniel Kruszewski": {"owner": "Daniel Kruszewski", "record": "6-5", "next_opp": "Tommy Ehrlich", "win_po_pct": 78.8, "loss_po_pct": 22.9, "leverage_swing": 55.9}
        }
        res = compute_weekly_awards(matchups, perfs, week_num=11, season=2026, playoff_odds=po)
        keys = {a["key"] for a in res["awards"]}
        self.assertEqual(keys, {"manager_of_week", "player_of_week", "start_sit_blunder", "bad_beat", "photo_finish", "pressure_cooker"})
        blunder = next(a for a in res["awards"] if a["key"] == "start_sit_blunder")
        self.assertIn("Healthy Bust", blunder["body"])
        self.assertNotIn("Hurt Guy", blunder["body"])

        payload = format_awards_discord_payload(res)
        self.assertEqual(len(payload["embeds"][0]["fields"]), 6)


class TestLeagueHubBundle(unittest.TestCase):
    def test_optimal_starting_lineup_ignores_bench_and_autofills_fa_gaps(self):
        # Manager has no active TE (on bye / injured) and 3 strong RBs; solver must start best legal lineup + FA TE fill
        roster = [
            {"id": 1, "name": "QB Stud", "pos": "QB", "proj": 21.5, "injury": "ACTIVE"},
            {"id": 2, "name": "RB1", "pos": "RB", "proj": 16.0, "injury": "ACTIVE"},
            {"id": 3, "name": "RB2", "pos": "RB", "proj": 14.0, "injury": "ACTIVE"},
            {"id": 4, "name": "RB3 Flex", "pos": "RB", "proj": 12.5, "injury": "ACTIVE"},
            {"id": 5, "name": "WR1", "pos": "WR", "proj": 15.0, "injury": "ACTIVE"},
            {"id": 6, "name": "WR2", "pos": "WR", "proj": 11.0, "injury": "ACTIVE"},
            {"id": 7, "name": "WR3 Bench", "pos": "WR", "proj": 9.0, "injury": "ACTIVE"},
            {"id": 8, "name": "TE Injured", "pos": "TE", "proj": 12.0, "injury": "OUT"},
            {"id": 9, "name": "TE Bye", "pos": "TE", "proj": 0.0, "injury": "ACTIVE"},
            {"id": 10, "name": "Kicker", "pos": "K", "proj": 8.5, "injury": "ACTIVE"},
            {"id": 11, "name": "Defense", "pos": "D/ST", "proj": 7.5, "injury": "ACTIVE"},
        ]
        fa = {"QB": 14.0, "RB": 7.0, "WR": 7.0, "TE": 6.2, "FLEX": 7.0, "K": 8.0, "D/ST": 6.5}
        sol = solve_optimal_starting_lineup(roster, fa_baselines=fa)
        self.assertEqual(len(sol["starters"]), 9)
        self.assertEqual(sol["fa_fills"], ["FA TE (+6.2)"])
        # 21.5 + 16 + 14 + 15 + 11 + 6.2(FA TE) + 12.5(RB3 FLEX) + 7.5 + 8.5 = 112.2
        self.assertAlmostEqual(sol["optimal_proj"], 112.2, places=1)

    def test_position_normalized_draft_steals_avoids_qb_inflation(self):
        if not DB_PATH.exists():
            self.skipTest("bfl_history.db not present")
        res_2026 = compute_position_normalized_draft_value(DB_PATH, season=2026, limit=10)
        steal_names_2026 = [s["player"] for s in res_2026["steals"]]
        # Mid-pack/below-replacement QBs (Trevor Lawrence QB18, Jacoby Brissett QB18) must NOT show up as steals
        self.assertNotIn("Trevor Lawrence", steal_names_2026)
        self.assertNotIn("Jacoby Brissett", steal_names_2026)
        steal_positions = {s["pos"] for s in res_2026["steals"]}
        self.assertTrue(bool(steal_positions & {"RB", "WR", "TE"}))

        res_all = compute_position_normalized_draft_value(DB_PATH, season=None, limit=10)
        all_names = [s["player"] for s in res_all["steals"]]
        # True MVP/league-winning steals (Lamar Jackson, Patrick Mahomes, Kyren Williams) must outrank backup-cliff QBs
        self.assertIn("Lamar Jackson", all_names[:3])
        self.assertNotIn("Sam Howell", all_names)
        self.assertNotIn("Daniel Jones", all_names[:5])

    def test_web_hub_static_files_and_history_export(self):
        for fname in ("index.html", "styles.css", "app.js"):
            self.assertTrue((WEB_HUB_DIR / fname).exists(), f"Missing {fname}")
        # Ensure Rej's written name is spelled "Rej" (not "Ray") in web UI
        app_js = (WEB_HUB_DIR / "app.js").read_text(encoding="utf-8")
        self.assertIn('return "Rej"', app_js)
        self.assertNotIn('return "Ray"', app_js)
        if DB_PATH.exists():
            hist = export_history_and_drafts(DB_PATH)
            self.assertEqual(len(hist["career"]), 16)
            self.assertGreater(len(hist["h2h"]), 100)
            self.assertIn("2026", hist["drafts"])


class TestTradeVaultEngine(unittest.TestCase):
    def test_extract_executed_trades_dedups_and_ignores_declines_and_draft_swaps(self):
        from FantasyRecap.trade_vault_engine import _extract_executed_trades_from_txs

        txs = [
            # Declined trade (must be ignored even though status == EXECUTED)
            {"id": "dec1", "type": "TRADE_DECLINE", "status": "EXECUTED", "scoringPeriodId": 4, "items": [
                {"type": "TRADE", "fromTeamId": 1, "toTeamId": 2, "playerId": 999},
            ]},
            # Draft-pick-only swap (playerId == 0, type == DRAFT_TRADE — must be ignored)
            {"id": "drf1", "type": "TRADE_UPHOLD", "status": "EXECUTED", "scoringPeriodId": 1, "items": [
                {"type": "DRAFT_TRADE", "fromTeamId": 1, "toTeamId": 2, "playerId": 0},
            ]},
            # Real trade: proposal holds items, TRADE_UPHOLD & TRADE_ACCEPT confirm it (must emit ONCE)
            {"id": "prop1", "type": "TRADE_PROPOSAL", "status": "PENDING", "scoringPeriodId": 6, "items": [
                {"type": "TRADE", "fromTeamId": 1, "toTeamId": 6, "playerId": 1001},
                {"type": "TRADE", "fromTeamId": 6, "toTeamId": 1, "playerId": 2002},
                {"type": "DROP", "fromTeamId": 1, "toTeamId": 0, "playerId": 3003},
            ]},
            {"id": "acc1", "type": "TRADE_ACCEPT", "status": "EXECUTED", "scoringPeriodId": 6, "relatedTransactionId": "prop1", "items": None},
            {"id": "uph1", "type": "TRADE_UPHOLD", "status": "EXECUTED", "scoringPeriodId": 6, "relatedTransactionId": "prop1", "items": None},
        ]
        parsed = _extract_executed_trades_from_txs(txs, season=2025)
        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0]["trade_week"], 6)
        self.assertEqual(len(parsed[0]["items"]), 2)

    def test_dual_lens_vorp_playoff_weight_matchups_flipped_and_trade_trees(self):
        import sqlite3
        import tempfile
        from pathlib import Path
        from FantasyRecap.bfl_history_db import init_db
        from FantasyRecap.trade_vault_engine import evaluate_all_trades

        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "test_vault.db"
            conn = sqlite3.connect(db)
            init_db(conn)
            cur = conn.cursor()

            # Two teams in 2025 (All-Time) + one trade in 2026 (Live)
            cur.execute(
                "INSERT INTO team_seasons VALUES (2025,1,'Team A','Nick Christus','Nick Christus','Nick Christus',1,'East',10,4,0,0.714,1500,1300,4,2,0,1,1,1,1,1,1,0,1)"
            )
            cur.execute(
                "INSERT INTO team_seasons VALUES (2025,2,'Team B','Daniel Kruszewski','Daniel Kruszewski','Daniel Kruszewski',1,'East',7,7,0,0.5,1350,1350,3,3,0,2,0,9,0,0,0,0,1)"
            )
            # Trade 1 (2025 Wk 10): Nick acquires WR Stud (101) from Dan for WR Bench (202)
            cur.execute(
                "INSERT INTO trades VALUES ('2025_w10_a',2025,10,10,1,'Nick Christus',2,'Daniel Kruszewski',0,'espn_tx')"
            )
            cur.execute(
                "INSERT INTO trade_items VALUES ('2025_w10_a',2025,2,'Daniel Kruszewski',1,'Nick Christus',101,'Stud WR','WR')"
            )
            cur.execute(
                "INSERT INTO trade_items VALUES ('2025_w10_a',2025,1,'Nick Christus',2,'Daniel Kruszewski',202,'Bench WR','WR')"
            )
            # Trade 2 (2025 Wk 12): Dan flips Bench WR (202) to Team 3 -> triggers Trade Tree!
            cur.execute(
                "INSERT INTO trades VALUES ('2025_w12_b',2025,12,12,2,'Daniel Kruszewski',3,'Shawn Lukose',0,'espn_tx')"
            )
            cur.execute(
                "INSERT INTO trade_items VALUES ('2025_w12_b',2025,2,'Daniel Kruszewski',3,'Shawn Lukose',202,'Bench WR','WR')"
            )
            cur.execute(
                "INSERT INTO trade_items VALUES ('2025_w12_b',2025,3,'Shawn Lukose',2,'Daniel Kruszewski',303,'Depth RB','RB')"
            )
            # Live 2026 trade (must stay separated in `live`, NOT mixed into `all_time`)
            cur.execute(
                "INSERT INTO trades VALUES ('2026_w04_c',2026,4,4,1,'Nick Christus',2,'Daniel Kruszewski',0,'espn_tx')"
            )
            cur.execute(
                "INSERT INTO trade_items VALUES ('2026_w04_c',2026,2,'Daniel Kruszewski',1,'Nick Christus',401,'Live WR','WR')"
            )
            cur.execute(
                "INSERT INTO trade_items VALUES ('2026_w04_c',2026,1,'Nick Christus',2,'Daniel Kruszewski',402,'Live RB','RB')"
            )

            # Games: Week 10 regular win by 5 pts; Week 15 playoff win by 10 pts
            cur.execute(
                "INSERT INTO games VALUES (2025,10,1,'NONE',1,0,0,1,'Team A','Nick Christus','Nick Christus','Nick Christus',110,2,'Team B','Daniel Kruszewski','Daniel Kruszewski',105,1,5.0)"
            )
            cur.execute(
                "INSERT INTO games VALUES (2025,15,2,'WINNERS_BRACKET',0,1,0,1,'Team A','Nick Christus','Nick Christus','Nick Christus',125,3,'Team C','Shawn Lukose','Shawn Lukose',115,1,10.0)"
            )

            # Boxscores (fallback WR baseline = 8.0):
            # Stud WR (101) started by Nick in Wk 10 (20.0 -> +12.0 VORP, flips 5-pt win!) & Wk 15 Playoffs (24.0 -> 1.5 * 16.0 = +24.0 VORP, flips 10-pt win!)
            cur.execute(
                "INSERT INTO player_boxscores VALUES (2025,10,1,1,'Team A','Nick Christus','Nick Christus',2,'Daniel Kruszewski','NONE',1,0,0,101,'Stud WR','DAL','WR',4,'WR',1,20.0,15.0)"
            )
            cur.execute(
                "INSERT INTO player_boxscores VALUES (2025,15,2,1,'Team A','Nick Christus','Nick Christus',3,'Shawn Lukose','WINNERS_BRACKET',0,1,0,101,'Stud WR','DAL','WR',4,'WR',1,24.0,16.0)"
            )
            # Bench WR (202) BENCHED on Dan's team in Wk 10 (22.0 -> 0 started VORP, +14.0 Pure ROS VORP!)
            # AND started in CONSOLATION Wk 15 (25.0 -> 0 started VORP because consolation excluded!)
            cur.execute(
                "INSERT INTO player_boxscores VALUES (2025,10,1,2,'Team B','Daniel Kruszewski','Daniel Kruszewski',1,'Nick Christus','NONE',1,0,0,202,'Bench WR','NYG','WR',20,'Bench',0,22.0,10.0)"
            )
            cur.execute(
                "INSERT INTO player_boxscores VALUES (2025,15,3,2,'Team B','Daniel Kruszewski','Daniel Kruszewski',4,'Adam Olen','LOSERS_CONSOLATION_LADDER',0,0,1,202,'Bench WR','NYG','WR',4,'WR',1,25.0,10.0)"
            )
            conn.commit()
            conn.close()

            vault = evaluate_all_trades(db, current_season=2026)
            self.assertEqual(vault["all_time"]["trade_count"], 2)
            self.assertEqual(vault["live"]["trade_count"], 1)

            tr1 = next(t for t in vault["all_time"]["trades"] if t["trade_id"] == "2025_w10_a")
            # Actual Lineup Impact: Nick = +12.0 (Wk10) + 1.5*16.0 (Wk15 PO) = +36.0; Dan = 0.0 (benched + consolation)
            self.assertAlmostEqual(tr1["started"]["a_vorp"], 36.0, places=1)
            self.assertAlmostEqual(tr1["started"]["b_vorp"], 0.0, places=1)
            self.assertEqual(tr1["started"]["winner"], "Nick Christus")
            self.assertEqual(tr1["a_games_swung"], 2)
            self.assertIn("Title Deal", tr1["started"]["tags"])
            self.assertIn("Playoff Hero", tr1["started"]["tags"])
            self.assertIn("Trade Tree", tr1["started"]["tags"])
            # Pure Player Outcome: Dan's Bench WR DID score 22 (Wk10: +14) + 25 (Wk15 PO 1.5x: +25.5) = +39.5 ROS VORP!
            self.assertAlmostEqual(tr1["ros"]["a_vorp"], 36.0, places=1)
            self.assertAlmostEqual(tr1["ros"]["b_vorp"], 39.5, places=1)
            self.assertEqual(tr1["ros"]["winner"], "Daniel Kruszewski")
            # Trade Tree metadata attached to Bench WR
            self.assertEqual(tr1["b_received"][0]["flipped"]["week"], 12)
            self.assertEqual(tr1["b_received"][0]["flipped"]["to"], "Shawn Lukose")

    def test_pure_ros_vorp_does_not_floor_individual_dud_weeks(self):
        import sqlite3
        import tempfile
        from pathlib import Path
        from FantasyRecap.bfl_history_db import init_db
        from FantasyRecap.trade_vault_engine import evaluate_all_trades

        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "test_boom_bust.db"
            conn = sqlite3.connect(db)
            init_db(conn)
            cur = conn.cursor()
            cur.execute("INSERT INTO trades VALUES ('2025_w05_bb',2025,5,5,1,'Nick Christus',2,'Daniel Kruszewski',0,'espn_tx')")
            cur.execute("INSERT INTO trade_items VALUES ('2025_w05_bb',2025,2,'Daniel Kruszewski',1,'Nick Christus',501,'BoomBust WR','WR')")
            cur.execute("INSERT INTO trade_items VALUES ('2025_w05_bb',2025,1,'Nick Christus',2,'Daniel Kruszewski',502,'Steady WR','WR')")
            # BoomBust WR drops 20, 2, 2, 2 vs WR replacement 8.0 -> raw sum = +12 - 6 - 6 - 6 = -6.0 -> floored at 0.0 (NOT +12.0!)
            for wk, pts in [(5, 20.0), (6, 2.0), (7, 2.0), (8, 2.0)]:
                cur.execute(
                    "INSERT INTO player_boxscores VALUES (2025,?,1,1,'Team A','Nick Christus','Nick Christus',2,'Daniel Kruszewski','NONE',1,0,0,501,'BoomBust WR','NYJ','WR',20,'Bench',0,?,10.0)",
                    (wk, pts),
                )
            # Steady WR drops 14, 14, 14, 14 vs WR replacement 8.0 -> +24.0 Pure ROS VORP
            for wk, pts in [(5, 14.0), (6, 14.0), (7, 14.0), (8, 14.0)]:
                cur.execute(
                    "INSERT INTO player_boxscores VALUES (2025,?,1,2,'Team B','Daniel Kruszewski','Daniel Kruszewski',1,'Nick Christus','NONE',1,0,0,502,'Steady WR','CIN','WR',20,'Bench',0,?,12.0)",
                    (wk, pts),
                )
            conn.commit()
            conn.close()

            vault = evaluate_all_trades(db, current_season=2026)
            tr = vault["all_time"]["trades"][0]
            self.assertAlmostEqual(tr["ros"]["a_vorp"], 0.0, places=1)
            self.assertAlmostEqual(tr["ros"]["b_vorp"], 24.0, places=1)
            self.assertEqual(tr["ros"]["winner"], "Daniel Kruszewski")

    def test_live_db_trade_vault_and_web_hub_ui(self):
        from FantasyRecap.trade_vault_engine import evaluate_all_trades

        idx_html = (WEB_HUB_DIR / "index.html").read_text(encoding="utf-8")
        app_js = (WEB_HUB_DIR / "app.js").read_text(encoding="utf-8")
        self.assertIn('id="tab-trades"', idx_html)
        self.assertIn("function renderTradesTab()", app_js)

        if DB_PATH.exists():
            vault = evaluate_all_trades(DB_PATH, current_season=2026)
            self.assertGreaterEqual(vault["all_time"]["trade_count"], 100)
            self.assertGreaterEqual(vault["live"]["trade_count"], 10)
            self.assertEqual(len(vault["all_time"]["owner_cards"]["started"]), 16)
            self.assertEqual(len(vault["all_time"]["owner_cards"]["ros"]), 16)


if __name__ == "__main__":
    unittest.main()
