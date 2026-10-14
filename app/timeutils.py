"""
Daylight-saving handling.

GitHub Actions cron only understands UTC and does NOT shift for British
Summer Time. A workflow set to '0 8 * * *' runs at 08:00 UK in winter but
09:00 UK in summer.

The fix used here: the workflow is scheduled at BOTH 07:00 and 08:00 UTC,
and this module decides whether the current run is the real one. Exactly
one of those two triggers matches 08:00 Europe/London on any given day.
"""

from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo


def local_now(tz_name: str = "Europe/London") -> datetime:
    return datetime.now(timezone.utc).astimezone(ZoneInfo(tz_name))


def is_scheduled_run_time(
    now_utc: datetime,
    target_local_time: str = "08:00",
    tz_name: str = "Europe/London",
    tolerance_minutes: int = 30,
) -> bool:
    """
    True if `now_utc` corresponds to the target local wall-clock time.

    `tolerance_minutes` absorbs GitHub Actions' scheduling lag, which can
    delay a workflow by several minutes during busy periods.
    """
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)

    local = now_utc.astimezone(ZoneInfo(tz_name))
    target_hour, target_minute = (int(p) for p in target_local_time.split(":"))

    target = local.replace(
        hour=target_hour, minute=target_minute, second=0, microsecond=0
    )
    delta_minutes = abs((local - target).total_seconds()) / 60.0
    return delta_minutes <= tolerance_minutes


def should_run_scheduled(
    now_utc: datetime,
    last_run_local_date: str | None,
    target_local_time: str = "09:00",
    tz_name: str = "Europe/London",
    tolerance_minutes: int = 30,
) -> tuple[bool, str]:
    """
    Decides whether a scheduled GitHub Actions run should do the search.

    Replaces the old "must start within 30 minutes of 09:00" gate. GitHub
    routinely starts scheduled runs hours late (runs on 14 Oct 2026 began
    at 13:25 and 14:25 UTC), so that gate rejected every single run and
    the search never happened.

    New rule: the first run of each UK calendar day that starts at or
    after the target time does the search; every other run that day exits.
    Late is fine. Early (before the target time) waits for the next trigger.
    """
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)

    local = now_utc.astimezone(ZoneInfo(tz_name))
    today = local.date().isoformat()

    if last_run_local_date == today:
        return False, f"already searched today ({today})"

    target_hour, target_minute = (int(p) for p in target_local_time.split(":"))
    target = local.replace(hour=target_hour, minute=target_minute, second=0, microsecond=0)
    if (target - local).total_seconds() / 60.0 > tolerance_minutes:
        return False, f"too early ({local:%H:%M %Z}, target {target_local_time})"

    return True, f"first run of {today} at {local:%H:%M %Z}"


def utc_offset_hours(now_utc: datetime, tz_name: str = "Europe/London") -> float:
    """Current UTC offset — 0.0 in GMT, 1.0 during BST. Useful in logs."""
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)
    offset = now_utc.astimezone(ZoneInfo(tz_name)).utcoffset()
    return offset.total_seconds() / 3600.0 if offset else 0.0
