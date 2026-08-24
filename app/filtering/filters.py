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


# ---------------------------------------------------------------------
# Junk listings
#
# Some employers post placeholder or administrative entries that are not
# real vacancies. "Admin Purposes Only — DPD Group UK" appeared in a live
# run and scored joint-top, because nothing in the text marks it as fake.
# These are matched on the TITLE only: the phrases below are meaningless
# in a genuine job title but common in internal placeholders.
# ---------------------------------------------------------------------
JUNK_TITLE_PATTERNS = [
    "admin purposes only",
    "for admin purposes",
    "do not apply",
    "test job",
    "test vacancy",
    "dummy",
    "placeholder",
    "internal use only",
    "template",
    "example job",
]


def _is_junk_listing(job: Job) -> str | None:
    title = (job.title or "").lower()
    for pattern in JUNK_TITLE_PATTERNS:
        if pattern in title:
            return pattern
    return None


# ---------------------------------------------------------------------
# Seniority
#
# Adzuna matches loosely, so searching "Operations Coordinator" also
# returns "Operations Manager" and "Field Service Manager" — real jobs,
# but years above an entry-level career changer.
#
# The subtlety: several TARGET titles legitimately contain manager words
# ("Junior Project Manager", "Assistant Project Manager", "Management
# Trainee", "Operations Trainee"). So a naive "reject anything with
# manager in it" would delete the very Tier 2/3 roles being searched for.
# Hence: a senior marker only rejects when no entry-level qualifier is
# present anywhere in the title.
# ---------------------------------------------------------------------
SENIOR_TITLE_MARKERS = [
    "head of", "director", "chief", "principal", "vp ", "vice president",
    "manager", "management", "lead ", " lead", "supervisor", "controller",
    "partner", "consultant",
]

ENTRY_LEVEL_QUALIFIERS = [
    "junior", "assistant", "trainee", "graduate", "apprentice", "entry",
    "support", "administrator", "coordinator", "co-ordinator", "intern",
]

# ---------------------------------------------------------------------
# Domain relevance
#
# Found in a real run: "Junior CNC Machinist" and "Quantity Surveyor"
# both reached the top 10. Neither has anything to do with admin,
# operations or project work — they got through because
# ENTRY_LEVEL_QUALIFIERS exists to protect titles like "Junior Project
# Coordinator" from the seniority filter, and "junior" alone was enough,
# regardless of what field the job was actually in.
#
# This check is separate on purpose: seniority asks "is this too senior",
# relevance asks "is this even in the right field". A title needs to
# clear both. Built from the words that actually appear across every
# tier of the candidate's own target_roles list in job_profile.yaml —
# if you retarget the search to a different field, update this list to
# match.
# ---------------------------------------------------------------------
RELEVANT_DOMAIN_WORDS = [
    "administrat", "assistant", "operation", "coordinat", "co-ordinat",
    "business", "support", "project", "pmo", "office", "commercial",
    "service", "schedul", "logistics", "resource", "account", "sales",
    "procurement", "property", "maintenance", "facilities", "client",
    "analyst", "officer", "compliance", "management",
]


def _is_too_senior(job: Job) -> str | None:
    """
    Returns the offending marker if the title looks above entry level.
    A title carrying any entry-level qualifier is normally allowed through —
    "Assistant Project Manager" and "Management Trainee" must survive.

    "senior" is the one exception, checked before that protection applies.
    Unlike "manager" or "lead", which appear in genuine development-track
    titles, "Senior X" in UK job titles specifically signals prior
    experience in that exact specialisation is expected — "Senior Pensions
    Administrator" is not entry-level just because it also says
    "Administrator". A real job with this exact title reached a candidate's
    top 10 before this check existed.
    """
    title = (job.title or "").lower()

    if "senior" in title:
        return "senior"

    if any(q in title for q in ENTRY_LEVEL_QUALIFIERS):
        return None

    for marker in SENIOR_TITLE_MARKERS:
        if marker in title:
            return marker.strip()
    return None


def _is_irrelevant_domain(job: Job) -> bool:
    """
    True if the title carries none of the candidate's target-field words.
    Checked independently of _is_too_senior — an entry-level qualifier
    protects a title from being rejected as too SENIOR, but must not also
    exempt it from needing to be in the right field at all.
    """
    title = (job.title or "").lower()
    return not any(word in title for word in RELEVANT_DOMAIN_WORDS)


def filter_jobs(
    jobs: list[Job],
    profile: JobProfile,
    max_age_days: int | None = None,
) -> FilterResult:
    """
    Applies, in order: junk listings, seniority, domain relevance,
    salary floor, excluded keywords, posted-date window.
    A job is rejected on the first check it fails — the rejection reason
    records which one, for the run log and for debugging a search that
    is returning too few results.
    """
    result = FilterResult()
    window = max_age_days if max_age_days is not None else profile.search_windows[0]
    exclude_keywords = profile.exclude_keywords

    for job in jobs:
        junk = _is_junk_listing(job)
        if junk:
            result.rejected.append((job, f"junk listing: title contains '{junk}'"))
            continue

        too_senior = _is_too_senior(job)
        if too_senior:
            result.rejected.append((job, f"above entry level: title contains '{too_senior}'"))
            continue

        if _is_irrelevant_domain(job):
            result.rejected.append((job, "outside target field: title matches none of your target keywords"))
            continue

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
