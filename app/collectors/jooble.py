"""
Jooble API collector. Added 15 Oct 2026.

Jooble gathers adverts from many UK job sites into one search, so it
reaches listings that Reed and Adzuna don't carry.

  POST https://jooble.org/api/{API_KEY}
  Body (JSON): keywords, location, radius (km, as text), salary (minimum),
               page, ResultOnPage
  Response: {"totalCount": n, "jobs": [{title, location, snippet, salary,
             source, type, link, company, updated, id}, ...]}

Salary arrives as free text ("£25,000 - £28,000 per annum", "£13.50 per
hour") and is parsed into yearly figures below. Descriptions are a short
snippet, like Adzuna's. Run `python3 main.py --test-sources` to confirm it
against the live API.
"""

from __future__ import annotations

import html
import re

import requests

from app.collectors.base import CollectorNotConfigured, CollectorRequestFailed
from app.database.models import Job, JobSource

SEARCH_URL_TEMPLATE = "https://jooble.org/api/{key}"
REQUEST_TIMEOUT_SECONDS = 20

# Rough full-time multipliers to turn hourly/daily/monthly pay into a year
_PERIOD_TO_YEAR = {"hour": 1950, "day": 230, "week": 52, "month": 12}


def parse_salary(text: str | None) -> tuple[float | None, float | None]:
    """'£25,000 - £28,000 per annum' -> (25000, 28000). Unknown -> (None, None)."""
    if not text:
        return None, None
    lowered = text.lower().replace(",", "")
    numbers = []
    for raw, k in re.findall(r"(\d+(?:\.\d+)?)\s*(k)?\b", lowered):
        value = float(raw) * (1000 if k else 1)
        numbers.append(value)
    if not numbers:
        return None, None
    multiplier = 1
    for period, factor in _PERIOD_TO_YEAR.items():
        if period in lowered:
            multiplier = factor
            break
    else:
        # No period stated: small figures are almost certainly hourly
        if max(numbers) < 200:
            multiplier = _PERIOD_TO_YEAR["hour"]
    yearly = [n * multiplier for n in numbers[:2]]
    return min(yearly), max(yearly)


def _clean(text: str | None) -> str:
    text = re.sub(r"<[^>]+>", " ", text or "")
    return " ".join(html.unescape(text).split())


class JoobleCollector:
    name = "jooble"

    def __init__(self, api_key: str):
        self.api_key = api_key

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key)

    def search(
        self,
        keywords: str,
        location: str = "Croydon",
        minimum_salary: int | None = None,
        distance_miles: int | None = None,
        page: int = 1,
        results_per_page: int = 20,
        **_ignored,
    ) -> list[Job]:
        if not self.is_configured:
            raise CollectorNotConfigured(self.name)

        body: dict[str, object] = {
            "keywords": keywords,
            "location": location,
            "page": str(page),
            "ResultOnPage": str(results_per_page),
        }
        if distance_miles:
            body["radius"] = str(round(distance_miles * 1.609))
        if minimum_salary:
            body["salary"] = str(minimum_salary)

        try:
            response = requests.post(
                SEARCH_URL_TEMPLATE.format(key=self.api_key),
                json=body,
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
        except requests.exceptions.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else "unknown"
            if status in (401, 403):
                raise CollectorRequestFailed(
                    self.name, f"HTTP {status} — check JOOBLE_API_KEY in .env"
                ) from exc
            raise CollectorRequestFailed(self.name, f"HTTP {status}") from exc
        except requests.exceptions.RequestException as exc:
            raise CollectorRequestFailed(self.name, str(exc)) from exc

        try:
            data = response.json()
        except ValueError as exc:
            raise CollectorRequestFailed(self.name, "response was not JSON") from exc
        return [self._to_job(item) for item in data.get("jobs", []) or []]

    @staticmethod
    def _to_job(item: dict) -> Job:
        link = item.get("link") or ""
        salary_min, salary_max = parse_salary(item.get("salary"))
        updated = (item.get("updated") or "")[:10] or None
        return Job(
            title=_clean(item.get("title")) or "Untitled role",
            company=_clean(item.get("company")) or "Not stated",
            location=_clean(item.get("location")) or None,
            salary_min=salary_min,
            salary_max=salary_max,
            salary_currency="GBP",
            salary_raw=item.get("salary") or None,
            employment_type=item.get("type") or None,
            date_posted=updated,
            description=_clean(item.get("snippet")),
            direct_application_url=link,
            sources=[JobSource(source="jooble", source_url=link,
                               source_job_id=str(item.get("id") or ""))],
        )
