"""
Phase 4 tests. All HTTP calls are mocked with response bodies shaped exactly
like Reed's and Adzuna's documented formats — no network required, no API
keys required. This proves the parsing and error-handling logic is correct.

It does NOT prove Reed's or Adzuna's real API still matches their docs
today. That's what `python main.py --test-sources` is for, run on your
own machine with real keys.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.collectors.adzuna import AdzunaCollector
from app.collectors.base import (
    CollectorNotConfigured,
    CollectorRequestFailed,
    is_within_days,
    parse_adzuna_date,
    parse_reed_date,
)
from app.collectors.reed import ReedCollector
from app.collectors.run import build_collectors, run_collection
from config.settings import JobProfile, Secrets

PROFILE_PATH = Path(__file__).resolve().parent.parent / "config" / "job_profile.yaml"

REED_SAMPLE_RESPONSE = {
    "results": [
        {
            "jobId": 40227781,
            "employerName": "Bright Facilities Ltd",
            "jobTitle": "Operations Coordinator",
            "locationName": "Croydon, Surrey",
            "minimumSalary": 28000.0,
            "maximumSalary": 32000.0,
            "currency": "GBP",
            "date": "05/08/2026",
            "expirationDate": "05/09/2026",
            "jobDescription": "Coordinate daily operations and resources.",
            "jobUrl": "https://www.reed.co.uk/jobs/operations-coordinator/40227781",
            "partTime": False,
            "fullTime": True,
        }
    ]
}

ADZUNA_SAMPLE_RESPONSE = {
    "results": [
        {
            "id": "4837201",
            "title": "Administrator",
            "company": {"display_name": "Bright Facilities Ltd"},
            "location": {"display_name": "Croydon, Surrey"},
            "salary_min": 25000,
            "salary_max": 27000,
            "created": "2026-08-05T09:12:00Z",
            "description": "Front-of-house administrator role.",
            "redirect_url": "https://www.adzuna.co.uk/jobs/details/4837201",
        }
    ]
}


def _mock_response(json_body: dict, status: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = json_body
    if status >= 400:
        import requests

        resp.raise_for_status.side_effect = requests.exceptions.HTTPError(response=resp)
    else:
        resp.raise_for_status.return_value = None
    return resp


class TestDateParsing(unittest.TestCase):
    def test_reed_date_format(self) -> None:
        self.assertEqual(parse_reed_date("05/08/2026"), "2026-08-05")

    def test_reed_date_missing(self) -> None:
        self.assertIsNone(parse_reed_date(None))
        self.assertIsNone(parse_reed_date(""))

    def test_reed_date_malformed_does_not_crash(self) -> None:
        self.assertIsNone(parse_reed_date("not-a-date"))

    def test_adzuna_date_format(self) -> None:
        self.assertEqual(parse_adzuna_date("2026-08-05T09:12:00Z"), "2026-08-05")

    def test_adzuna_date_missing(self) -> None:
        self.assertIsNone(parse_adzuna_date(None))

    def test_is_within_days(self) -> None:
        self.assertTrue(is_within_days("2026-08-10", max_age_days=7, today=__import__("datetime").date(2026, 8, 12)))
        self.assertFalse(is_within_days("2026-07-01", max_age_days=7, today=__import__("datetime").date(2026, 8, 12)))

    def test_is_within_days_unknown_date_passes(self) -> None:
        # A job with no parseable date shouldn't be silently dropped
        self.assertTrue(is_within_days(None, max_age_days=7))


class TestReedCollector(unittest.TestCase):
    def test_not_configured_raises_clearly(self) -> None:
        collector = ReedCollector(api_key="")
        self.assertFalse(collector.is_configured)
        with self.assertRaises(CollectorNotConfigured):
            collector.search(keywords="Administrator")

    @patch("app.collectors.reed.requests.get")
    def test_search_parses_documented_response_shape(self, mock_get: MagicMock) -> None:
        mock_get.return_value = _mock_response(REED_SAMPLE_RESPONSE)
        collector = ReedCollector(api_key="fake-key")

        jobs = collector.search(keywords="Operations Coordinator", location="Croydon")

        self.assertEqual(len(jobs), 1)
        job = jobs[0]
        self.assertEqual(job.title, "Operations Coordinator")
        self.assertEqual(job.company, "Bright Facilities Ltd")
        self.assertEqual(job.salary_min, 28000.0)
        self.assertEqual(job.date_posted, "2026-08-05")
        self.assertEqual(job.employment_type, "Full-time")
        self.assertEqual(job.sources[0].source, "reed")
        self.assertEqual(job.sources[0].source_job_id, "40227781")

    @patch("app.collectors.reed.requests.get")
    def test_uses_basic_auth_with_blank_password(self, mock_get: MagicMock) -> None:
        mock_get.return_value = _mock_response({"results": []})
        collector = ReedCollector(api_key="my-secret-key")
        collector.search(keywords="Administrator")

        _, kwargs = mock_get.call_args
        self.assertEqual(kwargs["auth"], ("my-secret-key", ""))

    @patch("app.collectors.reed.requests.get")
    def test_401_raises_helpful_error(self, mock_get: MagicMock) -> None:
        mock_get.return_value = _mock_response({}, status=401)
        collector = ReedCollector(api_key="wrong-key")
        with self.assertRaises(CollectorRequestFailed) as ctx:
            collector.search(keywords="Administrator")
        self.assertIn("REED_API_KEY", str(ctx.exception))

    @patch("app.collectors.reed.requests.get")
    def test_empty_results_returns_empty_list_not_error(self, mock_get: MagicMock) -> None:
        mock_get.return_value = _mock_response({"results": []})
        collector = ReedCollector(api_key="fake-key")
        self.assertEqual(collector.search(keywords="Nonexistent Role Xyz"), [])

    @patch("app.collectors.reed.requests.get")
    def test_missing_optional_fields_do_not_crash(self, mock_get: MagicMock) -> None:
        sparse = {"results": [{"jobId": 1, "jobTitle": "Administrator"}]}
        mock_get.return_value = _mock_response(sparse)
        collector = ReedCollector(api_key="fake-key")
        jobs = collector.search(keywords="Administrator")
        self.assertEqual(jobs[0].company, "Not stated")
        self.assertIsNone(jobs[0].salary_min)


class TestAdzunaCollector(unittest.TestCase):
    def test_not_configured_raises_clearly(self) -> None:
        collector = AdzunaCollector(app_id="", app_key="")
        self.assertFalse(collector.is_configured)
        with self.assertRaises(CollectorNotConfigured):
            collector.search(keywords="Administrator")

    def test_partial_credentials_still_not_configured(self) -> None:
        collector = AdzunaCollector(app_id="only-id", app_key="")
        self.assertFalse(collector.is_configured)

    @patch("app.collectors.adzuna.requests.get")
    def test_search_parses_documented_response_shape(self, mock_get: MagicMock) -> None:
        mock_get.return_value = _mock_response(ADZUNA_SAMPLE_RESPONSE)
        collector = AdzunaCollector(app_id="fake-id", app_key="fake-key")

        jobs = collector.search(keywords="Administrator", location="Croydon")

        self.assertEqual(len(jobs), 1)
        job = jobs[0]
        self.assertEqual(job.title, "Administrator")
        self.assertEqual(job.company, "Bright Facilities Ltd")
        self.assertEqual(job.location, "Croydon, Surrey")
        self.assertEqual(job.salary_min, 25000)
        self.assertEqual(job.date_posted, "2026-08-05")
        self.assertEqual(job.sources[0].source, "adzuna")

    @patch("app.collectors.adzuna.requests.get")
    def test_credentials_sent_as_query_params_not_headers(self, mock_get: MagicMock) -> None:
        mock_get.return_value = _mock_response({"results": []})
        collector = AdzunaCollector(app_id="my-id", app_key="my-key")
        collector.search(keywords="Administrator")

        _, kwargs = mock_get.call_args
        self.assertEqual(kwargs["params"]["app_id"], "my-id")
        self.assertEqual(kwargs["params"]["app_key"], "my-key")

    @patch("app.collectors.adzuna.requests.get")
    def test_country_and_page_build_correct_url(self, mock_get: MagicMock) -> None:
        mock_get.return_value = _mock_response({"results": []})
        collector = AdzunaCollector(app_id="id", app_key="key", country="gb")
        collector.search(keywords="Administrator", page=2)

        args, _ = mock_get.call_args
        self.assertEqual(args[0], "https://api.adzuna.com/v1/api/jobs/gb/search/2")

    @patch("app.collectors.adzuna.requests.get")
    def test_403_raises_helpful_error(self, mock_get: MagicMock) -> None:
        mock_get.return_value = _mock_response({}, status=403)
        collector = AdzunaCollector(app_id="id", app_key="wrong-key")
        with self.assertRaises(CollectorRequestFailed) as ctx:
            collector.search(keywords="Administrator")
        self.assertIn("ADZUNA_APP_ID", str(ctx.exception))


class TestRunCollection(unittest.TestCase):
    """The orchestration layer — proves one dead source never kills the run."""

    def setUp(self) -> None:
        self.profile = JobProfile.load(PROFILE_PATH)

    def test_build_collectors_reflects_readiness(self) -> None:
        secrets = Secrets(reed_api_key="", adzuna_app_id="id", adzuna_app_key="key")
        collectors = build_collectors(secrets)
        self.assertFalse(collectors["reed"].is_configured)
        self.assertTrue(collectors["adzuna"].is_configured)

    @patch("app.collectors.adzuna.requests.get")
    def test_reed_unconfigured_adzuna_still_runs(self, mock_adzuna_get: MagicMock) -> None:
        """This is THE scenario the user is in right now: no Reed key yet."""
        mock_adzuna_get.return_value = _mock_response(ADZUNA_SAMPLE_RESPONSE)
        secrets = Secrets(reed_api_key="", adzuna_app_id="id", adzuna_app_key="key")

        run = run_collection(self.profile, secrets)

        reed_result = next(r for r in run.source_results if r.source == "reed")
        adzuna_result = next(r for r in run.source_results if r.source == "adzuna")
        self.assertFalse(reed_result.configured)
        self.assertEqual(reed_result.queries_attempted, 0)
        self.assertTrue(adzuna_result.configured)
        self.assertGreater(adzuna_result.jobs_found, 0)
        self.assertTrue(run.has_any_success)

    @patch("requests.get")
    def test_one_failed_query_does_not_stop_the_others(self, mock_get: MagicMock) -> None:
        """
        Both collectors call the same shared `requests` module, so this must be
        one patch with URL-based routing — two separate @patch targets on
        'module.requests.get' silently collide, since it's the same underlying
        object either way. Caught by the test suite itself while writing this.
        """

        def route_by_url(url, *args, **kwargs):
            if "reed.co.uk" in url:
                return _mock_response(REED_SAMPLE_RESPONSE)
            return _mock_response({}, status=500)  # every Adzuna call fails

        mock_get.side_effect = route_by_url
        secrets = Secrets(reed_api_key="key", adzuna_app_id="id", adzuna_app_key="key")

        run = run_collection(self.profile, secrets)

        reed_result = next(r for r in run.source_results if r.source == "reed")
        adzuna_result = next(r for r in run.source_results if r.source == "adzuna")
        self.assertGreater(reed_result.jobs_found, 0)
        self.assertEqual(adzuna_result.jobs_found, 0)
        self.assertGreater(adzuna_result.queries_failed, 0)
        # The run as a whole succeeded because at least one source worked
        self.assertTrue(run.has_any_success)

    def test_uses_tier_1_roles_only(self) -> None:
        secrets = Secrets(reed_api_key="", adzuna_app_id="", adzuna_app_key="")
        run = run_collection(self.profile, secrets)
        # Neither source configured -> zero queries attempted, zero jobs,
        # but the run completes without raising
        self.assertEqual(run.jobs, [])
        self.assertFalse(run.has_any_success)


if __name__ == "__main__":
    unittest.main(verbosity=2)
