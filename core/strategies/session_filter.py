"""
Session filter – dead-zone logic shared by all strategies.

Determines whether a given UTC timestamp falls inside a "dead zone"
where new entries (BUY / scale-in) should be blocked.

Two dead-zone formats are supported (matching ``strategies.yaml``):

1. **Named-day window** – e.g. ``Saturday 21:00`` → ``Sunday 20:00``
   Matches a specific weekday-and-time range that may span midnight.

2. **Time-only window** – e.g. ``21:00`` → ``01:00``
   Applies every weeknight (Monday 21:00 → Tuesday 01:00 through
   Friday 21:00 → Saturday 01:00).  Weekend nights are covered by
   the named-day window instead.
"""

from datetime import datetime, time
from typing import Any, Dict, List

# Weekday constants (datetime.weekday(): 0=Mon … 6=Sun)
_MONDAY = 0
_FRIDAY = 4
_SATURDAY = 5
_SUNDAY = 6

_DAY_NAMES = {
    "monday": _MONDAY,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": _FRIDAY,
    "saturday": _SATURDAY,
    "sunday": _SUNDAY,
}


# ── Public API ───────────────────────────────────────────────────────────────


def is_in_dead_zone(
    dt: datetime,
    dead_zones: List[Dict[str, Any]],
) -> bool:
    """
    Return ``True`` when *dt* (UTC) falls inside any configured dead zone.

    Args:
        dt: The candle-open timestamp in UTC.
        dead_zones: List of dead-zone dicts, each with ``start_utc``
                    and ``end_utc`` string keys (and optional ``name``).

    Returns:
        ``True`` if entries should be blocked, ``False`` otherwise.
    """
    for zone in dead_zones:
        start_str: str = zone.get("start_utc", "")
        end_str: str = zone.get("end_utc", "")
        if not start_str or not end_str:
            continue

        start_day, start_time = _parse_time_spec(start_str)
        end_day, end_time = _parse_time_spec(end_str)

        if start_day is not None:
            # Named-day window (e.g. Saturday 21:00 → Sunday 20:00)
            if _in_named_day_window(dt, start_day, start_time, end_day, end_time):
                return True
        else:
            # Time-only window – applies weeknights (Mon–Fri)
            if _in_weeknight_window(dt, start_time, end_time):
                return True

    return False


# ── Private helpers ──────────────────────────────────────────────────────────


def _parse_time_spec(spec: str) -> tuple:
    """
    Parse ``"Saturday 21:00"`` → ``(5, time(21, 0))`` or
    ``"21:00"`` → ``(None, time(21, 0))``.

    Returns:
        ``(weekday_int | None, time)``
    """
    parts = spec.strip().split()
    if len(parts) == 2:
        day_name, time_str = parts
        weekday = _DAY_NAMES.get(day_name.lower())
        return weekday, _parse_hm(time_str)
    elif len(parts) == 1:
        return None, _parse_hm(parts[0])
    else:
        raise ValueError(f"Cannot parse time spec: {spec!r}")


def _parse_hm(hm: str) -> time:
    """Parse ``"21:00"`` into ``time(21, 0)``."""
    h, m = hm.split(":")
    return time(int(h), int(m))


def _in_named_day_window(
    dt: datetime,
    start_day: int,
    start_time: time,
    end_day: int | None,
    end_time: time,
) -> bool:
    """
    Check whether *dt* falls within a named-day window.

    Handles windows that span midnight and/or cross day boundaries.
    ``end_day`` of ``None`` means the end is on the same day (or next
    if end_time < start_time).
    """
    dow = dt.weekday()
    t = dt.time()

    if end_day is None:
        end_day = start_day

    if (start_day, start_time) <= (end_day, end_time):
        # Normal window (no wrap past Sunday)
        if start_day == end_day:
            # Same-day window
            return dow == start_day and start_time <= t < end_time
        else:
            # Multi-day: on start day after start_time, on end day before
            # end_time, or on any full day in between.
            if dow == start_day and t >= start_time:
                return True
            if dow == end_day and t < end_time:
                return True
            # Days strictly between start and end
            if start_day < dow < end_day:
                return True
    else:
        # Wraps around the week boundary (unlikely but handled)
        if dow == start_day and t >= start_time:
            return True
        if dow > start_day:
            return True
        if dow < end_day:
            return True
        if dow == end_day and t < end_time:
            return True

    return False


def _in_weeknight_window(dt: datetime, start_time: time, end_time: time) -> bool:
    """
    Check a time-only window that applies on weeknights.

    The window ``21:00 → 01:00`` means:
    Monday 21:00 → Tuesday 01:00, through Friday 21:00 → Saturday 01:00.

    Weekend days (Saturday ≥ 01:00 and all of Sunday) are NOT matched by
    this rule – they're covered by the named-day weekend zone.
    """
    dow = dt.weekday()
    t = dt.time()

    if start_time > end_time:
        # Crosses midnight: e.g. 21:00 → 01:00
        # Part A: weekday (Mon–Fri) at or after start_time
        if _MONDAY <= dow <= _FRIDAY and t >= start_time:
            return True
        # Part B: next day (Tue–Sat) before end_time
        # Tuesday(1) through Saturday(5)
        if 1 <= dow <= _SATURDAY and t < end_time:
            # Only if the *previous* day was a weekday
            prev_dow = (dow - 1) % 7
            if _MONDAY <= prev_dow <= _FRIDAY:
                return True
    else:
        # Same-day window (e.g. 21:00 → 23:00) – weekdays only
        if _MONDAY <= dow <= _FRIDAY and start_time <= t < end_time:
            return True

    return False
