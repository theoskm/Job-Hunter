"""
Phase 7 — Google Sheets CRM sync.

*** SENDING IS UNVERIFIED ***
No real spreadsheet has ever been written to from this environment.
The row-building logic below IS tested; the API calls are not.

Critically, this sync is one-way with one exception: your Status column
is never overwritten. If you mark a job APPLIED in the sheet, a later
automated run refreshes the salary, description and score but leaves
your status alone — the same guarantee the local database gives you.
"""

from __future__ import annotations

import sqlite3

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

HEADERS = [
    "Job ID", "Job Title", "Company", "Location", "Salary", "Employment Type",
    "Working Arrangement", "Source", "Direct Application URL", "Source URL",
    "Date Posted", "Closing Date", "Score", "Priority", "Career Fit",
    "Transferable Skills", "Accessibility", "Skills Match", "Salary Score",
    "Location Score", "Career Development", "Matching Skills", "Skill Gaps",
    "Why It Matches", "CV Version", "Cover Letter Required", "Application Date",
    "Status", "Follow Up Date", "Interview Date", "Rejection Reason", "Notes",
    "First Seen", "Last Updated",
]

STATUS_COLUMN_INDEX = HEADERS.index("Status")  # protected from overwrite


class SheetsNotConfigured(Exception):
    """Raised when the spreadsheet id or service account file is missing."""


def _money(row: sqlite3.Row) -> str:
    low, high = row["salary_min"], row["salary_max"]
    if low and high and low != high:
        return f"£{low:,.0f}-£{high:,.0f}"
    figure = high or low
    return f"£{figure:,.0f}" if figure else ""


def _joined(raw: str | None) -> str:
    import json

    if not raw:
        return ""
    try:
        return ", ".join(str(v) for v in json.loads(raw))
    except (ValueError, TypeError):
        return ""


def build_row(row: sqlite3.Row, source_urls: str = "") -> list[str]:
    """Turns a database row into a spreadsheet row. Pure function — tested."""
    return [
        row["job_id"] or "",
        row["title"] or "",
        row["company"] or "",
        row["location"] or "",
        _money(row),
        row["employment_type"] or "",
        row["working_arrangement"] or "",
        "",  # Source — filled from job_sources
        row["direct_application_url"] or "",
        source_urls,
        row["date_posted"] or "",
        row["closing_date"] or "",
        str(row["score"]) if row["score"] is not None else "",
        row["priority"] or "",
        str(row["career_fit"] or ""),
        str(row["transferable_skills"] or ""),
        str(row["accessibility"] or ""),
        str(row["skills_match"] or ""),
        str(row["salary_score"] or ""),
        str(row["location_score"] or ""),
        str(row["career_development"] or ""),
        _joined(row["matching_skills"]),
        _joined(row["skill_gaps"]),
        row["reasoning"] or "",
        row["cv_version"] or "",
        "Yes" if row["cover_letter_required"] else "",
        row["application_date"] or "",
        row["status"] or "NEW",
        row["follow_up_date"] or "",
        row["interview_date"] or "",
        row["rejection_reason"] or "",
        row["notes"] or "",
        row["first_seen"] or "",
        row["last_updated"] or "",
    ]


def build_all_rows(conn: sqlite3.Connection, minimum_score: int = 0) -> list[list[str]]:
    rows = conn.execute(
        "SELECT * FROM jobs WHERE COALESCE(score, 0) >= ? ORDER BY score DESC",
        (minimum_score,),
    ).fetchall()

    output = []
    for row in rows:
        sources = conn.execute(
            "SELECT source, source_url FROM job_sources WHERE job_id = ?",
            (row["job_id"],),
        ).fetchall()
        urls = " | ".join(s["source_url"] for s in sources if s["source_url"])
        built = build_row(row, source_urls=urls)
        built[7] = ", ".join(sorted({s["source"] for s in sources}))
        output.append(built)
    return output


def _service(service_account_json: str):
    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build
    except ImportError as exc:  # pragma: no cover
        raise SheetsNotConfigured(
            "Google libraries are not installed — run: pip3 install -r requirements.txt"
        ) from exc

    from pathlib import Path

    if not Path(service_account_json).exists():
        raise SheetsNotConfigured(
            f"No service account file at {service_account_json}. "
            "See USER_GUIDE.md, 'Connecting Google Sheets'."
        )
    creds = service_account.Credentials.from_service_account_file(
        service_account_json, scopes=SCOPES
    )
    return build("sheets", "v4", credentials=creds)


def sync_to_sheet(
    conn: sqlite3.Connection,
    spreadsheet_id: str,
    service_account_json: str,
    sheet_name: str = "Job Hunt CRM",
    minimum_score: int = 0,
) -> int:
    """
    Writes the tracker to Google Sheets. Returns the number of rows written.

    UNVERIFIED — this specific call has never been executed against a real
    spreadsheet from this environment.

    Existing Status values in the sheet are read first and preserved, so a
    sync never undoes a status you set by hand.
    """
    if not spreadsheet_id:
        raise SheetsNotConfigured("GOOGLE_SHEETS_SPREADSHEET_ID is not set in .env")

    service = _service(service_account_json)
    sheets = service.spreadsheets()

    # Preserve manually-set statuses, keyed by Job ID
    existing_statuses: dict[str, str] = {}
    try:
        current = sheets.values().get(
            spreadsheetId=spreadsheet_id, range=f"{sheet_name}!A:AH"
        ).execute()
        for line in current.get("values", [])[1:]:
            if len(line) > STATUS_COLUMN_INDEX and line[0]:
                existing_statuses[line[0]] = line[STATUS_COLUMN_INDEX]
    except Exception:  # noqa: BLE001 — first run has no sheet yet
        existing_statuses = {}

    rows = build_all_rows(conn, minimum_score=minimum_score)
    for row in rows:
        job_id = row[0]
        if job_id in existing_statuses and existing_statuses[job_id]:
            row[STATUS_COLUMN_INDEX] = existing_statuses[job_id]

    body = {"values": [HEADERS] + rows}
    sheets.values().update(
        spreadsheetId=spreadsheet_id,
        range=f"{sheet_name}!A1",
        valueInputOption="RAW",
        body=body,
    ).execute()
    return len(rows)
