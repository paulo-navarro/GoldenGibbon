"""
Mean Reversion strategy – contrarian fade with hourly confirmation.

Buys when price is statistically overextended below the lower Bollinger Band
in a range-bound market (ADX < 25), with volume spike and hourly confirmation.
Exits partially at the mean (middle BB) and fully at the upper BB, RSI overbought,
or regime shift (ADX > 30).

Signal priority:
    1. SELL_FULL  — upper BB / RSI overbought / regime shift to trending
    2. SELL_HALF  — price reverted to middle BB (SMA 20)
    3. BUY        — all conditions met (only from FLAT)
    4. HOLD       — default
"""

import math
from typing import Any, Dict

from core.models import (
    MarketData,
    MeanReversionConditions,
    Portfolio,
    Signal,
    StrategyState,
)
from core.strategies.base import Strategy


class MeanReversion(Strategy):
    """Contrarian mean-reversion strategy for 15m timeframe with hourly confirmation."""

    def __init__(self, config: Dict[str, Any]) -> None:
        super().__init__(config)
        self._conditions: MeanReversionConditions = MeanReversionConditions()
        self._cooldown_remaining: int = 0

    # ── Abstract interface ───────────────────────────────────────────────

    @property
    def name(self) -> str:  # noqa: D102
        return "mean_reversion"

    @property
    def conditions(self) -> MeanReversionConditions:  # noqa: D102
        return self._conditions

    def decide(self, market_data: MarketData, portfolio: Portfolio) -> Signal:
        """
        Evaluate market conditions and return a trading signal.

        Reads primary (15m) and secondary (1H) indicators from *market_data*,
        updates ``self._conditions``, and returns a signal gated by the
        current strategy state.

        State routing & transitions:
            FLAT     → BUY → POSITION
            POSITION → SELL_FULL → FLAT / SELL_HALF → REDUCED / HOLD
            REDUCED  → SELL_FULL → FLAT / HOLD
            COOLDOWN → countdown → FLAT (then re-evaluate)

        No cooldown on profit exits (SELL_FULL / SELL_HALF go directly
        to FLAT / REDUCED).  Cooldown is only entered via hard stop
        (task 1.16) or time stop (task 1.27).
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
            bb_lower = market_data.get_indicator("bb_lower")
            bb_middle = market_data.get_indicator("bb_middle")
            bb_upper = market_data.get_indicator("bb_upper")
            rsi = market_data.get_indicator("rsi")
            adx = market_data.get_indicator("adx")
            volume_sma = market_data.get_indicator("volume_sma")

            secondary_tf = market_data.secondary_timeframe
            rsi_1h = market_data.get_indicator("rsi", secondary_tf)
            ema_50_1h = market_data.get_indicator("ema_50", secondary_tf)
        except KeyError:
            return Signal.HOLD

        # Current bar values – primary
        close = market_data.candles["close"].iloc[-1]
        volume = market_data.candles["volume"].iloc[-1]
        bb_lower_val = bb_lower.iloc[-1]
        bb_middle_val = bb_middle.iloc[-1]
        bb_upper_val = bb_upper.iloc[-1]
        rsi_val = rsi.iloc[-1]
        adx_val = adx.iloc[-1]
        vol_sma_val = volume_sma.iloc[-1]

        # NaN guard
        if any(
            _isnan(v)
            for v in (
                close,
                volume,
                bb_lower_val,
                bb_middle_val,
                bb_upper_val,
                rsi_val,
                adx_val,
                vol_sma_val,
            )
        ):
            return Signal.HOLD

        # Current bar values – secondary
        close_1h = market_data.secondary_candles["close"].iloc[-1]

        # ── Hourly checks (with NaN guard) ───────────────────────────────
        hourly_rsi_ok = False
        hourly_close_above_ema = False

        if len(rsi_1h) >= 1 and len(ema_50_1h) >= 1:
            rsi_1h_val = rsi_1h.iloc[-1]
            ema_50_1h_val = ema_50_1h.iloc[-1]

            if not _isnan(rsi_1h_val):
                hourly_rsi_threshold = self.config.get("hourly_rsi_threshold", 35)
                hourly_rsi_ok = rsi_1h_val > hourly_rsi_threshold

            if not (_isnan(close_1h) or _isnan(ema_50_1h_val)):
                hourly_close_above_ema = close_1h > ema_50_1h_val

        # ── Update conditions snapshot ───────────────────────────────────
        adx_threshold = self.config.get("adx_threshold", 25)
        rsi_oversold = self.config.get("rsi_oversold", 30)
        volume_spike_multiplier = self.config.get("volume_spike_multiplier", 1.5)

        self._conditions = MeanReversionConditions(
            close_below_lower_bb=close <= bb_lower_val,
            rsi_oversold=rsi_val < rsi_oversold,
            adx_below_threshold=adx_val < adx_threshold,
            volume_spike=volume > volume_spike_multiplier * vol_sma_val,
            hourly_rsi_ok=hourly_rsi_ok,
            hourly_close_above_ema=hourly_close_above_ema,
            session_filter_pass=True,  # Stubbed until session filter task
        )

        # ── Signal evaluation (priority order) ───────────────────────────
        in_position = self._state in (StrategyState.POSITION, StrategyState.REDUCED)

        # 1. SELL_FULL – only when holding → FLAT (no cooldown on profit)
        if in_position:
            if self._check_sell_full(close, bb_upper_val, rsi_val, adx_val):
                self._state = StrategyState.FLAT
                return Signal.SELL_FULL

        # 2. SELL_HALF – only from full POSITION (not REDUCED)
        if self._state == StrategyState.POSITION:
            if self._check_sell_half(close, bb_middle_val):
                self._state = StrategyState.REDUCED
                return Signal.SELL_HALF

        # 3. BUY – only from FLAT (no scale-in for mean reversion)
        if self._state == StrategyState.FLAT:
            if self._conditions.all_buy_conditions_met:
                self._state = StrategyState.POSITION
                return Signal.BUY

        # 4. Default
        return Signal.HOLD

    def reset(self) -> None:  # noqa: D102
        super().reset()
        self._conditions = MeanReversionConditions()
        self._cooldown_remaining = 0

    # ── Private helpers ──────────────────────────────────────────────────

    def _enter_cooldown(self) -> None:
        """Transition to COOLDOWN state and initialise the countdown.

        Called by hard stop (task 1.16) and time stop (task 1.27).
        Not used for profit exits — those go directly to FLAT.
        """
        self._state = StrategyState.COOLDOWN
        self._cooldown_remaining = self.config.get("cooldown_candles", 8)

    def _check_sell_full(
        self,
        close: float,
        bb_upper_val: float,
        rsi_val: float,
        adx_val: float,
    ) -> bool:
        """
        Check SELL_FULL conditions.

        Three independent triggers (any one is sufficient):
            1. Close >= Upper Bollinger Band — full reversion complete
            2. RSI > 70                      — overbought
            3. ADX > 30                      — regime shifted to trending
        """
        rsi_overbought = self.config.get("rsi_overbought", 70)
        adx_regime_shift = self.config.get("adx_regime_shift", 30)

        if close >= bb_upper_val:
            return True
        if rsi_val > rsi_overbought:
            return True
        if adx_val > adx_regime_shift:
            return True

        return False

    def _check_sell_half(self, close: float, bb_middle_val: float) -> bool:
        """
        Check SELL_HALF conditions (middle band reversion).

        Triggered when close >= SMA(20) (middle Bollinger Band).
        Price has reverted to the mean — lock in partial profit.
        """
        return close >= bb_middle_val


def _isnan(value: float) -> bool:
    """Check for NaN supporting both float and numpy scalar types."""
    try:
        return math.isnan(float(value))
    except (TypeError, ValueError):
        return True
