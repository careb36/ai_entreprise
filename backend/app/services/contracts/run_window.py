"""A company's run window: the hours in which work that can wait may start
(issue #736). Plain data and pure functions; the clock and the holiday
calendars are the caller's.

A window is ``None`` (off: work starts at once, as before) or::

    {
        "timezone": "America/Montevideo",       # an IANA tz database name
        "preset": "provider_off_peak:deepseek",  # or None
        "intervals": [                           # when there is no preset
            {"days": ["mon", "tue"], "start": "22:00", "end": "06:00"},
        ],
        "holidays": {"calendar": "cn", "dates": ["2026-12-31"]},
    }

A preset is the complement of a provider's published peak hours, which are
given in the provider's own zone (``PRESETS``, with the page and the date it
was read); its own holiday calendar always applies. Custom intervals are in
the window's ``timezone``; an interval whose end is not after its start runs
over midnight into the next day, and belongs to the day it starts on. On a
holiday date (in the preset's zone, else the window's) the window is open all
day: the provider has no peak that day, or the company said so.

Every instant is an aware ``datetime``; zones are ``zoneinfo`` names, never
fixed offsets, so a zone's daylight-saving rules apply. Times out are
ISO 8601 with their offset.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
# How far ahead the next opening is searched; a valid window opens within it.
HORIZON_DAYS = 15
PREVIEW_DAYS = 7
MAX_INTERVALS = 28
MAX_DATES = 400
_TIME = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$|^24:00$")

DEEPSEEK = "provider_off_peak:deepseek"
PRESETS: dict[str, dict[str, Any]] = {
    DEEPSEEK: {
        "provider": "deepseek",
        # Peak hours as the provider publishes them: in UTC, Monday to Friday,
        # except Chinese public holidays; every other hour is off-peak, at
        # half the peak price.
        "zone": "UTC",
        "weekdays": ["mon", "tue", "wed", "thu", "fri"],
        "peak": [["01:00", "04:00"], ["06:00", "10:00"]],
        "holiday_calendar": "cn",
        "off_peak_price": "50% of the peak price",
        "source": "https://api-docs.deepseek.com/quick_start/pricing",
        "read_on": "2026-11-19",
    },
}


class InvalidWindow(ValueError):
    """A window that cannot be stored; the message says why."""


# --- reading and checking a window -------------------------------------------------


def _zone(name: Any) -> ZoneInfo:
    if not isinstance(name, str) or not name.strip():
        raise InvalidWindow(
            "timezone is an IANA time zone name, such as America/Montevideo"
        )
    try:
        return ZoneInfo(name.strip())
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise InvalidWindow(f"unknown time zone: {name!r}") from exc


def _minutes(value: Any, *, end: bool) -> int:
    if not isinstance(value, str) or not _TIME.match(value):
        raise InvalidWindow(f"a time is HH:MM, 00:00 to 24:00: {value!r}")
    if value == "24:00" and not end:
        raise InvalidWindow("an interval cannot start at 24:00")
    hours, minutes = value.split(":")
    return int(hours) * 60 + int(minutes)


def _interval(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise InvalidWindow("an interval is {days, start, end}")
    days = raw.get("days") or []
    if not isinstance(days, list) or not days:
        raise InvalidWindow("an interval names at least one day (mon … sun)")
    clean = []
    for day in days:
        name = str(day).strip().lower()[:3]
        if name not in DAYS:
            raise InvalidWindow(f"not a day: {day!r}")
        if name not in clean:
            clean.append(name)
    start = _minutes(raw.get("start"), end=False)
    finish = _minutes(raw.get("end"), end=True)
    if start == finish:
        raise InvalidWindow(
            "an interval's start and end differ (00:00 to 24:00 is a whole day)"
        )
    return {
        "days": [d for d in DAYS if d in clean],
        "start": raw["start"],
        "end": raw["end"],
    }


def validate(raw: Any) -> dict[str, Any] | None:
    """The window in its stored shape, or ``None`` (off); ``InvalidWindow`` else."""
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise InvalidWindow("the run window is an object, or null to turn it off")
    zone = _zone(raw.get("timezone"))
    preset = raw.get("preset") or None
    if preset is not None and preset not in PRESETS:
        raise InvalidWindow(f"preset is one of {', '.join(PRESETS)}")
    intervals_raw = raw.get("intervals") or []
    if not isinstance(intervals_raw, list) or len(intervals_raw) > MAX_INTERVALS:
        raise InvalidWindow(f"intervals is a list of at most {MAX_INTERVALS}")
    intervals = [_interval(i) for i in intervals_raw]
    if (preset is None) == (not intervals):
        raise InvalidWindow(
            "a run window has a preset or intervals, not both and not neither"
        )
    holidays_raw = raw.get("holidays") or {}
    if not isinstance(holidays_raw, dict):
        raise InvalidWindow("holidays is {calendar, dates}")
    calendar = holidays_raw.get("calendar") or None
    if calendar is not None and (not isinstance(calendar, str) or len(calendar) > 40):
        raise InvalidWindow("holidays.calendar is a calendar's name")
    dates_raw = holidays_raw.get("dates") or []
    if not isinstance(dates_raw, list) or len(dates_raw) > MAX_DATES:
        raise InvalidWindow(f"holidays.dates is a list of at most {MAX_DATES} dates")
    dates = []
    for value in dates_raw:
        try:
            dates.append(date.fromisoformat(str(value)).isoformat())
        except ValueError as exc:
            raise InvalidWindow(f"not an ISO 8601 date: {value!r}") from exc
    # Every interval holds some time of some day, and a preset is open on
    # weekends, so a valid window always opens within a week.
    return {
        "timezone": zone.key,
        "preset": preset,
        "intervals": intervals,
        "holidays": {"calendar": calendar, "dates": sorted(set(dates))},
    }


def parse(raw: Any) -> dict[str, Any] | None:
    """A stored window, or ``None`` when it is off or cannot be read."""
    try:
        return validate(raw)
    except InvalidWindow:
        return None


def calendars_of(window: dict[str, Any]) -> list[str]:
    """The holiday calendars a window uses: its preset's, then its own."""
    names = []
    preset = PRESETS.get(window.get("preset") or "")
    if preset and preset.get("holiday_calendar"):
        names.append(preset["holiday_calendar"])
    own = (window.get("holidays") or {}).get("calendar")
    if own and own not in names:
        names.append(own)
    return names


def own_dates(window: dict[str, Any]) -> frozenset[date]:
    return frozenset(
        date.fromisoformat(d) for d in (window.get("holidays") or {}).get("dates", [])
    )


def describe(window: dict[str, Any]) -> dict[str, Any]:
    """What a task event carries of the window."""
    return {
        "timezone": window["timezone"],
        "preset": window.get("preset"),
        "intervals": window.get("intervals") or [],
    }


# --- open or closed ----------------------------------------------------------------


def _utc(at: datetime) -> datetime:
    if at.tzinfo is None:
        return at.replace(tzinfo=UTC)
    return at.astimezone(UTC)


def _reference_zone(window: dict[str, Any]) -> ZoneInfo:
    preset = PRESETS.get(window.get("preset") or "")
    return ZoneInfo(preset["zone"] if preset else window["timezone"])


def _in_interval(interval: dict[str, Any], local: datetime) -> bool:
    start = _minutes(interval["start"], end=False)
    end = _minutes(interval["end"], end=True)
    now = local.hour * 60 + local.minute
    today = DAYS[local.weekday()]
    yesterday = DAYS[(local.weekday() - 1) % 7]
    if end > start:
        return today in interval["days"] and start <= now < end
    return (today in interval["days"] and now >= start) or (
        yesterday in interval["days"] and now < end
    )


def is_open(window: dict[str, Any], at: datetime, holidays: frozenset[date]) -> bool:
    """Whether work that can wait may start at *at*."""
    local = _utc(at).astimezone(_reference_zone(window))
    if local.date() in holidays:
        return True
    preset = PRESETS.get(window.get("preset") or "")
    if preset:
        if DAYS[local.weekday()] not in preset["weekdays"]:
            return True
        now = local.hour * 60 + local.minute
        return not any(
            _minutes(s, end=False) <= now < _minutes(e, end=True)
            for s, e in preset["peak"]
        )
    return any(_in_interval(i, local) for i in window.get("intervals") or [])


def _boundary_times(window: dict[str, Any]) -> list[str]:
    preset = PRESETS.get(window.get("preset") or "")
    if preset:
        times = [t for pair in preset["peak"] for t in pair]
    else:
        times = [i[k] for i in window.get("intervals") or [] for k in ("start", "end")]
    return sorted({"00:00", *times})


def _candidates(window: dict[str, Any], after: datetime, days: int):
    """Instants where the window may open or close, in order, after *after*."""
    zone = _reference_zone(window)
    first = after.astimezone(zone).date() - timedelta(days=1)
    times = _boundary_times(window)
    seen = []
    for offset in range(days + 2):
        day = first + timedelta(days=offset)
        for value in times:
            hours, minutes = (int(p) for p in value.split(":"))
            local_day = day + timedelta(days=1) if hours == 24 else day
            moment = datetime.combine(
                local_day, time(0 if hours == 24 else hours, minutes), tzinfo=zone
            ).astimezone(UTC)
            if moment > after:
                seen.append(moment)
    return sorted(set(seen))


def next_change(
    window: dict[str, Any],
    at: datetime,
    holidays: frozenset[date],
    days: int = HORIZON_DAYS,
) -> datetime | None:
    """The first instant after *at* where the window opens or closes."""
    at = _utc(at)
    state = is_open(window, at, holidays)
    for moment in _candidates(window, at, days):
        if is_open(window, moment, holidays) != state:
            return moment
    return None


def next_open(
    window: dict[str, Any], at: datetime, holidays: frozenset[date]
) -> datetime | None:
    """*at* when the window is open, else when it next opens (``None``: not within the horizon)."""
    at = _utc(at)
    if is_open(window, at, holidays):
        return at
    return next_change(window, at, holidays)


def spans(
    window: dict[str, Any],
    start: datetime,
    holidays: frozenset[date],
    days: int = PREVIEW_DAYS,
) -> list[tuple[datetime, datetime]]:
    """The open spans from *start* for *days*, clipped to that range, in UTC."""
    start = _utc(start)
    end = start + timedelta(days=days)
    out: list[tuple[datetime, datetime]] = []
    cursor = start
    for _ in range(days * 2 * 24 + 4):
        if cursor >= end:
            break
        change = next_change(window, cursor, holidays, days + 1) or end
        if is_open(window, cursor, holidays):
            out.append((cursor, min(change, end)))
        cursor = change
    return out


def local_iso(window: dict[str, Any], at: datetime) -> str:
    """*at* in the window's own zone, ISO 8601 with its offset."""
    return _utc(at).astimezone(ZoneInfo(window["timezone"])).isoformat()
