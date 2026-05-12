"""
Stop-check mixin for RiskEngine.

Implements hard stop, trailing stop (ATR-based), break-even ratchet,
and time stop (Mean Reversion only).
"""

from decimal import Decimal, ROUND_HALF_UP
from typing import TYPE_CHECKING

import structlog

from core.models import (
    ExitReason,
    MarketData,
    Portfolio,
    Position,
    PositionSide,
    RiskAction,
    RiskDecision,
    StopCheckResult,
)

if TYPE_CHECKING:
    pass

logger = structlog.get_logger(__name__)


class StopCheckMixin:
    """Stop-check methods extracted from RiskEngine."""

    def check_stops(
        self,
        market_data: MarketData,
        portfolio: Portfolio,
    ) -> StopCheckResult:
        """
        Scan the position for ``market_data.symbol`` and check stops.

        Hard stop (both strategies):
            Fires when close breaches the hard_stop_price. For longs: close < stop.
            For shorts: close > stop. Cooldown is set on exit.

        Time stop (Mean Reversion only):
            Fires when position held >= time_stop_candles.

        Trailing stop (all strategies, when enabled):
            Ratchets highest_close/lowest_close and recomputes stop from ATR.
            Fires when close breaches the trailing level.

        Returns:
            StopCheckResult with refreshed stop levels and optionally
            a RiskDecision to close the position.
        """
        symbol = market_data.symbol
        position = portfolio.positions.get(symbol)

        if position is None:
            return StopCheckResult()

        close = Decimal(str(market_data.candles["close"].iloc[-1]))
        is_short = position.side == PositionSide.SHORT

        # ── Break-even ratchet ───────────────────────────────────────
        ratcheted_hard_stop = self._apply_breakeven_ratchet(
            position, close, is_short, symbol
        )

        # ── Hard stop ────────────────────────────────────────────────
        hard_stop_hit = (
            (ratcheted_hard_stop > 0 and close > ratcheted_hard_stop)
            if is_short
            else (ratcheted_hard_stop > 0 and close < ratcheted_hard_stop)
        )
        if hard_stop_hit:
            logger.info(
                "risk.hard_stop",
                category="risk",
                symbol=symbol,
                close=str(close),
                hard_stop=str(ratcheted_hard_stop),
                cooldown=self._cooldown_candles,
            )
            decision = RiskDecision(
                action=RiskAction.CLOSE,
                symbol=symbol,
                side=position.side,
                size=position.size,
                price=close,
                exit_reason=ExitReason.HARD_STOP,
            )
            if is_short:
                lowest = min(position.lowest_close or close, close)
                return StopCheckResult(
                    decision=decision,
                    lowest_close=lowest,
                    trailing_stop_price=position.trailing_stop_price,
                    hard_stop_price=ratcheted_hard_stop,
                    cooldown_candles=self._cooldown_candles,
                )
            return StopCheckResult(
                decision=decision,
                highest_close=max(position.highest_close, close),
                trailing_stop_price=position.trailing_stop_price,
                hard_stop_price=ratcheted_hard_stop,
                cooldown_candles=self._cooldown_candles,
            )

        # ── Time stop (Mean Reversion only) ──────────────────────────
        if self._strategy_name == "mean_reversion":
            result = self._check_time_stop(
                position, close, market_data, ratcheted_hard_stop, symbol
            )
            if result is not None:
                return result

        # ── Trailing stop ────────────────────────────────────────────
        if self._trailing_enabled:
            if is_short:
                return self._check_trailing_short(
                    position, close, market_data, ratcheted_hard_stop, symbol
                )
            else:
                return self._check_trailing_long(
                    position, close, market_data, ratcheted_hard_stop, symbol
                )

        # Trailing disabled – return hard stop only
        return StopCheckResult(hard_stop_price=ratcheted_hard_stop)

    # ── Break-even ratchet ───────────────────────────────────────────

    def _apply_breakeven_ratchet(
        self,
        position: Position,
        close: Decimal,
        is_short: bool,
        symbol: str,
    ) -> Decimal:
        """Apply break-even ratchet logic, return the (possibly ratcheted) hard stop."""
        ratcheted_hard_stop = position.hard_stop_price

        if not (
            self._ratchet_enabled
            and position.hard_stop_price > 0
            and position.entry_price > 0
        ):
            return ratcheted_hard_stop

        if is_short:
            profit_pct = (position.entry_price - close) / position.entry_price
            new_stop = position.hard_stop_price

            if profit_pct >= self._lockin_trigger:
                new_stop = position.entry_price * (1 - self._lockin_stop_pct)
            elif profit_pct >= self._breakeven_trigger:
                new_stop = position.entry_price

            # Short stops only ratchet DOWN (never up)
            if new_stop < position.hard_stop_price:
                if new_stop < position.entry_price:
                    logger.info(
                        "risk.ratchet_lockin",
                        category="risk",
                        symbol=symbol,
                        side="short",
                        old_stop=str(position.hard_stop_price),
                        new_stop=str(new_stop),
                        profit_pct=str(profit_pct),
                        entry_price=str(position.entry_price),
                        close=str(close),
                    )
                else:
                    logger.info(
                        "risk.ratchet_breakeven",
                        category="risk",
                        symbol=symbol,
                        side="short",
                        old_stop=str(position.hard_stop_price),
                        new_stop=str(new_stop),
                        profit_pct=str(profit_pct),
                        entry_price=str(position.entry_price),
                        close=str(close),
                    )
                ratcheted_hard_stop = new_stop
        else:
            profit_pct = (close - position.entry_price) / position.entry_price
            new_stop = position.hard_stop_price

            if profit_pct >= self._lockin_trigger:
                new_stop = position.entry_price * (1 + self._lockin_stop_pct)
            elif profit_pct >= self._breakeven_trigger:
                new_stop = position.entry_price

            if new_stop > position.hard_stop_price:
                if new_stop > position.entry_price:
                    logger.info(
                        "risk.ratchet_lockin",
                        category="risk",
                        symbol=symbol,
                        old_stop=str(position.hard_stop_price),
                        new_stop=str(new_stop),
                        profit_pct=str(profit_pct),
                        entry_price=str(position.entry_price),
                        close=str(close),
                    )
                else:
                    logger.info(
                        "risk.ratchet_breakeven",
                        category="risk",
                        symbol=symbol,
                        old_stop=str(position.hard_stop_price),
                        new_stop=str(new_stop),
                        profit_pct=str(profit_pct),
                        entry_price=str(position.entry_price),
                        close=str(close),
                    )
                ratcheted_hard_stop = new_stop

        return ratcheted_hard_stop

    # ── Time stop ────────────────────────────────────────────────────

    def _check_time_stop(
        self,
        position: Position,
        close: Decimal,
        market_data: MarketData,
        ratcheted_hard_stop: Decimal,
        symbol: str,
    ) -> StopCheckResult | None:
        """Check time stop for Mean Reversion. Returns result if triggered, else None."""
        candles_held = self._count_candles_held(position, market_data)
        if candles_held < self._time_stop_candles:
            return None

        unrealized = (close - position.entry_price) / position.entry_price
        if self._time_stop_skip_profitable and unrealized > 0:
            logger.info(
                "risk.time_stop_skipped_profitable",
                category="risk",
                symbol=symbol,
                candles_held=candles_held,
                unrealized_pct=str(unrealized),
            )
            return None

        logger.info(
            "risk.time_stop",
            category="risk",
            symbol=symbol,
            candles_held=candles_held,
            time_stop_candles=self._time_stop_candles,
            cooldown=self._time_stop_cooldown,
        )
        decision = RiskDecision(
            action=RiskAction.CLOSE,
            symbol=symbol,
            size=position.size,
            price=close,
            exit_reason=ExitReason.TIME_STOP,
        )
        return StopCheckResult(
            decision=decision,
            highest_close=max(position.highest_close, close),
            trailing_stop_price=position.trailing_stop_price,
            hard_stop_price=ratcheted_hard_stop,
            cooldown_candles=self._time_stop_cooldown,
        )

    # ── Trailing stop (long) ─────────────────────────────────────────

    def _check_trailing_long(
        self,
        position: Position,
        close: Decimal,
        market_data: MarketData,
        ratcheted_hard_stop: Decimal,
        symbol: str,
    ) -> StopCheckResult:
        """Check trailing stop for long positions."""
        highest = max(position.highest_close, close)
        new_trailing = self._compute_trailing_stop(highest, market_data)

        # Only ratchet the stop upward; never lower it
        if position.trailing_stop_price > 0:
            new_trailing = max(new_trailing, position.trailing_stop_price)

        # Breach: price fell below trailing stop
        if new_trailing > 0 and close < new_trailing:
            logger.info(
                "risk.trailing_stop",
                category="risk",
                symbol=symbol,
                close=str(close),
                trailing_stop=str(new_trailing),
            )
            decision = RiskDecision(
                action=RiskAction.CLOSE,
                symbol=symbol,
                size=position.size,
                price=close,
                exit_reason=ExitReason.TRAILING_STOP,
            )
            return StopCheckResult(
                decision=decision,
                highest_close=highest,
                trailing_stop_price=new_trailing,
                hard_stop_price=ratcheted_hard_stop,
            )

        return StopCheckResult(
            highest_close=highest,
            trailing_stop_price=new_trailing,
            hard_stop_price=ratcheted_hard_stop,
        )

    # ── Trailing stop (short) ────────────────────────────────────────

    def _check_trailing_short(
        self,
        position: Position,
        close: Decimal,
        market_data: MarketData,
        ratcheted_hard_stop: Decimal,
        symbol: str,
    ) -> StopCheckResult:
        """Check trailing stop for short positions."""
        lowest = min(position.lowest_close or close, close)
        new_trailing = self._compute_short_trailing_stop(lowest, market_data)

        # Only ratchet the stop downward; never raise it
        if position.trailing_stop_price > 0:
            new_trailing = min(new_trailing, position.trailing_stop_price)

        # Breach: price rose above trailing stop
        if new_trailing > 0 and close > new_trailing:
            logger.info(
                "risk.trailing_stop",
                category="risk",
                symbol=symbol,
                side="short",
                close=str(close),
                trailing_stop=str(new_trailing),
            )
            decision = RiskDecision(
                action=RiskAction.CLOSE,
                symbol=symbol,
                side=PositionSide.SHORT,
                size=position.size,
                price=close,
                exit_reason=ExitReason.TRAILING_STOP,
            )
            return StopCheckResult(
                decision=decision,
                lowest_close=lowest,
                trailing_stop_price=new_trailing,
                hard_stop_price=ratcheted_hard_stop,
            )

        return StopCheckResult(
            lowest_close=lowest,
            trailing_stop_price=new_trailing,
            hard_stop_price=ratcheted_hard_stop,
        )

    # ── Stop-price computation ───────────────────────────────────────

    def _compute_hard_stop(self, entry_price: Decimal) -> Decimal:
        """Hard stop for longs: entry_price × (1 − hard_stop_pct)."""
        return (entry_price * (1 - self._hard_stop_pct)).quantize(
            Decimal("0.00000001"), rounding=ROUND_HALF_UP
        )

    def _compute_short_hard_stop(self, entry_price: Decimal) -> Decimal:
        """Hard stop for shorts: entry_price × (1 + hard_stop_pct) — above entry."""
        return (entry_price * (1 + self._hard_stop_pct)).quantize(
            Decimal("0.00000001"), rounding=ROUND_HALF_UP
        )

    def _compute_trailing_stop(
        self,
        highest_close: Decimal,
        market_data: MarketData,
    ) -> Decimal:
        """Trailing stop for longs: highest_close − (ATR × multiplier)."""
        try:
            atr_series = market_data.get_indicator("atr")
            atr_value = Decimal(str(atr_series.iloc[-1]))
            if atr_value != atr_value:  # NaN check
                return Decimal("0")
            return (
                highest_close - self._trailing_atr_mult * atr_value
            ).quantize(Decimal("0.00000001"), rounding=ROUND_HALF_UP)
        except (KeyError, IndexError):
            return Decimal("0")

    def _compute_short_trailing_stop(
        self,
        lowest_close: Decimal,
        market_data: MarketData,
    ) -> Decimal:
        """Trailing stop for shorts: lowest_close + (ATR × multiplier) — above entry."""
        try:
            atr_series = market_data.get_indicator("atr")
            atr_value = Decimal(str(atr_series.iloc[-1]))
            if atr_value != atr_value:  # NaN check
                return Decimal("0")
            return (
                lowest_close + self._trailing_atr_mult * atr_value
            ).quantize(Decimal("0.00000001"), rounding=ROUND_HALF_UP)
        except (KeyError, IndexError):
            return Decimal("0")

    def _count_candles_held(
        self,
        position: Position,
        market_data: MarketData,
    ) -> int:
        """Count how many candles the position has been held."""
        current_time = market_data.candles.index[-1]
        if hasattr(current_time, "to_pydatetime"):
            current_time = current_time.to_pydatetime()
        if current_time.tzinfo is not None:
            current_time = current_time.replace(tzinfo=None)
        entry_time = position.entry_time
        if entry_time.tzinfo is not None:
            entry_time = entry_time.replace(tzinfo=None)
        elapsed_minutes = (current_time - entry_time).total_seconds() / 60
        if self._timeframe_minutes <= 0:
            return 0
        return int(elapsed_minutes / self._timeframe_minutes)
