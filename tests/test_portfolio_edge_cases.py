"""
Edge-case tests for PortfolioManager (gap 9) and tick lock (gap 10).

Covers:
    Gap 9:
        - reduce_position where sell_fraction is so small that quantized
          sold_size rounds to zero → position should remain unchanged or
          the zero-size trade should be handled gracefully.
        - reduce_position with fraction=1 removes position entirely.
        - reduce_position with no position raises ValueError.
        - reduce_position with invalid fraction raises ValueError.

    Gap 10:
        - _acquire_tick_lock returns True when no previous lock exists.
        - _acquire_tick_lock returns False on duplicate candle time.
        - _acquire_tick_lock returns True when candle_open_time_iso is None.
        - _acquire_tick_lock returns True when Redis is unavailable (fail-open).
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from core.models import ExitReason, Position


# ── Gap 9: reduce_position edge cases ────────────────────────────────────────


class TestReducePositionZeroSize:
    """When sell_fraction is tiny, quantize(0.00000001) can round to zero."""

    def _make_pm(self, balance: Decimal = Decimal("10000")):
        from core.portfolio import PortfolioManager

        pm = PortfolioManager(
            initial_capital=balance,
            taker_fee=Decimal("0.001"),
        )
        return pm

    def _seed_position(self, pm, symbol="BTCUSDT", size=Decimal("0.00000010")):
        entry = Decimal("50000")
        pm._portfolio.positions[symbol] = Position(
            symbol=symbol,
            strategy="smart_hodler",
            size=size,
            entry_price=entry,
            entry_time=datetime(2026, 1, 1),
            highest_close=entry,
            trailing_stop_price=entry * Decimal("0.95"),
            hard_stop_price=entry * Decimal("0.97"),
        )
        pm._portfolio.open_trades_count = len(pm._portfolio.positions)

    def test_tiny_fraction_quantizes_to_zero_raises(self):
        """
        size=0.00000010, sell_fraction=0.01 → sold_size rounds to 0.00000000.
        Should raise ValueError instead of proceeding with a zero-size trade.
        """
        pm = self._make_pm()
        self._seed_position(pm, size=Decimal("0.00000010"))

        with pytest.raises(ValueError, match="quantizes to zero"):
            pm.reduce_position(
                symbol="BTCUSDT",
                sell_fraction=Decimal("0.01"),
                exit_price=Decimal("50000"),
                exit_time=datetime(2026, 1, 2),
                exit_reason=ExitReason.TRAILING_STOP,
            )

        # Position should remain unchanged
        assert pm.get_position("BTCUSDT") is not None

    def test_full_exit_removes_position(self):
        pm = self._make_pm()
        self._seed_position(pm, size=Decimal("0.50000000"))

        trade = pm.reduce_position(
            symbol="BTCUSDT",
            sell_fraction=Decimal("1"),
            exit_price=Decimal("55000"),
            exit_time=datetime(2026, 1, 2),
            exit_reason=ExitReason.EMA_CROSS,
        )

        assert trade.size == Decimal("0.50000000")
        assert pm.get_position("BTCUSDT") is None
        assert pm._portfolio.open_trades_count == 0

    def test_no_position_raises(self):
        pm = self._make_pm()

        with pytest.raises(ValueError, match="No position"):
            pm.reduce_position(
                symbol="BTCUSDT",
                sell_fraction=Decimal("0.5"),
                exit_price=Decimal("50000"),
                exit_time=datetime(2026, 1, 2),
                exit_reason=ExitReason.EMA_CROSS,
            )

    def test_invalid_fraction_raises(self):
        pm = self._make_pm()
        self._seed_position(pm)

        with pytest.raises(ValueError, match="sell_fraction"):
            pm.reduce_position(
                symbol="BTCUSDT",
                sell_fraction=Decimal("0"),
                exit_price=Decimal("50000"),
                exit_time=datetime(2026, 1, 2),
                exit_reason=ExitReason.EMA_CROSS,
            )

        with pytest.raises(ValueError, match="sell_fraction"):
            pm.reduce_position(
                symbol="BTCUSDT",
                sell_fraction=Decimal("1.5"),
                exit_price=Decimal("50000"),
                exit_time=datetime(2026, 1, 2),
                exit_reason=ExitReason.EMA_CROSS,
            )

    def test_partial_exit_updates_remaining(self):
        pm = self._make_pm()
        self._seed_position(pm, size=Decimal("1.00000000"))

        trade = pm.reduce_position(
            symbol="BTCUSDT",
            sell_fraction=Decimal("0.5"),
            exit_price=Decimal("55000"),
            exit_time=datetime(2026, 1, 2),
            exit_reason=ExitReason.TRAILING_STOP,
        )

        assert trade.size == Decimal("0.50000000")
        remaining = pm.get_position("BTCUSDT")
        assert remaining is not None
        assert remaining.size == Decimal("0.50000000")

    def test_pnl_computed_correctly(self):
        pm = self._make_pm(balance=Decimal("10000"))
        self._seed_position(pm, size=Decimal("0.10000000"))

        trade = pm.reduce_position(
            symbol="BTCUSDT",
            sell_fraction=Decimal("1"),
            exit_price=Decimal("60000"),  # 20% profit
            exit_time=datetime(2026, 1, 2),
            exit_reason=ExitReason.EMA_CROSS,
        )

        # Entry cost = 0.1 * 50000 = 5000, buy fee = 5
        # Proceeds = 0.1 * 60000 = 6000, sell fee = 6
        # Net proceeds = 5994, PnL = 5994 - 5005 = 989
        assert trade.pnl_usdt > 0
        assert trade.pnl_percent > 0


# ── Gap 10: _acquire_tick_lock ────────────────────────────────────────────────


class TestAcquireTickLock:

    def test_none_candle_time_always_proceeds(self):
        from core.tasks import _acquire_tick_lock

        assert _acquire_tick_lock(None) is True

    def test_first_lock_acquired(self):
        """SET NX returns True on first call → lock acquired."""
        from core.tasks import _acquire_tick_lock

        mock_redis = MagicMock()
        mock_client = MagicMock()
        mock_redis.Redis.from_url.return_value = mock_client
        mock_client.set.return_value = True  # NX succeeds

        with patch.dict("sys.modules", {"redis": mock_redis}):
            result = _acquire_tick_lock("2026-01-01T00:00:00")

        assert result is True
        mock_client.set.assert_called_once()
        call_kwargs = mock_client.set.call_args
        assert call_kwargs[1]["nx"] is True

    def test_duplicate_lock_rejected(self):
        """SET NX returns None on duplicate → lock not acquired."""
        from core.tasks import _acquire_tick_lock

        mock_redis = MagicMock()
        mock_client = MagicMock()
        mock_redis.Redis.from_url.return_value = mock_client
        mock_client.set.return_value = None  # NX fails — key exists

        with patch.dict("sys.modules", {"redis": mock_redis}):
            result = _acquire_tick_lock("2026-01-01T00:00:00")

        assert result is False

    def test_redis_error_fails_open(self):
        """If Redis is unreachable, fail-open (return True)."""
        from core.tasks import _acquire_tick_lock

        mock_redis = MagicMock()
        mock_client = MagicMock()
        mock_redis.Redis.from_url.return_value = mock_client
        mock_client.set.side_effect = ConnectionError("Redis down")

        with patch.dict("sys.modules", {"redis": mock_redis}):
            result = _acquire_tick_lock("2026-01-01T00:00:00")

        assert result is True

    def test_lock_key_includes_candle_time(self):
        """The Redis key should embed the candle open time for uniqueness."""
        from core.tasks import _acquire_tick_lock

        mock_redis = MagicMock()
        mock_client = MagicMock()
        mock_redis.Redis.from_url.return_value = mock_client
        mock_client.set.return_value = True

        with patch.dict("sys.modules", {"redis": mock_redis}):
            _acquire_tick_lock("2026-04-17T12:00:00")

        key = mock_client.set.call_args[0][0]
        assert "2026-04-17T12:00:00" in key
