"""When a routine runs (``services/contracts/routine_schedule.py``): a time on
the company's clock, the next occurrence strictly after a moment."""

from datetime import UTC, datetime

import pytest
from services.contracts import routine_schedule as rs

DAILY = {"every": "day", "at": "09:30", "timezone": "UTC"}


def test_the_next_daily_occurrence():
    assert rs.next_after(DAILY, datetime(2026, 11, 22, 8, 0, tzinfo=UTC)) == datetime(
        2026, 11, 22, 9, 30, tzinfo=UTC
    )
    # Strictly after: at the very minute, the next day.
    assert rs.next_after(DAILY, datetime(2026, 11, 22, 9, 30, tzinfo=UTC)) == datetime(
        2026, 11, 23, 9, 30, tzinfo=UTC
    )


def test_a_weekly_occurrence_on_its_weekday_in_its_timezone():
    monday_nine_madrid = {
        "every": "week",
        "at": "09:00",
        "timezone": "Europe/Madrid",
        "weekday": 0,
    }
    # Sunday 22 Nov 2026 -> Monday 23 Nov, 09:00 Madrid (UTC+1) = 08:00 UTC.
    assert rs.next_after(monday_nine_madrid, datetime(2026, 11, 22, 12, 0)) == datetime(
        2026, 11, 23, 8, 0, tzinfo=UTC
    )
    # Monday after 09:00 Madrid -> the next Monday.
    assert rs.next_after(
        monday_nine_madrid, datetime(2026, 11, 23, 8, 1, tzinfo=UTC)
    ) == datetime(2026, 11, 30, 8, 0, tzinfo=UTC)


def test_the_local_hour_holds_across_a_clock_change():
    daily_madrid = {"every": "day", "at": "09:00", "timezone": "Europe/Madrid"}
    summer = rs.next_after(daily_madrid, datetime(2027, 3, 27, 12, 0, tzinfo=UTC))
    assert summer == datetime(2027, 3, 28, 7, 0, tzinfo=UTC)  # UTC+2 from 28 March


@pytest.mark.parametrize(
    "raw",
    [
        None,
        {"every": "hour", "at": "09:00"},
        {"every": "day", "at": "9:00"},
        {"every": "day", "at": "24:00"},
        {"every": "day", "at": "09:00", "timezone": "Mars/Olympus"},
        {"every": "week", "at": "09:00"},
        {"every": "week", "at": "09:00", "weekday": 7},
        {"every": "week", "at": "09:00", "weekday": True},
        {"every": "day", "at": "09:00", "weekday": 1},
        {"every": "day", "at": "09:00", "cron": "* * * * *"},
    ],
)
def test_a_schedule_the_backend_refuses(raw):
    with pytest.raises(rs.InvalidSchedule):
        rs.validate(raw)


def test_a_valid_schedule_is_stored_with_its_timezone():
    assert rs.validate({"every": "day", "at": "07:05"}) == {
        "every": "day",
        "at": "07:05",
        "timezone": "UTC",
    }
