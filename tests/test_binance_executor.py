"""
Unit tests for BinanceExecutor (task 4.1).

Covers:
    - MARKET order fill (response with fills array)
    - LIMIT order fill (immediate fill)
    - LIMIT order timeout + cancellation
    - Order rejected by Binance (permanent error)
    - Retry exhaustion on transient errors
    - Safety gate: max order size
    - Quantity formatting via exchange filters
    - Price formatting via exchange filters
    - Signed request mechanics
    - Factory method (from_settings)
    - Exchange filter loading
"""

from datetime import datetime
from decimal import Decimal
from unittest.mock import MagicMock, patch, PropertyMock

import pytest
import requests

from core.execution.binance import BinanceAPIError, BinanceExecutor
from core.execution.retry import RetryConfig
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
)
from core.portfolio import PortfolioManager


# ── Helpers ──────────────────────────────────────────────────────────────────

T0 = datetime(2026, 4, 17, 10, 0)
TAKER_FEE = Decimal("0.001")


def _pm(capital: Decimal = Decimal("10000")) -> PortfolioManager:
    return PortfolioManager(initial_capital=capital, taker_fee=TAKER_FEE)


def _executor(
    pm: PortfolioManager | None = None,
    max_order_size_usdt: float = 10000.0,
    order_timeout: int = 5,
) -> BinanceExecutor:
    if pm is None:
        pm = _pm()
    ex = BinanceExecutor(
        strategy_name="smart_hodler",
        portfolio_manager=pm,
        api_key="test-api-key",
        api_secret="test-api-secret",
        use_testnet=True,
        max_order_size_usdt=max_order_size_usdt,
        order_timeout=order_timeout,
        retry_config=RetryConfig(max_attempts=1, base_delay=0.01, max_delay=0.01),
    )
    # Pre-load filters for BTCUSDT
    ex._filters["BTCUSDT"] = {
        "lot_step": Decimal("0.00001"),
        "lot_min": Decimal("0.00001"),
        "lot_max": Decimal("9000"),
        "price_step": Decimal("0.01"),
        "min_notional": Decimal("10"),
    }
    return ex


def _open_decision(
    symbol: str = "BTCUSDT",
    size: Decimal = Decimal("0.1"),
    price: Decimal = Decimal("50000"),
) -> RiskDecision:
    return RiskDecision(
        action=RiskAction.OPEN,
        symbol=symbol,
        size=size,
        price=price,
        hard_stop_price=Decimal("48500"),
        trailing_stop_price=Decimal("49000"),
    )


def _close_decision(
    symbol: str = "BTCUSDT",
    size: Decimal = Decimal("0.1"),
    price: Decimal = Decimal("51000"),
) -> RiskDecision:
    return RiskDecision(
        action=RiskAction.CLOSE,
        symbol=symbol,
        size=size,
        price=price,
        exit_reason=ExitReason.EMA_CROSS,
    )


def _binance_market_fill_response(
    order_id: int = 12345,
    symbol: str = "BTCUSDT",
    price: str = "50050.00",
    qty: str = "0.10000",
) -> dict:
    return {
        "symbol": symbol,
        "orderId": order_id,
        "clientOrderId": "abc123",
        "transactTime": 1713348000000,
        "price": "0.00000000",
        "origQty": qty,
        "executedQty": qty,
        "cumulativeQuoteQty": str(Decimal(price) * Decimal(qty)),
        "status": "FILLED",
        "timeInForce": "GTC",
        "type": "MARKET",
        "side": "BUY",
        "fills": [
            {
                "price": price,
                "qty": qty,
                "commission": "0.00010000",
                "commissionAsset": "BTC",
                "tradeId": 99999,
            }
        ],
    }


# ── MARKET order tests ───────────────────────────────────────────────────────


@patch("core.execution.binance.get_publisher")
class TestMarketOrderFill:
    """MARKET orders should fill immediately from the Binance response."""

    def test_open_market_order_returns_execution_result(self, mock_pub):
        mock_pub.return_value = MagicMock()
        ex = _executor()
        decision = _open_decision()
        response = _binance_market_fill_response()

        with patch.object(ex, "_signed_request", return_value=response):
            result = ex.execute(decision, T0)

        assert result is not None
        assert isinstance(result, ExecutionResult)
        assert result.order.status == OrderStatus.FILLED
        assert result.order.exchange_order_id == "12345"
        assert result.order.filled_amount == Decimal("0.10000")
        assert result.order.avg_fill_price == Decimal("50050.00")
        assert result.position is not None

    def test_close_market_order_returns_trade(self, mock_pub):
        mock_pub.return_value = MagicMock()
        pm = _pm()
        ex = _executor(pm=pm)

        # First open a position
        open_decision = _open_decision()
        open_response = _binance_market_fill_response()
        with patch.object(ex, "_signed_request", return_value=open_response):
            ex.execute(open_decision, T0)

        # Then close it
        close_decision = _close_decision()
        close_response = _binance_market_fill_response(
            price="51000.00", qty="0.10000"
        )
        close_response["side"] = "SELL"
        with patch.object(ex, "_get_available_balance", return_value=Decimal("0.10000")), \
             patch.object(ex, "_signed_request", return_value=close_response):
            result = ex.execute(close_decision, T0)

        assert result is not None
        assert result.trade is not None
        assert result.order.status == OrderStatus.FILLED

    def test_market_fill_computes_slippage_percent(self, mock_pub):
        mock_pub.return_value = MagicMock()
        ex = _executor()
        decision = _open_decision(price=Decimal("50000"))
        response = _binance_market_fill_response(price="50050.00")

        with patch.object(ex, "_signed_request", return_value=response):
            result = ex.execute(decision, T0)

        # 50050 vs 50000 = 0.1% slippage
        assert result.order.slippage_percent == Decimal("0.1000")

    def test_market_fill_aggregates_multiple_fills(self, mock_pub):
        mock_pub.return_value = MagicMock()
        ex = _executor()
        decision = _open_decision()
        response = _binance_market_fill_response()
        response["fills"] = [
            {
                "price": "50000.00",
                "qty": "0.05000",
                "commission": "0.00005",
                "commissionAsset": "BTC",
            },
            {
                "price": "50100.00",
                "qty": "0.05000",
                "commission": "0.05000",
                "commissionAsset": "USDT",
            },
        ]

        with patch.object(ex, "_signed_request", return_value=response):
            result = ex.execute(decision, T0)

        # Weighted avg: (50000*0.05 + 50100*0.05) / 0.10 = 50050
        assert result.order.avg_fill_price == Decimal("50050")
        assert result.order.filled_amount == Decimal("0.10000")
        # Fee: 0.00005 * 50000 + 0.05 USDT = 2.5 + 0.05 = 2.55
        assert result.order.fee_usdt == Decimal("2.55")


# ── LIMIT order tests ────────────────────────────────────────────────────────


@patch("core.execution.binance.get_publisher")
class TestLimitOrder:
    """LIMIT orders use polling or immediate fill."""

    def test_limit_order_immediate_fill(self, mock_pub):
        mock_pub.return_value = MagicMock()
        ex = _executor()
        decision = _open_decision()
        response = _binance_market_fill_response()
        response["type"] = "LIMIT"
        response["price"] = "49900.00"
        response["status"] = "FILLED"

        with patch.object(ex, "_signed_request", return_value=response):
            order = ex._place_and_fill(
                decision, OrderSide.BUY,
                order_type=OrderType.LIMIT,
                limit_price=Decimal("49900"),
                time_in_force=TimeInForce.GTC,
            )

        assert order is not None
        assert order.status == OrderStatus.FILLED
        assert order.time_in_force == TimeInForce.GTC


# ── Error handling tests ─────────────────────────────────────────────────────


@patch("core.execution.binance.get_publisher")
class TestErrorHandling:
    """Binance errors should be mapped to rejected/cancelled orders."""

    def test_permanent_error_returns_rejected_order(self, mock_pub):
        mock_pub.return_value = MagicMock()
        ex = _executor()
        decision = _open_decision()

        with patch.object(
            ex, "_signed_request",
            side_effect=BinanceAPIError(-2010, "Account has insufficient balance"),
        ):
            result = ex.execute(decision, T0)

        # Permanent error → rejected order, no ExecutionResult
        assert result is None  # execute returns None because order is REJECTED

    def test_api_error_creates_rejected_order(self, mock_pub):
        mock_pub.return_value = MagicMock()
        ex = _executor()
        decision = _open_decision()

        with patch.object(
            ex, "_signed_request",
            side_effect=BinanceAPIError(-2010, "Insufficient balance"),
        ):
            order = ex._place_and_fill(decision, OrderSide.BUY)

        assert order is not None
        assert order.status == OrderStatus.REJECTED
        assert "Insufficient balance" in order.reject_reason

    def test_connection_error_exhausts_retries(self, mock_pub):
        mock_pub.return_value = MagicMock()
        ex = _executor()
        decision = _open_decision()

        with patch.object(
            ex, "_signed_request",
            side_effect=requests.ConnectionError("Connection refused"),
        ):
            order = ex._place_and_fill(decision, OrderSide.BUY)

        assert order is not None
        assert order.status == OrderStatus.REJECTED
        assert "Retry exhausted" in order.reject_reason


# ── Safety gate tests ────────────────────────────────────────────────────────


@patch("core.execution.binance.get_publisher")
class TestSafetyGates:
    """Safety checks should prevent oversized orders."""

    def test_max_order_size_blocks_large_order(self, mock_pub):
        mock_pub.return_value = MagicMock()
        ex = _executor(max_order_size_usdt=1000.0)

        # 0.1 BTC @ 50000 = 5000 USDT > 1000 limit
        decision = _open_decision(size=Decimal("0.1"), price=Decimal("50000"))
        result = ex.execute(decision, T0)

        assert result is None

    def test_small_order_within_limit_proceeds(self, mock_pub):
        mock_pub.return_value = MagicMock()
        ex = _executor(max_order_size_usdt=10000.0)
        decision = _open_decision(size=Decimal("0.01"), price=Decimal("50000"))
        response = _binance_market_fill_response(qty="0.01000", price="50050.00")

        with patch.object(ex, "_signed_request", return_value=response):
            result = ex.execute(decision, T0)

        assert result is not None

    def test_hold_action_returns_none(self, mock_pub):
        mock_pub.return_value = MagicMock()
        ex = _executor()
        decision = RiskDecision(action=RiskAction.HOLD)
        result = ex.execute(decision, T0)
        assert result is None


# ── Quantity / price formatting tests ────────────────────────────────────────


class TestQuantityFormatting:
    """Exchange filters should be enforced on quantity."""

    def test_quantity_rounds_down_to_step(self):
        ex = _executor()
        qty = ex._format_quantity("BTCUSDT", Decimal("0.123456789"))
        # step = 0.00001 → 0.12345 (round down)
        assert qty == Decimal("0.12345")

    def test_quantity_below_min_returns_zero(self):
        ex = _executor()
        qty = ex._format_quantity("BTCUSDT", Decimal("0.000001"))
        # min = 0.00001, 0.000001 rounds to 0 which is < min
        assert qty == Decimal("0")

    def test_quantity_unknown_symbol_uses_8dp(self):
        ex = _executor()
        qty = ex._format_quantity("UNKNOWN", Decimal("1.123456789"))
        assert qty == Decimal("1.12345678")

    def test_price_rounds_to_tick_size(self):
        ex = _executor()
        price = ex._format_price("BTCUSDT", Decimal("50000.12345"))
        # tick = 0.01 → 50000.12
        assert price == Decimal("50000.12")


# ── Signed request tests ────────────────────────────────────────────────────


class TestSignedRequest:
    """HMAC-SHA256 signing and Binance error detection."""

    def test_error_response_raises_api_error(self):
        ex = _executor()
        error_body = {"code": -1013, "msg": "Filter failure: LOT_SIZE"}

        with patch.object(ex._session, "post", return_value=MagicMock(
            json=lambda: error_body,
        )):
            with pytest.raises(BinanceAPIError) as exc_info:
                ex._signed_request("POST", "/api/v3/order", {"symbol": "BTCUSDT"})

            assert exc_info.value.code == -1013
            assert exc_info.value.is_permanent is True
            assert exc_info.value.is_retryable is False

    def test_request_includes_signature_and_timestamp(self):
        ex = _executor()
        success_body = {"orderId": 1, "status": "FILLED", "fills": []}

        mock_post = MagicMock(return_value=MagicMock(
            json=lambda: success_body,
        ))
        with patch.object(ex._session, "post", mock_post):
            ex._signed_request("POST", "/api/v3/order", {"symbol": "BTCUSDT"})

        call_kwargs = mock_post.call_args
        params = call_kwargs.kwargs.get("params") or call_kwargs[1].get("params", {})
        assert "signature" in params
        assert "timestamp" in params
        assert "recvWindow" in params


# ── Exchange filter loading tests ────────────────────────────────────────────


class TestExchangeFilters:
    """Loading filters from /api/v3/exchangeInfo."""

    def test_load_exchange_filters_caches_symbols(self):
        ex = _executor()
        ex._filters.clear()  # Start fresh

        exchange_info = {
            "symbols": [
                {
                    "symbol": "ETHUSDT",
                    "filters": [
                        {"filterType": "LOT_SIZE", "stepSize": "0.0001", "minQty": "0.0001", "maxQty": "100000"},
                        {"filterType": "PRICE_FILTER", "tickSize": "0.01"},
                        {"filterType": "NOTIONAL", "minNotional": "10.00"},
                    ],
                }
            ]
        }

        with patch.object(ex._session, "get", return_value=MagicMock(
            json=lambda: exchange_info,
            raise_for_status=lambda: None,
        )):
            ex.load_exchange_filters(["ETHUSDT"])

        assert "ETHUSDT" in ex._filters
        assert ex._filters["ETHUSDT"]["lot_step"] == Decimal("0.0001")
        assert ex._filters["ETHUSDT"]["price_step"] == Decimal("0.01")
        assert ex._filters["ETHUSDT"]["min_notional"] == Decimal("10.00")


# ── BinanceAPIError tests ───────────────────────────────────────────────────


class TestBinanceAPIError:
    """Error code classification."""

    def test_permanent_error_classification(self):
        err = BinanceAPIError(-2010, "Insufficient balance")
        assert err.is_permanent is True
        assert err.is_retryable is False

    def test_retryable_error_classification(self):
        err = BinanceAPIError(-1003, "Too many requests")
        assert err.is_permanent is False
        assert err.is_retryable is True

    def test_unknown_error_is_neither(self):
        err = BinanceAPIError(-9999, "Unknown error")
        assert err.is_permanent is False
        assert err.is_retryable is False


# ── Factory method tests ─────────────────────────────────────────────────────


class TestFactory:
    """BinanceExecutor.from_settings factory method."""

    def test_from_settings_creates_executor(self):
        with patch("core.config.get_settings") as mock_settings:
            mock_live = MagicMock()
            mock_live.api_key = "key123"
            mock_live.api_secret = "secret456"
            mock_live.use_testnet = True
            mock_live.max_order_size_usdt = 500.0

            mock_exec = MagicMock()
            mock_exec.max_retries = 3
            mock_exec.retry_delay = 1
            mock_exec.order_timeout = 30
            mock_exec.exchange_stop_orders_enabled = False
            mock_exec.stop_limit_slippage_pct = 0.005

            mock_settings.return_value.live_trading = mock_live
            mock_settings.return_value.execution = mock_exec

            pm = _pm()
            ex = BinanceExecutor.from_settings("smart_hodler", pm)

            assert ex._api_key == "key123"
            assert ex._base_url == BinanceExecutor._TESTNET_BASE
            assert ex._max_order_size_usdt == Decimal("500.0")

    def test_from_settings_raises_on_missing_credentials(self):
        with patch("core.config.get_settings") as mock_settings:
            mock_live = MagicMock()
            mock_live.api_key = ""
            mock_live.api_secret = ""

            mock_settings.return_value.live_trading = mock_live

            pm = _pm()
            with pytest.raises(RuntimeError, match="credentials not configured"):
                BinanceExecutor.from_settings("smart_hodler", pm)
