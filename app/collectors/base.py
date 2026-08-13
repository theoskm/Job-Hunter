"""
Shared pieces every collector uses: a common error type so a failing source
never crashes the whole run, and the small amount of shared date/number
parsing that both Reed and Adzuna responses need.
"""

from __future__ import annotations

import re
from datetime import date, datetime


class CollectorError(Exception):
    """Base class for anything a collector deliberately raises."""


class CollectorNotConfigured(CollectorError):
    """Raised when a collector is used without its required credentials."""

    def __init__(self, source_name: str):
        self.source_name = source_name
        super().__init__(
            f"'{source_name}' has no API credentials configured — "
            f"add them to .env and see .env.example for the variable names."
        )


class CollectorRequestFailed(CollectorError):
    """Raised when the API itself returns an error (bad key, rate limit, etc.)."""

    def __init__(self, source_name: str, detail: str):
        self.source_name = source_name
        self.detail = detail
        super().__init__(f"'{source_name}' request failed: {detail}")


def parse_reed_date(value: str | None) -> str | None:
    """Reed dates arrive as 'DD/MM/YYYY'. Returns ISO-8601, or None."""
    if not value:
        return None
    try:
        return datetime.strptime(value.strip(), "%d/%m/%Y").date().isoformat()
    except ValueError:
        return None


def parse_adzuna_date(value: str | None) -> str | None:
    """Adzuna dates arrive as full ISO-8601 timestamps. Returns just the date."""
    if not value:
        return None
    try:
        # Handles both '...Z' and '...+00:00' forms
        cleaned = value.strip().replace("Z", "+00:00")
        return datetime.fromisoformat(cleaned).date().isoformat()
    except ValueError:
        # Fall back to taking the date portion before 'T' if parsing fails
        match = re.match(r"^(\d{4}-\d{2}-\d{2})", value.strip())
        return match.group(1) if match else None


def is_within_days(iso_date: str | None, max_age_days: int, today: date | None = None) -> bool:
    """True if iso_date is within max_age_days of today. Unknown dates pass through."""
    if not iso_date:
        return True
    try:
        posted = date.fromisoformat(iso_date)
    except ValueError:
        return True
    reference = today or date.today()
    return (reference - posted).days <= max_age_days
