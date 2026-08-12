"""
Phase 2 + 3 tests. Stdlib unittest only — no network, no API keys.

Run from the project root:
    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database.db import Database
from app.database.models import (
    Job, JobSource, build_canonical_key, normalise_company, normalise_location,
)
from app.timeutils import is_scheduled_run_time, utc_offset_hours
from config.settings import ConfigError, JobProfile

from tests.mock_jobs import MOCK_ADZUNA_1, MOCK_REED_1, MOCK_REED_2

PROFILE_PATH = Path(__file__).resolve().parent.parent / "config" / "job_profile.yaml"


class TestConfig(unittest.TestCase):
    def setUp(self) -> None:
        self.profile = JobProfile.load(PROFILE_PATH)

    def test_profile_loads(self) -> None:
        self.assertTrue(self.profile.all_target_roles)
        self.assertTrue(self.profile.locations)

    def test_weights_total_100(self) -> None:
        self.assertEqual(sum(self.profile.scoring_weights.values()), 100)

    def test_rejects_weights_that_do_not_total_100(self) -> None:
        broken = JobProfile(raw=dict(self.profile.raw))
        broken.raw["scoring_weights"] = {
            "career_transition_fit": 30, "transferable_skills": 20,
            "entry_level_accessibility": 20, "skills_match": 15,
            "salary": 10, "location": 5, "career_development": 5,
        }
        with self.assertRaises(ConfigError) as ctx:
            broken.validate()
        self.assertIn("105", str(ctx.exception))

    def test_location_scoring(self) -> None:
        self.assertEqual(self.profile.location_score("Croydon, Surrey"), 5)
        self.assertEqual(self.profile.location_score("Sutton"), 5)
        # Unknown place falls back to the worst score rather than crashing
        self.assertEqual(self.profile.location_score("Aberdeen"), 1)

    def test_tier_detection(self) -> None:
        self.assertEqual(self.profile.tier_for_role("Operations Coordinator"), 1)
        self.assertEqual(self.profile.tier_for_role("Facilities Coordinator"), 2)
        self.assertEqual(self.profile.tier_for_role("Junior Project Manager"), 3)
        self.assertIsNone(self.profile.tier_for_role("Head Chef"))

    def test_priority_bands(self) -> None:
        self.assertEqual(self.profile.priority_for_score(94), "MUST APPLY")
        self.assertEqual(self.profile.priority_for_score(83), "HIGH")
        self.assertEqual(self.profile.priority_for_score(71), "GOOD")
        self.assertEqual(self.profile.priority_for_score(64), "STRETCH")
        self.assertEqual(self.profile.priority_for_score(42), "LOW")


class TestDaylightSaving(unittest.TestCase):
    """The GitHub Actions UTC-cron problem."""

    def test_winter_gmt_08_utc_is_08_local(self) -> None:
        jan = datetime(2027, 1, 15, 8, 0, tzinfo=timezone.utc)
        self.assertEqual(utc_offset_hours(jan), 0.0)
        self.assertTrue(is_scheduled_run_time(jan))

    def test_winter_07_utc_is_not_08_local(self) -> None:
        jan = datetime(2027, 1, 15, 7, 0, tzinfo=timezone.utc)
        self.assertFalse(is_scheduled_run_time(jan))

    def test_summer_bst_07_utc_is_08_local(self) -> None:
        jul = datetime(2027, 7, 15, 7, 0, tzinfo=timezone.utc)
        self.assertEqual(utc_offset_hours(jul), 1.0)
        self.assertTrue(is_scheduled_run_time(jul))

    def test_summer_08_utc_is_not_08_local(self) -> None:
        jul = datetime(2027, 7, 15, 8, 0, tzinfo=timezone.utc)
        self.assertFalse(is_scheduled_run_time(jul))

    def test_exactly_one_trigger_fires_each_day(self) -> None:
        """Both crons run daily; precisely one must pass the gate."""
        for date in (datetime(2027, 1, 15), datetime(2027, 7, 15),
                     datetime(2027, 3, 28), datetime(2027, 10, 31)):
            fired = [
                is_scheduled_run_time(
                    datetime(date.year, date.month, date.day, h, 0, tzinfo=timezone.utc)
                )
                for h in (7, 8)
            ]
            self.assertEqual(
                sum(fired), 1,
                f"{date:%Y-%m-%d}: expected exactly one trigger, got {fired}",
            )

    def test_tolerates_actions_scheduling_lag(self) -> None:
        late = datetime(2027, 1, 15, 8, 20, tzinfo=timezone.utc)
        self.assertTrue(is_scheduled_run_time(late))


class TestNormalisation(unittest.TestCase):
    def test_company_suffixes_collapse(self) -> None:
        self.assertEqual(
            normalise_company("Bright Facilities Ltd"),
            normalise_company("Bright Facilities Limited"),
        )

    def test_location_county_suffix_ignored(self) -> None:
        self.assertEqual(
            normalise_location("Croydon, Surrey"), normalise_location("Croydon")
        )

    def test_canonical_key_matches_across_boards(self) -> None:
        self.assertEqual(
            build_canonical_key("Operations Coordinator", "Bright Facilities Ltd",
                                "Croydon, Surrey"),
            build_canonical_key("Operations Coordinator", "Bright Facilities Limited",
                                "Croydon"),
        )

    def test_different_jobs_do_not_collide(self) -> None:
        self.assertNotEqual(
            MOCK_REED_1.canonical_key, MOCK_REED_2.canonical_key
        )


class TestDatabase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmp.name) / "test.db")
        self.db.init_schema()

    def tearDown(self) -> None:
        self.db.close()
        self.tmp.cleanup()

    def test_insert_creates_job(self) -> None:
        job_id, existed = self.db.upsert_job(MOCK_REED_2)
        self.assertFalse(existed)
        self.assertIsNotNone(self.db.get_job(job_id))

    def test_cross_board_duplicate_collapses_to_one_record(self) -> None:
        id_a, _ = self.db.upsert_job(MOCK_REED_1)
        self.db.add_source(id_a, MOCK_REED_1.sources[0])

        id_b, existed = self.db.upsert_job(MOCK_ADZUNA_1)
        self.db.add_source(id_b, MOCK_ADZUNA_1.sources[0])

        self.assertTrue(existed, "second board should match the existing record")
        self.assertEqual(id_a, id_b, "duplicate must not create a second job_id")

        rows = self.db.conn.execute("SELECT COUNT(*) AS n FROM jobs").fetchone()
        self.assertEqual(rows["n"], 1)

        sources = self.db.get_sources(id_a)
        self.assertEqual(len(sources), 2)
        self.assertEqual({s["source"] for s in sources}, {"reed", "adzuna"})

    def test_identical_source_url_not_stored_twice(self) -> None:
        job_id, _ = self.db.upsert_job(MOCK_REED_1)
        self.assertTrue(self.db.add_source(job_id, MOCK_REED_1.sources[0]))
        self.assertFalse(self.db.add_source(job_id, MOCK_REED_1.sources[0]))
        self.assertEqual(len(self.db.get_sources(job_id)), 1)

    def test_automation_never_overwrites_user_status(self) -> None:
        """The single most important guarantee in the CRM."""
        job_id, _ = self.db.upsert_job(MOCK_REED_1)
        self.db.set_status(job_id, "APPLIED", changed_by="user")

        # A later automated run rediscovers the same job as NEW
        rediscovered = Job(
            title=MOCK_REED_1.title,
            company=MOCK_REED_1.company,
            location=MOCK_REED_1.location,
            description=MOCK_REED_1.description + " Updated advert text.",
            status="NEW",
        )
        self.db.upsert_job(rediscovered)

        row = self.db.get_job(job_id)
        self.assertEqual(row["status"], "APPLIED", "user status was overwritten")
        self.assertEqual(row["status_source"], "user")

    def test_automated_refresh_updates_details_but_not_status(self) -> None:
        job_id, _ = self.db.upsert_job(MOCK_REED_1)
        self.db.set_status(job_id, "SHORTLISTED", changed_by="user")

        updated = Job(
            title=MOCK_REED_1.title,
            company=MOCK_REED_1.company,
            location=MOCK_REED_1.location,
            salary_max=35000,
            description="Revised advert.",
            status="NEW",
        )
        self.db.upsert_job(updated)

        row = self.db.get_job(job_id)
        self.assertEqual(row["status"], "SHORTLISTED")
        self.assertEqual(row["salary_max"], 35000, "details should still refresh")

    def test_status_history_is_recorded(self) -> None:
        job_id, _ = self.db.upsert_job(MOCK_REED_2)
        self.db.set_status(job_id, "SHORTLISTED", changed_by="user")
        self.db.set_status(job_id, "APPLIED", changed_by="user", note="applied online")

        history = self.db.get_status_history(job_id)
        self.assertEqual(len(history), 3)  # created + two changes
        self.assertEqual(history[-1]["new_status"], "APPLIED")
        self.assertEqual(history[-1]["changed_by"], "user")

    def test_invalid_status_rejected(self) -> None:
        job_id, _ = self.db.upsert_job(MOCK_REED_2)
        with self.assertRaises(ValueError):
            self.db.set_status(job_id, "MAYBE LATER")

    def test_alert_not_sent_twice(self) -> None:
        job = Job(title="Ops Assistant", company="Acme", location="Croydon", score=88)
        job_id, _ = self.db.upsert_job(job)

        self.assertEqual(len(self.db.jobs_needing_alert(80)), 1)
        self.db.mark_alert_sent([job_id])
        self.assertEqual(len(self.db.jobs_needing_alert(80)), 0)

    def test_ai_analysis_cache_control(self) -> None:
        job = Job(title="Ops Assistant", company="Acme", location="Croydon",
                  description="Original text")
        self.assertTrue(self.db.needs_ai_analysis(job.canonical_key, job.description_hash))

        job_id, _ = self.db.upsert_job(job)
        self.db.conn.execute(
            "UPDATE jobs SET analysis_hash = ? WHERE job_id = ?",
            (job.description_hash, job_id),
        )
        self.db.conn.commit()
        self.assertFalse(
            self.db.needs_ai_analysis(job.canonical_key, job.description_hash),
            "unchanged description should not be re-analysed",
        )

        changed = Job(title="Ops Assistant", company="Acme", location="Croydon",
                      description="Rewritten advert")
        self.assertTrue(
            self.db.needs_ai_analysis(changed.canonical_key, changed.description_hash)
        )

    def test_run_log(self) -> None:
        run_id = self.db.start_run("reed")
        self.db.finish_run(run_id, jobs_found=12, jobs_new=3, jobs_duplicate=9)
        row = self.db.conn.execute(
            "SELECT * FROM run_log WHERE id = ?", (run_id,)
        ).fetchone()
        self.assertEqual(row["status"], "success")
        self.assertEqual(row["jobs_new"], 3)


if __name__ == "__main__":
    unittest.main(verbosity=2)
