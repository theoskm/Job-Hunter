"""
Phase 5 — deduplication.

Two different situations produce duplicates in a single collection run:

1. The SAME source returns the same job twice, because it matched more
   than one Tier 1 role search (e.g. "Operations Coordinator" matches
   both the "Operations Coordinator" and "Operations Assistant" queries).
2. TWO DIFFERENT sources (Reed and Adzuna) both index the same real-world
   vacancy.

The database's upsert_job() already handles this correctly on its own —
a second copy just updates the existing row rather than creating a new
one. This module dedupes BEFORE that, within the batch itself, so:
  - the run report can say how many duplicates were found, which matters
    for Section 12's "record all sources" requirement
  - a job with genuinely different descriptions across sources (Reed's
    write-up vs Adzuna's) gets the longer, more complete one kept, rather
    than whichever happened to be upserted last
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.database.models import Job, build_canonical_key


@dataclass
class DedupeResult:
    unique: list[Job] = field(default_factory=list)
    duplicate_count: int = 0


def dedupe_batch(jobs: list[Job]) -> DedupeResult:
    by_key: dict[str, Job] = {}

    for job in jobs:
        key = build_canonical_key(job.title, job.company, job.location)

        if key not in by_key:
            by_key[key] = job
            continue

        # Duplicate found — merge sources onto the first-seen copy rather
        # than discarding the new one outright.
        existing = by_key[key]
        existing.sources.extend(job.sources)

        # Prefer whichever description is more complete; a short teaser
        # from one board shouldn't overwrite a fuller one from another.
        if job.description and (
            not existing.description or len(job.description) > len(existing.description)
        ):
            existing.description = job.description

        # Fill in anything the first copy was missing
        if existing.salary_min is None and job.salary_min is not None:
            existing.salary_min = job.salary_min
        if existing.salary_max is None and job.salary_max is not None:
            existing.salary_max = job.salary_max
        if not existing.direct_application_url and job.direct_application_url:
            existing.direct_application_url = job.direct_application_url

    result = DedupeResult(unique=list(by_key.values()))
    result.duplicate_count = len(jobs) - len(result.unique)
    return result
