"""
Tests for the BearGuard strategy signal logic.

Covers:
    - Initialization and reset
    - SHORT path (all 7 conditions, each condition individually false)
    - SELL_FULL path (golden cross, consecutive candles above EMA200)
    - SELL_HALF path (momentum exhaustion: 1H RSI overbought + ADX falling)
    - Signal priority (SELL_FULL > SELL_HALF > SHORT > HOLD)
    - State routing (FLAT, POSITION, REDUCED, COOLDOWN)
    - Edge cases (NaN indicators, missing secondary data, insufficient data)
"""

import math
from unittest.mock import MagicMock

import numpy as np
import pandas as pd
import pytest

from core.models import BearGuardConditions, MarketData, Portfolio, Signal, StrategyState
from core.strategies.bear_guard import BearGuard


# ── Helpers ──────────────────────────────────────────────────────────────────

N = 50  # Number of bars for primary data
N_1H = 15  # Number of bars for secondary (hourly) data


def _make_candles(close_val=90.0, volume_val=6000.0, length=N):
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


def _make_candles_1h(close_val=90.0, volume_val=24000.0, length=N_1H):
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


def _make_series(value, length=N):
    """Create a constant pd.Series of the given length."""
    return pd.Series([value] * length, dtype=float)


def _make_rising_series(start, end, length=N):
    """Create a linearly rising pd.Series."""
    return pd.Series(np.linspace(start, end, length), dtype=float)


def _make_falling_series(start, end, length=N):
    """Create a linearly falling pd.Series."""
    return pd.Series(np.linspace(start, end, length), dtype=float)


DEFAULT_CONFIG = {
    "adx_threshold": 25,
    "volume_filter_pct": 0.70,
    "hourly_ema_lookback": 4,
    "hourly_rsi_bear_threshold": 55,
    "exit_confirmation_candles": 3,
    "rsi_overbought_threshold": 70,
    "adx_falling_lookback": 3,
    "cooldown_candles": 16,
}


def _make_market_data(
    *,
    close=90.0,
    volume=6000.0,
    ema_50=95.0,
    ema_200=100.0,
    adx=30.0,
    volume_sma=5000.0,
    ema_21_1h_vals=None,
    rsi_1h_val=40.0,
    include_secondary=True,
    adx_series=None,
) -> MarketData:
    """
    Build a MarketData object with hand-crafted indicators.

    By default all 7 SHORT conditions are met:
        1. death_cross:           ema_50 (95) < ema_200 (100)      ✓
        2. close_below_ema_fast:  close (90) < ema_50 (95)         ✓
        3. adx_above_threshold:   adx (30) > 25                    ✓
        4. volume_above_average:  volume (6000) >= vol_sma (5000) × 0.70  ✓
        5. hourly_ema_falling:    ema_21_1h falling                 ✓
        6. hourly_rsi_bearish:    rsi_1h (40) < 55                 ✓
        7. session_filter_pass:   True (no dead zones in default config)
    """
    candles = _make_candles(close_val=close, volume_val=volume)

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

        # Hourly EMA 21 – falling by default (bearish confirmation)
        if ema_21_1h_vals is None:
            ema_21_1h_vals = _make_falling_series(110, 90, N_1H)
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


# ── SHORT Signal (FLAT state) ───────────────────────────────────────────────


class TestShortSignal:
    """SHORT requires all 7 conditions met AND state == FLAT."""

    def test_short_all_conditions_met(self):
        s = BearGuard(DEFAULT_CONFIG)
        md = _make_market_data()
        assert s.decide(md, _portfolio()) == Signal.SHORT

    def test_short_conditions_snapshot_updated(self):
        s = BearGuard(DEFAULT_CONFIG)
        md = _make_market_data()
        s.decide(md, _portfolio())
        c = s.conditions
        assert c.death_cross
        assert c.close_below_ema_fast
        assert c.adx_above_threshold
        assert c.volume_above_average
        assert c.hourly_ema_falling
        assert c.hourly_rsi_bearish
        assert c.session_filter_pass
        assert c.all_short_conditions_met

    # ── Each condition individually false → HOLD ─────────────────────────

    def test_hold_when_no_death_cross(self):
        """EMA50 > EMA200 → death_cross = False."""
        s = BearGuard(DEFAULT_CONFIG)
        md = _make_market_data(ema_50=105.0, ema_200=100.0)
        assert s.decide(md, _portfolio()) == Signal.HOLD

    def test_hold_when_close_above_ema_fast(self):
        """Close above EMA 50 → close_below_ema_fast = False."""
        s = BearGuard(DEFAULT_CONFIG)
        md = _make_market_data(close=100.0, ema_50=95.0)
        assert s.decide(md, _portfolio()) == Signal.HOLD

    def test_hold_when_adx_below_threshold(self):
        """ADX below threshold → adx_above_threshold = False."""
        s = BearGuard(DEFAULT_CONFIG)
        md = _make_market_data(adx=20.0)
        assert s.decide(md, _portfolio()) == Signal.HOLD

    def test_hold_when_volume_below_average(self):
        """Volume below SMA × volume_filter_pct → volume_above_average = False."""
        s = BearGuard(DEFAULT_CONFIG)
        md = _make_market_data(volume=3000.0, volume_sma=5000.0)
        assert s.decide(md, _portfolio()) == Signal.HOLD

    def test_hold_when_hourly_ema_not_falling(self):
        """Hourly EMA 21 flat → hourly_ema_falling = False."""
        s = BearGuard(DEFAULT_CONFIG)
        flat_ema = _make_series(100.0, N_1H)
        md = _make_market_data(ema_21_1h_vals=flat_ema)
        assert s.decide(md, _portfolio()) == Signal.HOLD

    def test_hold_when_hourly_rsi_not_bearish(self):
        """Hourly RSI above bear threshold → hourly_rsi_bearish = False."""
        s = BearGuard(DEFAULT_CONFIG)
        md = _make_market_data(rsi_1h_val=60.0)
        assert s.decide(md, _portfolio()) == Signal.HOLD

    # ── No re-entry from POSITION ────────────────────────────────────────

    def test_hold_from_position_when_all_short_conditions_met(self):
        """SHORT is NOT produced from POSITION (no re-entry while in position)."""
        s = BearGuard(DEFAULT_CONFIG)
        s._state = StrategyState.POSITION
        md = _make_market_data()
        assert s.decide(md, _portfolio()) == Signal.HOLD


# ── SELL_FULL Signal ─────────────────────────────────────────────────────────


class TestSellFullSignal:
    """SELL_FULL: golden cross or N consecutive closes above EMA 200."""

    def test_sell_full_on_golden_cross(self):
        """EMA 50 > EMA 200 triggers SELL_FULL from POSITION."""
        s = BearGuard(DEFAULT_CONFIG)
        s._state = StrategyState.POSITION
        md = _make_market_data(ema_50=105.0, ema_200=100.0)
        assert s.decide(md, _portfolio()) == Signal.SELL_FULL

    def test_sell_full_on_consecutive_above_ema200(self):
        """Close above EMA 200 for exit_confirmation_candles → SELL_FULL."""
        s = BearGuard(DEFAULT_CONFIG)
        s._state = StrategyState.POSITION

        # close (110) > ema_200 (100), but ema_50 (95) < ema_200 → no golden cross
        md = _make_market_data(close=110.0, ema_50=95.0, ema_200=100.0)

        # First call: counter = 1 → not enough
        result1 = s.decide(md, _portfolio())
        assert result1 == Signal.HOLD
        assert s._consecutive_above_ema200 == 1

        # Second call: counter = 2 → not enough
        result2 = s.decide(md, _portfolio())
        assert result2 == Signal.HOLD
        assert s._consecutive_above_ema200 == 2

        # Third call: counter = 3 >= exit_confirmation_candles → SELL_FULL
        result3 = s.decide(md, _portfolio())
        assert result3 == Signal.SELL_FULL
        assert s._consecutive_above_ema200 == 3

    def test_consecutive_counter_resets_on_close_below(self):
        """Counter resets to 0 when close goes back below EMA 200."""
        s = BearGuard(DEFAULT_CONFIG)
        s._state = StrategyState.POSITION

        # One candle above
        md_above = _make_market_data(close=110.0, ema_50=95.0, ema_200=100.0)
        s.decide(md_above, _portfolio())
        assert s._consecutive_above_ema200 == 1

        # One candle below → resets
        md_below = _make_market_data(close=90.0, ema_50=95.0, ema_200=100.0)
        s.decide(md_below, _portfolio())
        assert s._consecutive_above_ema200 == 0

    def test_sell_full_from_reduced_state(self):
        """SELL_FULL is also reachable from REDUCED."""
        s = BearGuard(DEFAULT_CONFIG)
        s._state = StrategyState.REDUCED
        md = _make_market_data(ema_50=105.0, ema_200=100.0)
        assert s.decide(md, _portfolio()) == Signal.SELL_FULL

    def test_sell_full_not_from_flat(self):
        """SELL_FULL is NOT produced when state is FLAT."""
        s = BearGuard(DEFAULT_CONFIG)
        s._state = StrategyState.FLAT
        # Golden cross → no death cross → SHORT conditions fail → HOLD
        md = _make_market_data(ema_50=105.0, ema_200=100.0)
        assert s.decide(md, _portfolio()) == Signal.HOLD


# ── SELL_HALF Signal ─────────────────────────────────────────────────────────


class TestSellHalfSignal:
    """SELL_HALF: momentum exhaustion = 1H RSI overbought + ADX falling."""

    def test_sell_half_rsi_overbought_and_adx_falling(self):
        """1H RSI > 70 AND ADX falling → SELL_HALF from POSITION."""
        s = BearGuard(DEFAULT_CONFIG)
        s._state = StrategyState.POSITION

        # ADX: was 35 three bars ago, now 28 → falling
        adx_vals = _make_falling_series(35, 28)
        md = _make_market_data(
            rsi_1h_val=75.0,  # > rsi_overbought_threshold (70)
            adx_series=adx_vals,
            # Keep defaults: ema_50=95 < ema_200=100 → no golden cross
            # close=90 < ema_200=100 → counter stays 0
        )
        result = s.decide(md, _portfolio())
        assert result == Signal.SELL_HALF
        assert s.state == StrategyState.REDUCED

    def test_hold_when_rsi_not_overbought(self):
        """RSI below overbought → no SELL_HALF even if ADX falling."""
        s = BearGuard(DEFAULT_CONFIG)
        s._state = StrategyState.POSITION

        adx_vals = _make_falling_series(35, 28)
        md = _make_market_data(
            rsi_1h_val=60.0,  # <= 70
            adx_series=adx_vals,
        )
        assert s.decide(md, _portfolio()) == Signal.HOLD

    def test_hold_when_adx_not_falling(self):
        """ADX rising → no SELL_HALF even if RSI overbought."""
        s = BearGuard(DEFAULT_CONFIG)
        s._state = StrategyState.POSITION

        adx_vals = _make_rising_series(25, 35)
        md = _make_market_data(
            rsi_1h_val=75.0,
            adx_series=adx_vals,
        )
        assert s.decide(md, _portfolio()) == Signal.HOLD

    def test_sell_half_not_from_reduced(self):
        """SELL_HALF is NOT produced from REDUCED state."""
        s = BearGuard(DEFAULT_CONFIG)
        s._state = StrategyState.REDUCED

        adx_vals = _make_falling_series(35, 28)
        md = _make_market_data(
            rsi_1h_val=75.0,
            adx_series=adx_vals,
        )
        # REDUCED can only SELL_FULL, not SELL_HALF
        assert s.decide(md, _portfolio()) == Signal.HOLD

    def test_sell_half_not_from_flat(self):
        """SELL_HALF is NOT produced from FLAT state."""
        s = BearGuard(DEFAULT_CONFIG)
        s._state = StrategyState.FLAT

        adx_vals = _make_falling_series(35, 28)
        md = _make_market_data(
            rsi_1h_val=75.0,
            adx_series=adx_vals,
        )
        # From FLAT, only SHORT can fire (but RSI condition breaks it)
        assert s.decide(md, _portfolio()) != Signal.SELL_HALF


# ── Signal Priority ──────────────────────────────────────────────────────────


class TestSignalPriority:
    """SELL_FULL wins over SELL_HALF wins over SHORT."""

    def test_sell_full_beats_sell_half(self):
        """When both SELL_FULL and SELL_HALF conditions met, SELL_FULL wins."""
        s = BearGuard(DEFAULT_CONFIG)
        s._state = StrategyState.POSITION

        # Golden cross → SELL_FULL,  RSI overbought + ADX falling → SELL_HALF
        adx_vals = _make_falling_series(35, 28)
        md = _make_market_data(
            ema_50=105.0,  # > ema_200 → golden cross
            ema_200=100.0,
            rsi_1h_val=75.0,
            adx_series=adx_vals,
        )
        assert s.decide(md, _portfolio()) == Signal.SELL_FULL

    def test_sell_full_beats_short(self):
        """SELL_FULL from REDUCED takes priority (SHORT can't fire anyway)."""
        s = BearGuard(DEFAULT_CONFIG)
        s._state = StrategyState.REDUCED

        md = _make_market_data(ema_50=105.0, ema_200=100.0)
        assert s.decide(md, _portfolio()) == Signal.SELL_FULL


# ── State Transitions ────────────────────────────────────────────────────────


class TestStateTransitions:
    """Verify that decide() mutates self._state on signal emission."""

    def test_flat_short_transitions_to_position(self):
        """FLAT + SHORT signal → state becomes POSITION."""
        s = BearGuard(DEFAULT_CONFIG)
        assert s.state == StrategyState.FLAT

        md = _make_market_data()
        signal = s.decide(md, _portfolio())
        assert signal == Signal.SHORT
        assert s.state == StrategyState.POSITION

    def test_position_sell_full_transitions_to_flat(self):
        """POSITION + SELL_FULL → state becomes FLAT (no cooldown)."""
        s = BearGuard(DEFAULT_CONFIG)
        s._state = StrategyState.POSITION

        md = _make_market_data(ema_50=105.0, ema_200=100.0)
        signal = s.decide(md, _portfolio())
        assert signal == Signal.SELL_FULL
        assert s.state == StrategyState.FLAT

    def test_position_sell_full_no_cooldown(self):
        """Profit exit SELL_FULL does NOT enter cooldown."""
        s = BearGuard(DEFAULT_CONFIG)
        s._state = StrategyState.POSITION

        md = _make_market_data(ema_50=105.0, ema_200=100.0)
        s.decide(md, _portfolio())
        assert s._cooldown_remaining == 0
        assert s.state == StrategyState.FLAT

    def test_position_sell_half_transitions_to_reduced(self):
        """POSITION + SELL_HALF → state becomes REDUCED."""
        s = BearGuard(DEFAULT_CONFIG)
        s._state = StrategyState.POSITION

        adx_vals = _make_falling_series(35, 28)
        md = _make_market_data(rsi_1h_val=75.0, adx_series=adx_vals)
        signal = s.decide(md, _portfolio())
        assert signal == Signal.SELL_HALF
        assert s.state == StrategyState.REDUCED

    def test_reduced_sell_full_transitions_to_flat(self):
        """REDUCED + SELL_FULL → state becomes FLAT."""
        s = BearGuard(DEFAULT_CONFIG)
        s._state = StrategyState.REDUCED

        md = _make_market_data(ema_50=105.0, ema_200=100.0)
        signal = s.decide(md, _portfolio())
        assert signal == Signal.SELL_FULL
        assert s.state == StrategyState.FLAT

    def test_hold_does_not_change_state(self):
        """HOLD signal leaves the state unchanged."""
        for state in (StrategyState.FLAT, StrategyState.POSITION, StrategyState.REDUCED):
            s = BearGuard(DEFAULT_CONFIG)
            s._state = state
            md = _make_market_data(adx=10.0)  # ADX too low → conditions fail
            signal = s.decide(md, _portfolio())
            assert signal == Signal.HOLD
            assert s.state == state

    def test_immediate_reentry_after_profit_exit(self):
        """After profit SELL_FULL → FLAT, immediate SHORT if conditions met."""
        s = BearGuard(DEFAULT_CONFIG)
        s._state = StrategyState.POSITION

        # First call: golden cross → SELL_FULL → FLAT
        md_sell = _make_market_data(ema_50=105.0, ema_200=100.0)
        assert s.decide(md_sell, _portfolio()) == Signal.SELL_FULL
        assert s.state == StrategyState.FLAT

        # Second call: all SHORT conditions met → SHORT → POSITION
        md_short = _make_market_data()
        assert s.decide(md_short, _portfolio()) == Signal.SHORT
        assert s.state == StrategyState.POSITION


# ── Cooldown Countdown ───────────────────────────────────────────────────────


class TestCooldownCountdown:
    """Verify cooldown decrement, expiry, and re-evaluation behaviour.

    BearGuard cooldown is entered externally by the tick loop (on hard-stop
    exit), not by decide() itself. Tests simulate this by manually setting
    _state and _cooldown_remaining.
    """

    def test_cooldown_decrements_each_call(self):
        """Each decide() in COOLDOWN decrements _cooldown_remaining by 1."""
        s = BearGuard(DEFAULT_CONFIG)
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
        s = BearGuard(DEFAULT_CONFIG)
        s._state = StrategyState.COOLDOWN
        s._cooldown_remaining = 3

        md = _make_market_data()  # would trigger SHORT if not in cooldown
        assert s.decide(md, _portfolio()) == Signal.HOLD
        assert s.decide(md, _portfolio()) == Signal.HOLD

    def test_cooldown_expires_to_flat(self):
        """When _cooldown_remaining reaches 0, state transitions to FLAT."""
        s = BearGuard(DEFAULT_CONFIG)
        s._state = StrategyState.COOLDOWN
        s._cooldown_remaining = 1

        # Conditions that DON'T trigger SHORT (so we can check state)
        md = _make_market_data(adx=10.0)
        signal = s.decide(md, _portfolio())
        assert signal == Signal.HOLD
        assert s.state == StrategyState.FLAT

    def test_cooldown_expiry_re_evaluates_signals(self):
        """On the candle that expires cooldown, signals are re-evaluated."""
        s = BearGuard(DEFAULT_CONFIG)
        s._state = StrategyState.COOLDOWN
        s._cooldown_remaining = 1

        # All SHORT conditions met → should SHORT on expiry
        md = _make_market_data()
        signal = s.decide(md, _portfolio())
        assert signal == Signal.SHORT
        assert s.state == StrategyState.POSITION

    def test_full_cooldown_cycle(self):
        """Walk through 4 candles of cooldown then re-entry."""
        s = BearGuard(DEFAULT_CONFIG)
        s._state = StrategyState.COOLDOWN
        s._cooldown_remaining = 4

        md = _make_market_data()  # SHORT conditions met
        assert s.decide(md, _portfolio()) == Signal.HOLD  # rem 3
        assert s.decide(md, _portfolio()) == Signal.HOLD  # rem 2
        assert s.decide(md, _portfolio()) == Signal.HOLD  # rem 1

        # 4th candle: cooldown expires, re-eval triggers SHORT
        assert s.decide(md, _portfolio()) == Signal.SHORT
        assert s.state == StrategyState.POSITION

    def test_reset_clears_cooldown_remaining(self):
        """reset() zeroes _cooldown_remaining."""
        s = BearGuard(DEFAULT_CONFIG)
        s._state = StrategyState.COOLDOWN
        s._cooldown_remaining = 10
        s.reset()
        assert s._cooldown_remaining == 0
        assert s.state == StrategyState.FLAT

    def test_initial_cooldown_remaining_is_zero(self):
        """Fresh strategy has _cooldown_remaining == 0."""
        s = BearGuard(DEFAULT_CONFIG)
        assert s._cooldown_remaining == 0


# ── Edge Cases ───────────────────────────────────────────────────────────────


class TestEdgeCases:
    """NaN handling, missing data, and boundary conditions."""

    def test_hold_when_no_secondary_data(self):
        """Missing secondary timeframe → HOLD (conservative)."""
        s = BearGuard(DEFAULT_CONFIG)
        md = _make_market_data(include_secondary=False)
        assert s.decide(md, _portfolio()) == Signal.HOLD

    def test_hold_when_primary_indicator_is_nan(self):
        """NaN in a primary indicator → HOLD."""
        s = BearGuard(DEFAULT_CONFIG)
        md = _make_market_data()
        md.indicators["ema_50"].iloc[-1] = float("nan")
        assert s.decide(md, _portfolio()) == Signal.HOLD

    def test_hold_when_primary_indicator_missing(self):
        """Missing required primary indicator key → HOLD."""
        s = BearGuard(DEFAULT_CONFIG)
        md = _make_market_data()
        del md.indicators["adx"]
        assert s.decide(md, _portfolio()) == Signal.HOLD

    def test_hold_when_secondary_indicator_missing(self):
        """Missing required secondary indicator key → HOLD."""
        s = BearGuard(DEFAULT_CONFIG)
        md = _make_market_data()
        del md.secondary_indicators["ema_21"]
        assert s.decide(md, _portfolio()) == Signal.HOLD

    def test_hold_when_hourly_ema_nan(self):
        """NaN in hourly EMA → hourly_ema_falling stays False → HOLD."""
        s = BearGuard(DEFAULT_CONFIG)
        nan_ema = _make_series(float("nan"), N_1H)
        md = _make_market_data(ema_21_1h_vals=nan_ema)
        assert s.decide(md, _portfolio()) == Signal.HOLD

    def test_hold_when_hourly_rsi_nan(self):
        """NaN in hourly RSI → hourly_rsi_bearish stays False → HOLD."""
        s = BearGuard(DEFAULT_CONFIG)
        md = _make_market_data(rsi_1h_val=float("nan"))
        assert s.decide(md, _portfolio()) == Signal.HOLD

    def test_hold_when_hourly_data_insufficient(self):
        """Less than lookback_bars (4) hourly candles → conditions false."""
        s = BearGuard(DEFAULT_CONFIG)
        # Only 2 hourly values — less than hourly_ema_lookback (4)
        short_ema = _make_falling_series(110, 90, length=2)
        md = _make_market_data(ema_21_1h_vals=short_ema)
        assert s.decide(md, _portfolio()) == Signal.HOLD

    def test_conditions_updated_every_call(self):
        """Conditions snapshot is refreshed on each decide() call."""
        s = BearGuard(DEFAULT_CONFIG)

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
        s = BearGuard({})
        md = _make_market_data()
        # Should not raise — defaults kick in
        result = s.decide(md, _portfolio())
        assert result == Signal.SHORT


# ── Session Filter ───────────────────────────────────────────────────────────

_DEAD_ZONES = [
    {"name": "Weekend", "start_utc": "Saturday 21:00", "end_utc": "Sunday 20:00"},
    {"name": "Overnight Gap", "start_utc": "21:00", "end_utc": "01:00"},
]


def _make_candles_at(start_dt, close_val=90.0, volume_val=6000.0, length=N):
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
    """Build a default-SHORT MarketData with the last candle at *start_str*."""
    from datetime import datetime as _dt

    candle_time = pd.Timestamp(_dt.fromisoformat(start_str))
    candles = _make_candles_at(
        candle_time,
        close_val=kwargs.get("close", 90.0),
        volume_val=kwargs.get("volume", 6000.0),
    )
    candles_1h = _make_candles_1h()
    return MarketData(
        symbol="BTCUSDT",
        timeframe="15m",
        candles=candles,
        indicators={
            "ema_50": _make_series(kwargs.get("ema_50", 95.0)),
            "ema_200": _make_series(kwargs.get("ema_200", 100.0)),
            "adx": _make_series(kwargs.get("adx", 30.0)),
            "volume_sma": _make_series(kwargs.get("volume_sma", 5000.0)),
        },
        secondary_timeframe="1h",
        secondary_candles=candles_1h,
        secondary_indicators={
            "ema_21": _make_falling_series(110, 90, N_1H),
            "rsi": _make_series(kwargs.get("rsi_1h", 40.0), N_1H),
        },
    )


class TestSessionFilter:
    """Verify session filter blocks SHORT but not SELL during dead zones."""

    def _config_with_filter(self):
        return {
            **DEFAULT_CONFIG,
            "session_filter_enabled": True,
            "session_dead_zones": _DEAD_ZONES,
        }

    def test_short_blocked_during_weekend_dead_zone(self):
        """Saturday 22:00 → dead zone → SHORT conditions met but HOLD."""
        s = BearGuard(self._config_with_filter())
        md = _make_md_at_time("2026-02-21T22:00:00")  # Saturday
        assert s.decide(md, _portfolio()) == Signal.HOLD
        assert s.conditions.session_filter_pass is False

    def test_short_blocked_during_overnight_dead_zone(self):
        """Monday 22:00 → dead zone → SHORT blocked."""
        s = BearGuard(self._config_with_filter())
        md = _make_md_at_time("2026-02-16T22:00:00")  # Monday
        assert s.decide(md, _portfolio()) == Signal.HOLD
        assert s.conditions.session_filter_pass is False

    def test_short_allowed_outside_dead_zone(self):
        """Wednesday 14:00 → open session → SHORT fires."""
        s = BearGuard(self._config_with_filter())
        md = _make_md_at_time("2026-02-18T14:00:00")  # Wednesday
        assert s.decide(md, _portfolio()) == Signal.SHORT
        assert s.conditions.session_filter_pass is True

    def test_sell_not_blocked_during_dead_zone(self):
        """SELL signals fire even inside dead zones (protect capital)."""
        cfg = self._config_with_filter()
        s = BearGuard(cfg)
        s._state = StrategyState.POSITION
        # Saturday 22:00: golden cross → SELL_FULL regardless of dead zone
        md = _make_md_at_time("2026-02-21T22:00:00", ema_50=105.0, ema_200=100.0)
        assert s.decide(md, _portfolio()) == Signal.SELL_FULL

    def test_filter_disabled_allows_short_in_dead_zone(self):
        """session_filter_enabled=False → session_filter_pass=True always."""
        cfg = {**DEFAULT_CONFIG, "session_filter_enabled": False, "session_dead_zones": _DEAD_ZONES}
        s = BearGuard(cfg)
        md = _make_md_at_time("2026-02-21T22:00:00")  # Saturday
        assert s.decide(md, _portfolio()) == Signal.SHORT
        assert s.conditions.session_filter_pass is True

    def test_no_dead_zones_allows_short(self):
        """No dead zones configured → session_filter_pass=True."""
        s = BearGuard(DEFAULT_CONFIG)
        md = _make_md_at_time("2026-02-21T22:00:00")  # Saturday
        assert s.decide(md, _portfolio()) == Signal.SHORT
