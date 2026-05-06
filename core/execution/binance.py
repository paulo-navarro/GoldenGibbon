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
from core.execution._actions import _ActionsMixin
from core.execution._orders import _OrdersMixin
from core.execution.retry import RetryConfig, RetryExhausted, with_retry
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
    # Margin-specific errors
    -3003,  # Margin account does not exist
    -3005,  # Transfer out amount exceeds max
    -3007,  # Insufficient margin balance
    -3021,  # Margin account not active
    -3045,  # System doesn't have enough asset to borrow
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


class BinanceExecutor(_OrdersMixin, _ActionsMixin):
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
        exchange_stop_orders_enabled: bool = False,
        stop_limit_slippage_pct: float = 0.005,
        session_factory=None,
    ) -> None:
        self._strategy = strategy_name
        self._pm = portfolio_manager
        self._api_key = api_key
        self._api_secret = api_secret.encode("utf-8")
        self._base_url = self._TESTNET_BASE if use_testnet else self._PROD_BASE
        self._max_order_size_usdt = Decimal(str(max_order_size_usdt))
        self._order_timeout = order_timeout
        self._exchange_stops_enabled = exchange_stop_orders_enabled
        self._stop_slippage = Decimal(str(stop_limit_slippage_pct))
        self._session_factory = session_factory
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
    # ── Write-ahead order persistence (Phase 6.3) ────────────────────────

    @staticmethod
    def _derive_intent(action: RiskAction, side: PositionSide) -> str:
        """Map (RiskAction, PositionSide) to an OrderIntent string."""
        if action == RiskAction.OPEN:
            return "open_long" if side == PositionSide.LONG else "open_short"
        elif action == RiskAction.CLOSE:
            return "close_long" if side == PositionSide.LONG else "close_short"
        elif action == RiskAction.SCALE_IN:
            return "scale_in"
        elif action == RiskAction.REDUCE:
            return "reduce"
        return "open_long"

    def _write_ahead_order(
        self,
        symbol: str,
        side: str,
        order_type: str,
        amount: Decimal,
        price: Optional[Decimal],
        intent: str,
        trading_mode: str = "live",
        strategy: Optional[str] = None,
        stop_price: Optional[Decimal] = None,
    ) -> Optional[int]:
        """
        Create a PENDING OrderRecord in the DB before calling the exchange.

        Returns the DB record id on success, or None if session_factory is
        not configured (e.g. paper mode, tests without DB).
        """
        if self._session_factory is None:
            return None

        from core.models import OrderIntent
        from db.models import OrderRecord

        # Validate intent against enum — reject typos early
        valid_intents = {e.value for e in OrderIntent}
        if intent not in valid_intents:
            logger.critical(
                "write_ahead_order: invalid intent",
                symbol=symbol,
                side=side,
                intent=intent,
                valid=list(valid_intents),
            )
            return None

        try:
            with self._session_factory() as session:
                record = OrderRecord(
                    symbol=symbol,
                    side=side,
                    order_type=order_type,
                    amount=amount,
                    price=price,
                    stop_price=stop_price,
                    status="pending",
                    filled_amount=Decimal("0"),
                    intent=intent,
                    reconciliation_status="pending_sync",
                    trading_mode=trading_mode,
                    strategy=strategy or self._strategy,
                )
                session.add(record)
                session.flush()
                record_id = record.id
            return record_id
        except Exception:
            logger.critical(
                "write_ahead_order_failed",
                symbol=symbol,
                side=side,
                intent=intent,
                exc_info=True,
            )
            return None

    def _update_order_record(
        self,
        record_id: Optional[int],
        *,
        status: Optional[str] = None,
        exchange_order_id: Optional[str] = None,
        filled_amount: Optional[Decimal] = None,
        avg_fill_price: Optional[Decimal] = None,
        fee_usdt: Optional[Decimal] = None,
        slippage_percent: Optional[Decimal] = None,
        reject_reason: Optional[str] = None,
        exchange_status: Optional[str] = None,
        reconciliation_status: Optional[str] = None,
    ) -> None:
        """Update an existing OrderRecord after exchange response."""
        if record_id is None or self._session_factory is None:
            return

        from datetime import datetime as _dt, timezone as _tz
        from db.models import OrderRecord

        with self._session_factory() as session:
            record = session.get(OrderRecord, record_id)
            if record is None:
                return
            if status is not None:
                record.status = status
            if exchange_order_id is not None:
                record.exchange_order_id = exchange_order_id
            if filled_amount is not None:
                record.filled_amount = filled_amount
            if avg_fill_price is not None:
                record.avg_fill_price = avg_fill_price
            if fee_usdt is not None:
                record.fee_usdt = fee_usdt
            if slippage_percent is not None:
                record.slippage_percent = slippage_percent
            if reject_reason is not None:
                record.reject_reason = reject_reason
            if exchange_status is not None:
                record.exchange_status = exchange_status
            if reconciliation_status is not None:
                record.reconciliation_status = reconciliation_status
            if status == "filled":
                record.filled_at = _dt.now(_tz.utc)
                record.reconciliation_status = "synced"
            elif status == "rejected":
                record.reconciliation_status = "synced"

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
                params={"symbols": _json.dumps(symbols, separators=(",", ":"))},
                timeout=10,
            )
            resp.raise_for_status()
            for item in resp.json():
                prices[item["symbol"]] = Decimal(item["price"])
        except Exception as exc:
            logger.warning("get_ticker_prices: failed", error=str(exc))

        return prices

    # ── Public: exchange order queries (phase 6.1) ──────────────────────

    @staticmethod
    def _parse_order_response(raw: dict) -> dict:
        """Normalize a Binance order object — convert numeric strings to Decimal."""
        return {
            "orderId": raw["orderId"],
            "symbol": raw["symbol"],
            "side": raw["side"],
            "type": raw["type"],
            "status": raw["status"],
            "timeInForce": raw.get("timeInForce", ""),
            "origQty": Decimal(raw["origQty"]),
            "executedQty": Decimal(raw["executedQty"]),
            "cumulativeQuoteQty": Decimal(raw.get("cumulativeQuoteQty", "0")),
            "price": Decimal(raw["price"]),
            "stopPrice": Decimal(raw.get("stopPrice", "0")),
            "time": raw["time"],
            "updateTime": raw["updateTime"],
        }

    def get_open_orders(self, symbol: str | None = None) -> list[dict]:
        """
        Fetch all open orders on the spot account via ``GET /api/v3/openOrders``.

        Args:
            symbol: If provided, filter to a single trading pair (e.g. ``"BTCUSDT"``).
                    If *None*, returns open orders for all symbols.

        Returns a list of dicts, each containing::

            {
                "orderId": int,
                "symbol": str,
                "side": str,          # "BUY" | "SELL"
                "type": str,          # "LIMIT" | "STOP_LOSS_LIMIT" | ...
                "status": str,        # "NEW" | "PARTIALLY_FILLED"
                "timeInForce": str,
                "origQty": Decimal,
                "executedQty": Decimal,
                "cumulativeQuoteQty": Decimal,
                "price": Decimal,
                "stopPrice": Decimal,
                "time": int,          # ms timestamp
                "updateTime": int,
            }

        Raises :class:`BinanceAPIError` on API errors.
        """
        params: Dict[str, Any] = {}
        if symbol is not None:
            params["symbol"] = symbol

        data = self._signed_request("GET", "/api/v3/openOrders", params or None)
        return [self._parse_order_response(item) for item in data]

    def get_all_orders(
        self,
        symbol: str,
        start_time: int | None = None,
        limit: int = 500,
    ) -> list[dict]:
        """
        Fetch order history for a symbol via ``GET /api/v3/allOrders``.

        Used by fill recovery to find orders placed near a crash window.

        Args:
            symbol: Trading pair (required by Binance).
            start_time: Only return orders placed after this timestamp (ms).
            limit: Max number of results (capped at 1000 by Binance).

        Returns a list of dicts with the same shape as :meth:`get_open_orders`.

        Raises :class:`BinanceAPIError` on API errors.
        """
        params: Dict[str, Any] = {"symbol": symbol, "limit": min(limit, 1000)}
        if start_time is not None:
            params["startTime"] = start_time

        data = self._signed_request("GET", "/api/v3/allOrders", params)
        return [self._parse_order_response(item) for item in data]

    @staticmethod
    def _parse_trade_response(raw: dict) -> dict:
        """Normalize a Binance trade object — convert numeric strings to Decimal."""
        return {
            "id": raw["id"],
            "orderId": raw["orderId"],
            "symbol": raw["symbol"],
            "side": raw.get("side", ""),
            "price": Decimal(raw["price"]),
            "qty": Decimal(raw["qty"]),
            "commission": Decimal(raw["commission"]),
            "commissionAsset": raw["commissionAsset"],
            "time": raw["time"],
        }

    def get_my_trades(
        self,
        symbol: str,
        start_time: int | None = None,
        limit: int = 500,
    ) -> list[dict]:
        """
        Fetch executed trades for a symbol via ``GET /api/v3/myTrades``.

        Used by position reconciliation to reconstruct entry prices.

        Args:
            symbol: Trading pair (required by Binance).
            start_time: Only return trades after this timestamp (ms).
            limit: Max number of results (capped at 1000 by Binance).

        Returns a list of dicts, each containing::

            {
                "id": int,
                "orderId": int,
                "symbol": str,
                "side": str,
                "price": Decimal,
                "qty": Decimal,
                "commission": Decimal,
                "commissionAsset": str,
                "time": int,
            }

        Raises :class:`BinanceAPIError` on API errors.
        """
        params: Dict[str, Any] = {"symbol": symbol, "limit": min(limit, 1000)}
        if start_time is not None:
            params["startTime"] = start_time

        data = self._signed_request("GET", "/api/v3/myTrades", params)
        return [self._parse_trade_response(item) for item in data]

    def get_margin_open_orders(self, symbol: str | None = None) -> list[dict]:
        """
        Fetch all open orders on the margin account via ``GET /sapi/v1/margin/openOrders``.

        Same interface and return shape as :meth:`get_open_orders` but for
        the margin account.

        Raises :class:`BinanceAPIError` on API errors.
        """
        params: Dict[str, Any] = {}
        if symbol is not None:
            params["symbol"] = symbol

        data = self._signed_request("GET", "/sapi/v1/margin/openOrders", params or None)
        return [self._parse_order_response(item) for item in data]

    def get_margin_account(self) -> Dict[str, Dict[str, Decimal]]:
        """
        Fetch margin account details via ``GET /sapi/v1/margin/account``.

        Returns a dict keyed by asset with ``borrowed`` and ``free`` amounts::

            {"BTC": {"borrowed": Decimal("0.05"), "free": Decimal("0.01")}, ...}

        Only assets with non-zero borrowed or free balances are included.
        """
        data = self._signed_request("GET", "/sapi/v1/margin/account")

        result: Dict[str, Dict[str, Decimal]] = {}
        for item in data.get("userAssets", []):
            borrowed = Decimal(item.get("borrowed", "0"))
            free = Decimal(item.get("free", "0"))
            locked = Decimal(item.get("locked", "0"))
            if borrowed > 0 or free > 0 or locked > 0:
                result[item["asset"]] = {
                    "borrowed": borrowed,
                    "free": free,
                    "locked": locked,
                }

        return result

    def get_order_status(self, symbol: str, order_id: int) -> dict:
        """
        Query the status of a specific order via ``GET /api/v3/order``.

        Args:
            symbol: Trading pair.
            order_id: The exchange order ID to look up.

        Returns a single dict with the same shape as :meth:`get_open_orders`
        entries.

        Raises :class:`BinanceAPIError` on API errors.
        """
        params: Dict[str, Any] = {"symbol": symbol, "orderId": order_id}
        data = self._signed_request("GET", "/api/v3/order", params)
        return self._parse_order_response(data)

    def cancel_order(self, symbol: str, order_id: int) -> dict:
        """
        Cancel an open order via ``DELETE /api/v3/order``.

        Returns the final order status as a dict (same shape as
        :meth:`get_order_status`).

        If the order was already filled or cancelled, returns gracefully
        with the known terminal status instead of raising.

        Raises :class:`BinanceAPIError` only for unexpected errors.
        """
        params: Dict[str, Any] = {"symbol": symbol, "orderId": order_id}
        try:
            data = self._signed_request("DELETE", "/api/v3/order", params)
            return self._parse_order_response(data)
        except BinanceAPIError as exc:
            if exc.code in (-2011, -2013):
                # -2011: Unknown order (already cancelled/filled)
                # -2013: Order does not exist
                return {
                    "orderId": order_id,
                    "symbol": symbol,
                    "side": "",
                    "type": "",
                    "status": "CANCELED",
                    "timeInForce": "",
                    "origQty": Decimal("0"),
                    "executedQty": Decimal("0"),
                    "cumulativeQuoteQty": Decimal("0"),
                    "price": Decimal("0"),
                    "stopPrice": Decimal("0"),
                    "time": 0,
                    "updateTime": 0,
                }
            raise

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

        from db import get_session

        executor = cls(
            strategy_name=strategy_name,
            portfolio_manager=portfolio_manager,
            api_key=api_key,
            api_secret=api_secret,
            use_testnet=live.use_testnet,
            max_order_size_usdt=live.max_order_size_usdt,
            order_timeout=execution.order_timeout,
            retry_config=retry_cfg,
            exchange_stop_orders_enabled=execution.exchange_stop_orders_enabled,
            stop_limit_slippage_pct=execution.stop_limit_slippage_pct,
            session_factory=get_session,
        )

        return executor
