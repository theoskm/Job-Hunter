"""Jooble collector: request shape, parsing, salary text."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from app.collectors.jooble import JoobleCollector, parse_salary

SAMPLE = {"totalCount": 1, "jobs": [{
    "title": "<b>Office</b> Administrator", "location": "Croydon, Greater London",
    "snippet": "Join our friendly team &amp; support the office. Full training provided.",
    "salary": "£25,000 - £28,000 per annum", "source": "example.co.uk", "type": "Full-time",
    "link": "https://jooble.org/desc/123", "company": "Acme Ltd",
    "updated": "2026-10-14T00:00:00.0000000", "id": 123}]}


class TestJooble(unittest.TestCase):
    @patch("app.collectors.jooble.requests.post")
    def test_request_and_parse(self, mock_post: MagicMock) -> None:
        response = MagicMock()
        response.raise_for_status.return_value = None
        response.json.return_value = SAMPLE
        mock_post.return_value = response

        jobs = JoobleCollector("KEY").search("Administrator", "Croydon",
                                             minimum_salary=24000, distance_miles=20)

        url = mock_post.call_args.args[0]
        body = mock_post.call_args.kwargs["json"]
        self.assertEqual(url, "https://jooble.org/api/KEY")
        self.assertEqual(body["keywords"], "Administrator")
        self.assertEqual(body["radius"], "32")
        self.assertEqual(body["salary"], "24000")
        job = jobs[0]
        self.assertEqual(job.title, "Office Administrator")
        self.assertEqual((job.salary_min, job.salary_max), (25000, 28000))
        self.assertEqual(job.date_posted, "2026-10-14")
        self.assertIn("support the office", job.description)
        self.assertEqual(job.sources[0].source, "jooble")

    def test_salary_text(self) -> None:
        self.assertEqual(parse_salary("£25k - £28k"), (25000, 28000))
        self.assertEqual(parse_salary("£13.50 per hour"), (13.5 * 1950, 13.5 * 1950))
        self.assertEqual(parse_salary("£2,200 per month"), (26400, 26400))
        self.assertEqual(parse_salary("Competitive"), (None, None))
        self.assertEqual(parse_salary(None), (None, None))

    def test_unconfigured_is_skipped(self) -> None:
        self.assertFalse(JoobleCollector("").is_configured)


if __name__ == "__main__":
    unittest.main(verbosity=2)
