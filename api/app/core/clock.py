"""Time and the Student's day (Consistency Conventions: Time and days).

Timestamps are UTC. A Student's day is defined by ``profile.preferences.timezone`` (IANA,
default ``Asia/Kolkata``); every per-day rule uses :func:`local_day` and
:func:`next_local_time`.
"""

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DEFAULT_TIMEZONE = "Asia/Kolkata"


def utcnow() -> datetime:
    return datetime.now(UTC)


def zone(tz: str | None) -> ZoneInfo:
    """``tz`` as a ZoneInfo; an empty or unknown name falls back to :data:`DEFAULT_TIMEZONE`."""
    if tz:
        try:
            return ZoneInfo(tz)
        except (ZoneInfoNotFoundError, ValueError):
            pass
    return ZoneInfo(DEFAULT_TIMEZONE)


def local_day(tz: str | None, now: datetime | None = None) -> date:
    """The calendar date in ``tz`` (default Asia/Kolkata) at ``now`` (default: the current
    time). A naive ``now`` is taken as UTC."""
    at = now or utcnow()
    if at.tzinfo is None:
        at = at.replace(tzinfo=UTC)
    return at.astimezone(zone(tz)).date()


def next_local_time(tz: str | None, hour: int, now: datetime | None = None) -> datetime:
    """The first ``hour``:00 in ``tz`` (default Asia/Kolkata) strictly after ``now``, in UTC."""
    if not 0 <= hour <= 23:
        raise ValueError("hour must be 0..23")
    at = now or utcnow()
    if at.tzinfo is None:
        at = at.replace(tzinfo=UTC)
    zinfo = zone(tz)
    local = at.astimezone(zinfo)
    candidate = datetime.combine(local.date(), time(hour), zinfo)
    if candidate <= at:
        candidate = datetime.combine(local.date() + timedelta(days=1), time(hour), zinfo)
    return candidate.astimezone(UTC)
