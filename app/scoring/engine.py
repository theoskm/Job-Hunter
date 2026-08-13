"""
Phase 6 — scoring engine.

Read this before trusting a score too far: every sub-score here comes
from counting keyword matches in the job title and description, plus a
few pieces of structured data (salary figures, location string). It has
no understanding of context, tone, or what a job is actually like — it
cannot tell a genuine "no experience needed" advert from one that says
that and then lists five years of requirements underneath. That
distinction is exactly what a human (or a real LLM analysis pass) is for.

Treat this as a first-pass triage that gets the save_to_tracker threshold
roughly right across a large batch of jobs, not as a substitute for
reading the ones that matter.
"""

from __future__ import annotations

from app.database.models import Job
from config.settings import JobProfile


def _job_text(job: Job) -> str:
    return f"{job.title or ''} {job.description or ''}".lower()


def _count_matches(text: str, keywords: list[str]) -> int:
    return sum(1 for kw in keywords if kw in text)


def _scale(count: int, cap_count: int, max_points: int, base_fraction: float = 0.0) -> int:
    """
    Turns a keyword-hit count into a 0..max_points score.
    base_fraction gives a floor even with zero matches, since an advert
    that happens to avoid every listed keyword isn't necessarily a bad
    match — it just wasn't written in the phrases we're scanning for.
    """
    base = base_fraction * max_points
    if cap_count <= 0:
        return round(base)
    scaled = base + (min(count, cap_count) / cap_count) * (max_points - base)
    return round(min(scaled, max_points))


def score_career_transition_fit(job: Job, profile: JobProfile) -> int:
    max_points = profile.scoring_weights["career_transition_fit"]
    text = _job_text(job)
    tier = profile.tier_for_role(job.title or "")

    # Tier match on the title itself is a much stronger signal than a
    # keyword appearing somewhere in a long description.
    tier_score = {1: 0.8, 2: 0.55, 3: 0.9}.get(tier, 0.3)  # tier 3 = PM-track stretch roles
    keyword_count = _count_matches(text, profile.scoring_keywords("career_fit_keywords"))
    keyword_score = _scale(keyword_count, cap_count=4, max_points=max_points, base_fraction=0.0)

    return round(max(tier_score * max_points, keyword_score))


def score_transferable_skills(job: Job, profile: JobProfile) -> int:
    max_points = profile.scoring_weights["transferable_skills"]
    count = _count_matches(_job_text(job), profile.scoring_keywords("transferable_keywords"))
    return _scale(count, cap_count=5, max_points=max_points, base_fraction=0.3)


def score_accessibility(job: Job, profile: JobProfile) -> int:
    max_points = profile.scoring_weights["entry_level_accessibility"]
    text = _job_text(job)
    positive = _count_matches(text, profile.scoring_keywords("accessibility_positive"))
    negative = _count_matches(text, profile.scoring_keywords("accessibility_negative"))

    base = _scale(positive, cap_count=3, max_points=max_points, base_fraction=0.4)
    penalty = min(negative, 3) * (max_points * 0.15)
    return round(max(0, base - penalty))


def score_skills_match(job: Job, profile: JobProfile) -> int:
    max_points = profile.scoring_weights["skills_match"]
    count = _count_matches(_job_text(job), profile.scoring_keywords("skills_keywords"))
    return _scale(count, cap_count=4, max_points=max_points, base_fraction=0.25)


def score_salary(job: Job, profile: JobProfile) -> int:
    max_points = profile.scoring_weights["salary"]
    benchmark = profile.salary_benchmark
    figure = job.salary_max or job.salary_min

    if figure is None:
        # Unknown salary: middling score rather than zero — it already
        # passed the salary floor filter, or had no figure to check
        return round(max_points * 0.5)

    if figure <= profile.minimum_salary:
        return round(max_points * 0.3)
    if figure >= benchmark["excellent"]:
        return max_points
    if figure >= benchmark["good"]:
        # Linear between 'good' and 'excellent'
        span = benchmark["excellent"] - benchmark["good"]
        progress = (figure - benchmark["good"]) / span if span else 1
        return round(max_points * (0.7 + 0.3 * progress))
    # Linear between the floor and 'good'
    span = benchmark["good"] - profile.minimum_salary
    progress = (figure - profile.minimum_salary) / span if span else 1
    return round(max_points * (0.3 + 0.4 * max(0, progress)))


def score_location(job: Job, profile: JobProfile) -> int:
    # profile.location_score already returns a value on the configured
    # 0-5 scale from job_profile.yaml — no rescaling needed unless the
    # location weight itself is changed to something other than 5.
    max_points = profile.scoring_weights["location"]
    raw = profile.location_score(job.location or "")
    configured_max = max((loc["location_score"] for loc in profile.locations), default=5)
    if configured_max <= 0:
        return 0
    return round((raw / configured_max) * max_points)


def score_career_development(job: Job, profile: JobProfile) -> int:
    max_points = profile.scoring_weights["career_development"]
    count = _count_matches(_job_text(job), profile.scoring_keywords("development_keywords"))
    return _scale(count, cap_count=3, max_points=max_points, base_fraction=0.2)


class ScoringEngine:
    """Scores a Job in place and returns it, ready to persist."""

    def __init__(self, profile: JobProfile):
        self.profile = profile

    def score(self, job: Job) -> Job:
        job.career_fit = score_career_transition_fit(job, self.profile)
        job.transferable_skills = score_transferable_skills(job, self.profile)
        job.accessibility = score_accessibility(job, self.profile)
        job.skills_match = score_skills_match(job, self.profile)
        job.salary_score = score_salary(job, self.profile)
        job.location_score = score_location(job, self.profile)
        job.career_development = score_career_development(job, self.profile)

        job.score = sum(
            v for v in (
                job.career_fit, job.transferable_skills, job.accessibility,
                job.skills_match, job.salary_score, job.location_score,
                job.career_development,
            )
        )
        job.priority = self.profile.priority_for_score(job.score)
        return job

    def score_all(self, jobs: list[Job]) -> list[Job]:
        return [self.score(job) for job in jobs]
