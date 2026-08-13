"""
Diagnostics for the scoring engine.

Exists because "nothing scored 70+" is not by itself actionable — you need
to see WHICH component is dragging, and whether the advert text the scorer
saw was even substantial enough to judge.
"""

from __future__ import annotations

import sqlite3

from config.settings import JobProfile


def describe_scores(rows: list[sqlite3.Row], profile: JobProfile, limit: int = 10) -> str:
    if not rows:
        return "No scored jobs in the database yet — run `python main.py --run` first."

    weights = profile.scoring_weights
    lines: list[str] = []
    lines.append(f"{len(rows)} scored job(s) in the database. Showing top {min(limit, len(rows))}.\n")

    # Component averages tell you what's systematically dragging
    totals = {k: 0 for k in weights}
    field_map = {
        "career_transition_fit": "career_fit",
        "transferable_skills": "transferable_skills",
        "entry_level_accessibility": "accessibility",
        "skills_match": "skills_match",
        "salary": "salary_score",
        "location": "location_score",
        "career_development": "career_development",
    }
    for row in rows:
        for weight_key, column in field_map.items():
            totals[weight_key] += row[column] or 0

    lines.append("AVERAGE SCORE BY COMPONENT (average / max — the low ones are your bottleneck)")
    for weight_key, column in field_map.items():
        avg = totals[weight_key] / len(rows)
        max_points = weights[weight_key]
        pct = (avg / max_points * 100) if max_points else 0
        bar = "#" * int(pct / 10)
        lines.append(f"  {weight_key:<28} {avg:5.1f} / {max_points:<3} {pct:5.1f}%  {bar}")

    lines.append("\nTOP JOBS")
    for row in rows[:limit]:
        desc_len = len(row["description"] or "")
        lines.append(
            f"\n  {row['score']:>3}  {row['priority']:<12} {row['title']} — {row['company']}"
        )
        lines.append(
            f"       fit {row['career_fit']}/{weights['career_transition_fit']}  "
            f"transferable {row['transferable_skills']}/{weights['transferable_skills']}  "
            f"access {row['accessibility']}/{weights['entry_level_accessibility']}  "
            f"skills {row['skills_match']}/{weights['skills_match']}  "
            f"salary {row['salary_score']}/{weights['salary']}  "
            f"loc {row['location_score']}/{weights['location']}  "
            f"dev {row['career_development']}/{weights['career_development']}"
        )
        lines.append(f"       advert text the scorer saw: {desc_len} characters")

    # The single most common cause of uniformly low scores
    avg_desc = sum(len(r["description"] or "") for r in rows) / len(rows)
    lines.append(f"\nAverage advert length across all scored jobs: {avg_desc:.0f} characters")
    if avg_desc < 400:
        lines.append(
            "\n  ⚠ These descriptions are short — most likely API snippets rather than\n"
            "    full adverts. Four of the seven scoring components read the advert\n"
            "    text, so short text means low scores regardless of how good the job\n"
            "    actually is. This is a data problem, not a job-quality problem."
        )
    return "\n".join(lines)
