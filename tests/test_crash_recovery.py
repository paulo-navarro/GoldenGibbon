"""
Unit tests for crash recovery (task 4.7).

Verifies that ``_recover_state`` correctly restores:
  - Strategy state (state machine, cooldown, counters)
  - Open positions
  - USDT balance from state_data
  - Kill-switch state
  - Recent trade history from TradeRecord
  - Equity curve from PortfolioSnapshot
  - Graceful degradation when DB has no records
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from core.models import ExitReason, StrategyState
from core.portfolio import PortfolioManager
from core.risk import RiskEngine
from core.tasks import _recover_state, clear_worker_state
from db import get_session
from db.models import (
    PortfolioSnapshot as PortfolioSnapshotORM,
    PositionRecord,
    StrategyStateRecord,
    TradeRecord,
)


# ── Helpers ─────────────────────────────────────────────────────────────────

STRATEGY = "smart_hodler"
SYMBOL = "BTCUSDT"
RUN_ID = "paper_smart_hodler_BTCUSDT_20260417T100000"
T0 = datetime(2026, 4, 17, 10, 0, tzinfo=timezone.utc)


def _make_pm(capital: str = "10000") -> PortfolioManager:
    return PortfolioManager(
        initial_capital=Decimal(capital),
        taker_fee=Decimal("0.001"),
    )


def _make_strategy():
    """Return a real SmartHodler instance for state recovery tests."""
    from core.strategies.smart_hodler import SmartHodler

    return SmartHodler({"cooldown_candles": 16})


def _make_risk_engine():
    """Return a real RiskEngine with default config."""
    return RiskEngine(
        STRATEGY,
        {"timeframe_primary": "15m", "timeframe_confirmation": "1h"},
        {},
    )


@pytest.fixture(autouse=True)
def _clear_state():
    clear_worker_state()
    yield
    clear_worker_state()


def _seed_state_record(
    *,
    state: str = "flat",
    state_data: dict | None = None,
    buy_candles: int = 0,
) -> None:
    data = state_data or {
        "run_id": RUN_ID,
        "usdt_balance": "8500",
        "equity": "8500",
        "total_pnl": "-1500",
        "cooldown_remaining": 3,
    }
    with get_session() as session:
        session.add(
            StrategyStateRecord(
                symbol=SYMBOL,
                strategy=STRATEGY,
                state=state,
                consecutive_buy_candles=buy_candles,
                state_data=data,
            )
        )


def _seed_position(
    *,
    size: str = "0.01",
    entry_price: str = "50000",
) -> None:
    with get_session() as session:
        session.add(
            PositionRecord(
                symbol=SYMBOL,
                strategy=STRATEGY,
                side="long",
                size=Decimal(size),
                entry_price=Decimal(entry_price),
                entry_time=T0,
                highest_close=Decimal(entry_price),
                trailing_stop_price=Decimal(entry_price) * Decimal("0.95"),
                hard_stop_price=Decimal(entry_price) * Decimal("0.97"),
                scale_in_count=0,
                buy_signal_candles=0,
            )
        )


def _seed_trades(count: int = 5) -> None:
    base = datetime(2026, 4, 17, 0, 0, tzinfo=timezone.utc)
    with get_session() as session:
        for i in range(count):
            session.add(
                TradeRecord(
                    run_id=RUN_ID,
                    symbol=SYMBOL,
                    strategy=STRATEGY,
                    side="long",
                    entry_price=Decimal("50000"),
                    exit_price=Decimal("51000") + i,
                    size=Decimal("0.01"),
                    entry_time=base + timedelta(minutes=i * 15),
                    exit_time=base + timedelta(minutes=i * 15 + 60),
                    pnl_usdt=Decimal("10") + i,
                    pnl_percent=Decimal("2.00"),
                    duration_minutes=60,
                    exit_reason="trailing_stop",
                )
            )


def _seed_snapshots(count: int = 5) -> None:
    base = datetime(2026, 4, 17, 0, 0, tzinfo=timezone.utc)
    with get_session() as session:
        for i in range(count):
            session.add(
                PortfolioSnapshotORM(
                    run_id=RUN_ID,
                    timestamp=base + timedelta(minutes=i * 15),
                    usdt_balance=Decimal("10000") - i * 10,
                    positions_value=Decimal("0"),
                    total_equity=Decimal("10000") - i * 10,
                    daily_pnl=Decimal("0"),
                    total_pnl=Decimal(str(-i * 10)),
                    open_positions_count=0,
                )
            )


# ── No state → fresh start ─────────────────────────────────────────────────


class TestNoStateRecovery:
    """When DB has no persisted state, recovery returns None."""

    def test_returns_none_when_no_records(self):
        pm = _make_pm()
        result = _recover_state(STRATEGY, SYMBOL, _make_strategy(), pm, _make_risk_engine())
        assert result is None

    def test_portfolio_stays_empty(self):
        pm = _make_pm()
        _recover_state(STRATEGY, SYMBOL, _make_strategy(), pm, _make_risk_engine())
        assert pm.portfolio.trade_history == []
        assert pm.portfolio.equity_curve == []
        assert len(pm.portfolio.positions) == 0


# ── Strategy state recovery ────────────────────────────────────────────────


class TestStrategyStateRecovery:
    """Strategy state, cooldown, and counters restored from DB."""

    def test_restores_state_and_cooldown(self):
        _seed_state_record(state="cooldown")

        strategy = _make_strategy()
        pm = _make_pm()
        result = _recover_state(STRATEGY, SYMBOL, strategy, pm, _make_risk_engine())

        assert result == RUN_ID
        assert strategy._state == StrategyState.COOLDOWN
        assert strategy._cooldown_remaining == 3

    def test_restores_buy_signal_candles(self):
        _seed_state_record(buy_candles=7)

        risk_engine = _make_risk_engine()
        _recover_state(STRATEGY, SYMBOL, _make_strategy(), _make_pm(), risk_engine)

        assert risk_engine.get_buy_signal_candles(SYMBOL) == 7

    def test_restores_consecutive_below_ema200(self):
        _seed_state_record(
            state_data={
                "run_id": RUN_ID,
                "usdt_balance": "10000",
                "cooldown_remaining": 0,
                "consecutive_below_ema200": 12,
            },
        )

        strategy = _make_strategy()
        _recover_state(STRATEGY, SYMBOL, strategy, _make_pm(), _make_risk_engine())

        assert strategy._consecutive_below_ema200 == 12

    def test_restores_usdt_balance(self):
        _seed_state_record(state="position")
        _seed_position()

        pm = _make_pm("10000")
        _recover_state(STRATEGY, SYMBOL, _make_strategy(), pm, _make_risk_engine())

        assert pm.portfolio.usdt_balance == Decimal("8500")


# ── Position recovery ──────────────────────────────────────────────────────


class TestPositionRecovery:
    """Open positions restored from PositionRecord."""

    def test_restores_position(self):
        _seed_state_record(state="position")
        _seed_position(size="0.05", entry_price="48000")

        pm = _make_pm()
        _recover_state(STRATEGY, SYMBOL, _make_strategy(), pm, _make_risk_engine())

        assert pm.has_position(SYMBOL)
        pos = pm.get_position(SYMBOL)
        assert pos.size == Decimal("0.05")
        assert pos.entry_price == Decimal("48000")

    def test_no_position_when_flat(self):
        _seed_state_record(state="flat")

        pm = _make_pm()
        _recover_state(STRATEGY, SYMBOL, _make_strategy(), pm, _make_risk_engine())

        assert not pm.has_position(SYMBOL)


# ── Trade history recovery (task 4.7) ──────────────────────────────────────


class TestTradeHistoryRecovery:
    """Recent trades loaded from TradeRecord into portfolio.trade_history."""

    def test_restores_trades(self):
        _seed_state_record()
        _seed_trades(5)

        pm = _make_pm()
        _recover_state(STRATEGY, SYMBOL, _make_strategy(), pm, _make_risk_engine())

        assert len(pm.portfolio.trade_history) == 5

    def test_trades_in_chronological_order(self):
        _seed_state_record()
        _seed_trades(3)

        pm = _make_pm()
        _recover_state(STRATEGY, SYMBOL, _make_strategy(), pm, _make_risk_engine())

        times = [t.exit_time for t in pm.portfolio.trade_history]
        assert times == sorted(times)

    def test_no_trades_when_no_run_id(self):
        """If no state record → no run_id → no trades loaded."""
        _seed_trades(3)  # trades exist but no state record

        pm = _make_pm()
        _recover_state(STRATEGY, SYMBOL, _make_strategy(), pm, _make_risk_engine())

        assert pm.portfolio.trade_history == []

    def test_respects_max_memory_limit(self):
        _seed_state_record()
        _seed_trades(80)  # more than _MAX_MEMORY_TRADES (50)

        pm = _make_pm()
        _recover_state(STRATEGY, SYMBOL, _make_strategy(), pm, _make_risk_engine())

        assert len(pm.portfolio.trade_history) <= 50

    def test_trade_fields_converted(self):
        _seed_state_record()
        _seed_trades(1)

        pm = _make_pm()
        _recover_state(STRATEGY, SYMBOL, _make_strategy(), pm, _make_risk_engine())

        trade = pm.portfolio.trade_history[0]
        assert trade.symbol == SYMBOL
        assert trade.strategy == STRATEGY
        assert trade.exit_reason == ExitReason.TRAILING_STOP
        assert isinstance(trade.pnl_usdt, Decimal)


# ── Equity curve recovery (task 4.7) ──────────────────────────────────────


class TestEquityCurveRecovery:
    """Equity curve loaded from PortfolioSnapshot into portfolio.equity_curve."""

    def test_restores_snapshots(self):
        _seed_state_record()
        _seed_snapshots(5)

        pm = _make_pm()
        _recover_state(STRATEGY, SYMBOL, _make_strategy(), pm, _make_risk_engine())

        assert len(pm.portfolio.equity_curve) == 5

    def test_snapshots_in_chronological_order(self):
        _seed_state_record()
        _seed_snapshots(3)

        pm = _make_pm()
        _recover_state(STRATEGY, SYMBOL, _make_strategy(), pm, _make_risk_engine())

        times = [s.timestamp for s in pm.portfolio.equity_curve]
        assert times == sorted(times)

    def test_no_snapshots_when_no_run_id(self):
        _seed_snapshots(3)

        pm = _make_pm()
        _recover_state(STRATEGY, SYMBOL, _make_strategy(), pm, _make_risk_engine())

        assert pm.portfolio.equity_curve == []

    def test_respects_max_memory_limit(self):
        _seed_state_record()
        _seed_snapshots(150)  # more than _MAX_MEMORY_SNAPSHOTS (100)

        pm = _make_pm()
        _recover_state(STRATEGY, SYMBOL, _make_strategy(), pm, _make_risk_engine())

        assert len(pm.portfolio.equity_curve) <= 100

    def test_snapshot_fields_converted(self):
        _seed_state_record()
        _seed_snapshots(1)

        pm = _make_pm()
        _recover_state(STRATEGY, SYMBOL, _make_strategy(), pm, _make_risk_engine())

        snap = pm.portfolio.equity_curve[0]
        assert isinstance(snap.total_equity, Decimal)
        assert isinstance(snap.usdt_balance, Decimal)
        assert snap.timestamp.tzinfo is not None


# ── Kill-switch state recovery ─────────────────────────────────────────────


class TestKillSwitchRecovery:
    """Kill-switch state restored from state_data JSONB."""

    @patch("core.risk.kill_switch.get_publisher")
    def test_restores_triggered_state(self, mock_pub):
        mock_pub.return_value = MagicMock()
        _seed_state_record(
            state_data={
                "run_id": RUN_ID,
                "usdt_balance": "8000",
                "cooldown_remaining": 0,
                "kill_switch_triggered": True,
                "kill_switch_reason": "Global drawdown 16.0% >= 15.0%",
                "kill_switch_peak_equity": "10000",
            },
        )

        from core.risk.kill_switch import KillSwitch

        ks = KillSwitch(max_drawdown_pct=0.15, max_daily_loss_pct=0.10)
        _recover_state(
            STRATEGY, SYMBOL, _make_strategy(), _make_pm(), _make_risk_engine(),
            kill_switch=ks,
        )

        assert ks.is_triggered is True
        assert "Global drawdown" in ks.trigger_reason
        assert ks.peak_equity == Decimal("10000")

    @patch("core.risk.kill_switch.get_publisher")
    def test_restores_non_triggered_state(self, mock_pub):
        mock_pub.return_value = MagicMock()
        _seed_state_record(
            state_data={
                "run_id": RUN_ID,
                "usdt_balance": "10000",
                "cooldown_remaining": 0,
                "kill_switch_triggered": False,
                "kill_switch_peak_equity": "10500",
            },
        )

        from core.risk.kill_switch import KillSwitch

        ks = KillSwitch(max_drawdown_pct=0.15, max_daily_loss_pct=0.10)
        _recover_state(
            STRATEGY, SYMBOL, _make_strategy(), _make_pm(), _make_risk_engine(),
            kill_switch=ks,
        )

        assert ks.is_triggered is False
        assert ks.peak_equity == Decimal("10500")


# ── Full recovery scenario ─────────────────────────────────────────────────


class TestFullRecovery:
    """End-to-end: all components recovered in a single call."""

    def test_full_state_restored(self):
        _seed_state_record(state="position")
        _seed_position(size="0.02", entry_price="55000")
        _seed_trades(3)
        _seed_snapshots(4)

        strategy = _make_strategy()
        pm = _make_pm()
        risk_engine = _make_risk_engine()
        run_id = _recover_state(STRATEGY, SYMBOL, strategy, pm, risk_engine)

        # Run ID recovered
        assert run_id == RUN_ID

        # Strategy state
        assert strategy._state == StrategyState.POSITION
        assert strategy._cooldown_remaining == 3

        # Position
        assert pm.has_position(SYMBOL)
        assert pm.get_position(SYMBOL).size == Decimal("0.02")

        # Balance
        assert pm.portfolio.usdt_balance == Decimal("8500")

        # Trade history
        assert len(pm.portfolio.trade_history) == 3

        # Equity curve
        assert len(pm.portfolio.equity_curve) == 4


# ── Error handling ─────────────────────────────────────────────────────────


class TestErrorHandling:
    """Recovery failures are swallowed — system starts fresh."""

    @patch("db.get_session")
    def test_db_error_returns_none(self, mock_session):
        mock_session.side_effect = RuntimeError("DB gone")

        pm = _make_pm()
        result = _recover_state(STRATEGY, SYMBOL, _make_strategy(), pm, _make_risk_engine())

        assert result is None
        assert pm.portfolio.trade_history == []
