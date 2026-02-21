"""
Risk engine – position sizing, stop-loss checks, and signal validation.

Sits between strategy ``decide()`` and the portfolio / executor layer.
Translates raw ``Signal`` values into concrete ``RiskDecision`` objects
that carry the authorised action, computed size, and initial stop levels.

Trailing stop logic (ATR-based) and hard stop (percentage-based with
cooldown) are both implemented in ``check_stops()``.
"""

from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP
from typing import Any, Dict, Optional

import structlog

from core.models import (
    ExitReason,
    MarketData,
    Portfolio,
    Position,
    RiskAction,
    RiskDecision,
    Signal,
    StopCheckResult,
)

logger = structlog.get_logger(__name__)


class RiskEngine:
    """
    Evaluates strategy signals and produces sized, validated risk decisions.

    Responsibilities:
      - Position sizing (strategy-specific: Smart Hodler scaled entries,
        Mean Reversion flat 75 %).
      - Scale-in eligibility & consecutive BUY-candle tracking.
      - Stop-check framework (``check_stops`` – currently returns None;
        wired by tasks 1.15 / 1.16).
      - Exposure caps (``max_position_size_pct``).
    """

    # ── Construction ─────────────────────────────────────────────────────

    def __init__(
        self,
        strategy_name: str,
        strategy_config: Dict[str, Any],
        risk_config: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        Args:
            strategy_name: ``"smart_hodler"`` or ``"mean_reversion"``.
            strategy_config: Strategy-specific section from strategies.yaml.
            risk_config: Global risk section from settings.yaml.
                         Falls back to sensible defaults when None.
        """
        self._strategy_name = strategy_name
        self._strategy_config = strategy_config
        self._risk_config = risk_config or {}

        # ── Resolved sizing params ───────────────────────────────────
        if strategy_name == "smart_hodler":
            self._initial_pct = Decimal(
                str(strategy_config.get("entry_initial_pct", 0.50))
            )
            self._scale_1_pct = Decimal(
                str(strategy_config.get("entry_scale_1_pct", 0.25))
            )
            self._scale_2_pct = Decimal(
                str(strategy_config.get("entry_scale_2_pct", 0.25))
            )
            self._scale_1_candles = int(
                strategy_config.get("entry_scale_1_candles", 8)
            )
            self._scale_2_candles = int(
                strategy_config.get("entry_scale_2_candles", 16)
            )
            self._sell_half_fraction = Decimal(
                str(strategy_config.get("exit_momentum_fade_pct", 0.50))
            )
        elif strategy_name == "mean_reversion":
            self._initial_pct = Decimal(
                str(strategy_config.get("entry_pct", 0.75))
            )
            self._sell_half_fraction = Decimal(
                str(strategy_config.get("exit_partial_pct", 0.50))
            )
        else:
            raise ValueError(f"Unknown strategy: {strategy_name}")

        # Hard-stop percentage (used to compute initial hard_stop_price)
        self._hard_stop_pct = Decimal(
            str(strategy_config.get("hard_stop_pct", 0.03))
        )

        # Trailing-stop ATR multiplier
        self._trailing_atr_mult = Decimal(
            str(
                strategy_config.get(
                    "trailing_stop_atr_multiplier",
                    self._risk_config.get("trailing_stop_atr_multiplier", 2.0),
                )
            )
        )

        # Max position size as fraction of equity (global cap)
        self._max_position_pct = Decimal(
            str(self._risk_config.get("max_position_size_pct", 1.0))
        )

        # Cooldown candles after a hard-stop exit (SH: 16, MR: 8)
        self._cooldown_candles = int(
            strategy_config.get("cooldown_candles", 16)
        )

        # Time stop (Mean Reversion only)
        self._time_stop_candles = int(
            strategy_config.get("time_stop_candles", 16)
        )
        self._time_stop_cooldown = int(
            strategy_config.get("time_stop_cooldown_candles", 4)
        )

        # Primary timeframe in minutes (for candle counting)
        tf_str = strategy_config.get("timeframe_primary", "15m")
        self._timeframe_minutes = self._parse_timeframe_minutes(tf_str)

        # Internal counter: consecutive BUY candles for scale-in timing
        self._buy_signal_candles: Dict[str, int] = {}

    # ── Properties ───────────────────────────────────────────────────────

    @property
    def strategy_name(self) -> str:
        return self._strategy_name

    def get_buy_signal_candles(self, symbol: str) -> int:
        """Return the consecutive BUY-candle count for *symbol*."""
        return self._buy_signal_candles.get(symbol, 0)

    # ── Main evaluation ──────────────────────────────────────────────────

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
                # New position
                decision = self._evaluate_open(symbol, close, portfolio, market_data)
            else:
                # Potential scale-in (Smart Hodler only)
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

        # Fallback (should never happen)
        return RiskDecision(action=RiskAction.HOLD)  # pragma: no cover

    # ── Stop-check logic ───────────────────────────────────────────

    def check_stops(
        self,
        market_data: MarketData,
        portfolio: Portfolio,
    ) -> StopCheckResult:
        """
        Scan the position for ``market_data.symbol`` and check stops.

        **Hard stop** (both strategies):
        If the current close drops strictly below ``position.hard_stop_price``
        the position is closed immediately and ``cooldown_candles`` is set so
        the caller can block re-entry.

        **Time stop** (Mean Reversion only):
        If the position has been held for >= ``time_stop_candles`` (default 16),
        close it with ``ExitReason.TIME_STOP`` and enter a short cooldown
        (``time_stop_cooldown_candles``, default 4).

        **Trailing stop** (Smart Hodler only):
        1. Ratchets ``highest_close`` upward if the current close is a
           new high.
        2. Recomputes the trailing stop from the (possibly new) highest
           close and the latest ATR reading.
        3. Checks whether the current close breaches the trailing stop.

        Mean Reversion positions skip the trailing stop (by design –
        see ``strategy_mean_reversion.md``).

        Returns:
            A ``StopCheckResult`` that always carries the refreshed
            ``highest_close`` and ``trailing_stop_price`` (when
            applicable), and optionally a ``RiskDecision`` to close
            the position if a stop was hit.  ``cooldown_candles`` is
            populated on hard-stop and time-stop exits.
        """
        symbol = market_data.symbol
        position = portfolio.positions.get(symbol)

        # No position → nothing to do
        if position is None:
            return StopCheckResult()

        close = Decimal(str(market_data.candles["close"].iloc[-1]))

        # ── Hard stop (all strategies) ───────────────────────────────
        if position.hard_stop_price > 0 and close < position.hard_stop_price:
            logger.info(
                "risk.hard_stop",
                symbol=symbol,
                close=str(close),
                hard_stop=str(position.hard_stop_price),
                cooldown=self._cooldown_candles,
            )
            decision = RiskDecision(
                action=RiskAction.CLOSE,
                symbol=symbol,
                size=position.size,
                price=close,
                exit_reason=ExitReason.HARD_STOP,
            )
            return StopCheckResult(
                decision=decision,
                highest_close=max(position.highest_close, close),
                trailing_stop_price=position.trailing_stop_price,
                cooldown_candles=self._cooldown_candles,
            )

        # ── Time stop (Mean Reversion only) ──────────────────────────
        if self._strategy_name == "mean_reversion":
            candles_held = self._count_candles_held(position, market_data)
            if candles_held >= self._time_stop_candles:
                logger.info(
                    "risk.time_stop",
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
                    cooldown_candles=self._time_stop_cooldown,
                )
            # MR has no trailing stop
            return StopCheckResult()

        # ── Trailing stop (Smart Hodler) ─────────────────────────────
        # 1. Ratchet highest_close
        highest = max(position.highest_close, close)

        # 2. Recompute trailing stop from (possibly new) highest close
        new_trailing = self._compute_trailing_stop(highest, market_data)

        # Only ratchet the stop upward; never lower it
        if position.trailing_stop_price > 0:
            new_trailing = max(new_trailing, position.trailing_stop_price)

        # 3. Check trailing-stop violation (strict less-than)
        if new_trailing > 0 and close < new_trailing:
            logger.info(
                "risk.trailing_stop",
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
            )

        # No stop hit – return updated levels for the caller to persist
        return StopCheckResult(
            highest_close=highest,
            trailing_stop_price=new_trailing,
        )

    # ── Private: open position sizing ────────────────────────────────

    def _evaluate_open(
        self,
        symbol: str,
        close: Decimal,
        portfolio: Portfolio,
        market_data: MarketData,
    ) -> RiskDecision:
        """Size and authorise a new position."""
        self._buy_signal_candles[symbol] = 1

        size = self._calculate_initial_size(portfolio, close)
        if size <= 0:
            return RiskDecision(action=RiskAction.HOLD)

        hard_stop = self._compute_hard_stop(close)
        trailing_stop = self._compute_trailing_stop(close, market_data)

        return RiskDecision(
            action=RiskAction.OPEN,
            symbol=symbol,
            size=size,
            price=close,
            hard_stop_price=hard_stop,
            trailing_stop_price=trailing_stop,
        )

    # ── Private: scale-in sizing ─────────────────────────────────────

    def _evaluate_scale_in(
        self,
        symbol: str,
        close: Decimal,
        portfolio: Portfolio,
        position: Position,
        market_data: MarketData,
    ) -> RiskDecision:
        """Check scale-in eligibility and size the tranche."""
        # Increment consecutive BUY counter
        count = self._buy_signal_candles.get(symbol, 0) + 1
        self._buy_signal_candles[symbol] = count

        # Mean Reversion never scales in
        if self._strategy_name == "mean_reversion":
            return RiskDecision(action=RiskAction.HOLD)

        # Smart Hodler scaled entries
        if position.scale_in_count >= 2:
            return RiskDecision(action=RiskAction.HOLD)

        # Determine which tranche is next
        if position.scale_in_count == 0 and count >= self._scale_1_candles:
            pct = self._scale_1_pct
        elif position.scale_in_count == 1 and count >= self._scale_2_candles:
            pct = self._scale_2_pct
        else:
            return RiskDecision(action=RiskAction.HOLD)

        size = self._size_from_pct(pct, portfolio, close)
        if size <= 0:
            return RiskDecision(action=RiskAction.HOLD)

        return RiskDecision(
            action=RiskAction.SCALE_IN,
            symbol=symbol,
            size=size,
            price=close,
        )

    # ── Private: sizing helpers ──────────────────────────────────────

    def _calculate_initial_size(
        self,
        portfolio: Portfolio,
        price: Decimal,
    ) -> Decimal:
        """
        Compute the base-asset quantity for the first entry.

        Applies the strategy-specific ``initial_pct`` to
        ``available_capital``, capped by ``max_position_size_pct``
        relative to equity.
        """
        return self._size_from_pct(self._initial_pct, portfolio, price)

    def _size_from_pct(
        self,
        pct: Decimal,
        portfolio: Portfolio,
        price: Decimal,
    ) -> Decimal:
        """
        Convert a capital-percentage into a base-asset quantity,
        respecting the global max position size cap.
        """
        if price <= 0:
            return Decimal("0")

        desired_notional = portfolio.available_capital * pct

        # Cap at max_position_size_pct × equity
        max_notional = portfolio.equity * self._max_position_pct
        notional = min(desired_notional, max_notional)

        size = (notional / price).quantize(
            Decimal("0.00000001"), rounding=ROUND_DOWN
        )
        if size <= 0:
            return Decimal("0")
        return size

    # ── Private: stop computation ────────────────────────────────────

    def _compute_hard_stop(self, entry_price: Decimal) -> Decimal:
        """Hard stop: entry_price × (1 − hard_stop_pct)."""
        return (entry_price * (1 - self._hard_stop_pct)).quantize(
            Decimal("0.00000001"), rounding=ROUND_HALF_UP
        )

    def _compute_trailing_stop(
        self,
        highest_close: Decimal,
        market_data: MarketData,
    ) -> Decimal:
        """
        Trailing stop: highest_close − (ATR × multiplier).

        Falls back to ``Decimal("0")`` when ATR is unavailable.
        """
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

    # ── Private: exit reason inference ───────────────────────────────

    @staticmethod
    def _infer_exit_reason_full(market_data: MarketData) -> ExitReason:
        """
        Best-effort inference of exit reason from indicators.

        For now returns ``EMA_CROSS`` as a default for SELL_FULL.
        The backtest runner / caller can override when more context
        is available.
        """
        return ExitReason.EMA_CROSS

    # ── Private: time-stop helpers ───────────────────────────────────

    def _count_candles_held(
        self,
        position: Position,
        market_data: MarketData,
    ) -> int:
        """
        Count how many candles the position has been held.

        Uses elapsed time between ``position.entry_time`` and the
        current candle's timestamp, divided by the primary timeframe.
        """
        current_time = market_data.candles.index[-1]
        # Handle pandas Timestamp → datetime
        if hasattr(current_time, "to_pydatetime"):
            current_time = current_time.to_pydatetime()
        elapsed_minutes = (current_time - position.entry_time).total_seconds() / 60
        if self._timeframe_minutes <= 0:
            return 0
        return int(elapsed_minutes / self._timeframe_minutes)

    @staticmethod
    def _parse_timeframe_minutes(tf: str) -> int:
        """Convert timeframe string (e.g. '15m', '1h') to minutes."""
        mapping = {
            "1m": 1, "5m": 5, "15m": 15, "30m": 30,
            "1h": 60, "4h": 240, "1d": 1440, "1w": 10080,
        }
        return mapping.get(tf, 15)
