"""
Loads configuration from two places:

  config/job_profile.yaml  -> what jobs to look for (safe to edit by hand)
  .env                     -> secrets: API keys, addresses (never committed)

Validation happens at load time so a typo fails loudly and immediately
rather than silently producing wrong scores.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - dotenv is optional at runtime
    def load_dotenv(*_args: Any, **_kwargs: Any) -> bool:
        return False


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PROFILE_PATH = PROJECT_ROOT / "config" / "job_profile.yaml"


class ConfigError(Exception):
    """Raised when the configuration is invalid. Message is user-facing."""


@dataclass
class Secrets:
    """Values read from .env. Empty string means 'not configured yet'."""

    reed_api_key: str = ""
    adzuna_app_id: str = ""
    adzuna_app_key: str = ""
    anthropic_api_key: str = ""
    gmail_sender: str = ""
    gmail_recipient: str = ""
    google_oauth_client_secrets: str = ""
    google_token_path: str = ""
    sheets_spreadsheet_id: str = ""
    service_account_json: str = ""
    database_path: str = "data/job_hunter.db"
    log_level: str = "INFO"

    @classmethod
    def from_env(cls) -> "Secrets":
        load_dotenv(PROJECT_ROOT / ".env")
        g = os.getenv
        return cls(
            reed_api_key=g("REED_API_KEY", "") or "",
            adzuna_app_id=g("ADZUNA_APP_ID", "") or "",
            adzuna_app_key=g("ADZUNA_APP_KEY", "") or "",
            anthropic_api_key=g("ANTHROPIC_API_KEY", "") or "",
            gmail_sender=g("GMAIL_SENDER_ADDRESS", "") or "",
            gmail_recipient=g("GMAIL_RECIPIENT_ADDRESS", "") or "",
            google_oauth_client_secrets=g("GOOGLE_OAUTH_CLIENT_SECRETS", "") or "",
            google_token_path=g("GOOGLE_TOKEN_PATH", "") or "",
            sheets_spreadsheet_id=g("GOOGLE_SHEETS_SPREADSHEET_ID", "") or "",
            service_account_json=g("GOOGLE_SERVICE_ACCOUNT_JSON", "") or "",
            database_path=g("DATABASE_PATH", "data/job_hunter.db") or "data/job_hunter.db",
            log_level=g("LOG_LEVEL", "INFO") or "INFO",
        )

    def source_readiness(self) -> dict[str, bool]:
        """Which collectors have the credentials they need."""
        return {
            "reed": bool(self.reed_api_key),
            "adzuna": bool(self.adzuna_app_id and self.adzuna_app_key),
        }


@dataclass
class JobProfile:
    """The user-editable search profile, validated."""

    raw: dict[str, Any] = field(repr=False)

    # ---- convenience accessors -------------------------------------
    @property
    def all_target_roles(self) -> list[str]:
        tiers = self.raw["target_roles"]
        return [r for tier in ("tier_1", "tier_2", "tier_3") for r in tiers.get(tier, [])]

    def tier_for_role(self, role_title: str) -> int | None:
        """Return 1, 2 or 3 if the title matches a configured target role."""
        needle = role_title.strip().lower()
        for idx, tier in enumerate(("tier_1", "tier_2", "tier_3"), start=1):
            for configured in self.raw["target_roles"].get(tier, []):
                if configured.strip().lower() in needle:
                    return idx
        return None

    @property
    def locations(self) -> list[dict[str, Any]]:
        return self.raw["locations"]

    def location_score(self, location_name: str) -> int:
        """Best matching configured location score, else 1 (very long commute)."""
        needle = (location_name or "").strip().lower()
        best = 0
        for loc in self.locations:
            if loc["name"].strip().lower() in needle:
                best = max(best, int(loc["location_score"]))
        return best if best else 1

    @property
    def scoring_weights(self) -> dict[str, int]:
        return self.raw["scoring_weights"]

    @property
    def thresholds(self) -> dict[str, int]:
        return self.raw["thresholds"]

    @property
    def priority_bands(self) -> dict[str, int]:
        return self.raw["priority_bands"]

    @property
    def search_windows(self) -> list[int]:
        return self.raw["search"]["windows_days"]

    @property
    def minimum_salary(self) -> int:
        return int(self.raw["search"]["minimum_salary"])

    @property
    def exclude_companies(self) -> list[str]:
        return [c.lower().strip() for c in self.raw["search"].get("exclude_companies", []) or []]

    @property
    def exclude_keywords(self) -> list[str]:
        return [k.lower() for k in self.raw["search"].get("exclude_keywords", [])]

    @property
    def run_at_local_time(self) -> str:
        return self.raw["schedule"]["run_at_local_time"]

    @property
    def timezone(self) -> str:
        return self.raw["schedule"]["timezone"]

    def priority_for_score(self, score: int) -> str:
        b = self.priority_bands
        if score >= b["must_apply"]:
            return "MUST APPLY"
        if score >= b["high"]:
            return "HIGH"
        if score >= b["good"]:
            return "GOOD"
        if score >= b["stretch"]:
            return "STRETCH"
        return "LOW"

    @property
    def salary_benchmark(self) -> dict[str, int]:
        return self.raw["salary_benchmark"]

    def scoring_keywords(self, category: str) -> list[str]:
        """Lower-cased keyword list for a scoring category, e.g. 'skills_keywords'."""
        return [k.lower() for k in self.raw["scoring_keywords"].get(category, [])]

    # ---- loading & validation --------------------------------------
    @classmethod
    def load(cls, path: Path | str = DEFAULT_PROFILE_PATH) -> "JobProfile":
        path = Path(path)
        if not path.exists():
            raise ConfigError(
                f"Could not find your job profile at {path}.\n"
                "This file tells the system what jobs to look for."
            )
        with path.open("r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
        if not isinstance(data, dict):
            raise ConfigError(f"{path} is not valid YAML — check the indentation.")
        profile = cls(raw=data)
        profile.validate()
        return profile

    def validate(self) -> None:
        required_top = [
            "candidate", "target_roles", "locations",
            "scoring_weights", "priority_bands", "thresholds", "search", "schedule",
        ]
        missing = [k for k in required_top if k not in self.raw]
        if missing:
            raise ConfigError(
                "Your job profile is missing these sections: "
                + ", ".join(missing)
            )

        weights = self.raw["scoring_weights"]
        expected_keys = {
            "career_transition_fit", "transferable_skills",
            "entry_level_accessibility", "skills_match",
            "salary", "location", "career_development",
        }
        if set(weights) != expected_keys:
            raise ConfigError(
                "scoring_weights must contain exactly these keys: "
                + ", ".join(sorted(expected_keys))
            )

        total = sum(int(v) for v in weights.values())
        if total != 100:
            raise ConfigError(
                f"Your scoring_weights add up to {total}, but they must add up to 100. "
                "Adjust the numbers in config/job_profile.yaml."
            )

        if not self.all_target_roles:
            raise ConfigError("You have no target roles configured — add at least one.")

        if not self.locations:
            raise ConfigError("You have no locations configured — add at least one.")

        for loc in self.locations:
            if not 0 <= int(loc.get("location_score", -1)) <= 5:
                raise ConfigError(
                    f"Location '{loc.get('name')}' has an invalid location_score "
                    "(must be between 0 and 5)."
                )

        bands = self.raw["priority_bands"]
        ordered = [bands["must_apply"], bands["high"], bands["good"], bands["stretch"]]
        if ordered != sorted(ordered, reverse=True):
            raise ConfigError(
                "priority_bands must descend: must_apply > high > good > stretch."
            )

        windows = self.raw["search"].get("windows_days", [])
        if not windows or sorted(windows) != windows:
            raise ConfigError(
                "search.windows_days must be listed smallest first, e.g. [7, 14, 30]."
            )

        if "salary_benchmark" not in self.raw:
            raise ConfigError("Missing salary_benchmark section.")
        sb = self.raw["salary_benchmark"]
        if sb.get("excellent", 0) <= sb.get("good", 0):
            raise ConfigError("salary_benchmark.excellent must be greater than .good.")

        if "scoring_keywords" not in self.raw:
            raise ConfigError("Missing scoring_keywords section.")
        required_categories = {
            "accessibility_positive", "accessibility_negative", "skills_keywords",
            "transferable_keywords", "career_fit_keywords", "development_keywords",
        }
        missing = required_categories - set(self.raw["scoring_keywords"])
        if missing:
            raise ConfigError(
                "scoring_keywords is missing categories: " + ", ".join(sorted(missing))
            )


@dataclass
class Settings:
    profile: JobProfile
    secrets: Secrets

    @classmethod
    def load(cls, profile_path: Path | str = DEFAULT_PROFILE_PATH) -> "Settings":
        return cls(profile=JobProfile.load(profile_path), secrets=Secrets.from_env())
