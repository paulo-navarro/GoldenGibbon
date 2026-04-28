"""
Risk engine – position sizing, stop-loss checks, and signal validation.

Sits between strategy ``decide()`` and the portfolio / executor layer.
Translates raw ``Signal`` values into concrete ``RiskDecision`` objects
that carry the authorised action, computed size, and initial stop levels.

Trailing stop logic (ATR-based) and hard stop (percentage-based with
cooldown) are both implemented in ``check_stops()``.
"""

from datetime import date, datetime, timezone
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

        # Trailing stop toggle
        self._trailing_enabled = bool(
            strategy_config.get("trailing_stop_enabled", True)
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

        # Max daily new entries (OPEN + SCALE_IN combined); None = unlimited
        _max_daily = self._risk_config.get("max_trades_per_day")
        self._max_daily_trades: Optional[int] = (
            int(_max_daily) if _max_daily is not None else None
        )

        # Per-trade absolute notional cap in USDT; None = no cap (4.6)
        _max_trade = self._risk_config.get("max_trade_size_usdt")
        self._max_trade_size_usdt: Optional[Decimal] = (
            Decimal(str(_max_trade)) if _max_trade is not None else None
        )

        # Per-symbol absolute exposure cap in USDT; None = no cap (4.6)
        _max_sym = self._risk_config.get("max_symbol_exposure_usdt")
        self._max_symbol_exposure_usdt: Optional[Decimal] = (
            Decimal(str(_max_sym)) if _max_sym is not None else None
        )

        # Daily open counter: {UTC date: count} — reset automatically each day
        self._daily_opens: Dict[date, int] = {}

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
        self._time_stop_skip_profitable = bool(
            strategy_config.get("time_stop_skip_profitable", True)
        )

        # Break-even ratchet
        self._ratchet_enabled = bool(
            strategy_config.get("breakeven_ratchet_enabled", True)
        )
        self._breakeven_trigger = Decimal(
            str(strategy_config.get("breakeven_trigger_pct", 0.02))
        )
        self._lockin_trigger = Decimal(
            str(strategy_config.get("lockin_trigger_pct", 0.04))
        )
        self._lockin_stop_pct = Decimal(
            str(strategy_config.get("lockin_stop_pct", 0.01))
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

    def get_daily_opens(self, day: Optional[date] = None) -> int:
        """Return the number of entries opened on *day* (defaults to today UTC)."""
        key = day if day is not None else self._today_utc()
        return self._daily_opens.get(key, 0)

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

        # ── Break-even ratchet (all strategies) ─────────────────────
        ratcheted_hard_stop = position.hard_stop_price
        if (
            self._ratchet_enabled
            and position.hard_stop_price > 0
            and position.entry_price > 0
        ):
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
                        symbol=symbol,
                        old_stop=str(position.hard_stop_price),
                        new_stop=str(new_stop),
                        profit_pct=str(profit_pct),
                        entry_price=str(position.entry_price),
                        close=str(close),
                    )
                ratcheted_hard_stop = new_stop

        # ── Hard stop (all strategies) ───────────────────────────────
        if ratcheted_hard_stop > 0 and close < ratcheted_hard_stop:
            logger.info(
                "risk.hard_stop",
                symbol=symbol,
                close=str(close),
                hard_stop=str(ratcheted_hard_stop),
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
                hard_stop_price=ratcheted_hard_stop,
                cooldown_candles=self._cooldown_candles,
            )

        # ── Time stop (Mean Reversion only) ──────────────────────────
        if self._strategy_name == "mean_reversion":
            candles_held = self._count_candles_held(position, market_data)
            if candles_held >= self._time_stop_candles:
                unrealized = (close - position.entry_price) / position.entry_price
                if self._time_stop_skip_profitable and unrealized > 0:
                    logger.info(
                        "risk.time_stop_skipped_profitable",
                        symbol=symbol,
                        candles_held=candles_held,
                        unrealized_pct=str(unrealized),
                    )
                else:
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
                        hard_stop_price=ratcheted_hard_stop,
                        cooldown_candles=self._time_stop_cooldown,
                    )
        # ── Trailing stop (all strategies, when enabled) ────────────
        if self._trailing_enabled:
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
                    hard_stop_price=ratcheted_hard_stop,
                )

            # No stop hit – return updated levels for the caller to persist
            return StopCheckResult(
                highest_close=highest,
                trailing_stop_price=new_trailing,
                hard_stop_price=ratcheted_hard_stop,
            )

        # Trailing disabled – return hard stop only
        return StopCheckResult(
            hard_stop_price=ratcheted_hard_stop,
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

        # ── Daily trade limit check (4.4) ────────────────────────────
        if self._is_daily_limit_reached():
            logger.info(
                "risk.daily_limit_reached",
                symbol=symbol,
                max_daily_trades=self._max_daily_trades,
                opens_today=self.get_daily_opens(),
            )
            return RiskDecision(action=RiskAction.HOLD)

        # ── Per-symbol exposure check (4.6) ──────────────────────────
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

        # ── Daily trade limit check (4.4) ────────────────────────────
        if self._is_daily_limit_reached():
            logger.info(
                "risk.daily_limit_reached",
                symbol=symbol,
                action="scale_in",
                max_daily_trades=self._max_daily_trades,
                opens_today=self.get_daily_opens(),
            )
            return RiskDecision(action=RiskAction.HOLD)

        # ── Per-symbol exposure check (4.6) ──────────────────────────
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

        # Per-trade absolute notional cap (4.6)
        if self._max_trade_size_usdt is not None:
            notional = min(notional, self._max_trade_size_usdt)

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

        Checks indicator state to determine the most likely cause
        for a SELL_FULL signal:

        * EMA50 < EMA200 → ``EMA_CROSS``
        * 2+ closes below EMA200 → ``CLOSE_BELOW_EMA200``
        * ADX falling while price declining → ``MOMENTUM_FADE``
        * Otherwise defaults to ``EMA_CROSS``.
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

    # ── Private: limit helpers (4.4 / 4.6) ──────────────────────────

    def _today_utc(self) -> date:
        return datetime.now(timezone.utc).date()

    def _is_daily_limit_reached(self) -> bool:
        """Return True if max_trades_per_day has been reached for today."""
        if self._max_daily_trades is None:
            return False
        return self.get_daily_opens() >= self._max_daily_trades

    def _increment_daily_opens(self) -> None:
        """Record one more entry open for today."""
        today = self._today_utc()
        self._daily_opens[today] = self._daily_opens.get(today, 0) + 1

    def _is_symbol_exposure_exceeded(
        self,
        symbol: str,
        portfolio: Portfolio,
        close: Decimal,
    ) -> bool:
        """
        Return True if opening a new position would breach the per-symbol
        exposure cap.

        Checks current open position value (if any) against
        ``max_symbol_exposure_usdt``.  The check is conservative:
        if a position is already open and its notional already exceeds
        the cap, it blocks further entries.
        """
        if self._max_symbol_exposure_usdt is None:
            return False
        position = portfolio.positions.get(symbol)
        if position is None:
            return False
        current_exposure = position.size * close
        return current_exposure >= self._max_symbol_exposure_usdt
