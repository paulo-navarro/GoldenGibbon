"""
Unit tests for the session filter (dead-zone logic) – task 1.13.

Tests the shared ``is_in_dead_zone()`` utility that both Smart Hodler
and Mean Reversion use to block entries during low-liquidity windows.

Dead zones from the spec:
    Weekend:     Saturday 21:00 – Sunday 20:00 UTC
    Overnight:   Weekdays 21:00 – next day 01:00 UTC (Mon–Fri nights)
"""

from datetime import datetime

import pytest

from core.strategies.session_filter import is_in_dead_zone

# ── Shared dead-zone config (mirrors smart_hodler defaults) ──────────────────

DEAD_ZONES = [
    {"name": "Weekend", "start_utc": "Saturday 21:00", "end_utc": "Sunday 20:00"},
    {"name": "Overnight Gap", "start_utc": "21:00", "end_utc": "01:00"},
]


# ── Weekend dead zone ───────────────────────────────────────────────────────


class TestWeekendDeadZone:
    """Saturday 21:00 → Sunday 20:00 UTC."""

    def test_saturday_21_00_in_zone(self):
        # Saturday 21:00 → inside
        dt = datetime(2026, 2, 21, 21, 0)  # Saturday
        assert dt.weekday() == 5
        assert is_in_dead_zone(dt, DEAD_ZONES) is True

    def test_saturday_21_30_in_zone(self):
        dt = datetime(2026, 2, 21, 21, 30)
        assert is_in_dead_zone(dt, DEAD_ZONES) is True

    def test_saturday_23_59_in_zone(self):
        dt = datetime(2026, 2, 21, 23, 59)
        assert is_in_dead_zone(dt, DEAD_ZONES) is True

    def test_sunday_00_00_in_zone(self):
        dt = datetime(2026, 2, 22, 0, 0)  # Sunday
        assert dt.weekday() == 6
        assert is_in_dead_zone(dt, DEAD_ZONES) is True

    def test_sunday_12_00_in_zone(self):
        dt = datetime(2026, 2, 22, 12, 0)
        assert is_in_dead_zone(dt, DEAD_ZONES) is True

    def test_sunday_19_59_in_zone(self):
        dt = datetime(2026, 2, 22, 19, 59)
        assert is_in_dead_zone(dt, DEAD_ZONES) is True

    def test_sunday_20_00_out_of_zone(self):
        # Sunday 20:00 → outside (boundary: end is exclusive)
        dt = datetime(2026, 2, 22, 20, 0)
        assert is_in_dead_zone(dt, DEAD_ZONES) is False

    def test_sunday_20_30_out_of_zone(self):
        dt = datetime(2026, 2, 22, 20, 30)
        assert is_in_dead_zone(dt, DEAD_ZONES) is False

    def test_saturday_20_59_out_of_zone(self):
        # Saturday 20:59 → outside (before weekend zone starts)
        dt = datetime(2026, 2, 21, 20, 59)
        assert is_in_dead_zone(dt, DEAD_ZONES) is False

    def test_saturday_morning_out_of_zone(self):
        # Saturday 10:00 → outside (before 21:00)
        dt = datetime(2026, 2, 21, 10, 0)
        assert is_in_dead_zone(dt, DEAD_ZONES) is False


# ── Overnight dead zone ─────────────────────────────────────────────────────


class TestOvernightDeadZone:
    """Weekdays 21:00 → next day 01:00 UTC (Mon–Fri nights)."""

    def test_monday_21_00_in_zone(self):
        dt = datetime(2026, 2, 16, 21, 0)  # Monday
        assert dt.weekday() == 0
        assert is_in_dead_zone(dt, DEAD_ZONES) is True

    def test_monday_22_30_in_zone(self):
        dt = datetime(2026, 2, 16, 22, 30)
        assert is_in_dead_zone(dt, DEAD_ZONES) is True

    def test_tuesday_00_30_in_zone(self):
        # Tuesday 00:30 → still inside (Mon night overflow)
        dt = datetime(2026, 2, 17, 0, 30)  # Tuesday
        assert dt.weekday() == 1
        assert is_in_dead_zone(dt, DEAD_ZONES) is True

    def test_tuesday_00_59_in_zone(self):
        dt = datetime(2026, 2, 17, 0, 59)
        assert is_in_dead_zone(dt, DEAD_ZONES) is True

    def test_tuesday_01_00_out_of_zone(self):
        # Tuesday 01:00 → outside (boundary: end exclusive)
        dt = datetime(2026, 2, 17, 1, 0)
        assert is_in_dead_zone(dt, DEAD_ZONES) is False

    def test_monday_20_59_out_of_zone(self):
        dt = datetime(2026, 2, 16, 20, 59)
        assert is_in_dead_zone(dt, DEAD_ZONES) is False

    def test_wednesday_14_00_out_of_zone(self):
        # Midday Wednesday → outside
        dt = datetime(2026, 2, 18, 14, 0)
        assert dt.weekday() == 2
        assert is_in_dead_zone(dt, DEAD_ZONES) is False

    def test_friday_21_00_in_zone(self):
        # Friday 21:00 → inside (weeknight)
        dt = datetime(2026, 2, 20, 21, 0)  # Friday
        assert dt.weekday() == 4
        assert is_in_dead_zone(dt, DEAD_ZONES) is True

    def test_saturday_00_30_in_zone(self):
        # Saturday 00:30 → inside (Friday night overflow)
        dt = datetime(2026, 2, 21, 0, 30)  # Saturday
        assert dt.weekday() == 5
        assert is_in_dead_zone(dt, DEAD_ZONES) is True

    def test_thursday_23_15_in_zone(self):
        dt = datetime(2026, 2, 19, 23, 15)  # Thursday
        assert dt.weekday() == 3
        assert is_in_dead_zone(dt, DEAD_ZONES) is True

    def test_friday_01_00_out_of_zone(self):
        # Friday 01:00 → outside (Thu night gap ended)
        dt = datetime(2026, 2, 20, 1, 0)
        assert is_in_dead_zone(dt, DEAD_ZONES) is False


# ── Edge cases ───────────────────────────────────────────────────────────────


class TestEdgeCases:
    def test_no_dead_zones_configured(self):
        """Empty list → always allow."""
        dt = datetime(2026, 2, 21, 22, 0)  # Saturday night
        assert is_in_dead_zone(dt, []) is False

    def test_missing_start_utc(self):
        """Malformed zone entry → skipped gracefully."""
        dt = datetime(2026, 2, 21, 22, 0)
        assert is_in_dead_zone(dt, [{"end_utc": "01:00"}]) is False

    def test_missing_end_utc(self):
        dt = datetime(2026, 2, 21, 22, 0)
        assert is_in_dead_zone(dt, [{"start_utc": "21:00"}]) is False

    def test_only_weekend_zone(self):
        """Only weekend configured, weeknight should be open."""
        weekend_only = [DEAD_ZONES[0]]
        dt = datetime(2026, 2, 16, 22, 0)  # Monday 22:00
        assert is_in_dead_zone(dt, weekend_only) is False

    def test_only_overnight_zone(self):
        """Only overnight configured, weekend should be open."""
        overnight_only = [DEAD_ZONES[1]]
        # Sunday afternoon → no overnight match (Sunday is not a weeknight)
        dt = datetime(2026, 2, 22, 14, 0)  # Sunday
        assert is_in_dead_zone(dt, overnight_only) is False

    def test_sunday_00_30_in_weekend_zone(self):
        """Sunday 00:30 is inside the weekend zone (Sat 21→Sun 20)."""
        dt = datetime(2026, 2, 22, 0, 30)
        assert is_in_dead_zone(dt, [DEAD_ZONES[0]]) is True

    def test_weekday_afternoon_always_open(self):
        """Typical weekday trading hours should never be in a dead zone."""
        for day_offset in range(5):  # Mon–Fri
            dt = datetime(2026, 2, 16 + day_offset, 14, 0)
            assert is_in_dead_zone(dt, DEAD_ZONES) is False, (
                f"Day offset {day_offset} (weekday={dt.weekday()}) "
                f"should be outside dead zones"
            )
