import random
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from FantasyRecap.generate_notebook_payload import (
    _parse_rotowire_age_days,
    detect_in_game_injury,
)
from FantasyRecap.league_recap_generator import calculate_optimal_lineup
from FantasyRecap.live_game_desk_daemon import _dst_kickoff_cushion_jump
from FantasyRecap.live_win_probability_monitor import (
    calculate_matchup_win_probability,
    detect_probability_shockwaves,
)
from FantasyRecap.playoff_odds_simulator import (
    DIVISIONS,
    resolve_division_tiebreakers,
    resolve_wildcard_tiebreakers,
    run_playoff_simulation,
)


class TestPlayoffTiebreakersAndSimulation(unittest.TestCase):
    def test_two_team_division_tiebreaker_prefers_h2h_then_pf(self):
        records = {
            "A": {"wins": 8, "losses": 6, "pf": 1400.0},
            "B": {"wins": 8, "losses": 6, "pf": 1550.0},
        }
        # A beat B head-to-head despite lower PF -> A wins tiebreaker
        self.assertEqual(resolve_division_tiebreakers(["A", "B"], records, {("A", "B"): 2}), ["A", "B"])
        # Split H2H (1-1) -> falls back to PF -> B wins
        self.assertEqual(
            resolve_division_tiebreakers(["A", "B"], records, {("A", "B"): 1, ("B", "A"): 1}),
            ["B", "A"],
        )

    def test_three_team_division_tiebreaker_h2h_subpct(self):
        records = {
            "A": {"wins": 7, "losses": 7, "pf": 1300.0},
            "B": {"wins": 7, "losses": 7, "pf": 1500.0},
            "C": {"wins": 7, "losses": 7, "pf": 1450.0},
        }
        # A went 2-0 vs B and C; B & C split 1-1 -> A first, then B (higher PF) over C
        h2h = {("A", "B"): 1, ("A", "C"): 1, ("B", "C"): 1, ("C", "B"): 1}
        self.assertEqual(resolve_division_tiebreakers(["A", "B", "C"], records, h2h), ["A", "B", "C"])

    def test_wildcard_tiebreaker_requires_clean_h2h_sweep_for_two_teams(self):
        records = {
            "A": {"wins": 8, "losses": 6, "pf": 1350.0},
            "B": {"wins": 8, "losses": 6, "pf": 1490.0},
        }
        self.assertEqual(resolve_wildcard_tiebreakers(["A", "B"], records, {("A", "B"): 1}), ["A", "B"])
        # Never played -> PF decides
        self.assertEqual(resolve_wildcard_tiebreakers(["A", "B"], records, {}), ["B", "A"])

    def test_monte_carlo_probability_invariants(self):
        random.seed(42)
        all_owners = [o for mems in DIVISIONS.values() for o in mems]
        # Build a clean 1-week remaining schedule (8 games)
        schedule = [
            {"week": 14, "away": all_owners[i], "home": all_owners[i + 1], "type": "Division"}
            for i in range(0, 16, 2)
        ]
        results = run_playoff_simulation(current_week=14, completed_games=[], schedule=schedule, n_sims=500)
        self.assertEqual(len(results), 16)
        # Exactly 7 playoff teams -> sum of playoff_pct ~ 700%
        total_po = sum(r["playoff_pct"] for r in results.values())
        self.assertAlmostEqual(total_po, 700.0, delta=2.0)
        # Exactly 1 #1 seed bye -> sum of bye_pct ~ 100%
        total_bye = sum(r["bye_pct"] for r in results.values())
        self.assertAlmostEqual(total_bye, 100.0, delta=1.5)
        # Each division has 1 champion -> sum per division ~ 100%
        for div, members in DIVISIONS.items():
            div_sum = sum(results[m]["div_title_pct"] for m in members)
            self.assertAlmostEqual(div_sum, 100.0, delta=1.5, msg=f"Division {div} sum mismatch")


class TestWinProbabilityAndKickoffCushion(unittest.TestCase):
    def test_all_final_is_deterministic(self):
        a = [{"pos_id": 1, "baseline_proj": 20.0, "current_score": 25.0, "game_status": "FINAL", "pct_remaining": 0.0}]
        h = [{"pos_id": 1, "baseline_proj": 20.0, "current_score": 20.0, "game_status": "FINAL", "pct_remaining": 0.0}]
        ap, hp, _, _ = calculate_matchup_win_probability(a, h, 105.0, 99.0)
        self.assertEqual((ap, hp), (1.0, 0.0))

        ap_tie, hp_tie, _, _ = calculate_matchup_win_probability(a, h, 100.0, 100.0)
        self.assertEqual((ap_tie, hp_tie), (0.5, 0.5))

    def test_dst_kickoff_cushion_does_not_spike_win_prob(self):
        # Pre-game: equal projections (8.0 each)
        a_pre = [{"name": "Ravens D/ST", "pos_id": 16, "baseline_proj": 8.0, "current_score": 0.0, "game_status": "PRE", "pct_remaining": 1.0}]
        h_pre = [{"name": "Bills D/ST", "pos_id": 16, "baseline_proj": 8.0, "current_score": 0.0, "game_status": "PRE", "pct_remaining": 1.0}]
        ap_pre, _, _, _ = calculate_matchup_win_probability(a_pre, h_pre, 0.0, 0.0)
        self.assertAlmostEqual(ap_pre, 0.5, places=3)

        # Away D/ST kicks off and gets ESPN's automatic +10.0 starting bonus (pct_remaining=0.99)
        a_ko = [{"name": "Ravens D/ST", "pos_id": 16, "baseline_proj": 8.0, "current_score": 10.0, "game_status": "IN_PROGRESS", "pct_remaining": 0.99}]
        ap_ko, _, a_exp, h_exp = calculate_matchup_win_probability(a_ko, h_pre, 10.0, 0.0)
        # Expected total should stay right around 8.0 (NOT jump to 18.0!)
        self.assertAlmostEqual(a_exp, 8.02, delta=0.2)
        self.assertAlmostEqual(ap_ko, 0.5, delta=0.02)

        # Also verify kickoff cushion detector catches the +10.0 jump
        cushion = _dst_kickoff_cushion_jump(
            {"away_starters": a_pre, "home_starters": h_pre},
            {"away_starters": a_ko, "home_starters": h_pre},
        )
        self.assertEqual(cushion["away"], 10.0)
        self.assertEqual(cushion["home"], 0.0)

    def test_shockwave_detector_threshold(self):
        prev = {"m1": {"away_team": "A", "home_team": "B", "away_prob": 0.35}}
        curr = {"m1": {"away_team": "A", "home_team": "B", "away_score": 90.0, "home_score": 82.0, "away_prob": 0.65}}
        alerts = detect_probability_shockwaves(prev, curr, threshold=0.25)
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0]["direction"], "AWAY")
        self.assertAlmostEqual(alerts[0]["swing_pct"], 30.0)


class TestLineupLegalityAndInjuryFilters(unittest.TestCase):
    def _entry(self, pid, name, pos_id, slot_id, pts, proj, week=10):
        return {
            "lineupSlotId": slot_id,
            "playerPoolEntry": {
                "appliedStatTotal": pts,
                "player": {
                    "id": pid,
                    "fullName": name,
                    "proTeamId": 1,
                    "defaultPositionId": pos_id,
                    "stats": [{"statSourceId": 1, "scoringPeriodId": week, "appliedTotal": proj}],
                },
            },
        }

    def test_bench_blunder_enforces_position_legality(self):
        entries = [
            self._entry(1, "Starter QB", 1, 0, 22.0, 20.0),
            self._entry(2, "Starter RB1", 2, 2, 12.0, 14.0),
            self._entry(3, "Starter RB2", 2, 2, 4.0, 11.0),
            self._entry(4, "Starter WR1", 3, 4, 15.0, 15.0),
            self._entry(5, "Starter WR2", 3, 4, 14.0, 13.0),
            self._entry(6, "Starter TE", 4, 6, 9.0, 9.0),
            self._entry(7, "Starter FLEX", 3, 23, 8.0, 11.0),
            self._entry(8, "Starter K", 5, 17, 7.0, 8.0),
            self._entry(9, "Starter DST", 16, 16, 6.0, 7.0),
            # Bench QB scored 28, but Starter QB scored 22 -> legal QB blunder (+6)
            # Crucially, Bench QB must NOT be matched against Starter RB2 (4 pts)!
            self._entry(10, "Bench QB", 1, 20, 28.0, 18.0),
            # Bench WR scored 20 on 10 proj -> legal against Starter FLEX (8 pts, 11 proj) -> +12 blunder
            self._entry(11, "Bench WR", 3, 20, 20.0, 10.0),
        ]
        _, _, _, _, blunders = calculate_optimal_lineup(entries, week_num=10)
        qb_blunder = next(b for b in blunders if b["benched"]["name"] == "Bench QB")
        self.assertEqual(qb_blunder["started"]["name"], "Starter QB")
        self.assertEqual(qb_blunder["diff"], 6.0)
        wr_blunder = next(b for b in blunders if b["benched"]["name"] == "Bench WR")
        self.assertEqual(wr_blunder["started"]["name"], "Starter FLEX")
        self.assertEqual(wr_blunder["diff"], 12.0)

    def test_rotowire_age_parser_and_injury_guardrails(self):
        self.assertIsNone(_parse_rotowire_age_days(""))
        # High-scoring player (>= 8 pts) should not trigger curtailed in-game injury
        self.assertIsNone(detect_in_game_injury({"id": 999, "name": "Star", "pts": 12.5, "proj": 15.0}))

        # Pre-game activation headline must be suppressed even if player scored < 8 pts
        with patch(
            "FantasyRecap.generate_notebook_payload.fetch_rotowire_details",
            return_value={
                "headline": "Player activated from injured reserve for Sunday",
                "story": "He was cleared to play after a hamstring injury.",
                "is_fresh_3d": True,
            },
        ):
            self.assertIsNone(detect_in_game_injury({"id": 1001, "name": "Guy", "pts": 3.0, "proj": 10.0}))

        # Genuine mid-game exit on fresh 3d Rotowire report must be detected
        with patch(
            "FantasyRecap.generate_notebook_payload.fetch_rotowire_details",
            return_value={
                "headline": "Exited Sunday's game with a hamstring injury",
                "story": "He did not return after leaving in the first quarter.",
                "is_fresh_3d": True,
            },
        ):
            res = detect_in_game_injury({"id": 1002, "name": "Injured Guy", "pts": 2.1, "proj": 14.0})
            self.assertIsNotNone(res)
            self.assertEqual(res["player"], "Injured Guy")


if __name__ == "__main__":
    unittest.main()
