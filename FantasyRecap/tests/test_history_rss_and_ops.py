import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from FantasyRecap.bfl_history_db import DB_PATH, _parse_draft_picks, _parse_team_seasons, init_db, validate_readonly_sql
from FantasyRecap.core.ops import ProcessLock
from FantasyRecap.gemini_podcast_generator import (
    _sanitize_script,
    apply_phonetics,
    pcm_chunks_to_wav,
    split_script_into_tts_chunks,
)
from FantasyRecap.league_history_intelligence import _compile_h2h_from_sqlite
from FantasyRecap.podcast_rss_builder import add_episode_to_rss, upload_file_to_gcs


class TestSQLValidatorAndHistoryFastPath(unittest.TestCase):
    def test_parse_team_seasons_divisions_ranks_and_same_division_query(self):
        import sqlite3

        sample_espn = {
            "settings": {
                "scheduleSettings": {
                    "playoffTeamCount": 7,
                    "divisions": [{"id": 1, "name": "North"}, {"id": 2, "name": "South"}],
                }
            },
            "members": [
                {"id": "m1", "firstName": "Nick", "lastName": "Christus"},
                {"id": "m2", "firstName": "Dan", "lastName": "Kruszewski"},
                {"id": "m3", "firstName": "Abe", "lastName": "Thomas"},
            ],
            "teams": [
                {
                    "id": 1, "name": "Mykonos Minotaurs", "primaryOwner": "m1", "owners": ["m1"],
                    "divisionId": 1, "playoffSeed": 2, "rankCalculatedFinal": 3,
                    "record": {"overall": {"wins": 10, "losses": 4, "ties": 0, "pointsFor": 1620.5, "pointsAgainst": 1410.0},
                               "division": {"wins": 4, "losses": 2, "ties": 0}},
                },
                {
                    "id": 2, "name": "Dynasty Destroyers", "primaryOwner": "m2", "owners": ["m2"],
                    "divisionId": 1, "playoffSeed": 6, "rankCalculatedFinal": 2,
                    "record": {"overall": {"wins": 8, "losses": 6, "ties": 0, "pointsFor": 1510.2, "pointsAgainst": 1490.0},
                               "division": {"wins": 3, "losses": 3, "ties": 0}},
                },
                {
                    "id": 3, "name": "Crashee Bandicoot", "primaryOwner": "m3", "owners": ["m3"],
                    "divisionId": 2, "playoffSeed": 1, "rankCalculatedFinal": 1,
                    "record": {"overall": {"wins": 11, "losses": 3, "ties": 0, "pointsFor": 1700.0, "pointsAgainst": 1300.0},
                               "division": {"wins": 5, "losses": 1, "ties": 0}},
                },
            ],
            "schedule": [
                {"playoffTierType": "WINNERS_BRACKET", "away": {"teamId": 1}, "home": {"teamId": 2}},
                {"playoffTierType": "WINNERS_BRACKET", "away": {"teamId": 2}, "home": {"teamId": 3}},
            ],
        }

        rows = _parse_team_seasons(sample_espn, season=2025, current_yr=2026)
        self.assertEqual(len(rows), 3)

        conn = sqlite3.connect(":memory:")
        init_db(conn)
        conn.executemany(
            "INSERT INTO team_seasons VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            rows,
        )
        res = conn.execute(
            """
            SELECT a.season, a.division_name, a.division_rank, b.division_rank
            FROM team_seasons a
            JOIN team_seasons b
              ON a.season = b.season AND a.division_id = b.division_id AND a.team_id != b.team_id
            WHERE a.owner = 'Nick Christus' AND b.owner = 'Daniel Kruszewski'
            """
        ).fetchall()
        conn.close()
        self.assertEqual(res, [(2025, "North", 1, 2)])

    def test_parse_draft_picks_computes_pos_ranks_value_delta_and_round_baseline(self):
        import sqlite3

        conn = sqlite3.connect(":memory:")
        init_db(conn)
        # Seed a couple of starter boxscores for drafter Team 1 (player 102)
        conn.execute(
            "INSERT INTO player_boxscores VALUES (2025,1,1,1,'Mykonos','Nick Christus','Nick Christus',2,'Dan','NONE',1,0,0,102,'Puka Steal','LAR','WR',4,'WR',1,24.5,10.0)"
        )
        conn.execute(
            "INSERT INTO player_boxscores VALUES (2025,2,2,1,'Mykonos','Nick Christus','Nick Christus',2,'Dan','NONE',1,0,0,102,'Puka Steal','LAR','WR',4,'WR',1,21.5,11.0)"
        )
        conn.commit()

        draft_data = {
            "members": [
                {"id": "m1", "firstName": "Nick", "lastName": "Christus"},
                {"id": "m2", "firstName": "Dan", "lastName": "Kruszewski"},
            ],
            "teams": [
                {"id": 1, "name": "Mykonos", "primaryOwner": "m1", "owners": ["m1"]},
                {"id": 2, "name": "Destroyers", "primaryOwner": "m2", "owners": ["m2"]},
            ],
            "draftDetail": {
                "picks": [
                    {"overallPickNumber": 1, "roundId": 1, "roundPickNumber": 1, "teamId": 2, "playerId": 101},
                    {"overallPickNumber": 2, "roundId": 1, "roundPickNumber": 2, "teamId": 1, "playerId": 103},
                    {"overallPickNumber": 150, "roundId": 10, "roundPickNumber": 6, "teamId": 1, "playerId": 102},
                ]
            },
        }
        player_info = {
            101: {"name": "Round1 Bust", "position": "WR", "pro_team": "NYJ", "season_points": 90.0, "season_ppg": 6.0, "has_season_stats": 1},
            103: {"name": "Round1 Stud", "position": "WR", "pro_team": "CIN", "season_points": 290.0, "season_ppg": 17.1, "has_season_stats": 1},
            102: {"name": "Puka Steal", "position": "WR", "pro_team": "LAR", "season_points": 260.0, "season_ppg": 15.3, "has_season_stats": 1},
        }

        rows = _parse_draft_picks(draft_data, player_info, season=2025, conn=conn)
        self.assertEqual(len(rows), 3)
        conn.executemany(
            "INSERT INTO draft_picks VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            rows,
        )
        steal = conn.execute(
            "SELECT player_name, pos_draft_rank, pos_finish_rank, pos_value_delta, started_points_for_drafter, weeks_started_for_drafter FROM draft_picks WHERE player_id = 102"
        ).fetchone()
        bust = conn.execute(
            "SELECT player_name, pos_draft_rank, pos_finish_rank, pos_value_delta, points_above_round_avg FROM draft_picks WHERE player_id = 101"
        ).fetchone()
        conn.close()

        # Drafted as WR3 (pick 150), finished WR2 -> pos_value_delta = +1; started 2 wks for 46.0 pts for drafter
        self.assertEqual(steal, ("Puka Steal", 3, 2, 1, 46.0, 2))
        # Drafted as WR1 (pick 1), finished WR3 -> pos_value_delta = -2; Round 1 avg = (90+290)/2 = 190 -> -100.0 vs round avg
        self.assertEqual(bust, ("Round1 Bust", 1, 3, -2, -100.0))
    def test_readonly_sql_validator_allows_select_and_with(self):
        self.assertEqual(validate_readonly_sql("SELECT * FROM games LIMIT 5;"), "SELECT * FROM games LIMIT 5")
        self.assertTrue(validate_readonly_sql("WITH cte AS (SELECT 1) SELECT * FROM cte").startswith("WITH"))

    def test_readonly_sql_validator_blocks_mutations_and_injection(self):
        bad_queries = [
            "",
            "DELETE FROM games",
            "DROP TABLE games",
            "SELECT * FROM games; DROP TABLE games",
            "PRAGMA table_info(games)",
            "UPDATE games SET win = 1",
            "ATTACH DATABASE '/tmp/x.db' AS x",
        ]
        for q in bad_queries:
            with self.assertRaises(ValueError, msg=f"Should reject: {q}"):
                validate_readonly_sql(q)

    def test_sqlite_h2h_fast_path_loads_when_db_present(self):
        if not DB_PATH.exists():
            self.skipTest("bfl_history.db not present")
        h2h, career_stats, covered_years = _compile_h2h_from_sqlite(current_season=2026, up_to_week=11)
        self.assertGreater(len(h2h), 50)
        self.assertIn("Nick Christus", career_stats)
        self.assertIn(2008, covered_years)
        self.assertIn(2025, covered_years)


class TestProcessLockAndRSSBuilder(unittest.TestCase):
    def test_atomic_process_lock_prevents_double_acquire(self):
        with tempfile.TemporaryDirectory() as tmp:
            lock_path = Path(tmp) / "test.lock"
            lock1 = ProcessLock(lock_path)
            lock2 = ProcessLock(lock_path)
            self.assertTrue(lock1.acquire())
            self.assertTrue(ProcessLock.is_locked(lock_path))
            self.assertFalse(lock2.acquire())
            lock1.release()
            self.assertFalse(ProcessLock.is_locked(lock_path))
            self.assertTrue(lock2.acquire())
            lock2.release()

    def test_podcast_rss_builder_generates_valid_itunes_feed(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            fake_audio = tmp_path / "sample.mp3"
            fake_audio.write_bytes(b"ID3" + b"\x00" * 1024)
            rss_dir = tmp_path / "rss"

            info = add_episode_to_rss(
                season=2026,
                week=10,
                audio_path=str(fake_audio),
                title="Week 10 Test Episode",
                notes="Test recap notes",
                base_url="https://pod.bfl.test",
                rss_dir=rss_dir,
            )
            feed_path = Path(info["feed_path"])
            self.assertTrue(feed_path.exists())
            root = ET.fromstring(feed_path.read_text(encoding="utf-8"))
            self.assertEqual(root.tag, "rss")
            channel = root.find("channel")
            self.assertIsNotNone(channel)
            items = channel.findall("item")
            self.assertEqual(len(items), 1)
            self.assertEqual(items[0].find("title").text, "Week 10 Test Episode")
            enc = items[0].find("enclosure")
            self.assertEqual(enc.attrib["url"], "https://pod.bfl.test/episodes/bfl_notebooklm_s2026_w10.mp3")
            self.assertEqual(enc.attrib["type"], "audio/mpeg")

    def test_gemini_podcast_phonetics_chunking_and_wav_stitching(self):
        import wave

        raw = "**Chris:** Let us look at Saagar and rej hoxha chasing the Jabroni Trophy! [laughs]\n**Dave:** I am telling you, Lukose and Kruszewski are sweating in Mykonos!"
        cleaned = _sanitize_script(raw)
        self.assertIn("Let's", cleaned)
        self.assertIn("I'm", cleaned)
        self.assertIn("Sah-gur", cleaned)
        self.assertIn("Ray Hozha", cleaned)
        self.assertIn("Jah-bronee", cleaned)
        self.assertIn("Luke-ose", cleaned)
        self.assertIn("Kroo-sheff-skee", cleaned)
        self.assertIn("Mee-ko-nos", cleaned)
        self.assertNotIn("[laughs]", cleaned)
        self.assertNotIn("**", cleaned)

        chunks = split_script_into_tts_chunks(cleaned, max_words=12)
        self.assertEqual(len(chunks), 2)
        # Ensure balanced chunking never produces a tiny tail when total_words <= max_words
        single = split_script_into_tts_chunks(cleaned, max_words=500)
        self.assertEqual(len(single), 1)

        with tempfile.TemporaryDirectory() as tmp:
            wav_path = Path(tmp) / "out.wav"
            # 2 chunks of 0.1s silence at 24kHz 16-bit mono PCM (2400 samples * 2 bytes = 4800 bytes each)
            chunk_pcm = b"\x00\x00" * 2400
            pcm_chunks_to_wav([(chunk_pcm, 24000), (chunk_pcm, 24000)], wav_path, pause_ms=100)
            with wave.open(str(wav_path), "rb") as wf:
                self.assertEqual(wf.getnchannels(), 1)
                self.assertEqual(wf.getsampwidth(), 2)
                self.assertEqual(wf.getframerate(), 24000)
                # 2400 + 2400 (pause) + 2400 = 7200 frames (0.3s)
                self.assertEqual(wf.getnframes(), 7200)

    def test_gcs_rest_uploader_sends_auth_and_cache_headers(self):
        from unittest.mock import MagicMock, patch

        with tempfile.TemporaryDirectory() as tmp:
            sample = Path(tmp) / "feed.xml"
            sample.write_text("<rss/>", encoding="utf-8")
            mock_post = MagicMock(return_value=MagicMock(status_code=200))
            mock_patch = MagicMock(return_value=MagicMock(status_code=200))
            with patch("FantasyRecap.podcast_rss_builder._get_gcs_access_token", return_value="fake-tok"), \
                 patch("FantasyRecap.podcast_rss_builder.requests.post", mock_post), \
                 patch("FantasyRecap.podcast_rss_builder.requests.patch", mock_patch):
                url = upload_file_to_gcs(
                    sample,
                    "bfl-pod-secret123",
                    "feed.xml",
                    content_type="application/rss+xml; charset=utf-8",
                    cache_control="no-cache, max-age=60",
                )
            self.assertEqual(url, "https://storage.googleapis.com/bfl-pod-secret123/feed.xml")
            self.assertEqual(mock_post.call_count, 1)
            self.assertEqual(mock_post.call_args.kwargs["headers"]["Authorization"], "Bearer fake-tok")
            self.assertEqual(mock_patch.call_count, 1)


if __name__ == "__main__":
    unittest.main()
