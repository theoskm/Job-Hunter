"""
Reed Jobseeker API collector.

Verified request/response format (no official OpenAPI spec is published,
so this is cross-checked against reed.co.uk/developers/jobseeker and
multiple independent client implementations):

  GET https://www.reed.co.uk/api/1.0/search
  Auth: HTTP Basic — API key as username, blank password
  Params: keywords, locationName, distanceFromLocation (miles), minimumSalary,
          maximumSalary, permanent/contract/temp/partTime/fullTime (bool),
          resultsToTake, resultsToSkip

Response is JSON with a "results" list. Each result's actual field names
(confirmed from Reed client library documentation):
  jobId, employerName, jobTitle, locationName, minimumSalary, maximumSalary,
  currency, date, expirationDate, jobDescription, jobUrl, partTime, fullTime

This has not been tested against a live response in this environment —
the build sandbox has no outbound network. Run `python main.py --test-sources`
on your own machine to confirm it against the real API.
"""

from __future__ import annotations

import requests

from app.collectors.base import CollectorNotConfigured, CollectorRequestFailed, parse_reed_date
from app.database.models import Job, JobSource

SEARCH_URL = "https://www.reed.co.uk/api/1.0/search"
DETAILS_URL = "https://www.reed.co.uk/api/1.0/jobs/{job_id}"
REQUEST_TIMEOUT_SECONDS = 15


class ReedCollector:
    name = "reed"

    def __init__(self, api_key: str):
        self.api_key = api_key

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key)

    def search(
        self,
        keywords: str,
        location: str = "Croydon",
        distance_miles: int = 15,
        minimum_salary: int | None = None,
        results_to_take: int = 20,
        full_time_only: bool = True,
    ) -> list[Job]:
        if not self.is_configured:
            raise CollectorNotConfigured(self.name)

        params: dict[str, object] = {
            "keywords": keywords,
            "locationName": location,
            "distanceFromLocation": distance_miles,
            "resultsToTake": results_to_take,
        }
        if minimum_salary:
            params["minimumSalary"] = minimum_salary
        if full_time_only:
            params["fullTime"] = "true"

        try:
            response = requests.get(
                SEARCH_URL,
                params=params,
                auth=(self.api_key, ""),
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
        except requests.exceptions.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else "unknown"
            if status == 401:
                raise CollectorRequestFailed(
                    self.name, "401 Unauthorized — check REED_API_KEY in .env"
                ) from exc
            raise CollectorRequestFailed(self.name, f"HTTP {status}") from exc
        except requests.exceptions.RequestException as exc:
            raise CollectorRequestFailed(self.name, str(exc)) from exc

        data = response.json()
        return [self._to_job(item) for item in data.get("results", [])]

    def fetch_full_description(self, job_id: str) -> str | None:
        """
        Search results only carry the first few hundred characters of an
        advert, which usually cuts off before the requirements section.
        The details endpoint returns the whole thing (as HTML). Returns
        plain text, or None on any failure; never raises.
        """
        if not self.is_configured or not job_id:
            return None
        try:
            response = requests.get(
                DETAILS_URL.format(job_id=job_id),
                auth=(self.api_key, ""),
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
            html = response.json().get("jobDescription") or ""
        except (requests.exceptions.RequestException, ValueError):
            return None
        import html as html_lib
        import re

        text = re.sub(r"<[^>]+>", " ", html)
        text = html_lib.unescape(text)
        return " ".join(text.split()) or None

    @staticmethod
    def _to_job(item: dict) -> Job:
        job_id = item.get("jobId")
        job_url = item.get("jobUrl") or ""
        employment_type = None
        if item.get("fullTime"):
            employment_type = "Full-time"
        elif item.get("partTime"):
            employment_type = "Part-time"

        return Job(
            title=item.get("jobTitle") or "Untitled role",
            company=item.get("employerName") or "Not stated",
            location=item.get("locationName"),
            salary_min=item.get("minimumSalary"),
            salary_max=item.get("maximumSalary"),
            salary_currency=item.get("currency") or "GBP",
            salary_raw=None,
            employment_type=employment_type,
            date_posted=parse_reed_date(item.get("date")),
            closing_date=parse_reed_date(item.get("expirationDate")),
            description=item.get("jobDescription"),
            direct_application_url=job_url,
            sources=[
                JobSource(
                    source="reed",
                    source_url=job_url,
                    source_job_id=str(job_id) if job_id is not None else None,
                )
            ],
        )
