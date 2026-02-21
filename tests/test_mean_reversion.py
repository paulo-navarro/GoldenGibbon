"""
Tests for the Mean Reversion strategy signal logic.

Covers:
    - Initialization and reset
    - BUY path (all 7 conditions, each condition individually false)
    - SELL_FULL path (upper BB, RSI overbought, ADX regime shift)
    - SELL_HALF path (middle BB reversion)
    - Signal priority (SELL_FULL > SELL_HALF > BUY > HOLD)
    - State routing (FLAT, POSITION, REDUCED, COOLDOWN)
    - Edge cases (NaN indicators, missing secondary data, config defaults)
"""

import math
from unittest.mock import MagicMock

import numpy as np
import pandas as pd
import pytest

from core.models import (
    MarketData,
    MeanReversionConditions,
    Portfolio,
    Signal,
    StrategyState,
)
from core.strategies import MeanReversion


# ── Helpers ──────────────────────────────────────────────────────────────────

DEFAULT_CONFIG = {
    "adx_threshold": 25,
    "rsi_oversold": 30,
    "rsi_overbought": 70,
    "adx_regime_shift": 30,
    "volume_spike_multiplier": 1.5,
    "hourly_rsi_threshold": 35,
}

N = 50  # Primary bars
N_1H = 15  # Secondary (hourly) bars


def _make_series(value, length=N):
    """Create a constant pd.Series of the given length."""
    return pd.Series([value] * length, dtype=float)


def _make_candles(close_val=90.0, volume_val=8000.0, length=N):
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
    close=90.0,
    volume=8000.0,
    bb_lower=95.0,
    bb_middle=100.0,
    bb_upper=105.0,
    rsi=25.0,
    adx=20.0,
    volume_sma=5000.0,
    rsi_1h_val=50.0,
    ema_50_1h_val=100.0,
    close_1h=110.0,
    include_secondary=True,
) -> MarketData:
    """
    Build a MarketData object with hand-crafted indicators.

    By default all 7 BUY conditions are met:
        1. close_below_lower_bb:   close (90) <= bb_lower (95)     ✓
        2. rsi_oversold:           rsi (25) < 30                   ✓
        3. adx_below_threshold:    adx (20) < 25                   ✓
        4. volume_spike:           volume (8000) > 1.5 × 5000      ✓
        5. hourly_rsi_ok:          rsi_1h (50) > 35                ✓
        6. hourly_close_above_ema: close_1h (110) > ema_50_1h (100)✓
        7. session_filter_pass:    True (no dead zones in default config)
    """
    candles = _make_candles(close_val=close, volume_val=volume)

    primary_indicators = {
        "bb_lower": _make_series(bb_lower),
        "bb_middle": _make_series(bb_middle),
        "bb_upper": _make_series(bb_upper),
        "rsi": _make_series(rsi),
        "adx": _make_series(adx),
        "volume_sma": _make_series(volume_sma),
    }

    kwargs = dict(
        symbol="BTCUSDT",
        timeframe="15m",
        candles=candles,
        indicators=primary_indicators,
    )

    if include_secondary:
        candles_1h = _make_candles_1h(close_val=close_1h)
        kwargs.update(
            secondary_timeframe="1h",
            secondary_candles=candles_1h,
            secondary_indicators={
                "rsi": _make_series(rsi_1h_val, N_1H),
                "ema_50": _make_series(ema_50_1h_val, N_1H),
            },
        )

    return MarketData(**kwargs)


def _portfolio() -> Portfolio:
    """Minimal Portfolio mock."""
    return MagicMock(spec=Portfolio)


# ── Initialization & Reset ───────────────────────────────────────────────────


class TestMeanReversionInit:
    """Verify constructor defaults and reset behavior."""

    def test_name_returns_mean_reversion(self):
        s = MeanReversion(DEFAULT_CONFIG)
        assert s.name == "mean_reversion"

    def test_initial_state_is_flat(self):
        s = MeanReversion(DEFAULT_CONFIG)
        assert s.state == StrategyState.FLAT

    def test_initial_conditions_type(self):
        s = MeanReversion(DEFAULT_CONFIG)
        assert isinstance(s.conditions, MeanReversionConditions)

    def test_initial_conditions_all_false(self):
        s = MeanReversion(DEFAULT_CONFIG)
        assert not s.conditions.all_buy_conditions_met

    def test_reset_clears_state(self):
        s = MeanReversion(DEFAULT_CONFIG)
        s._state = StrategyState.POSITION
        s.reset()
        assert s.state == StrategyState.FLAT

    def test_reset_clears_conditions(self):
        s = MeanReversion(DEFAULT_CONFIG)
        md = _make_market_data()
        s.decide(md, _portfolio())
        assert s.conditions.close_below_lower_bb  # confirm it was set
        s.reset()
        assert not s.conditions.close_below_lower_bb

    def test_import_from_package(self):
        from core.strategies import MeanReversion as Imported

        assert Imported is MeanReversion


# ── BUY Signal (FLAT state) ─────────────────────────────────────────────────


class TestBuySignal:
    """BUY requires all 7 conditions met AND state == FLAT."""

    def test_buy_all_conditions_met(self):
        s = MeanReversion(DEFAULT_CONFIG)
        md = _make_market_data()
        assert s.decide(md, _portfolio()) == Signal.BUY

    def test_buy_conditions_snapshot_updated(self):
        s = MeanReversion(DEFAULT_CONFIG)
        md = _make_market_data()
        s.decide(md, _portfolio())
        c = s.conditions
        assert c.close_below_lower_bb
        assert c.rsi_oversold
        assert c.adx_below_threshold
        assert c.volume_spike
        assert c.hourly_rsi_ok
        assert c.hourly_close_above_ema
        assert c.session_filter_pass

    # ── Each condition individually false → HOLD ─────────────────────────

    def test_hold_when_close_above_lower_bb(self):
        """Close > lower BB → close_below_lower_bb = False."""
        s = MeanReversion(DEFAULT_CONFIG)
        md = _make_market_data(close=96.0, bb_lower=95.0)
        assert s.decide(md, _portfolio()) == Signal.HOLD

    def test_buy_when_close_equals_lower_bb(self):
        """Close == lower BB → close_below_lower_bb = True (<=)."""
        s = MeanReversion(DEFAULT_CONFIG)
        md = _make_market_data(close=95.0, bb_lower=95.0)
        assert s.decide(md, _portfolio()) == Signal.BUY

    def test_hold_when_rsi_above_oversold(self):
        """RSI >= 30 → rsi_oversold = False."""
        s = MeanReversion(DEFAULT_CONFIG)
        md = _make_market_data(rsi=35.0)
        assert s.decide(md, _portfolio()) == Signal.HOLD

    def test_hold_when_adx_above_threshold(self):
        """ADX >= 25 → adx_below_threshold = False (market is trending)."""
        s = MeanReversion(DEFAULT_CONFIG)
        md = _make_market_data(adx=30.0)
        assert s.decide(md, _portfolio()) == Signal.HOLD

    def test_hold_when_volume_below_spike(self):
        """Volume <= 1.5 × SMA → volume_spike = False."""
        s = MeanReversion(DEFAULT_CONFIG)
        md = _make_market_data(volume=7000.0, volume_sma=5000.0)
        # 7000 < 1.5 × 5000 = 7500 → no spike
        assert s.decide(md, _portfolio()) == Signal.HOLD

    def test_hold_when_hourly_rsi_below_threshold(self):
        """1H RSI <= 35 → hourly_rsi_ok = False (falling knife)."""
        s = MeanReversion(DEFAULT_CONFIG)
        md = _make_market_data(rsi_1h_val=30.0)
        assert s.decide(md, _portfolio()) == Signal.HOLD

    def test_hold_when_hourly_close_below_ema(self):
        """1H close <= EMA(50) → hourly_close_above_ema = False."""
        s = MeanReversion(DEFAULT_CONFIG)
        md = _make_market_data(close_1h=95.0, ema_50_1h_val=100.0)
        assert s.decide(md, _portfolio()) == Signal.HOLD

    def test_no_buy_from_reduced(self):
        """No scale-in: BUY is NOT allowed from REDUCED state."""
        s = MeanReversion(DEFAULT_CONFIG)
        s._state = StrategyState.REDUCED
        md = _make_market_data()
        assert s.decide(md, _portfolio()) == Signal.HOLD


# ── SELL_FULL Signal ─────────────────────────────────────────────────────────


class TestSellFullSignal:
    """SELL_FULL: upper BB, RSI overbought, or ADX regime shift."""

    def test_sell_full_on_upper_bb(self):
        """Close >= upper BB → SELL_FULL from POSITION."""
        s = MeanReversion(DEFAULT_CONFIG)
        s._state = StrategyState.POSITION
        md = _make_market_data(close=106.0, bb_upper=105.0)
        assert s.decide(md, _portfolio()) == Signal.SELL_FULL

    def test_sell_full_on_close_equals_upper_bb(self):
        """Close == upper BB → SELL_FULL (>=)."""
        s = MeanReversion(DEFAULT_CONFIG)
        s._state = StrategyState.POSITION
        md = _make_market_data(close=105.0, bb_upper=105.0)
        assert s.decide(md, _portfolio()) == Signal.SELL_FULL

    def test_sell_full_on_rsi_overbought(self):
        """RSI > 70 → SELL_FULL from POSITION."""
        s = MeanReversion(DEFAULT_CONFIG)
        s._state = StrategyState.POSITION
        md = _make_market_data(close=98.0, rsi=75.0)
        assert s.decide(md, _portfolio()) == Signal.SELL_FULL

    def test_sell_full_on_adx_regime_shift(self):
        """ADX > 30 → SELL_FULL from POSITION (market started trending)."""
        s = MeanReversion(DEFAULT_CONFIG)
        s._state = StrategyState.POSITION
        md = _make_market_data(close=98.0, adx=35.0)
        assert s.decide(md, _portfolio()) == Signal.SELL_FULL

    def test_sell_full_from_reduced(self):
        """SELL_FULL is also reachable from REDUCED."""
        s = MeanReversion(DEFAULT_CONFIG)
        s._state = StrategyState.REDUCED
        md = _make_market_data(close=106.0, bb_upper=105.0)
        assert s.decide(md, _portfolio()) == Signal.SELL_FULL

    def test_sell_full_not_from_flat(self):
        """SELL_FULL is NOT produced when state is FLAT."""
        s = MeanReversion(DEFAULT_CONFIG)
        s._state = StrategyState.FLAT
        # Close above upper BB + RSI overbought, but FLAT → no sell
        md = _make_market_data(close=106.0, bb_upper=105.0, rsi=75.0)
        assert s.decide(md, _portfolio()) == Signal.HOLD


# ── SELL_HALF Signal ─────────────────────────────────────────────────────────


class TestSellHalfSignal:
    """SELL_HALF: close >= middle BB (SMA 20) — price reverted to mean."""

    def test_sell_half_on_middle_bb(self):
        """Close >= middle BB → SELL_HALF from POSITION."""
        s = MeanReversion(DEFAULT_CONFIG)
        s._state = StrategyState.POSITION
        md = _make_market_data(close=101.0, bb_middle=100.0)
        assert s.decide(md, _portfolio()) == Signal.SELL_HALF

    def test_sell_half_on_close_equals_middle_bb(self):
        """Close == middle BB → SELL_HALF (>=)."""
        s = MeanReversion(DEFAULT_CONFIG)
        s._state = StrategyState.POSITION
        md = _make_market_data(close=100.0, bb_middle=100.0)
        assert s.decide(md, _portfolio()) == Signal.SELL_HALF

    def test_hold_when_close_below_middle_bb(self):
        """Close < middle BB from POSITION → HOLD (hasn't reverted yet)."""
        s = MeanReversion(DEFAULT_CONFIG)
        s._state = StrategyState.POSITION
        md = _make_market_data(close=98.0, bb_middle=100.0)
        assert s.decide(md, _portfolio()) == Signal.HOLD

    def test_sell_half_not_from_reduced(self):
        """SELL_HALF is NOT produced from REDUCED state."""
        s = MeanReversion(DEFAULT_CONFIG)
        s._state = StrategyState.REDUCED
        md = _make_market_data(close=101.0, bb_middle=100.0)
        # REDUCED can only get SELL_FULL or HOLD
        assert s.decide(md, _portfolio()) == Signal.HOLD

    def test_sell_half_not_from_flat(self):
        """SELL_HALF is NOT produced from FLAT state."""
        s = MeanReversion(DEFAULT_CONFIG)
        s._state = StrategyState.FLAT
        md = _make_market_data(close=101.0, bb_middle=100.0)
        assert s.decide(md, _portfolio()) == Signal.HOLD


# ── Signal Priority ──────────────────────────────────────────────────────────


class TestSignalPriority:
    """SELL_FULL wins over SELL_HALF wins over BUY."""

    def test_sell_full_beats_sell_half(self):
        """Close >= upper BB also >= middle BB, but SELL_FULL wins."""
        s = MeanReversion(DEFAULT_CONFIG)
        s._state = StrategyState.POSITION
        # Close above upper BB (→ SELL_FULL) and also above middle BB (→ SELL_HALF)
        md = _make_market_data(close=106.0, bb_middle=100.0, bb_upper=105.0)
        assert s.decide(md, _portfolio()) == Signal.SELL_FULL

    def test_rsi_overbought_beats_sell_half(self):
        """RSI > 70 triggers SELL_FULL even when close >= middle BB."""
        s = MeanReversion(DEFAULT_CONFIG)
        s._state = StrategyState.POSITION
        md = _make_market_data(close=101.0, bb_middle=100.0, rsi=75.0)
        assert s.decide(md, _portfolio()) == Signal.SELL_FULL

    def test_adx_regime_shift_beats_sell_half(self):
        """ADX > 30 triggers SELL_FULL even when close >= middle BB."""
        s = MeanReversion(DEFAULT_CONFIG)
        s._state = StrategyState.POSITION
        md = _make_market_data(close=101.0, bb_middle=100.0, adx=35.0)
        assert s.decide(md, _portfolio()) == Signal.SELL_FULL


# ── State Routing ────────────────────────────────────────────────────────────


class TestStateRouting:
    """Verify signal gating per strategy state."""

    def test_cooldown_always_hold(self):
        """COOLDOWN state returns HOLD while countdown > 0."""
        s = MeanReversion(DEFAULT_CONFIG)
        s._state = StrategyState.COOLDOWN
        s._cooldown_remaining = 5  # still counting down
        md = _make_market_data()
        assert s.decide(md, _portfolio()) == Signal.HOLD

    def test_no_buy_from_position(self):
        """POSITION state cannot produce BUY."""
        s = MeanReversion(DEFAULT_CONFIG)
        s._state = StrategyState.POSITION
        md = _make_market_data()
        assert s.decide(md, _portfolio()) != Signal.BUY

    def test_hold_from_flat_when_conditions_not_met(self):
        """FLAT + conditions not met → HOLD."""
        s = MeanReversion(DEFAULT_CONFIG)
        md = _make_market_data(rsi=50.0)  # RSI not oversold
        assert s.decide(md, _portfolio()) == Signal.HOLD


# ── Edge Cases ───────────────────────────────────────────────────────────────


class TestEdgeCases:
    """NaN handling, missing data, and boundary conditions."""

    def test_hold_when_no_secondary_data(self):
        """Missing secondary timeframe → HOLD (conservative)."""
        s = MeanReversion(DEFAULT_CONFIG)
        md = _make_market_data(include_secondary=False)
        assert s.decide(md, _portfolio()) == Signal.HOLD

    def test_hold_when_primary_indicator_nan(self):
        """NaN in a primary indicator → HOLD."""
        s = MeanReversion(DEFAULT_CONFIG)
        md = _make_market_data()
        md.indicators["rsi"].iloc[-1] = float("nan")
        assert s.decide(md, _portfolio()) == Signal.HOLD

    def test_hold_when_primary_indicator_missing(self):
        """Missing required primary indicator key → HOLD."""
        s = MeanReversion(DEFAULT_CONFIG)
        md = _make_market_data()
        del md.indicators["bb_lower"]
        assert s.decide(md, _portfolio()) == Signal.HOLD

    def test_hold_when_secondary_indicator_missing(self):
        """Missing required secondary indicator key → HOLD."""
        s = MeanReversion(DEFAULT_CONFIG)
        md = _make_market_data()
        del md.secondary_indicators["rsi"]
        assert s.decide(md, _portfolio()) == Signal.HOLD

    def test_hold_when_hourly_rsi_nan(self):
        """NaN in hourly RSI → hourly_rsi_ok stays False → HOLD."""
        s = MeanReversion(DEFAULT_CONFIG)
        nan_rsi = _make_series(float("nan"), N_1H)
        md = _make_market_data()
        md.secondary_indicators["rsi"] = nan_rsi
        assert s.decide(md, _portfolio()) == Signal.HOLD

    def test_hold_when_hourly_ema_nan(self):
        """NaN in hourly EMA 50 → hourly_close_above_ema stays False → HOLD."""
        s = MeanReversion(DEFAULT_CONFIG)
        md = _make_market_data()
        md.secondary_indicators["ema_50"] = _make_series(float("nan"), N_1H)
        assert s.decide(md, _portfolio()) == Signal.HOLD

    def test_conditions_updated_every_call(self):
        """Conditions snapshot is refreshed on each decide() call."""
        s = MeanReversion(DEFAULT_CONFIG)

        # First call: all conditions met
        md1 = _make_market_data()
        s.decide(md1, _portfolio())
        assert s.conditions.rsi_oversold is True

        # Second call: RSI not oversold
        md2 = _make_market_data(rsi=50.0)
        s.decide(md2, _portfolio())
        assert s.conditions.rsi_oversold is False

    def test_config_defaults_used_when_keys_missing(self):
        """Strategy works with empty config (uses defaults)."""
        s = MeanReversion({})
        md = _make_market_data()
        result = s.decide(md, _portfolio())
        assert result == Signal.BUY

    def test_volume_spike_threshold_uses_multiplier(self):
        """Volume must exceed multiplier × SMA, not just SMA."""
        s = MeanReversion(DEFAULT_CONFIG)
        # volume=7500, sma=5000 → 7500 == 1.5 × 5000, not strictly greater
        md = _make_market_data(volume=7500.0, volume_sma=5000.0)
        assert s.decide(md, _portfolio()) == Signal.HOLD

    def test_volume_spike_just_above_threshold(self):
        """Volume just above multiplier × SMA → spike detected."""
        s = MeanReversion(DEFAULT_CONFIG)
        md = _make_market_data(volume=7501.0, volume_sma=5000.0)
        assert s.decide(md, _portfolio()) == Signal.BUY


# ── Regime Complementarity with Smart Hodler ─────────────────────────────────


class TestRegimeComplementarity:
    """Mean Reversion and Smart Hodler should not BUY at the same time."""

    def test_adx_filter_inverted_from_smart_hodler(self):
        """Mean Reversion requires ADX < 25; Smart Hodler requires ADX > 25."""
        s = MeanReversion(DEFAULT_CONFIG)
        # ADX at 30 → trending market → mean reversion won't buy
        md = _make_market_data(adx=30.0)
        s.decide(md, _portfolio())
        assert s.conditions.adx_below_threshold is False

        # ADX at 20 → range-bound → mean reversion will buy
        md2 = _make_market_data(adx=20.0)
        s.decide(md2, _portfolio())
        assert s.conditions.adx_below_threshold is True


# ── State Transitions ────────────────────────────────────────────────────────


class TestStateTransitions:
    """Verify that decide() mutates self._state on signal emission."""

    def test_flat_buy_transitions_to_position(self):
        """FLAT + BUY signal → state becomes POSITION."""
        s = MeanReversion(DEFAULT_CONFIG)
        assert s.state == StrategyState.FLAT

        md = _make_market_data()
        signal = s.decide(md, _portfolio())
        assert signal == Signal.BUY
        assert s.state == StrategyState.POSITION

    def test_position_sell_full_transitions_to_flat(self):
        """POSITION + SELL_FULL → state becomes FLAT (no cooldown on profit)."""
        s = MeanReversion(DEFAULT_CONFIG)
        s._state = StrategyState.POSITION

        md = _make_market_data(close=106.0, bb_upper=105.0)
        signal = s.decide(md, _portfolio())
        assert signal == Signal.SELL_FULL
        assert s.state == StrategyState.FLAT

    def test_position_sell_half_transitions_to_reduced(self):
        """POSITION + SELL_HALF → state becomes REDUCED."""
        s = MeanReversion(DEFAULT_CONFIG)
        s._state = StrategyState.POSITION

        md = _make_market_data(close=101.0, bb_middle=100.0)
        signal = s.decide(md, _portfolio())
        assert signal == Signal.SELL_HALF
        assert s.state == StrategyState.REDUCED

    def test_reduced_sell_full_transitions_to_flat(self):
        """REDUCED + SELL_FULL → state becomes FLAT (no cooldown on profit)."""
        s = MeanReversion(DEFAULT_CONFIG)
        s._state = StrategyState.REDUCED

        md = _make_market_data(close=106.0, bb_upper=105.0)
        signal = s.decide(md, _portfolio())
        assert signal == Signal.SELL_FULL
        assert s.state == StrategyState.FLAT

    def test_hold_does_not_change_state(self):
        """HOLD signal leaves the state unchanged."""
        for state in (StrategyState.FLAT, StrategyState.POSITION, StrategyState.REDUCED):
            s = MeanReversion(DEFAULT_CONFIG)
            s._state = state
            md = _make_market_data(rsi=50.0)  # RSI not oversold → neither BUY nor SELL
            signal = s.decide(md, _portfolio())
            assert signal == Signal.HOLD
            assert s.state == state


# ── No Cooldown on Profit Exits ──────────────────────────────────────────────


class TestNoCooldownOnProfitExit:
    """Mean Reversion profit exits go directly to FLAT, no cooldown."""

    def test_sell_full_from_position_no_cooldown(self):
        """SELL_FULL from POSITION → FLAT, cooldown_remaining stays 0."""
        s = MeanReversion(DEFAULT_CONFIG)
        s._state = StrategyState.POSITION

        md = _make_market_data(close=106.0, bb_upper=105.0)
        s.decide(md, _portfolio())
        assert s.state == StrategyState.FLAT
        assert s._cooldown_remaining == 0

    def test_sell_full_from_reduced_no_cooldown(self):
        """SELL_FULL from REDUCED → FLAT, cooldown_remaining stays 0."""
        s = MeanReversion(DEFAULT_CONFIG)
        s._state = StrategyState.REDUCED

        md = _make_market_data(close=106.0, bb_upper=105.0)
        s.decide(md, _portfolio())
        assert s.state == StrategyState.FLAT
        assert s._cooldown_remaining == 0

    def test_immediate_rebuy_after_profit_exit(self):
        """After profit SELL_FULL → FLAT, immediate BUY if conditions met."""
        s = MeanReversion(DEFAULT_CONFIG)
        s._state = StrategyState.POSITION

        # First call: SELL_FULL → FLAT
        md_sell = _make_market_data(close=106.0, bb_upper=105.0)
        assert s.decide(md_sell, _portfolio()) == Signal.SELL_FULL
        assert s.state == StrategyState.FLAT

        # Second call: all BUY conditions met → BUY → POSITION
        md_buy = _make_market_data()
        assert s.decide(md_buy, _portfolio()) == Signal.BUY
        assert s.state == StrategyState.POSITION

    def test_sell_half_does_not_enter_cooldown(self):
        """SELL_HALF → REDUCED, cooldown_remaining stays 0."""
        s = MeanReversion(DEFAULT_CONFIG)
        s._state = StrategyState.POSITION

        md = _make_market_data(close=101.0, bb_middle=100.0)
        s.decide(md, _portfolio())
        assert s.state == StrategyState.REDUCED
        assert s._cooldown_remaining == 0


# ── Cooldown Countdown ───────────────────────────────────────────────────────


class TestCooldownCountdown:
    """Verify cooldown decrement, expiry, and re-evaluation behaviour."""

    def test_cooldown_decrements_each_call(self):
        """Each decide() in COOLDOWN decrements _cooldown_remaining by 1."""
        s = MeanReversion(DEFAULT_CONFIG)
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
        s = MeanReversion(DEFAULT_CONFIG)
        s._state = StrategyState.COOLDOWN
        s._cooldown_remaining = 3

        md = _make_market_data()  # would trigger BUY if not in cooldown
        assert s.decide(md, _portfolio()) == Signal.HOLD
        assert s.decide(md, _portfolio()) == Signal.HOLD

    def test_cooldown_expires_to_flat(self):
        """When _cooldown_remaining reaches 0, state transitions to FLAT."""
        s = MeanReversion(DEFAULT_CONFIG)
        s._state = StrategyState.COOLDOWN
        s._cooldown_remaining = 1

        # Conditions that DON'T trigger BUY so we can verify the state
        md = _make_market_data(rsi=50.0)
        signal = s.decide(md, _portfolio())
        assert signal == Signal.HOLD
        assert s.state == StrategyState.FLAT

    def test_cooldown_expiry_re_evaluates_signals(self):
        """On the candle that expires cooldown, signals are re-evaluated."""
        s = MeanReversion(DEFAULT_CONFIG)
        s._state = StrategyState.COOLDOWN
        s._cooldown_remaining = 1

        # All BUY conditions met → should BUY and transition to POSITION
        md = _make_market_data()
        signal = s.decide(md, _portfolio())
        assert signal == Signal.BUY
        assert s.state == StrategyState.POSITION

    def test_full_cooldown_cycle(self):
        """Walk through a full cooldown then re-entry."""
        s = MeanReversion(DEFAULT_CONFIG)
        # Manually enter cooldown (as hard stop would in task 1.16)
        s._enter_cooldown()
        assert s.state == StrategyState.COOLDOWN
        assert s._cooldown_remaining == 8  # default for mean reversion

        md_buy = _make_market_data()  # conditions for BUY

        # 7 HOLD candles
        for i in range(7):
            assert s.decide(md_buy, _portfolio()) == Signal.HOLD
            assert s.state == StrategyState.COOLDOWN

        # 8th candle: cooldown expires, re-eval triggers BUY
        assert s.decide(md_buy, _portfolio()) == Signal.BUY
        assert s.state == StrategyState.POSITION

    def test_enter_cooldown_sets_state_and_counter(self):
        """_enter_cooldown() sets COOLDOWN state and initialises counter."""
        s = MeanReversion(DEFAULT_CONFIG)
        s._enter_cooldown()
        assert s.state == StrategyState.COOLDOWN
        assert s._cooldown_remaining == 8

    def test_enter_cooldown_custom_config(self):
        """_enter_cooldown() reads cooldown_candles from config."""
        config = {**DEFAULT_CONFIG, "cooldown_candles": 4}
        s = MeanReversion(config)
        s._enter_cooldown()
        assert s._cooldown_remaining == 4

    def test_reset_clears_cooldown_remaining(self):
        """reset() zeroes _cooldown_remaining."""
        s = MeanReversion(DEFAULT_CONFIG)
        s._state = StrategyState.COOLDOWN
        s._cooldown_remaining = 10
        s.reset()
        assert s._cooldown_remaining == 0
        assert s.state == StrategyState.FLAT

    def test_initial_cooldown_remaining_is_zero(self):
        """Fresh strategy has _cooldown_remaining == 0."""
        s = MeanReversion(DEFAULT_CONFIG)
        assert s._cooldown_remaining == 0


# ── Session Filter ───────────────────────────────────────────────────────────

_DEAD_ZONES = [
    {"name": "Weekend", "start_utc": "Saturday 21:00", "end_utc": "Sunday 20:00"},
    {"name": "Overnight Gap", "start_utc": "21:00", "end_utc": "01:00"},
]


def _make_candles_at(start_dt, close_val=90.0, volume_val=8000.0, length=N):
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
    """Build a default-BUY MarketData with last candle at *start_str*."""
    from datetime import datetime as _dt
    candle_time = pd.Timestamp(_dt.fromisoformat(start_str))
    candles = _make_candles_at(
        candle_time,
        close_val=kwargs.get("close", 90.0),
        volume_val=kwargs.get("volume", 8000.0),
    )
    candles_1h = _make_candles_1h(close_val=kwargs.get("close_1h", 110.0))
    return MarketData(
        symbol="BTCUSDT",
        timeframe="15m",
        candles=candles,
        indicators={
            "bb_lower": _make_series(kwargs.get("bb_lower", 95.0)),
            "bb_middle": _make_series(kwargs.get("bb_middle", 100.0)),
            "bb_upper": _make_series(kwargs.get("bb_upper", 105.0)),
            "rsi": _make_series(kwargs.get("rsi", 25.0)),
            "adx": _make_series(kwargs.get("adx", 20.0)),
            "volume_sma": _make_series(kwargs.get("volume_sma", 5000.0)),
        },
        secondary_timeframe="1h",
        secondary_candles=candles_1h,
        secondary_indicators={
            "rsi": _make_series(kwargs.get("rsi_1h", 50.0), N_1H),
            "ema_50": _make_series(kwargs.get("ema_50_1h", 100.0), N_1H),
        },
    )


class TestMRSessionFilter:
    """Verify session filter blocks BUY but not SELL during dead zones."""

    def _config_with_filter(self):
        return {
            **DEFAULT_CONFIG,
            "session_filter_enabled": True,
            "session_dead_zones": _DEAD_ZONES,
        }

    def test_buy_blocked_during_weekend_dead_zone(self):
        """Saturday 22:00 → dead zone → BUY conditions met but HOLD."""
        s = MeanReversion(self._config_with_filter())
        md = _make_md_at_time("2026-02-21T22:00:00")
        assert s.decide(md, _portfolio()) == Signal.HOLD
        assert s.conditions.session_filter_pass is False

    def test_buy_blocked_during_overnight_dead_zone(self):
        """Monday 22:00 → dead zone → BUY blocked."""
        s = MeanReversion(self._config_with_filter())
        md = _make_md_at_time("2026-02-16T22:00:00")
        assert s.decide(md, _portfolio()) == Signal.HOLD
        assert s.conditions.session_filter_pass is False

    def test_buy_allowed_outside_dead_zone(self):
        """Wednesday 14:00 → open session → BUY fires."""
        s = MeanReversion(self._config_with_filter())
        md = _make_md_at_time("2026-02-18T14:00:00")
        assert s.decide(md, _portfolio()) == Signal.BUY
        assert s.conditions.session_filter_pass is True

    def test_sell_not_blocked_during_dead_zone(self):
        """SELL signals fire even inside dead zones."""
        cfg = self._config_with_filter()
        s = MeanReversion(cfg)
        s._state = StrategyState.POSITION
        # Saturday 22:00: RSI overbought → SELL_FULL regardless
        md = _make_md_at_time("2026-02-21T22:00:00", rsi=75.0, adx=35.0)
        assert s.decide(md, _portfolio()) == Signal.SELL_FULL

    def test_filter_disabled_allows_buy_in_dead_zone(self):
        cfg = {**DEFAULT_CONFIG, "session_filter_enabled": False, "session_dead_zones": _DEAD_ZONES}
        s = MeanReversion(cfg)
        md = _make_md_at_time("2026-02-21T22:00:00")
        assert s.decide(md, _portfolio()) == Signal.BUY
        assert s.conditions.session_filter_pass is True
