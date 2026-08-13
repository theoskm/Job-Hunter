"""
The full daily pipeline: SEARCH -> FILTER -> DEDUPLICATE -> SCORE -> TRACK.
(Alerting and the learning loop are Phases 8 and beyond.)

This is what `python main.py --run` calls. Each stage's counts are kept
for the run report and the run_log table, so a search that suddenly
returns far fewer jobs than usual is visible in the numbers, not silently
buried.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.collectors.run import run_collection
from app.database.db import Database
from app.database.models import Job
from app.deduplication.dedup import dedupe_batch
from app.filtering.filters import filter_jobs
from app.scoring.engine import ScoringEngine
from config.settings import Secrets, Settings


@dataclass
class PipelineReport:
    raw_found: int = 0
    filtered_out: int = 0
    duplicates_merged: int = 0
    scored: int = 0
    new_jobs: int = 0
    updated_jobs: int = 0
    saved_to_tracker: int = 0
    source_errors: list[str] = field(default_factory=list)
    top_matches: list[Job] = field(default_factory=list)


def run_full_pipeline(settings: Settings, db: Database) -> PipelineReport:
    profile = settings.profile
    report = PipelineReport()

    collection = run_collection(profile, settings.secrets)
    report.raw_found = len(collection.jobs)
    for source_result in collection.source_results:
        report.source_errors.extend(source_result.errors)

    # Deduplicate BEFORE filtering. Each Tier 1 role is searched separately,
    # so the same vacancy arrives many times over; filtering first would
    # count one rejected job once per query it appeared in, making
    # `filtered_out` meaningless (17 rather than 1). Deduping first also
    # means the filters do a fraction of the work.
    deduped = dedupe_batch(collection.jobs)
    report.duplicates_merged = deduped.duplicate_count

    filtered = filter_jobs(deduped.unique, profile)
    report.filtered_out = filtered.rejected_count

    scorer = ScoringEngine(profile)
    scored_jobs: list[Job] = scorer.score_all(filtered.kept)
    report.scored = len(scored_jobs)

    for job in scored_jobs:
        job_id, existed = db.upsert_job(job)
        for source in job.sources:
            db.add_source(job_id, source)
        if existed:
            report.updated_jobs += 1
        else:
            report.new_jobs += 1

    threshold = profile.thresholds["save_to_tracker"]
    savable = [j for j in scored_jobs if (j.score or 0) >= threshold]
    report.saved_to_tracker = len(savable)
    report.top_matches = sorted(savable, key=lambda j: j.score or 0, reverse=True)[:5]

    return report
