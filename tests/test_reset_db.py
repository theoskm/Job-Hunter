"""
Phase 5-6 addendum: --reset-db.

Built to fix a real gap found from live data: filter fixes only apply to
newly-collected jobs, so bad rows from before a fix existed sit in the
database forever unless something removes them. This must never be able
to delete a job the user has actually acted on.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database.db import Database
from app.database.models import Job
from config.settings import JobProfile, Secrets, Settings

PROFILE_PATH = Path(__file__).resolve().parent.parent / "config" / "job_profile.yaml"


def _job(title="Administrator", **kw) -> Job:
    return Job(title=title, company="Example Ltd", location="Croydon", score=50, **kw)


class TestResetDb(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "test.db"
        self.settings = Settings(
            profile=JobProfile.load(PROFILE_PATH),
            secrets=Secrets(database_path=str(self.db_path)),
        )
        with Database(self.db_path) as db:
            db.init_schema()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_refuses_to_reset_when_a_status_is_user_set(self) -> None:
        from main import cmd_reset_db

        with Database(self.db_path) as db:
            job_id, _ = db.upsert_job(_job())
            db.set_status(job_id, "APPLIED", changed_by="user")

        result = cmd_reset_db(self.settings)

        self.assertEqual(result, 1)  # refused
        with Database(self.db_path) as db:
            remaining = db.conn.execute("SELECT COUNT(*) AS n FROM jobs").fetchone()["n"]
        self.assertEqual(remaining, 1, "a real application must survive a refused reset")

    @patch("builtins.input", return_value="yes")
    def test_clears_database_when_nothing_is_user_owned(self, _mock_input) -> None:
        from main import cmd_reset_db

        with Database(self.db_path) as db:
            db.upsert_job(_job(title="Quantity Surveyor"))  # a stale bad row
            db.upsert_job(_job(title="Administrator"))

        result = cmd_reset_db(self.settings)

        self.assertEqual(result, 0)
        with Database(self.db_path) as db:
            remaining = db.conn.execute("SELECT COUNT(*) AS n FROM jobs").fetchone()["n"]
        self.assertEqual(remaining, 0)

    @patch("builtins.input", return_value="no")
    def test_declining_the_prompt_deletes_nothing(self, _mock_input) -> None:
        from main import cmd_reset_db

        with Database(self.db_path) as db:
            db.upsert_job(_job())

        cmd_reset_db(self.settings)

        with Database(self.db_path) as db:
            remaining = db.conn.execute("SELECT COUNT(*) AS n FROM jobs").fetchone()["n"]
        self.assertEqual(remaining, 1)

    def test_missing_database_is_handled_gracefully(self) -> None:
        from main import cmd_reset_db

        self.db_path.unlink()  # simulate no database existing yet
        result = cmd_reset_db(self.settings)
        self.assertEqual(result, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
