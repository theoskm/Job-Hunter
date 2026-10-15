"""Blocked companies: course sellers posing as employers never reach you."""

from __future__ import annotations

import tempfile
import unittest
from datetime import date
from pathlib import Path

from app.database.db import Database
from app.database.models import Job
from app.filtering.filters import filter_jobs
from config.settings import JobProfile

PROFILE_PATH = Path(__file__).resolve().parent.parent / "config" / "job_profile.yaml"


def _job(company: str, title: str = "Trainee Project Coordinator") -> Job:
    return Job(title=title, company=company, location="Croydon",
               salary_min=26000, salary_max=30000,
               date_posted=date.today().isoformat(),
               description="Coordinate projects and support the team.")


class TestBlockedCompanies(unittest.TestCase):
    def setUp(self) -> None:
        self.profile = JobProfile.load(PROFILE_PATH)

    def test_profile_lists_the_course_sellers(self) -> None:
        self.assertIn("itol recruit", self.profile.exclude_companies)
        self.assertIn("it career switch", self.profile.exclude_companies)

    def test_blocked_names_are_filtered_regardless_of_case(self) -> None:
        result = filter_jobs([_job("ITOL Recruit"), _job("It Career  Switch Ltd")], self.profile)
        self.assertEqual(result.kept, [])
        self.assertTrue(all("blocked company" in why for _, why in result.rejected))

    def test_real_agencies_and_employers_are_kept(self) -> None:
        result = filter_jobs([_job("Office Angels"), _job("Bright Facilities Ltd")], self.profile)
        self.assertEqual(len(result.kept), 2)

    def test_existing_rows_removed_but_applied_ones_kept(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with Database(Path(tmp) / "t.db") as db:
                db.init_schema()
                new_id, _ = db.upsert_job(_job("ITOL Recruit", "Trainee Project Manager"))
                applied_id, _ = db.upsert_job(_job("ITOL Recruit", "Trainee Project Support"))
                keep_id, _ = db.upsert_job(_job("Office Angels", "Administrator"))
                db.set_status(applied_id, "APPLIED", changed_by="user")

                removed = db.remove_jobs_from_companies(self.profile.exclude_companies)

                self.assertEqual(removed, 1)
                self.assertIsNone(db.get_job(new_id))
                self.assertIsNotNone(db.get_job(applied_id))
                self.assertIsNotNone(db.get_job(keep_id))


if __name__ == "__main__":
    unittest.main(verbosity=2)
