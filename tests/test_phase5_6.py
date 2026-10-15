"""
Phase 5 + 6 tests: filtering, deduplication, scoring, and one full
end-to-end pipeline run with the collectors mocked.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database.db import Database
from app.database.models import Job, JobSource
from app.deduplication.dedup import dedupe_batch
from app.filtering.filters import filter_jobs
from app.pipeline import run_full_pipeline
from app.scoring.engine import ScoringEngine
from config.settings import JobProfile, Secrets, Settings

PROFILE_PATH = Path(__file__).resolve().parent.parent / "config" / "job_profile.yaml"


def _job(**overrides) -> Job:
    defaults = dict(
        title="Operations Coordinator",
        company="Bright Facilities Ltd",
        location="Croydon, Surrey",
        salary_min=28000,
        salary_max=32000,
        date_posted=date.today().isoformat(),  # was hardcoded — went stale within weeks
        description="Coordinate operations. Excellent Excel skills required.",
    )
    defaults.update(overrides)
    return Job(**defaults)


class TestFiltering(unittest.TestCase):
    def setUp(self) -> None:
        self.profile = JobProfile.load(PROFILE_PATH)  # minimum_salary = 21000

    def test_salary_below_floor_rejected(self) -> None:
        job = _job(salary_min=15000, salary_max=18000)
        result = filter_jobs([job], self.profile)
        self.assertEqual(len(result.kept), 0)
        self.assertIn("salary", result.rejected[0][1])

    def test_salary_above_floor_kept(self) -> None:
        job = _job(salary_min=25000, salary_max=30000)
        result = filter_jobs([job], self.profile)
        self.assertEqual(len(result.kept), 1)

    def test_missing_salary_not_rejected(self) -> None:
        """No salary listed shouldn't mean automatic rejection — many good ads omit it."""
        job = _job(salary_min=None, salary_max=None)
        result = filter_jobs([job], self.profile)
        self.assertEqual(len(result.kept), 1)

    def test_excluded_keyword_rejected(self) -> None:
        job = _job(description="Commission only role, cold calling required.")
        result = filter_jobs([job], self.profile)
        self.assertEqual(len(result.kept), 0)
        self.assertIn("excluded keyword", result.rejected[0][1])

    def test_old_job_rejected_by_default_window(self) -> None:
        old = (date.today() - timedelta(days=365)).isoformat()
        job = _job(date_posted=old)  # always long before the 7-day window
        result = filter_jobs([job], self.profile, max_age_days=7)
        self.assertEqual(len(result.kept), 0)
        self.assertIn("posted more than", result.rejected[0][1])

    def test_no_date_not_rejected(self) -> None:
        job = _job(date_posted=None)
        result = filter_jobs([job], self.profile, max_age_days=7)
        self.assertEqual(len(result.kept), 1)

    def test_recent_job_kept(self) -> None:
        recent = (date.today() - timedelta(days=2)).isoformat()
        job = _job(date_posted=recent)
        result = filter_jobs([job], self.profile, max_age_days=7)
        self.assertEqual(len(result.kept), 1)


class TestDeduplication(unittest.TestCase):
    def test_identical_jobs_collapse_to_one(self) -> None:
        job_a = _job(sources=[JobSource(source="reed", source_url="https://reed/1")])
        job_b = _job(sources=[JobSource(source="adzuna", source_url="https://adzuna/1")])

        result = dedupe_batch([job_a, job_b])

        self.assertEqual(len(result.unique), 1)
        self.assertEqual(result.duplicate_count, 1)
        self.assertEqual(len(result.unique[0].sources), 2)
        self.assertEqual({s.source for s in result.unique[0].sources}, {"reed", "adzuna"})

    def test_different_jobs_both_kept(self) -> None:
        job_a = _job(title="Operations Coordinator")
        job_b = _job(title="Project Administrator")
        result = dedupe_batch([job_a, job_b])
        self.assertEqual(len(result.unique), 2)
        self.assertEqual(result.duplicate_count, 0)

    def test_shorter_description_does_not_overwrite_longer_one(self) -> None:
        job_a = _job(description="Short teaser.")
        job_b = _job(description="A much longer and more complete description of the role.")
        result = dedupe_batch([job_a, job_b])
        self.assertIn("more complete", result.unique[0].description)

    def test_merge_fills_missing_salary(self) -> None:
        job_a = _job(salary_min=None, salary_max=None)
        job_b = _job(salary_min=28000, salary_max=32000)
        result = dedupe_batch([job_a, job_b])
        self.assertEqual(result.unique[0].salary_min, 28000)


class TestScoringEngine(unittest.TestCase):
    def setUp(self) -> None:
        self.profile = JobProfile.load(PROFILE_PATH)
        self.engine = ScoringEngine(self.profile)

    def test_score_is_within_bounds(self) -> None:
        job = _job()
        scored = self.engine.score(job)
        self.assertGreaterEqual(scored.score, 0)
        self.assertLessEqual(scored.score, 100)

    def test_sub_scores_sum_to_total(self) -> None:
        job = _job()
        scored = self.engine.score(job)
        total = (
            scored.career_fit + scored.transferable_skills + scored.accessibility
            + scored.skills_match + scored.salary_score + scored.location_score
            + scored.career_development
        )
        self.assertEqual(scored.score, total)

    def test_no_sub_score_exceeds_its_configured_cap(self) -> None:
        job = _job(
            description=(
                "no experience necessary training provided trainee graduate scheme "
                "excel microsoft office crm reporting customer service fast-paced "
                "under pressure project coordinator operations pmo training progression"
            )
        )
        scored = self.engine.score(job)
        weights = self.profile.scoring_weights
        self.assertLessEqual(scored.career_fit, weights["career_transition_fit"])
        self.assertLessEqual(scored.transferable_skills, weights["transferable_skills"])
        self.assertLessEqual(scored.accessibility, weights["entry_level_accessibility"])
        self.assertLessEqual(scored.skills_match, weights["skills_match"])
        self.assertLessEqual(scored.salary_score, weights["salary"])
        self.assertLessEqual(scored.location_score, weights["location"])
        self.assertLessEqual(scored.career_development, weights["career_development"])

    def test_accessible_role_scores_higher_than_senior_role(self) -> None:
        accessible = _job(
            title="Trainee Administrator",
            description="No experience necessary, full training provided. Entry level role.",
        )
        senior = _job(
            title="Senior Operations Manager",
            description="Must have 5 years experience essential. Proven track record required.",
        )
        acc_score = self.engine.score(accessible).accessibility
        sen_score = self.engine.score(senior).accessibility
        self.assertGreater(acc_score, sen_score)

    def test_ideal_location_scores_higher_than_unknown_location(self) -> None:
        near = _job(location="Croydon, Surrey")
        far = _job(location="Inverness")
        near_score = self.engine.score(near).location_score
        far_score = self.engine.score(far).location_score
        self.assertGreater(near_score, far_score)

    def test_salary_at_floor_scores_lower_than_excellent_salary(self) -> None:
        low = _job(salary_min=21000, salary_max=21000)
        high = _job(salary_min=40000, salary_max=42000)
        low_score = self.engine.score(low).salary_score
        high_score = self.engine.score(high).salary_score
        self.assertGreater(high_score, low_score)

    def test_missing_salary_scores_moderately_not_zero(self) -> None:
        job = _job(salary_min=None, salary_max=None)
        scored = self.engine.score(job)
        self.assertGreater(scored.salary_score, 0)

    def test_priority_band_matches_score(self) -> None:
        job = _job()
        scored = self.engine.score(job)
        self.assertEqual(scored.priority, self.profile.priority_for_score(scored.score))

    def test_tier_1_title_beats_unrelated_title_on_career_fit(self) -> None:
        tier1 = _job(title="Operations Coordinator", description="")
        unrelated = _job(title="Head Chef", description="")
        self.assertGreater(
            self.engine.score(tier1).career_fit,
            self.engine.score(unrelated).career_fit,
        )


class TestFullPipeline(unittest.TestCase):
    """One real end-to-end run: collect (mocked) -> filter -> dedup -> score -> persist."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "test.db"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    @patch("requests.get")
    def test_pipeline_end_to_end(self, mock_get: MagicMock) -> None:
        recent_iso = (date.today() - timedelta(days=2)).isoformat() + "T09:00:00Z"
        adzuna_response = MagicMock()
        adzuna_response.status_code = 200
        adzuna_response.raise_for_status.return_value = None
        adzuna_response.json.return_value = {
            "results": [
                {
                    "id": "1",
                    "title": "Operations Coordinator",
                    "company": {"display_name": "Bright Facilities Ltd"},
                    "location": {"display_name": "Croydon, Surrey"},
                    "salary_min": 28000,
                    "salary_max": 32000,
                    "created": recent_iso,
                    "description": "No experience necessary, full training provided. Excel needed.",
                    "redirect_url": "https://www.adzuna.co.uk/jobs/details/1",
                },
                {
                    "id": "2",
                    "title": "Sales Executive",
                    "company": {"display_name": "QuickWin Sales"},
                    "location": {"display_name": "London"},
                    "salary_min": 0,
                    "salary_max": None,
                    "created": recent_iso,
                    "description": "Commission only, cold calling, door to door sales.",
                    "redirect_url": "https://www.adzuna.co.uk/jobs/details/2",
                },
            ]
        }
        mock_get.return_value = adzuna_response

        settings = Settings(
            profile=JobProfile.load(PROFILE_PATH),
            secrets=Secrets(reed_api_key="", adzuna_app_id="id", adzuna_app_key="key",
                             database_path=str(self.db_path)),
        )

        with Database(self.db_path) as db:
            db.init_schema()
            report = run_full_pipeline(settings, db)

            # The commission-only sales role must be filtered before scoring
            self.assertEqual(report.filtered_out, 1)
            self.assertEqual(report.scored, 1)
            self.assertEqual(report.new_jobs, 1)

            row = db.conn.execute("SELECT * FROM jobs").fetchone()
            self.assertEqual(row["title"], "Operations Coordinator")
            self.assertIsNotNone(row["score"])
            self.assertGreater(row["score"], 0)

    @patch("requests.get")
    def test_second_run_does_not_duplicate_or_overwrite_user_status(
        self, mock_get: MagicMock
    ) -> None:
        response = MagicMock()
        response.status_code = 200
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "results": [{
                "id": "1", "title": "Operations Coordinator",
                "company": {"display_name": "Bright Facilities Ltd"},
                "location": {"display_name": "Croydon, Surrey"},
                "salary_min": 28000, "salary_max": 32000,
                "created": (date.today() - timedelta(days=1)).isoformat() + "T09:00:00Z",
                "description": "Coordinate operations.",
                "redirect_url": "https://www.adzuna.co.uk/jobs/details/1",
            }]
        }
        mock_get.return_value = response
        settings = Settings(
            profile=JobProfile.load(PROFILE_PATH),
            secrets=Secrets(reed_api_key="", adzuna_app_id="id", adzuna_app_key="key",
                             database_path=str(self.db_path)),
        )

        with Database(self.db_path) as db:
            db.init_schema()
            run_full_pipeline(settings, db)
            job_id = db.conn.execute("SELECT job_id FROM jobs").fetchone()["job_id"]
            db.set_status(job_id, "APPLIED", changed_by="user")

            run_full_pipeline(settings, db)  # simulates tomorrow's run finding it again

            count = db.conn.execute("SELECT COUNT(*) AS n FROM jobs").fetchone()["n"]
            status = db.get_job(job_id)["status"]
            self.assertEqual(count, 1, "second run must not create a duplicate row")
            self.assertEqual(status, "APPLIED", "second run must not overwrite the user's status")


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestTruncationAwareScoring(unittest.TestCase):
    """
    Adzuna truncates descriptions at 500 chars. Four of the seven scoring
    components read description text, so without this handling a perfectly
    good job scores low purely because the advert was cut off.
    """

    def setUp(self) -> None:
        self.profile = JobProfile.load(PROFILE_PATH)
        self.engine = ScoringEngine(self.profile)

    def _job(self, description: str) -> Job:
        return Job(
            title="Operations Coordinator",
            company="Example Ltd",
            location="Croydon, Surrey",
            salary_min=28000,
            salary_max=30000,
            date_posted=date.today().isoformat(),
            description=description,
        )

    def test_detects_500_char_truncation(self) -> None:
        from app.scoring.engine import is_truncated

        self.assertTrue(is_truncated(self._job("A" * 500)))
        self.assertFalse(is_truncated(self._job("A" * 100)))

    def test_detects_ellipsis_truncation(self) -> None:
        from app.scoring.engine import is_truncated

        self.assertTrue(is_truncated(self._job("Short advert that trails off...")))

    def test_truncated_advert_scores_above_floor(self) -> None:
        """A truncated advert with no keywords should score as 'unknown', not 'bad'."""
        scored = self.engine.score_all([self._job("A" * 500)])[0]
        # Without the fix this landed around 49; unknown should sit mid-range
        self.assertGreater(scored.score, 55)

    def test_full_advert_still_outscores_truncated_one(self) -> None:
        """The fix must not flatten real signal — a good full advert still wins."""
        rich = (
            "Full training provided, no experience necessary, entry level. "
            "Fast-paced, organised, coordinate, deadline, communication, teamwork. "
            "Excel, Outlook, scheduling, documentation, records. "
            "Career progression and professional development."
        ) * 4
        truncated_score = self.engine.score_all([self._job("A" * 500)])[0].score
        full_score = self.engine.score_all([self._job(rich)])[0].score
        self.assertGreater(full_score, truncated_score)

    def test_short_complete_advert_not_treated_as_truncated(self) -> None:
        """A genuinely short but complete advert keeps the normal (stricter) floors."""
        from app.scoring.engine import is_truncated

        self.assertFalse(is_truncated(self._job("Brief but complete advert text.")))


class TestJunkAndSeniorityFilters(unittest.TestCase):
    """
    Both filters came from real defects in a live run: a DPD placeholder
    listing scored joint-top, and Adzuna's loose matching returned
    Operations Manager / Field Service Manager against entry-level searches.
    """

    def _job(self, title: str) -> Job:
        return Job(title=title, company="Example Ltd", location="Croydon, Surrey")

    def test_rejects_placeholder_listings(self) -> None:
        from app.filtering.filters import _is_junk_listing

        self.assertIsNotNone(_is_junk_listing(self._job("Admin Purposes Only")))
        self.assertIsNotNone(_is_junk_listing(self._job("TEST JOB - do not apply")))
        self.assertIsNone(_is_junk_listing(self._job("Finance Administrator")))

    def test_rejects_roles_above_entry_level(self) -> None:
        from app.filtering.filters import _is_too_senior

        for title in ("Operations Manager", "Field Service Manager",
                      "Head of Operations", "Store Merchandising Team Leader"):
            self.assertIsNotNone(_is_too_senior(self._job(title)), title)

    def test_protects_target_titles_containing_manager_words(self) -> None:
        """
        The important one. These are on the candidate's own Tier 2/3 target
        list — a naive 'reject anything containing manager' would delete
        exactly the roles being searched for.
        """
        for title in ("Junior Project Manager", "Assistant Project Manager",
                      "Management Trainee", "Operations Trainee",
                      "Graduate Management Scheme"):
            from app.filtering.filters import _is_too_senior

            self.assertIsNone(_is_too_senior(self._job(title)), title)

    def test_standard_target_titles_pass_both_filters(self) -> None:
        from app.filtering.filters import _is_junk_listing, _is_too_senior

        for title in ("Project Coordinator", "PMO Assistant", "Office Coordinator",
                      "Business Support Coordinator", "Junior Administrator"):
            job = self._job(title)
            self.assertIsNone(_is_junk_listing(job), title)
            self.assertIsNone(_is_too_senior(job), title)


class TestDomainRelevanceFilter(unittest.TestCase):
    """
    Found in the candidate's own live run: 'Junior CNC Machinist' and
    'Quantity Surveyor' both reached the top 10, purely because 'junior'
    was enough to survive the seniority filter regardless of field.
    Seniority answers 'is this too senior'; this answers 'is this even
    the right field'. A title must clear both.
    """

    def _job(self, title: str) -> Job:
        return Job(title=title, company="Example Ltd", location="Croydon, Surrey")

    def test_rejects_the_exact_titles_found_in_a_live_run(self) -> None:
        from app.filtering.filters import _is_irrelevant_domain

        for title in ("Quantity Surveyor (Luxury Interiors / Fit-Out)",
                      "Junior CNC Machinist"):
            self.assertTrue(_is_irrelevant_domain(self._job(title)), title)

    def test_keeps_the_legitimate_admin_titles_from_the_same_run(self) -> None:
        from app.filtering.filters import _is_irrelevant_domain

        for title in ("Administrator", "Partnership Administrator",
                      "Administrator Receptionist", "Production and Office Assistant",
                      "Operation and compliance coordinator",
                      "Graduate Operational Excellence Analyst",
                      "Administration Officer"):
            self.assertFalse(_is_irrelevant_domain(self._job(title)), title)

    def test_all_target_tier_titles_still_pass_relevance(self) -> None:
        """Every title in job_profile.yaml's target_roles must clear this filter."""
        from app.filtering.filters import _is_irrelevant_domain

        profile = JobProfile.load(PROFILE_PATH)
        for title in profile.all_target_roles:
            self.assertFalse(_is_irrelevant_domain(self._job(title)), title)

    def test_senior_is_now_caught(self) -> None:
        """'Senior' was missing from the marker list — a real job slipped through on it."""
        from app.filtering.filters import _is_too_senior

        self.assertIsNotNone(_is_too_senior(self._job("Senior Pensions Administrator")))

    def test_full_pipeline_rejects_both_real_failures(self) -> None:
        jobs = [
            Job(title="Quantity Surveyor (Luxury Interiors / Fit-Out)",
                company="Ernest Gordon Recruitment", location="London",
                salary_min=35000, salary_max=45000),
            Job(title="Junior CNC Machinist", company="Centronic LTD",
                location="Croydon", salary_min=26000, salary_max=28000),
            Job(title="Administrator", company="Rape Crisis South London",
                location="Croydon", salary_min=30000, salary_max=32000),
        ]
        profile = JobProfile.load(PROFILE_PATH)
        result = filter_jobs(jobs, profile, max_age_days=365)
        kept_titles = [j.title for j in result.kept]
        self.assertEqual(kept_titles, ["Administrator"])
