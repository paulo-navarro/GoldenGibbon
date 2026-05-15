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
    close=105.4,
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

    By default all 8 BUY conditions are met:
        1. ema_cross_bullish:        ema_50 (105) > ema_200 (100)  ✓
        2. adx_above_threshold:      adx (30) > 25                 ✓
        3. close_above_ema_fast:     close (105.4) > ema_50 (105)  ✓
        4. volume_above_average:     volume (6000) > volume_sma (5000) ✓
        5. hourly_ema_rising:        ema_21_1h rising              ✓
        6. hourly_rsi_above_threshold: rsi_1h (55) > 45            ✓
        7. pullback_near_ema:        |close - ema_50| / ema_50 ≤ 0.5%  ✓
        8. session_filter_pass:      True (no dead zones in default config)
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
        assert c.pullback_near_ema
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

    def test_hold_when_close_too_far_from_ema(self):
        """Close > 0.5% above EMA 50 → pullback_near_ema = False."""
        s = SmartHodler(DEFAULT_CONFIG)
        md = _make_market_data(close=110.0, ema_50=105.0)
        assert s.decide(md, _portfolio()) == Signal.HOLD
        assert s.conditions.pullback_near_ema is False

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
        """COOLDOWN state returns HOLD while countdown > 0."""
        s = SmartHodler(DEFAULT_CONFIG)
        s._state = StrategyState.COOLDOWN
        s._cooldown_remaining = 5  # still counting down

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


# ── State Transitions ────────────────────────────────────────────────────────


class TestStateTransitions:
    """Verify that decide() mutates self._state on signal emission."""

    def test_flat_buy_transitions_to_position(self):
        """FLAT + BUY signal → state becomes POSITION."""
        s = SmartHodler(DEFAULT_CONFIG)
        assert s.state == StrategyState.FLAT

        md = _make_market_data()
        signal = s.decide(md, _portfolio())
        assert signal == Signal.BUY
        assert s.state == StrategyState.POSITION

    def test_position_sell_full_transitions_to_cooldown(self):
        """POSITION + SELL_FULL → state becomes COOLDOWN."""
        s = SmartHodler(DEFAULT_CONFIG)
        s._state = StrategyState.POSITION

        md = _make_market_data(ema_50=95.0, ema_200=100.0)
        signal = s.decide(md, _portfolio())
        assert signal == Signal.SELL_FULL
        assert s.state == StrategyState.COOLDOWN

    def test_position_sell_full_sets_cooldown_remaining(self):
        """SELL_FULL from POSITION initialises cooldown counter."""
        s = SmartHodler(DEFAULT_CONFIG)
        s._state = StrategyState.POSITION

        md = _make_market_data(ema_50=95.0, ema_200=100.0)
        s.decide(md, _portfolio())
        assert s._cooldown_remaining == DEFAULT_CONFIG["cooldown_candles"]

    def test_position_sell_half_transitions_to_reduced(self):
        """POSITION + SELL_HALF → state becomes REDUCED."""
        s = SmartHodler(DEFAULT_CONFIG)
        s._state = StrategyState.POSITION

        adx_vals = _make_falling_series(35, 28)
        md = _make_market_data(
            close=100.0,
            ema_50=105.0,
            ema_200=95.0,
            adx_series=adx_vals,
        )
        signal = s.decide(md, _portfolio())
        assert signal == Signal.SELL_HALF
        assert s.state == StrategyState.REDUCED

    def test_reduced_buy_transitions_to_position(self):
        """REDUCED + BUY → state becomes POSITION."""
        s = SmartHodler(DEFAULT_CONFIG)
        s._state = StrategyState.REDUCED

        md = _make_market_data()
        signal = s.decide(md, _portfolio())
        assert signal == Signal.BUY
        assert s.state == StrategyState.POSITION

    def test_reduced_sell_full_transitions_to_cooldown(self):
        """REDUCED + SELL_FULL → state becomes COOLDOWN."""
        s = SmartHodler(DEFAULT_CONFIG)
        s._state = StrategyState.REDUCED

        md = _make_market_data(ema_50=95.0, ema_200=100.0)
        signal = s.decide(md, _portfolio())
        assert signal == Signal.SELL_FULL
        assert s.state == StrategyState.COOLDOWN
        assert s._cooldown_remaining == DEFAULT_CONFIG["cooldown_candles"]

    def test_hold_does_not_change_state(self):
        """HOLD signal leaves the state unchanged."""
        for state in (StrategyState.FLAT, StrategyState.POSITION, StrategyState.REDUCED):
            s = SmartHodler(DEFAULT_CONFIG)
            s._state = state
            md = _make_market_data(adx=10.0)  # ADX too low → HOLD
            signal = s.decide(md, _portfolio())
            assert signal == Signal.HOLD
            assert s.state == state

    def test_custom_cooldown_candles_config(self):
        """Cooldown counter uses the configured cooldown_candles value."""
        config = {**DEFAULT_CONFIG, "cooldown_candles": 8}
        s = SmartHodler(config)
        s._state = StrategyState.POSITION

        md = _make_market_data(ema_50=95.0, ema_200=100.0)
        s.decide(md, _portfolio())
        assert s._cooldown_remaining == 8


# ── Cooldown Countdown ───────────────────────────────────────────────────────


class TestCooldownCountdown:
    """Verify cooldown decrement, expiry, and re-evaluation behaviour."""

    def test_cooldown_decrements_each_call(self):
        """Each decide() in COOLDOWN decrements _cooldown_remaining by 1."""
        s = SmartHodler(DEFAULT_CONFIG)
        s._state = StrategyState.COOLDOWN
        s._cooldown_remaining = 3

        md = _make_market_data()
        s.decide(md, _portfolio())
        assert s._cooldown_remaining == 2
        assert s.state == StrategyState.COOLDOWN

        s.decide(md, _portfolio())
        assert s._cooldown_remaining == 1
        assert s.state == StrategyState.COOLDOWN

    def test_cooldown_holds_during_countdown(self):
        """Returns HOLD every call while cooldown is active."""
        s = SmartHodler(DEFAULT_CONFIG)
        s._state = StrategyState.COOLDOWN
        s._cooldown_remaining = 3

        md = _make_market_data()  # would trigger BUY if not in cooldown
        assert s.decide(md, _portfolio()) == Signal.HOLD
        assert s.decide(md, _portfolio()) == Signal.HOLD

    def test_cooldown_expires_to_flat(self):
        """When _cooldown_remaining reaches 0, state transitions to FLAT."""
        s = SmartHodler(DEFAULT_CONFIG)
        s._state = StrategyState.COOLDOWN
        s._cooldown_remaining = 1

        # Conditions that DON'T trigger BUY (so we can check state without
        # it immediately moving to POSITION)
        md = _make_market_data(adx=10.0)
        signal = s.decide(md, _portfolio())
        assert signal == Signal.HOLD
        assert s.state == StrategyState.FLAT

    def test_cooldown_expiry_re_evaluates_signals(self):
        """On the candle that expires cooldown, signals are re-evaluated."""
        s = SmartHodler(DEFAULT_CONFIG)
        s._state = StrategyState.COOLDOWN
        s._cooldown_remaining = 1

        # All BUY conditions met → should BUY and transition to POSITION
        md = _make_market_data()
        signal = s.decide(md, _portfolio())
        assert signal == Signal.BUY
        assert s.state == StrategyState.POSITION

    def test_full_cooldown_cycle(self):
        """Walk through all 16 candles of cooldown then re-entry."""
        config = {**DEFAULT_CONFIG, "cooldown_candles": 4}  # shorter for the test
        s = SmartHodler(config)
        s._state = StrategyState.POSITION

        # Trigger SELL_FULL → COOLDOWN
        md_sell = _make_market_data(ema_50=95.0, ema_200=100.0)
        assert s.decide(md_sell, _portfolio()) == Signal.SELL_FULL
        assert s.state == StrategyState.COOLDOWN
        assert s._cooldown_remaining == 4

        # 3 HOLD candles (remaining goes 4→3→2→1 during cooldown)
        md_buy = _make_market_data()  # conditions for BUY
        assert s.decide(md_buy, _portfolio()) == Signal.HOLD  # rem 3
        assert s.decide(md_buy, _portfolio()) == Signal.HOLD  # rem 2
        assert s.decide(md_buy, _portfolio()) == Signal.HOLD  # rem 1

        # 4th candle: cooldown expires, re-eval triggers BUY
        assert s.decide(md_buy, _portfolio()) == Signal.BUY
        assert s.state == StrategyState.POSITION

    def test_reset_clears_cooldown_remaining(self):
        """reset() zeroes _cooldown_remaining."""
        s = SmartHodler(DEFAULT_CONFIG)
        s._state = StrategyState.COOLDOWN
        s._cooldown_remaining = 10
        s.reset()
        assert s._cooldown_remaining == 0
        assert s.state == StrategyState.FLAT

    def test_initial_cooldown_remaining_is_zero(self):
        """Fresh strategy has _cooldown_remaining == 0."""
        s = SmartHodler(DEFAULT_CONFIG)
        assert s._cooldown_remaining == 0


# ── Session Filter ───────────────────────────────────────────────────────────

_DEAD_ZONES = [
    {"name": "Weekend", "start_utc": "Saturday 21:00", "end_utc": "Sunday 20:00"},
    {"name": "Overnight Gap", "start_utc": "21:00", "end_utc": "01:00"},
]


def _make_candles_at(start_dt, close_val=110.0, volume_val=6000.0, length=N):
    """Create candles whose *last* bar opens at *start_dt*."""
    dates = pd.date_range(end=start_dt, periods=length, freq="15min")
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


def _make_md_at_time(start_str, **kwargs):
    """Build a default-BUY MarketData with the last candle at *start_str*."""
    from datetime import datetime as _dt
    candle_time = pd.Timestamp(_dt.fromisoformat(start_str))
    candles = _make_candles_at(
        candle_time,
        close_val=kwargs.get("close", 105.4),
        volume_val=kwargs.get("volume", 6000.0),
    )
    candles_1h = _make_candles_1h()
    return MarketData(
        symbol="BTCUSDT",
        timeframe="15m",
        candles=candles,
        indicators={
            "ema_50": _make_series(kwargs.get("ema_50", 105.0)),
            "ema_200": _make_series(kwargs.get("ema_200", 100.0)),
            "adx": _make_series(kwargs.get("adx", 30.0)),
            "volume_sma": _make_series(kwargs.get("volume_sma", 5000.0)),
        },
        secondary_timeframe="1h",
        secondary_candles=candles_1h,
        secondary_indicators={
            "ema_21": _make_rising_series(100, 110, N_1H),
            "rsi": _make_series(55.0, N_1H),
        },
    )


class TestSessionFilter:
    """Verify session filter blocks BUY but not SELL during dead zones."""

    def _config_with_filter(self):
        return {
            **DEFAULT_CONFIG,
            "session_filter_enabled": True,
            "session_dead_zones": _DEAD_ZONES,
        }

    def test_buy_blocked_during_weekend_dead_zone(self):
        """Saturday 22:00 → dead zone → BUY conditions met but HOLD."""
        s = SmartHodler(self._config_with_filter())
        md = _make_md_at_time("2026-02-21T22:00:00")  # Saturday
        assert s.decide(md, _portfolio()) == Signal.HOLD
        assert s.conditions.session_filter_pass is False

    def test_buy_blocked_during_overnight_dead_zone(self):
        """Monday 22:00 → dead zone → BUY blocked."""
        s = SmartHodler(self._config_with_filter())
        md = _make_md_at_time("2026-02-16T22:00:00")  # Monday
        assert s.decide(md, _portfolio()) == Signal.HOLD
        assert s.conditions.session_filter_pass is False

    def test_buy_allowed_outside_dead_zone(self):
        """Wednesday 14:00 → open session → BUY fires."""
        s = SmartHodler(self._config_with_filter())
        md = _make_md_at_time("2026-02-18T14:00:00")  # Wednesday
        assert s.decide(md, _portfolio()) == Signal.BUY
        assert s.conditions.session_filter_pass is True

    def test_sell_not_blocked_during_dead_zone(self):
        """SELL signals fire even inside dead zones (protect capital)."""
        cfg = self._config_with_filter()
        s = SmartHodler(cfg)
        s._state = StrategyState.POSITION
        # Saturday 22:00: EMA death cross → SELL_FULL regardless of dead zone
        md = _make_md_at_time("2026-02-21T22:00:00", ema_50=95.0, ema_200=100.0)
        assert s.decide(md, _portfolio()) == Signal.SELL_FULL

    def test_filter_disabled_allows_buy_in_dead_zone(self):
        """session_filter_enabled=False → session_filter_pass=True always."""
        cfg = {**DEFAULT_CONFIG, "session_filter_enabled": False, "session_dead_zones": _DEAD_ZONES}
        s = SmartHodler(cfg)
        md = _make_md_at_time("2026-02-21T22:00:00")  # Saturday
        assert s.decide(md, _portfolio()) == Signal.BUY
        assert s.conditions.session_filter_pass is True

    def test_no_dead_zones_allows_buy(self):
        """No dead zones configured → session_filter_pass=True."""
        s = SmartHodler(DEFAULT_CONFIG)
        md = _make_md_at_time("2026-02-21T22:00:00")  # Saturday
        assert s.decide(md, _portfolio()) == Signal.BUY
