"""
Runs both collectors across the target role list.

Deliberately NOT a full cartesian product of 36 roles x 7 locations
(252 calls per source, per run — that would burn through Adzuna's free-tier
quota in a single morning). Instead: Tier 1 roles only, searched from a
single central point (Croydon) with a wide-enough radius to also catch
Sutton, Wimbledon and South London in the same call. That's ~17 calls per
source per run — sustainable daily, and Reed's own radius parameter does
the location-widening for us rather than needing a separate call per town.

Tier 2/3 roles and other locations are intentionally left for a manual
`--run --deep` pass (Phase 6+), not the daily automated one.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.collectors.adzuna import AdzunaCollector
from app.collectors.base import CollectorError
from app.collectors.reed import ReedCollector
from app.database.models import Job
from config.settings import JobProfile, Secrets

PRIMARY_LOCATION = "Croydon"
PRIMARY_RADIUS_MILES = 15


@dataclass
class SourceRunResult:
    source: str
    configured: bool
    queries_attempted: int = 0
    queries_failed: int = 0
    jobs_found: int = 0
    errors: list[str] = field(default_factory=list)


@dataclass
class CollectionRun:
    jobs: list[Job]
    source_results: list[SourceRunResult]

    @property
    def has_any_success(self) -> bool:
        return any(r.jobs_found > 0 for r in self.source_results)


def build_collectors(secrets: Secrets) -> dict[str, object]:
    return {
        "reed": ReedCollector(api_key=secrets.reed_api_key),
        "adzuna": AdzunaCollector(app_id=secrets.adzuna_app_id, app_key=secrets.adzuna_app_key),
    }


def run_collection(profile: JobProfile, secrets: Secrets) -> CollectionRun:
    """
    Searches every Tier 1 role against both configured sources.
    A source with no credentials is skipped, not treated as an error —
    that's the normal state until Reed's key is added.
    A single failed query (bad response, timeout) is logged and skipped;
    it never stops the rest of the run. Per Section 28 of the original brief.
    """
    collectors = build_collectors(secrets)
    search_cfg = profile.raw.get("search", {})
    keywords_list = search_cfg.get("search_keywords") or profile.raw["target_roles"]["tier_1"]
    areas = search_cfg.get("search_areas") or [
        {"name": PRIMARY_LOCATION, "radius_miles": PRIMARY_RADIUS_MILES}
    ]

    all_jobs: list[Job] = []
    results: list[SourceRunResult] = []

    for name, collector in collectors.items():
        result = SourceRunResult(source=name, configured=collector.is_configured)
        if not collector.is_configured:
            results.append(result)
            continue

        for area in areas:
            for role in keywords_list:
                result.queries_attempted += 1
                try:
                    jobs = collector.search(
                        keywords=role,
                        location=area["name"],
                        minimum_salary=profile.minimum_salary,
                        distance_miles=int(area.get("radius_miles", PRIMARY_RADIUS_MILES)),
                    )
                    result.jobs_found += len(jobs)
                    all_jobs.extend(jobs)
                except CollectorError as exc:
                    result.queries_failed += 1
                    result.errors.append(f"{role} / {area['name']}: {exc}")
                    continue  # one bad query must not stop the others

        results.append(result)

    return CollectionRun(jobs=all_jobs, source_results=results)
