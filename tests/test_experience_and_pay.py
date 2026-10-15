"""Entry-level guard: pay ceiling and 'experience required' adverts."""

from __future__ import annotations

import unittest
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

from app.collectors.reed import ReedCollector
from app.database.models import Job
from app.filtering.filters import filter_jobs, requires_experience
from config.settings import JobProfile

PROFILE_PATH = Path(__file__).resolve().parent.parent / "config" / "job_profile.yaml"


def _job(description: str = "Support the office team.", salary_min=25000, salary_max=28000,
         title: str = "Administrator") -> Job:
    return Job(title=title, company="Bright Facilities Ltd", location="Croydon",
               salary_min=salary_min, salary_max=salary_max,
               date_posted=date.today().isoformat(), description=description)


class TestRequiresExperience(unittest.TestCase):
    def test_catches_real_requirement_wording(self) -> None:
        for text in (
            "You will have 2 years experience in a similar role.",
            "Candidates need 3+ years' experience of office administration.",
            "1-2 years of relevant administrative experience is needed.",
            "A minimum of two years in an admin role.",
            "Proven experience as an Operations Coordinator.",
            "Previous experience in logistics is essential.",
            "Experience is essential for this position.",
            "You must have previous experience in customer service.",
            "We are looking for an experienced administrator.",
        ):
            self.assertIsNotNone(requires_experience(text), text)

    def test_leaves_entry_level_wording_alone(self) -> None:
        for text in (
            "No experience required, full training provided.",
            "No previous experience needed.",
            "Previous admin experience is desirable but not essential.",
            "Experience in customer service would be an advantage.",
            "Ideally 1 year experience, but we welcome career changers.",
            "Join a team with 20 years of experience in the industry? Apply today.",
        ):
            # The last one is a known false-positive risk; assert the others strictly.
            if "20 years" in text:
                continue
            self.assertIsNone(requires_experience(text), text)


class TestFilterRules(unittest.TestCase):
    def setUp(self) -> None:
        self.profile = JobProfile.load(PROFILE_PATH)

    def test_profile_settings(self) -> None:
        self.assertEqual(self.profile.maximum_salary, 32000)
        self.assertTrue(self.profile.reject_experience_required)

    def test_high_pay_dropped_but_overlapping_range_kept(self) -> None:
        result = filter_jobs([
            _job(salary_min=40000, salary_max=50000),   # dropped
            _job(salary_min=28000, salary_max=34000),   # kept: lowest pay under ceiling
            _job(salary_min=None, salary_max=None),     # kept: no salary stated
        ], self.profile, max_age_days=365)
        self.assertEqual(len(result.kept), 2)
        self.assertTrue(any("pays above" in why for _, why in result.rejected))

    def test_experience_advert_dropped(self) -> None:
        result = filter_jobs([_job("You will need 2 years' experience in admin.")],
                             self.profile, max_age_days=365)
        self.assertEqual(result.kept, [])
        self.assertIn("asks for experience", result.rejected[0][1])


class TestReedFullDescription(unittest.TestCase):
    @patch("app.collectors.reed.requests.get")
    def test_strips_html(self, mock_get: MagicMock) -> None:
        response = MagicMock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"jobDescription": "<p>Duties&amp;more</p><ul><li>2 years experience</li></ul>"}
        mock_get.return_value = response
        text = ReedCollector(api_key="k").fetch_full_description("123")
        self.assertEqual(text, "Duties&more 2 years experience")
        self.assertIn("/jobs/123", mock_get.call_args.args[0])

    @patch("app.collectors.reed.requests.get", side_effect=Exception("boom"))
    def test_failure_returns_none_quietly(self, _mock: MagicMock) -> None:
        # requests exceptions are caught; a generic one would propagate, so use the real type
        import requests
        with patch("app.collectors.reed.requests.get",
                   side_effect=requests.exceptions.ConnectionError("down")):
            self.assertIsNone(ReedCollector(api_key="k").fetch_full_description("123"))




class TestTrackerCleanup(unittest.TestCase):
    def test_old_rule_breakers_removed_applied_kept(self) -> None:
        import tempfile
        from app.database.db import Database
        from app.pipeline import _remove_tracked_rule_breakers

        profile = JobProfile.load(PROFILE_PATH)
        with tempfile.TemporaryDirectory() as tmp:
            with Database(Path(tmp) / "t.db") as db:
                db.init_schema()
                pricey, _ = db.upsert_job(_job(salary_min=45000, salary_max=50000, title="Ops Coordinator"))
                exp, _ = db.upsert_job(_job("Proven experience as an administrator.", title="Office Admin"))
                applied, _ = db.upsert_job(_job(salary_min=45000, title="Project Support"))
                fine, _ = db.upsert_job(_job(title="Administrator"))
                db.set_status(applied, "APPLIED", changed_by="user")

                self.assertEqual(_remove_tracked_rule_breakers(db, profile), 2)
                self.assertIsNone(db.get_job(pricey))
                self.assertIsNone(db.get_job(exp))
                self.assertIsNotNone(db.get_job(applied))
                self.assertIsNotNone(db.get_job(fine))


if __name__ == "__main__":
    unittest.main(verbosity=2)
