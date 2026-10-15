"""
Adzuna API collector.

Verified against the official documentation at developer.adzuna.com/docs/search:

  GET https://api.adzuna.com/v1/api/jobs/{country}/search/{page}
  Auth: app_id and app_key as query string parameters (not headers)
  Params: what (keywords), where (location), salary_min, full_time (1/0),
          permanent (1/0), results_per_page, content-type=application/json

Response is JSON with a "results" list. Each result:
  title, company.display_name, location.display_name, salary_min,
  salary_max, salary_is_predicted, description, created (ISO-8601),
  redirect_url

This has not been tested against a live response in this environment —
the build sandbox has no outbound network. Run `python main.py --test-sources`
on your own machine to confirm it against the real API.
"""

from __future__ import annotations

import requests

from app.collectors.base import CollectorNotConfigured, CollectorRequestFailed, parse_adzuna_date
from app.database.models import Job, JobSource

SEARCH_URL_TEMPLATE = "https://api.adzuna.com/v1/api/jobs/{country}/search/{page}"
REQUEST_TIMEOUT_SECONDS = 15


class AdzunaCollector:
    name = "adzuna"

    def __init__(self, app_id: str, app_key: str, country: str = "gb"):
        self.app_id = app_id
        self.app_key = app_key
        self.country = country

    @property
    def is_configured(self) -> bool:
        return bool(self.app_id and self.app_key)

    def search(
        self,
        keywords: str,
        location: str = "Croydon",
        minimum_salary: int | None = None,
        results_per_page: int = 20,
        page: int = 1,
        full_time_only: bool = True,
        distance_miles: int | None = None,
    ) -> list[Job]:
        if not self.is_configured:
            raise CollectorNotConfigured(self.name)

        url = SEARCH_URL_TEMPLATE.format(country=self.country, page=page)
        params: dict[str, object] = {
            "app_id": self.app_id,
            "app_key": self.app_key,
            "what": keywords,
            "where": location,
            "results_per_page": results_per_page,
            "content-type": "application/json",
        }
        if minimum_salary:
            params["salary_min"] = minimum_salary
        if distance_miles:
            params["distance"] = round(distance_miles * 1.609)  # Adzuna uses km
        if full_time_only:
            params["full_time"] = 1

        try:
            response = requests.get(url, params=params, timeout=REQUEST_TIMEOUT_SECONDS)
            response.raise_for_status()
        except requests.exceptions.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else "unknown"
            if status in (401, 403):
                raise CollectorRequestFailed(
                    self.name,
                    f"HTTP {status} — check ADZUNA_APP_ID and ADZUNA_APP_KEY in .env",
                ) from exc
            raise CollectorRequestFailed(self.name, f"HTTP {status}") from exc
        except requests.exceptions.RequestException as exc:
            raise CollectorRequestFailed(self.name, str(exc)) from exc

        data = response.json()
        return [self._to_job(item) for item in data.get("results", [])]

    @staticmethod
    def _to_job(item: dict) -> Job:
        company = (item.get("company") or {}).get("display_name") or "Not stated"
        location = (item.get("location") or {}).get("display_name")
        redirect_url = item.get("redirect_url") or ""

        return Job(
            title=item.get("title") or "Untitled role",
            company=company,
            location=location,
            salary_min=item.get("salary_min"),
            salary_max=item.get("salary_max"),
            salary_currency="GBP",
            date_posted=parse_adzuna_date(item.get("created")),
            description=item.get("description"),
            direct_application_url=redirect_url,
            sources=[
                JobSource(
                    source="adzuna",
                    source_url=redirect_url,
                    source_job_id=str(item.get("id") or ""),
                )
            ],
        )
