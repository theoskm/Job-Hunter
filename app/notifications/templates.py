"""
Phase 8, part 1 — building the emails.

Deliberately separated from sending. Everything here is a pure function
(data in, strings out), so the templates are fully unit-testable with no
Gmail account, no OAuth and no network. Sending lives in gmail.py, which
cannot be tested here.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

PRIORITY_EMOJI = {
    "MUST APPLY": "\U0001F525",
    "HIGH": "\U0001F7E2",
    "GOOD": "\U0001F7E1",
    "STRETCH": "\U0001F7E0",
    "LOW": "\U0001F534",
}


@dataclass
class Email:
    subject: str
    body_text: str
    body_html: str


def _money(row: sqlite3.Row) -> str:
    low, high = row["salary_min"], row["salary_max"]
    if low and high and low != high:
        return f"£{low:,.0f}–£{high:,.0f}"
    figure = high or low
    return f"£{figure:,.0f}" if figure else "not stated"


def _job_url(row: sqlite3.Row) -> str:
    return row["direct_application_url"] or ""


def _skills_list(raw: str | None, limit: int = 4) -> list[str]:
    import json

    if not raw:
        return []
    try:
        values = json.loads(raw)
    except (ValueError, TypeError):
        return []
    return [str(v) for v in values][:limit]


def build_single_alert(row: sqlite3.Row) -> Email:
    """One high-scoring job worth interrupting someone's day for."""
    priority = row["priority"] or "GOOD"
    emoji = PRIORITY_EMOJI.get(priority, "")
    subject = f"{emoji} {priority} — {row['title']} at {row['company']} — {row['score']}/100"

    matching = _skills_list(row["matching_skills"])
    gaps = _skills_list(row["skill_gaps"], limit=3)

    lines = [
        f"{row['title']} — {row['company']}",
        "",
        f"Score:     {row['score']}/100 ({priority})",
        f"Location:  {row['location'] or 'not stated'}",
        f"Salary:    {_money(row)}",
        f"Posted:    {row['date_posted'] or 'not stated'}",
        "",
    ]
    if matching:
        lines.append("Why you match:")
        lines.extend(f"  • {s}" for s in matching)
        lines.append("")
    if gaps:
        lines.append("Gaps to be ready to address:")
        lines.extend(f"  • {g}" for g in gaps)
        lines.append("")
    if _job_url(row):
        lines.append(f"Apply: {_job_url(row)}")
    lines.append("")
    lines.append("Scored automatically from the advert text — read the full advert before applying.")

    html_matching = "".join(f"<li>{s}</li>" for s in matching)
    html_gaps = "".join(f"<li>{g}</li>" for g in gaps)
    body_html = f"""
<div style="font-family:-apple-system,Segoe UI,Roboto,sans-serif;max-width:600px">
  <h2 style="margin-bottom:4px">{row['title']}</h2>
  <p style="color:#555;margin-top:0">{row['company']}</p>
  <p style="font-size:20px;margin:12px 0"><strong>{row['score']}/100</strong>
     <span style="color:#666">{emoji} {priority}</span></p>
  <table style="border-collapse:collapse;font-size:14px">
    <tr><td style="padding:2px 12px 2px 0;color:#666">Location</td><td>{row['location'] or 'not stated'}</td></tr>
    <tr><td style="padding:2px 12px 2px 0;color:#666">Salary</td><td>{_money(row)}</td></tr>
    <tr><td style="padding:2px 12px 2px 0;color:#666">Posted</td><td>{row['date_posted'] or 'not stated'}</td></tr>
  </table>
  {'<h4>Why you match</h4><ul>' + html_matching + '</ul>' if matching else ''}
  {'<h4>Gaps</h4><ul>' + html_gaps + '</ul>' if gaps else ''}
  {'<p><a href="' + _job_url(row) + '" style="background:#1F3864;color:#fff;padding:10px 18px;text-decoration:none;border-radius:4px;display:inline-block">Open the advert</a></p>' if _job_url(row) else ''}
  <p style="color:#888;font-size:12px">Scored automatically from the advert text — read the full advert before applying.</p>
</div>""".strip()

    return Email(subject=subject, body_text="\n".join(lines), body_html=body_html)


def build_results_markdown(rows: list[sqlite3.Row], stats=None) -> str:
    """
    Real markdown for data/latest_results.md — what GitHub Actions commits
    back to the repo after each scheduled run, and what GitHub emails you
    about via its own "file changed" notification if Gmail isn't set up.

    Pulls from the database, not just this run's fresh top 5, so the file
    always reflects the best currently-known matches, same data source as
    the email digest.
    """
    from datetime import datetime, timezone

    lines = [
        "# Job Hunter — Latest Results",
        "",
        f"_Last updated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}_",
        "",
    ]

    if stats:
        lines.append(
            f"**{stats.total_jobs} tracked** · {stats.scoring_80_plus} scoring 80+ · "
            f"{stats.scoring_70_plus} scoring 70+ · {stats.applied} applied · "
            f"{stats.interviews} interview(s)"
        )
        lines.append("")

    if not rows:
        lines.append("No jobs currently meet the tracking threshold.")
        return "\n".join(lines)

    lines.append("## Top matches")
    lines.append("")
    for row in sorted(rows, key=lambda r: r["score"] or 0, reverse=True)[:15]:
        emoji = PRIORITY_EMOJI.get(row["priority"] or "", "")
        lines.append(f"### {row['score']}/100 {emoji} {row['title']} — {row['company']}")
        lines.append(f"- **Location:** {row['location'] or 'not stated'}")
        lines.append(f"- **Salary:** {_money(row)}")
        lines.append(f"- **Status:** {row['status'] or 'NEW'}")
        if row["direct_application_url"]:
            lines.append(f"- [Open the advert]({row['direct_application_url']})")
        matching = _skills_list(row["matching_skills"])
        if matching:
            lines.append(f"- **Matches:** {', '.join(matching)}")
        lines.append("")

    lines.append("---")
    lines.append("_Nothing here is applied for automatically — every application is yours to send._")
    return "\n".join(lines)


def build_digest(rows: list[sqlite3.Row], new_count: int, stats=None) -> Email:
    """The daily summary — Section 18 of the brief."""
    total = len(rows)
    must = sum(1 for r in rows if (r["score"] or 0) >= 90)
    high = sum(1 for r in rows if 80 <= (r["score"] or 0) < 90)
    good = sum(1 for r in rows if 70 <= (r["score"] or 0) < 80)

    subject = f"Job Hunt Daily Digest — {new_count} new match{'es' if new_count != 1 else ''}"

    lines = [
        "JOB HUNT DAILY DIGEST",
        "",
        f"  New since yesterday : {new_count}",
        f"  Tracked total       : {total}",
        f"  Scoring 90+         : {must}",
        f"  Scoring 80-89       : {high}",
        f"  Scoring 70-79       : {good}",
        "",
    ]

    top = sorted(rows, key=lambda r: r["score"] or 0, reverse=True)[:5]
    if top:
        lines.append("TOP OPPORTUNITIES")
        lines.append("")
        for index, row in enumerate(top, start=1):
            emoji = PRIORITY_EMOJI.get(row["priority"] or "", "")
            lines.append(f"{index}. {row['title']} — {row['company']}")
            lines.append(f"   {row['score']}/100 {emoji} {row['priority']}  |  "
                         f"{row['location'] or 'location not stated'}  |  {_money(row)}")
            matching = _skills_list(row["matching_skills"], limit=3)
            if matching:
                lines.append(f"   Matches: {', '.join(matching)}")
            gaps = _skills_list(row["skill_gaps"], limit=2)
            if gaps:
                lines.append(f"   Gap: {', '.join(gaps)}")
            if _job_url(row):
                lines.append(f"   {_job_url(row)}")
            lines.append("")
    else:
        lines.append("No jobs met the tracking threshold today.")
        lines.append("")

    if stats and stats.applied:
        lines.append("YOUR PIPELINE")
        lines.append(f"  Applied: {stats.applied}   Interviews: {stats.interviews}   "
                     f"Offers: {stats.offers}")
        rate = stats.interview_rate
        if rate is not None:
            lines.append(f"  Interview rate: {rate:.0%}")
        lines.append("")

    lines.append("Nothing here is applied for automatically — every application is yours to send.")

    cards = ""
    for row in top:
        emoji = PRIORITY_EMOJI.get(row["priority"] or "", "")
        link = (f'<a href="{_job_url(row)}" style="color:#1F3864">Open advert</a>'
                if _job_url(row) else "")
        cards += f"""
  <div style="border:1px solid #ddd;border-radius:6px;padding:12px;margin-bottom:10px">
    <div style="font-weight:600">{row['title']}</div>
    <div style="color:#666;font-size:13px">{row['company']}</div>
    <div style="margin:6px 0"><strong>{row['score']}/100</strong>
        <span style="color:#666">{emoji} {row['priority']}</span></div>
    <div style="font-size:13px;color:#444">{row['location'] or 'location not stated'} · {_money(row)}</div>
    <div style="margin-top:6px">{link}</div>
  </div>"""

    body_html = f"""
<div style="font-family:-apple-system,Segoe UI,Roboto,sans-serif;max-width:640px">
  <h2>Job Hunt Daily Digest</h2>
  <p style="color:#555">{new_count} new · {total} tracked · {must} scoring 90+ · {high} scoring 80-89</p>
  <h3>Top opportunities</h3>
  {cards if cards else '<p>No jobs met the tracking threshold today.</p>'}
  <p style="color:#888;font-size:12px">Nothing here is applied for automatically —
     every application is yours to send.</p>
</div>""".strip()

    return Email(subject=subject, body_text="\n".join(lines), body_html=body_html)
