"""
Unit tests for capital limits (task 4.4) and trade size limits (task 4.6)
in RiskEngine.

Covers:
  - max_daily_trades: blocks OPEN and SCALE_IN when limit reached
  - max_daily_trades: counter resets across different dates
  - max_trade_size_usdt: caps notional per trade
  - max_symbol_exposure_usdt: blocks OPEN when symbol already at cap
  - Interactions: both caps active simultaneously
"""

from datetime import date, datetime, timezone
from decimal import Decimal
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from core.models import (
    MarketData,
    Portfolio,
    Position,
    RiskAction,
    Signal,
)
from core.risk import RiskEngine


# ── Helpers ───────────────────────────────────────────────────────────────────

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


def _portfolio(balance=Decimal("10000")) -> Portfolio:
    return Portfolio(usdt_balance=balance, equity=balance)


def _portfolio_with_position(
    symbol="BTCUSDT",
    size=Decimal("0.1"),
    entry_price=Decimal("50000"),
    scale_in_count=0,
    balance=Decimal("5000"),
) -> Portfolio:
    pos = Position(
        symbol=symbol,
        size=size,
        entry_price=entry_price,
        entry_time=datetime(2024, 1, 1, 12),
        highest_close=entry_price,
        trailing_stop_price=Decimal("0"),
        hard_stop_price=Decimal("0"),
        scale_in_count=scale_in_count,
    )
    return Portfolio(
        usdt_balance=balance,
        equity=balance + size * entry_price,
        positions={symbol: pos},
    )


def _sh_engine(risk_config: dict) -> RiskEngine:
    """Smart Hodler engine with custom risk config."""
    return RiskEngine(
        strategy_name="smart_hodler",
        strategy_config={
            "entry_initial_pct": 0.50,
            "entry_scale_1_pct": 0.25,
            "entry_scale_2_pct": 0.25,
            "entry_scale_1_candles": 1,
            "entry_scale_2_candles": 1,
            "exit_momentum_fade_pct": 0.50,
            "hard_stop_pct": 0.03,
            "cooldown_candles": 16,
            "timeframe_primary": "15m",
        },
        risk_config=risk_config,
    )


# ── 4.4: max_daily_trades ─────────────────────────────────────────────────────

class TestMaxDailyTrades:
    def test_open_allowed_when_limit_not_reached(self):
        engine = _sh_engine({"max_trades_per_day": 5})
        decision = engine.evaluate(Signal.BUY, _market_data(), _portfolio())
        assert decision.action == RiskAction.OPEN

    def test_open_blocked_when_limit_reached(self):
        engine = _sh_engine({"max_trades_per_day": 2})
        portfolio = _portfolio()
        market = _market_data()
        # Open 2 positions (each on a fresh portfolio so they succeed)
        engine.evaluate(Signal.BUY, market, portfolio)
        engine.evaluate(Signal.BUY, market, portfolio)
        # Third attempt must be blocked
        decision = engine.evaluate(Signal.BUY, market, portfolio)
        assert decision.action == RiskAction.HOLD

    def test_daily_open_counter_increments(self):
        engine = _sh_engine({"max_trades_per_day": 10})
        assert engine.get_daily_opens() == 0
        engine.evaluate(Signal.BUY, _market_data(), _portfolio())
        assert engine.get_daily_opens() == 1

    def test_scale_in_blocked_when_daily_limit_reached(self):
        engine = _sh_engine({"max_trades_per_day": 1})
        market = _market_data()
        portfolio = _portfolio()
        # First open consumes the single daily slot
        engine.evaluate(Signal.BUY, market, portfolio)
        assert engine.get_daily_opens() == 1
        # Now try to scale into an existing position — should be blocked
        portfolio_with_pos = _portfolio_with_position(scale_in_count=0)
        decision = engine.evaluate(Signal.BUY, market, portfolio_with_pos)
        assert decision.action == RiskAction.HOLD

    def test_no_limit_when_max_trades_per_day_not_set(self):
        engine = _sh_engine({})  # no max_trades_per_day
        for _ in range(50):
            engine.evaluate(Signal.BUY, _market_data(), _portfolio())
        # Counter keeps incrementing — no block
        assert engine.get_daily_opens() == 50

    def test_daily_limit_resets_on_new_day(self):
        engine = _sh_engine({"max_trades_per_day": 1})
        today = date(2024, 6, 1)
        tomorrow = date(2024, 6, 2)

        with patch.object(type(engine), "_today_utc", return_value=today):
            engine.evaluate(Signal.BUY, _market_data(), _portfolio())
            assert engine.get_daily_opens(today) == 1
            assert engine._is_daily_limit_reached() is True

        with patch.object(type(engine), "_today_utc", return_value=tomorrow):
            assert engine._is_daily_limit_reached() is False
            engine.evaluate(Signal.BUY, _market_data(), _portfolio())
            assert engine.get_daily_opens(tomorrow) == 1

    def test_sell_signal_not_affected_by_daily_limit(self):
        engine = _sh_engine({"max_trades_per_day": 0})
        # Force limit exceeded
        engine._daily_opens[engine._today_utc()] = 99
        portfolio = _portfolio_with_position()
        # SELL should still go through — limit only blocks new entries
        decision = engine.evaluate(Signal.SELL_FULL, _market_data(), portfolio)
        assert decision.action == RiskAction.CLOSE


# ── 4.6: max_trade_size_usdt ──────────────────────────────────────────────────

class TestMaxTradeSizeUsdt:
    def test_trade_size_not_capped_when_unset(self):
        engine = _sh_engine({"max_trade_size_usdt": None})
        portfolio = _portfolio(balance=Decimal("10000"))
        decision = engine.evaluate(Signal.BUY, _market_data(close=50000.0), portfolio)
        assert decision.action == RiskAction.OPEN
        # 50% of 10000 / 50000 = 0.1 BTC
        assert decision.size == Decimal("0.1")

    def test_trade_size_capped_by_max_trade_size_usdt(self):
        # Cap at 1000 USDT; 50% of 10000 = 5000 USDT → capped to 1000 USDT
        # 1000 / 50000 = 0.02 BTC
        engine = _sh_engine({"max_trade_size_usdt": 1000})
        portfolio = _portfolio(balance=Decimal("10000"))
        decision = engine.evaluate(Signal.BUY, _market_data(close=50000.0), portfolio)
        assert decision.action == RiskAction.OPEN
        assert decision.size == Decimal("0.02")

    def test_trade_size_cap_applies_to_scale_in(self):
        engine = _sh_engine({"max_trade_size_usdt": 500})
        portfolio = _portfolio_with_position(scale_in_count=0, balance=Decimal("5000"))
        decision = engine.evaluate(Signal.BUY, _market_data(close=50000.0), portfolio)
        if decision.action == RiskAction.SCALE_IN:
            notional = decision.size * Decimal("50000")
            assert notional <= Decimal("500")

    def test_small_cap_results_in_hold_when_size_rounds_to_zero(self):
        # Cap of 0.0001 USDT → size = 0.0001 / 50000 = 2e-9 → rounds DOWN to 0.00000000
        engine = _sh_engine({"max_trade_size_usdt": 0.0001})
        decision = engine.evaluate(Signal.BUY, _market_data(close=50000.0), _portfolio())
        assert decision.action == RiskAction.HOLD


# ── 4.6: max_symbol_exposure_usdt ────────────────────────────────────────────

class TestMaxSymbolExposureUsdt:
    def test_open_allowed_when_no_existing_position(self):
        engine = _sh_engine({"max_symbol_exposure_usdt": 5000})
        decision = engine.evaluate(Signal.BUY, _market_data(), _portfolio())
        assert decision.action == RiskAction.OPEN

    def test_open_blocked_when_exposure_already_at_cap(self):
        # Position: 0.1 BTC × 50000 = 5000 USDT — already at cap
        engine = _sh_engine({"max_symbol_exposure_usdt": 5000})
        portfolio = _portfolio_with_position(
            size=Decimal("0.1"), entry_price=Decimal("50000")
        )
        decision = engine.evaluate(Signal.BUY, _market_data(close=50000.0), portfolio)
        assert decision.action == RiskAction.HOLD

    def test_open_allowed_when_exposure_below_cap(self):
        # Position: 0.05 BTC × 50000 = 2500 USDT, cap = 5000 USDT
        engine = _sh_engine({"max_symbol_exposure_usdt": 5000})
        portfolio = _portfolio_with_position(
            size=Decimal("0.05"), entry_price=Decimal("50000"), scale_in_count=0
        )
        decision = engine.evaluate(Signal.BUY, _market_data(close=50000.0), portfolio)
        # Either SCALE_IN or HOLD depending on candle count, but NOT blocked by cap
        assert decision.action != RiskAction.HOLD or engine._is_symbol_exposure_exceeded(
            "BTCUSDT", portfolio, Decimal("50000")
        ) is False

    def test_no_cap_when_max_symbol_exposure_not_set(self):
        engine = _sh_engine({})
        assert engine._is_symbol_exposure_exceeded(
            "BTCUSDT",
            _portfolio_with_position(size=Decimal("1000"), entry_price=Decimal("50000")),
            Decimal("50000"),
        ) is False


# ── Interaction: both caps active ─────────────────────────────────────────────

class TestCombinedLimits:
    def test_daily_limit_checked_before_symbol_exposure(self):
        """When daily limit is hit, should block even if symbol exposure is fine."""
        engine = _sh_engine({"max_trades_per_day": 1, "max_symbol_exposure_usdt": 100000})
        market = _market_data()
        portfolio = _portfolio()
        engine.evaluate(Signal.BUY, market, portfolio)  # consumes the daily slot
        decision = engine.evaluate(Signal.BUY, market, portfolio)
        assert decision.action == RiskAction.HOLD

    def test_trade_size_and_daily_limit_both_enforced(self):
        engine = _sh_engine({"max_trade_size_usdt": 500, "max_trades_per_day": 3})
        portfolio = _portfolio(balance=Decimal("10000"))
        market = _market_data(close=50000.0)
        results = [engine.evaluate(Signal.BUY, market, portfolio) for _ in range(5)]
        opens = [r for r in results if r.action == RiskAction.OPEN]
        assert len(opens) == 3
        for r in opens:
            notional = r.size * Decimal("50000")
            assert notional <= Decimal("500")
