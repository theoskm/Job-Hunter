"""Each listed job gets a short plain-English reason for its score."""

from __future__ import annotations

import sqlite3
import unittest

from app.notifications.templates import build_results_markdown, explain_score

WEIGHTS = {"career_transition_fit": 10, "transferable_skills": 20, "entry_level_accessibility": 30,
           "skills_match": 15, "salary": 15, "location": 5, "career_development": 5}


def _row(**overrides) -> sqlite3.Row:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    data = dict(title="Administrator", company="Acme", location="Croydon", score=66,
                priority="STRETCH", salary_min=26000, salary_max=28000, status="NEW",
                direct_application_url="https://example.com", matching_skills='["excel"]',
                skill_gaps=None, accessibility=27, transferable_skills=10, skills_match=3,
                salary_score=13, career_fit=5, location_score=5, career_development=2,
                description="x" * 1000)
    data.update(overrides)
    cols = ", ".join(f":{k} AS {k}" for k in data)
    return conn.execute(f"SELECT {cols}", data).fetchone()


class TestExplainScore(unittest.TestCase):
    def test_names_strengths_and_weaknesses_with_points(self) -> None:
        text = explain_score(_row(), WEIGHTS)
        self.assertIn("entry-level friendly (27/30)", text)
        self.assertIn("pay in your target range (13/15)", text)
        self.assertIn("few of your listed skills mentioned (3/15)", text)
        self.assertNotIn("transferable", text)  # 10/20 is middling, not mentioned

    def test_flags_short_adverts(self) -> None:
        self.assertIn("rough score", explain_score(_row(description="short"), WEIGHTS))

    def test_results_file_has_why_line(self) -> None:
        self.assertIn("- **Why:**", build_results_markdown([_row()], weights=WEIGHTS))


if __name__ == "__main__":
    unittest.main(verbosity=2)
