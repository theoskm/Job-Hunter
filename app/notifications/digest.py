"""
Builds the daily digest content.

Deliberately has no email or Google dependencies — it just turns scored
jobs into text. That means it is fully testable offline, and the same
content can be emailed (Phase 8), written to a file the scheduler commits
(the zero-setup route), or printed to the terminal.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import date

PRIORITY_EMOJI = {
    "MUST APPLY": "🔥",
    "HIGH": "🟢",
    "GOOD": "🟡",
    "STRETCH": "🟠",
    "LOW": "🔴",
}


@dataclass
class DigestData:
    run_date: str
    total_in_tracker: int
    new_today: int
    count_90_plus: int
    count_80_plus: int
    count_70_plus: int
    top_jobs: list[sqlite3.Row]
    status_counts: dict[str, int]


def gather_digest_data(db, top_n: int = 5) -> DigestData:
    conn = db.conn
    top = conn.execute(
        "SELECT * FROM jobs WHERE score IS NOT NULL AND is_expired = 0 "
        "ORDER BY score DESC, date_posted DESC LIMIT ?",
        (top_n,),
    ).fetchall()

    def count_at_least(threshold: int) -> int:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM jobs WHERE score >= ? AND is_expired = 0",
            (threshold,),
        ).fetchone()
        return row["n"] if row else 0

    total = conn.execute("SELECT COUNT(*) AS n FROM jobs").fetchone()["n"]
    today_iso = date.today().isoformat()
    new_today = conn.execute(
        "SELECT COUNT(*) AS n FROM jobs WHERE substr(first_seen, 1, 10) = ?",
        (today_iso,),
    ).fetchone()["n"]

    return DigestData(
        run_date=today_iso,
        total_in_tracker=total,
        new_today=new_today,
        count_90_plus=count_at_least(90),
        count_80_plus=count_at_least(80),
        count_70_plus=count_at_least(70),
        top_jobs=top,
        status_counts=db.counts_by_status(),
    )


def _parse_list(value: str | None) -> list[str]:
    if not value:
        return []
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, list) else []
    except (json.JSONDecodeError, TypeError):
        return []


def _format_salary(row: sqlite3.Row) -> str:
    low, high = row["salary_min"], row["salary_max"]
    if low and high and low != high:
        return f"£{low:,.0f}–£{high:,.0f}"
    figure = high or low
    return f"£{figure:,.0f}" if figure else "Not stated"


def build_markdown(data: DigestData) -> str:
    lines = [
        f"# Job Hunt Digest — {data.run_date}",
        "",
        f"**{data.new_today} new** today · "
        f"{data.count_90_plus} scoring 90+ · "
        f"{data.count_80_plus} scoring 80+ · "
        f"{data.count_70_plus} scoring 70+ · "
        f"{data.total_in_tracker} tracked in total",
        "",
    ]

    if not data.top_jobs:
        lines.append("No scored matches yet. Run `python3 main.py --run` to search.")
        return "\n".join(lines)

    lines.append("## Top opportunities")
    lines.append("")

    for row in data.top_jobs:
        emoji = PRIORITY_EMOJI.get(row["priority"] or "", "")
        lines.append(f"### {emoji} {row['title']} — {row['company']}")
        lines.append("")
        lines.append(f"**{row['score']}/100 · {row['priority']}**")
        lines.append("")
        lines.append(f"- **Location:** {row['location'] or 'Not stated'}")
        lines.append(f"- **Salary:** {_format_salary(row)}")
        lines.append(f"- **Posted:** {row['date_posted'] or 'Not stated'}")
        if row["working_arrangement"]:
            lines.append(f"- **Arrangement:** {row['working_arrangement']}")

        matching = _parse_list(row["matching_skills"])
        if matching:
            lines.append(f"- **You match on:** {', '.join(matching[:6])}")
        gaps = _parse_list(row["skill_gaps"])
        if gaps:
            lines.append(f"- **Main gap:** {', '.join(gaps[:3])}")

        url = row["direct_application_url"]
        if url:
            lines.append(f"- **Apply:** {url}")
        lines.append("")

    if data.status_counts:
        lines.append("## Your pipeline")
        lines.append("")
        for status, count in sorted(data.status_counts.items(), key=lambda kv: -kv[1]):
            lines.append(f"- {status}: {count}")
        lines.append("")

    lines.append("---")
    lines.append("")
    lines.append(
        "_Scores are automated triage from advert text, not a judgement on whether "
        "a job is right for you. Read the advert before applying._"
    )
    return "\n".join(lines)


def build_plain_text(data: DigestData) -> str:
    """Terminal/email-safe version without markdown syntax."""
    lines = [
        f"JOB HUNT DIGEST — {data.run_date}",
        "=" * 46,
        "",
        f"New today          : {data.new_today}",
        f"Scoring 90+        : {data.count_90_plus}",
        f"Scoring 80+        : {data.count_80_plus}",
        f"Scoring 70+        : {data.count_70_plus}",
        f"Tracked in total   : {data.total_in_tracker}",
        "",
    ]
    if not data.top_jobs:
        lines.append("No scored matches yet. Run: python3 main.py --run")
        return "\n".join(lines)

    lines.append("TOP OPPORTUNITIES")
    lines.append("-" * 46)
    for index, row in enumerate(data.top_jobs, start=1):
        lines.append("")
        lines.append(f"{index}. {row['title']} — {row['company']}")
        lines.append(f"   Score    : {row['score']}/100 ({row['priority']})")
        lines.append(f"   Location : {row['location'] or 'Not stated'}")
        lines.append(f"   Salary   : {_format_salary(row)}")
        lines.append(f"   Posted   : {row['date_posted'] or 'Not stated'}")
        matching = _parse_list(row["matching_skills"])
        if matching:
            lines.append(f"   Matches  : {', '.join(matching[:6])}")
        gaps = _parse_list(row["skill_gaps"])
        if gaps:
            lines.append(f"   Gap      : {', '.join(gaps[:3])}")
        if row["direct_application_url"]:
            lines.append(f"   Apply    : {row['direct_application_url']}")
    return "\n".join(lines)


def subject_line(data: DigestData) -> str:
    if data.count_90_plus:
        top = data.top_jobs[0]
        return f"🔥 MUST APPLY — {top['title']} at {top['company']} — {top['score']}/100"
    if data.count_80_plus:
        top = data.top_jobs[0]
        return f"🟢 HIGH PRIORITY — {top['title']} at {top['company']}"
    return f"Job Hunt Daily Digest — {data.new_today} New Matches"
