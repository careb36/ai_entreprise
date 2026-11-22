"""When a routine runs, as data (``services/loop/routines.py``).

A schedule is ``{"every": "day" | "week", "at": "HH:MM", "timezone": <IANA
name>}``, plus ``"weekday"`` (``0`` Monday … ``6`` Sunday) for ``week``. It is
a time on the company's clock, never an interval from the last run, so a
backend that was down does not shift it: ``next_after`` answers the first
occurrence strictly after a moment, and the occurrences missed while it was
down collapse into one (Paperclip's routines coalesce the same way).

Pure: no database, no clock of its own.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

EVERY = ("day", "week")
_AT = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


class InvalidSchedule(ValueError):
    """A schedule the backend refuses; the message says why."""


def validate(raw: Any) -> dict[str, Any]:
    """*raw* as a stored schedule, or ``InvalidSchedule``."""
    if not isinstance(raw, dict):
        raise InvalidSchedule("schedule must be an object")
    unknown = set(raw) - {"every", "at", "timezone", "weekday"}
    if unknown:
        raise InvalidSchedule(
            f"unknown schedule field(s): {', '.join(sorted(unknown))}"
        )
    every = raw.get("every")
    if every not in EVERY:
        raise InvalidSchedule(f"every must be one of {', '.join(EVERY)}")
    at = raw.get("at")
    if not isinstance(at, str) or not _AT.match(at):
        raise InvalidSchedule("at must be a time HH:MM, 00:00 to 23:59")
    timezone = raw.get("timezone") or "UTC"
    if not isinstance(timezone, str):
        raise InvalidSchedule("timezone must be an IANA name such as Europe/Madrid")
    try:
        ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise InvalidSchedule(f"unknown timezone {timezone!r}") from exc
    out: dict[str, Any] = {"every": every, "at": at, "timezone": timezone}
    if every == "week":
        weekday = raw.get("weekday")
        if (
            not isinstance(weekday, int)
            or isinstance(weekday, bool)
            or not 0 <= weekday <= 6
        ):
            raise InvalidSchedule("weekday must be 0 (Monday) to 6 (Sunday)")
        out["weekday"] = weekday
    elif "weekday" in raw:
        raise InvalidSchedule("weekday applies only to every: week")
    return out


def next_after(schedule: dict[str, Any], moment: datetime) -> datetime:
    """The first occurrence of *schedule* strictly after *moment*, in UTC
    (naive *moment* is read as UTC)."""
    zone = ZoneInfo(schedule.get("timezone") or "UTC")
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    local = moment.astimezone(zone)
    hour, minute = (int(part) for part in schedule["at"].split(":"))
    day = local.date()
    for offset in range(9):
        candidate_day = day + timedelta(days=offset)
        if (
            schedule["every"] == "week"
            and candidate_day.weekday() != schedule["weekday"]
        ):
            continue
        candidate = datetime(
            candidate_day.year,
            candidate_day.month,
            candidate_day.day,
            hour,
            minute,
            tzinfo=zone,
        )
        if candidate > local:
            return candidate.astimezone(UTC)
    raise AssertionError("a daily or weekly schedule always occurs within 8 days")
