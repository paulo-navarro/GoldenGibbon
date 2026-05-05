"""
Unit tests for the Risk Engine (task 1.14).

Covers: initialisation, position sizing (Smart Hodler scaled entries,
Mean Reversion flat 75 %), sell mapping, hold passthrough,
scale-in eligibility & BUY-candle tracking, exposure caps,
and the stop-check stub.
"""

from datetime import datetime
from decimal import Decimal

import numpy as np
import pandas as pd
import pytest

from core.models import (
    ExitReason,
    MarketData,
    Portfolio,
    Position,
    PositionSide,
    RiskAction,
    Signal,
    StopCheckResult,
)
from core.risk import RiskEngine


# ── Helpers ──────────────────────────────────────────────────────────────────

N = 50  # primary bars
N_1H = 15  # secondary bars


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


def _market_data(close=50000.0, atr=500.0, include_atr=True) -> MarketData:
    """Build a minimal MarketData with optional ATR indicator."""
    indicators = {}
    if include_atr:
        indicators["atr"] = _series(atr)
    return MarketData(
        symbol="BTCUSDT",
        timeframe="15m",
        candles=_candles(close),
        indicators=indicators,
    )


def _empty_portfolio(balance=Decimal("10000")) -> Portfolio:
    return Portfolio(
        usdt_balance=balance,
        equity=balance,
    )


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
        entry_time=datetime(2026, 2, 17, 10, 0),
        highest_close=entry_price,
        trailing_stop_price=Decimal("0"),
        hard_stop_price=Decimal("0"),
        scale_in_count=scale_in_count,
    )
    return Portfolio(
        usdt_balance=balance,
        positions={symbol: pos},
        equity=balance + size * entry_price,
        open_trades_count=1,
    )


# ── Configs ──────────────────────────────────────────────────────────────────

SH_CONFIG = {
    "entry_initial_pct": 0.50,
    "entry_scale_1_pct": 0.25,
    "entry_scale_2_pct": 0.25,
    "entry_scale_1_candles": 8,
    "entry_scale_2_candles": 16,
    "exit_momentum_fade_pct": 0.50,
    "hard_stop_pct": 0.03,
    "trailing_stop_atr_multiplier": 2.0,
}

MR_CONFIG = {
    "entry_pct": 0.75,
    "exit_partial_pct": 0.50,
    "hard_stop_pct": 0.02,
}

RISK_CONFIG = {
    "max_position_size_pct": 1.0,
    "trailing_stop_atr_multiplier": 2.0,
}


def _sh_engine(**overrides) -> RiskEngine:
    cfg = {**SH_CONFIG, **overrides}
    return RiskEngine("smart_hodler", cfg, RISK_CONFIG)


def _mr_engine(**overrides) -> RiskEngine:
    cfg = {**MR_CONFIG, **overrides}
    return RiskEngine("mean_reversion", cfg, RISK_CONFIG)


BG_CONFIG = {
    "position_size_pct": 0.50,
    "exit_partial_pct": 0.50,
    "hard_stop_pct": 0.05,
}


def _bg_engine(*, shorts_enabled=True, **overrides) -> RiskEngine:
    cfg = {**BG_CONFIG, **overrides}
    return RiskEngine("bear_guard", cfg, RISK_CONFIG, shorts_enabled=shorts_enabled)


def _portfolio_with_short_position(
    symbol="BTCUSDT",
    size=Decimal("0.1"),
    entry_price=Decimal("50000"),
    balance=Decimal("5000"),
) -> Portfolio:
    pos = Position(
        symbol=symbol,
        side=PositionSide.SHORT,
        size=size,
        entry_price=entry_price,
        entry_time=datetime(2026, 2, 17, 10, 0),
        highest_close=entry_price,
        lowest_close=entry_price,
        trailing_stop_price=Decimal("52000"),
        hard_stop_price=Decimal("52500"),
    )
    return Portfolio(
        usdt_balance=balance,
        positions={symbol: pos},
        equity=balance + size * entry_price,
        open_trades_count=1,
    )


# ── Initialisation ───────────────────────────────────────────────────────────


class TestInitialisation:
    def test_smart_hodler_strategy_name(self):
        engine = _sh_engine()
        assert engine.strategy_name == "smart_hodler"

    def test_mean_reversion_strategy_name(self):
        engine = _mr_engine()
        assert engine.strategy_name == "mean_reversion"

    def test_unknown_strategy_rejected(self):
        with pytest.raises(ValueError, match="Unknown strategy"):
            RiskEngine("momentum", {}, {})


# ── HOLD passthrough ─────────────────────────────────────────────────────────


class TestHoldPassthrough:
    def test_hold_returns_hold(self):
        engine = _sh_engine()
        md = _market_data()
        dec = engine.evaluate(Signal.HOLD, md, _empty_portfolio())
        assert dec.action == RiskAction.HOLD

    def test_hold_resets_buy_candle_counter(self):
        engine = _sh_engine()
        md = _market_data()
        port = _portfolio_with_position()
        # Simulate a BUY first
        engine.evaluate(Signal.BUY, md, port)
        assert engine.get_buy_signal_candles("BTCUSDT") >= 1
        # Then HOLD
        engine.evaluate(Signal.HOLD, md, port)
        assert engine.get_buy_signal_candles("BTCUSDT") == 0


# ── SELL_FULL mapping ────────────────────────────────────────────────────────


class TestSellFull:
    def test_sell_full_with_position(self):
        engine = _sh_engine()
        md = _market_data()
        port = _portfolio_with_position()
        dec = engine.evaluate(Signal.SELL_FULL, md, port)
        assert dec.action == RiskAction.CLOSE
        assert dec.symbol == "BTCUSDT"
        assert dec.size == Decimal("0.1")
        assert dec.exit_reason is not None

    def test_sell_full_without_position(self):
        engine = _sh_engine()
        md = _market_data()
        dec = engine.evaluate(Signal.SELL_FULL, md, _empty_portfolio())
        assert dec.action == RiskAction.HOLD

    def test_sell_full_resets_counter(self):
        engine = _sh_engine()
        md = _market_data()
        port = _portfolio_with_position()
        engine._buy_signal_candles["BTCUSDT"] = 5
        engine.evaluate(Signal.SELL_FULL, md, port)
        assert engine.get_buy_signal_candles("BTCUSDT") == 0


# ── SELL_HALF mapping ────────────────────────────────────────────────────────


class TestSellHalf:
    def test_sell_half_with_position(self):
        engine = _sh_engine()
        md = _market_data()
        port = _portfolio_with_position()
        dec = engine.evaluate(Signal.SELL_HALF, md, port)
        assert dec.action == RiskAction.REDUCE
        assert dec.sell_fraction == Decimal("0.5")
        assert dec.exit_reason == ExitReason.MOMENTUM_FADE

    def test_sell_half_without_position(self):
        engine = _sh_engine()
        md = _market_data()
        dec = engine.evaluate(Signal.SELL_HALF, md, _empty_portfolio())
        assert dec.action == RiskAction.HOLD

    def test_sell_half_mean_reversion(self):
        engine = _mr_engine()
        md = _market_data()
        port = _portfolio_with_position()
        dec = engine.evaluate(Signal.SELL_HALF, md, port)
        assert dec.action == RiskAction.REDUCE
        assert dec.sell_fraction == Decimal("0.5")


# ── Position Sizing – Smart Hodler initial entry ─────────────────────────────


class TestSizingSmartHodler:
    def test_initial_entry_50_pct(self):
        engine = _sh_engine()
        md = _market_data(close=50000.0)
        port = _empty_portfolio(Decimal("10000"))
        dec = engine.evaluate(Signal.BUY, md, port)

        assert dec.action == RiskAction.OPEN
        # 50% of 10000 = 5000 USDT → 5000 / 50000 = 0.1 BTC
        assert dec.size == Decimal("0.10000000")

    def test_initial_entry_hard_stop_set(self):
        engine = _sh_engine()
        md = _market_data(close=50000.0)
        dec = engine.evaluate(Signal.BUY, md, _empty_portfolio())

        # Hard stop = 50000 * (1 - 0.03) = 48500
        assert dec.hard_stop_price == Decimal("48500.00000000")

    def test_initial_entry_trailing_stop_set(self):
        engine = _sh_engine()
        md = _market_data(close=50000.0, atr=1000.0)
        dec = engine.evaluate(Signal.BUY, md, _empty_portfolio())

        # Trailing stop = 50000 - 2 * 1000 = 48000
        assert dec.trailing_stop_price == Decimal("48000.00000000")

    def test_trailing_stop_no_atr_returns_zero(self):
        engine = _sh_engine()
        md = _market_data(close=50000.0, include_atr=False)
        dec = engine.evaluate(Signal.BUY, md, _empty_portfolio())
        assert dec.trailing_stop_price == Decimal("0")


# ── Position Sizing – Mean Reversion initial entry ───────────────────────────


class TestSizingMeanReversion:
    def test_initial_entry_75_pct(self):
        engine = _mr_engine()
        md = _market_data(close=50000.0)
        port = _empty_portfolio(Decimal("10000"))
        dec = engine.evaluate(Signal.BUY, md, port)

        assert dec.action == RiskAction.OPEN
        # 75% of 10000 = 7500 → 7500 / 50000 = 0.15
        assert dec.size == Decimal("0.15000000")

    def test_hard_stop_2_pct(self):
        engine = _mr_engine()
        md = _market_data(close=50000.0)
        dec = engine.evaluate(Signal.BUY, md, _empty_portfolio())
        # 50000 * (1 - 0.02) = 49000
        assert dec.hard_stop_price == Decimal("49000.00000000")


# ── Scale-in – Smart Hodler ─────────────────────────────────────────────────


class TestScaleInSmartHodler:
    def test_no_scale_before_8_candles(self):
        engine = _sh_engine()
        md = _market_data()
        port = _portfolio_with_position(scale_in_count=0)

        # Simulate 7 BUY signals (1 initial + 6 more)
        for _ in range(7):
            dec = engine.evaluate(Signal.BUY, md, port)

        # 7th call → counter=7, not yet 8
        assert dec.action == RiskAction.HOLD

    def test_scale_in_at_8_candles(self):
        engine = _sh_engine()
        md = _market_data(close=50000.0)
        port = _portfolio_with_position(
            scale_in_count=0,
            balance=Decimal("5000"),
        )

        # Simulate 8 consecutive BUY signals
        for _ in range(8):
            dec = engine.evaluate(Signal.BUY, md, port)

        assert dec.action == RiskAction.SCALE_IN
        # 25% of 5000 = 1250 → 1250 / 50000 = 0.025
        assert dec.size == Decimal("0.02500000")

    def test_scale_in_at_16_candles(self):
        engine = _sh_engine()
        md = _market_data(close=50000.0)

        # After first scale-in, scale_in_count=1
        port = _portfolio_with_position(
            scale_in_count=1,
            balance=Decimal("3750"),
        )

        # Simulate 16 consecutive BUY signals
        for _ in range(16):
            dec = engine.evaluate(Signal.BUY, md, port)

        assert dec.action == RiskAction.SCALE_IN
        # 25% of 3750 = 937.5 → 937.5 / 50000 = 0.01875
        assert dec.size == Decimal("0.01875000")

    def test_no_scale_after_max(self):
        engine = _sh_engine()
        md = _market_data()
        port = _portfolio_with_position(scale_in_count=2)

        for _ in range(20):
            dec = engine.evaluate(Signal.BUY, md, port)

        assert dec.action == RiskAction.HOLD


# ── Scale-in – Mean Reversion (never) ───────────────────────────────────────


class TestScaleInMeanReversion:
    def test_no_scale_in(self):
        engine = _mr_engine()
        md = _market_data()
        port = _portfolio_with_position(scale_in_count=0)

        for _ in range(20):
            dec = engine.evaluate(Signal.BUY, md, port)

        assert dec.action == RiskAction.HOLD


# ── BUY signal candle counter ────────────────────────────────────────────────


class TestBuySignalCandles:
    def test_counter_starts_at_1_on_open(self):
        engine = _sh_engine()
        md = _market_data()
        engine.evaluate(Signal.BUY, md, _empty_portfolio())
        assert engine.get_buy_signal_candles("BTCUSDT") == 1

    def test_counter_increments_on_consecutive_buys(self):
        engine = _sh_engine()
        md = _market_data()
        port = _portfolio_with_position()
        engine.evaluate(Signal.BUY, md, port)
        engine.evaluate(Signal.BUY, md, port)
        engine.evaluate(Signal.BUY, md, port)
        assert engine.get_buy_signal_candles("BTCUSDT") == 3

    def test_counter_resets_on_hold(self):
        engine = _sh_engine()
        md = _market_data()
        port = _portfolio_with_position()
        engine.evaluate(Signal.BUY, md, port)
        engine.evaluate(Signal.BUY, md, port)
        engine.evaluate(Signal.HOLD, md, port)
        assert engine.get_buy_signal_candles("BTCUSDT") == 0

    def test_counter_resets_on_sell(self):
        engine = _sh_engine()
        md = _market_data()
        port = _portfolio_with_position()
        engine.evaluate(Signal.BUY, md, port)
        engine.evaluate(Signal.SELL_FULL, md, port)
        assert engine.get_buy_signal_candles("BTCUSDT") == 0


# ── Exposure cap ─────────────────────────────────────────────────────────────


class TestExposureCap:
    def test_size_capped_at_max_position_pct(self):
        engine = RiskEngine(
            "smart_hodler",
            {**SH_CONFIG, "entry_initial_pct": 0.50},
            {"max_position_size_pct": 0.30},  # Cap at 30% of equity
        )
        md = _market_data(close=50000.0)
        port = _empty_portfolio(Decimal("10000"))
        dec = engine.evaluate(Signal.BUY, md, port)

        # Desired = 50% of 10000 = 5000; Cap = 30% of 10000 = 3000
        # Size = 3000 / 50000 = 0.06
        assert dec.action == RiskAction.OPEN
        assert dec.size == Decimal("0.06000000")


# ── Check stops – trailing stop ──────────────────────────────────────────────


class TestCheckStops_NoPosition:
    """When no position exists the result should be empty."""

    def test_empty_portfolio(self):
        engine = _sh_engine()
        md = _market_data()
        result = engine.check_stops(md, _empty_portfolio())
        assert result.decision is None
        assert result.highest_close is None
        assert result.trailing_stop_price is None
        assert result.stop_hit is False

    def test_different_symbol(self):
        """Position for ETHUSDT but MarketData for BTCUSDT."""
        engine = _sh_engine()
        md = _market_data()
        port = _portfolio_with_position(symbol="ETHUSDT")
        result = engine.check_stops(md, port)
        assert result.decision is None


class TestCheckStops_MeanReversion:
    """Mean reversion trailing stop behaviour."""

    def test_mr_trailing_stop_fires(self):
        engine = _mr_engine()
        md = _market_data(close=40000.0, atr=500.0)
        port = _portfolio_with_position(entry_price=Decimal("50000"))
        result = engine.check_stops(md, port)
        assert result.decision is not None
        assert result.decision.exit_reason == ExitReason.TRAILING_STOP

    def test_mr_skips_trailing_stop_when_disabled(self):
        engine = _mr_engine(trailing_stop_enabled=False)
        md = _market_data(close=40000.0, atr=500.0)
        port = _portfolio_with_position(entry_price=Decimal("50000"))
        result = engine.check_stops(md, port)
        assert result.decision is None
        assert result.stop_hit is False


class TestCheckStops_TrailingStop:
    """Smart Hodler trailing stop logic (2×ATR below highest close)."""

    # -- Ratchet highest_close upward -----------------------------------------

    def test_highest_close_ratchets_up(self):
        """When close > position.highest_close, result reflects new high."""
        engine = _sh_engine()
        md = _market_data(close=52000.0, atr=500.0)
        port = _portfolio_with_position()
        # Position highest_close defaults to entry_price = 50 000
        result = engine.check_stops(md, port)
        assert result.highest_close == Decimal("52000.0")

    def test_highest_close_does_not_lower(self):
        """When close < position.highest_close, highest remains unchanged."""
        engine = _sh_engine()
        md = _market_data(close=49000.0, atr=500.0)
        port = _portfolio_with_position()
        # highest_close == entry_price == 50 000
        result = engine.check_stops(md, port)
        assert result.highest_close == Decimal("50000")

    # -- Trailing stop calculation (highest − ATR × multiplier) ---------------

    def test_trailing_stop_value(self):
        """trailing_stop = highest − (atr × multiplier)."""
        engine = _sh_engine()  # multiplier = 2.0
        md = _market_data(close=50000.0, atr=500.0)
        port = _portfolio_with_position()
        result = engine.check_stops(md, port)
        # 50000 − (500 × 2) = 49 000
        assert result.trailing_stop_price == Decimal("49000")

    def test_trailing_stop_ratchets_up_with_new_high(self):
        """When a new high raises highest_close, trailing stop moves up."""
        engine = _sh_engine()
        md = _market_data(close=55000.0, atr=500.0)
        port = _portfolio_with_position()
        result = engine.check_stops(md, port)
        # 55000 − 1000 = 54 000
        assert result.trailing_stop_price == Decimal("54000")

    def test_trailing_stop_never_lowers(self):
        """Even if ATR widens, the stop must not move down."""
        engine = _sh_engine()
        md = _market_data(close=50000.0, atr=500.0)
        # Suppose position already has a higher trailing stop stored
        port = _portfolio_with_position()
        pos = port.positions["BTCUSDT"]
        pos.trailing_stop_price = Decimal("49500")
        result = engine.check_stops(md, port)
        # Computed = 50000 − 1000 = 49 000, but existing 49 500 is higher
        assert result.trailing_stop_price == Decimal("49500")

    # -- Stop violation -------------------------------------------------------

    def test_close_below_trailing_triggers_exit(self):
        """Close strictly less than trailing stop → CLOSE decision."""
        engine = _sh_engine()
        # ATR=500, multiplier=2 → trailing = 50000 − 1000 = 49000
        md = _market_data(close=48999.0, atr=500.0)
        port = _portfolio_with_position()
        # Ensure trailing stop is already set at 49000
        pos = port.positions["BTCUSDT"]
        pos.trailing_stop_price = Decimal("49000")
        result = engine.check_stops(md, port)
        assert result.stop_hit is True
        assert result.decision is not None
        assert result.decision.action == RiskAction.CLOSE
        assert result.decision.symbol == "BTCUSDT"
        assert result.decision.size == Decimal("0.1")
        assert result.decision.exit_reason == ExitReason.TRAILING_STOP

    def test_close_equals_trailing_no_exit(self):
        """Close == trailing stop → no violation (strict less-than)."""
        engine = _sh_engine()
        md = _market_data(close=49000.0, atr=500.0)
        port = _portfolio_with_position()
        pos = port.positions["BTCUSDT"]
        pos.trailing_stop_price = Decimal("49000")
        result = engine.check_stops(md, port)
        assert result.stop_hit is False
        assert result.decision is None

    def test_close_above_trailing_no_exit(self):
        """Close above trailing → normal update, no exit."""
        engine = _sh_engine()
        md = _market_data(close=51000.0, atr=500.0)
        port = _portfolio_with_position()
        result = engine.check_stops(md, port)
        assert result.stop_hit is False
        assert result.decision is None
        # Updated levels are returned
        assert result.highest_close == Decimal("51000.0")

    # -- Edge: no ATR available -----------------------------------------------

    def test_no_atr_graceful(self):
        """When ATR indicator is missing, trailing stop should be 0."""
        engine = _sh_engine()
        md = _market_data(close=50000.0, include_atr=False)
        port = _portfolio_with_position()
        result = engine.check_stops(md, port)
        # Can't compute trailing → trailing_stop_price stays at 0 / not set
        assert result.stop_hit is False

    # -- Decision carries correct price ---------------------------------------

    def test_exit_price_is_close(self):
        """The RiskDecision price should be the triggering close."""
        engine = _sh_engine()
        md = _market_data(close=48500.0, atr=500.0)
        port = _portfolio_with_position()
        pos = port.positions["BTCUSDT"]
        pos.trailing_stop_price = Decimal("49000")
        result = engine.check_stops(md, port)
        assert result.decision.price == Decimal("48500.0")

    # -- Multiple ATR multiplier values ---------------------------------------

    def test_custom_atr_multiplier(self):
        """Different multiplier changes the trailing stop level."""
        engine = _sh_engine(trailing_stop_atr_multiplier=3.0)
        md = _market_data(close=50000.0, atr=500.0)
        port = _portfolio_with_position()
        result = engine.check_stops(md, port)
        # 50000 − (500 × 3) = 48 500
        assert result.trailing_stop_price == Decimal("48500")


# ── Check stops – hard stop ─────────────────────────────────────────────────


def _portfolio_with_hard_stop(
    symbol="BTCUSDT",
    size=Decimal("0.1"),
    entry_price=Decimal("50000"),
    hard_stop_price=Decimal("48500"),
    trailing_stop_price=Decimal("0"),
    balance=Decimal("5000"),
) -> Portfolio:
    """Build a portfolio with a position that has a hard stop set."""
    pos = Position(
        symbol=symbol,
        size=size,
        entry_price=entry_price,
        entry_time=datetime(2026, 2, 17, 10, 0),
        highest_close=entry_price,
        trailing_stop_price=trailing_stop_price,
        hard_stop_price=hard_stop_price,
        scale_in_count=0,
    )
    return Portfolio(
        usdt_balance=balance,
        positions={symbol: pos},
        equity=balance + size * entry_price,
        open_trades_count=1,
    )


class TestCheckStops_HardStop:
    """Hard stop: close < hard_stop_price → CLOSE + cooldown."""

    # -- Smart Hodler hard stop -----------------------------------------------

    def test_sh_close_below_hard_stop(self):
        """SH: close < hard_stop → CLOSE with HARD_STOP reason."""
        engine = _sh_engine()
        # entry 50k, hard stop at 48500 (3%: 50000 * 0.97)
        md = _market_data(close=48000.0, atr=500.0)
        port = _portfolio_with_hard_stop(hard_stop_price=Decimal("48500"))
        result = engine.check_stops(md, port)
        assert result.stop_hit is True
        assert result.decision.action == RiskAction.CLOSE
        assert result.decision.symbol == "BTCUSDT"
        assert result.decision.size == Decimal("0.1")
        assert result.decision.exit_reason == ExitReason.HARD_STOP

    def test_sh_hard_stop_cooldown_candles(self):
        """SH hard stop triggers 16-candle cooldown."""
        engine = _sh_engine(cooldown_candles=16)
        md = _market_data(close=48000.0, atr=500.0)
        port = _portfolio_with_hard_stop(hard_stop_price=Decimal("48500"))
        result = engine.check_stops(md, port)
        assert result.cooldown_candles == 16

    def test_sh_close_equals_hard_stop_no_exit(self):
        """SH: close == hard_stop → no hard-stop violation (strict less-than)."""
        engine = _sh_engine()
        # Use close = hard_stop = 49500, which is also above trailing
        # (trailing = 50000 − 1000 = 49000), so neither stop fires.
        md = _market_data(close=49500.0, atr=500.0)
        port = _portfolio_with_hard_stop(hard_stop_price=Decimal("49500"))
        result = engine.check_stops(md, port)
        assert result.stop_hit is False
        assert result.decision is None
        assert result.cooldown_candles is None

    def test_sh_close_above_hard_stop_no_exit(self):
        """SH: close above hard stop → falls through to trailing logic."""
        engine = _sh_engine()
        md = _market_data(close=49000.0, atr=500.0)
        port = _portfolio_with_hard_stop(hard_stop_price=Decimal("48500"))
        result = engine.check_stops(md, port)
        assert result.stop_hit is False
        assert result.cooldown_candles is None
        # Trailing stop still computed
        assert result.trailing_stop_price is not None

    # -- Mean Reversion hard stop ---------------------------------------------

    def test_mr_close_below_hard_stop(self):
        """MR: close < hard_stop (2%) → CLOSE with HARD_STOP reason."""
        engine = _mr_engine(cooldown_candles=8)
        # entry 50k, hard stop at 49000 (2%: 50000 * 0.98)
        md = _market_data(close=48900.0, atr=500.0)
        port = _portfolio_with_hard_stop(hard_stop_price=Decimal("49000"))
        result = engine.check_stops(md, port)
        assert result.stop_hit is True
        assert result.decision.action == RiskAction.CLOSE
        assert result.decision.exit_reason == ExitReason.HARD_STOP

    def test_mr_hard_stop_cooldown_candles(self):
        """MR hard stop triggers 8-candle cooldown."""
        engine = _mr_engine(cooldown_candles=8)
        md = _market_data(close=48900.0, atr=500.0)
        port = _portfolio_with_hard_stop(hard_stop_price=Decimal("49000"))
        result = engine.check_stops(md, port)
        assert result.cooldown_candles == 8

    def test_mr_above_hard_stop_returns_empty(self):
        """MR: close above hard stop → empty result (no trailing stop)."""
        engine = _mr_engine()
        md = _market_data(close=49500.0, atr=500.0)
        port = _portfolio_with_hard_stop(hard_stop_price=Decimal("49000"))
        result = engine.check_stops(md, port)
        assert result.stop_hit is False
        assert result.decision is None
        assert result.cooldown_candles is None

    # -- Priority: hard stop wins over trailing --------------------------------

    def test_hard_stop_takes_priority_over_trailing(self):
        """When both stops would fire, hard stop wins (runs first)."""
        engine = _sh_engine()
        # Set up so both would trigger:
        # hard_stop = 49000, trailing already at 49500, close = 48000
        md = _market_data(close=48000.0, atr=500.0)
        port = _portfolio_with_hard_stop(
            hard_stop_price=Decimal("49000"),
            trailing_stop_price=Decimal("49500"),
        )
        result = engine.check_stops(md, port)
        assert result.stop_hit is True
        assert result.decision.exit_reason == ExitReason.HARD_STOP
        assert result.cooldown_candles is not None

    # -- Decision metadata ----------------------------------------------------

    def test_hard_stop_decision_price(self):
        """Decision price is the triggering close."""
        engine = _sh_engine()
        md = _market_data(close=47500.0, atr=500.0)
        port = _portfolio_with_hard_stop(hard_stop_price=Decimal("48500"))
        result = engine.check_stops(md, port)
        assert result.decision.price == Decimal("47500.0")

    def test_hard_stop_preserves_highest_close(self):
        """highest_close is still returned even on hard stop exit."""
        engine = _sh_engine()
        md = _market_data(close=47500.0, atr=500.0)
        port = _portfolio_with_hard_stop(hard_stop_price=Decimal("48500"))
        result = engine.check_stops(md, port)
        # highest = max(50000, 47500) = 50000
        assert result.highest_close == Decimal("50000")

    # -- Trailing stop no cooldown --------------------------------------------

    def test_trailing_stop_has_no_cooldown(self):
        """Trailing stop exit must NOT set cooldown_candles."""
        engine = _sh_engine()
        md = _market_data(close=48999.0, atr=500.0)
        port = _portfolio_with_hard_stop(
            hard_stop_price=Decimal("45000"),  # Hard stop far away
            trailing_stop_price=Decimal("49000"),
        )
        result = engine.check_stops(md, port)
        assert result.stop_hit is True
        assert result.decision.exit_reason == ExitReason.TRAILING_STOP
        assert result.cooldown_candles is None


# ── Edge cases ───────────────────────────────────────────────────────────────


class TestEdgeCases:
    def test_buy_with_zero_balance(self):
        engine = _sh_engine()
        md = _market_data()
        port = _empty_portfolio(Decimal("0"))
        dec = engine.evaluate(Signal.BUY, md, port)
        assert dec.action == RiskAction.HOLD

    def test_buy_with_tiny_balance(self):
        engine = _sh_engine()
        md = _market_data(close=50000.0)
        port = _empty_portfolio(Decimal("0.0000001"))
        dec = engine.evaluate(Signal.BUY, md, port)
        # Size rounds to 0 → HOLD
        assert dec.action == RiskAction.HOLD

    def test_decision_includes_price(self):
        engine = _sh_engine()
        md = _market_data(close=42000.0)
        dec = engine.evaluate(Signal.BUY, md, _empty_portfolio())
        assert dec.price == Decimal("42000.0")

    def test_sell_full_decision_includes_price(self):
        engine = _sh_engine()
        md = _market_data(close=51000.0)
        port = _portfolio_with_position()
        dec = engine.evaluate(Signal.SELL_FULL, md, port)
        assert dec.price == Decimal("51000.0")


# ── Time Stop (Mean Reversion) ──────────────────────────────────────────────


def _mr_portfolio_with_position(
    entry_time=datetime(2024, 1, 1, 0, 0),
    entry_price=Decimal("50000"),
    hard_stop_price=Decimal("0"),
    size=Decimal("0.1"),
) -> Portfolio:
    """Portfolio with a position whose entry_time is controllable."""
    pos = Position(
        symbol="BTCUSDT",
        size=size,
        entry_price=entry_price,
        entry_time=entry_time,
        highest_close=entry_price,
        trailing_stop_price=Decimal("0"),
        hard_stop_price=hard_stop_price,
        scale_in_count=0,
    )
    return Portfolio(
        usdt_balance=Decimal("5000"),
        positions={"BTCUSDT": pos},
        equity=Decimal("5000") + size * entry_price,
        open_trades_count=1,
    )


def _mr_candles_at(start: datetime, n: int = 50, close: float = 50000.0):
    """Candles starting at *start* with 15 min intervals."""
    dates = pd.date_range(start, periods=n, freq="15min")
    return pd.DataFrame(
        {
            "open": [close] * n,
            "high": [close + 10] * n,
            "low": [close - 10] * n,
            "close": [close] * n,
            "volume": [100.0] * n,
        },
        index=dates,
    )


def _mr_md_at(start: datetime, close: float = 50000.0) -> MarketData:
    return MarketData(
        symbol="BTCUSDT",
        timeframe="15m",
        candles=_mr_candles_at(start, close=close),
        indicators={},
    )


class TestCheckStops_TimeStop:
    """Mean-reversion time stop – exit after N candles if still in position."""

    def test_time_stop_fires_after_16_candles(self):
        """Position held for 16+ candles → TIME_STOP exit."""
        entry = datetime(2024, 1, 1, 0, 0)
        # Last candle at entry + 50×15min = entry + 12.5h → 50 candles > 16
        engine = _mr_engine(time_stop_candles=16, time_stop_cooldown_candles=4)
        md = _mr_md_at(entry)
        port = _mr_portfolio_with_position(entry_time=entry)
        result = engine.check_stops(md, port)
        assert result.stop_hit
        assert result.decision.exit_reason == ExitReason.TIME_STOP
        assert result.decision.action == RiskAction.CLOSE

    def test_time_stop_cooldown_candles(self):
        """Time-stop sets cooldown to time_stop_cooldown_candles."""
        entry = datetime(2024, 1, 1, 0, 0)
        engine = _mr_engine(time_stop_candles=16, time_stop_cooldown_candles=4)
        md = _mr_md_at(entry)
        port = _mr_portfolio_with_position(entry_time=entry)
        result = engine.check_stops(md, port)
        assert result.cooldown_candles == 4

    def test_time_stop_does_not_fire_before_threshold(self):
        """Position held for < 16 candles → no time stop."""
        entry = datetime(2024, 1, 1, 0, 0)
        engine = _mr_engine(time_stop_candles=16, time_stop_cooldown_candles=4)
        # Last candle at entry + 10×15min = entry + 2.5h → 10 candles < 16
        candles = _mr_candles_at(entry, n=11)
        md = MarketData(
            symbol="BTCUSDT",
            timeframe="15m",
            candles=candles,
            indicators={},
        )
        port = _mr_portfolio_with_position(entry_time=entry)
        result = engine.check_stops(md, port)
        assert not result.stop_hit

    def test_time_stop_exactly_at_threshold(self):
        """Position held for exactly 16 candles → fires."""
        entry = datetime(2024, 1, 1, 0, 0)
        engine = _mr_engine(time_stop_candles=16, time_stop_cooldown_candles=4)
        # 17 candles → last is at entry + 16×15min = 4h → exactly 16
        candles = _mr_candles_at(entry, n=17)
        md = MarketData(
            symbol="BTCUSDT",
            timeframe="15m",
            candles=candles,
            indicators={},
        )
        port = _mr_portfolio_with_position(entry_time=entry)
        result = engine.check_stops(md, port)
        assert result.stop_hit
        assert result.decision.exit_reason == ExitReason.TIME_STOP

    def test_time_stop_not_on_smart_hodler(self):
        """Smart Hodler should never fire time stop."""
        entry = datetime(2024, 1, 1, 0, 0)
        engine = _sh_engine()
        md = _mr_md_at(entry, close=50000.0)
        # Also add ATR indicator for SH trailing stop
        md.indicators["atr"] = _series(500.0)
        port = _mr_portfolio_with_position(entry_time=entry)
        result = engine.check_stops(md, port)
        # SH checks trailing stop, not time stop
        assert result.decision is None or result.decision.exit_reason != ExitReason.TIME_STOP

    def test_hard_stop_takes_priority_over_time_stop(self):
        """If both hard stop and time stop would fire, hard stop wins."""
        entry = datetime(2024, 1, 1, 0, 0)
        engine = _mr_engine(time_stop_candles=16, time_stop_cooldown_candles=4)
        md = _mr_md_at(entry, close=40000.0)
        port = _mr_portfolio_with_position(
            entry_time=entry,
            hard_stop_price=Decimal("45000"),
        )
        result = engine.check_stops(md, port)
        assert result.stop_hit
        assert result.decision.exit_reason == ExitReason.HARD_STOP

    def test_time_stop_decision_has_correct_size_and_price(self):
        """Decision carries position size and current close (losing position)."""
        entry = datetime(2024, 1, 1, 0, 0)
        engine = _mr_engine(time_stop_candles=16, time_stop_cooldown_candles=4)
        md = _mr_md_at(entry, close=49000.0)
        port = _mr_portfolio_with_position(entry_time=entry)
        result = engine.check_stops(md, port)
        assert result.decision.size == Decimal("0.1")
        assert result.decision.price == Decimal("49000.0")

    def test_custom_time_stop_candles(self):
        """Custom time_stop_candles=5, position held 6 candles → fires."""
        entry = datetime(2024, 1, 1, 0, 0)
        engine = _mr_engine(time_stop_candles=5, time_stop_cooldown_candles=2)
        # 7 candles → last at entry + 6×15min → 6 candles ≥ 5
        candles = _mr_candles_at(entry, n=7)
        md = MarketData(
            symbol="BTCUSDT",
            timeframe="15m",
            candles=candles,
            indicators={},
        )
        port = _mr_portfolio_with_position(entry_time=entry)
        result = engine.check_stops(md, port)
        assert result.stop_hit
        assert result.cooldown_candles == 2


# ── SHORT Kill Switch (5.9.1) ────────────────────────────────────────────────


class TestShortKillSwitch:
    """Signal.SHORT with shorts disabled → HOLD immediately."""

    def test_short_disabled_returns_hold(self):
        engine = _bg_engine(shorts_enabled=False)
        md = _market_data()
        dec = engine.evaluate(Signal.SHORT, md, _empty_portfolio())
        assert dec.action == RiskAction.HOLD

    def test_short_disabled_no_size_or_symbol(self):
        engine = _bg_engine(shorts_enabled=False)
        md = _market_data()
        dec = engine.evaluate(Signal.SHORT, md, _empty_portfolio())
        assert dec.size == Decimal("0")
        assert dec.symbol == ""

    def test_short_disabled_does_not_increment_daily_opens(self):
        engine = _bg_engine(shorts_enabled=False)
        md = _market_data()
        engine.evaluate(Signal.SHORT, md, _empty_portfolio())
        assert engine.get_daily_opens() == 0


# ── SHORT Open (5.9.2) ───────────────────────────────────────────────────────


class TestShortOpen:
    """Signal.SHORT with shorts enabled, no existing position → OPEN."""

    def test_short_open_returns_open_action(self):
        engine = _bg_engine()
        md = _market_data()
        dec = engine.evaluate(Signal.SHORT, md, _empty_portfolio())
        assert dec.action == RiskAction.OPEN

    def test_short_open_has_short_side(self):
        engine = _bg_engine()
        md = _market_data()
        dec = engine.evaluate(Signal.SHORT, md, _empty_portfolio())
        assert dec.side == PositionSide.SHORT

    def test_short_open_correct_size(self):
        """10000 × 0.50 / 50000 = 0.1 BTC."""
        engine = _bg_engine()
        md = _market_data(close=50000.0)
        dec = engine.evaluate(Signal.SHORT, md, _empty_portfolio(Decimal("10000")))
        assert dec.size == Decimal("0.1")

    def test_short_open_symbol(self):
        engine = _bg_engine()
        md = _market_data()
        dec = engine.evaluate(Signal.SHORT, md, _empty_portfolio())
        assert dec.symbol == "BTCUSDT"

    def test_short_open_hard_stop_above_entry(self):
        """Hard stop = 50000 × (1 + 0.05) = 52500."""
        engine = _bg_engine()
        md = _market_data(close=50000.0)
        dec = engine.evaluate(Signal.SHORT, md, _empty_portfolio())
        assert dec.hard_stop_price == Decimal("52500")

    def test_short_open_trailing_stop_above_entry(self):
        """Trailing stop = 50000 + (500 × 2.0) = 51000."""
        engine = _bg_engine()
        md = _market_data(close=50000.0, atr=500.0)
        dec = engine.evaluate(Signal.SHORT, md, _empty_portfolio())
        assert dec.trailing_stop_price == Decimal("51000")

    def test_short_open_increments_daily_opens(self):
        engine = _bg_engine()
        md = _market_data()
        engine.evaluate(Signal.SHORT, md, _empty_portfolio())
        assert engine.get_daily_opens() == 1


# ── SHORT No Scale-In (5.9.3) ────────────────────────────────────────────────


class TestShortNoScaleIn:
    """Signal.SHORT with existing short position → HOLD (no scale-in)."""

    def test_short_with_existing_short_returns_hold(self):
        engine = _bg_engine()
        md = _market_data()
        port = _portfolio_with_short_position()
        dec = engine.evaluate(Signal.SHORT, md, port)
        assert dec.action == RiskAction.HOLD

    def test_short_with_existing_long_still_opens(self):
        """Existing LONG at same symbol does NOT block SHORT open."""
        engine = _bg_engine()
        md = _market_data()
        port = _portfolio_with_position()  # LONG position
        dec = engine.evaluate(Signal.SHORT, md, port)
        assert dec.action == RiskAction.OPEN
        assert dec.side == PositionSide.SHORT


# ── SHORT Hard Stop (5.9.4) ──────────────────────────────────────────────────


class TestShortHardStop:
    """Hard stop for shorts is ABOVE entry: entry × (1 + hard_stop_pct)."""

    @pytest.mark.parametrize(
        "close,expected_stop",
        [
            (50000.0, Decimal("52500")),       # 50000 × 1.05
            (30000.0, Decimal("31500")),       # 30000 × 1.05
            (100.0, Decimal("105")),           # 100 × 1.05
        ],
    )
    def test_hard_stop_at_various_prices(self, close, expected_stop):
        engine = _bg_engine()
        md = _market_data(close=close)
        dec = engine.evaluate(Signal.SHORT, md, _empty_portfolio())
        assert dec.hard_stop_price == expected_stop

    def test_hard_stop_custom_pct(self):
        """hard_stop_pct=0.10 → 50000 × 1.10 = 55000."""
        engine = _bg_engine(hard_stop_pct=0.10)
        md = _market_data(close=50000.0)
        dec = engine.evaluate(Signal.SHORT, md, _empty_portfolio())
        assert dec.hard_stop_price == Decimal("55000")

    def test_hard_stop_always_above_entry(self):
        engine = _bg_engine()
        md = _market_data(close=42000.0)
        dec = engine.evaluate(Signal.SHORT, md, _empty_portfolio())
        assert dec.hard_stop_price > Decimal("42000")


# ── SHORT Trailing Stop (5.9.5) ──────────────────────────────────────────────


class TestShortTrailingStop:
    """Trailing stop for shorts is ABOVE entry: entry + (ATR × mult)."""

    @pytest.mark.parametrize(
        "close,atr,expected_stop",
        [
            (50000.0, 500.0, Decimal("51000")),   # 50000 + 500×2.0
            (50000.0, 1000.0, Decimal("52000")),  # 50000 + 1000×2.0
            (50000.0, 250.0, Decimal("50500")),   # 50000 + 250×2.0
        ],
    )
    def test_trailing_stop_various_atr(self, close, atr, expected_stop):
        engine = _bg_engine()
        md = _market_data(close=close, atr=atr)
        dec = engine.evaluate(Signal.SHORT, md, _empty_portfolio())
        assert dec.trailing_stop_price == expected_stop

    def test_trailing_stop_no_atr_returns_zero(self):
        """Missing ATR indicator → trailing stop = 0."""
        engine = _bg_engine()
        md = _market_data(close=50000.0, include_atr=False)
        dec = engine.evaluate(Signal.SHORT, md, _empty_portfolio())
        assert dec.trailing_stop_price == Decimal("0")

    def test_trailing_stop_nan_atr_returns_zero(self):
        """NaN ATR value → trailing stop = 0."""
        engine = _bg_engine()
        md = _market_data(close=50000.0)
        md.indicators["atr"].iloc[-1] = float("nan")
        dec = engine.evaluate(Signal.SHORT, md, _empty_portfolio())
        assert dec.trailing_stop_price == Decimal("0")

    def test_trailing_stop_always_above_entry(self):
        engine = _bg_engine()
        md = _market_data(close=42000.0, atr=800.0)
        dec = engine.evaluate(Signal.SHORT, md, _empty_portfolio())
        assert dec.trailing_stop_price > Decimal("42000")


# ── Helper: short position with configurable stops ──────────────────────────


def _short_portfolio(
    symbol="BTCUSDT",
    size=Decimal("0.1"),
    entry_price=Decimal("50000"),
    hard_stop_price=Decimal("52500"),
    trailing_stop_price=Decimal("0"),
    lowest_close=None,
    balance=Decimal("5000"),
) -> Portfolio:
    """Portfolio with a SHORT position and configurable stop levels."""
    pos = Position(
        symbol=symbol,
        side=PositionSide.SHORT,
        size=size,
        entry_price=entry_price,
        entry_time=datetime(2026, 2, 17, 10, 0),
        highest_close=entry_price,
        lowest_close=lowest_close if lowest_close is not None else entry_price,
        trailing_stop_price=trailing_stop_price,
        hard_stop_price=hard_stop_price,
    )
    return Portfolio(
        usdt_balance=balance,
        positions={symbol: pos},
        equity=balance + size * entry_price,
        open_trades_count=1,
    )


# ── check_stops() — SHORT hard stop (5.9.6) ─────────────────────────────────


class TestCheckStops_ShortHardStop:
    """Hard stop for shorts fires when close > hard_stop_price."""

    def test_hard_stop_fires_above_stop(self):
        engine = _bg_engine()
        md = _market_data(close=53000.0, atr=500.0)
        port = _short_portfolio(hard_stop_price=Decimal("52500"))
        result = engine.check_stops(md, port)
        assert result.stop_hit is True
        assert result.decision.action == RiskAction.CLOSE
        assert result.decision.exit_reason == ExitReason.HARD_STOP
        assert result.decision.side == PositionSide.SHORT
        assert result.decision.size == Decimal("0.1")

    def test_hard_stop_fires_just_above(self):
        engine = _bg_engine()
        md = _market_data(close=52501.0, atr=500.0)
        port = _short_portfolio(hard_stop_price=Decimal("52500"))
        result = engine.check_stops(md, port)
        assert result.stop_hit is True

    def test_hard_stop_does_not_fire_at_equal(self):
        """close == hard_stop → no violation (strict >)."""
        engine = _bg_engine()
        md = _market_data(close=52500.0, atr=500.0)
        # lowest_close set near close so trailing (52500+1000=53500) stays above
        port = _short_portfolio(
            hard_stop_price=Decimal("52500"),
            lowest_close=Decimal("52500"),
        )
        result = engine.check_stops(md, port)
        assert result.stop_hit is False

    def test_hard_stop_cooldown_set(self):
        engine = _bg_engine(cooldown_candles=8)
        md = _market_data(close=53000.0, atr=500.0)
        port = _short_portfolio(hard_stop_price=Decimal("52500"))
        result = engine.check_stops(md, port)
        assert result.cooldown_candles == 8

    def test_exit_price_is_close(self):
        engine = _bg_engine()
        md = _market_data(close=53000.0, atr=500.0)
        port = _short_portfolio(hard_stop_price=Decimal("52500"))
        result = engine.check_stops(md, port)
        assert result.decision.price == Decimal("53000.0")


# ── check_stops() — SHORT hard stop does NOT fire when favourable (5.9.7) ───


class TestCheckStops_ShortHardStopFavourable:
    """When price moves in our favour (down), hard stop does not fire."""

    def test_close_below_entry_no_stop(self):
        engine = _bg_engine()
        md = _market_data(close=48000.0, atr=500.0)
        port = _short_portfolio(hard_stop_price=Decimal("52500"))
        result = engine.check_stops(md, port)
        assert result.stop_hit is False
        assert result.decision is None

    def test_close_well_below_entry_no_stop(self):
        engine = _bg_engine()
        md = _market_data(close=40000.0, atr=500.0)
        port = _short_portfolio(hard_stop_price=Decimal("52500"))
        result = engine.check_stops(md, port)
        assert result.stop_hit is False

    def test_close_between_entry_and_stop_no_fire(self):
        """close=51000 is above entry but below stop 52500 → no fire."""
        engine = _bg_engine()
        md = _market_data(close=51000.0, atr=500.0)
        port = _short_portfolio(hard_stop_price=Decimal("52500"))
        result = engine.check_stops(md, port)
        assert result.stop_hit is False


# ── check_stops() — SHORT trailing stop ratchet (5.9.8) ─────────────────────


class TestCheckStops_ShortTrailingRatchet:
    """Trailing stop for shorts ratchets downward; lowest_close tracks minimum."""

    def test_lowest_close_ratchets_down(self):
        """close < position.lowest_close → result reflects new low."""
        engine = _bg_engine()
        md = _market_data(close=48000.0, atr=500.0)
        port = _short_portfolio(lowest_close=Decimal("50000"))
        result = engine.check_stops(md, port)
        assert result.lowest_close == Decimal("48000.0")

    def test_lowest_close_does_not_rise(self):
        """close > position.lowest_close → lowest remains unchanged."""
        engine = _bg_engine()
        md = _market_data(close=49000.0, atr=500.0)
        port = _short_portfolio(lowest_close=Decimal("47000"))
        result = engine.check_stops(md, port)
        assert result.lowest_close == Decimal("47000")

    def test_trailing_stop_value(self):
        """trailing = lowest_close + (ATR × mult) = 48000 + 1000 = 49000."""
        engine = _bg_engine()
        md = _market_data(close=48000.0, atr=500.0)
        # lowest_close will be min(50000, 48000) = 48000
        port = _short_portfolio(lowest_close=Decimal("50000"))
        result = engine.check_stops(md, port)
        # 48000 + (500 × 2.0) = 49000
        assert result.trailing_stop_price == Decimal("49000")

    def test_trailing_stop_ratchets_down_with_new_low(self):
        """New low → trailing stop moves down."""
        engine = _bg_engine()
        md = _market_data(close=46000.0, atr=500.0)
        port = _short_portfolio(lowest_close=Decimal("50000"))
        result = engine.check_stops(md, port)
        # lowest=46000, trailing = 46000 + 1000 = 47000
        assert result.trailing_stop_price == Decimal("47000")

    def test_trailing_stop_never_rises(self):
        """Even if computed trailing is higher, existing stop is kept."""
        engine = _bg_engine()
        md = _market_data(close=49000.0, atr=500.0)
        # Existing trailing=47500, lowest=47000
        # New lowest = min(47000, 49000) = 47000
        # Computed trailing = 47000 + 1000 = 48000
        # existing = 47500 → min(48000, 47500) = 47500
        port = _short_portfolio(
            lowest_close=Decimal("47000"),
            trailing_stop_price=Decimal("47500"),
        )
        result = engine.check_stops(md, port)
        assert result.trailing_stop_price == Decimal("47500")

    def test_no_atr_graceful(self):
        """Missing ATR → trailing_stop stays at 0 / doesn't fire."""
        engine = _bg_engine()
        md = _market_data(close=48000.0, include_atr=False)
        port = _short_portfolio(lowest_close=Decimal("50000"))
        result = engine.check_stops(md, port)
        assert result.stop_hit is False


# ── check_stops() — SHORT trailing stop fires (5.9.9) ───────────────────────


class TestCheckStops_ShortTrailingFire:
    """Trailing stop for shorts fires when close > trailing_stop_price."""

    def test_close_above_trailing_triggers_exit(self):
        engine = _bg_engine()
        md = _market_data(close=48500.0, atr=500.0)
        port = _short_portfolio(
            lowest_close=Decimal("46000"),
            trailing_stop_price=Decimal("48000"),
        )
        result = engine.check_stops(md, port)
        assert result.stop_hit is True
        assert result.decision.action == RiskAction.CLOSE
        assert result.decision.exit_reason == ExitReason.TRAILING_STOP
        assert result.decision.side == PositionSide.SHORT
        assert result.decision.size == Decimal("0.1")

    def test_close_equals_trailing_no_exit(self):
        """close == trailing → no violation (strict >)."""
        engine = _bg_engine()
        md = _market_data(close=48000.0, atr=500.0)
        # lowest=47000 → computed trailing=47000+1000=48000, min(48000,48000)=48000
        port = _short_portfolio(
            lowest_close=Decimal("47000"),
            trailing_stop_price=Decimal("48000"),
        )
        result = engine.check_stops(md, port)
        assert result.stop_hit is False

    def test_close_below_trailing_no_exit(self):
        engine = _bg_engine()
        md = _market_data(close=47000.0, atr=500.0)
        port = _short_portfolio(
            lowest_close=Decimal("46000"),
            trailing_stop_price=Decimal("48000"),
        )
        result = engine.check_stops(md, port)
        assert result.stop_hit is False

    def test_exit_price_is_close(self):
        engine = _bg_engine()
        md = _market_data(close=49000.0, atr=500.0)
        port = _short_portfolio(
            lowest_close=Decimal("46000"),
            trailing_stop_price=Decimal("48000"),
        )
        result = engine.check_stops(md, port)
        assert result.decision.price == Decimal("49000.0")


# ── check_stops() — SHORT break-even ratchet (5.9.10) ───────────────────────


class TestCheckStops_ShortBreakevenRatchet:
    """Break-even ratchet for shorts moves hard stop DOWN as profit grows.

    Defaults: breakeven_trigger_pct=0.02, lockin_trigger_pct=0.04, lockin_stop_pct=0.01
    Entry=50000, initial hard_stop=52500.
    """

    def test_below_breakeven_trigger_no_ratchet(self):
        """1% profit (close=49500) — below 2% trigger, stop stays at 52500."""
        engine = _bg_engine()
        md = _market_data(close=49500.0, atr=500.0)
        port = _short_portfolio(hard_stop_price=Decimal("52500"))
        result = engine.check_stops(md, port)
        assert result.hard_stop_price == Decimal("52500")
        assert result.stop_hit is False

    def test_breakeven_trigger_ratchets_to_entry(self):
        """2% profit (close=49000) — ratchets hard stop to entry (50000)."""
        engine = _bg_engine()
        md = _market_data(close=49000.0, atr=500.0)
        port = _short_portfolio(hard_stop_price=Decimal("52500"))
        result = engine.check_stops(md, port)
        assert result.hard_stop_price == Decimal("50000")
        assert result.stop_hit is False

    def test_between_breakeven_and_lockin(self):
        """3% profit (close=48500) — still breakeven tier, stop=50000."""
        engine = _bg_engine()
        md = _market_data(close=48500.0, atr=500.0)
        port = _short_portfolio(hard_stop_price=Decimal("52500"))
        result = engine.check_stops(md, port)
        assert result.hard_stop_price == Decimal("50000")

    def test_lockin_trigger_ratchets_below_entry(self):
        """4% profit (close=48000) — ratchets to entry*(1-0.01) = 49500."""
        engine = _bg_engine()
        md = _market_data(close=48000.0, atr=500.0)
        port = _short_portfolio(hard_stop_price=Decimal("52500"))
        result = engine.check_stops(md, port)
        assert result.hard_stop_price == Decimal("49500.00")
        assert result.stop_hit is False

    def test_deep_profit_stays_at_lockin(self):
        """8% profit (close=46000) — still lock-in tier, stop=49500."""
        engine = _bg_engine()
        md = _market_data(close=46000.0, atr=500.0)
        port = _short_portfolio(hard_stop_price=Decimal("52500"))
        result = engine.check_stops(md, port)
        assert result.hard_stop_price == Decimal("49500.00")


# ── check_stops() — SHORT ratchet never rises back (5.9.11) ─────────────────


class TestCheckStops_ShortRatchetNeverRisesBack:
    """Once ratcheted down, the short hard stop never moves back UP."""

    def test_ratcheted_stop_stays_after_price_rises(self):
        """Portfolio at lockin stop (49500); close=49000 (2% profit, breakeven
        tier) — computed new_stop=50000 is ABOVE 49500, so guard blocks it."""
        engine = _bg_engine()
        md = _market_data(close=49000.0, atr=500.0)
        # hard_stop already ratcheted to 49500 (lockin) in a prior tick
        port = _short_portfolio(hard_stop_price=Decimal("49500"))
        result = engine.check_stops(md, port)
        # 2% profit → breakeven tier → new_stop=50000, but 50000 > 49500
        # so guard (new_stop < hard_stop) fails → stop stays at 49500
        assert result.hard_stop_price == Decimal("49500")
        assert result.stop_hit is False

    def test_ratcheted_stop_stays_at_breakeven(self):
        """Portfolio at breakeven stop (50000); close=49900 (0.2% profit) → stays."""
        engine = _bg_engine()
        md = _market_data(close=49900.0, atr=500.0)
        port = _short_portfolio(hard_stop_price=Decimal("50000"))
        result = engine.check_stops(md, port)
        assert result.hard_stop_price == Decimal("50000")
        assert result.stop_hit is False

    def test_loss_does_not_move_stop_up(self):
        """In loss (close=50500 > entry); hard_stop already 49500 → stays 49500."""
        engine = _bg_engine()
        md = _market_data(close=50500.0, atr=500.0)
        port = _short_portfolio(hard_stop_price=Decimal("49500"))
        result = engine.check_stops(md, port)
        # profit_pct = (50000-50500)/50000 = -0.01 → below triggers
        # new_stop stays at 49500, guard fails (not < 49500) → no ratchet
        assert result.hard_stop_price == Decimal("49500")
        # 50500 > 49500 fires the hard stop!
        assert result.stop_hit is True
        assert result.decision.exit_reason == ExitReason.HARD_STOP
