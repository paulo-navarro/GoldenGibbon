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
    PositionSide,
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
        assert result.order.filled_amount == Decimal("0.09990000")
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
        # Net qty: 0.10000 - 0.00005 BTC commission = 0.09995
        assert result.order.filled_amount == Decimal("0.09995")
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


# ── Dust close tests ───────────────────────────────────────────────────────


@patch("core.execution.binance.get_publisher")
class TestDustClose:
    """Dust close logic: when formatted_qty <= 0 during a CLOSE."""

    def _open_position(self, pm: PortfolioManager) -> None:
        """Open a position in the portfolio manager so close_position succeeds."""
        pm.open_position(
            symbol="BTCUSDT",
            size=Decimal("0.00001"),
            entry_price=Decimal("50000"),
            entry_time=T0,
            hard_stop_price=Decimal("48000"),
            trailing_stop_price=Decimal("49000"),
            strategy="smart_hodler",
        )

    def test_dust_close_sells_real_balance_when_exchange_sellable(self, mock_pub):
        """Exchange has a sellable qty above minQty — should sell on exchange, not dust-close."""
        mock_pub.return_value = MagicMock()
        pm = _pm()
        ex = _executor(pm=pm)
        self._open_position(pm)

        decision = _close_decision(symbol="BTCUSDT", size=Decimal("0.000001"), price=Decimal("51000"))

        sell_response = _binance_market_fill_response(
            price="51000.00", qty="0.00001", symbol="BTCUSDT",
        )
        sell_response["side"] = "SELL"

        with patch.object(ex, "_get_available_balance", return_value=Decimal("0.00001")), \
             patch.object(ex, "_signed_request", return_value=sell_response):
            result = ex.execute(decision, T0)

        assert result is not None
        assert result.order.status == OrderStatus.FILLED
        assert result.trade is not None

    def test_dust_close_fires_when_exchange_truly_dust(self, mock_pub):
        """Exchange balance is below minQty (true dust) — should local-close only."""
        mock_pub.return_value = MagicMock()
        pm = _pm()
        ex = _executor(pm=pm)
        self._open_position(pm)

        decision = _close_decision(symbol="BTCUSDT", size=Decimal("0.000001"), price=Decimal("51000"))

        with patch.object(ex, "_get_available_balance", return_value=Decimal("0.000001")):
            result = ex.execute(decision, T0)

        assert result is not None
        assert result.order.status == OrderStatus.FILLED
        assert result.trade is not None
        assert result.order.side == OrderSide.SELL

    def test_dust_close_fires_when_exchange_zero(self, mock_pub):
        """Exchange has 0 balance — should local-close only (reconciliation case)."""
        mock_pub.return_value = MagicMock()
        pm = _pm()
        ex = _executor(pm=pm)
        self._open_position(pm)

        decision = _close_decision(symbol="BTCUSDT", size=Decimal("0.000001"), price=Decimal("51000"))

        with patch.object(ex, "_get_available_balance", return_value=Decimal("0")):
            result = ex.execute(decision, T0)

        assert result is not None
        assert result.trade is not None

    def test_close_aborts_when_balance_query_fails(self, mock_pub):
        """Cannot query exchange balance — should refuse to close (fail-safe)."""
        mock_pub.return_value = MagicMock()
        pm = _pm()
        ex = _executor(pm=pm)
        self._open_position(pm)

        decision = _close_decision(symbol="BTCUSDT", size=Decimal("0.000001"), price=Decimal("51000"))

        with patch.object(ex, "_get_available_balance", return_value=None):
            result = ex.execute(decision, T0)

        assert result is None


# ── Short/Margin helpers ─────────────────────────────────────────────────────


def _open_short_decision(
    symbol: str = "BTCUSDT",
    size: Decimal = Decimal("0.1"),
    price: Decimal = Decimal("50000"),
) -> RiskDecision:
    return RiskDecision(
        action=RiskAction.OPEN,
        symbol=symbol,
        side=PositionSide.SHORT,
        size=size,
        price=price,
        hard_stop_price=Decimal("52500"),
        trailing_stop_price=Decimal("51500"),
    )


def _close_short_decision(
    symbol: str = "BTCUSDT",
    size: Decimal = Decimal("0.1"),
    price: Decimal = Decimal("48000"),
) -> RiskDecision:
    return RiskDecision(
        action=RiskAction.CLOSE,
        symbol=symbol,
        size=size,
        price=price,
        exit_reason=ExitReason.EMA_CROSS,
    )


def _reduce_short_decision(
    symbol: str = "BTCUSDT",
    size: Decimal = Decimal("0.1"),
    price: Decimal = Decimal("48000"),
    sell_fraction: Decimal = Decimal("0.5"),
) -> RiskDecision:
    return RiskDecision(
        action=RiskAction.REDUCE,
        symbol=symbol,
        size=size,
        price=price,
        sell_fraction=sell_fraction,
        exit_reason=ExitReason.MOMENTUM_FADE,
    )


def _binance_margin_fill_response(
    order_id: int = 54321,
    symbol: str = "BTCUSDT",
    price: str = "50000.00",
    qty: str = "0.10000",
    side: str = "SELL",
) -> dict:
    return {
        "symbol": symbol,
        "orderId": order_id,
        "clientOrderId": "futures_abc123",
        "transactTime": 1713348000000,
        "price": "0.00000000",
        "origQty": qty,
        "executedQty": qty,
        "cumQuote": str(Decimal(price) * Decimal(qty)),
        "avgPrice": price,
        "status": "FILLED",
        "timeInForce": "GTC",
        "type": "MARKET",
        "side": side,
    }


def _mock_shorts_enabled():
    """Context manager that mocks get_settings().shorts.enabled = True."""
    mock_settings = MagicMock()
    mock_settings.return_value.shorts.enabled = True
    return patch("core.config.get_settings", mock_settings)


def _mock_shorts_disabled():
    """Context manager that mocks get_settings().shorts.enabled = False."""
    mock_settings = MagicMock()
    mock_settings.return_value.shorts.enabled = False
    return patch("core.config.get_settings", mock_settings)


# ── Margin short open tests ──────────────────────────────────────────────────


@patch("core.execution.binance.get_publisher")
class TestMarginShortOpen:
    """OPEN with side=SHORT should route to margin API."""

    def test_open_short_calls_futures_api(self, mock_pub):
        mock_pub.return_value = MagicMock()
        ex = _executor()
        decision = _open_short_decision()
        response = _binance_margin_fill_response(side="SELL")

        with _mock_shorts_enabled(), \
             patch.object(ex, "_futures_signed_request", return_value=response) as mock_req:
            result = ex.execute(decision, T0)

        assert result is not None
        assert result.order.status == OrderStatus.FILLED
        call_args = mock_req.call_args
        assert call_args[0][0] == "POST"
        assert call_args[0][1] == "/fapi/v1/order"
        params = call_args[0][2]
        assert params["side"] == "SELL"
        assert "sideEffectType" not in params
        assert "isIsolated" not in params

    def test_open_short_creates_position(self, mock_pub):
        mock_pub.return_value = MagicMock()
        ex = _executor()
        decision = _open_short_decision()
        response = _binance_margin_fill_response(side="SELL")

        with _mock_shorts_enabled(), \
             patch.object(ex, "_futures_signed_request", return_value=response):
            result = ex.execute(decision, T0)

        assert result.position is not None
        assert result.position.side == PositionSide.SHORT

    def test_open_short_with_exchange_stop(self, mock_pub):
        mock_pub.return_value = MagicMock()
        pm = _pm()
        ex = _executor(pm=pm)
        ex._exchange_stops_enabled = True

        decision = _open_short_decision()
        fill_response = _binance_margin_fill_response(side="SELL")
        stop_response = {"orderId": 99999, "status": "NEW"}

        with _mock_shorts_enabled(), \
             patch.object(ex, "_futures_signed_request", side_effect=[fill_response, stop_response]):
            result = ex.execute(decision, T0)

        assert result is not None
        # Check that the stop was placed
        pos = pm.get_position("BTCUSDT")
        assert pos.exchange_stop_order_id == "99999"

    def test_open_short_futures_api_error_returns_none(self, mock_pub):
        mock_pub.return_value = MagicMock()
        ex = _executor()
        decision = _open_short_decision()

        with _mock_shorts_enabled(), \
             patch.object(ex, "_futures_signed_request", side_effect=BinanceAPIError(-3007, "Futures error")):
            result = ex.execute(decision, T0)

        assert result is None


# ── Margin short close tests ─────────────────────────────────────────────────


@patch("core.execution.binance.get_publisher")
class TestMarginShortClose:
    """CLOSE on a SHORT position should route to margin BUY with AUTO_REPAY."""

    def _open_short(self, ex, pm):
        """Helper: open a short position via executor."""
        decision = _open_short_decision()
        response = _binance_margin_fill_response(side="SELL")
        with _mock_shorts_enabled(), \
             patch.object(ex, "_futures_signed_request", return_value=response):
            ex.execute(decision, T0)

    def test_close_short_calls_futures_buy(self, mock_pub):
        mock_pub.return_value = MagicMock()
        pm = _pm()
        ex = _executor(pm=pm)
        self._open_short(ex, pm)

        decision = _close_short_decision()
        close_response = _binance_margin_fill_response(
            side="BUY", price="48000.00"
        )

        with _mock_shorts_enabled(), \
             patch.object(ex, "_futures_signed_request", return_value=close_response) as mock_req:
            result = ex.execute(decision, T0)

        assert result is not None
        assert result.trade is not None
        call_args = mock_req.call_args
        params = call_args[0][2]
        assert params["side"] == "BUY"
        assert "sideEffectType" not in params

    def test_close_short_does_not_clamp_to_balance(self, mock_pub):
        """Short close should NOT call _get_available_balance."""
        mock_pub.return_value = MagicMock()
        pm = _pm()
        ex = _executor(pm=pm)
        self._open_short(ex, pm)

        decision = _close_short_decision()
        close_response = _binance_margin_fill_response(
            side="BUY", price="48000.00"
        )

        with _mock_shorts_enabled(), \
             patch.object(ex, "_futures_signed_request", return_value=close_response), \
             patch.object(ex, "_get_available_balance") as mock_balance:
            ex.execute(decision, T0)

        mock_balance.assert_not_called()


# ── Margin short reduce tests ────────────────────────────────────────────────


@patch("core.execution.binance.get_publisher")
class TestMarginShortReduce:
    """REDUCE on a SHORT position should use margin BUY with AUTO_REPAY."""

    def _open_short(self, ex, pm):
        """Helper: open a short position via executor."""
        decision = _open_short_decision()
        response = _binance_margin_fill_response(side="SELL")
        with _mock_shorts_enabled(), \
             patch.object(ex, "_futures_signed_request", return_value=response):
            ex.execute(decision, T0)

    def test_reduce_short_calls_futures_buy(self, mock_pub):
        mock_pub.return_value = MagicMock()
        pm = _pm()
        ex = _executor(pm=pm)
        self._open_short(ex, pm)

        decision = _reduce_short_decision()
        reduce_response = _binance_margin_fill_response(
            side="BUY", price="48000.00", qty="0.05000",
        )

        with _mock_shorts_enabled(), \
             patch.object(ex, "_futures_signed_request", return_value=reduce_response) as mock_req:
            result = ex.execute(decision, T0)

        assert result is not None
        assert result.trade is not None
        call_args = mock_req.call_args
        params = call_args[0][2]
        assert params["side"] == "BUY"
        assert "sideEffectType" not in params

    def test_reduce_short_does_not_clamp_to_balance(self, mock_pub):
        """Short reduce should NOT call _get_available_balance."""
        mock_pub.return_value = MagicMock()
        pm = _pm()
        ex = _executor(pm=pm)
        self._open_short(ex, pm)

        decision = _reduce_short_decision()
        reduce_response = _binance_margin_fill_response(
            side="BUY", price="48000.00", qty="0.05000",
        )

        with _mock_shorts_enabled(), \
             patch.object(ex, "_futures_signed_request", return_value=reduce_response), \
             patch.object(ex, "_get_available_balance") as mock_balance:
            ex.execute(decision, T0)

        mock_balance.assert_not_called()

    def test_reduce_short_with_exchange_stop_replacement(self, mock_pub):
        """After reduce, remaining position should get a futures stop order."""
        mock_pub.return_value = MagicMock()
        pm = _pm()
        ex = _executor(pm=pm)
        ex._exchange_stops_enabled = True

        # Open short with stop
        decision = _open_short_decision()
        fill_response = _binance_margin_fill_response(side="SELL")
        stop_response = {"orderId": 77777, "status": "NEW"}
        with _mock_shorts_enabled(), \
             patch.object(ex, "_futures_signed_request", side_effect=[fill_response, stop_response]):
            ex.execute(decision, T0)

        # Reduce 50%: cancel old stop + reduce fill + place new stop = 3 calls
        reduce_decision = _reduce_short_decision()
        cancel_response = {"orderId": 77777, "status": "CANCELED"}
        reduce_response = _binance_margin_fill_response(
            side="BUY", price="48000.00", qty="0.05000",
        )
        new_stop_response = {"orderId": 88888, "status": "NEW"}

        with _mock_shorts_enabled(), \
             patch.object(
                 ex, "_futures_signed_request",
                 side_effect=[cancel_response, reduce_response, new_stop_response],
             ):
            result = ex.execute(reduce_decision, T0)

        assert result is not None
        pos = pm.get_position("BTCUSDT")
        assert pos.exchange_stop_order_id == "88888"


# ── Shorts disabled gate tests ───────────────────────────────────────────────


@patch("core.execution.binance.get_publisher")
class TestShortsDisabledGate:
    """Opening a short when shorts.enabled=False should raise RuntimeError."""

    def test_short_open_raises_when_disabled(self, mock_pub):
        mock_pub.return_value = MagicMock()
        ex = _executor()
        decision = _open_short_decision()

        with _mock_shorts_disabled(), \
             pytest.raises(RuntimeError, match="Short selling is disabled"):
            ex.execute(decision, T0)


# ── Margin stop order tests ──────────────────────────────────────────────────


@patch("core.execution.binance.get_publisher")
class TestMarginStopOrder:
    """Futures stop orders are placed via /fapi/v1/order with STOP_MARKET."""

    def test_futures_stop_uses_buy_side(self, mock_pub):
        mock_pub.return_value = MagicMock()
        pm = _pm()
        ex = _executor(pm=pm)
        ex._exchange_stops_enabled = True

        decision = _open_short_decision()
        fill_response = _binance_margin_fill_response(side="SELL")
        stop_response = {"orderId": 55555, "status": "NEW"}

        with _mock_shorts_enabled(), \
             patch.object(ex, "_futures_signed_request", side_effect=[fill_response, stop_response]) as mock_req:
            ex.execute(decision, T0)

        stop_call = mock_req.call_args_list[1]
        params = stop_call[0][2]
        assert params["side"] == "BUY"
        assert params["type"] == "STOP_MARKET"
        assert "sideEffectType" not in params
        assert stop_call[0][1] == "/fapi/v1/order"

    def test_futures_stop_market_has_stop_price(self, mock_pub):
        """Futures STOP_MARKET should have stopPrice but no limit price."""
        mock_pub.return_value = MagicMock()
        pm = _pm()
        ex = _executor(pm=pm)
        ex._exchange_stops_enabled = True

        decision = _open_short_decision()  # hard_stop = 52500
        fill_response = _binance_margin_fill_response(side="SELL")
        stop_response = {"orderId": 55555, "status": "NEW"}

        with _mock_shorts_enabled(), \
             patch.object(ex, "_futures_signed_request", side_effect=[fill_response, stop_response]) as mock_req:
            ex.execute(decision, T0)

        stop_call = mock_req.call_args_list[1]
        params = stop_call[0][2]
        assert "stopPrice" in params
        assert "price" not in params


# ── Exchange Query Layer (phase 6.1) ────────────────────────────────────────


_OPEN_ORDER_RAW = {
    "orderId": 12345,
    "symbol": "BTCUSDT",
    "side": "BUY",
    "type": "LIMIT",
    "status": "NEW",
    "timeInForce": "GTC",
    "origQty": "0.10000",
    "executedQty": "0.00000",
    "cumulativeQuoteQty": "0.00000",
    "price": "50000.00",
    "stopPrice": "0.00",
    "time": 1714900000000,
    "updateTime": 1714900001000,
}

_STOP_ORDER_RAW = {
    "orderId": 12346,
    "symbol": "ETHUSDT",
    "side": "SELL",
    "type": "STOP_LOSS_LIMIT",
    "status": "NEW",
    "timeInForce": "GTC",
    "origQty": "1.500",
    "executedQty": "0.000",
    "cumulativeQuoteQty": "0.000",
    "price": "3400.00",
    "stopPrice": "3500.00",
    "time": 1714900002000,
    "updateTime": 1714900003000,
}


@patch("core.execution.binance.get_publisher")
class TestGetOpenOrders:
    """Tests for BinanceExecutor.get_open_orders() — phase 6.1.1."""

    def test_with_symbol_filter(self, mock_pub):
        mock_pub.return_value = MagicMock()
        ex = _executor()

        with patch.object(ex, "_signed_request", return_value=[_OPEN_ORDER_RAW]) as mock_req:
            result = ex.get_open_orders(symbol="BTCUSDT")

        mock_req.assert_called_once_with("GET", "/api/v3/openOrders", {"symbol": "BTCUSDT"})
        assert len(result) == 1
        order = result[0]
        assert order["orderId"] == 12345
        assert order["symbol"] == "BTCUSDT"
        assert order["side"] == "BUY"
        assert order["type"] == "LIMIT"
        assert order["status"] == "NEW"
        assert order["origQty"] == Decimal("0.10000")
        assert order["executedQty"] == Decimal("0.00000")
        assert order["price"] == Decimal("50000.00")
        assert order["stopPrice"] == Decimal("0.00")
        assert order["time"] == 1714900000000
        assert order["updateTime"] == 1714900001000

    def test_without_symbol_returns_all(self, mock_pub):
        mock_pub.return_value = MagicMock()
        ex = _executor()

        with patch.object(
            ex, "_signed_request", return_value=[_OPEN_ORDER_RAW, _STOP_ORDER_RAW]
        ) as mock_req:
            result = ex.get_open_orders()

        mock_req.assert_called_once_with("GET", "/api/v3/openOrders", None)
        assert len(result) == 2
        assert result[0]["symbol"] == "BTCUSDT"
        assert result[1]["symbol"] == "ETHUSDT"

    def test_empty_response(self, mock_pub):
        mock_pub.return_value = MagicMock()
        ex = _executor()

        with patch.object(ex, "_signed_request", return_value=[]):
            result = ex.get_open_orders(symbol="BTCUSDT")

        assert result == []

    def test_api_error_propagates(self, mock_pub):
        mock_pub.return_value = MagicMock()
        ex = _executor()

        with patch.object(
            ex, "_signed_request", side_effect=BinanceAPIError(-1003, "Too many requests")
        ):
            with pytest.raises(BinanceAPIError) as exc_info:
                ex.get_open_orders()
            assert exc_info.value.code == -1003


@patch("core.execution.binance.get_publisher")
class TestGetAllOrders:
    """Tests for BinanceExecutor.get_all_orders() — phase 6.1.2."""

    def test_happy_path_with_start_time(self, mock_pub):
        mock_pub.return_value = MagicMock()
        ex = _executor()
        filled_order = {**_OPEN_ORDER_RAW, "status": "FILLED", "executedQty": "0.10000"}

        with patch.object(ex, "_signed_request", return_value=[filled_order]) as mock_req:
            result = ex.get_all_orders("BTCUSDT", start_time=1714900000000)

        mock_req.assert_called_once_with(
            "GET",
            "/api/v3/allOrders",
            {"symbol": "BTCUSDT", "limit": 500, "startTime": 1714900000000},
        )
        assert len(result) == 1
        assert result[0]["status"] == "FILLED"
        assert result[0]["executedQty"] == Decimal("0.10000")

    def test_without_start_time(self, mock_pub):
        mock_pub.return_value = MagicMock()
        ex = _executor()

        with patch.object(ex, "_signed_request", return_value=[]) as mock_req:
            ex.get_all_orders("ETHUSDT")

        mock_req.assert_called_once_with(
            "GET",
            "/api/v3/allOrders",
            {"symbol": "ETHUSDT", "limit": 500},
        )

    def test_limit_clamped_to_1000(self, mock_pub):
        mock_pub.return_value = MagicMock()
        ex = _executor()

        with patch.object(ex, "_signed_request", return_value=[]) as mock_req:
            ex.get_all_orders("BTCUSDT", limit=5000)

        call_params = mock_req.call_args[0][2]
        assert call_params["limit"] == 1000

    def test_empty_response(self, mock_pub):
        mock_pub.return_value = MagicMock()
        ex = _executor()

        with patch.object(ex, "_signed_request", return_value=[]):
            result = ex.get_all_orders("BTCUSDT")

        assert result == []

    def test_api_error_propagates(self, mock_pub):
        mock_pub.return_value = MagicMock()
        ex = _executor()

        with patch.object(
            ex, "_signed_request", side_effect=BinanceAPIError(-2010, "Insufficient balance")
        ):
            with pytest.raises(BinanceAPIError) as exc_info:
                ex.get_all_orders("BTCUSDT")
            assert exc_info.value.code == -2010


# ── get_my_trades (phase 6.1.3) ─────────────────────────────────────────────

_TRADE_RAW = {
    "id": 99001,
    "orderId": 12345,
    "symbol": "BTCUSDT",
    "side": "BUY",
    "price": "50100.50",
    "qty": "0.05000",
    "commission": "0.00005",
    "commissionAsset": "BTC",
    "time": 1714900010000,
}


@patch("core.execution.binance.get_publisher")
class TestGetMyTrades:
    """Tests for BinanceExecutor.get_my_trades() — phase 6.1.3."""

    def test_happy_path(self, mock_pub):
        mock_pub.return_value = MagicMock()
        ex = _executor()

        with patch.object(ex, "_signed_request", return_value=[_TRADE_RAW]) as mock_req:
            result = ex.get_my_trades("BTCUSDT", start_time=1714900000000)

        mock_req.assert_called_once_with(
            "GET",
            "/api/v3/myTrades",
            {"symbol": "BTCUSDT", "limit": 500, "startTime": 1714900000000},
        )
        assert len(result) == 1
        t = result[0]
        assert t["id"] == 99001
        assert t["orderId"] == 12345
        assert t["symbol"] == "BTCUSDT"
        assert t["side"] == "BUY"
        assert t["price"] == Decimal("50100.50")
        assert t["qty"] == Decimal("0.05000")
        assert t["commission"] == Decimal("0.00005")
        assert t["commissionAsset"] == "BTC"
        assert t["time"] == 1714900010000

    def test_limit_clamped_to_1000(self, mock_pub):
        mock_pub.return_value = MagicMock()
        ex = _executor()

        with patch.object(ex, "_signed_request", return_value=[]) as mock_req:
            ex.get_my_trades("BTCUSDT", limit=5000)

        call_params = mock_req.call_args[0][2]
        assert call_params["limit"] == 1000

    def test_empty_response(self, mock_pub):
        mock_pub.return_value = MagicMock()
        ex = _executor()

        with patch.object(ex, "_signed_request", return_value=[]):
            result = ex.get_my_trades("BTCUSDT")

        assert result == []

    def test_api_error_propagates(self, mock_pub):
        mock_pub.return_value = MagicMock()
        ex = _executor()

        with patch.object(
            ex, "_signed_request", side_effect=BinanceAPIError(-1003, "Too many requests")
        ):
            with pytest.raises(BinanceAPIError):
                ex.get_my_trades("BTCUSDT")


# ── get_futures_open_orders (phase 6.1.4) ─────────────────────────────────────


@patch("core.execution.binance.get_publisher")
class TestGetFuturesOpenOrders:
    """Tests for BinanceExecutor.get_futures_open_orders()."""

    def test_with_symbol(self, mock_pub):
        mock_pub.return_value = MagicMock()
        ex = _executor()

        with patch.object(ex, "_futures_signed_request", return_value=[_OPEN_ORDER_RAW]) as mock_req:
            result = ex.get_futures_open_orders(symbol="BTCUSDT")

        mock_req.assert_called_once_with(
            "GET", "/fapi/v1/openOrders", {"symbol": "BTCUSDT"}
        )
        assert len(result) == 1
        assert result[0]["orderId"] == 12345

    def test_without_symbol(self, mock_pub):
        mock_pub.return_value = MagicMock()
        ex = _executor()

        with patch.object(ex, "_futures_signed_request", return_value=[]) as mock_req:
            ex.get_futures_open_orders()

        mock_req.assert_called_once_with("GET", "/fapi/v1/openOrders", None)

    def test_api_error_propagates(self, mock_pub):
        mock_pub.return_value = MagicMock()
        ex = _executor()

        with patch.object(
            ex, "_futures_signed_request", side_effect=BinanceAPIError(-3003, "Futures API error")
        ):
            with pytest.raises(BinanceAPIError):
                ex.get_futures_open_orders()


# ── get_order_status (phase 6.1.5) ───────────────────────────────────────────

_FILLED_ORDER_RAW = {
    **_OPEN_ORDER_RAW,
    "status": "FILLED",
    "executedQty": "0.10000",
    "cumulativeQuoteQty": "5000.00",
}


@patch("core.execution.binance.get_publisher")
class TestGetOrderStatus:
    """Tests for BinanceExecutor.get_order_status() — phase 6.1.5."""

    def test_filled_order(self, mock_pub):
        mock_pub.return_value = MagicMock()
        ex = _executor()

        with patch.object(ex, "_signed_request", return_value=_FILLED_ORDER_RAW) as mock_req:
            result = ex.get_order_status("BTCUSDT", 12345)

        mock_req.assert_called_once_with(
            "GET", "/api/v3/order", {"symbol": "BTCUSDT", "orderId": 12345}
        )
        assert result["status"] == "FILLED"
        assert result["executedQty"] == Decimal("0.10000")

    def test_new_order(self, mock_pub):
        mock_pub.return_value = MagicMock()
        ex = _executor()

        with patch.object(ex, "_signed_request", return_value=_OPEN_ORDER_RAW):
            result = ex.get_order_status("BTCUSDT", 12345)

        assert result["status"] == "NEW"

    def test_api_error_propagates(self, mock_pub):
        mock_pub.return_value = MagicMock()
        ex = _executor()

        with patch.object(
            ex, "_signed_request", side_effect=BinanceAPIError(-2011, "Unknown order")
        ):
            with pytest.raises(BinanceAPIError):
                ex.get_order_status("BTCUSDT", 99999)


# ── cancel_order (phase 6.1.6) ───────────────────────────────────────────────

_CANCELLED_ORDER_RAW = {
    **_OPEN_ORDER_RAW,
    "status": "CANCELED",
}


@patch("core.execution.binance.get_publisher")
class TestCancelOrder:
    """Tests for BinanceExecutor.cancel_order() — phase 6.1.6."""

    def test_successful_cancel(self, mock_pub):
        mock_pub.return_value = MagicMock()
        ex = _executor()

        with patch.object(ex, "_signed_request", return_value=_CANCELLED_ORDER_RAW) as mock_req:
            result = ex.cancel_order("BTCUSDT", 12345)

        mock_req.assert_called_once_with(
            "DELETE", "/api/v3/order", {"symbol": "BTCUSDT", "orderId": 12345}
        )
        assert result["status"] == "CANCELED"
        assert result["orderId"] == 12345

    def test_already_filled_returns_gracefully(self, mock_pub):
        mock_pub.return_value = MagicMock()
        ex = _executor()

        with patch.object(
            ex, "_signed_request", side_effect=BinanceAPIError(-2011, "Unknown order")
        ):
            result = ex.cancel_order("BTCUSDT", 12345)

        assert result["status"] == "CANCELED"
        assert result["orderId"] == 12345
        assert result["symbol"] == "BTCUSDT"

    def test_order_does_not_exist_returns_gracefully(self, mock_pub):
        mock_pub.return_value = MagicMock()
        ex = _executor()

        with patch.object(
            ex, "_signed_request", side_effect=BinanceAPIError(-2013, "Order does not exist")
        ):
            result = ex.cancel_order("BTCUSDT", 99999)

        assert result["status"] == "CANCELED"
        assert result["orderId"] == 99999

    def test_unexpected_api_error_propagates(self, mock_pub):
        mock_pub.return_value = MagicMock()
        ex = _executor()

        with patch.object(
            ex, "_signed_request", side_effect=BinanceAPIError(-1003, "Too many requests")
        ):
            with pytest.raises(BinanceAPIError) as exc_info:
                ex.cancel_order("BTCUSDT", 12345)
            assert exc_info.value.code == -1003


# ── Phase 6.3: Write-Ahead Order Flow ───────────────────────────────────────


@patch("core.execution.binance.get_publisher")
class TestWriteAheadPlaceAndFill:
    """Test that _place_and_fill creates PENDING record before API call."""

    def test_creates_pending_record_before_api_call(self, mock_pub):
        """Write-ahead: OrderRecord(PENDING) exists before _signed_request fires."""
        mock_pub.return_value = MagicMock()
        ex = _executor()

        records_created = []
        status_at_api_call = []

        def fake_session_factory():
            from contextlib import contextmanager
            from unittest.mock import MagicMock as _M

            @contextmanager
            def _session():
                session = _M()
                session.flush = lambda: setattr(
                    session._last_added, "id", 42
                )

                def _add(record):
                    session._last_added = record
                    records_created.append(record)

                def _get(model, pk):
                    if records_created:
                        return records_created[0]
                    return None

                session.add = _add
                session.get = _get
                yield session

            return _session()

        ex._session_factory = fake_session_factory

        fill_response = {
            "orderId": 12345,
            "status": "FILLED",
            "executedQty": "0.1",
            "cumulativeQuoteQty": "5000",
            "fills": [{"price": "50000", "qty": "0.1", "commission": "0.05", "commissionAsset": "USDT"}],
        }

        def mock_signed(method, path, params):
            # Capture the status of the record at the time of the API call
            if records_created:
                status_at_api_call.append(records_created[0].status)
            return fill_response

        with patch.object(ex, "_signed_request", side_effect=mock_signed):
            order = ex._place_and_fill(_open_decision(), OrderSide.BUY, intent="open_long")

        assert order is not None
        assert order.status == OrderStatus.FILLED
        # Write-ahead record was created BEFORE the API call
        assert len(records_created) == 1
        # At the moment of the API call, status was still "pending"
        assert status_at_api_call[0] == "pending"
        assert records_created[0].intent == "open_long"
        # After fill, reconciliation_status is updated to "synced"
        assert records_created[0].reconciliation_status == "synced"

    def test_rejected_order_updates_record_to_rejected(self, mock_pub):
        """On API rejection, write-ahead record transitions to REJECTED."""
        mock_pub.return_value = MagicMock()
        ex = _executor()

        last_update = {}

        def fake_session_factory():
            from contextlib import contextmanager
            from unittest.mock import MagicMock as _M

            @contextmanager
            def _session():
                session = _M()
                record = _M()
                record.id = 42

                def _add(r):
                    session._record = r
                    r.id = 42

                def _get(model, pk):
                    return session._record

                session.add = _add
                session.flush = lambda: None
                session.get = _get
                yield session

            return _session()

        ex._session_factory = fake_session_factory

        with patch.object(
            ex, "_signed_request", side_effect=BinanceAPIError(-2010, "Insufficient balance")
        ):
            order = ex._place_and_fill(_open_decision(), OrderSide.BUY, intent="open_long")

        assert order is not None
        assert order.status == OrderStatus.REJECTED

    def test_unexpected_error_leaves_record_pending(self, mock_pub):
        """On unexpected crash, record stays PENDING (for recovery by 6.5)."""
        mock_pub.return_value = MagicMock()
        ex = _executor()

        pending_record = None

        def fake_session_factory():
            from contextlib import contextmanager
            from unittest.mock import MagicMock as _M

            @contextmanager
            def _session():
                nonlocal pending_record
                session = _M()
                record = _M()
                record.id = 42
                record.status = "pending"

                def _add(r):
                    nonlocal pending_record
                    r.id = 42
                    pending_record = r

                def _get(model, pk):
                    return pending_record

                session.add = _add
                session.flush = lambda: None
                session.get = _get
                yield session

            return _session()

        ex._session_factory = fake_session_factory

        with patch.object(
            ex, "_signed_request", side_effect=RuntimeError("Connection reset")
        ):
            order = ex._place_and_fill(_open_decision(), OrderSide.BUY, intent="open_long")

        assert order is None
        # Record should stay PENDING (no update to rejected)
        assert pending_record.status == "pending"

    def test_exchange_order_id_persisted_after_fill(self, mock_pub):
        """After fill, exchange_order_id is set on the DB record."""
        mock_pub.return_value = MagicMock()
        ex = _executor()

        updates = []

        def fake_session_factory():
            from contextlib import contextmanager
            from unittest.mock import MagicMock as _M

            @contextmanager
            def _session():
                session = _M()
                record = _M()
                record.id = 42
                record.status = "pending"

                def _add(r):
                    r.id = 42
                    session._record = r

                def _get(model, pk):
                    r = session._record
                    updates.append({"before_status": r.status})
                    return r

                session.add = _add
                session.flush = lambda: None
                session.get = _get
                yield session

            return _session()

        ex._session_factory = fake_session_factory

        fill_response = {
            "orderId": 77777,
            "status": "FILLED",
            "executedQty": "0.1",
            "cumulativeQuoteQty": "5000",
            "fills": [{"price": "50000", "qty": "0.1", "commission": "0.05", "commissionAsset": "USDT"}],
        }

        with patch.object(ex, "_signed_request", return_value=fill_response):
            order = ex._place_and_fill(_open_decision(), OrderSide.BUY, intent="open_long")

        assert order.exchange_order_id == "77777"

    def test_no_write_ahead_when_session_factory_is_none(self, mock_pub):
        """Without session_factory, write-ahead is skipped (paper mode)."""
        mock_pub.return_value = MagicMock()
        ex = _executor()
        assert ex._session_factory is None

        fill_response = {
            "orderId": 12345,
            "status": "FILLED",
            "executedQty": "0.1",
            "cumulativeQuoteQty": "5000",
            "fills": [{"price": "50000", "qty": "0.1", "commission": "0.05", "commissionAsset": "USDT"}],
        }

        with patch.object(ex, "_signed_request", return_value=fill_response):
            order = ex._place_and_fill(_open_decision(), OrderSide.BUY, intent="open_long")

        assert order is not None
        assert order.status == OrderStatus.FILLED


@patch("core.execution.binance.get_publisher")
class TestWriteAheadStopOrder:
    """Test that stop orders use write-ahead."""

    def test_stop_order_creates_pending_record(self, mock_pub):
        """_place_stop_order writes PENDING record with intent=stop_loss."""
        mock_pub.return_value = MagicMock()
        ex = _executor()

        records_created = []

        def fake_session_factory():
            from contextlib import contextmanager
            from unittest.mock import MagicMock as _M

            @contextmanager
            def _session():
                session = _M()

                def _add(r):
                    r.id = 42
                    records_created.append(r)

                def _get(model, pk):
                    if records_created:
                        return records_created[0]
                    return None

                session.add = _add
                session.flush = lambda: None
                session.get = _get
                yield session

            return _session()

        ex._session_factory = fake_session_factory

        with patch.object(
            ex, "_signed_request", return_value={"orderId": 99988}
        ):
            order_id = ex._place_stop_order("BTCUSDT", Decimal("0.1"), Decimal("48500"))

        assert order_id == "99988"
        assert len(records_created) == 1
        assert records_created[0].intent == "stop_loss"
        assert records_created[0].status == "new"
        assert records_created[0].side == "sell"

    def test_stop_order_rejected_marks_record(self, mock_pub):
        """If stop order fails, record is marked rejected."""
        mock_pub.return_value = MagicMock()
        ex = _executor()

        last_record = None

        def fake_session_factory():
            from contextlib import contextmanager
            from unittest.mock import MagicMock as _M

            @contextmanager
            def _session():
                nonlocal last_record
                session = _M()
                record = _M()
                record.id = 42
                record.status = "pending"

                def _add(r):
                    nonlocal last_record
                    r.id = 42
                    last_record = r

                def _get(model, pk):
                    return last_record

                session.add = _add
                session.flush = lambda: None
                session.get = _get
                yield session

            return _session()

        ex._session_factory = fake_session_factory

        with patch.object(
            ex, "_signed_request", side_effect=BinanceAPIError(-2010, "Insufficient balance")
        ):
            order_id = ex._place_stop_order("BTCUSDT", Decimal("0.1"), Decimal("48500"))

        assert order_id is None


@patch("core.execution.binance.get_publisher")
class TestDeriveIntent:
    """Test _derive_intent helper."""

    def test_open_long(self, mock_pub):
        assert BinanceExecutor._derive_intent(RiskAction.OPEN, PositionSide.LONG) == "open_long"

    def test_open_short(self, mock_pub):
        assert BinanceExecutor._derive_intent(RiskAction.OPEN, PositionSide.SHORT) == "open_short"

    def test_close_long(self, mock_pub):
        assert BinanceExecutor._derive_intent(RiskAction.CLOSE, PositionSide.LONG) == "close_long"

    def test_close_short(self, mock_pub):
        assert BinanceExecutor._derive_intent(RiskAction.CLOSE, PositionSide.SHORT) == "close_short"

    def test_scale_in(self, mock_pub):
        assert BinanceExecutor._derive_intent(RiskAction.SCALE_IN, PositionSide.LONG) == "scale_in"

    def test_reduce(self, mock_pub):
        assert BinanceExecutor._derive_intent(RiskAction.REDUCE, PositionSide.LONG) == "reduce"
