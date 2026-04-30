"""
Unit tests for KillSwitch (task 4.5).

Covers:
    - Global drawdown trigger
    - Daily loss trigger
    - Peak equity (high-water mark) tracking
    - Daily reset at midnight UTC
    - Latch behaviour (stays triggered once fired)
    - Manual reset
    - State serialisation / restoration
    - Event publishing on trigger
    - No false triggers below threshold
"""

from datetime import date, datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from core.risk.kill_switch import KillSwitch


# ── Helpers ──────────────────────────────────────────────────────────────────

T0 = datetime(2026, 4, 17, 10, 0, tzinfo=timezone.utc)
T1 = datetime(2026, 4, 17, 10, 15, tzinfo=timezone.utc)
T2 = datetime(2026, 4, 17, 10, 30, tzinfo=timezone.utc)
NEXT_DAY = datetime(2026, 4, 18, 0, 15, tzinfo=timezone.utc)


def _ks(
    max_drawdown: float = 0.15,
    max_daily_loss: float = 0.10,
) -> KillSwitch:
    return KillSwitch(
        max_drawdown_pct=max_drawdown,
        max_daily_loss_pct=max_daily_loss,
    )


# ── Initialisation ──────────────────────────────────────────────────────────


class TestInitialisation:
    """First call should set peak equity and not trigger."""

    def test_first_check_sets_peak(self):
        ks = _ks()
        result = ks.check(Decimal("10000"), T0)
        assert result is False
        assert ks.peak_equity == Decimal("10000")

    def test_not_triggered_initially(self):
        ks = _ks()
        assert ks.is_triggered is False
        assert ks.trigger_reason is None


# ── Global drawdown ─────────────────────────────────────────────────────────


@patch("core.risk.kill_switch.get_publisher")
class TestGlobalDrawdown:
    """Kill-switch should trigger when equity drops >= max_drawdown from peak."""

    def test_triggers_at_threshold(self, mock_pub):
        mock_pub.return_value = MagicMock()
        ks = _ks(max_drawdown=0.15)

        ks.check(Decimal("10000"), T0)  # peak = 10000
        result = ks.check(Decimal("8500"), T1)  # 15% drop

        assert result is True
        assert ks.is_triggered is True
        assert "Global drawdown" in ks.trigger_reason
        assert "15" in ks.trigger_reason

    def test_triggers_beyond_threshold(self, mock_pub):
        mock_pub.return_value = MagicMock()
        ks = _ks(max_drawdown=0.15)

        ks.check(Decimal("10000"), T0)
        result = ks.check(Decimal("8000"), T1)  # 20% drop

        assert result is True
        assert ks.is_triggered is True

    def test_no_trigger_below_threshold(self, mock_pub):
        mock_pub.return_value = MagicMock()
        # Set daily loss high so only drawdown is tested
        ks = _ks(max_drawdown=0.15, max_daily_loss=0.50)

        ks.check(Decimal("10000"), T0)
        result = ks.check(Decimal("8600"), T1)  # 14% drop < 15% threshold

        assert result is False
        assert ks.is_triggered is False

    def test_peak_tracks_high_water_mark(self, mock_pub):
        mock_pub.return_value = MagicMock()
        ks = _ks(max_drawdown=0.15)

        ks.check(Decimal("10000"), T0)
        ks.check(Decimal("12000"), T1)  # new peak
        assert ks.peak_equity == Decimal("12000")

        # 15% of 12000 = 1800 → trigger at 10200
        result = ks.check(Decimal("10200"), T2)
        assert result is True

    def test_peak_never_decreases(self, mock_pub):
        mock_pub.return_value = MagicMock()
        ks = _ks(max_drawdown=0.50)  # high threshold so we don't trigger

        ks.check(Decimal("10000"), T0)
        ks.check(Decimal("12000"), T1)
        ks.check(Decimal("11000"), T2)  # dropped but peak stays
        assert ks.peak_equity == Decimal("12000")


# ── Daily loss ──────────────────────────────────────────────────────────────


@patch("core.risk.kill_switch.get_publisher")
class TestDailyLoss:
    """Kill-switch should trigger when daily loss exceeds threshold."""

    def test_triggers_at_daily_threshold(self, mock_pub):
        mock_pub.return_value = MagicMock()
        ks = _ks(max_drawdown=0.50, max_daily_loss=0.10)

        ks.check(Decimal("10000"), T0)
        result = ks.check(Decimal("9000"), T1)  # 10% daily loss

        assert result is True
        assert "Daily loss" in ks.trigger_reason

    def test_no_trigger_below_daily_threshold(self, mock_pub):
        mock_pub.return_value = MagicMock()
        ks = _ks(max_drawdown=0.50, max_daily_loss=0.10)

        ks.check(Decimal("10000"), T0)
        result = ks.check(Decimal("9100"), T1)  # 9% daily loss

        assert result is False

    def test_daily_loss_resets_on_new_day(self, mock_pub):
        mock_pub.return_value = MagicMock()
        ks = _ks(max_drawdown=0.50, max_daily_loss=0.10)

        ks.check(Decimal("10000"), T0)
        ks.check(Decimal("9200"), T1)  # 8% loss, not triggered

        # Next day — day_start_equity resets to current equity
        result = ks.check(Decimal("9200"), NEXT_DAY)
        assert result is False

        # 10% of 9200 = 920 → trigger at 8280
        result = ks.check(Decimal("8280"), NEXT_DAY)
        assert result is True


# ── Latch behaviour ─────────────────────────────────────────────────────────


@patch("core.risk.kill_switch.get_publisher")
class TestLatch:
    """Once triggered, kill-switch stays latched regardless of equity recovery."""

    def test_stays_triggered_after_equity_recovery(self, mock_pub):
        mock_pub.return_value = MagicMock()
        ks = _ks(max_drawdown=0.15)

        ks.check(Decimal("10000"), T0)
        ks.check(Decimal("8500"), T1)  # trigger
        assert ks.is_triggered is True

        # Equity recovers — still triggered
        result = ks.check(Decimal("11000"), T2)
        assert result is True
        assert ks.is_triggered is True

    def test_trigger_time_recorded(self, mock_pub):
        mock_pub.return_value = MagicMock()
        ks = _ks(max_drawdown=0.15)

        ks.check(Decimal("10000"), T0)
        ks.check(Decimal("8500"), T1)

        assert ks.trigger_time == T1


# ── Reset ────────────────────────────────────────────────────────────────────


@patch("core.risk.kill_switch.get_publisher")
class TestReset:
    """Manual reset should re-enable trading but keep peak equity."""

    def test_reset_clears_trigger(self, mock_pub):
        mock_pub.return_value = MagicMock()
        ks = _ks(max_drawdown=0.15)

        ks.check(Decimal("10000"), T0)
        ks.check(Decimal("8500"), T1)
        assert ks.is_triggered is True

        ks.reset()
        assert ks.is_triggered is False
        assert ks.trigger_reason is None
        assert ks.trigger_time is None

    def test_reset_preserves_peak_equity(self, mock_pub):
        mock_pub.return_value = MagicMock()
        ks = _ks(max_drawdown=0.15)

        ks.check(Decimal("10000"), T0)
        ks.check(Decimal("8500"), T1)

        ks.reset()
        assert ks.peak_equity == Decimal("10000")

    def test_can_retrigger_after_reset(self, mock_pub):
        mock_pub.return_value = MagicMock()
        ks = _ks(max_drawdown=0.15)

        ks.check(Decimal("10000"), T0)
        ks.check(Decimal("8500"), T1)
        ks.reset()

        # Drops again from peak (still 10000)
        result = ks.check(Decimal("8400"), T2)
        assert result is True


# ── State persistence ────────────────────────────────────────────────────────


@patch("core.risk.kill_switch.get_publisher")
class TestStatePersistence:
    """Serialisation and restoration of kill-switch state."""

    def test_to_dict_captures_state(self, mock_pub):
        mock_pub.return_value = MagicMock()
        ks = _ks(max_drawdown=0.15)

        ks.check(Decimal("10000"), T0)
        ks.check(Decimal("8500"), T1)  # trigger

        data = ks.to_dict()
        assert data["kill_switch_triggered"] is True
        assert data["kill_switch_reason"] is not None
        assert data["kill_switch_peak_equity"] == "10000"

    def test_restore_from_dict_recovers_triggered(self, mock_pub):
        mock_pub.return_value = MagicMock()
        ks = _ks(max_drawdown=0.15)

        ks.check(Decimal("10000"), T0)
        ks.check(Decimal("8500"), T1)

        data = ks.to_dict()

        # New instance, restore state
        ks2 = _ks(max_drawdown=0.15)
        ks2.restore_from_dict(data)

        assert ks2.is_triggered is True
        assert ks2.trigger_reason == ks.trigger_reason
        assert ks2.peak_equity == Decimal("10000")

    def test_restore_non_triggered_state(self, mock_pub):
        mock_pub.return_value = MagicMock()
        ks = _ks()

        ks.check(Decimal("10000"), T0)
        data = ks.to_dict()

        ks2 = _ks()
        ks2.restore_from_dict(data)

        assert ks2.is_triggered is False
        assert ks2.peak_equity == Decimal("10000")

    def test_restore_empty_dict_is_safe(self, mock_pub):
        mock_pub.return_value = MagicMock()
        ks = _ks()
        ks.restore_from_dict({})
        assert ks.is_triggered is False


# ── Event publishing ─────────────────────────────────────────────────────────


@patch("core.risk.kill_switch.get_publisher")
class TestEventPublishing:
    """Kill-switch trigger should publish an event."""

    def test_publishes_event_on_trigger(self, mock_pub):
        mock_publisher = MagicMock()
        mock_pub.return_value = mock_publisher
        ks = _ks(max_drawdown=0.15)

        ks.check(Decimal("10000"), T0)
        ks.check(Decimal("8500"), T1)

        mock_publisher.publish.assert_called_once()
        call_args = mock_publisher.publish.call_args
        assert call_args[0][1].value == "kill_switch_triggered"

    def test_does_not_publish_below_threshold(self, mock_pub):
        mock_publisher = MagicMock()
        mock_pub.return_value = mock_publisher
        # Set daily loss high so only drawdown is tested
        ks = _ks(max_drawdown=0.15, max_daily_loss=0.50)

        ks.check(Decimal("10000"), T0)
        ks.check(Decimal("9000"), T1)  # 10% < 15%

        mock_publisher.publish.assert_not_called()

    def test_publishes_only_once(self, mock_pub):
        mock_publisher = MagicMock()
        mock_pub.return_value = mock_publisher
        ks = _ks(max_drawdown=0.15)

        ks.check(Decimal("10000"), T0)
        ks.check(Decimal("8500"), T1)  # trigger
        ks.check(Decimal("8000"), T2)  # still triggered, no new event

        assert mock_publisher.publish.call_count == 1


# ── Hard reset & mode-change tests ───────────────────────────────────────────


@patch("core.risk.kill_switch.get_publisher")
class TestHardReset:

    def test_hard_reset_clears_peak_equity(self, mock_pub):
        mock_pub.return_value = MagicMock()
        ks = _ks(max_drawdown=0.15)
        ks.check(Decimal("10000"), T0)
        ks.check(Decimal("8000"), T1)  # trigger
        assert ks.is_triggered
        assert ks.peak_equity == Decimal("10000")

        ks.hard_reset()
        assert not ks.is_triggered
        assert ks.peak_equity == Decimal("0")

    def test_hard_reset_reinitialises_on_next_check(self, mock_pub):
        mock_pub.return_value = MagicMock()
        ks = _ks(max_drawdown=0.15)
        ks.check(Decimal("10000"), T0)
        ks.check(Decimal("8000"), T1)

        ks.hard_reset()
        # Next check should initialise peak to 100 (the new current equity)
        result = ks.check(Decimal("100"), T2)
        assert result is False
        assert ks.peak_equity == Decimal("100")


@patch("core.risk.kill_switch.get_publisher")
class TestModeSwitchRestore:

    def test_restore_skips_when_mode_changes(self, mock_pub):
        mock_pub.return_value = MagicMock()
        ks = _ks()
        ks.check(Decimal("5000"), T0)
        ks.check(Decimal("4000"), T1)  # trigger

        data = ks.to_dict(trading_mode="paper")
        assert data["kill_switch_trading_mode"] == "paper"

        ks2 = _ks()
        ks2.restore_from_dict(data, current_trading_mode="live")
        # Should NOT have restored the triggered state or peak
        assert not ks2.is_triggered
        assert ks2.peak_equity == Decimal("0")

    def test_restore_works_when_mode_same(self, mock_pub):
        mock_pub.return_value = MagicMock()
        ks = _ks()
        ks.check(Decimal("5000"), T0)
        ks.check(Decimal("4000"), T1)

        data = ks.to_dict(trading_mode="live")

        ks2 = _ks()
        ks2.restore_from_dict(data, current_trading_mode="live")
        assert ks2.is_triggered
        assert ks2.peak_equity == Decimal("5000")

    def test_restore_backward_compat_missing_mode(self, mock_pub):
        mock_pub.return_value = MagicMock()
        ks = _ks()
        ks.check(Decimal("5000"), T0)
        ks.check(Decimal("4000"), T1)

        data = ks.to_dict()  # no trading_mode
        assert "kill_switch_trading_mode" not in data

        ks2 = _ks()
        ks2.restore_from_dict(data, current_trading_mode="live")
        # Without saved mode, falls through to normal restore
        assert ks2.is_triggered
        assert ks2.peak_equity == Decimal("5000")

    def test_to_dict_includes_trading_mode(self, mock_pub):
        mock_pub.return_value = MagicMock()
        ks = _ks()
        d = ks.to_dict(trading_mode="live")
        assert d["kill_switch_trading_mode"] == "live"

    def test_to_dict_omits_trading_mode_when_none(self, mock_pub):
        mock_pub.return_value = MagicMock()
        ks = _ks()
        d = ks.to_dict()
        assert "kill_switch_trading_mode" not in d
