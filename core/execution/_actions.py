"""
Action handlers: open, scale-in, close, reduce, exchange stop sync.

Split from ``binance.py`` for readability — mixed back in via inheritance.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, Optional

import structlog

from core.models import (
    ExecutionResult,
    ExitReason,
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
    PositionSide,
    RiskAction,
    RiskDecision,
)

logger = structlog.get_logger(__name__)


class _ActionsMixin:
    """Action execution handlers (open, close, scale-in, reduce)."""

    # ── Public: exchange stop sync ───────────────────────────────────────

    def check_exchange_stop(self, symbol: str) -> Optional[Dict[str, Any]]:
        """
        Check if the exchange-side stop order for *symbol* has been filled.

        Returns a dict with ``status`` and ``fill_price`` if filled,
        ``None`` if no stop order exists, feature is off, or status is NEW.
        """
        if not self._exchange_stops_enabled:
            return None

        pos = self._pm.get_position(symbol)
        if pos is None or not pos.exchange_stop_order_id:
            return None

        if pos.side == PositionSide.SHORT:
            info = self._check_futures_order_status(symbol, pos.exchange_stop_order_id)
        else:
            info = self._check_stop_order_status(symbol, pos.exchange_stop_order_id)
        if info is None:
            return None

        status = info.get("status", "")

        if status == "FILLED":
            fill_price = Decimal(info.get("price", "0"))
            if "fills" in info and info["fills"]:
                total_qty = sum(Decimal(f["qty"]) for f in info["fills"])
                total_cost = sum(Decimal(f["qty"]) * Decimal(f["price"]) for f in info["fills"])
                if total_qty > 0:
                    fill_price = total_cost / total_qty
            elif info.get("cummulativeQuoteQty") and info.get("executedQty"):
                exec_qty = Decimal(info["executedQty"])
                if exec_qty > 0:
                    fill_price = Decimal(info["cummulativeQuoteQty"]) / exec_qty

            logger.info(
                "binance.exchange_stop_filled",
                symbol=symbol,
                order_id=pos.exchange_stop_order_id,
                fill_price=str(fill_price),
            )
            return {"status": "FILLED", "fill_price": fill_price}

        if status in ("CANCELED", "EXPIRED", "UNKNOWN"):
            logger.warning(
                "binance.exchange_stop_gone",
                symbol=symbol,
                order_id=pos.exchange_stop_order_id,
                status=status,
            )
            self._pm.update_stop_order_id(symbol, None)

        return None

    # ── Private: buy-side ────────────────────────────────────────────────

    def _execute_open(
        self,
        decision: RiskDecision,
        timestamp: datetime,
    ) -> Optional[ExecutionResult]:
        """OPEN -> place order and open new position."""
        is_short = decision.side == PositionSide.SHORT

        if is_short:
            self._assert_shorts_enabled()
            order = self._place_margin_order(
                decision, OrderSide.SELL, intent="open_short"
            )
        else:
            order = self._place_and_fill(decision, OrderSide.BUY, intent="open_long")

        if order is None or order.status != OrderStatus.FILLED:
            logger.error(
                "binance.open_failed",
                symbol=decision.symbol,
                side=decision.side.value,
                size=str(decision.size),
                order_status=order.status.value if order else "no_order",
                reject_reason=order.reject_reason if order else "unknown",
            )
            return None

        fill_price = order.avg_fill_price or decision.price
        filled_size = order.filled_amount

        # BUG-014: the exchange order already filled — ensure PM can track
        # it.  An untracked position is far worse than an adjusted balance.
        self._ensure_balance_for_cost(
            decision.symbol, filled_size * fill_price,
        )

        position = self._pm.open_position(
            symbol=decision.symbol,
            size=filled_size,
            entry_price=fill_price,
            entry_time=timestamp,
            hard_stop_price=decision.hard_stop_price or Decimal("0"),
            trailing_stop_price=decision.trailing_stop_price or Decimal("0"),
            strategy=self._strategy,
            side=decision.side,
        )

        logger.info(
            "binance.open",
            symbol=decision.symbol,
            side=decision.side.value,
            size=str(filled_size),
            fill_price=str(fill_price),
            exchange_order_id=order.exchange_order_id,
        )

        if self._exchange_stops_enabled and decision.hard_stop_price:
            if is_short:
                stop_order_id = self._place_margin_stop_order(
                    decision.symbol, filled_size,
                    decision.hard_stop_price, self._stop_slippage,
                )
            else:
                stop_order_id = self._place_stop_order(
                    decision.symbol, filled_size,
                    decision.hard_stop_price, self._stop_slippage,
                )
            if stop_order_id:
                self._pm.update_stop_order_id(decision.symbol, stop_order_id)

        return ExecutionResult(order=order, position=position)

    def _execute_scale_in(
        self,
        decision: RiskDecision,
        timestamp: datetime,
    ) -> Optional[ExecutionResult]:
        """SCALE_IN -> buy, add to position."""
        order = self._place_and_fill(decision, OrderSide.BUY, intent="scale_in")
        if order is None or order.status != OrderStatus.FILLED:
            logger.error(
                "binance.scale_in_failed",
                symbol=decision.symbol,
                size=str(decision.size),
                order_status=order.status.value if order else "no_order",
                reject_reason=order.reject_reason if order else "unknown",
            )
            return None

        fill_price = order.avg_fill_price or decision.price
        filled_size = order.filled_amount

        # BUG-014: same safety net as _execute_open.
        self._ensure_balance_for_cost(
            decision.symbol, filled_size * fill_price,
        )

        position = self._pm.scale_in(
            symbol=decision.symbol,
            additional_size=filled_size,
            price=fill_price,
            time=timestamp,
        )

        logger.info(
            "binance.scale_in",
            symbol=decision.symbol,
            size=str(filled_size),
            fill_price=str(fill_price),
        )

        if self._exchange_stops_enabled:
            old_stop_id = self._pm.get_position(decision.symbol)
            if old_stop_id and old_stop_id.exchange_stop_order_id:
                self._cancel_stop_order(decision.symbol, old_stop_id.exchange_stop_order_id)
            stop_price = position.hard_stop_price
            if stop_price and stop_price > 0:
                new_stop_id = self._place_stop_order(
                    decision.symbol, position.size,
                    stop_price, self._stop_slippage,
                )
                if new_stop_id:
                    self._pm.update_stop_order_id(decision.symbol, new_stop_id)

        return ExecutionResult(order=order, position=position)

    # ── Private: sell-side ───────────────────────────────────────────────

    def _execute_close(
        self,
        decision: RiskDecision,
        timestamp: datetime,
    ) -> Optional[ExecutionResult]:
        """CLOSE -> exit entire position (spot sell for longs, margin buy for shorts)."""
        pos = self._pm.get_position(decision.symbol)
        is_short = pos is not None and pos.side == PositionSide.SHORT

        if is_short:
            # Short close: futures BUY — no spot balance needed
            order = self._place_margin_order(
                decision, OrderSide.BUY, intent="close_short"
            )
        else:
            # Long close: spot SELL with balance clamping
            real_balance = self._get_available_balance(decision.symbol)

            # If free balance is 0 but we have an exchange stop order, the balance
            # is locked by that stop. Cancel it first to unlock the asset.
            if real_balance is not None and real_balance <= Decimal("0"):
                if pos and pos.exchange_stop_order_id:
                    logger.info(
                        "binance.close_cancel_stop_to_unlock",
                        symbol=decision.symbol,
                        order_id=pos.exchange_stop_order_id,
                    )
                    self._cancel_stop_order(decision.symbol, pos.exchange_stop_order_id)
                    self._pm.update_stop_order_id(decision.symbol, None)
                    # Re-query balance after cancel
                    real_balance = self._get_available_balance(decision.symbol)

            if real_balance is not None and real_balance < decision.size:
                logger.warning(
                    "binance.close_clamped_to_real_balance",
                    symbol=decision.symbol,
                    requested=str(decision.size),
                    available=str(real_balance),
                )
                decision = decision.model_copy(update={"size": real_balance})

            formatted_qty = self._format_quantity(decision.symbol, decision.size)
            if formatted_qty <= 0:
                if real_balance is not None and real_balance > Decimal("0"):
                    formatted_real = self._format_quantity(decision.symbol, real_balance)
                    if formatted_real > Decimal("0"):
                        logger.warning(
                            "binance.dust_close_avoided",
                            symbol=decision.symbol,
                            pm_size=str(decision.size),
                            real_balance=str(real_balance),
                        )
                        decision = decision.model_copy(update={"size": real_balance})
                    else:
                        logger.info(
                            "binance.dust_close",
                            symbol=decision.symbol,
                            real_balance=str(real_balance),
                        )
                        return self._local_dust_close(decision, timestamp, is_short)
                elif real_balance is None:
                    logger.error(
                        "binance.close_aborted_balance_unknown",
                        symbol=decision.symbol,
                        pm_size=str(decision.size),
                    )
                    return None
                else:
                    logger.warning(
                        "binance.dust_close_exchange_zero",
                        symbol=decision.symbol,
                        pm_size=str(decision.size),
                    )
                    return self._local_dust_close(decision, timestamp, is_short)

            order = self._place_and_fill(decision, OrderSide.SELL, intent="close_long")

        if order is None or order.status != OrderStatus.FILLED:
            logger.error(
                "binance.close_failed",
                symbol=decision.symbol,
                size=str(decision.size),
                order_status=order.status.value if order else "no_order",
                reject_reason=order.reject_reason if order else "unknown",
            )
            return None

        # Cancel the exchange stop order only AFTER the close is confirmed.
        if self._exchange_stops_enabled:
            pos = self._pm.get_position(decision.symbol)
            if pos and pos.exchange_stop_order_id:
                if is_short:
                    self._cancel_futures_order(decision.symbol, pos.exchange_stop_order_id)
                else:
                    self._cancel_stop_order(decision.symbol, pos.exchange_stop_order_id)
                self._pm.update_stop_order_id(decision.symbol, None)

        fill_price = order.avg_fill_price or decision.price

        trade = self._pm.close_position(
            symbol=decision.symbol,
            exit_price=fill_price,
            exit_time=timestamp,
            exit_reason=decision.exit_reason or ExitReason.MANUAL,
            strategy=self._strategy,
        )

        logger.info(
            "binance.close",
            symbol=decision.symbol,
            size=str(trade.size),
            fill_price=str(fill_price),
            pnl_usdt=str(trade.pnl_usdt),
        )

        return ExecutionResult(order=order, trade=trade)

    def _local_dust_close(
        self, decision: RiskDecision, timestamp: datetime,
        is_short: bool = False,
    ) -> ExecutionResult:
        """Close position locally without exchange order (true dust only)."""
        trade = self._pm.close_position(
            symbol=decision.symbol,
            exit_price=decision.price,
            exit_time=timestamp,
            exit_reason=decision.exit_reason or ExitReason.MANUAL,
            strategy=self._strategy,
        )
        order_side = OrderSide.BUY if is_short else OrderSide.SELL
        dust_order = Order(
            symbol=decision.symbol,
            side=order_side,
            order_type=OrderType.MARKET,
            amount=decision.size,
            price=decision.price,
            status=OrderStatus.FILLED,
            avg_fill_price=decision.price,
            filled_amount=decision.size,
        )
        return ExecutionResult(order=dust_order, trade=trade)

    def _execute_reduce(
        self,
        decision: RiskDecision,
        timestamp: datetime,
    ) -> Optional[ExecutionResult]:
        """REDUCE -> partial exit (spot sell for longs, margin buy for shorts)."""
        pos = self._pm.get_position(decision.symbol)
        is_short = pos is not None and pos.side == PositionSide.SHORT

        if self._exchange_stops_enabled:
            if pos and pos.exchange_stop_order_id:
                if is_short:
                    self._cancel_futures_order(decision.symbol, pos.exchange_stop_order_id)
                else:
                    self._cancel_stop_order(decision.symbol, pos.exchange_stop_order_id)
                self._pm.update_stop_order_id(decision.symbol, None)

        # decision.size is the FULL position size from the risk engine.
        # Compute the actual sell quantity using sell_fraction.
        sell_fraction = decision.sell_fraction or Decimal("0.5")
        sell_qty = decision.size * sell_fraction
        decision = decision.model_copy(update={"size": sell_qty})

        if is_short:
            # Short reduce: futures BUY — no spot balance needed
            order = self._place_margin_order(
                decision, OrderSide.BUY, intent="reduce"
            )
        else:
            # Long reduce: spot SELL with balance clamping
            real_balance = self._get_available_balance(decision.symbol)
            if real_balance is not None and real_balance < decision.size:
                logger.warning(
                    "binance.reduce_clamped_to_real_balance",
                    symbol=decision.symbol,
                    requested=str(decision.size),
                    available=str(real_balance),
                )
                decision = decision.model_copy(update={"size": real_balance})

            order = self._place_and_fill(decision, OrderSide.SELL, intent="reduce")

        if order is None or order.status != OrderStatus.FILLED:
            logger.error(
                "binance.reduce_failed",
                symbol=decision.symbol,
                size=str(decision.size),
                order_status=order.status.value if order else "no_order",
                reject_reason=order.reject_reason if order else "unknown",
            )
            return None

        fill_price = order.avg_fill_price or decision.price

        trade = self._pm.reduce_position(
            symbol=decision.symbol,
            sell_fraction=decision.sell_fraction or Decimal("0.5"),
            exit_price=fill_price,
            exit_time=timestamp,
            exit_reason=decision.exit_reason or ExitReason.MOMENTUM_FADE,
            strategy=self._strategy,
        )

        logger.info(
            "binance.reduce",
            symbol=decision.symbol,
            size=str(trade.size),
            fill_price=str(fill_price),
            sell_fraction=str(decision.sell_fraction),
        )

        if self._exchange_stops_enabled:
            remaining = self._pm.get_position(decision.symbol)
            if remaining and remaining.hard_stop_price > 0:
                if is_short:
                    new_stop_id = self._place_margin_stop_order(
                        decision.symbol, remaining.size,
                        remaining.hard_stop_price, self._stop_slippage,
                    )
                else:
                    new_stop_id = self._place_stop_order(
                        decision.symbol, remaining.size,
                        remaining.hard_stop_price, self._stop_slippage,
                    )
                if new_stop_id:
                    self._pm.update_stop_order_id(decision.symbol, new_stop_id)

        return ExecutionResult(order=order, trade=trade)

    # ── Private: balance helpers ─────────────────────────────────────────

    def _ensure_balance_for_cost(
        self, symbol: str, notional_cost: Decimal,
    ) -> None:
        """Top up PM balance if it can't cover *notional_cost* (+ fee).

        Called after an exchange order already filled to guarantee the PM
        will accept the position.  Only adjusts when needed; in paper mode
        the balance is authoritative so this is normally a no-op.
        """
        fee = self._pm._apply_fee(notional_cost)
        total = notional_cost + fee
        balance = self._pm._portfolio.usdt_balance
        if total <= balance:
            return
        logger.warning(
            "binance.balance_forced_for_fill",
            symbol=symbol,
            shortfall=str(total - balance),
            balance_before=str(balance),
            total_cost=str(total),
        )
        self._pm._portfolio.usdt_balance = total

    def _get_available_balance(self, symbol: str) -> Optional[Decimal]:
        """Query Binance for the free balance of the base asset in *symbol* (e.g. 'HBAR' from 'HBARUSDT')."""
        base_asset = symbol.replace("USDT", "")
        try:
            info = self.get_account_info()
            bal = info["balances"].get(base_asset, {})
            return bal.get("free", Decimal("0"))
        except Exception as exc:
            logger.warning("binance.balance_query_failed", symbol=symbol, error=str(exc))
            return None
