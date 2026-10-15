"""
SQLite persistence.

The important rule enforced here: an automated run may enrich a job's
details and scores, but it must never overwrite a status the user set.
That is checked in `upsert_job` via `status_source` / USER_OWNED_STATUSES.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .models import Job, JobSource, USER_OWNED_STATUSES, STATUSES

SCHEMA_PATH = Path(__file__).parent / "schema.sql"

# Columns an automated run is allowed to refresh on an existing job.
_REFRESHABLE = [
    "title", "company", "location", "salary_min", "salary_max", "salary_raw",
    "salary_currency", "employment_type", "working_arrangement", "date_posted",
    "closing_date", "description", "requirements", "company_website",
    "recruiter", "direct_application_url", "score", "priority", "career_fit",
    "transferable_skills", "accessibility", "skills_match", "salary_score",
    "location_score", "career_development", "matching_skills", "skill_gaps",
    "reasoning", "description_hash", "analysis_hash", "is_expired",
]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Database:
    def __init__(self, path: str | Path = "data/job_hunter.db"):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")

    # ---------------------------------------------------------------
    def init_schema(self) -> None:
        with SCHEMA_PATH.open("r", encoding="utf-8") as fh:
            self.conn.executescript(fh.read())
        self._migrate_add_missing_columns()
        self.conn.commit()

    def _migrate_add_missing_columns(self) -> None:
        """
        Adds columns introduced after a database already existed, so
        upgrading the code never requires --reset-db. Safe to call on
        every startup — checks what's there before adding anything.
        """
        existing = {row["name"] for row in self.conn.execute("PRAGMA table_info(jobs)")}
        new_columns = {
            "cv_suggestions": "TEXT",
            "cover_letter_draft": "TEXT",
            "ats_keywords": "TEXT",
        }
        for column, sql_type in new_columns.items():
            if column not in existing:
                self.conn.execute(f"ALTER TABLE jobs ADD COLUMN {column} {sql_type}")

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "Database":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # ---------------------------------------------------------------
    def find_by_canonical_key(self, canonical_key: str) -> sqlite3.Row | None:
        cur = self.conn.execute(
            "SELECT * FROM jobs WHERE canonical_key = ?", (canonical_key,)
        )
        return cur.fetchone()

    def get_job(self, job_id: str) -> sqlite3.Row | None:
        cur = self.conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,))
        return cur.fetchone()

    def upsert_job(self, job: Job) -> tuple[str, bool]:
        """
        Insert a new job, or refresh an existing one.

        Returns (job_id, was_created). Existing rows keep their job_id,
        their first_seen, and — critically — any user-owned status.
        """
        existing = self.find_by_canonical_key(job.canonical_key)
        row = job.to_row()

        if existing is None:
            row["last_updated"] = _now_iso()
            row["last_checked"] = _now_iso()
            columns = ", ".join(row.keys())
            placeholders = ", ".join("?" for _ in row)
            self.conn.execute(
                f"INSERT INTO jobs ({columns}) VALUES ({placeholders})",
                list(row.values()),
            )
            self._record_status(job.job_id, None, job.status, "system", "created")
            self.conn.commit()
            return job.job_id, False

        job_id = existing["job_id"]
        updates = {
            col: row.get(col)
            for col in _REFRESHABLE
            if row.get(col) is not None
        }

        # Never regress a status the user owns.
        user_owns = (
            existing["status_source"] == "user"
            or existing["status"] in USER_OWNED_STATUSES
        )
        if not user_owns and job.status and job.status != existing["status"]:
            updates["status"] = job.status
            self._record_status(
                job_id, existing["status"], job.status, "system", "automated refresh"
            )

        updates["last_updated"] = _now_iso()
        updates["last_checked"] = _now_iso()

        assignments = ", ".join(f"{col} = ?" for col in updates)
        self.conn.execute(
            f"UPDATE jobs SET {assignments} WHERE job_id = ?",
            [*updates.values(), job_id],
        )
        self.conn.commit()
        return job_id, True

    # ---------------------------------------------------------------
    def add_source(self, job_id: str, source: JobSource) -> bool:
        """Record where a job was found. Returns False if already known."""
        try:
            self.conn.execute(
                """INSERT INTO job_sources
                   (job_id, source, source_job_id, source_url, first_seen)
                   VALUES (?, ?, ?, ?, ?)""",
                (job_id, source.source, source.source_job_id,
                 source.source_url, source.first_seen),
            )
            self.conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    def get_sources(self, job_id: str) -> list[sqlite3.Row]:
        cur = self.conn.execute(
            "SELECT * FROM job_sources WHERE job_id = ? ORDER BY first_seen", (job_id,)
        )
        return cur.fetchall()

    # ---------------------------------------------------------------
    def set_status(
        self,
        job_id: str,
        new_status: str,
        changed_by: str = "user",
        note: str | None = None,
    ) -> None:
        if new_status not in STATUSES:
            raise ValueError(
                f"'{new_status}' is not a valid status. Valid: {', '.join(STATUSES)}"
            )
        current = self.get_job(job_id)
        if current is None:
            raise KeyError(f"No job with id {job_id}")

        self.conn.execute(
            """UPDATE jobs
               SET status = ?, status_source = ?, last_updated = ?
               WHERE job_id = ?""",
            (new_status, changed_by, _now_iso(), job_id),
        )
        self._record_status(job_id, current["status"], new_status, changed_by, note)
        self.conn.commit()

    def _record_status(
        self,
        job_id: str,
        old: str | None,
        new: str,
        changed_by: str,
        note: str | None,
    ) -> None:
        self.conn.execute(
            """INSERT INTO status_history
               (job_id, old_status, new_status, changed_by, changed_at, note)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (job_id, old, new, changed_by, _now_iso(), note),
        )

    def get_status_history(self, job_id: str) -> list[sqlite3.Row]:
        cur = self.conn.execute(
            "SELECT * FROM status_history WHERE job_id = ? ORDER BY id", (job_id,)
        )
        return cur.fetchall()

    # ---------------------------------------------------------------
    def jobs_above_score(self, minimum: int) -> list[sqlite3.Row]:
        cur = self.conn.execute(
            "SELECT * FROM jobs WHERE score >= ? AND is_expired = 0 "
            "ORDER BY score DESC, date_posted DESC",
            (minimum,),
        )
        return cur.fetchall()

    def jobs_needing_alert(self, minimum: int) -> list[sqlite3.Row]:
        """High scorers not yet alerted — prevents repeat emails."""
        cur = self.conn.execute(
            "SELECT * FROM jobs WHERE score >= ? AND alert_sent_at IS NULL "
            "AND is_expired = 0 ORDER BY score DESC",
            (minimum,),
        )
        return cur.fetchall()

    def mark_alert_sent(self, job_ids: Iterable[str]) -> None:
        stamp = _now_iso()
        self.conn.executemany(
            "UPDATE jobs SET alert_sent_at = ? WHERE job_id = ?",
            [(stamp, jid) for jid in job_ids],
        )
        self.conn.commit()

    def remove_jobs_from_companies(self, company_names: Iterable[str]) -> int:
        """
        Deletes tracked jobs from blocked companies. Jobs with a status you
        set yourself (APPLIED, INTERVIEW, etc.) are always kept.
        Returns how many were removed.
        """
        from app.database.models import USER_OWNED_STATUSES

        names = [n.lower() for n in company_names if n]
        if not names:
            return 0
        rows = self.conn.execute("SELECT job_id, company, status FROM jobs").fetchall()
        doomed = [
            r["job_id"] for r in rows
            if (r["status"] or "NEW") not in USER_OWNED_STATUSES
            and any(n in " ".join((r["company"] or "").lower().split()) for n in names)
        ]
        if not doomed:
            return 0
        marks = ",".join("?" * len(doomed))
        for table in ("job_sources", "status_history"):
            try:
                self.conn.execute(f"DELETE FROM {table} WHERE job_id IN ({marks})", doomed)
            except sqlite3.OperationalError:
                pass  # table not present in this schema
        self.conn.execute(f"DELETE FROM jobs WHERE job_id IN ({marks})", doomed)
        self.conn.commit()
        return len(doomed)

    def needs_ai_analysis(self, canonical_key: str, description_hash: str) -> bool:
        """False when this exact description was already analysed — saves API spend."""
        row = self.find_by_canonical_key(canonical_key)
        if row is None:
            return True
        return row["analysis_hash"] != description_hash

    def counts_by_status(self) -> dict[str, int]:
        cur = self.conn.execute(
            "SELECT status, COUNT(*) AS n FROM jobs GROUP BY status"
        )
        return {r["status"]: r["n"] for r in cur.fetchall()}

    def start_run(self, source: str | None = None) -> int:
        cur = self.conn.execute(
            "INSERT INTO run_log (run_started, source, status) VALUES (?, ?, 'running')",
            (_now_iso(), source),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def finish_run(
        self,
        run_id: int,
        jobs_found: int = 0,
        jobs_new: int = 0,
        jobs_duplicate: int = 0,
        errors: str | None = None,
        status: str = "success",
    ) -> None:
        self.conn.execute(
            """UPDATE run_log
               SET run_finished = ?, jobs_found = ?, jobs_new = ?,
                   jobs_duplicate = ?, errors = ?, status = ?
               WHERE id = ?""",
            (_now_iso(), jobs_found, jobs_new, jobs_duplicate, errors, status, run_id),
        )
        self.conn.commit()
