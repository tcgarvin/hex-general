"""The world turn clock (WORLD_SPEC §2): turns resolve at 01:00 and 13:00 New York.

A scheduled world stores the instant of its next resolution in
``games.next_tick_at``. `next_tick_after` computes it; the scheduler endpoint
resolves every world whose instant has passed and moves it on in the same
compare-and-set write as the new state, so a repeated or late scheduler call can
never resolve a turn twice.

The hours are wall-clock times in ``America/New_York``, so they follow daylight
saving time. 01:00 is ambiguous on the autumn changeover day (it happens twice);
the first, daylight-time one is used (``fold=0``). It is never skipped, because the
spring change jumps from 02:00 to 03:00.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

TICK_ZONE = ZoneInfo("America/New_York")
TICK_HOURS: tuple[int, ...] = (1, 13)


def next_tick_after(
    now: datetime, hours: tuple[int, ...] = TICK_HOURS, zone: ZoneInfo = TICK_ZONE
) -> datetime:
    """The first scheduled tick strictly after ``now``, as an aware UTC datetime.

    Raises `ValueError` for a naive ``now``: a clock that silently assumed a
    timezone would drift by hours.
    """
    if now.tzinfo is None:
        raise ValueError("next_tick_after needs an aware datetime")
    local = now.astimezone(zone)
    for day in range(3):
        date = local.date() + timedelta(days=day)
        for hour in sorted(hours):
            candidate = datetime.combine(date, time(hour), tzinfo=zone)
            if candidate > now:
                return candidate.astimezone(timezone.utc)
    raise ValueError(f"no tick within three days of {now} for hours {hours}")
