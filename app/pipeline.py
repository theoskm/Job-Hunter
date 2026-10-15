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
    removed_blocked: int = 0
    window_days_used: int = 0
    window_was_widened: bool = False
    materials_generated: int = 0
    generation_errors: list[str] = field(default_factory=list)
    generation_skipped_reason: str | None = None
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

    # Section 10 of the brief: try 7 days first, widen to 14 then 30 only
    # if that isn't enough. filter_jobs previously always used the first
    # window and nothing wider — found because a real job posted 13 days
    # ago was silently dropped instead of being caught by the 14-day pass.
    minimum_results = profile.raw["search"].get("minimum_results_before_widening", 10)
    filtered = None
    for index, window in enumerate(profile.search_windows):
        filtered = filter_jobs(deduped.unique, profile, max_age_days=window)
        report.window_days_used = window
        report.window_was_widened = index > 0
        if len(filtered.kept) >= minimum_results:
            break
    report.filtered_out = filtered.rejected_count

    # Clear out anything already tracked from a company that has since been
    # blocked. Never touches a job you've applied to or otherwise acted on.
    report.removed_blocked = db.remove_jobs_from_companies(profile.exclude_companies)

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

    generate_for_jobs(scored_jobs, settings, db, report)

    return report


def generate_for_jobs(
    scored_jobs: list[Job], settings: Settings, db: Database, report: PipelineReport
) -> None:
    """
    Generates CV suggestions and a cover letter for jobs crossing the
    threshold, skipping any whose description hasn't changed since it was
    last generated (analysis_hash caching) or whose API key isn't set.
    Never raises — a generation failure must not take down the search.
    """
    from app.generation.tailor import (
        CvContextMissing,
        GenerationNotConfigured,
        generate_materials,
        load_cv_context,
        materials_to_row,
    )

    threshold = settings.profile.thresholds.get(
        "generate_materials_minimum_score", settings.profile.thresholds["save_to_tracker"]
    )
    candidates = [j for j in scored_jobs if (j.score or 0) >= threshold]
    if not candidates:
        return

    api_key = settings.secrets.anthropic_api_key
    if not api_key:
        report.generation_skipped_reason = "ANTHROPIC_API_KEY not set in .env"
        return

    try:
        cv_context = load_cv_context()
    except CvContextMissing as exc:
        report.generation_skipped_reason = str(exc)
        return

    model = settings.profile.raw.get("generation", {}).get(
        "model", "claude-haiku-4-5-20251001"
    )

    for job in candidates:
        existing = db.find_by_canonical_key(job.canonical_key)
        if existing and not db.needs_ai_analysis(job.canonical_key, job.description_hash):
            continue  # unchanged since last generation — do not spend money again

        try:
            materials = generate_materials(
                api_key=api_key,
                cv_context=cv_context,
                job_title=job.title,
                company=job.company,
                job_description=job.description or "",
                model=model,
            )
        except Exception as exc:  # noqa: BLE001 — one bad generation must not stop the rest
            report.generation_errors.append(f"{job.title} at {job.company}: {exc}")
            continue

        job_id = existing["job_id"] if existing else job.job_id
        row = materials_to_row(materials)
        row["analysis_hash"] = job.description_hash
        assignments = ", ".join(f"{col} = ?" for col in row)
        db.conn.execute(
            f"UPDATE jobs SET {assignments} WHERE job_id = ?",
            [*row.values(), job_id],
        )
        db.conn.commit()
        report.materials_generated += 1
