"""
Smart Hodler strategy – trend-following with hourly confirmation.

Buys when all 7 conditions align (15m trend + 1H confirmation + session filter),
exits fully on structure break or confirmed close below EMA 200,
exits half on momentum fade (ADX falling + close below EMA 50).

Signal priority:
    1. SELL_FULL  — trend structure broken
    2. SELL_HALF  — momentum fading (only from POSITION)
    3. BUY        — all conditions met (only from FLAT / REDUCED)
    4. HOLD       — default
"""

import math
from typing import Any, Dict

import pandas as pd

from core.models import MarketData, Portfolio, Signal, StrategyConditions, StrategyState
from core.strategies.base import Strategy
from core.strategies.session_filter import is_in_dead_zone


class SmartHodler(Strategy):
    """Trend-following strategy for 15m timeframe with hourly confirmation."""

    DEFAULT_COOLDOWN_CANDLES: int = 16

    def __init__(self, config: Dict[str, Any]) -> None:
        super().__init__(config)
        self._consecutive_below_ema200: int = 0
        self._cooldown_remaining: int = 0

    # ── Abstract interface ───────────────────────────────────────────────

    @property
    def name(self) -> str:  # noqa: D102
        return "smart_hodler"

    def decide(self, market_data: MarketData, portfolio: Portfolio) -> Signal:
        """
        Evaluate market conditions and return a trading signal.

        Reads primary (15m) and secondary (1H) indicators from *market_data*,
        updates ``self._conditions``, and returns a signal gated by the
        current strategy state.

        State routing & transitions:
            FLAT     → BUY → POSITION
            POSITION → SELL_FULL → COOLDOWN / SELL_HALF → REDUCED / HOLD
            REDUCED  → SELL_FULL → COOLDOWN / BUY → POSITION / HOLD
            COOLDOWN → countdown → FLAT (then re-evaluate)
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
        lookback_bars = 4  # current vs 3 bars ago → index -1 vs -4
        hourly_ema_rising = False
        hourly_rsi_ok = False

        if len(ema_21_1h) >= lookback_bars and len(rsi_1h) >= 1:
            ema_1h_now = ema_21_1h.iloc[-1]
            ema_1h_prev = ema_21_1h.iloc[-lookback_bars]
            rsi_1h_now = rsi_1h.iloc[-1]

            if not (_isnan(ema_1h_now) or _isnan(ema_1h_prev)):
                hourly_ema_rising = ema_1h_now > ema_1h_prev

            if not _isnan(rsi_1h_now):
                rsi_threshold = self.config.get("rsi_threshold", 45)
                hourly_rsi_ok = rsi_1h_now > rsi_threshold

        # ── Update conditions snapshot ───────────────────────────────────
        adx_threshold = self.config.get("adx_threshold", 25)
        pullback_pct = self.config.get("pullback_max_distance_pct", 0.005)
        pullback_near = ema_50_val > 0 and abs(close - ema_50_val) / ema_50_val <= pullback_pct

        self._conditions = StrategyConditions(
            ema_cross_bullish=ema_50_val > ema_200_val,
            adx_above_threshold=adx_val > adx_threshold,
            close_above_ema_fast=close > ema_50_val,
            volume_above_average=volume > vol_sma_val,
            hourly_ema_rising=hourly_ema_rising,
            hourly_rsi_above_threshold=hourly_rsi_ok,
            pullback_near_ema=pullback_near,
            session_filter_pass=self._check_session_filter(market_data),
        )

        # ── Consecutive-below-EMA200 counter ─────────────────────────────
        if close < ema_200_val:
            self._consecutive_below_ema200 += 1
        else:
            self._consecutive_below_ema200 = 0

        # ── Signal evaluation (priority order) ───────────────────────────
        in_position = self._state in (StrategyState.POSITION, StrategyState.REDUCED)

        # 1. SELL_FULL – only when holding
        if in_position:
            if self._check_sell_full(ema_50_val, ema_200_val):
                self._enter_cooldown()
                return Signal.SELL_FULL

        # 2. SELL_HALF – only from full POSITION (not REDUCED)
        if self._state == StrategyState.POSITION:
            if self._check_sell_half(close, ema_50_val, adx):
                self._state = StrategyState.REDUCED
                return Signal.SELL_HALF

        # 3. BUY – only from FLAT or REDUCED
        if self._state in (StrategyState.FLAT, StrategyState.REDUCED):
            if self._conditions.all_buy_conditions_met:
                self._state = StrategyState.POSITION
                return Signal.BUY

        # 4. Default
        return Signal.HOLD

    def to_state_dict(self) -> Dict[str, Any]:  # noqa: D102
        d = super().to_state_dict()
        d["consecutive_below_ema200"] = self._consecutive_below_ema200
        return d

    def from_state_dict(self, data: Dict[str, Any]) -> None:  # noqa: D102
        super().from_state_dict(data)
        self._consecutive_below_ema200 = data.get("consecutive_below_ema200", 0)

    def reset(self) -> None:  # noqa: D102
        super().reset()
        self._consecutive_below_ema200 = 0
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

    def _enter_cooldown(self) -> None:
        """Transition to COOLDOWN state and initialise the countdown.

        Thin wrapper around the public :meth:`enter_cooldown` (base class) so
        internal callers and the live tick loop share one path.
        """
        self.enter_cooldown(reason="hard_stop")

    def _check_sell_full(self, ema_50_val: float, ema_200_val: float) -> bool:
        """
        Check SELL_FULL conditions.

        Priority 1: EMA 50 < EMA 200  (trend structure broken)
        Priority 2: Close < EMA 200 for N consecutive candles
        """
        # Priority 1 – EMA death cross
        if ema_50_val < ema_200_val:
            return True

        # Priority 2 – confirmed structure break
        confirmation = self.config.get("exit_confirmation_candles", 2)
        if self._consecutive_below_ema200 >= confirmation:
            return True

        return False

    def _check_sell_half(
        self, close: float, ema_50_val: float, adx: pd.Series
    ) -> bool:
        """
        Check SELL_HALF conditions (momentum fade).

        Triggered when close < EMA 50 AND ADX is falling
        (current ADX < ADX N bars ago).
        """
        if close >= ema_50_val:
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
