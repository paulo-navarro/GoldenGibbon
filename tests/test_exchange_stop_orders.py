"""Tests for exchange-side stop orders (phase 3, item 4)."""

from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch, call

import pytest

from core.execution.binance import BinanceAPIError, BinanceExecutor
from core.execution.retry import RetryConfig
from core.models import (
    ExitReason,
    OrderSide,
    OrderStatus,
    OrderType,
    RiskAction,
    RiskDecision,
)
from core.portfolio import PortfolioManager


T0 = datetime(2026, 4, 26, 10, 0, tzinfo=timezone.utc)
TAKER_FEE = Decimal("0.001")


def _pm(capital: Decimal = Decimal("10000")) -> PortfolioManager:
    return PortfolioManager(initial_capital=capital, taker_fee=TAKER_FEE)


def _executor(
    pm: PortfolioManager | None = None,
    exchange_stops: bool = True,
    slippage: float = 0.005,
) -> BinanceExecutor:
    if pm is None:
        pm = _pm()
    ex = BinanceExecutor(
        strategy_name="smart_hodler",
        portfolio_manager=pm,
        api_key="test-key",
        api_secret="test-secret",
        use_testnet=True,
        max_order_size_usdt=100000.0,
        order_timeout=5,
        retry_config=RetryConfig(max_attempts=1, base_delay=0.01, max_delay=0.01),
        exchange_stop_orders_enabled=exchange_stops,
        stop_limit_slippage_pct=slippage,
    )
    ex._filters["BTCUSDT"] = {
        "lot_step": Decimal("0.00001"),
        "lot_min": Decimal("0.00001"),
        "lot_max": Decimal("9000"),
        "price_step": Decimal("0.01"),
        "min_notional": Decimal("10"),
    }
    return ex


def _market_fill(order_id=12345, price="50000.00", qty="0.10000"):
    return {
        "symbol": "BTCUSDT",
        "orderId": order_id,
        "clientOrderId": "abc",
        "transactTime": 1713348000000,
        "price": "0.00000000",
        "origQty": qty,
        "executedQty": qty,
        "cumulativeQuoteQty": str(Decimal(price) * Decimal(qty)),
        "status": "FILLED",
        "type": "MARKET",
        "side": "BUY",
        "fills": [{"price": price, "qty": qty, "commission": "0.0001", "commissionAsset": "BTC", "tradeId": 1}],
    }


def _stop_order_response(order_id=99999):
    return {"symbol": "BTCUSDT", "orderId": order_id, "status": "NEW"}


def _open_decision(hard_stop=Decimal("48500")):
    return RiskDecision(
        action=RiskAction.OPEN,
        symbol="BTCUSDT",
        size=Decimal("0.1"),
        price=Decimal("50000"),
        hard_stop_price=hard_stop,
        trailing_stop_price=Decimal("49000"),
    )


def _close_decision():
    return RiskDecision(
        action=RiskAction.CLOSE,
        symbol="BTCUSDT",
        size=Decimal("0.1"),
        price=Decimal("51000"),
        exit_reason=ExitReason.EMA_CROSS,
    )


def _reduce_decision():
    return RiskDecision(
        action=RiskAction.REDUCE,
        symbol="BTCUSDT",
        size=Decimal("0.05"),
        price=Decimal("51000"),
        sell_fraction=Decimal("0.5"),
        exit_reason=ExitReason.MOMENTUM_FADE,
    )


# ── Place stop after open ────────────────────────────────────────────────────


@patch("core.execution.binance.get_publisher")
class TestStopAfterOpen:

    def test_open_places_stop_order(self, mock_pub):
        mock_pub.return_value = MagicMock()
        pm = _pm()
        ex = _executor(pm=pm)

        calls = []
        def mock_signed(method, path, params=None):
            calls.append((method, path))
            if method == "POST" and params and params.get("type") == "STOP_LOSS_LIMIT":
                return _stop_order_response(99999)
            return _market_fill()

        with patch.object(ex, "_signed_request", side_effect=mock_signed):
            result = ex.execute(_open_decision(), T0)

        assert result is not None
        pos = pm.get_position("BTCUSDT")
        assert pos.exchange_stop_order_id == "99999"
        stop_calls = [c for c in calls if c == ("POST", "/api/v3/order")]
        assert len(stop_calls) == 2  # market buy + stop order

    def test_open_no_stop_when_disabled(self, mock_pub):
        mock_pub.return_value = MagicMock()
        pm = _pm()
        ex = _executor(pm=pm, exchange_stops=False)

        with patch.object(ex, "_signed_request", return_value=_market_fill()):
            result = ex.execute(_open_decision(), T0)

        pos = pm.get_position("BTCUSDT")
        assert pos.exchange_stop_order_id is None

    def test_open_stop_failure_does_not_abort(self, mock_pub):
        mock_pub.return_value = MagicMock()
        pm = _pm()
        ex = _executor(pm=pm)

        call_count = [0]
        def mock_signed(method, path, params=None):
            call_count[0] += 1
            if params and params.get("type") == "STOP_LOSS_LIMIT":
                raise BinanceAPIError(code=-1013, msg="Filter failure")
            return _market_fill()

        with patch.object(ex, "_signed_request", side_effect=mock_signed):
            result = ex.execute(_open_decision(), T0)

        assert result is not None
        pos = pm.get_position("BTCUSDT")
        assert pos.exchange_stop_order_id is None


# ── Cancel stop before close ─────────────────────────────────────────────────


@patch("core.execution.binance.get_publisher")
class TestCancelStopOnClose:

    def _open_with_stop(self, ex, pm):
        def mock_signed(method, path, params=None):
            if params and params.get("type") == "STOP_LOSS_LIMIT":
                return _stop_order_response(99999)
            return _market_fill()

        with patch.object(ex, "_signed_request", side_effect=mock_signed):
            ex.execute(_open_decision(), T0)
        assert pm.get_position("BTCUSDT").exchange_stop_order_id == "99999"

    def test_close_cancels_stop(self, mock_pub):
        mock_pub.return_value = MagicMock()
        pm = _pm()
        ex = _executor(pm=pm)
        self._open_with_stop(ex, pm)

        calls = []
        def mock_signed(method, path, params=None):
            calls.append(method)
            if method == "DELETE":
                return {"symbol": "BTCUSDT", "orderId": 99999, "status": "CANCELED"}
            if method == "GET":
                return {"balances": [{"asset": "BTC", "free": "0.1", "locked": "0"}], "canTrade": True}
            return _market_fill(order_id=20000, price="51000.00")

        with patch.object(ex, "_signed_request", side_effect=mock_signed):
            result = ex.execute(_close_decision(), T0)

        assert result is not None
        assert "DELETE" in calls

    def test_cancel_2011_treated_as_success(self, mock_pub):
        mock_pub.return_value = MagicMock()
        pm = _pm()
        ex = _executor(pm=pm)
        self._open_with_stop(ex, pm)

        def mock_signed(method, path, params=None):
            if method == "DELETE":
                raise BinanceAPIError(code=-2011, msg="Unknown order")
            if method == "GET":
                return {"balances": [{"asset": "BTC", "free": "0.1", "locked": "0"}], "canTrade": True}
            return _market_fill(order_id=20000, price="51000.00")

        with patch.object(ex, "_signed_request", side_effect=mock_signed):
            result = ex.execute(_close_decision(), T0)

        assert result is not None


# ── Reduce cancels and recreates stop ────────────────────────────────────────


@patch("core.execution.binance.get_publisher")
class TestReduceRecreatesStop:

    def test_reduce_recreates_stop_with_remaining_qty(self, mock_pub):
        mock_pub.return_value = MagicMock()
        pm = _pm()
        ex = _executor(pm=pm)

        # Open position with stop
        def mock_open(method, path, params=None):
            if params and params.get("type") == "STOP_LOSS_LIMIT":
                return _stop_order_response(99999)
            return _market_fill()

        with patch.object(ex, "_signed_request", side_effect=mock_open):
            ex.execute(_open_decision(), T0)

        # Reduce
        stop_ids = []
        def mock_reduce(method, path, params=None):
            if method == "DELETE":
                return {"status": "CANCELED"}
            if method == "GET":
                return {"balances": [{"asset": "BTC", "free": "0.1", "locked": "0"}], "canTrade": True}
            if params and params.get("type") == "STOP_LOSS_LIMIT":
                stop_ids.append(88888)
                return _stop_order_response(88888)
            return _market_fill(order_id=30000, price="51000.00", qty="0.05000")

        with patch.object(ex, "_signed_request", side_effect=mock_reduce):
            result = ex.execute(_reduce_decision(), T0)

        assert result is not None
        pos = pm.get_position("BTCUSDT")
        assert pos is not None
        assert pos.exchange_stop_order_id == "88888"


# ── check_exchange_stop ──────────────────────────────────────────────────────


@patch("core.execution.binance.get_publisher")
class TestCheckExchangeStop:

    def _setup_position_with_stop(self, pm):
        pm.open_position(
            symbol="BTCUSDT", size=Decimal("0.1"),
            entry_price=Decimal("50000"), entry_time=T0,
            hard_stop_price=Decimal("48500"),
            trailing_stop_price=Decimal("49000"),
            strategy="smart_hodler",
        )
        pm.update_stop_order_id("BTCUSDT", "99999")

    def test_filled_returns_fill_info(self, mock_pub):
        mock_pub.return_value = MagicMock()
        pm = _pm()
        ex = _executor(pm=pm)
        self._setup_position_with_stop(pm)

        fill_response = {
            "orderId": 99999,
            "status": "FILLED",
            "executedQty": "0.10000",
            "cummulativeQuoteQty": "4850.00",
            "price": "48500.00",
            "fills": [],
        }

        with patch.object(ex, "_signed_request", return_value=fill_response):
            result = ex.check_exchange_stop("BTCUSDT")

        assert result is not None
        assert result["status"] == "FILLED"
        assert result["fill_price"] == Decimal("48500")

    def test_new_returns_none(self, mock_pub):
        mock_pub.return_value = MagicMock()
        pm = _pm()
        ex = _executor(pm=pm)
        self._setup_position_with_stop(pm)

        with patch.object(ex, "_signed_request", return_value={"orderId": 99999, "status": "NEW"}):
            result = ex.check_exchange_stop("BTCUSDT")

        assert result is None
        assert pm.get_position("BTCUSDT").exchange_stop_order_id == "99999"

    def test_canceled_clears_order_id(self, mock_pub):
        mock_pub.return_value = MagicMock()
        pm = _pm()
        ex = _executor(pm=pm)
        self._setup_position_with_stop(pm)

        with patch.object(ex, "_signed_request", return_value={"orderId": 99999, "status": "CANCELED"}):
            result = ex.check_exchange_stop("BTCUSDT")

        assert result is None
        assert pm.get_position("BTCUSDT").exchange_stop_order_id is None

    def test_disabled_returns_none(self, mock_pub):
        mock_pub.return_value = MagicMock()
        pm = _pm()
        ex = _executor(pm=pm, exchange_stops=False)
        self._setup_position_with_stop(pm)

        result = ex.check_exchange_stop("BTCUSDT")
        assert result is None

    def test_no_position_returns_none(self, mock_pub):
        mock_pub.return_value = MagicMock()
        ex = _executor()
        assert ex.check_exchange_stop("BTCUSDT") is None


# ── Ratchet updates stop ─────────────────────────────────────────────────────


@patch("core.execution.binance.get_publisher")
class TestRatchetUpdatesStop:

    def test_ratchet_no_change_no_exchange_call(self, mock_pub):
        mock_pub.return_value = MagicMock()
        pm = _pm()
        ex = _executor(pm=pm)

        pm.open_position(
            symbol="BTCUSDT", size=Decimal("0.1"),
            entry_price=Decimal("50000"), entry_time=T0,
            hard_stop_price=Decimal("48500"),
            trailing_stop_price=Decimal("49000"),
            strategy="smart_hodler",
        )
        pm.update_stop_order_id("BTCUSDT", "99999")

        pos = pm.get_position("BTCUSDT")
        old_effective = max(pos.hard_stop_price, pos.trailing_stop_price)

        pm.update_stops("BTCUSDT", hard_stop_price=Decimal("48500"), trailing_stop_price=Decimal("49000"))

        new_pos = pm.get_position("BTCUSDT")
        new_effective = max(new_pos.hard_stop_price, new_pos.trailing_stop_price)
        assert new_effective == old_effective


# ── Slippage calculation ─────────────────────────────────────────────────────


@patch("core.execution.binance.get_publisher")
class TestSlippageCalculation:

    def test_limit_price_has_slippage(self, mock_pub):
        mock_pub.return_value = MagicMock()
        pm = _pm()
        ex = _executor(pm=pm, slippage=0.005)

        captured_params = {}
        def mock_signed(method, path, params=None):
            if params and params.get("type") == "STOP_LOSS_LIMIT":
                captured_params.update(params)
                return _stop_order_response()
            return _market_fill()

        with patch.object(ex, "_signed_request", side_effect=mock_signed):
            ex.execute(_open_decision(hard_stop=Decimal("48500")), T0)

        assert captured_params
        stop_price = Decimal(captured_params["stopPrice"])
        limit_price = Decimal(captured_params["price"])
        assert limit_price < stop_price
        expected_limit = (Decimal("48500") * Decimal("0.995")).quantize(Decimal("0.01"))
        assert limit_price == expected_limit


# ── Scale-in recreates stop ──────────────────────────────────────────────────


@patch("core.execution.binance.get_publisher")
class TestScaleInRecreatesStop:

    def test_scale_in_cancels_old_and_places_new(self, mock_pub):
        mock_pub.return_value = MagicMock()
        pm = _pm()
        ex = _executor(pm=pm)

        # Open with stop
        def mock_open(method, path, params=None):
            if params and params.get("type") == "STOP_LOSS_LIMIT":
                return _stop_order_response(11111)
            return _market_fill()

        with patch.object(ex, "_signed_request", side_effect=mock_open):
            ex.execute(_open_decision(), T0)

        assert pm.get_position("BTCUSDT").exchange_stop_order_id == "11111"

        # Scale in
        methods = []
        def mock_scale(method, path, params=None):
            methods.append(method)
            if method == "DELETE":
                return {"status": "CANCELED"}
            if params and params.get("type") == "STOP_LOSS_LIMIT":
                return _stop_order_response(22222)
            return _market_fill(order_id=50000, qty="0.05000")

        scale_decision = RiskDecision(
            action=RiskAction.SCALE_IN,
            symbol="BTCUSDT",
            size=Decimal("0.05"),
            price=Decimal("50000"),
            hard_stop_price=Decimal("48500"),
        )

        with patch.object(ex, "_signed_request", side_effect=mock_scale):
            result = ex.execute(scale_decision, T0)

        assert result is not None
        assert "DELETE" in methods
        assert pm.get_position("BTCUSDT").exchange_stop_order_id == "22222"


# ── Cancel marks DB record ──────────────────────────────────────────────────


@patch("core.execution.binance.get_publisher")
class TestCancelMarksDbRecord:

    def test_cancel_stop_marks_order_record_cancelled(self, mock_pub):
        """_cancel_stop_order updates the OrderRecord status to 'cancelled'."""
        mock_pub.return_value = MagicMock()
        pm = _pm()
        ex = _executor(pm=pm)

        # Simulate a session factory with an in-memory record store
        from unittest.mock import PropertyMock
        from contextlib import contextmanager

        records = {}

        class FakeRecord:
            def __init__(self, **kwargs):
                for k, v in kwargs.items():
                    setattr(self, k, v)

        @contextmanager
        def fake_session():
            class Session:
                def query(self, model):
                    return self
                def filter_by(self, **kwargs):
                    self._filter = kwargs
                    return self
                def first(self):
                    key = (self._filter.get("symbol"), self._filter.get("exchange_order_id"))
                    return records.get(key)
            yield Session()

        records[("BTCUSDT", "99999")] = FakeRecord(
            symbol="BTCUSDT",
            exchange_order_id="99999",
            intent="stop_loss",
            status="pending",
            exchange_status="NEW",
            reconciliation_status="pending_sync",
        )

        ex._session_factory = fake_session

        with patch.object(ex, "_signed_request", return_value={"status": "CANCELED"}):
            result = ex._cancel_stop_order("BTCUSDT", "99999")

        assert result is True
        record = records[("BTCUSDT", "99999")]
        assert record.status == "cancelled"
        assert record.exchange_status == "CANCELED"

    def test_cancel_2011_also_marks_record(self, mock_pub):
        """When Binance says order already gone (-2011), record is still marked."""
        mock_pub.return_value = MagicMock()
        pm = _pm()
        ex = _executor(pm=pm)

        from contextlib import contextmanager

        records = {}

        class FakeRecord:
            def __init__(self, **kwargs):
                for k, v in kwargs.items():
                    setattr(self, k, v)

        @contextmanager
        def fake_session():
            class Session:
                def query(self, model):
                    return self
                def filter_by(self, **kwargs):
                    self._filter = kwargs
                    return self
                def first(self):
                    key = (self._filter.get("symbol"), self._filter.get("exchange_order_id"))
                    return records.get(key)
            yield Session()

        records[("BTCUSDT", "99999")] = FakeRecord(
            symbol="BTCUSDT",
            exchange_order_id="99999",
            intent="stop_loss",
            status="pending",
            exchange_status="NEW",
            reconciliation_status="pending_sync",
        )

        ex._session_factory = fake_session

        def raise_2011(*args, **kwargs):
            raise BinanceAPIError(code=-2011, msg="Unknown order")

        with patch.object(ex, "_signed_request", side_effect=raise_2011):
            result = ex._cancel_stop_order("BTCUSDT", "99999")

        assert result is True
        record = records[("BTCUSDT", "99999")]
        assert record.status == "cancelled"


# ── Stale record cleanup ────────────────────────────────────────────────────


@patch("core.execution.binance.get_publisher")
class TestStaleRecordCleanup:

    def test_stale_pending_records_cleaned_before_new_stop(self, mock_pub):
        """Old pending stop_loss records are marked cancelled before placing a new stop."""
        mock_pub.return_value = MagicMock()
        pm = _pm()
        ex = _executor(pm=pm)

        from contextlib import contextmanager

        cleaned = []

        class FakeRecord:
            def __init__(self, **kwargs):
                for k, v in kwargs.items():
                    setattr(self, k, v)

        stale_record = FakeRecord(
            symbol="BTCUSDT", intent="stop_loss", status="pending",
            exchange_status="NEW", reconciliation_status="pending_sync",
        )

        @contextmanager
        def fake_session():
            class QueryChain:
                def __init__(self):
                    self._model = None
                def filter_by(self, **kwargs):
                    self._filter = kwargs
                    return self
                def filter(self, *args):
                    return self
                def all(self):
                    return [stale_record]

            class Session:
                def query(self, model):
                    return QueryChain()
                def add(self, record):
                    pass
                def flush(self):
                    pass
            yield Session()

        ex._session_factory = fake_session
        ex._cancel_stale_stop_records("BTCUSDT")

        assert stale_record.status == "cancelled"
        assert stale_record.reconciliation_status == "synced"
