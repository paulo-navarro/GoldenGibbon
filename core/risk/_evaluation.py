"""
Signal-evaluation mixin for RiskEngine.

Dispatches strategy signals (BUY, SELL_FULL, SELL_HALF, SHORT, HOLD)
into concrete RiskDecision objects with sizing and stop levels.
"""

from decimal import Decimal
from typing import Optional

import structlog

from core.models import (
    ExitReason,
    MarketData,
    Portfolio,
    Position,
    PositionSide,
    RiskAction,
    RiskDecision,
    Signal,
)

logger = structlog.get_logger(__name__)


class EvaluationMixin:
    """Signal evaluation methods extracted from RiskEngine."""

    def evaluate(
        self,
        signal: Signal,
        market_data: MarketData,
        portfolio: Portfolio,
    ) -> RiskDecision:
        """
        Translate a raw strategy signal into a sized risk decision.

        Args:
            signal: Signal produced by ``strategy.decide()``.
            market_data: Current market snapshot (for price / ATR).
            portfolio: Current portfolio state.

        Returns:
            A ``RiskDecision`` the executor can act on.
        """
        symbol = market_data.symbol
        close = Decimal(str(market_data.candles["close"].iloc[-1]))
        position = portfolio.positions.get(symbol)

        # ── HOLD ─────────────────────────────────────────────────────
        if signal == Signal.HOLD:
            if position is not None:
                self._buy_signal_candles[symbol] = 0
            return RiskDecision(action=RiskAction.HOLD)

        # ── SELL_FULL → CLOSE ────────────────────────────────────────
        if signal == Signal.SELL_FULL:
            self._buy_signal_candles[symbol] = 0
            if position is None:
                return RiskDecision(action=RiskAction.HOLD)
            decision = RiskDecision(
                action=RiskAction.CLOSE,
                symbol=symbol,
                side=position.side,
                size=position.size,
                price=close,
                exit_reason=self._infer_exit_reason_full(market_data),
            )
            logger.info(
                "risk.evaluate",
                action=decision.action.value,
                symbol=symbol,
                signal=signal.value,
                size=str(position.size),
                price=str(close),
            )
            return decision

        # ── SELL_HALF → REDUCE ───────────────────────────────────────
        if signal == Signal.SELL_HALF:
            self._buy_signal_candles[symbol] = 0
            if position is None:
                return RiskDecision(action=RiskAction.HOLD)
            decision = RiskDecision(
                action=RiskAction.REDUCE,
                symbol=symbol,
                side=position.side,
                size=position.size,
                price=close,
                exit_reason=ExitReason.MOMENTUM_FADE,
                sell_fraction=self._sell_half_fraction,
            )
            logger.info(
                "risk.evaluate",
                action=decision.action.value,
                symbol=symbol,
                signal=signal.value,
                size=str(position.size),
                price=str(close),
                sell_fraction=str(self._sell_half_fraction),
            )
            return decision

        # ── BUY ──────────────────────────────────────────────────────
        if signal == Signal.BUY:
            if position is None:
                decision = self._evaluate_open(symbol, close, portfolio, market_data)
            else:
                decision = self._evaluate_scale_in(
                    symbol, close, portfolio, position, market_data
                )
            if decision.action != RiskAction.HOLD:
                logger.info(
                    "risk.evaluate",
                    action=decision.action.value,
                    symbol=symbol,
                    signal=signal.value,
                    size=str(decision.size),
                    price=str(close),
                )
            return decision

        # ── SHORT ────────────────────────────────────────────────────
        if signal == Signal.SHORT:
            if not self._shorts_enabled:
                logger.info(
                    "risk.short_disabled",
                    symbol=symbol,
                    signal=signal.value,
                )
                return RiskDecision(action=RiskAction.HOLD)

            short_position = self._find_short_position(symbol, portfolio)
            if short_position is not None:
                return RiskDecision(action=RiskAction.HOLD)

            decision = self._evaluate_short_open(
                symbol, close, portfolio, market_data
            )
            if decision.action != RiskAction.HOLD:
                logger.info(
                    "risk.evaluate",
                    action=decision.action.value,
                    symbol=symbol,
                    signal=signal.value,
                    side="short",
                    size=str(decision.size),
                    price=str(close),
                )
            return decision

        return RiskDecision(action=RiskAction.HOLD)  # pragma: no cover

    # ── Open (long) ──────────────────────────────────────────────────

    def _evaluate_open(
        self,
        symbol: str,
        close: Decimal,
        portfolio: Portfolio,
        market_data: MarketData,
    ) -> RiskDecision:
        """Size and authorise a new long position."""
        self._buy_signal_candles[symbol] = 1

        if self._is_daily_limit_reached():
            logger.info(
                "risk.daily_limit_reached",
                symbol=symbol,
                max_daily_trades=self._max_daily_trades,
                opens_today=self.get_daily_opens(),
            )
            return RiskDecision(action=RiskAction.HOLD)

        if self._is_symbol_exposure_exceeded(symbol, portfolio, close):
            logger.info(
                "risk.symbol_exposure_exceeded",
                symbol=symbol,
                max_symbol_exposure_usdt=str(self._max_symbol_exposure_usdt),
            )
            return RiskDecision(action=RiskAction.HOLD)

        size = self._calculate_initial_size(portfolio, close)
        if size <= 0:
            return RiskDecision(action=RiskAction.HOLD)

        hard_stop = self._compute_hard_stop(close)
        trailing_stop = self._compute_trailing_stop(close, market_data)

        self._increment_daily_opens()
        return RiskDecision(
            action=RiskAction.OPEN,
            symbol=symbol,
            size=size,
            price=close,
            hard_stop_price=hard_stop,
            trailing_stop_price=trailing_stop,
        )

    # ── Open (short) ─────────────────────────────────────────────────

    @staticmethod
    def _find_short_position(
        symbol: str, portfolio: Portfolio
    ) -> Optional[Position]:
        """Return the short position for *symbol*, or None."""
        pos = portfolio.positions.get(symbol)
        if pos is not None and pos.side == PositionSide.SHORT:
            return pos
        return None

    def _evaluate_short_open(
        self,
        symbol: str,
        close: Decimal,
        portfolio: Portfolio,
        market_data: MarketData,
    ) -> RiskDecision:
        """Size and authorise a new short position."""
        if self._is_daily_limit_reached():
            logger.info(
                "risk.daily_limit_reached",
                symbol=symbol,
                action="short_open",
                max_daily_trades=self._max_daily_trades,
                opens_today=self.get_daily_opens(),
            )
            return RiskDecision(action=RiskAction.HOLD)

        if self._is_symbol_exposure_exceeded(symbol, portfolio, close):
            logger.info(
                "risk.symbol_exposure_exceeded",
                symbol=symbol,
                action="short_open",
                max_symbol_exposure_usdt=str(self._max_symbol_exposure_usdt),
            )
            return RiskDecision(action=RiskAction.HOLD)

        size = self._calculate_initial_size(portfolio, close)
        if size <= 0:
            return RiskDecision(action=RiskAction.HOLD)

        hard_stop = self._compute_short_hard_stop(close)
        trailing_stop = self._compute_short_trailing_stop(close, market_data)

        self._increment_daily_opens()
        return RiskDecision(
            action=RiskAction.OPEN,
            symbol=symbol,
            side=PositionSide.SHORT,
            size=size,
            price=close,
            hard_stop_price=hard_stop,
            trailing_stop_price=trailing_stop,
        )

    # ── Scale-in ─────────────────────────────────────────────────────

    def _evaluate_scale_in(
        self,
        symbol: str,
        close: Decimal,
        portfolio: Portfolio,
        position: Position,
        market_data: MarketData,
    ) -> RiskDecision:
        """Check scale-in eligibility and size the tranche."""
        count = self._buy_signal_candles.get(symbol, 0) + 1
        self._buy_signal_candles[symbol] = count

        # Mean Reversion never scales in
        if self._strategy_name == "mean_reversion":
            return RiskDecision(action=RiskAction.HOLD)

        if position.scale_in_count >= 2:
            return RiskDecision(action=RiskAction.HOLD)

        # Determine which tranche is next
        if position.scale_in_count == 0 and count >= self._scale_1_candles:
            pct = self._scale_1_pct
        elif position.scale_in_count == 1 and count >= self._scale_2_candles:
            pct = self._scale_2_pct
        else:
            return RiskDecision(action=RiskAction.HOLD)

        if self._is_daily_limit_reached():
            logger.info(
                "risk.daily_limit_reached",
                symbol=symbol,
                action="scale_in",
                max_daily_trades=self._max_daily_trades,
                opens_today=self.get_daily_opens(),
            )
            return RiskDecision(action=RiskAction.HOLD)

        if self._is_symbol_exposure_exceeded(symbol, portfolio, close):
            logger.info(
                "risk.symbol_exposure_exceeded",
                symbol=symbol,
                action="scale_in",
                max_symbol_exposure_usdt=str(self._max_symbol_exposure_usdt),
            )
            return RiskDecision(action=RiskAction.HOLD)

        size = self._size_from_pct(pct, portfolio, close)
        if size <= 0:
            return RiskDecision(action=RiskAction.HOLD)

        self._increment_daily_opens()
        return RiskDecision(
            action=RiskAction.SCALE_IN,
            symbol=symbol,
            size=size,
            price=close,
        )

    # ── Exit reason inference ────────────────────────────────────────

    @staticmethod
    def _infer_exit_reason_full(market_data: MarketData) -> ExitReason:
        """
        Best-effort inference of exit reason from indicators.

        Checks indicator state to determine the most likely cause
        for a SELL_FULL signal.
        """
        indicators = market_data.indicators
        if not indicators:
            return ExitReason.EMA_CROSS

        # EMA cross: fast below slow
        ema_fast = indicators.get("ema_fast")
        if ema_fast is None:
            ema_fast = indicators.get("ema_50")
        ema_slow = indicators.get("ema_slow")
        if ema_slow is None:
            ema_slow = indicators.get("ema_200")
        if ema_fast is not None and ema_slow is not None:
            if len(ema_fast) > 0 and len(ema_slow) > 0:
                if ema_fast.iloc[-1] < ema_slow.iloc[-1]:
                    return ExitReason.EMA_CROSS

        # Consecutive closes below EMA200
        if ema_slow is not None and not market_data.candles.empty:
            close = market_data.candles["close"]
            if len(close) >= 2 and len(ema_slow) >= 2:
                if close.iloc[-1] < ema_slow.iloc[-1] and close.iloc[-2] < ema_slow.iloc[-2]:
                    return ExitReason.CLOSE_BELOW_EMA200

        # Momentum fade: ADX declining
        adx = indicators.get("adx")
        if adx is not None and len(adx) >= 2:
            if adx.iloc[-1] < adx.iloc[-2]:
                return ExitReason.MOMENTUM_FADE

        return ExitReason.EMA_CROSS
