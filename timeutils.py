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


def utc_offset_hours(now_utc: datetime, tz_name: str = "Europe/London") -> float:
    """Current UTC offset — 0.0 in GMT, 1.0 during BST. Useful in logs."""
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)
    offset = now_utc.astimezone(ZoneInfo(tz_name)).utcoffset()
    return offset.total_seconds() / 3600.0 if offset else 0.0
