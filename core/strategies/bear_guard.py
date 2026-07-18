"""
BearGuard strategy – bear trend short with hourly confirmation.

Opens short when all 7 conditions align (15m death cross + 1H confirmation
+ session filter), covers fully on golden cross or confirmed close above
EMA 200, covers half on momentum exhaustion (RSI overbought + ADX falling).

Signal priority:
    1. SELL_FULL  — trend structure reversed (golden cross | confirmed reversal)
    2. SELL_HALF  — momentum exhaustion (only from POSITION)
    3. SHORT      — all conditions met (only from FLAT)
    4. HOLD       — default

Cooldown is only applied on hard-stop exits (handled externally by the tick
loop via StopCheckResult.cooldown_candles). Profit exits (SELL_FULL /
SELL_HALF) go directly to FLAT / REDUCED with no cooldown.
"""

import math
from typing import Any, Dict

import pandas as pd

from core.models import (
    BearGuardConditions,
    MarketData,
    Portfolio,
    Signal,
    StrategyState,
)
from core.strategies.base import Strategy
from core.strategies.session_filter import is_in_dead_zone


class BearGuard(Strategy):
    """Bear trend short strategy for 15m timeframe with hourly confirmation."""

    DEFAULT_COOLDOWN_CANDLES: int = 16

    def __init__(self, config: Dict[str, Any]) -> None:
        super().__init__(config)
        self._conditions: BearGuardConditions = BearGuardConditions()
        self._consecutive_above_ema200: int = 0
        self._cooldown_remaining: int = 0

    # ── Abstract interface ───────────────────────────────────────────────

    @property
    def name(self) -> str:  # noqa: D102
        return "bear_guard"

    def decide(self, market_data: MarketData, portfolio: Portfolio) -> Signal:
        """
        Evaluate market conditions and return a trading signal.

        State routing & transitions:
            FLAT     → SHORT → POSITION
            POSITION → SELL_FULL → FLAT / SELL_HALF → REDUCED / HOLD
            REDUCED  → SELL_FULL → FLAT / HOLD
            COOLDOWN → countdown → FLAT (then re-evaluate)

        No cooldown on profit exits.  Cooldown only on hard-stop exits
        (set externally by the tick loop).
        """
        # ── Cooldown countdown ────────────────────────────────────────
        if self._state == StrategyState.COOLDOWN:
            self._cooldown_remaining -= 1
            if self._cooldown_remaining > 0:
                return Signal.HOLD
            # Cooldown expired → transition to FLAT, fall through to re-eval
            self._state = StrategyState.FLAT

        # ── Data guard ───────────────────────────────────────────────────
        if not market_data.has_secondary:
            return Signal.HOLD

        try:
            ema_50 = market_data.get_indicator("ema_50")
            ema_200 = market_data.get_indicator("ema_200")
            adx = market_data.get_indicator("adx")
            volume_sma = market_data.get_indicator("volume_sma")

            secondary_tf = market_data.secondary_timeframe
            ema_21_1h = market_data.get_indicator("ema_21", secondary_tf)
            rsi_1h = market_data.get_indicator("rsi", secondary_tf)
        except KeyError:
            return Signal.HOLD

        # Current bar values
        close = market_data.candles["close"].iloc[-1]
        volume = market_data.candles["volume"].iloc[-1]
        ema_50_val = ema_50.iloc[-1]
        ema_200_val = ema_200.iloc[-1]
        adx_val = adx.iloc[-1]
        vol_sma_val = volume_sma.iloc[-1]

        # NaN guard – warmup periods produce NaN
        if any(
            _isnan(v)
            for v in (close, ema_50_val, ema_200_val, adx_val, vol_sma_val)
        ):
            return Signal.HOLD

        # ── Hourly checks (with NaN guard) ───────────────────────────────
        lookback_bars = self.config.get("hourly_ema_lookback", 4)
        hourly_ema_falling = False
        hourly_rsi_bearish = False
        rsi_1h_val = float("nan")

        if len(ema_21_1h) >= lookback_bars and len(rsi_1h) >= 1:
            ema_1h_now = ema_21_1h.iloc[-1]
            ema_1h_prev = ema_21_1h.iloc[-lookback_bars]
            rsi_1h_val = rsi_1h.iloc[-1]

            if not (_isnan(ema_1h_now) or _isnan(ema_1h_prev)):
                hourly_ema_falling = ema_1h_now < ema_1h_prev

            if not _isnan(rsi_1h_val):
                rsi_bear_threshold = self.config.get(
                    "hourly_rsi_bear_threshold", 55
                )
                hourly_rsi_bearish = rsi_1h_val < rsi_bear_threshold

        # ── Update conditions snapshot ───────────────────────────────────
        adx_threshold = self.config.get("adx_threshold", 25)
        volume_filter_pct = self.config.get("volume_filter_pct", 0.70)

        self._conditions = BearGuardConditions(
            death_cross=ema_50_val < ema_200_val,
            close_below_ema_fast=close < ema_50_val,
            adx_above_threshold=adx_val > adx_threshold,
            volume_above_average=volume >= vol_sma_val * volume_filter_pct,
            hourly_ema_falling=hourly_ema_falling,
            hourly_rsi_bearish=hourly_rsi_bearish,
            session_filter_pass=self._check_session_filter(market_data),
        )

        # ── Consecutive-above-EMA200 counter (for confirmed reversal) ────
        if close > ema_200_val:
            self._consecutive_above_ema200 += 1
        else:
            self._consecutive_above_ema200 = 0

        # ── Signal evaluation (priority order) ───────────────────────────
        in_position = self._state in (
            StrategyState.POSITION,
            StrategyState.REDUCED,
        )

        # 1. SELL_FULL – only when holding a short position
        if in_position:
            if self._check_sell_full(ema_50_val, ema_200_val):
                self._state = StrategyState.FLAT
                return Signal.SELL_FULL

        # 2. SELL_HALF – only from full POSITION (not REDUCED)
        if self._state == StrategyState.POSITION:
            if self._check_sell_half(rsi_1h_val, adx):
                self._state = StrategyState.REDUCED
                return Signal.SELL_HALF

        # 3. SHORT – only from FLAT
        if self._state == StrategyState.FLAT:
            if self._conditions.all_short_conditions_met:
                self._state = StrategyState.POSITION
                return Signal.SHORT

        # 4. Default
        return Signal.HOLD

    def to_state_dict(self) -> Dict[str, Any]:  # noqa: D102
        d = super().to_state_dict()
        d["consecutive_above_ema200"] = self._consecutive_above_ema200
        return d

    def from_state_dict(self, data: Dict[str, Any]) -> None:  # noqa: D102
        super().from_state_dict(data)
        self._consecutive_above_ema200 = data.get("consecutive_above_ema200", 0)

    def reset(self) -> None:  # noqa: D102
        super().reset()
        self._consecutive_above_ema200 = 0
        self._cooldown_remaining = 0

    # ── Private helpers ──────────────────────────────────────────────────

    def _check_session_filter(self, market_data: MarketData) -> bool:
        """Return True when trading is allowed (outside dead zones)."""
        if not self.config.get("session_filter_enabled", True):
            return True
        dead_zones = self.config.get("session_dead_zones", [])
        if not dead_zones:
            return True
        candle_time = market_data.candles.index[-1].to_pydatetime()
        return not is_in_dead_zone(candle_time, dead_zones)

    def _check_sell_full(self, ema_50_val: float, ema_200_val: float) -> bool:
        """
        Check SELL_FULL conditions (cover 100%).

        Priority 1: EMA 50 > EMA 200  (golden cross — trend reversed)
        Priority 2: Close > EMA 200 for N consecutive candles (confirmed reversal)
        """
        # Priority 1 – golden cross
        if ema_50_val > ema_200_val:
            return True

        # Priority 2 – confirmed reversal
        confirmation = self.config.get("exit_confirmation_candles", 3)
        if self._consecutive_above_ema200 >= confirmation:
            return True

        return False

    def _check_sell_half(self, rsi_1h_val: float, adx: pd.Series) -> bool:
        """
        Check SELL_HALF conditions (momentum exhaustion — cover 50%).

        Triggered when 1H RSI > overbought threshold AND ADX is falling
        (current ADX < ADX N bars ago).
        """
        if _isnan(rsi_1h_val):
            return False

        rsi_overbought = self.config.get("rsi_overbought_threshold", 70)
        if rsi_1h_val <= rsi_overbought:
            return False

        lookback = self.config.get("adx_falling_lookback", 3)
        if len(adx) < lookback + 1:
            return False

        adx_now = adx.iloc[-1]
        adx_prev = adx.iloc[-(lookback + 1)]

        if _isnan(adx_now) or _isnan(adx_prev):
            return False

        return adx_now < adx_prev


def _isnan(value: float) -> bool:
    """Check for NaN supporting both float and numpy scalar types."""
    try:
        return math.isnan(float(value))
    except (TypeError, ValueError):
        return True
