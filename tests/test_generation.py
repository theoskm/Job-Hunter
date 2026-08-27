"""
Phase 13 tests.

build_prompt and parse_response are pure functions and are fully tested
here with no network and no API key. generate_materials() itself — the
actual call to Anthropic — is NOT exercised; there is no way to prove it
works without spending real money against a real key, which this
environment cannot do. `--run` against your own ANTHROPIC_API_KEY is the
only real proof of that part.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database.db import Database
from app.database.models import Job
from app.generation.tailor import (
    CvContextMissing,
    GenerationNotConfigured,
    GeneratedMaterials,
    build_prompt,
    load_cv_context,
    materials_to_row,
    parse_response,
)
from app.pipeline import generate_for_jobs, PipelineReport
from config.settings import JobProfile, Secrets, Settings

PROFILE_PATH = Path(__file__).resolve().parent.parent / "config" / "job_profile.yaml"

SAMPLE_RESPONSE = """Some preamble the model might add.

## CV SUGGESTIONS
- Coordinated daily operations and resources under strict service deadlines
- Trained and supervised junior colleagues
- Managed stock levels and supplier communication

## ATS KEYWORDS
scheduling, coordination, stakeholder communication, deadline management, MS Office

## COVER LETTER
Dear Hiring Manager,

I am writing to apply for the Operations Coordinator role. In my current \
position I coordinate daily operations and resources under strict \
deadlines, which maps directly onto what this role asks for.

I don't have direct office experience, but I've trained junior colleagues \
and managed supplier communication daily.

I'd welcome the opportunity to discuss the role.

Kind regards"""


class TestPromptConstruction(unittest.TestCase):
    def test_prompt_includes_the_full_cv_context_verbatim(self) -> None:
        context = "Worked at Example Ltd as a Coordinator, 2020-2023."
        prompt = build_prompt(context, "Operations Coordinator", "Acme", "Coordinate things.")
        self.assertIn(context, prompt)

    def test_prompt_includes_job_details(self) -> None:
        prompt = build_prompt("Some background.", "Operations Coordinator", "Acme Ltd", "Do stuff.")
        self.assertIn("Operations Coordinator", prompt)
        self.assertIn("Acme Ltd", prompt)
        self.assertIn("Do stuff.", prompt)

    def test_prompt_explicitly_forbids_invention(self) -> None:
        """The single most important line in this whole feature."""
        prompt = build_prompt("Background.", "Role", "Co", "JD.")
        self.assertIn("Never invent", prompt)

    def test_long_job_description_gets_truncated_not_dropped(self) -> None:
        huge_jd = "A" * 10000
        prompt = build_prompt("Background.", "Role", "Co", huge_jd)
        self.assertLess(len(prompt), 10000 + 2000)  # truncated, not included whole
        self.assertIn("A" * 100, prompt)  # but real content is still present


class TestResponseParsing(unittest.TestCase):
    def test_parses_all_three_sections(self) -> None:
        materials = parse_response(SAMPLE_RESPONSE)
        self.assertEqual(len(materials.cv_suggestions), 3)
        self.assertIn("Coordinated daily operations", materials.cv_suggestions[0])
        self.assertIn("scheduling", materials.ats_keywords)
        self.assertIn("Dear Hiring Manager", materials.cover_letter)

    def test_ignores_preamble_before_first_header(self) -> None:
        materials = parse_response(SAMPLE_RESPONSE)
        self.assertNotIn("Some preamble", materials.cover_letter)
        self.assertNotIn("Some preamble", " ".join(materials.cv_suggestions))

    def test_raises_clearly_when_headers_are_entirely_missing(self) -> None:
        with self.assertRaises(ValueError):
            parse_response("The model just returned a paragraph with no structure at all.")

    def test_tolerates_bullet_character_variants(self) -> None:
        text = SAMPLE_RESPONSE.replace("- Coordinated", "\u2022 Coordinated")
        materials = parse_response(text)
        self.assertEqual(len(materials.cv_suggestions), 3)

    def test_keywords_split_on_commas_and_trimmed(self) -> None:
        materials = parse_response(SAMPLE_RESPONSE)
        self.assertNotIn(" MS Office", materials.ats_keywords)  # no leading space
        self.assertIn("MS Office", materials.ats_keywords)


class TestCvContextLoading(unittest.TestCase):
    def test_missing_file_raises_clear_error(self) -> None:
        with self.assertRaises(CvContextMissing):
            load_cv_context("/nonexistent/path/cv_context.md")

    def test_near_empty_file_raises_clear_error(self) -> None:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write("short")
            path = f.name
        with self.assertRaises(CvContextMissing):
            load_cv_context(path)

    def test_real_project_cv_context_loads_successfully(self) -> None:
        """The actual shipped file must itself pass this check."""
        real_path = Path(__file__).resolve().parent.parent / "config" / "cv_context.md"
        context = load_cv_context(real_path)
        self.assertIn("Ivy Cafe", context)


class TestMaterialsToRow(unittest.TestCase):
    def test_shapes_for_database_storage(self) -> None:
        materials = GeneratedMaterials(
            cv_suggestions=["a", "b"], ats_keywords=["x", "y"],
            cover_letter="Dear...", raw_response="raw",
        )
        row = materials_to_row(materials)
        self.assertEqual(json.loads(row["cv_suggestions"]), ["a", "b"])
        self.assertEqual(json.loads(row["ats_keywords"]), ["x", "y"])
        self.assertEqual(row["cover_letter_draft"], "Dear...")


class TestPipelineIntegration(unittest.TestCase):
    """Threshold gating, caching, and graceful failure — no real API calls."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "test.db"
        self.db = Database(self.db_path)
        self.db.init_schema()
        self.profile = JobProfile.load(PROFILE_PATH)

    def tearDown(self) -> None:
        self.db.close()
        self.tmp.cleanup()

    def _settings(self, api_key: str = "") -> Settings:
        return Settings(
            profile=self.profile,
            secrets=Secrets(database_path=str(self.db_path), anthropic_api_key=api_key),
        )

    def _job(self, score: int, description: str = "Coordinate things.") -> Job:
        return Job(title="Operations Coordinator", company="Acme", location="Croydon",
                   score=score, description=description)

    def test_below_threshold_job_is_skipped_entirely(self) -> None:
        report = PipelineReport()
        job = self._job(score=50)  # below the 70 default
        generate_for_jobs([job], self._settings(api_key="fake-key"), self.db, report)
        self.assertEqual(report.materials_generated, 0)

    def test_no_api_key_skips_with_a_clear_reason_not_a_crash(self) -> None:
        report = PipelineReport()
        job = self._job(score=90)
        generate_for_jobs([job], self._settings(api_key=""), self.db, report)
        self.assertEqual(report.materials_generated, 0)
        self.assertIn("ANTHROPIC_API_KEY", report.generation_skipped_reason)

    @patch("app.generation.tailor.generate_materials")
    def test_qualifying_job_gets_generated_and_saved(self, mock_generate: MagicMock) -> None:
        mock_generate.return_value = GeneratedMaterials(
            cv_suggestions=["Did a thing"], ats_keywords=["thing"],
            cover_letter="Dear Hiring Manager, ...", raw_response="raw",
        )
        job = self._job(score=90)
        job_id, _ = self.db.upsert_job(job)
        report = PipelineReport()

        generate_for_jobs([job], self._settings(api_key="fake-key"), self.db, report)

        self.assertEqual(report.materials_generated, 1)
        row = self.db.get_job(job_id)
        self.assertIn("Dear Hiring Manager", row["cover_letter_draft"])
        self.assertEqual(json.loads(row["ats_keywords"]), ["thing"])

    @patch("app.generation.tailor.generate_materials")
    def test_unchanged_description_is_not_regenerated(self, mock_generate: MagicMock) -> None:
        """The whole point of analysis_hash — this is what saves the money."""
        mock_generate.return_value = GeneratedMaterials(
            cv_suggestions=["x"], ats_keywords=["y"], cover_letter="z", raw_response="raw",
        )
        job = self._job(score=90)
        self.db.upsert_job(job)
        report = PipelineReport()
        settings = self._settings(api_key="fake-key")

        generate_for_jobs([job], settings, self.db, report)
        generate_for_jobs([job], settings, self.db, report)  # identical job, second run

        self.assertEqual(mock_generate.call_count, 1, "second run must not re-spend money")
        self.assertEqual(report.materials_generated, 1)

    @patch("app.generation.tailor.generate_materials")
    def test_changed_description_does_regenerate(self, mock_generate: MagicMock) -> None:
        mock_generate.return_value = GeneratedMaterials(
            cv_suggestions=["x"], ats_keywords=["y"], cover_letter="z", raw_response="raw",
        )
        job_v1 = self._job(score=90, description="Original advert text.")
        self.db.upsert_job(job_v1)
        report = PipelineReport()
        settings = self._settings(api_key="fake-key")
        generate_for_jobs([job_v1], settings, self.db, report)

        job_v2 = self._job(score=90, description="The advert was edited by the employer.")
        generate_for_jobs([job_v2], settings, self.db, report)

        self.assertEqual(mock_generate.call_count, 2, "a genuinely changed advert should regenerate")

    @patch("app.generation.tailor.generate_materials")
    def test_one_failed_generation_does_not_stop_the_others(self, mock_generate: MagicMock) -> None:
        good = GeneratedMaterials(cv_suggestions=["x"], ats_keywords=["y"],
                                   cover_letter="z", raw_response="raw")
        mock_generate.side_effect = [RuntimeError("API timeout"), good]

        job_a = self._job(score=90, description="First job.")
        job_a.title = "First Role"
        job_b = self._job(score=90, description="Second job.")
        job_b.title = "Second Role"
        self.db.upsert_job(job_a)
        self.db.upsert_job(job_b)
        report = PipelineReport()

        generate_for_jobs([job_a, job_b], self._settings(api_key="fake-key"), self.db, report)

        self.assertEqual(report.materials_generated, 1)
        self.assertEqual(len(report.generation_errors), 1)
        self.assertIn("API timeout", report.generation_errors[0])


class TestSchemaMigration(unittest.TestCase):
    """
    Proves the new columns can be added to a database that already has real
    jobs in it — this is the user's actual situation, not a fresh install.
    """

    def test_existing_database_gains_new_columns_without_losing_data(self) -> None:
        import re
        import sqlite3

        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "pre_existing.db"
            schema_path = Path(__file__).resolve().parent.parent / "app" / "database" / "schema.sql"
            full_schema = schema_path.read_text()
            old_schema = re.sub(
                r"\s*-- Generated application materials.*?ats_keywords\s+TEXT,\s*-- JSON array\n",
                "\n", full_schema, flags=re.DOTALL,
            )
            self.assertNotIn("cv_suggestions", old_schema, "test setup itself is invalid")

            conn = sqlite3.connect(db_path)
            conn.executescript(old_schema)
            conn.execute(
                "INSERT INTO jobs (job_id, canonical_key, title, company, score, "
                "status, status_source, first_seen, last_updated) VALUES "
                "('abc123', 'key1', 'Administrator', 'Rape Crisis South London', "
                "67, 'APPLIED', 'user', '2026-08-24', '2026-08-24')"
            )
            conn.commit()
            conn.close()

            with Database(db_path) as db:
                db.init_schema()
                row = db.get_job("abc123")

            self.assertEqual(row["title"], "Administrator")
            self.assertEqual(row["status"], "APPLIED", "a real application status must survive")
            self.assertIn("cv_suggestions", row.keys())
            self.assertIsNone(row["cv_suggestions"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
