"""Core data structures. Stdlib only — no pydantic needed at this layer."""

from __future__ import annotations

import hashlib
import re
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any

# CRM pipeline. Order matters for the dashboard funnel.
STATUSES = [
    "NEW",
    "SHORTLISTED",
    "CV READY",
    "READY TO APPLY",
    "APPLIED",
    "FOLLOW-UP",
    "INTERVIEW",
    "SECOND INTERVIEW",
    "OFFER",
    "ACCEPTED",
    "REJECTED",
    "WITHDRAWN",
]

# Once a job reaches any of these, it is part of the user's real pipeline
# and the automation must never move it backwards.
USER_OWNED_STATUSES = {
    "APPLIED", "FOLLOW-UP", "INTERVIEW", "SECOND INTERVIEW",
    "OFFER", "ACCEPTED", "REJECTED", "WITHDRAWN",
}

_WORD_RE = re.compile(r"[^a-z0-9]+")

# Suffixes that differ between boards for the same employer
_COMPANY_NOISE = {
    "ltd", "limited", "plc", "llp", "uk", "group", "holdings",
    "recruitment", "the", "and",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def normalise(text: str | None) -> str:
    """Lowercase, strip punctuation, collapse whitespace."""
    if not text:
        return ""
    return _WORD_RE.sub(" ", text.lower()).strip()


def normalise_company(name: str | None) -> str:
    """
    Normalise an employer name so 'ACME Ltd.' and 'Acme Limited'
    collapse to the same token.
    """
    tokens = [t for t in normalise(name).split() if t and t not in _COMPANY_NOISE]
    return " ".join(tokens)


def normalise_location(location: str | None) -> str:
    """
    Reduce a location to its leading place name. Boards render the same
    place as 'Croydon', 'Croydon, Surrey' and 'Croydon, Greater London'.
    """
    base = normalise(location)
    if not base:
        return ""
    return base.split()[0] if base else ""


def build_canonical_key(title: str, company: str, location: str | None) -> str:
    """
    The dedup key. Deliberately excludes salary and description, which
    are frequently reworded between boards for an identical vacancy.
    """
    return "|".join(
        [normalise(title), normalise_company(company), normalise_location(location)]
    )


def hash_text(text: str | None) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:32]


@dataclass
class JobSource:
    source: str
    source_url: str
    source_job_id: str | None = None
    first_seen: str = field(default_factory=_now_iso)


@dataclass
class Job:
    title: str
    company: str
    location: str | None = None

    job_id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])
    salary_min: float | None = None
    salary_max: float | None = None
    salary_currency: str = "GBP"
    salary_raw: str | None = None
    employment_type: str | None = None
    working_arrangement: str | None = None
    date_posted: str | None = None
    closing_date: str | None = None
    description: str | None = None
    requirements: str | None = None
    company_website: str | None = None
    recruiter: str | None = None
    direct_application_url: str | None = None

    score: int | None = None
    priority: str | None = None
    career_fit: int | None = None
    transferable_skills: int | None = None
    accessibility: int | None = None
    skills_match: int | None = None
    salary_score: int | None = None
    location_score: int | None = None
    career_development: int | None = None
    matching_skills: str | None = None
    skill_gaps: str | None = None
    reasoning: str | None = None

    status: str = "NEW"
    status_source: str = "system"
    cv_version: str | None = None
    cover_letter_required: int = 0
    application_date: str | None = None
    follow_up_date: str | None = None
    interview_date: str | None = None
    rejection_reason: str | None = None
    notes: str | None = None

    first_seen: str = field(default_factory=_now_iso)
    last_updated: str = field(default_factory=_now_iso)
    last_checked: str | None = None
    is_expired: int = 0
    alert_sent_at: str | None = None

    sources: list[JobSource] = field(default_factory=list)

    @property
    def canonical_key(self) -> str:
        return build_canonical_key(self.title, self.company, self.location)

    @property
    def description_hash(self) -> str:
        return hash_text(self.description)

    def to_row(self) -> dict[str, Any]:
        """Flatten for SQLite — drops the nested sources list."""
        data = asdict(self)
        data.pop("sources", None)
        data["canonical_key"] = self.canonical_key
        data["description_hash"] = self.description_hash
        data.setdefault("analysis_hash", None)
        return data
