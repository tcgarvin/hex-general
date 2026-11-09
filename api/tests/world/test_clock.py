"""Turn clock times: 01:00 and 13:00 New York, across daylight saving changes."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.world.clock import TICK_ZONE, next_tick_after

UTC = timezone.utc


def ny(
    year: int, month: int, day: int, hour: int, minute: int = 0, second: int = 0
) -> datetime:
    return datetime(year, month, day, hour, minute, second, tzinfo=TICK_ZONE)


@pytest.mark.parametrize(
    ("now", "expected"),
    [
        (ny(2026, 11, 10, 0, 30), ny(2026, 11, 10, 1)),
        (ny(2026, 11, 10, 1), ny(2026, 11, 10, 13)),  # strictly after
        (ny(2026, 11, 10, 12, 59), ny(2026, 11, 10, 13)),
        (ny(2026, 11, 10, 13, 0, 1), ny(2026, 11, 11, 1)),
        (ny(2026, 12, 31, 23), ny(2027, 1, 1, 1)),
    ],
)
def test_next_tick_is_the_next_01_or_13_new_york(
    now: datetime, expected: datetime
) -> None:
    assert next_tick_after(now) == expected
    assert next_tick_after(now).tzinfo is UTC


def test_summer_and_winter_ticks_shift_in_utc() -> None:
    assert next_tick_after(ny(2026, 7, 1, 2)) == datetime(2026, 7, 1, 17, tzinfo=UTC)
    assert next_tick_after(ny(2026, 1, 15, 2)) == datetime(2026, 1, 15, 18, tzinfo=UTC)


def test_autumn_changeover_uses_the_first_01_00() -> None:
    # 2026-11-01: clocks go back at 02:00 EDT, so 01:00 happens twice.
    tick = next_tick_after(datetime(2026, 11, 1, 4, tzinfo=UTC))  # 00:00 EDT
    assert tick == datetime(2026, 11, 1, 5, tzinfo=UTC)  # 01:00 EDT


def test_spring_changeover_keeps_both_ticks() -> None:
    # 2026-03-08: clocks jump 02:00 → 03:00; 01:00 and 13:00 still happen.
    before = datetime(2026, 3, 8, 5, tzinfo=UTC)  # 00:00 EST
    first = next_tick_after(before)
    assert first == datetime(2026, 3, 8, 6, tzinfo=UTC)  # 01:00 EST
    assert next_tick_after(first) == datetime(2026, 3, 8, 17, tzinfo=UTC)  # 13:00 EDT
    assert next_tick_after(first) - first == timedelta(hours=11)


def test_a_naive_time_is_refused() -> None:
    with pytest.raises(ValueError, match="aware"):
        next_tick_after(datetime(2026, 11, 10, 12))
