"""
BinanceExecutor – real order execution against the Binance API.

Pipeline position:  Risk → **Executor** → Portfolio

Translates a ``RiskDecision`` into a real Binance order by:

1. Formatting quantity/price to comply with exchange filters (LOT_SIZE,
   PRICE_FILTER).
2. Placing the order via signed REST request.
3. Polling until terminal status (MARKET fills instantly; LIMIT may need
   polling or cancellation on timeout).
4. Delegating balance/position mutations to ``PortfolioManager``.
5. Returning an ``ExecutionResult`` matching the PaperExecutor contract.

Credentials are read from environment variables (never config files).
"""

from __future__ import annotations

import hashlib
import hmac
import json as _json
import os
import time as _time
from datetime import datetime, timezone
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP, InvalidOperation
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlencode

import requests
import structlog
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from core.events import EventChannel, EventType, get_publisher
from core.execution.retry import RetryConfig, RetryExhausted, with_retry
from core.models import (
    ExecutionResult,
    ExitReason,
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
    RiskAction,
    RiskDecision,
    TimeInForce,
    Trade,
)
from core.portfolio import PortfolioManager

logger = structlog.get_logger(__name__)

# ── Binance error codes ──────────────────────────────────────────────────────

# Permanent errors — never retry these
_PERMANENT_CODES = frozenset({
    -1013,  # Filter failure (LOT_SIZE, PRICE_FILTER, MIN_NOTIONAL)
    -1021,  # Timestamp outside recvWindow
    -1100,  # Illegal characters in a parameter
    -1102,  # Mandatory parameter missing
    -2010,  # Insufficient balance (NEW_ORDER_REJECTED)
    -2011,  # Unknown order (cancel non-existent)
    -1116,  # Invalid order type
    -1117,  # Invalid side
})

# Retryable errors — transient, worth retrying
_RETRYABLE_CODES = frozenset({
    -1003,  # Too many requests (rate limit)
    -1015,  # Too many new orders
})


class BinanceAPIError(Exception):
    """Raised when Binance returns an error response."""

    def __init__(self, code: int, msg: str) -> None:
        self.code = code
        self.msg = msg
        super().__init__(f"Binance API error {code}: {msg}")

    @property
    def is_retryable(self) -> bool:
        return self.code in _RETRYABLE_CODES

    @property
    def is_permanent(self) -> bool:
        return self.code in _PERMANENT_CODES


class BinanceExecutor:
    """
    Real executor for live trading on Binance.

    Same ``execute(decision, timestamp)`` contract as PaperExecutor.
    """

    # ── Endpoints ────────────────────────────────────────────────────────

    _PROD_BASE = "https://api.binance.com"
    _TESTNET_BASE = "https://testnet.binance.vision"

    # ── Construction ─────────────────────────────────────────────────────

    def __init__(
        self,
        strategy_name: str,
        portfolio_manager: PortfolioManager,
        api_key: str,
        api_secret: str,
        use_testnet: bool = True,
        max_order_size_usdt: float = 1000.0,
        order_timeout: int = 30,
        retry_config: Optional[RetryConfig] = None,
    ) -> None:
        self._strategy = strategy_name
        self._pm = portfolio_manager
        self._api_key = api_key
        self._api_secret = api_secret.encode("utf-8")
        self._base_url = self._TESTNET_BASE if use_testnet else self._PROD_BASE
        self._max_order_size_usdt = Decimal(str(max_order_size_usdt))
        self._order_timeout = order_timeout
        self._retry_config = retry_config or RetryConfig(
            max_attempts=3,
            base_delay=1.0,
            max_delay=30.0,
        )

        # HTTP session with connection-level retries for transient errors
        self._session = requests.Session()
        adapter = HTTPAdapter(
            max_retries=Retry(
                total=3,
                backoff_factor=1,
                status_forcelist=[500, 502, 503, 504],
            )
        )
        self._session.mount("https://", adapter)
        self._session.headers.update({
            "X-MBX-APIKEY": self._api_key,
        })

        # Exchange filters cache: symbol → {lot_step, price_step, min_notional}
        self._filters: Dict[str, Dict[str, Decimal]] = {}

    # ── Properties ───────────────────────────────────────────────────────

    @property
    def strategy_name(self) -> str:
        return self._strategy

    @property
    def portfolio_manager(self) -> PortfolioManager:
        return self._pm

    # ── Public: load exchange info ───────────────────────────────────────

    def load_exchange_filters(self, symbols: Optional[List[str]] = None) -> None:
        """
        Fetch LOT_SIZE, PRICE_FILTER, and MIN_NOTIONAL filters from
        ``GET /api/v3/exchangeInfo`` and cache them per symbol.

        Should be called once on startup. If *symbols* is None, loads all.
        """
        url = f"{self._base_url}/api/v3/exchangeInfo"
        params = {}
        if symbols:
            params["symbols"] = str(symbols).replace("'", '"')

        resp = self._session.get(url, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()

        for sym_info in data.get("symbols", []):
            symbol = sym_info["symbol"]
            filters: Dict[str, Decimal] = {}

            for f in sym_info.get("filters", []):
                if f["filterType"] == "LOT_SIZE":
                    filters["lot_step"] = Decimal(f["stepSize"])
                    filters["lot_min"] = Decimal(f["minQty"])
                    filters["lot_max"] = Decimal(f["maxQty"])
                elif f["filterType"] == "PRICE_FILTER":
                    filters["price_step"] = Decimal(f["tickSize"])
                elif f["filterType"] == "NOTIONAL" or f["filterType"] == "MIN_NOTIONAL":
                    filters["min_notional"] = Decimal(
                        f.get("minNotional", f.get("notional", "10"))
                    )

            if filters:
                self._filters[symbol] = filters

        logger.info(
            "binance.exchange_filters_loaded",
            symbols_count=len(self._filters),
        )

    # ── Main entry point ─────────────────────────────────────────────────

    def execute(
        self,
        decision: RiskDecision,
        timestamp: datetime,
    ) -> Optional[ExecutionResult]:
        """
        Execute a risk decision against Binance.

        Same contract as PaperExecutor.execute().
        """
        action = decision.action

        if action == RiskAction.HOLD:
            return None

        # Safety gate: max order size
        notional = decision.size * decision.price
        if notional > self._max_order_size_usdt:
            logger.error(
                "binance.order_rejected_size_limit",
                symbol=decision.symbol,
                notional=str(notional),
                max_allowed=str(self._max_order_size_usdt),
            )
            return None

        result: Optional[ExecutionResult] = None

        if action == RiskAction.OPEN:
            result = self._execute_open(decision, timestamp)
        elif action == RiskAction.SCALE_IN:
            result = self._execute_scale_in(decision, timestamp)
        elif action == RiskAction.CLOSE:
            result = self._execute_close(decision, timestamp)
        elif action == RiskAction.REDUCE:
            result = self._execute_reduce(decision, timestamp)

        if result is not None:
            publisher = get_publisher()
            publisher.publish(
                EventChannel.EXECUTION,
                EventType.ORDER_FILLED,
                {
                    "action": action.value,
                    **result.order.model_dump(mode="json"),
                },
            )

        return result

    # ── Private: buy-side ────────────────────────────────────────────────

    def _execute_open(
        self,
        decision: RiskDecision,
        timestamp: datetime,
    ) -> Optional[ExecutionResult]:
        """OPEN -> buy, open new position."""
        order = self._place_and_fill(decision, OrderSide.BUY)
        if order is None or order.status != OrderStatus.FILLED:
            logger.error(
                "binance.open_failed",
                symbol=decision.symbol,
                size=str(decision.size),
                order_status=order.status.value if order else "no_order",
                reject_reason=order.reject_reason if order else "unknown",
            )
            return None

        fill_price = order.avg_fill_price or decision.price
        filled_size = order.filled_amount

        position = self._pm.open_position(
            symbol=decision.symbol,
            size=filled_size,
            entry_price=fill_price,
            entry_time=timestamp,
            hard_stop_price=decision.hard_stop_price or Decimal("0"),
            trailing_stop_price=decision.trailing_stop_price or Decimal("0"),
            strategy=self._strategy,
        )

        logger.info(
            "binance.open",
            symbol=decision.symbol,
            size=str(filled_size),
            fill_price=str(fill_price),
            exchange_order_id=order.exchange_order_id,
        )

        return ExecutionResult(order=order, position=position)

    def _execute_scale_in(
        self,
        decision: RiskDecision,
        timestamp: datetime,
    ) -> Optional[ExecutionResult]:
        """SCALE_IN -> buy, add to position."""
        order = self._place_and_fill(decision, OrderSide.BUY)
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

        return ExecutionResult(order=order, position=position)

    # ── Private: sell-side ───────────────────────────────────────────────

    def _execute_close(
        self,
        decision: RiskDecision,
        timestamp: datetime,
    ) -> Optional[ExecutionResult]:
        """CLOSE -> sell entire position."""
        real_balance = self._get_available_balance(decision.symbol)
        if real_balance is not None and real_balance < decision.size:
            logger.warning(
                "binance.close_clamped_to_real_balance",
                symbol=decision.symbol,
                requested=str(decision.size),
                available=str(real_balance),
            )
            decision = decision.model_copy(update={"size": real_balance})

        order = self._place_and_fill(decision, OrderSide.SELL)
        if order is None or order.status != OrderStatus.FILLED:
            logger.error(
                "binance.close_failed",
                symbol=decision.symbol,
                size=str(decision.size),
                order_status=order.status.value if order else "no_order",
                reject_reason=order.reject_reason if order else "unknown",
            )
            return None

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

    def _execute_reduce(
        self,
        decision: RiskDecision,
        timestamp: datetime,
    ) -> Optional[ExecutionResult]:
        """REDUCE -> sell fraction of position."""
        real_balance = self._get_available_balance(decision.symbol)
        if real_balance is not None and real_balance < decision.size:
            logger.warning(
                "binance.reduce_clamped_to_real_balance",
                symbol=decision.symbol,
                requested=str(decision.size),
                available=str(real_balance),
            )
            decision = decision.model_copy(update={"size": real_balance})

        order = self._place_and_fill(decision, OrderSide.SELL)
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

        return ExecutionResult(order=order, trade=trade)

    # ── Private: balance helper ───────────────────────────────────────────

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

    # ── Private: order placement + fill ──────────────────────────────────

    def _place_and_fill(
        self,
        decision: RiskDecision,
        side: OrderSide,
        order_type: OrderType = OrderType.MARKET,
        limit_price: Optional[Decimal] = None,
        time_in_force: Optional[TimeInForce] = None,
    ) -> Optional[Order]:
        """
        Place an order on Binance and wait for fill.

        Returns a filled Order on success, or an Order with REJECTED/
        CANCELLED status on failure. Returns None only on unexpected errors.
        """
        symbol = decision.symbol
        qty = self._format_quantity(symbol, decision.size)

        if qty <= 0:
            logger.error("binance.zero_quantity_after_format", symbol=symbol)
            return None

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
            return Order(
                symbol=symbol,
                side=side,
                order_type=order_type,
                amount=qty,
                price=decision.price,
                status=OrderStatus.REJECTED,
                reject_reason=f"Retry exhausted: {exc.last_error}",
            )
        except Exception as exc:
            logger.exception(
                "binance.order_unexpected_error",
                symbol=symbol,
            )
            return None

        # Parse Binance response
        exchange_order_id = str(response["orderId"])
        exchange_status = response.get("status", "")

        # MARKET orders are filled immediately in the response
        if order_type == OrderType.MARKET:
            return self._parse_fill_response(
                response, decision, side, order_type, qty, time_in_force
            )

        # LIMIT orders may need polling
        if exchange_status == "FILLED":
            return self._parse_fill_response(
                response, decision, side, order_type, qty, time_in_force
            )

        # Poll for fill
        return self._poll_until_terminal(
            symbol=symbol,
            exchange_order_id=exchange_order_id,
            decision=decision,
            side=side,
            order_type=order_type,
            qty=qty,
            time_in_force=time_in_force,
        )

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
        fills = response.get("fills", [])
        if fills:
            avg_price, total_qty, total_fee = self._aggregate_fills(fills)
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
    ) -> Tuple[Decimal, Decimal, Decimal]:
        """
        Aggregate fills array into (avg_price, total_qty, total_fee_usdt).

        Each fill: {"price": "...", "qty": "...", "commission": "...",
                     "commissionAsset": "..."}
        """
        total_cost = Decimal("0")
        total_qty = Decimal("0")
        total_fee = Decimal("0")

        for fill in fills:
            price = Decimal(fill["price"])
            qty = Decimal(fill["qty"])
            commission = Decimal(fill["commission"])

            total_cost += price * qty
            total_qty += qty

            # Approximate fee in USDT (if commission asset is BNB/BTC,
            # this is an approximation — close enough for tracking)
            if fill.get("commissionAsset") in ("USDT", "BUSD", "FDUSD"):
                total_fee += commission
            else:
                # Rough estimate: commission in base asset × fill price
                total_fee += commission * price

        avg_price = (total_cost / total_qty) if total_qty > 0 else Decimal("0")
        return avg_price, total_qty, total_fee

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

    # ── Private: cancel order ────────────────────────────────────────────

    def _cancel_order(self, symbol: str, order_id: str) -> None:
        """Best-effort cancel of an open order."""
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

    # ── Public: account query (task 4.8) ──────────────────────────────────

    def get_account_info(self) -> Dict[str, Any]:
        """
        Fetch Binance account balances via ``GET /api/v3/account``.

        Returns a dict with::

            {
                "balances": {"USDT": {"free": Decimal, "locked": Decimal}, ...},
                "can_trade": bool,
            }

        Only includes assets with non-zero balance.

        Raises :class:`BinanceAPIError` on API errors.
        """
        data = self._signed_request("GET", "/api/v3/account")

        balances: Dict[str, Dict[str, Decimal]] = {}
        for item in data.get("balances", []):
            free = Decimal(item["free"])
            locked = Decimal(item["locked"])
            if free > 0 or locked > 0:
                balances[item["asset"]] = {"free": free, "locked": locked}

        return {
            "balances": balances,
            "can_trade": data.get("canTrade", False),
        }

    def get_ticker_prices(self, symbols: list[str]) -> Dict[str, Decimal]:
        """
        Fetch current prices for a list of symbols via the public ticker endpoint.

        Returns ``{"BTCUSDT": Decimal("67500.12"), ...}``.
        Symbols with no data are silently omitted.
        """
        prices: Dict[str, Decimal] = {}
        if not symbols:
            return prices

        try:
            resp = self._session.get(
                f"{self._base_url}/api/v3/ticker/price",
                params={"symbols": _json.dumps(symbols)},
                timeout=10,
            )
            resp.raise_for_status()
            for item in resp.json():
                prices[item["symbol"]] = Decimal(item["price"])
        except Exception as exc:
            logger.warning("get_ticker_prices: failed", error=str(exc))

        return prices

    # ── Private: signed HTTP requests ────────────────────────────────────

    def _signed_request(
        self,
        method: str,
        path: str,
        params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Send a signed request to Binance.

        Adds ``timestamp`` and ``signature`` (HMAC-SHA256) to every request.
        """
        params = dict(params or {})
        params["timestamp"] = int(_time.time() * 1000)
        params["recvWindow"] = 5000

        query_string = urlencode(params)
        signature = hmac.new(
            self._api_secret,
            query_string.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        params["signature"] = signature

        url = f"{self._base_url}{path}"

        if method == "GET":
            resp = self._session.get(url, params=params, timeout=10)
        elif method == "POST":
            resp = self._session.post(url, params=params, timeout=10)
        elif method == "DELETE":
            resp = self._session.delete(url, params=params, timeout=10)
        else:
            raise ValueError(f"Unsupported HTTP method: {method}")

        data = resp.json()

        # Check for Binance error response
        if "code" in data and data["code"] < 0:
            raise BinanceAPIError(code=data["code"], msg=data.get("msg", ""))

        return data

    # ── Factory ──────────────────────────────────────────────────────────

    @classmethod
    def from_settings(
        cls,
        strategy_name: str,
        portfolio_manager: PortfolioManager,
    ) -> "BinanceExecutor":
        """
        Build a BinanceExecutor from application settings.

        Reads API credentials from environment variables configured in
        ``LiveTradingConfig``.
        """
        from core.config import get_settings

        settings = get_settings()
        live = settings.live_trading
        execution = settings.execution

        api_key = live.api_key
        api_secret = live.api_secret

        if not api_key or not api_secret:
            raise RuntimeError(
                "Binance API credentials not configured. "
                "Set api_key and api_secret in the live_trading config."
            )

        retry_cfg = RetryConfig(
            max_attempts=execution.max_retries,
            base_delay=float(execution.retry_delay),
            max_delay=30.0,
        )

        executor = cls(
            strategy_name=strategy_name,
            portfolio_manager=portfolio_manager,
            api_key=api_key,
            api_secret=api_secret,
            use_testnet=live.use_testnet,
            max_order_size_usdt=live.max_order_size_usdt,
            order_timeout=execution.order_timeout,
            retry_config=retry_cfg,
        )

        return executor
