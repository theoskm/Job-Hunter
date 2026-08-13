"""
Phase 10 — analytics and the learning loop.

Two jobs:
  1. Summary stats for the dashboard and the daily digest.
  2. The feedback loop from Section 21 of the brief: once enough
     applications exist, work out which job titles, industries and salary
     bands actually produce interviews, so the search can be re-weighted
     on evidence rather than guesswork.

Everything here reads the database only. Nothing here writes or sends.
"""

from __future__ import annotations

import sqlite3
from collections import Counter
from dataclasses import dataclass, field

from app.database.models import USER_OWNED_STATUSES

# Statuses that mean an application was actually submitted
SUBMITTED = {"APPLIED", "FOLLOW-UP", "INTERVIEW", "SECOND INTERVIEW",
             "OFFER", "ACCEPTED", "REJECTED"}
INTERVIEWED = {"INTERVIEW", "SECOND INTERVIEW", "OFFER", "ACCEPTED"}


@dataclass
class Stats:
    total_jobs: int = 0
    jobs_this_week: int = 0
    scoring_80_plus: int = 0
    scoring_70_plus: int = 0
    applied: int = 0
    interviews: int = 0
    offers: int = 0
    rejections: int = 0
    average_score: float = 0.0
    average_salary: float | None = None
    by_status: dict[str, int] = field(default_factory=dict)
    top_companies: list[tuple[str, int]] = field(default_factory=list)
    common_skill_gaps: list[tuple[str, int]] = field(default_factory=list)

    @property
    def interview_rate(self) -> float | None:
        """Interviews as a share of submitted applications."""
        if self.applied == 0:
            return None
        return self.interviews / self.applied


def gather_stats(conn: sqlite3.Connection) -> Stats:
    import json

    rows = conn.execute("SELECT * FROM jobs").fetchall()
    stats = Stats(total_jobs=len(rows))
    if not rows:
        return stats

    scores = [r["score"] for r in rows if r["score"] is not None]
    stats.average_score = sum(scores) / len(scores) if scores else 0.0
    stats.scoring_80_plus = sum(1 for s in scores if s >= 80)
    stats.scoring_70_plus = sum(1 for s in scores if s >= 70)

    salaries = [r["salary_max"] or r["salary_min"] for r in rows]
    salaries = [s for s in salaries if s]
    stats.average_salary = sum(salaries) / len(salaries) if salaries else None

    stats.by_status = dict(Counter(r["status"] for r in rows))
    stats.applied = sum(stats.by_status.get(s, 0) for s in SUBMITTED)
    stats.interviews = sum(stats.by_status.get(s, 0) for s in INTERVIEWED)
    stats.offers = stats.by_status.get("OFFER", 0) + stats.by_status.get("ACCEPTED", 0)
    stats.rejections = stats.by_status.get("REJECTED", 0)

    stats.top_companies = Counter(r["company"] for r in rows).most_common(5)

    gap_counter: Counter = Counter()
    for row in rows:
        raw = row["skill_gaps"]
        if not raw:
            continue
        try:
            for gap in json.loads(raw):
                gap_counter[gap] += 1
        except (ValueError, TypeError):
            continue
    stats.common_skill_gaps = gap_counter.most_common(10)

    return stats


@dataclass
class LearningInsight:
    message: str
    evidence: str
    suggested_action: str


def learning_loop(conn: sqlite3.Connection, minimum_applications: int = 20) -> list[LearningInsight]:
    """
    Section 21: after enough applications, compare what produced interviews
    against what didn't.

    Deliberately returns nothing until `minimum_applications` is reached —
    drawing conclusions from four applications would be noise dressed up as
    insight, and acting on it would make the search worse, not better.
    """
    rows = conn.execute(
        "SELECT * FROM jobs WHERE status IN ({})".format(
            ",".join("?" * len(SUBMITTED))
        ),
        tuple(SUBMITTED),
    ).fetchall()

    if len(rows) < minimum_applications:
        return [
            LearningInsight(
                message=(
                    f"Not enough data yet — {len(rows)} application(s) submitted, "
                    f"{minimum_applications} needed before patterns mean anything."
                ),
                evidence="Small samples produce false patterns.",
                suggested_action="Keep applying; this analysis unlocks itself.",
            )
        ]

    insights: list[LearningInsight] = []

    def rate_by(key_fn, label: str) -> None:
        buckets: dict[str, list[sqlite3.Row]] = {}
        for row in rows:
            key = key_fn(row)
            if key:
                buckets.setdefault(key, []).append(row)

        scored = []
        for key, group in buckets.items():
            if len(group) < 3:  # too few to judge
                continue
            interviews = sum(1 for r in group if r["status"] in INTERVIEWED)
            scored.append((key, interviews / len(group), len(group), interviews))

        if len(scored) < 2:
            return

        scored.sort(key=lambda t: t[1], reverse=True)
        best, best_rate, best_n, best_i = scored[0]
        worst, worst_rate, worst_n, worst_i = scored[-1]
        if best_rate > worst_rate:
            insights.append(
                LearningInsight(
                    message=f"{label}: '{best}' is outperforming '{worst}'.",
                    evidence=(
                        f"'{best}' — {best_i}/{best_n} reached interview "
                        f"({best_rate:.0%}). '{worst}' — {worst_i}/{worst_n} "
                        f"({worst_rate:.0%})."
                    ),
                    suggested_action=(
                        f"Consider weighting '{best}' more heavily in "
                        f"config/job_profile.yaml, and dropping or deprioritising '{worst}'."
                    ),
                )
            )

    def title_bucket(row: sqlite3.Row) -> str | None:
        title = (row["title"] or "").lower()
        for keyword in ("coordinator", "administrator", "assistant",
                        "project", "operations", "support"):
            if keyword in title:
                return keyword
        return None

    def salary_bucket(row: sqlite3.Row) -> str | None:
        figure = row["salary_max"] or row["salary_min"]
        if not figure:
            return None
        if figure < 25000:
            return "under £25k"
        if figure < 30000:
            return "£25-30k"
        if figure < 35000:
            return "£30-35k"
        return "£35k+"

    rate_by(title_bucket, "Job title type")
    rate_by(salary_bucket, "Salary band")

    # Score calibration: are high scores actually converting?
    high = [r for r in rows if (r["score"] or 0) >= 75]
    low = [r for r in rows if (r["score"] or 0) < 75]
    if len(high) >= 3 and len(low) >= 3:
        high_rate = sum(1 for r in high if r["status"] in INTERVIEWED) / len(high)
        low_rate = sum(1 for r in low if r["status"] in INTERVIEWED) / len(low)
        if low_rate > high_rate:
            insights.append(
                LearningInsight(
                    message="Lower-scoring applications are converting better than higher-scoring ones.",
                    evidence=f"75+ scored: {high_rate:.0%} interview rate. Under 75: {low_rate:.0%}.",
                    suggested_action=(
                        "The scoring weights in config/job_profile.yaml are miscalibrated "
                        "for what employers actually respond to — worth reviewing them."
                    ),
                )
            )

    if not insights:
        insights.append(
            LearningInsight(
                message="No clear pattern yet across job types or salary bands.",
                evidence=f"{len(rows)} applications analysed.",
                suggested_action="Keep the current strategy and re-check after more responses.",
            )
        )
    return insights
