"""
Phase 5 — filtering.

Runs on the raw batch a collector returns, before anything gets scored or
saved. A job that fails here never reaches the database at all, so this
is the cheapest possible place to reject something (Section 26's cost
control principle: filter deterministically before spending any effort
that costs more).
"""

from __future__ import annotations

import re
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


# ---------------------------------------------------------------------
# Experience required
#
# Added 15 Oct 2026 after the first real run: almost everything near the
# top asked for prior experience. Matched against the advert text, so it
# only works as well as the text available. Reed adverts are fetched in
# full before this runs (see app/collectors/reed.py fetch_full_description);
# Adzuna only ever gives ~500 characters.
# ---------------------------------------------------------------------
_NUM = r"(?:\d+|one|two|three|four|five|six)"
EXPERIENCE_REQUIRED_PATTERNS = [
    # "2 years experience", "3+ years' experience", "1-2 years of relevant experience"
    rf"\b{_NUM}\s*\+?\s*(?:-|to)?\s*{_NUM}?\s*\+?\s*years?[’']?\s+(?:of\s+)?(?:\w+\s+){{0,3}}experience",
    # "minimum of 2 years", "at least one year"
    rf"\b(?:minimum|at least|min\.?)\s+(?:of\s+)?{_NUM}\s+years?",
    r"\bproven (?:experience|track record)",
    r"\bprevious (?:experience (?:as|in|of|within|working)|\w+ experience)",
    r"\bdemonstrable experience",
    r"\bextensive experience",
    r"\bexperience (?:is|will be) (?:essential|required|a must)",
    r"\bmust have (?:previous |prior |relevant )?experience",
    r"\bexperienced (?:administrator|coordinator|co-ordinator|assistant|advisor|adviser)",
]
_EXPERIENCE_RE = re.compile("|".join(EXPERIENCE_REQUIRED_PATTERNS), re.IGNORECASE)


# If the sentence containing the phrase also says one of these, it's a
# nice-to-have, not a requirement ("previous admin experience is
# desirable", "no previous experience needed").
_SOFTENERS = re.compile(
    r"\b(?:desirable|preferred|preferable|advantageous|an advantage|beneficial|"
    r"a bonus|a plus|helpful|ideally|not essential|not required|not necessary|"
    r"no (?:previous |prior )?experience|don[’']?t need|do not need|"
    r"isn[’']?t essential|full training)\b",
    re.IGNORECASE,
)


def requires_experience(text: str) -> str | None:
    """Returns the phrase that shows experience is required, or None."""
    text = text or ""
    for match in _EXPERIENCE_RE.finditer(text):
        start = max(text.rfind(".", 0, match.start()), text.rfind("\n", 0, match.start())) + 1
        end_candidates = [i for i in (text.find(".", match.end()), text.find("\n", match.end())) if i != -1]
        end = min(end_candidates) if end_candidates else len(text)
        if _SOFTENERS.search(text[start:end]):
            continue
        return match.group(0).strip()
    return None


def _above_salary_ceiling(salary_min, maximum_salary: int | None) -> bool:
    """Only the LOWEST stated pay counts, so a £28k-£34k range still passes a £32k ceiling."""
    return bool(maximum_salary and salary_min and salary_min > maximum_salary)


def hard_rule_violation(
    title: str | None, company: str | None, description: str | None,
    salary_min, profile: JobProfile,
) -> str | None:
    """
    The rules that apply to jobs already in the database as well as new
    ones: blocked company, pay above the ceiling, experience required.
    Returns the reason, or None if the job is fine.
    """
    company_text = " ".join((company or "").lower().split())
    for name in profile.exclude_companies:
        if name and name in company_text:
            return f"blocked company: '{name}'"
    if _above_salary_ceiling(salary_min, profile.maximum_salary):
        return f"pays above £{profile.maximum_salary:,} (likely needs experience)"
    if profile.reject_experience_required:
        phrase = requires_experience(f"{title or ''} {description or ''}")
        if phrase:
            return f"asks for experience: '{phrase}'"
    return None


def _excluded_company(job: Job, exclude_companies: list[str]) -> str | None:
    """Returns the blocked company name matched, or None."""
    company = " ".join((job.company or "").lower().split())
    for name in exclude_companies:
        if name and name in company:
            return name
    return None


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
    "transport", "supply chain", "shipping", "freight", "customer",
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

    exclude_companies = profile.exclude_companies

    for job in jobs:
        blocked = _excluded_company(job, exclude_companies)
        if blocked:
            result.rejected.append((job, f"blocked company: '{blocked}'"))
            continue

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

        if _above_salary_ceiling(job.salary_min, profile.maximum_salary):
            result.rejected.append((job, f"pays above £{profile.maximum_salary:,} (likely needs experience)"))
            continue

        if profile.reject_experience_required:
            phrase = requires_experience(_job_text(job))
            if phrase:
                result.rejected.append((job, f"asks for experience: '{phrase}'"))
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
