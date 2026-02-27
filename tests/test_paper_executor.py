"""
Unit tests for PaperExecutor (task 1.18).

Covers:
    - OPEN  → buy with slippage, Position returned
    - SCALE_IN → buy, position updated
    - CLOSE → sell with slippage, Trade returned
    - REDUCE → partial sell, Trade returned
    - HOLD → returns None
    - Slippage arithmetic (always-adverse) + toggle
    - Order metadata (fill price, slippage %, status, timestamps)
    - Edge cases
    - Event publishing (ORDER_CREATED, ORDER_FILLED)
"""

from datetime import datetime
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from core.events import EventChannel, EventType
from core.execution.paper import PaperExecutor
from core.models import (
    ExecutionResult,
    ExitReason,
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
    RiskAction,
    RiskDecision,
)
from core.portfolio import PortfolioManager


# ── Helpers ──────────────────────────────────────────────────────────────────

T0 = datetime(2026, 2, 20, 10, 0)
T1 = datetime(2026, 2, 20, 10, 15)
T2 = datetime(2026, 2, 20, 10, 30)

SLIPPAGE = Decimal("0.001")  # 0.1 %
TAKER_FEE = Decimal("0.001")


def _pm(capital: Decimal = Decimal("10000")) -> PortfolioManager:
    return PortfolioManager(initial_capital=capital, taker_fee=TAKER_FEE)


def _executor(
    pm: PortfolioManager | None = None,
    strategy: str = "smart_hodler",
    simulate_slippage: bool = True,
) -> PaperExecutor:
    if pm is None:
        pm = _pm()
    return PaperExecutor(
        strategy_name=strategy,
        portfolio_manager=pm,
        slippage=SLIPPAGE,
        simulate_slippage=simulate_slippage,
    )


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


# ── Initialization ───────────────────────────────────────────────────────────


class TestPaperExecutorInit:
    def test_strategy_name(self):
        ex = _executor(strategy="mean_reversion")
        assert ex.strategy_name == "mean_reversion"

    def test_portfolio_manager_reference(self):
        pm = _pm()
        ex = _executor(pm=pm)
        assert ex.portfolio_manager is pm


# ── HOLD ─────────────────────────────────────────────────────────────────────


class TestExecuteHold:
    def test_hold_returns_none(self):
        ex = _executor()
        decision = RiskDecision(action=RiskAction.HOLD)
        assert ex.execute(decision, T0) is None

    def test_hold_does_not_change_balance(self):
        pm = _pm()
        ex = _executor(pm=pm)
        decision = RiskDecision(action=RiskAction.HOLD)
        ex.execute(decision, T0)
        assert pm.portfolio.usdt_balance == Decimal("10000")


# ── OPEN ─────────────────────────────────────────────────────────────────────


class TestExecuteOpen:
    def test_returns_execution_result(self):
        ex = _executor()
        result = ex.execute(_open_decision(), T0)
        assert isinstance(result, ExecutionResult)

    def test_position_created(self):
        ex = _executor()
        result = ex.execute(_open_decision(), T0)
        assert result.position is not None
        assert result.position.symbol == "BTCUSDT"
        assert result.position.size == Decimal("0.1")

    def test_trade_is_none_for_open(self):
        ex = _executor()
        result = ex.execute(_open_decision(), T0)
        assert result.trade is None

    def test_order_is_filled(self):
        ex = _executor()
        result = ex.execute(_open_decision(), T0)
        assert result.order.status == OrderStatus.FILLED
        assert result.order.side == OrderSide.BUY
        assert result.order.order_type == OrderType.MARKET
        assert result.order.filled_amount == Decimal("0.1")
        assert result.order.filled_at == T0

    def test_buy_slippage_applied(self):
        """Buy fill price = price × (1 + slippage)."""
        ex = _executor()
        result = ex.execute(_open_decision(price=Decimal("50000")), T0)
        expected = Decimal("50050")  # 50000 × 1.001
        assert result.order.avg_fill_price == expected

    def test_position_entry_price_includes_slippage(self):
        ex = _executor()
        result = ex.execute(_open_decision(price=Decimal("50000")), T0)
        assert result.position.entry_price == Decimal("50050")

    def test_hard_stop_passed_through(self):
        ex = _executor()
        result = ex.execute(_open_decision(), T0)
        assert result.position.hard_stop_price == Decimal("48500")

    def test_trailing_stop_passed_through(self):
        ex = _executor()
        result = ex.execute(_open_decision(), T0)
        assert result.position.trailing_stop_price == Decimal("49000")

    def test_balance_reduced(self):
        pm = _pm()
        ex = _executor(pm=pm)
        ex.execute(_open_decision(price=Decimal("50000"), size=Decimal("0.1")), T0)
        # cost = 0.1 × 50050 = 5005, fee = 5005 × 0.001 = 5.005
        # total_cost = 5005 + 5.005 = 5010.005
        assert pm.portfolio.usdt_balance < Decimal("5000")

    def test_strategy_name_propagated(self):
        """Position records the correct strategy (verified via close)."""
        pm = _pm()
        ex = PaperExecutor("mean_reversion", pm, SLIPPAGE)
        ex.execute(_open_decision(), T0)
        # Closing will produce a Trade whose strategy field we can check
        trade_result = ex.execute(_close_decision(), T1)
        assert trade_result.trade.strategy == "mean_reversion"


# ── SCALE_IN ─────────────────────────────────────────────────────────────────


class TestExecuteScaleIn:
    def _setup(self):
        pm = _pm()
        ex = _executor(pm=pm)
        ex.execute(_open_decision(), T0)
        return ex, pm

    def test_scale_in_updates_position(self):
        ex, pm = self._setup()
        decision = RiskDecision(
            action=RiskAction.SCALE_IN,
            symbol="BTCUSDT",
            size=Decimal("0.05"),
            price=Decimal("51000"),
        )
        result = ex.execute(decision, T1)
        assert result.position is not None
        # 0.1 + 0.05 = 0.15
        assert result.position.size == Decimal("0.15")

    def test_scale_in_order_is_buy(self):
        ex, _ = self._setup()
        decision = RiskDecision(
            action=RiskAction.SCALE_IN,
            symbol="BTCUSDT",
            size=Decimal("0.05"),
            price=Decimal("51000"),
        )
        result = ex.execute(decision, T1)
        assert result.order.side == OrderSide.BUY

    def test_scale_in_slippage_applied(self):
        ex, _ = self._setup()
        decision = RiskDecision(
            action=RiskAction.SCALE_IN,
            symbol="BTCUSDT",
            size=Decimal("0.05"),
            price=Decimal("51000"),
        )
        result = ex.execute(decision, T1)
        expected = Decimal("51051")  # 51000 × 1.001
        assert result.order.avg_fill_price == expected

    def test_scale_in_trade_is_none(self):
        ex, _ = self._setup()
        decision = RiskDecision(
            action=RiskAction.SCALE_IN,
            symbol="BTCUSDT",
            size=Decimal("0.05"),
            price=Decimal("51000"),
        )
        result = ex.execute(decision, T1)
        assert result.trade is None


# ── CLOSE ────────────────────────────────────────────────────────────────────


class TestExecuteClose:
    def _setup(self):
        pm = _pm()
        ex = _executor(pm=pm)
        ex.execute(_open_decision(), T0)
        return ex, pm

    def test_returns_trade(self):
        ex, _ = self._setup()
        result = ex.execute(_close_decision(), T1)
        assert result.trade is not None
        assert result.trade.symbol == "BTCUSDT"

    def test_position_is_none_for_close(self):
        ex, _ = self._setup()
        result = ex.execute(_close_decision(), T1)
        assert result.position is None

    def test_sell_slippage_applied(self):
        """Sell fill price = price × (1 − slippage)."""
        ex, _ = self._setup()
        result = ex.execute(_close_decision(price=Decimal("51000")), T1)
        expected = Decimal("50949")  # 51000 × 0.999
        assert result.order.avg_fill_price == expected

    def test_order_is_sell(self):
        ex, _ = self._setup()
        result = ex.execute(_close_decision(), T1)
        assert result.order.side == OrderSide.SELL

    def test_exit_reason_on_trade(self):
        ex, _ = self._setup()
        result = ex.execute(_close_decision(), T1)
        assert result.trade.exit_reason == ExitReason.EMA_CROSS

    def test_position_removed_from_portfolio(self):
        ex, pm = self._setup()
        assert pm.has_position("BTCUSDT")
        ex.execute(_close_decision(), T1)
        assert not pm.has_position("BTCUSDT")

    def test_balance_increased_after_close(self):
        ex, pm = self._setup()
        balance_after_open = pm.portfolio.usdt_balance
        ex.execute(_close_decision(price=Decimal("55000")), T1)
        assert pm.portfolio.usdt_balance > balance_after_open


# ── REDUCE ───────────────────────────────────────────────────────────────────


class TestExecuteReduce:
    def _setup(self):
        pm = _pm()
        ex = _executor(pm=pm)
        ex.execute(_open_decision(), T0)
        return ex, pm

    def test_reduce_returns_trade(self):
        ex, _ = self._setup()
        decision = RiskDecision(
            action=RiskAction.REDUCE,
            symbol="BTCUSDT",
            size=Decimal("0.1"),
            price=Decimal("52000"),
            exit_reason=ExitReason.MOMENTUM_FADE,
            sell_fraction=Decimal("0.5"),
        )
        result = ex.execute(decision, T1)
        assert result.trade is not None
        assert result.trade.size == Decimal("0.05")  # half of 0.1

    def test_reduce_order_is_sell(self):
        ex, _ = self._setup()
        decision = RiskDecision(
            action=RiskAction.REDUCE,
            symbol="BTCUSDT",
            size=Decimal("0.1"),
            price=Decimal("52000"),
            exit_reason=ExitReason.MOMENTUM_FADE,
            sell_fraction=Decimal("0.5"),
        )
        result = ex.execute(decision, T1)
        assert result.order.side == OrderSide.SELL

    def test_reduce_keeps_remaining_position(self):
        ex, pm = self._setup()
        decision = RiskDecision(
            action=RiskAction.REDUCE,
            symbol="BTCUSDT",
            size=Decimal("0.1"),
            price=Decimal("52000"),
            exit_reason=ExitReason.MOMENTUM_FADE,
            sell_fraction=Decimal("0.5"),
        )
        ex.execute(decision, T1)
        assert pm.has_position("BTCUSDT")
        assert pm.get_position("BTCUSDT").size == Decimal("0.05")

    def test_reduce_sell_slippage(self):
        ex, _ = self._setup()
        decision = RiskDecision(
            action=RiskAction.REDUCE,
            symbol="BTCUSDT",
            size=Decimal("0.1"),
            price=Decimal("52000"),
            exit_reason=ExitReason.MOMENTUM_FADE,
            sell_fraction=Decimal("0.5"),
        )
        result = ex.execute(decision, T1)
        expected = Decimal("51948")  # 52000 × 0.999
        assert result.order.avg_fill_price == expected


# ── Slippage ─────────────────────────────────────────────────────────────────


class TestSlippage:
    def test_slippage_disabled(self):
        """simulate_slippage=False → fill at exact reference price."""
        ex = _executor(simulate_slippage=False)
        result = ex.execute(_open_decision(price=Decimal("50000")), T0)
        assert result.order.avg_fill_price == Decimal("50000")

    def test_slippage_percent_on_order(self):
        ex = _executor()
        result = ex.execute(_open_decision(price=Decimal("50000")), T0)
        # slippage = |50050 - 50000| / 50000 × 100 = 0.1%
        assert result.order.slippage_percent == Decimal("0.1000")

    def test_slippage_percent_zero_when_disabled(self):
        ex = _executor(simulate_slippage=False)
        result = ex.execute(_open_decision(price=Decimal("50000")), T0)
        assert result.order.slippage_percent == Decimal("0.0000")

    def test_custom_slippage_rate(self):
        """Higher slippage rate (0.5%) produces larger price impact."""
        pm = _pm()
        ex = PaperExecutor("smart_hodler", pm, slippage=Decimal("0.005"))
        result = ex.execute(_open_decision(price=Decimal("50000")), T0)
        # 50000 × 1.005 = 50250
        assert result.order.avg_fill_price == Decimal("50250")


# ── Order metadata ───────────────────────────────────────────────────────────


class TestOrderMetadata:
    def test_order_symbol(self):
        ex = _executor()
        result = ex.execute(_open_decision(symbol="ETHUSDT"), T0)
        assert result.order.symbol == "ETHUSDT"

    def test_order_timestamps(self):
        ex = _executor()
        result = ex.execute(_open_decision(), T0)
        assert result.order.created_at == T0
        assert result.order.updated_at == T0
        assert result.order.filled_at == T0

    def test_order_amount_matches_decision(self):
        pm = _pm(capital=Decimal("50000"))
        ex = _executor(pm=pm)
        result = ex.execute(_open_decision(size=Decimal("0.25")), T0)
        assert result.order.amount == Decimal("0.25")

    def test_order_reference_price(self):
        """Order.price stores the original reference price, not fill."""
        ex = _executor()
        result = ex.execute(_open_decision(price=Decimal("50000")), T0)
        assert result.order.price == Decimal("50000")
        assert result.order.avg_fill_price == Decimal("50050")


# ── Edge cases ───────────────────────────────────────────────────────────────


class TestEdgeCases:
    def test_close_with_no_exit_reason_defaults_to_manual(self):
        pm = _pm()
        ex = _executor(pm=pm)
        ex.execute(_open_decision(), T0)
        decision = RiskDecision(
            action=RiskAction.CLOSE,
            symbol="BTCUSDT",
            size=Decimal("0.1"),
            price=Decimal("51000"),
            exit_reason=None,
        )
        result = ex.execute(decision, T1)
        assert result.trade.exit_reason == ExitReason.MANUAL

    def test_reduce_with_no_fraction_defaults_to_half(self):
        pm = _pm()
        ex = _executor(pm=pm)
        ex.execute(_open_decision(), T0)
        decision = RiskDecision(
            action=RiskAction.REDUCE,
            symbol="BTCUSDT",
            size=Decimal("0.1"),
            price=Decimal("52000"),
            exit_reason=ExitReason.MOMENTUM_FADE,
            sell_fraction=None,
        )
        result = ex.execute(decision, T1)
        # Default fraction = 0.5 → sell half
        assert result.trade.size == Decimal("0.05")

    def test_multiple_open_close_cycles(self):
        pm = _pm()
        ex = _executor(pm=pm)
        # Cycle 1
        ex.execute(_open_decision(), T0)
        ex.execute(_close_decision(), T1)
        assert not pm.has_position("BTCUSDT")
        # Cycle 2
        ex.execute(_open_decision(), T2)
        assert pm.has_position("BTCUSDT")
        assert len(pm.portfolio.trade_history) == 1


# ── Event publishing ─────────────────────────────────────────────────────────


class TestExecutionEvents:
    """Verify ORDER_CREATED and ORDER_FILLED events (task 2.17)."""

    @patch("core.execution.paper.get_publisher")
    def test_order_created_on_open(self, mock_get_pub):
        pub = MagicMock()
        mock_get_pub.return_value = pub

        ex = _executor()
        ex.execute(_open_decision(), T0)

        pub.publish_model.assert_called_once()
        args = pub.publish_model.call_args[0]
        assert args[0] == EventChannel.EXECUTION
        assert args[1] == EventType.ORDER_CREATED

    @patch("core.execution.paper.get_publisher")
    def test_order_created_contains_order_data(self, mock_get_pub):
        pub = MagicMock()
        mock_get_pub.return_value = pub

        ex = _executor()
        ex.execute(_open_decision(symbol="ETHUSDT"), T0)

        order_model = pub.publish_model.call_args[0][2]
        assert order_model.symbol == "ETHUSDT"
        assert order_model.side == OrderSide.BUY
        assert order_model.status == OrderStatus.FILLED

    @patch("core.execution.paper.get_publisher")
    def test_order_filled_on_open(self, mock_get_pub):
        pub = MagicMock()
        mock_get_pub.return_value = pub

        ex = _executor()
        ex.execute(_open_decision(), T0)

        pub.publish.assert_called_once()
        args = pub.publish.call_args[0]
        assert args[0] == EventChannel.EXECUTION
        assert args[1] == EventType.ORDER_FILLED
        assert args[2]["action"] == "open"
        assert args[2]["symbol"] == "BTCUSDT"
        assert args[2]["side"] == "buy"

    @patch("core.execution.paper.get_publisher")
    def test_order_filled_on_close(self, mock_get_pub):
        pub = MagicMock()
        mock_get_pub.return_value = pub

        pm = _pm()
        ex = _executor(pm=pm)
        ex.execute(_open_decision(), T0)
        pub.reset_mock()

        ex.execute(_close_decision(), T1)

        pub.publish.assert_called_once()
        data = pub.publish.call_args[0][2]
        assert data["action"] == "close"
        assert data["side"] == "sell"

    @patch("core.execution.paper.get_publisher")
    def test_order_filled_on_reduce(self, mock_get_pub):
        pub = MagicMock()
        mock_get_pub.return_value = pub

        pm = _pm()
        ex = _executor(pm=pm)
        ex.execute(_open_decision(), T0)
        pub.reset_mock()

        decision = RiskDecision(
            action=RiskAction.REDUCE,
            symbol="BTCUSDT",
            size=Decimal("0.1"),
            price=Decimal("52000"),
            exit_reason=ExitReason.MOMENTUM_FADE,
            sell_fraction=Decimal("0.5"),
        )
        ex.execute(decision, T1)

        data = pub.publish.call_args[0][2]
        assert data["action"] == "reduce"

    @patch("core.execution.paper.get_publisher")
    def test_order_filled_on_scale_in(self, mock_get_pub):
        pub = MagicMock()
        mock_get_pub.return_value = pub

        pm = _pm()
        ex = _executor(pm=pm)
        ex.execute(_open_decision(), T0)
        pub.reset_mock()

        decision = RiskDecision(
            action=RiskAction.SCALE_IN,
            symbol="BTCUSDT",
            size=Decimal("0.05"),
            price=Decimal("49000"),
        )
        ex.execute(decision, T1)

        data = pub.publish.call_args[0][2]
        assert data["action"] == "scale_in"

    @patch("core.execution.paper.get_publisher")
    def test_no_events_on_hold(self, mock_get_pub):
        pub = MagicMock()
        mock_get_pub.return_value = pub

        ex = _executor()
        ex.execute(RiskDecision(action=RiskAction.HOLD), T0)

        pub.publish.assert_not_called()
        pub.publish_model.assert_not_called()

    @patch("core.execution.paper.get_publisher")
    def test_two_events_per_fill(self, mock_get_pub):
        """Each actionable execution publishes ORDER_CREATED + ORDER_FILLED."""
        pub = MagicMock()
        mock_get_pub.return_value = pub

        ex = _executor()
        ex.execute(_open_decision(), T0)

        assert pub.publish_model.call_count == 1  # ORDER_CREATED
        assert pub.publish.call_count == 1  # ORDER_FILLED

    @patch("core.execution.paper.get_publisher")
    def test_order_filled_includes_fill_price(self, mock_get_pub):
        pub = MagicMock()
        mock_get_pub.return_value = pub

        ex = _executor()
        result = ex.execute(_open_decision(price=Decimal("50000")), T0)

        data = pub.publish.call_args[0][2]
        # avg_fill_price should match the result's order
        assert data["avg_fill_price"] == str(result.order.avg_fill_price)
