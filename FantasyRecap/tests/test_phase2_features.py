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
        res = compute_position_normalized_draft_value(DB_PATH, season=2026, limit=10)
        steal_names = [s["player"] for s in res["steals"]]
        # Mid-pack QB18 Trevor Lawrence must NOT show up as a top steal
        self.assertNotIn("Trevor Lawrence", steal_names)
        # Steals must include non-QB positional out-performers (RB/WR/TE)
        steal_positions = {s["pos"] for s in res["steals"]}
        self.assertTrue(bool(steal_positions & {"RB", "WR", "TE"}))

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


if __name__ == "__main__":
    unittest.main()
