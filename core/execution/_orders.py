"""
Order placement, polling, parsing, formatting, and cancellation.

Split from ``binance.py`` for readability — mixed back in via inheritance.
"""

from __future__ import annotations

import time as _time
from datetime import datetime, timezone
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP
from typing import Any, Dict, List, Optional, Tuple

import requests
import structlog

from core.events import EventChannel, EventType, get_publisher
from core.execution.retry import RetryExhausted, with_retry
from core.models import (
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
    PositionSide,
    RiskDecision,
    TimeInForce,
)

logger = structlog.get_logger(__name__)


class _OrdersMixin:
    """Order placement, polling, parsing, formatting, and cancellation."""

    # ── Private: margin order placement (Phase 5.6) ──────────────────────

    @staticmethod
    def _assert_shorts_enabled() -> None:
        """Defence-in-depth gate — raises if shorts are globally disabled."""
        from core.config import get_settings

        if not get_settings().shorts.enabled:
            raise RuntimeError(
                "Short selling is disabled (shorts.enabled=False). "
                "This code path should not have been reached."
            )

    def _get_margin_type(self) -> str:
        """Read margin_type from BearGuard config ('cross' or 'isolated')."""
        from core.config import get_settings

        return get_settings().strategies.bear_guard.margin_type

    def _place_margin_order(
        self,
        decision: RiskDecision,
        side: OrderSide,
        side_effect_type: str,
        intent: Optional[str] = None,
    ) -> Optional[Order]:
        """
        Place an order on Binance Margin and wait for fill.

        Args:
            decision: Risk decision with symbol, size, price.
            side: BUY or SELL.
            side_effect_type: "MARGIN_BUY" (borrow+sell) or "AUTO_REPAY" (buy+repay).
            intent: Order intent for write-ahead (e.g. "open_short", "close_short").

        Returns a filled Order on success, or an Order with REJECTED status
        on failure. Returns None only on unexpected errors.
        """
        from core.execution.binance import BinanceAPIError

        symbol = decision.symbol
        qty = self._format_quantity(symbol, decision.size)

        if qty <= 0:
            logger.error("binance.margin_zero_quantity", symbol=symbol)
            return None

        # Write-ahead: persist PENDING record before calling exchange
        effective_intent = intent
        if effective_intent is None:
            effective_intent = "open_short"
            logger.warning(
                "binance.intent_not_provided",
                symbol=symbol,
                side=side.value,
                default_intent=effective_intent,
            )
        record_id = self._write_ahead_order(
            symbol=symbol,
            side=side.value,
            order_type="market",
            amount=qty,
            price=decision.price,
            intent=effective_intent,
        )

        margin_type = self._get_margin_type()
        is_isolated = "TRUE" if margin_type == "isolated" else "FALSE"

        params: Dict[str, Any] = {
            "symbol": symbol,
            "side": side.value.upper(),
            "type": "MARKET",
            "quantity": str(qty),
            "sideEffectType": side_effect_type,
            "isIsolated": is_isolated,
        }

        # Publish ORDER_CREATED event
        publisher = get_publisher()
        pending_order = Order(
            symbol=symbol,
            side=side,
            order_type=OrderType.MARKET,
            amount=qty,
            price=decision.price,
            status=OrderStatus.PENDING,
        )
        publisher.publish_model(
            EventChannel.EXECUTION, EventType.ORDER_CREATED, pending_order
        )

        try:
            response = with_retry(
                fn=lambda: self._signed_request(
                    "POST", "/sapi/v1/margin/order", params
                ),
                config=self._retry_config,
                retryable=(requests.ConnectionError, requests.Timeout),
                label=f"binance.place_margin_order({symbol})",
            )
        except BinanceAPIError as exc:
            logger.error(
                "binance.margin_order_rejected",
                symbol=symbol,
                code=exc.code,
                msg=exc.msg,
                side_effect=side_effect_type,
            )
            self._update_order_record(
                record_id,
                status="rejected",
                reject_reason=exc.msg,
                exchange_status=str(exc.code),
                reconciliation_status="synced",
            )
            return Order(
                symbol=symbol,
                side=side,
                order_type=OrderType.MARKET,
                amount=qty,
                price=decision.price,
                status=OrderStatus.REJECTED,
                reject_reason=exc.msg,
                exchange_status=str(exc.code),
            )
        except RetryExhausted as exc:
            logger.error(
                "binance.margin_order_retry_exhausted",
                symbol=symbol,
                error=str(exc.last_error),
            )
            self._update_order_record(
                record_id,
                status="rejected",
                reject_reason=f"Retry exhausted: {exc.last_error}",
                reconciliation_status="synced",
            )
            return Order(
                symbol=symbol,
                side=side,
                order_type=OrderType.MARKET,
                amount=qty,
                price=decision.price,
                status=OrderStatus.REJECTED,
                reject_reason=f"Retry exhausted: {exc.last_error}",
            )
        except Exception:
            logger.exception(
                "binance.margin_order_unexpected_error", symbol=symbol
            )
            # Leave as PENDING — fill recovery will handle it
            return None

        # Parse the fill response (same format as spot)
        order = self._parse_fill_response(
            response=response,
            decision=decision,
            side=side,
            order_type=OrderType.MARKET,
            qty=qty,
            time_in_force=None,
        )

        self._update_order_record(
            record_id,
            status="filled",
            exchange_order_id=order.exchange_order_id,
            filled_amount=order.filled_amount,
            avg_fill_price=order.avg_fill_price,
            fee_usdt=order.fee_usdt,
            slippage_percent=order.slippage_percent,
            exchange_status=order.exchange_status,
        )

        return order

    def _place_margin_stop_order(
        self,
        symbol: str,
        quantity: Decimal,
        stop_price: Decimal,
        slippage_pct: Decimal = Decimal("0.005"),
    ) -> Optional[str]:
        """Place a margin stop-loss order for a short position (BUY side)."""
        from core.execution.binance import BinanceAPIError

        qty = self._format_quantity(symbol, quantity)
        if qty <= 0:
            logger.error("binance.margin_stop_zero_qty", symbol=symbol)
            return None

        formatted_stop = self._format_price(symbol, stop_price)
        # For shorts, stop is ABOVE entry — limit gives room above the stop
        limit_price = self._format_price(
            symbol, stop_price * (1 + slippage_pct),
        )

        # Write-ahead: persist stop order as PENDING
        record_id = self._write_ahead_order(
            symbol=symbol,
            side="buy",
            order_type="stop_loss_limit",
            amount=qty,
            price=limit_price,
            stop_price=formatted_stop,
            intent="stop_loss",
        )

        margin_type = self._get_margin_type()
        is_isolated = "TRUE" if margin_type == "isolated" else "FALSE"

        params: Dict[str, Any] = {
            "symbol": symbol,
            "side": "BUY",
            "type": "STOP_LOSS_LIMIT",
            "quantity": str(qty),
            "stopPrice": str(formatted_stop),
            "price": str(limit_price),
            "timeInForce": "GTC",
            "sideEffectType": "AUTO_REPAY",
            "isIsolated": is_isolated,
        }

        try:
            response = with_retry(
                fn=lambda: self._signed_request(
                    "POST", "/sapi/v1/margin/order", params
                ),
                config=self._retry_config,
                retryable=(requests.ConnectionError, requests.Timeout),
                label=f"binance.place_margin_stop({symbol})",
            )
        except (BinanceAPIError, RetryExhausted) as exc:
            logger.error(
                "binance.margin_stop_order_failed",
                symbol=symbol,
                stop_price=str(formatted_stop),
                error=str(exc),
            )
            self._update_order_record(
                record_id,
                status="rejected",
                reject_reason=str(exc),
                reconciliation_status="synced",
            )
            return None
        except Exception:
            logger.exception("binance.margin_stop_unexpected", symbol=symbol)
            # Leave as PENDING for recovery
            return None

        order_id = str(response["orderId"])
        logger.info(
            "binance.margin_stop_placed",
            symbol=symbol,
            stop_price=str(formatted_stop),
            limit_price=str(limit_price),
            quantity=str(qty),
            order_id=order_id,
        )

        # Update record with exchange_order_id (stop is still "pending" until triggered)
        self._update_order_record(
            record_id,
            exchange_order_id=order_id,
            exchange_status="NEW",
        )

        return order_id

    # ── Private: spot order placement + fill ─────────────────────────────

    def _place_and_fill(
        self,
        decision: RiskDecision,
        side: OrderSide,
        order_type: OrderType = OrderType.MARKET,
        limit_price: Optional[Decimal] = None,
        time_in_force: Optional[TimeInForce] = None,
        intent: Optional[str] = None,
    ) -> Optional[Order]:
        """
        Place an order on Binance and wait for fill.

        Returns a filled Order on success, or an Order with REJECTED/
        CANCELLED status on failure. Returns None only on unexpected errors.
        """
        from core.execution.binance import BinanceAPIError

        symbol = decision.symbol
        qty = self._format_quantity(symbol, decision.size)

        if qty <= 0:
            logger.error("binance.zero_quantity_after_format", symbol=symbol)
            return None

        # Write-ahead: persist PENDING record before calling exchange
        effective_intent = intent
        if effective_intent is None:
            effective_intent = "open_long"
            logger.warning(
                "binance.intent_not_provided",
                symbol=symbol,
                side=side.value,
                default_intent=effective_intent,
            )
        record_id = self._write_ahead_order(
            symbol=symbol,
            side=side.value,
            order_type=order_type.value,
            amount=qty,
            price=decision.price,
            intent=effective_intent,
        )

        # Build order params
        params: Dict[str, Any] = {
            "symbol": symbol,
            "side": side.value.upper(),
            "type": order_type.value.upper(),
            "quantity": str(qty),
        }

        if order_type == OrderType.LIMIT:
            tif = time_in_force or TimeInForce.GTC
            price = self._format_price(symbol, limit_price or decision.price)
            params["timeInForce"] = tif.value
            params["price"] = str(price)

        # Publish ORDER_CREATED event
        publisher = get_publisher()
        pending_order = Order(
            symbol=symbol,
            side=side,
            order_type=order_type,
            amount=qty,
            price=decision.price,
            status=OrderStatus.PENDING,
            limit_price=limit_price,
            time_in_force=time_in_force if order_type == OrderType.LIMIT else None,
        )
        publisher.publish_model(
            EventChannel.EXECUTION, EventType.ORDER_CREATED, pending_order
        )

        # Place the order with retry
        try:
            response = with_retry(
                fn=lambda: self._signed_request("POST", "/api/v3/order", params),
                config=self._retry_config,
                retryable=(requests.ConnectionError, requests.Timeout),
                label=f"binance.place_order({symbol})",
            )
        except BinanceAPIError as exc:
            logger.error(
                "binance.order_rejected",
                symbol=symbol,
                code=exc.code,
                msg=exc.msg,
            )
            self._update_order_record(
                record_id,
                status="rejected",
                reject_reason=exc.msg,
                exchange_status=str(exc.code),
                reconciliation_status="synced",
            )
            return Order(
                symbol=symbol,
                side=side,
                order_type=order_type,
                amount=qty,
                price=decision.price,
                status=OrderStatus.REJECTED,
                reject_reason=exc.msg,
                exchange_status=str(exc.code),
            )
        except RetryExhausted as exc:
            logger.error(
                "binance.order_retry_exhausted",
                symbol=symbol,
                error=str(exc.last_error),
            )
            self._update_order_record(
                record_id,
                status="rejected",
                reject_reason=f"Retry exhausted: {exc.last_error}",
                reconciliation_status="synced",
            )
            return Order(
                symbol=symbol,
                side=side,
                order_type=order_type,
                amount=qty,
                price=decision.price,
                status=OrderStatus.REJECTED,
                reject_reason=f"Retry exhausted: {exc.last_error}",
            )
        except Exception:
            logger.exception(
                "binance.order_unexpected_error",
                symbol=symbol,
            )
            # Leave as PENDING — fill recovery (6.5) will handle it
            return None

        # Parse Binance response
        exchange_order_id = str(response["orderId"])
        exchange_status = response.get("status", "")

        # MARKET orders are filled immediately in the response
        if order_type == OrderType.MARKET:
            order = self._parse_fill_response(
                response, decision, side, order_type, qty, time_in_force
            )
            self._update_order_record(
                record_id,
                status="filled",
                exchange_order_id=order.exchange_order_id,
                filled_amount=order.filled_amount,
                avg_fill_price=order.avg_fill_price,
                fee_usdt=order.fee_usdt,
                slippage_percent=order.slippage_percent,
                exchange_status=order.exchange_status,
            )
            return order

        # LIMIT orders may need polling
        if exchange_status == "FILLED":
            order = self._parse_fill_response(
                response, decision, side, order_type, qty, time_in_force
            )
            self._update_order_record(
                record_id,
                status="filled",
                exchange_order_id=order.exchange_order_id,
                filled_amount=order.filled_amount,
                avg_fill_price=order.avg_fill_price,
                fee_usdt=order.fee_usdt,
                slippage_percent=order.slippage_percent,
                exchange_status=order.exchange_status,
            )
            return order

        # Update exchange_order_id immediately (for recovery if crash during poll)
        self._update_order_record(
            record_id,
            exchange_order_id=exchange_order_id,
            exchange_status=exchange_status,
        )

        # Poll for fill
        order = self._poll_until_terminal(
            symbol=symbol,
            exchange_order_id=exchange_order_id,
            decision=decision,
            side=side,
            order_type=order_type,
            qty=qty,
            time_in_force=time_in_force,
        )

        # Update record with final state
        self._update_order_record(
            record_id,
            status=order.status.value,
            exchange_order_id=order.exchange_order_id,
            filled_amount=order.filled_amount,
            avg_fill_price=order.avg_fill_price,
            fee_usdt=order.fee_usdt,
            slippage_percent=order.slippage_percent,
            exchange_status=order.exchange_status,
            reject_reason=order.reject_reason,
            reconciliation_status="synced",
        )
        return order

    def _poll_until_terminal(
        self,
        symbol: str,
        exchange_order_id: str,
        decision: RiskDecision,
        side: OrderSide,
        order_type: OrderType,
        qty: Decimal,
        time_in_force: Optional[TimeInForce],
    ) -> Order:
        """
        Poll ``GET /api/v3/order`` until the order reaches a terminal state
        or the timeout expires (at which point we cancel it).
        """
        deadline = _time.monotonic() + self._order_timeout
        poll_interval = 1.0

        while _time.monotonic() < deadline:
            _time.sleep(poll_interval)

            try:
                response = self._signed_request(
                    "GET",
                    "/api/v3/order",
                    {"symbol": symbol, "orderId": exchange_order_id},
                )
            except Exception:
                logger.warning(
                    "binance.poll_error",
                    symbol=symbol,
                    exchange_order_id=exchange_order_id,
                )
                continue

            status = response.get("status", "")

            if status == "FILLED":
                return self._parse_fill_response(
                    response, decision, side, order_type, qty, time_in_force
                )
            elif status in ("CANCELED", "REJECTED", "EXPIRED"):
                return Order(
                    symbol=symbol,
                    side=side,
                    order_type=order_type,
                    amount=qty,
                    price=decision.price,
                    status=OrderStatus.CANCELLED if status == "CANCELED" else OrderStatus.REJECTED,
                    exchange_order_id=exchange_order_id,
                    exchange_status=status,
                    reject_reason=f"Exchange status: {status}",
                    time_in_force=time_in_force,
                )

        # Timeout — cancel the order
        logger.warning(
            "binance.order_timeout_cancelling",
            symbol=symbol,
            exchange_order_id=exchange_order_id,
        )
        self._cancel_order(symbol, exchange_order_id)

        return Order(
            symbol=symbol,
            side=side,
            order_type=order_type,
            amount=qty,
            price=decision.price,
            status=OrderStatus.CANCELLED,
            exchange_order_id=exchange_order_id,
            exchange_status="TIMEOUT_CANCELLED",
            reject_reason=f"Order not filled within {self._order_timeout}s",
            time_in_force=time_in_force,
        )

    # ── Private: response parsing ────────────────────────────────────────

    def _parse_fill_response(
        self,
        response: Dict[str, Any],
        decision: RiskDecision,
        side: OrderSide,
        order_type: OrderType,
        qty: Decimal,
        time_in_force: Optional[TimeInForce],
    ) -> Order:
        """Parse a Binance order response with fills into an Order model."""
        exchange_order_id = str(response["orderId"])

        # Compute weighted average fill price and total fees from fills array
        base_asset = decision.symbol.replace("USDT", "")
        fills = response.get("fills", [])
        if fills:
            avg_price, total_qty, total_fee, base_commission = self._aggregate_fills(
                fills, base_asset=base_asset,
            )
            if side == OrderSide.BUY and base_commission > 0:
                total_qty -= base_commission
        else:
            # Fallback: use cumulativeQuoteQty / executedQty
            exec_qty = Decimal(response.get("executedQty", str(qty)))
            cum_quote = Decimal(response.get("cumulativeQuoteQty", "0"))
            avg_price = (cum_quote / exec_qty) if exec_qty > 0 else decision.price
            total_qty = exec_qty
            total_fee = Decimal("0")

        # Compute slippage vs reference price
        slippage_pct = Decimal("0")
        if decision.price > 0:
            slippage_pct = (
                abs(avg_price - decision.price) / decision.price * 100
            ).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)

        now = datetime.now(timezone.utc)

        return Order(
            symbol=decision.symbol,
            side=side,
            order_type=order_type,
            amount=qty,
            price=decision.price,
            status=OrderStatus.FILLED,
            filled_amount=total_qty,
            avg_fill_price=avg_price,
            slippage_percent=slippage_pct,
            fee_usdt=total_fee,
            exchange_order_id=exchange_order_id,
            exchange_status=response.get("status", "FILLED"),
            time_in_force=time_in_force if order_type == OrderType.LIMIT else None,
            limit_price=Decimal(response["price"]) if order_type == OrderType.LIMIT and response.get("price") else None,
            created_at=now,
            updated_at=now,
            filled_at=now,
        )

    @staticmethod
    def _aggregate_fills(
        fills: List[Dict[str, str]],
        base_asset: str = "",
    ) -> Tuple[Decimal, Decimal, Decimal, Decimal]:
        """
        Aggregate fills array into (avg_price, total_qty, total_fee_usdt, base_commission).

        ``base_commission`` is the total commission paid in the base asset
        (e.g. INJ for INJUSDT).  For BUY orders this must be subtracted
        from ``total_qty`` to get the net received amount.

        Each fill: {"price": "...", "qty": "...", "commission": "...",
                     "commissionAsset": "..."}
        """
        total_cost = Decimal("0")
        total_qty = Decimal("0")
        total_fee = Decimal("0")
        base_commission = Decimal("0")

        for fill in fills:
            price = Decimal(fill["price"])
            qty = Decimal(fill["qty"])
            commission = Decimal(fill["commission"])
            commission_asset = fill.get("commissionAsset", "")

            total_cost += price * qty
            total_qty += qty

            if commission_asset == base_asset:
                base_commission += commission
                total_fee += commission * price
            elif commission_asset in ("USDT", "BUSD", "FDUSD"):
                total_fee += commission
            else:
                total_fee += commission * price

        avg_price = (total_cost / total_qty) if total_qty > 0 else Decimal("0")
        return avg_price, total_qty, total_fee, base_commission

    # ── Private: quantity / price formatting ─────────────────────────────

    def _format_quantity(self, symbol: str, qty: Decimal) -> Decimal:
        """
        Round quantity down to the exchange's LOT_SIZE step.

        If no filters are cached for this symbol, returns qty rounded to
        8 decimal places (safe default).
        """
        filters = self._filters.get(symbol)
        if not filters or "lot_step" not in filters:
            return qty.quantize(Decimal("0.00000001"), rounding=ROUND_DOWN)

        step = filters["lot_step"]
        # Round down to nearest step: floor(qty / step) * step
        formatted = (qty / step).to_integral_value(rounding=ROUND_DOWN) * step

        # Enforce min/max
        lot_min = filters.get("lot_min", Decimal("0"))
        lot_max = filters.get("lot_max", Decimal("99999999"))

        if formatted < lot_min:
            logger.warning(
                "binance.qty_below_min",
                symbol=symbol,
                qty=str(formatted),
                min_qty=str(lot_min),
            )
            return Decimal("0")

        if formatted > lot_max:
            formatted = lot_max

        return formatted

    def _format_price(self, symbol: str, price: Decimal) -> Decimal:
        """Round price to the exchange's PRICE_FILTER tick size."""
        filters = self._filters.get(symbol)
        if not filters or "price_step" not in filters:
            return price.quantize(Decimal("0.00000001"), rounding=ROUND_HALF_UP)

        step = filters["price_step"]
        return (price / step).to_integral_value(rounding=ROUND_HALF_UP) * step

    # ── Private: exchange stop orders ────────────────────────────────────

    def _place_stop_order(
        self,
        symbol: str,
        quantity: Decimal,
        stop_price: Decimal,
        slippage_pct: Decimal = Decimal("0.005"),
    ) -> Optional[str]:
        from core.execution.binance import BinanceAPIError

        qty = self._format_quantity(symbol, quantity)
        if qty <= 0:
            logger.error("binance.stop_order_zero_qty", symbol=symbol)
            return None

        formatted_stop = self._format_price(symbol, stop_price)
        limit_price = self._format_price(
            symbol, stop_price * (1 - slippage_pct),
        )

        # Write-ahead: persist stop order as PENDING
        record_id = self._write_ahead_order(
            symbol=symbol,
            side="sell",
            order_type="stop_loss_limit",
            amount=qty,
            price=limit_price,
            stop_price=formatted_stop,
            intent="stop_loss",
        )

        params: Dict[str, Any] = {
            "symbol": symbol,
            "side": "SELL",
            "type": "STOP_LOSS_LIMIT",
            "quantity": str(qty),
            "stopPrice": str(formatted_stop),
            "price": str(limit_price),
            "timeInForce": "GTC",
        }

        try:
            response = with_retry(
                fn=lambda: self._signed_request("POST", "/api/v3/order", params),
                config=self._retry_config,
                retryable=(requests.ConnectionError, requests.Timeout),
                label=f"binance.place_stop_order({symbol})",
            )
        except (BinanceAPIError, RetryExhausted) as exc:
            logger.error(
                "binance.stop_order_failed",
                symbol=symbol,
                stop_price=str(formatted_stop),
                error=str(exc),
            )
            self._update_order_record(
                record_id,
                status="rejected",
                reject_reason=str(exc),
                reconciliation_status="synced",
            )
            return None
        except Exception:
            logger.exception("binance.stop_order_unexpected", symbol=symbol)
            # Leave as PENDING for recovery
            return None

        order_id = str(response["orderId"])
        logger.info(
            "binance.stop_order_placed",
            symbol=symbol,
            stop_price=str(formatted_stop),
            limit_price=str(limit_price),
            quantity=str(qty),
            order_id=order_id,
        )

        # Update record with exchange_order_id (stop is still "pending" until triggered)
        self._update_order_record(
            record_id,
            exchange_order_id=order_id,
            exchange_status="NEW",
        )

        return order_id

    def _cancel_stop_order(self, symbol: str, order_id: str) -> bool:
        from core.execution.binance import BinanceAPIError

        try:
            self._signed_request(
                "DELETE", "/api/v3/order",
                {"symbol": symbol, "orderId": order_id},
            )
            logger.info(
                "binance.stop_order_cancelled",
                symbol=symbol,
                order_id=order_id,
            )
            return True
        except BinanceAPIError as exc:
            if exc.code == -2011:
                logger.info(
                    "binance.stop_order_already_gone",
                    symbol=symbol,
                    order_id=order_id,
                )
                return True
            logger.error(
                "binance.stop_order_cancel_failed",
                symbol=symbol,
                order_id=order_id,
                code=exc.code,
                msg=exc.msg,
            )
            return False
        except Exception:
            logger.exception(
                "binance.stop_order_cancel_unexpected",
                symbol=symbol,
                order_id=order_id,
            )
            return False

    def _check_stop_order_status(
        self, symbol: str, order_id: str,
    ) -> Optional[Dict[str, Any]]:
        from core.execution.binance import BinanceAPIError

        try:
            return self._signed_request(
                "GET", "/api/v3/order",
                {"symbol": symbol, "orderId": order_id},
            )
        except BinanceAPIError as exc:
            if exc.code == -2011:
                return {"status": "UNKNOWN", "orderId": order_id}
            logger.error(
                "binance.stop_order_status_failed",
                symbol=symbol,
                order_id=order_id,
                code=exc.code,
                msg=exc.msg,
            )
            return None
        except Exception:
            logger.exception(
                "binance.stop_order_status_unexpected",
                symbol=symbol,
                order_id=order_id,
            )
            return None

    # ── Private: cancel order ────────────────────────────────────────────

    def _cancel_order(self, symbol: str, order_id: str) -> None:
        """Best-effort cancel of an open order."""
        from core.execution.binance import BinanceAPIError

        try:
            self._signed_request(
                "DELETE",
                "/api/v3/order",
                {"symbol": symbol, "orderId": order_id},
            )
            logger.info(
                "binance.order_cancelled",
                symbol=symbol,
                order_id=order_id,
            )
        except BinanceAPIError as exc:
            if exc.code == -2011:
                # Unknown order — already cancelled or filled
                logger.info(
                    "binance.cancel_already_gone",
                    symbol=symbol,
                    order_id=order_id,
                )
            else:
                logger.error(
                    "binance.cancel_failed",
                    symbol=symbol,
                    order_id=order_id,
                    code=exc.code,
                    msg=exc.msg,
                )
        except Exception:
            logger.exception(
                "binance.cancel_unexpected_error",
                symbol=symbol,
                order_id=order_id,
            )
