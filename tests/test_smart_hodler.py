"""
Tests for the Smart Hodler strategy signal logic.

Covers:
    - Initialization and reset
    - BUY path (all 7 conditions, each condition individually false)
    - SELL_FULL path (EMA death cross, consecutive candles below EMA200)
    - SELL_HALF path (momentum fade: close < EMA50 + ADX falling)
    - Signal priority (SELL_FULL > SELL_HALF > BUY > HOLD)
    - State routing (FLAT, POSITION, REDUCED, COOLDOWN)
    - Edge cases (NaN indicators, missing secondary data, insufficient data)
"""

import math
from decimal import Decimal
from unittest.mock import MagicMock

import numpy as np
import pandas as pd
import pytest

from core.models import MarketData, Portfolio, Signal, StrategyConditions, StrategyState
from core.strategies import SmartHodler


# ── Helpers ──────────────────────────────────────────────────────────────────

DEFAULT_CONFIG = {
    "adx_threshold": 25,
    "rsi_threshold": 45,
    "exit_confirmation_candles": 2,
    "adx_falling_lookback": 3,
    "cooldown_candles": 16,
}

N = 50  # Number of bars for primary data
N_1H = 15  # Number of bars for secondary (hourly) data


def _make_series(value, length=N):
    """Create a constant pd.Series of the given length."""
    return pd.Series([value] * length, dtype=float)


def _make_rising_series(start, end, length=N):
    """Create a linearly rising pd.Series."""
    return pd.Series(np.linspace(start, end, length), dtype=float)


def _make_falling_series(start, end, length=N):
    """Create a linearly falling pd.Series."""
    return pd.Series(np.linspace(start, end, length), dtype=float)


def _make_candles(close_val=110.0, volume_val=6000.0, length=N):
    """Create a simple OHLCV DataFrame with constant values."""
    dates = pd.date_range("2024-01-01", periods=length, freq="15min")
    return pd.DataFrame(
        {
            "open": [close_val] * length,
            "high": [close_val + 1] * length,
            "low": [close_val - 1] * length,
            "close": [close_val] * length,
            "volume": [volume_val] * length,
        },
        index=dates,
    )


def _make_candles_1h(close_val=110.0, volume_val=24000.0, length=N_1H):
    """Create a simple 1H OHLCV DataFrame with constant values."""
    dates = pd.date_range("2024-01-01", periods=length, freq="1h")
    return pd.DataFrame(
        {
            "open": [close_val] * length,
            "high": [close_val + 1] * length,
            "low": [close_val - 1] * length,
            "close": [close_val] * length,
            "volume": [volume_val] * length,
        },
        index=dates,
    )


def _make_market_data(
    *,
    close=110.0,
    volume=6000.0,
    ema_50=105.0,
    ema_200=100.0,
    adx=30.0,
    volume_sma=5000.0,
    ema_21_1h_vals=None,
    rsi_1h_val=55.0,
    include_secondary=True,
    adx_series=None,
) -> MarketData:
    """
    Build a MarketData object with hand-crafted indicators.

    By default all 7 BUY conditions are met:
        1. ema_cross_bullish:        ema_50 (105) > ema_200 (100)  ✓
        2. adx_above_threshold:      adx (30) > 25                 ✓
        3. close_above_ema_fast:     close (110) > ema_50 (105)    ✓
        4. volume_above_average:     volume (6000) > volume_sma (5000) ✓
        5. hourly_ema_rising:        ema_21_1h rising              ✓
        6. hourly_rsi_above_threshold: rsi_1h (55) > 45            ✓
        7. session_filter_pass:      stubbed True                  ✓
    """
    candles = _make_candles(close_val=close, volume_val=volume)

    # ADX as a series (needed for _check_sell_half lookback)
    if adx_series is None:
        adx_series = _make_series(adx)

    primary_indicators = {
        "ema_50": _make_series(ema_50),
        "ema_200": _make_series(ema_200),
        "adx": adx_series,
        "volume_sma": _make_series(volume_sma),
    }

    kwargs = dict(
        symbol="BTCUSDT",
        timeframe="15m",
        candles=candles,
        indicators=primary_indicators,
    )

    if include_secondary:
        candles_1h = _make_candles_1h()

        # Hourly EMA 21 – rising by default
        if ema_21_1h_vals is None:
            ema_21_1h_vals = _make_rising_series(100, 110, N_1H)
        elif isinstance(ema_21_1h_vals, (int, float)):
            ema_21_1h_vals = _make_series(ema_21_1h_vals, N_1H)

        kwargs.update(
            secondary_timeframe="1h",
            secondary_candles=candles_1h,
            secondary_indicators={
                "ema_21": ema_21_1h_vals,
                "rsi": _make_series(rsi_1h_val, N_1H),
            },
        )

    return MarketData(**kwargs)


def _portfolio() -> Portfolio:
    """Minimal Portfolio mock."""
    return MagicMock(spec=Portfolio)


# ── Initialization & Reset ───────────────────────────────────────────────────


class TestSmartHodlerInit:
    """Verify constructor defaults and reset behavior."""

    def test_name_returns_smart_hodler(self):
        s = SmartHodler(DEFAULT_CONFIG)
        assert s.name == "smart_hodler"

    def test_initial_state_is_flat(self):
        s = SmartHodler(DEFAULT_CONFIG)
        assert s.state == StrategyState.FLAT

    def test_initial_conditions_all_false(self):
        s = SmartHodler(DEFAULT_CONFIG)
        assert not s.conditions.all_buy_conditions_met

    def test_initial_consecutive_counter_zero(self):
        s = SmartHodler(DEFAULT_CONFIG)
        assert s._consecutive_below_ema200 == 0

    def test_reset_clears_state_and_counter(self):
        s = SmartHodler(DEFAULT_CONFIG)
        s._state = StrategyState.POSITION
        s._consecutive_below_ema200 = 5
        s.reset()
        assert s.state == StrategyState.FLAT
        assert s._consecutive_below_ema200 == 0

    def test_reset_clears_conditions(self):
        s = SmartHodler(DEFAULT_CONFIG)
        md = _make_market_data()
        s.decide(md, _portfolio())
        assert s.conditions.ema_cross_bullish  # just confirm it was set
        s.reset()
        assert not s.conditions.ema_cross_bullish

    def test_import_from_package(self):
        """SmartHodler is importable from core.strategies."""
        from core.strategies import SmartHodler as Imported

        assert Imported is SmartHodler


# ── BUY Signal (FLAT state) ─────────────────────────────────────────────────


class TestBuySignal:
    """BUY requires all 7 conditions met AND state in {FLAT, REDUCED}."""

    def test_buy_all_conditions_met(self):
        s = SmartHodler(DEFAULT_CONFIG)
        md = _make_market_data()
        assert s.decide(md, _portfolio()) == Signal.BUY

    def test_buy_conditions_snapshot_updated(self):
        s = SmartHodler(DEFAULT_CONFIG)
        md = _make_market_data()
        s.decide(md, _portfolio())
        c = s.conditions
        assert c.ema_cross_bullish
        assert c.adx_above_threshold
        assert c.close_above_ema_fast
        assert c.volume_above_average
        assert c.hourly_ema_rising
        assert c.hourly_rsi_above_threshold
        assert c.session_filter_pass

    # ── Each condition individually false → HOLD ─────────────────────────

    def test_hold_when_ema_cross_bearish(self):
        """EMA50 < EMA200 → ema_cross_bullish = False."""
        s = SmartHodler(DEFAULT_CONFIG)
        md = _make_market_data(ema_50=95.0, ema_200=100.0)
        assert s.decide(md, _portfolio()) == Signal.HOLD

    def test_hold_when_adx_below_threshold(self):
        """ADX below threshold → adx_above_threshold = False."""
        s = SmartHodler(DEFAULT_CONFIG)
        md = _make_market_data(adx=20.0)
        assert s.decide(md, _portfolio()) == Signal.HOLD

    def test_hold_when_close_below_ema_fast(self):
        """Close below EMA 50 → close_above_ema_fast = False."""
        s = SmartHodler(DEFAULT_CONFIG)
        md = _make_market_data(close=100.0, ema_50=105.0)
        assert s.decide(md, _portfolio()) == Signal.HOLD

    def test_hold_when_volume_below_average(self):
        """Volume below SMA → volume_above_average = False."""
        s = SmartHodler(DEFAULT_CONFIG)
        md = _make_market_data(volume=3000.0, volume_sma=5000.0)
        assert s.decide(md, _portfolio()) == Signal.HOLD

    def test_hold_when_hourly_ema_not_rising(self):
        """Hourly EMA 21 flat/falling → hourly_ema_rising = False."""
        s = SmartHodler(DEFAULT_CONFIG)
        flat_ema = _make_series(100.0, N_1H)
        md = _make_market_data(ema_21_1h_vals=flat_ema)
        assert s.decide(md, _portfolio()) == Signal.HOLD

    def test_hold_when_hourly_rsi_below_threshold(self):
        """Hourly RSI below threshold → hourly_rsi_above_threshold = False."""
        s = SmartHodler(DEFAULT_CONFIG)
        md = _make_market_data(rsi_1h_val=30.0)
        assert s.decide(md, _portfolio()) == Signal.HOLD

    def test_buy_from_reduced_state(self):
        """BUY is allowed from REDUCED state when all conditions met."""
        s = SmartHodler(DEFAULT_CONFIG)
        s._state = StrategyState.REDUCED
        md = _make_market_data()
        assert s.decide(md, _portfolio()) == Signal.BUY


# ── SELL_FULL Signal ─────────────────────────────────────────────────────────


class TestSellFullSignal:
    """SELL_FULL: EMA death cross or N consecutive closes below EMA 200."""

    def test_sell_full_on_ema_death_cross(self):
        """EMA 50 < EMA 200 triggers SELL_FULL from POSITION."""
        s = SmartHodler(DEFAULT_CONFIG)
        s._state = StrategyState.POSITION
        md = _make_market_data(ema_50=95.0, ema_200=100.0)
        assert s.decide(md, _portfolio()) == Signal.SELL_FULL

    def test_sell_full_on_consecutive_below_ema200(self):
        """Close below EMA 200 for exit_confirmation_candles → SELL_FULL."""
        s = SmartHodler(DEFAULT_CONFIG)
        s._state = StrategyState.POSITION

        # First call: close (90) < ema_200 (100) → counter becomes 1 → not enough
        md = _make_market_data(close=90.0, ema_50=105.0, ema_200=100.0)
        result1 = s.decide(md, _portfolio())
        assert result1 == Signal.HOLD  # Only 1 candle so far
        assert s._consecutive_below_ema200 == 1

        # Second call: counter becomes 2 >= exit_confirmation_candles → SELL_FULL
        result2 = s.decide(md, _portfolio())
        assert result2 == Signal.SELL_FULL
        assert s._consecutive_below_ema200 == 2

    def test_consecutive_counter_resets_on_close_above(self):
        """Counter resets to 0 when close goes back above EMA 200."""
        s = SmartHodler(DEFAULT_CONFIG)
        s._state = StrategyState.POSITION

        # One candle below
        md_below = _make_market_data(close=90.0, ema_50=105.0, ema_200=100.0)
        s.decide(md_below, _portfolio())
        assert s._consecutive_below_ema200 == 1

        # One candle above → resets
        md_above = _make_market_data(close=110.0, ema_50=105.0, ema_200=100.0)
        s.decide(md_above, _portfolio())
        assert s._consecutive_below_ema200 == 0

    def test_sell_full_from_reduced_state(self):
        """SELL_FULL is also reachable from REDUCED."""
        s = SmartHodler(DEFAULT_CONFIG)
        s._state = StrategyState.REDUCED
        md = _make_market_data(ema_50=95.0, ema_200=100.0)
        assert s.decide(md, _portfolio()) == Signal.SELL_FULL

    def test_sell_full_not_from_flat(self):
        """SELL_FULL is NOT produced when state is FLAT."""
        s = SmartHodler(DEFAULT_CONFIG)
        s._state = StrategyState.FLAT
        md = _make_market_data(ema_50=95.0, ema_200=100.0)
        # All buy conditions will fail (bearish cross), so HOLD
        assert s.decide(md, _portfolio()) == Signal.HOLD


# ── SELL_HALF Signal ─────────────────────────────────────────────────────────


class TestSellHalfSignal:
    """SELL_HALF: momentum fade = close < EMA50 + ADX falling."""

    def test_sell_half_momentum_fade(self):
        """Close < EMA50 AND ADX falling → SELL_HALF from POSITION."""
        s = SmartHodler(DEFAULT_CONFIG)
        s._state = StrategyState.POSITION

        # ADX: was 35 three bars ago, now 28 → falling
        adx_vals = _make_falling_series(35, 28)
        md = _make_market_data(
            close=100.0,  # below ema_50=105
            ema_50=105.0,
            ema_200=95.0,  # EMA50 > EMA200 so SELL_FULL doesn't trigger
            adx_series=adx_vals,
        )
        assert s.decide(md, _portfolio()) == Signal.SELL_HALF

    def test_hold_when_close_above_ema50(self):
        """Close >= EMA50 → no SELL_HALF even if ADX falling."""
        s = SmartHodler(DEFAULT_CONFIG)
        s._state = StrategyState.POSITION

        adx_vals = _make_falling_series(35, 28)
        md = _make_market_data(
            close=110.0,  # above ema_50=105
            ema_50=105.0,
            ema_200=95.0,
            adx_series=adx_vals,
        )
        assert s.decide(md, _portfolio()) == Signal.HOLD

    def test_hold_when_adx_not_falling(self):
        """ADX rising → no SELL_HALF even if close < EMA50."""
        s = SmartHodler(DEFAULT_CONFIG)
        s._state = StrategyState.POSITION

        adx_vals = _make_rising_series(25, 35)
        md = _make_market_data(
            close=100.0,  # below ema_50=105
            ema_50=105.0,
            ema_200=95.0,
            adx_series=adx_vals,
        )
        assert s.decide(md, _portfolio()) == Signal.HOLD

    def test_sell_half_not_from_reduced(self):
        """SELL_HALF is NOT produced from REDUCED state."""
        s = SmartHodler(DEFAULT_CONFIG)
        s._state = StrategyState.REDUCED

        adx_vals = _make_falling_series(35, 28)
        md = _make_market_data(
            close=100.0,
            ema_50=105.0,
            ema_200=95.0,
            adx_series=adx_vals,
        )
        # REDUCED can't SELL_HALF; and BUY conditions won't be all met
        # (close < ema_50 → close_above_ema_fast = False)
        assert s.decide(md, _portfolio()) == Signal.HOLD

    def test_sell_half_not_from_flat(self):
        """SELL_HALF is NOT produced from FLAT state."""
        s = SmartHodler(DEFAULT_CONFIG)
        s._state = StrategyState.FLAT

        adx_vals = _make_falling_series(35, 28)
        md = _make_market_data(
            close=100.0,
            ema_50=105.0,
            ema_200=95.0,
            adx_series=adx_vals,
        )
        assert s.decide(md, _portfolio()) == Signal.HOLD


# ── Signal Priority ──────────────────────────────────────────────────────────


class TestSignalPriority:
    """SELL_FULL wins over SELL_HALF wins over BUY."""

    def test_sell_full_beats_sell_half(self):
        """When both SELL_FULL and SELL_HALF conditions met, SELL_FULL wins."""
        s = SmartHodler(DEFAULT_CONFIG)
        s._state = StrategyState.POSITION

        # EMA death cross → SELL_FULL,  close < ema_50 + ADX falling → SELL_HALF
        adx_vals = _make_falling_series(35, 28)
        md = _make_market_data(
            close=90.0,
            ema_50=95.0,  # < ema_200 → death cross
            ema_200=100.0,
            adx_series=adx_vals,
        )
        assert s.decide(md, _portfolio()) == Signal.SELL_FULL

    def test_sell_full_beats_buy(self):
        """SELL_FULL priority is higher than BUY from REDUCED state."""
        s = SmartHodler(DEFAULT_CONFIG)
        s._state = StrategyState.REDUCED

        md = _make_market_data(ema_50=95.0, ema_200=100.0)
        # EMA cross bearish → SELL_FULL, and buy conditions fail anyway
        assert s.decide(md, _portfolio()) == Signal.SELL_FULL


# ── State Routing ────────────────────────────────────────────────────────────


class TestStateRouting:
    """Verify signal gating per strategy state."""

    def test_cooldown_always_hold(self):
        """COOLDOWN state always returns HOLD regardless of conditions."""
        s = SmartHodler(DEFAULT_CONFIG)
        s._state = StrategyState.COOLDOWN

        # Conditions that would normally trigger BUY
        md = _make_market_data()
        assert s.decide(md, _portfolio()) == Signal.HOLD

    def test_no_buy_from_position(self):
        """POSITION state cannot produce BUY (only SELL_FULL/SELL_HALF/HOLD)."""
        s = SmartHodler(DEFAULT_CONFIG)
        s._state = StrategyState.POSITION

        # All BUY conditions met, but state is POSITION
        md = _make_market_data()
        assert s.decide(md, _portfolio()) == Signal.HOLD

    def test_hold_from_flat_when_conditions_not_met(self):
        """FLAT + conditions not met → HOLD."""
        s = SmartHodler(DEFAULT_CONFIG)
        md = _make_market_data(adx=10.0)  # ADX too low
        assert s.decide(md, _portfolio()) == Signal.HOLD


# ── Edge Cases ───────────────────────────────────────────────────────────────


class TestEdgeCases:
    """NaN handling, missing data, and boundary conditions."""

    def test_hold_when_no_secondary_data(self):
        """Missing secondary timeframe → HOLD (conservative)."""
        s = SmartHodler(DEFAULT_CONFIG)
        md = _make_market_data(include_secondary=False)
        assert s.decide(md, _portfolio()) == Signal.HOLD

    def test_hold_when_primary_indicator_is_nan(self):
        """NaN in a primary indicator → HOLD."""
        s = SmartHodler(DEFAULT_CONFIG)
        md = _make_market_data()
        # Inject NaN into last value of ema_50
        md.indicators["ema_50"].iloc[-1] = float("nan")
        assert s.decide(md, _portfolio()) == Signal.HOLD

    def test_hold_when_primary_indicator_missing(self):
        """Missing required primary indicator key → HOLD."""
        s = SmartHodler(DEFAULT_CONFIG)
        md = _make_market_data()
        del md.indicators["adx"]
        assert s.decide(md, _portfolio()) == Signal.HOLD

    def test_hold_when_secondary_indicator_missing(self):
        """Missing required secondary indicator key → HOLD."""
        s = SmartHodler(DEFAULT_CONFIG)
        md = _make_market_data()
        del md.secondary_indicators["ema_21"]
        assert s.decide(md, _portfolio()) == Signal.HOLD

    def test_hold_when_hourly_ema_nan(self):
        """NaN in hourly EMA → hourly_ema_rising stays False → HOLD."""
        s = SmartHodler(DEFAULT_CONFIG)
        nan_ema = _make_series(float("nan"), N_1H)
        md = _make_market_data(ema_21_1h_vals=nan_ema)
        assert s.decide(md, _portfolio()) == Signal.HOLD

    def test_hold_when_hourly_rsi_nan(self):
        """NaN in hourly RSI → hourly_rsi_above_threshold stays False → HOLD."""
        s = SmartHodler(DEFAULT_CONFIG)
        md = _make_market_data(rsi_1h_val=float("nan"))
        assert s.decide(md, _portfolio()) == Signal.HOLD

    def test_hold_when_hourly_data_insufficient(self):
        """Less than lookback_bars (4) hourly candles → ema/rsi conditions false."""
        s = SmartHodler(DEFAULT_CONFIG)
        # Only 2 hourly values
        short_ema = _make_rising_series(100, 110, length=2)
        md = _make_market_data(ema_21_1h_vals=short_ema)
        # hourly_ema_rising will be False because len < 4
        assert s.decide(md, _portfolio()) == Signal.HOLD

    def test_adx_falling_lookback_insufficient(self):
        """Not enough ADX bars for lookback → SELL_HALF not triggered."""
        s = SmartHodler(DEFAULT_CONFIG)
        s._state = StrategyState.POSITION

        # Only 2 ADX values, but lookback=3 needs at least 4
        short_adx = pd.Series([35.0, 28.0], dtype=float)
        md = _make_market_data(
            close=100.0,
            ema_50=105.0,
            ema_200=95.0,
            adx_series=short_adx,
        )
        # SELL_HALF: lookback check fails → HOLD
        assert s.decide(md, _portfolio()) == Signal.HOLD

    def test_conditions_updated_every_call(self):
        """Conditions snapshot is refreshed on each decide() call."""
        s = SmartHodler(DEFAULT_CONFIG)

        # First call: all conditions met
        md1 = _make_market_data()
        s.decide(md1, _portfolio())
        assert s.conditions.adx_above_threshold is True

        # Second call: ADX below threshold
        md2 = _make_market_data(adx=10.0)
        s.decide(md2, _portfolio())
        assert s.conditions.adx_above_threshold is False

    def test_config_defaults_used_when_keys_missing(self):
        """Strategy works with empty config (uses defaults)."""
        s = SmartHodler({})
        md = _make_market_data()
        # Should not raise — defaults kick in
        result = s.decide(md, _portfolio())
        assert result == Signal.BUY


# ── Consecutive Counter Behavior ─────────────────────────────────────────────


class TestConsecutiveCounter:
    """Detailed tests for _consecutive_below_ema200 counter."""

    def test_counter_increments_when_below(self):
        s = SmartHodler(DEFAULT_CONFIG)
        md = _make_market_data(close=90.0, ema_200=100.0)
        s.decide(md, _portfolio())
        assert s._consecutive_below_ema200 == 1
        s.decide(md, _portfolio())
        assert s._consecutive_below_ema200 == 2

    def test_counter_resets_when_above(self):
        s = SmartHodler(DEFAULT_CONFIG)
        s._consecutive_below_ema200 = 5
        md = _make_market_data(close=110.0, ema_200=100.0)
        s.decide(md, _portfolio())
        assert s._consecutive_below_ema200 == 0

    def test_counter_resets_on_strategy_reset(self):
        s = SmartHodler(DEFAULT_CONFIG)
        s._consecutive_below_ema200 = 10
        s.reset()
        assert s._consecutive_below_ema200 == 0
