"""
Phase 5 — filtering.

Runs on the raw batch a collector returns, before anything gets scored or
saved. A job that fails here never reaches the database at all, so this
is the cheapest possible place to reject something (Section 26's cost
control principle: filter deterministically before spending any effort
that costs more).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.collectors.base import is_within_days
from app.database.models import Job
from config.settings import JobProfile


@dataclass
class FilterResult:
    kept: list[Job] = field(default_factory=list)
    rejected: list[tuple[Job, str]] = field(default_factory=list)  # (job, reason)

    @property
    def rejected_count(self) -> int:
        return len(self.rejected)


def _job_text(job: Job) -> str:
    """Title + description, lower-cased, for keyword matching."""
    return f"{job.title or ''} {job.description or ''}".lower()


def _passes_salary_floor(job: Job, minimum_salary: int) -> bool:
    """
    A job with no salary stated is kept, not rejected — many good adverts
    simply don't list one, and dropping them all would lose real matches.
    A job IS rejected if its stated maximum is below the floor, since
    that's a certain miss rather than an unknown.
    """
    if job.salary_max is not None:
        return job.salary_max >= minimum_salary
    if job.salary_min is not None:
        return job.salary_min >= minimum_salary
    return True


def _contains_excluded_keyword(job: Job, exclude_keywords: list[str]) -> str | None:
    """Returns the matched keyword, or None if nothing excluded was found."""
    text = _job_text(job)
    for keyword in exclude_keywords:
        if keyword in text:
            return keyword
    return None


def filter_jobs(
    jobs: list[Job],
    profile: JobProfile,
    max_age_days: int | None = None,
) -> FilterResult:
    """
    Applies, in order: salary floor, excluded keywords, posted-date window.
    A job is rejected on the first check it fails — the rejection reason
    records which one, for the run log and for debugging a search that
    is returning too few results.
    """
    result = FilterResult()
    window = max_age_days if max_age_days is not None else profile.search_windows[0]
    exclude_keywords = profile.exclude_keywords

    for job in jobs:
        if not _passes_salary_floor(job, profile.minimum_salary):
            result.rejected.append((job, f"below minimum salary of £{profile.minimum_salary:,}"))
            continue

        matched_exclusion = _contains_excluded_keyword(job, exclude_keywords)
        if matched_exclusion:
            result.rejected.append((job, f"excluded keyword: '{matched_exclusion}'"))
            continue

        if not is_within_days(job.date_posted, max_age_days=window):
            result.rejected.append((job, f"posted more than {window} days ago"))
            continue

        result.kept.append(job)

    return result
