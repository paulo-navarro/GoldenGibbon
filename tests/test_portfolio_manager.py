"""
Unit tests for PortfolioManager.

Covers: initialisation, open / close / reduce / scale-in positions,
fee accounting, stop updates, snapshots, error paths,
multi-symbol support, and event publishing.
"""

from datetime import datetime, timedelta
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from core.events import EventChannel, EventType
from core.models import ExitReason, Position, PortfolioSnapshot, Trade
from core.portfolio import PortfolioManager


# ── Helpers ──────────────────────────────────────────────────────────────────

T0 = datetime(2026, 2, 17, 10, 0)
T1 = T0 + timedelta(hours=2)
T2 = T0 + timedelta(hours=4)
FEE_RATE = Decimal("0.001")  # 0.1 % default


def _fee(amount: Decimal) -> Decimal:
    """Mirror the manager's fee calculation."""
    return (amount * FEE_RATE).quantize(Decimal("0.00000001"))


# ── Initialisation ───────────────────────────────────────────────────────────


class TestInitialisation:
    """PortfolioManager construction and initial state."""

    def test_initial_state(self, portfolio_manager: PortfolioManager):
        p = portfolio_manager.portfolio
        assert p.usdt_balance == Decimal("10000")
        assert p.equity == Decimal("10000")
        assert len(p.positions) == 0
        assert p.open_trades_count == 0
        assert p.total_pnl == Decimal("0")
        assert p.trade_history == []
        assert p.equity_curve == []

    def test_initial_capital_property(self, portfolio_manager: PortfolioManager):
        assert portfolio_manager.initial_capital == Decimal("10000")

    def test_zero_capital_rejected(self):
        with pytest.raises(ValueError, match="positive"):
            PortfolioManager(initial_capital=Decimal("0"))

    def test_negative_capital_rejected(self):
        with pytest.raises(ValueError, match="positive"):
            PortfolioManager(initial_capital=Decimal("-100"))

    def test_custom_fee_rate(self):
        pm = PortfolioManager(
            initial_capital=Decimal("5000"),
            taker_fee=Decimal("0.0005"),
        )
        assert pm._taker_fee == Decimal("0.0005")


# ── Open Position ────────────────────────────────────────────────────────────


class TestOpenPosition:
    """Opening new positions."""

    def test_open_position_basic(self, portfolio_manager: PortfolioManager):
        pos = portfolio_manager.open_position(
            symbol="BTCUSDT",
            size=Decimal("0.1"),
            entry_price=Decimal("50000"),
            entry_time=T0,
        )
        assert pos.symbol == "BTCUSDT"
        assert pos.size == Decimal("0.1")
        assert pos.entry_price == Decimal("50000")
        assert pos.scale_in_count == 0

        p = portfolio_manager.portfolio
        cost = Decimal("0.1") * Decimal("50000")  # 5000
        fee = _fee(cost)  # 5.0
        assert p.usdt_balance == Decimal("10000") - cost - fee
        assert p.open_trades_count == 1

    def test_open_position_appears_in_dict(self, portfolio_manager: PortfolioManager):
        portfolio_manager.open_position(
            symbol="ETHUSDT",
            size=Decimal("1"),
            entry_price=Decimal("3000"),
            entry_time=T0,
        )
        assert portfolio_manager.has_position("ETHUSDT")
        assert portfolio_manager.get_position("ETHUSDT") is not None

    def test_open_duplicate_rejected(self, portfolio_manager: PortfolioManager):
        portfolio_manager.open_position(
            symbol="BTCUSDT",
            size=Decimal("0.01"),
            entry_price=Decimal("50000"),
            entry_time=T0,
        )
        with pytest.raises(ValueError, match="already exists"):
            portfolio_manager.open_position(
                symbol="BTCUSDT",
                size=Decimal("0.01"),
                entry_price=Decimal("51000"),
                entry_time=T1,
            )

    def test_insufficient_funds(self, portfolio_manager: PortfolioManager):
        with pytest.raises(ValueError, match="Insufficient funds"):
            portfolio_manager.open_position(
                symbol="BTCUSDT",
                size=Decimal("1"),
                entry_price=Decimal("20000"),
                entry_time=T0,
            )


# ── Close Position ───────────────────────────────────────────────────────────


class TestClosePosition:
    """Full close of a position."""

    def test_close_profitable_trade(self, portfolio_manager: PortfolioManager):
        portfolio_manager.open_position(
            symbol="BTCUSDT",
            size=Decimal("0.1"),
            entry_price=Decimal("50000"),
            entry_time=T0,
        )
        trade = portfolio_manager.close_position(
            symbol="BTCUSDT",
            exit_price=Decimal("52000"),
            exit_time=T1,
            exit_reason=ExitReason.TRAILING_STOP,
        )

        # Trade record checks
        assert trade.symbol == "BTCUSDT"
        assert trade.entry_price == Decimal("50000")
        assert trade.exit_price == Decimal("52000")
        assert trade.size == Decimal("0.1")
        assert trade.exit_reason == ExitReason.TRAILING_STOP
        assert trade.pnl_usdt > 0
        assert trade.duration_minutes == 120

        # Position removed
        assert not portfolio_manager.has_position("BTCUSDT")
        assert portfolio_manager.portfolio.open_trades_count == 0

        # Trade in history
        assert len(portfolio_manager.portfolio.trade_history) == 1
        assert portfolio_manager.portfolio.trade_history[0] is trade

    def test_close_losing_trade(self, portfolio_manager: PortfolioManager):
        portfolio_manager.open_position(
            symbol="BTCUSDT",
            size=Decimal("0.1"),
            entry_price=Decimal("50000"),
            entry_time=T0,
        )
        trade = portfolio_manager.close_position(
            symbol="BTCUSDT",
            exit_price=Decimal("48000"),
            exit_time=T1,
            exit_reason=ExitReason.HARD_STOP,
        )
        assert trade.pnl_usdt < 0
        assert trade.pnl_percent < 0

    def test_close_no_position_raises(self, portfolio_manager: PortfolioManager):
        with pytest.raises(ValueError, match="No position"):
            portfolio_manager.close_position(
                symbol="BTCUSDT",
                exit_price=Decimal("50000"),
                exit_time=T0,
                exit_reason=ExitReason.MANUAL,
            )


# ── Reduce Position (partial) ───────────────────────────────────────────────


class TestReducePosition:
    """Partial sells."""

    def test_reduce_half(self, portfolio_manager: PortfolioManager):
        portfolio_manager.open_position(
            symbol="BTCUSDT",
            size=Decimal("0.1"),
            entry_price=Decimal("50000"),
            entry_time=T0,
        )
        trade = portfolio_manager.reduce_position(
            symbol="BTCUSDT",
            sell_fraction=Decimal("0.5"),
            exit_price=Decimal("51000"),
            exit_time=T1,
            exit_reason=ExitReason.MOMENTUM_FADE,
        )

        # Sold half
        assert trade.size == Decimal("0.05000000")
        assert trade.exit_reason == ExitReason.MOMENTUM_FADE

        # Remaining position is half
        pos = portfolio_manager.get_position("BTCUSDT")
        assert pos is not None
        assert pos.size == Decimal("0.05000000")

        # Trade recorded
        assert len(portfolio_manager.portfolio.trade_history) == 1

    def test_reduce_then_close(self, portfolio_manager: PortfolioManager):
        portfolio_manager.open_position(
            symbol="BTCUSDT",
            size=Decimal("0.1"),
            entry_price=Decimal("50000"),
            entry_time=T0,
        )
        portfolio_manager.reduce_position(
            symbol="BTCUSDT",
            sell_fraction=Decimal("0.5"),
            exit_price=Decimal("51000"),
            exit_time=T1,
            exit_reason=ExitReason.MOMENTUM_FADE,
        )
        portfolio_manager.close_position(
            symbol="BTCUSDT",
            exit_price=Decimal("52000"),
            exit_time=T2,
            exit_reason=ExitReason.TRAILING_STOP,
        )

        assert not portfolio_manager.has_position("BTCUSDT")
        assert len(portfolio_manager.portfolio.trade_history) == 2
        assert portfolio_manager.portfolio.open_trades_count == 0

    def test_invalid_fraction_zero(self, portfolio_manager: PortfolioManager):
        portfolio_manager.open_position(
            symbol="BTCUSDT",
            size=Decimal("0.1"),
            entry_price=Decimal("50000"),
            entry_time=T0,
        )
        with pytest.raises(ValueError, match="sell_fraction"):
            portfolio_manager.reduce_position(
                symbol="BTCUSDT",
                sell_fraction=Decimal("0"),
                exit_price=Decimal("51000"),
                exit_time=T1,
                exit_reason=ExitReason.MANUAL,
            )

    def test_invalid_fraction_over_one(self, portfolio_manager: PortfolioManager):
        portfolio_manager.open_position(
            symbol="BTCUSDT",
            size=Decimal("0.1"),
            entry_price=Decimal("50000"),
            entry_time=T0,
        )
        with pytest.raises(ValueError, match="sell_fraction"):
            portfolio_manager.reduce_position(
                symbol="BTCUSDT",
                sell_fraction=Decimal("1.5"),
                exit_price=Decimal("51000"),
                exit_time=T1,
                exit_reason=ExitReason.MANUAL,
            )


# ── Scale In ─────────────────────────────────────────────────────────────────


class TestScaleIn:
    """Scaled entries."""

    def test_scale_in_once(self, portfolio_manager: PortfolioManager):
        portfolio_manager.open_position(
            symbol="BTCUSDT",
            size=Decimal("0.05"),
            entry_price=Decimal("50000"),
            entry_time=T0,
        )
        updated = portfolio_manager.scale_in(
            symbol="BTCUSDT",
            additional_size=Decimal("0.025"),
            price=Decimal("51000"),
            time=T1,
        )

        assert updated.size == Decimal("0.075")
        assert updated.scale_in_count == 1
        # Weighted average: (50000*0.05 + 51000*0.025) / 0.075 ≈ 50333.33
        assert updated.entry_price > Decimal("50000")
        assert updated.entry_price < Decimal("51000")

    def test_scale_in_twice(self, portfolio_manager: PortfolioManager):
        portfolio_manager.open_position(
            symbol="BTCUSDT",
            size=Decimal("0.05"),
            entry_price=Decimal("50000"),
            entry_time=T0,
        )
        portfolio_manager.scale_in(
            symbol="BTCUSDT",
            additional_size=Decimal("0.025"),
            price=Decimal("51000"),
            time=T1,
        )
        updated = portfolio_manager.scale_in(
            symbol="BTCUSDT",
            additional_size=Decimal("0.025"),
            price=Decimal("52000"),
            time=T2,
        )
        assert updated.scale_in_count == 2
        assert updated.size == Decimal("0.1")

    def test_scale_in_max_exceeded(self, portfolio_manager: PortfolioManager):
        portfolio_manager.open_position(
            symbol="BTCUSDT",
            size=Decimal("0.04"),
            entry_price=Decimal("50000"),
            entry_time=T0,
        )
        portfolio_manager.scale_in(
            symbol="BTCUSDT",
            additional_size=Decimal("0.02"),
            price=Decimal("50500"),
            time=T1,
        )
        portfolio_manager.scale_in(
            symbol="BTCUSDT",
            additional_size=Decimal("0.02"),
            price=Decimal("51000"),
            time=T2,
        )
        with pytest.raises(ValueError, match="Max scale-in count"):
            portfolio_manager.scale_in(
                symbol="BTCUSDT",
                additional_size=Decimal("0.01"),
                price=Decimal("52000"),
                time=T2 + timedelta(hours=1),
            )

    def test_scale_in_no_position(self, portfolio_manager: PortfolioManager):
        with pytest.raises(ValueError, match="No position"):
            portfolio_manager.scale_in(
                symbol="BTCUSDT",
                additional_size=Decimal("0.01"),
                price=Decimal("50000"),
                time=T0,
            )

    def test_scale_in_insufficient_funds(self, portfolio_manager: PortfolioManager):
        # Open a position using most of the capital
        portfolio_manager.open_position(
            symbol="BTCUSDT",
            size=Decimal("0.19"),
            entry_price=Decimal("50000"),
            entry_time=T0,
        )
        with pytest.raises(ValueError, match="Insufficient funds"):
            portfolio_manager.scale_in(
                symbol="BTCUSDT",
                additional_size=Decimal("0.1"),
                price=Decimal("50000"),
                time=T1,
            )


# ── Fee Accounting ───────────────────────────────────────────────────────────


class TestFees:
    """Verify fees are deducted correctly."""

    def test_buy_fee_deducted(self, portfolio_manager: PortfolioManager):
        cost = Decimal("0.1") * Decimal("50000")  # 5000
        fee = _fee(cost)  # 5

        portfolio_manager.open_position(
            symbol="BTCUSDT",
            size=Decimal("0.1"),
            entry_price=Decimal("50000"),
            entry_time=T0,
        )
        assert portfolio_manager.portfolio.usdt_balance == Decimal("10000") - cost - fee

    def test_sell_fee_deducted(self, portfolio_manager: PortfolioManager):
        portfolio_manager.open_position(
            symbol="BTCUSDT",
            size=Decimal("0.1"),
            entry_price=Decimal("50000"),
            entry_time=T0,
        )
        balance_before_sell = portfolio_manager.portfolio.usdt_balance

        portfolio_manager.close_position(
            symbol="BTCUSDT",
            exit_price=Decimal("50000"),  # flat, no price change
            exit_time=T1,
            exit_reason=ExitReason.MANUAL,
        )

        proceeds = Decimal("0.1") * Decimal("50000")
        sell_fee = _fee(proceeds)
        expected_balance = balance_before_sell + proceeds - sell_fee
        assert portfolio_manager.portfolio.usdt_balance == expected_balance

    def test_round_trip_fees_cause_loss(self, portfolio_manager: PortfolioManager):
        """Buy and sell at same price → slight loss due to fees."""
        portfolio_manager.open_position(
            symbol="BTCUSDT",
            size=Decimal("0.1"),
            entry_price=Decimal("50000"),
            entry_time=T0,
        )
        trade = portfolio_manager.close_position(
            symbol="BTCUSDT",
            exit_price=Decimal("50000"),
            exit_time=T1,
            exit_reason=ExitReason.MANUAL,
        )
        # PnL should be negative (two 0.1 % fees)
        assert trade.pnl_usdt < 0
        assert portfolio_manager.portfolio.usdt_balance < Decimal("10000")


# ── Stop Updates ─────────────────────────────────────────────────────────────


class TestUpdateStops:
    """Stop-price mutations."""

    def test_update_trailing_stop(self, portfolio_manager: PortfolioManager):
        portfolio_manager.open_position(
            symbol="BTCUSDT",
            size=Decimal("0.1"),
            entry_price=Decimal("50000"),
            entry_time=T0,
        )
        updated = portfolio_manager.update_stops(
            symbol="BTCUSDT",
            highest_close=Decimal("52000"),
            trailing_stop_price=Decimal("51000"),
        )
        assert updated.highest_close == Decimal("52000")
        assert updated.trailing_stop_price == Decimal("51000")
        # hard_stop unchanged
        assert updated.hard_stop_price == Decimal("0")

    def test_update_hard_stop(self, portfolio_manager: PortfolioManager):
        portfolio_manager.open_position(
            symbol="BTCUSDT",
            size=Decimal("0.1"),
            entry_price=Decimal("50000"),
            entry_time=T0,
            hard_stop_price=Decimal("48500"),
        )
        updated = portfolio_manager.update_stops(
            symbol="BTCUSDT",
            hard_stop_price=Decimal("49000"),
        )
        assert updated.hard_stop_price == Decimal("49000")

    def test_update_stops_no_position(self, portfolio_manager: PortfolioManager):
        with pytest.raises(ValueError, match="No position"):
            portfolio_manager.update_stops(
                symbol="BTCUSDT",
                trailing_stop_price=Decimal("49000"),
            )


# ── Snapshots ────────────────────────────────────────────────────────────────


class TestSnapshots:
    """Equity-curve snapshots."""

    def test_take_snapshot_no_positions(self, portfolio_manager: PortfolioManager):
        snap = portfolio_manager.take_snapshot(T0, {})
        assert snap.usdt_balance == Decimal("10000")
        assert snap.positions_value == Decimal("0")
        assert snap.total_equity == Decimal("10000")
        assert snap.open_positions_count == 0

        assert len(portfolio_manager.portfolio.equity_curve) == 1

    def test_take_snapshot_with_position(self, portfolio_manager: PortfolioManager):
        portfolio_manager.open_position(
            symbol="BTCUSDT",
            size=Decimal("0.1"),
            entry_price=Decimal("50000"),
            entry_time=T0,
        )
        snap = portfolio_manager.take_snapshot(
            T1,
            {"BTCUSDT": Decimal("52000")},
        )
        assert snap.positions_value == Decimal("0.1") * Decimal("52000")
        assert snap.open_positions_count == 1
        expected_equity = portfolio_manager.portfolio.usdt_balance + snap.positions_value
        assert snap.total_equity == expected_equity

    def test_multiple_snapshots(self, portfolio_manager: PortfolioManager):
        portfolio_manager.take_snapshot(T0, {})
        portfolio_manager.take_snapshot(T1, {})
        portfolio_manager.take_snapshot(T2, {})
        assert len(portfolio_manager.portfolio.equity_curve) == 3


# ── Lookup Helpers ───────────────────────────────────────────────────────────


class TestLookups:
    """has_position / get_position."""

    def test_has_position_false(self, portfolio_manager: PortfolioManager):
        assert not portfolio_manager.has_position("BTCUSDT")

    def test_has_position_true(self, portfolio_manager: PortfolioManager):
        portfolio_manager.open_position(
            symbol="BTCUSDT",
            size=Decimal("0.01"),
            entry_price=Decimal("50000"),
            entry_time=T0,
        )
        assert portfolio_manager.has_position("BTCUSDT")

    def test_get_position_none(self, portfolio_manager: PortfolioManager):
        assert portfolio_manager.get_position("BTCUSDT") is None

    def test_get_position_returns_position(self, portfolio_manager: PortfolioManager):
        portfolio_manager.open_position(
            symbol="BTCUSDT",
            size=Decimal("0.01"),
            entry_price=Decimal("50000"),
            entry_time=T0,
        )
        pos = portfolio_manager.get_position("BTCUSDT")
        assert pos is not None
        assert pos.symbol == "BTCUSDT"


# ── Multi-symbol ─────────────────────────────────────────────────────────────


class TestMultiSymbol:
    """Holding positions in multiple symbols simultaneously."""

    def test_two_symbols(self, portfolio_manager: PortfolioManager):
        portfolio_manager.open_position(
            symbol="BTCUSDT",
            size=Decimal("0.05"),
            entry_price=Decimal("50000"),
            entry_time=T0,
        )
        portfolio_manager.open_position(
            symbol="ETHUSDT",
            size=Decimal("1"),
            entry_price=Decimal("3000"),
            entry_time=T0,
        )
        assert portfolio_manager.has_position("BTCUSDT")
        assert portfolio_manager.has_position("ETHUSDT")
        assert portfolio_manager.portfolio.open_trades_count == 2

    def test_close_one_keeps_other(self, portfolio_manager: PortfolioManager):
        portfolio_manager.open_position(
            symbol="BTCUSDT",
            size=Decimal("0.05"),
            entry_price=Decimal("50000"),
            entry_time=T0,
        )
        portfolio_manager.open_position(
            symbol="ETHUSDT",
            size=Decimal("1"),
            entry_price=Decimal("3000"),
            entry_time=T0,
        )
        portfolio_manager.close_position(
            symbol="BTCUSDT",
            exit_price=Decimal("51000"),
            exit_time=T1,
            exit_reason=ExitReason.TRAILING_STOP,
        )
        assert not portfolio_manager.has_position("BTCUSDT")
        assert portfolio_manager.has_position("ETHUSDT")
        assert portfolio_manager.portfolio.open_trades_count == 1

    def test_snapshot_with_two_symbols(self, portfolio_manager: PortfolioManager):
        portfolio_manager.open_position(
            symbol="BTCUSDT",
            size=Decimal("0.05"),
            entry_price=Decimal("50000"),
            entry_time=T0,
        )
        portfolio_manager.open_position(
            symbol="ETHUSDT",
            size=Decimal("1"),
            entry_price=Decimal("3000"),
            entry_time=T0,
        )
        snap = portfolio_manager.take_snapshot(
            T1,
            {"BTCUSDT": Decimal("52000"), "ETHUSDT": Decimal("3100")},
        )
        expected = Decimal("0.05") * Decimal("52000") + Decimal("1") * Decimal("3100")
        assert snap.positions_value == expected
        assert snap.open_positions_count == 2


# ── PnL Tracking ─────────────────────────────────────────────────────────────


class TestPnlTracking:
    """Cumulative total_pnl on the portfolio."""

    def test_total_pnl_after_winning_trade(self, portfolio_manager: PortfolioManager):
        portfolio_manager.open_position(
            symbol="BTCUSDT",
            size=Decimal("0.1"),
            entry_price=Decimal("50000"),
            entry_time=T0,
        )
        trade = portfolio_manager.close_position(
            symbol="BTCUSDT",
            exit_price=Decimal("55000"),
            exit_time=T1,
            exit_reason=ExitReason.TRAILING_STOP,
        )
        assert portfolio_manager.portfolio.total_pnl == trade.pnl_usdt
        assert portfolio_manager.portfolio.total_pnl > 0

    def test_total_pnl_accumulates(self, portfolio_manager: PortfolioManager):
        # Trade 1
        portfolio_manager.open_position(
            symbol="BTCUSDT",
            size=Decimal("0.05"),
            entry_price=Decimal("50000"),
            entry_time=T0,
        )
        t1 = portfolio_manager.close_position(
            symbol="BTCUSDT",
            exit_price=Decimal("52000"),
            exit_time=T1,
            exit_reason=ExitReason.TRAILING_STOP,
        )
        # Trade 2
        portfolio_manager.open_position(
            symbol="ETHUSDT",
            size=Decimal("1"),
            entry_price=Decimal("3000"),
            entry_time=T1,
        )
        t2 = portfolio_manager.close_position(
            symbol="ETHUSDT",
            exit_price=Decimal("2900"),
            exit_time=T2,
            exit_reason=ExitReason.HARD_STOP,
        )
        assert portfolio_manager.portfolio.total_pnl == t1.pnl_usdt + t2.pnl_usdt


# ── Event publishing ─────────────────────────────────────────────────────────


class TestPortfolioEvents:
    """Verify POSITION_OPENED/UPDATED, TRADE_CLOSED, BALANCE/EQUITY events (task 2.18)."""

    @patch("core.portfolio.get_publisher")
    def test_open_publishes_position_opened_and_balance(self, mock_get_pub):
        pub = MagicMock()
        mock_get_pub.return_value = pub

        pm = PortfolioManager(initial_capital=Decimal("10000"))
        pm.open_position(
            symbol="BTCUSDT",
            size=Decimal("0.1"),
            entry_price=Decimal("50000"),
            entry_time=T0,
        )

        event_types = [c[0][1] for c in pub.publish_model.call_args_list]
        assert EventType.POSITION_OPENED in event_types

        # BALANCE_UPDATED via publish()
        balance_calls = [
            c for c in pub.publish.call_args_list
            if c[0][1] == EventType.BALANCE_UPDATED
        ]
        assert len(balance_calls) == 1
        assert balance_calls[0][0][2]["symbol"] == "BTCUSDT"

    @patch("core.portfolio.get_publisher")
    def test_open_position_opened_model(self, mock_get_pub):
        pub = MagicMock()
        mock_get_pub.return_value = pub

        pm = PortfolioManager(initial_capital=Decimal("10000"))
        pm.open_position(
            symbol="ETHUSDT",
            size=Decimal("1"),
            entry_price=Decimal("3000"),
            entry_time=T0,
        )

        model = pub.publish_model.call_args_list[0][0][2]
        assert isinstance(model, Position)
        assert model.symbol == "ETHUSDT"

    @patch("core.portfolio.get_publisher")
    def test_scale_in_publishes_position_updated_and_balance(self, mock_get_pub):
        pub = MagicMock()
        mock_get_pub.return_value = pub

        pm = PortfolioManager(initial_capital=Decimal("10000"))
        pm.open_position(
            symbol="BTCUSDT",
            size=Decimal("0.05"),
            entry_price=Decimal("50000"),
            entry_time=T0,
        )
        pub.reset_mock()

        pm.scale_in(
            symbol="BTCUSDT",
            additional_size=Decimal("0.02"),
            price=Decimal("49000"),
            time=T1,
        )

        model_events = [c[0][1] for c in pub.publish_model.call_args_list]
        assert EventType.POSITION_UPDATED in model_events

        balance_calls = [
            c for c in pub.publish.call_args_list
            if c[0][1] == EventType.BALANCE_UPDATED
        ]
        assert len(balance_calls) == 1

    @patch("core.portfolio.get_publisher")
    def test_close_publishes_trade_closed_and_balance(self, mock_get_pub):
        pub = MagicMock()
        mock_get_pub.return_value = pub

        pm = PortfolioManager(initial_capital=Decimal("10000"))
        pm.open_position(
            symbol="BTCUSDT",
            size=Decimal("0.1"),
            entry_price=Decimal("50000"),
            entry_time=T0,
        )
        pub.reset_mock()

        pm.close_position(
            symbol="BTCUSDT",
            exit_price=Decimal("52000"),
            exit_time=T1,
            exit_reason=ExitReason.EMA_CROSS,
        )

        model_events = [c[0][1] for c in pub.publish_model.call_args_list]
        assert EventType.TRADE_CLOSED in model_events

        # Full close → no POSITION_UPDATED (position removed)
        assert EventType.POSITION_UPDATED not in model_events

        balance_calls = [
            c for c in pub.publish.call_args_list
            if c[0][1] == EventType.BALANCE_UPDATED
        ]
        assert len(balance_calls) == 1

    @patch("core.portfolio.get_publisher")
    def test_close_trade_model(self, mock_get_pub):
        pub = MagicMock()
        mock_get_pub.return_value = pub

        pm = PortfolioManager(initial_capital=Decimal("10000"))
        pm.open_position(
            symbol="BTCUSDT",
            size=Decimal("0.1"),
            entry_price=Decimal("50000"),
            entry_time=T0,
        )
        pub.reset_mock()

        pm.close_position(
            symbol="BTCUSDT",
            exit_price=Decimal("52000"),
            exit_time=T1,
            exit_reason=ExitReason.TRAILING_STOP,
        )

        trade_model = pub.publish_model.call_args_list[0][0][2]
        assert isinstance(trade_model, Trade)
        assert trade_model.symbol == "BTCUSDT"

    @patch("core.portfolio.get_publisher")
    def test_partial_reduce_publishes_trade_and_position_updated(self, mock_get_pub):
        pub = MagicMock()
        mock_get_pub.return_value = pub

        pm = PortfolioManager(initial_capital=Decimal("10000"))
        pm.open_position(
            symbol="BTCUSDT",
            size=Decimal("0.1"),
            entry_price=Decimal("50000"),
            entry_time=T0,
        )
        pub.reset_mock()

        pm.reduce_position(
            symbol="BTCUSDT",
            sell_fraction=Decimal("0.5"),
            exit_price=Decimal("52000"),
            exit_time=T1,
            exit_reason=ExitReason.MOMENTUM_FADE,
        )

        model_events = [c[0][1] for c in pub.publish_model.call_args_list]
        assert EventType.TRADE_CLOSED in model_events
        assert EventType.POSITION_UPDATED in model_events

        balance_calls = [
            c for c in pub.publish.call_args_list
            if c[0][1] == EventType.BALANCE_UPDATED
        ]
        assert len(balance_calls) == 1

    @patch("core.portfolio.get_publisher")
    def test_update_stops_publishes_position_updated(self, mock_get_pub):
        pub = MagicMock()
        mock_get_pub.return_value = pub

        pm = PortfolioManager(initial_capital=Decimal("10000"))
        pm.open_position(
            symbol="BTCUSDT",
            size=Decimal("0.1"),
            entry_price=Decimal("50000"),
            entry_time=T0,
        )
        pub.reset_mock()

        pm.update_stops(
            symbol="BTCUSDT",
            trailing_stop_price=Decimal("49500"),
        )

        pub.publish_model.assert_called_once()
        args = pub.publish_model.call_args[0]
        assert args[0] == EventChannel.PORTFOLIO
        assert args[1] == EventType.POSITION_UPDATED
        assert isinstance(args[2], Position)

    @patch("core.portfolio.get_publisher")
    def test_update_stops_no_event_when_no_changes(self, mock_get_pub):
        pub = MagicMock()
        mock_get_pub.return_value = pub

        pm = PortfolioManager(initial_capital=Decimal("10000"))
        pm.open_position(
            symbol="BTCUSDT",
            size=Decimal("0.1"),
            entry_price=Decimal("50000"),
            entry_time=T0,
        )
        pub.reset_mock()

        pm.update_stops(symbol="BTCUSDT")  # all None → no updates dict

        pub.publish_model.assert_not_called()

    @patch("core.portfolio.get_publisher")
    def test_take_snapshot_publishes_equity_updated(self, mock_get_pub):
        pub = MagicMock()
        mock_get_pub.return_value = pub

        pm = PortfolioManager(initial_capital=Decimal("10000"))
        pub.reset_mock()

        pm.take_snapshot(T0, {"BTCUSDT": Decimal("50000")})

        pub.publish_model.assert_called_once()
        args = pub.publish_model.call_args[0]
        assert args[0] == EventChannel.PORTFOLIO
        assert args[1] == EventType.EQUITY_UPDATED
        assert isinstance(args[2], PortfolioSnapshot)

    @patch("core.portfolio.get_publisher")
    def test_no_events_on_error_path(self, mock_get_pub):
        """Duplicate open raises before any publishing."""
        pub = MagicMock()
        mock_get_pub.return_value = pub

        pm = PortfolioManager(initial_capital=Decimal("10000"))
        pm.open_position(
            symbol="BTCUSDT",
            size=Decimal("0.1"),
            entry_price=Decimal("50000"),
            entry_time=T0,
        )
        pub.reset_mock()

        with pytest.raises(ValueError):
            pm.open_position(
                symbol="BTCUSDT",
                size=Decimal("0.1"),
                entry_price=Decimal("50000"),
                entry_time=T1,
            )

        pub.publish_model.assert_not_called()
        pub.publish.assert_not_called()

    @patch("core.portfolio.get_publisher")
    def test_event_counts_full_cycle(self, mock_get_pub):
        """open → close: 2 publish_model + 2 publish (balance) calls."""
        pub = MagicMock()
        mock_get_pub.return_value = pub

        pm = PortfolioManager(initial_capital=Decimal("10000"))
        pm.open_position(
            symbol="BTCUSDT",
            size=Decimal("0.1"),
            entry_price=Decimal("50000"),
            entry_time=T0,
        )
        pm.close_position(
            symbol="BTCUSDT",
            exit_price=Decimal("52000"),
            exit_time=T1,
            exit_reason=ExitReason.EMA_CROSS,
        )

        # publish_model: POSITION_OPENED + TRADE_CLOSED
        assert pub.publish_model.call_count == 2
        # publish: BALANCE_UPDATED × 2 (open + close)
        assert pub.publish.call_count == 2
