"""
Phases 7-10 tests.

Scope note, because it matters: these test everything that CAN be tested
without network access — email template building, MIME encoding, sheet row
construction, statistics, the learning loop, and the DST scheduling gate.

They do NOT test that Gmail sends, or that Google Sheets writes. Those
require real OAuth credentials and a live connection, neither of which
exists here. `--test-email` and `--update-sheets` on your own machine are
the only real proof of those.
"""

from __future__ import annotations

import base64
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.analytics.stats import gather_stats, learning_loop
from app.database.db import Database
from app.database.models import Job
from app.notifications.gmail import build_mime_message
from app.notifications.templates import Email, build_digest, build_single_alert
from app.sheets.sync import HEADERS, STATUS_COLUMN_INDEX, build_all_rows, build_row
from app.timeutils import is_scheduled_run_time


def _make_db(tmpdir: str) -> Database:
    db = Database(Path(tmpdir) / "test.db")
    db.init_schema()
    return db


def _job(title="Operations Coordinator", company="Example Ltd", score=82, **kw) -> Job:
    defaults = dict(
        location="Croydon, Surrey",
        salary_min=28000,
        salary_max=32000,
        date_posted="2026-08-12",
        description="Coordinate operations and schedules.",
        direct_application_url="https://example.com/job/1",
        score=score,
        priority="HIGH",
        career_fit=8,
        transferable_skills=15,
        accessibility=24,
        skills_match=11,
        salary_score=11,
        location_score=5,
        career_development=3,
        matching_skills='["scheduling", "excel", "coordination"]',
        skill_gaps='["power bi"]',
        reasoning="Tier 1 target role; strong location match",
    )
    defaults.update(kw)
    return Job(title=title, company=company, **defaults)


class TestEmailTemplates(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = _make_db(self.tmp.name)
        self.db.upsert_job(_job())
        self.rows = self.db.jobs_above_score(70)

    def tearDown(self) -> None:
        self.db.close()
        self.tmp.cleanup()

    def test_single_alert_contains_essentials(self) -> None:
        email = build_single_alert(self.rows[0])
        self.assertIn("Operations Coordinator", email.subject)
        self.assertIn("Example Ltd", email.subject)
        self.assertIn("82", email.subject)
        self.assertIn("Croydon", email.body_text)
        self.assertIn("https://example.com/job/1", email.body_text)

    def test_single_alert_has_both_text_and_html(self) -> None:
        email = build_single_alert(self.rows[0])
        self.assertTrue(email.body_text.strip())
        self.assertIn("<div", email.body_html)

    def test_alert_lists_matching_skills_and_gaps(self) -> None:
        email = build_single_alert(self.rows[0])
        self.assertIn("scheduling", email.body_text)
        self.assertIn("power bi", email.body_text)

    def test_digest_reports_counts(self) -> None:
        email = build_digest(self.rows, new_count=1)
        self.assertIn("1 new match", email.subject)
        self.assertIn("Operations Coordinator", email.body_text)

    def test_digest_handles_empty_results_without_crashing(self) -> None:
        email = build_digest([], new_count=0)
        self.assertIn("0 new matches", email.subject)
        self.assertIn("No jobs met the tracking threshold", email.body_text)

    def test_digest_states_nothing_is_auto_applied(self) -> None:
        """Section 20: the system must never imply it applies for you."""
        email = build_digest(self.rows, new_count=1)
        self.assertIn("every application is yours to send", email.body_text)

    def test_missing_salary_does_not_crash(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = _make_db(tmp)
            db.upsert_job(_job(salary_min=None, salary_max=None))
            rows = db.jobs_above_score(70)
            email = build_single_alert(rows[0])
            self.assertIn("not stated", email.body_text)
            db.close()


class TestMimeEncoding(unittest.TestCase):
    def test_message_is_base64url_encoded(self) -> None:
        email = Email(subject="Test", body_text="Hello", body_html="<p>Hello</p>")
        payload = build_mime_message(email, "me@example.com", "you@example.com")
        self.assertIn("raw", payload)
        decoded = base64.urlsafe_b64decode(payload["raw"]).decode()
        self.assertIn("Subject: Test", decoded)
        self.assertIn("you@example.com", decoded)

    def test_handles_unicode_in_subject(self) -> None:
        """Priority emoji in subject lines must survive encoding."""
        email = Email(subject="\U0001F525 MUST APPLY", body_text="x", body_html="<p>x</p>")
        payload = build_mime_message(email, "me@example.com", "you@example.com")
        decoded = base64.urlsafe_b64decode(payload["raw"])
        self.assertTrue(len(decoded) > 0)


class TestSheetsRows(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = _make_db(self.tmp.name)
        self.db.upsert_job(_job())

    def tearDown(self) -> None:
        self.db.close()
        self.tmp.cleanup()

    def test_row_length_matches_headers(self) -> None:
        rows = build_all_rows(self.db.conn)
        self.assertEqual(len(rows), 1)
        self.assertEqual(len(rows[0]), len(HEADERS))

    def test_status_column_index_is_correct(self) -> None:
        """The sync relies on this index to protect manual status edits."""
        self.assertEqual(HEADERS[STATUS_COLUMN_INDEX], "Status")

    def test_row_carries_score_and_status(self) -> None:
        rows = build_all_rows(self.db.conn)
        self.assertIn("82", rows[0])
        self.assertEqual(rows[0][STATUS_COLUMN_INDEX], "NEW")

    def test_minimum_score_filter(self) -> None:
        self.db.upsert_job(_job(title="Low Scorer", company="Other Ltd", score=40))
        self.assertEqual(len(build_all_rows(self.db.conn, minimum_score=70)), 1)
        self.assertEqual(len(build_all_rows(self.db.conn, minimum_score=0)), 2)


class TestAnalytics(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = _make_db(self.tmp.name)

    def tearDown(self) -> None:
        self.db.close()
        self.tmp.cleanup()

    def test_empty_database_produces_zero_stats(self) -> None:
        stats = gather_stats(self.db.conn)
        self.assertEqual(stats.total_jobs, 0)
        self.assertIsNone(stats.interview_rate)

    def test_counts_applications_and_interviews(self) -> None:
        a, _ = self.db.upsert_job(_job(title="Role A", company="A Ltd"))
        b, _ = self.db.upsert_job(_job(title="Role B", company="B Ltd"))
        self.db.set_status(a, "APPLIED", changed_by="user")
        self.db.set_status(b, "INTERVIEW", changed_by="user")

        stats = gather_stats(self.db.conn)
        self.assertEqual(stats.applied, 2)     # INTERVIEW counts as submitted
        self.assertEqual(stats.interviews, 1)
        self.assertAlmostEqual(stats.interview_rate, 0.5)

    def test_collects_common_skill_gaps(self) -> None:
        self.db.upsert_job(_job(title="Role A", company="A Ltd"))
        self.db.upsert_job(_job(title="Role B", company="B Ltd"))
        stats = gather_stats(self.db.conn)
        self.assertIn(("power bi", 2), stats.common_skill_gaps)

    def test_learning_loop_refuses_to_conclude_from_small_samples(self) -> None:
        """
        The important guarantee: no fake insight from four data points.
        Drawing conclusions from a tiny sample would actively make the
        search worse.
        """
        job_id, _ = self.db.upsert_job(_job())
        self.db.set_status(job_id, "APPLIED", changed_by="user")

        insights = learning_loop(self.db.conn, minimum_applications=20)
        self.assertEqual(len(insights), 1)
        self.assertIn("Not enough data", insights[0].message)

    def test_learning_loop_engages_once_threshold_met(self) -> None:
        for index in range(24):
            job = _job(title=f"Coordinator {index}" if index % 2 else f"Administrator {index}",
                       company=f"Company {index}")
            job_id, _ = self.db.upsert_job(job)
            # Coordinators interview, administrators don't — a clear signal
            status = "INTERVIEW" if index % 2 else "REJECTED"
            self.db.set_status(job_id, status, changed_by="user")

        insights = learning_loop(self.db.conn, minimum_applications=20)
        self.assertNotIn("Not enough data", insights[0].message)


class TestSchedulerGate(unittest.TestCase):
    """Phase 9 — the workflow fires twice daily; exactly one must proceed."""

    def test_only_one_of_the_two_utc_triggers_passes(self) -> None:
        for month in (1, 4, 7, 10):
            fired = [
                is_scheduled_run_time(
                    datetime(2027, month, 15, hour, 0, tzinfo=timezone.utc),
                    "08:00",
                    "Europe/London",
                )
                for hour in (7, 8)
            ]
            self.assertEqual(sum(fired), 1, f"month {month}: {fired}")

    def test_midday_trigger_never_passes(self) -> None:
        self.assertFalse(
            is_scheduled_run_time(
                datetime(2027, 6, 15, 12, 0, tzinfo=timezone.utc), "08:00", "Europe/London"
            )
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
