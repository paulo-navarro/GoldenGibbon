"""
Tests for Phase 3.2: trailing stop + smart time stop for Mean Reversion.
"""

from datetime import datetime
from decimal import Decimal

import pandas as pd

from core.models import (
    ExitReason,
    MarketData,
    Portfolio,
    Position,
)
from core.risk import RiskEngine


# ── Helpers ──────────────────────────────────────────────────────────────────

N = 50


def _series(value, length=N):
    return pd.Series([value] * length, dtype=float)


def _candles(close=50000.0, length=N):
    dates = pd.date_range("2024-01-01", periods=length, freq="15min")
    return pd.DataFrame(
        {
            "open": [close] * length,
            "high": [close + 10] * length,
            "low": [close - 10] * length,
            "close": [close] * length,
            "volume": [100.0] * length,
        },
        index=dates,
    )


def _market_data(close=50000.0, atr=500.0) -> MarketData:
    return MarketData(
        symbol="BTCUSDT",
        timeframe="15m",
        candles=_candles(close),
        indicators={"atr": _series(atr)},
    )


def _portfolio_with_position(
    entry_price=Decimal("50000"),
    highest_close=None,
    trailing_stop_price=Decimal("0"),
    hard_stop_price=Decimal("0"),
    entry_time=None,
) -> Portfolio:
    if highest_close is None:
        highest_close = entry_price
    if entry_time is None:
        entry_time = datetime(2026, 2, 17, 10, 0)
    pos = Position(
        symbol="BTCUSDT",
        size=Decimal("0.1"),
        entry_price=entry_price,
        entry_time=entry_time,
        highest_close=highest_close,
        trailing_stop_price=trailing_stop_price,
        hard_stop_price=hard_stop_price,
        scale_in_count=0,
    )
    return Portfolio(
        usdt_balance=Decimal("5000"),
        positions={"BTCUSDT": pos},
        equity=Decimal("5000") + Decimal("0.1") * entry_price,
        open_trades_count=1,
    )


MR_CONFIG = {
    "entry_pct": 0.75,
    "exit_partial_pct": 0.50,
    "hard_stop_pct": 0.02,
    "trailing_stop_atr_multiplier": 2.5,
    "time_stop_candles": 16,
    "time_stop_cooldown_candles": 4,
    "timeframe_primary": "15m",
}

RISK_CONFIG = {
    "max_position_size_pct": 1.0,
    "trailing_stop_atr_multiplier": 2.0,
}


def _mr_engine(**overrides) -> RiskEngine:
    cfg = {**MR_CONFIG, **overrides}
    return RiskEngine("mean_reversion", cfg, RISK_CONFIG)


# ── Trailing stop tests ─────────────────────────────────────────────────────


class TestMRTrailingStop:

    def test_trailing_stop_closes_position(self):
        """MR position with trailing stop active, close drops below -> CLOSE."""
        engine = _mr_engine()
        port = _portfolio_with_position(
            entry_price=Decimal("50000"),
            highest_close=Decimal("52000"),
            trailing_stop_price=Decimal("50750"),
        )
        md = _market_data(close=50500.0, atr=500.0)
        result = engine.check_stops(md, port)
        assert result.decision is not None
        assert result.decision.exit_reason == ExitReason.TRAILING_STOP

    def test_trailing_stop_ratchets_up(self):
        """highest_close rises -> trailing stop follows, never lowers."""
        engine = _mr_engine()
        port = _portfolio_with_position(
            entry_price=Decimal("50000"),
            highest_close=Decimal("50000"),
        )
        # Close goes to 52000, ATR=500, mult=2.5 -> trailing = 52000 - 1250 = 50750
        md = _market_data(close=52000.0, atr=500.0)
        result = engine.check_stops(md, port)
        assert result.trailing_stop_price == Decimal("50750")

        # Now close dips to 51000 (above trailing) -> trailing stays at 50750
        port2 = _portfolio_with_position(
            entry_price=Decimal("50000"),
            highest_close=Decimal("52000"),
            trailing_stop_price=Decimal("50750"),
        )
        md2 = _market_data(close=51000.0, atr=500.0)
        result2 = engine.check_stops(md2, port2)
        assert result2.decision is None
        assert result2.trailing_stop_price == Decimal("50750")

    def test_trailing_stop_disabled(self):
        """trailing_stop_enabled=False disables trailing for MR."""
        engine = _mr_engine(trailing_stop_enabled=False)
        port = _portfolio_with_position(entry_price=Decimal("50000"))
        md = _market_data(close=40000.0, atr=500.0)
        result = engine.check_stops(md, port)
        assert result.decision is None

    def test_mr_uses_wider_atr_multiplier(self):
        """MR uses 2.5x ATR (not SH's 2.0x)."""
        engine = _mr_engine()
        port = _portfolio_with_position(
            entry_price=Decimal("50000"),
            highest_close=Decimal("50000"),
        )
        md = _market_data(close=50000.0, atr=1000.0)
        result = engine.check_stops(md, port)
        # 50000 - (1000 * 2.5) = 47500
        assert result.trailing_stop_price == Decimal("47500")


# ── Smart time stop tests ────────────────────────────────────────────────────


class TestSmartTimeStop:

    def _old_entry_portfolio(self, entry_price=Decimal("50000")):
        """Position opened long ago (> 16 candles = 4h at 15m)."""
        return _portfolio_with_position(
            entry_price=entry_price,
            entry_time=datetime(2024, 1, 1, 0, 0),
        )

    def test_time_stop_skips_profitable(self):
        """Time stop does NOT close when position is in profit."""
        engine = _mr_engine()
        port = self._old_entry_portfolio(entry_price=Decimal("50000"))
        md = _market_data(close=51000.0, atr=500.0)
        result = engine.check_stops(md, port)
        assert result.decision is None or result.decision.exit_reason != ExitReason.TIME_STOP

    def test_time_stop_closes_losing(self):
        """Time stop CLOSES when position PnL <= 0."""
        engine = _mr_engine()
        port = self._old_entry_portfolio(entry_price=Decimal("50000"))
        md = _market_data(close=49000.0, atr=500.0)
        result = engine.check_stops(md, port)
        assert result.decision is not None
        assert result.decision.exit_reason == ExitReason.TIME_STOP

    def test_time_stop_closes_breakeven(self):
        """Time stop CLOSES when position is exactly break-even (PnL == 0)."""
        engine = _mr_engine()
        port = self._old_entry_portfolio(entry_price=Decimal("50000"))
        md = _market_data(close=50000.0, atr=500.0)
        result = engine.check_stops(md, port)
        assert result.decision is not None
        assert result.decision.exit_reason == ExitReason.TIME_STOP

    def test_time_stop_closes_when_skip_disabled(self):
        """time_stop_skip_profitable=False closes regardless of PnL."""
        engine = _mr_engine(time_stop_skip_profitable=False)
        port = self._old_entry_portfolio(entry_price=Decimal("50000"))
        md = _market_data(close=55000.0, atr=500.0)
        result = engine.check_stops(md, port)
        assert result.decision is not None
        assert result.decision.exit_reason == ExitReason.TIME_STOP
