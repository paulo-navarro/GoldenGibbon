"""
Unit tests for backtest reporting (task 1.21).

Covers:

    Console output:
        - Summary panel contains strategy / symbol / dates / capital
        - Metrics table: total return, drawdown, win rate, Sharpe, etc.
        - Buy & Hold line appears when benchmark data present
        - Trade detail table renders correct rows
        - No trades → no trade table
        - max_trades limits rendered rows

    Database persistence:
        - save_metrics inserts BacktestResult ORM row
        - save_trades inserts TradeRecord rows with run_id
        - save_equity_curve inserts PortfolioSnapshot rows with run_id
        - Flags false → no inserts for that category
        - config_snapshot stored on ORM row

    Orchestrator (report_backtest):
        - output_format "console" → console only
        - output_format "database" → db only
        - output_format "both" → both called
"""

from datetime import datetime, timedelta
from decimal import Decimal
from io import StringIO
from typing import List
from unittest.mock import MagicMock, patch, call

import pytest
from rich.console import Console

from core.backtest.reporting import (
    format_console_report,
    persist_to_database,
    report_backtest,
)
from core.models import (
    BacktestMetrics,
    BacktestResult,
    ExitReason,
    Portfolio,
    PortfolioSnapshot,
    Trade,
)


# ── Helpers ──────────────────────────────────────────────────────────────────

T0 = datetime(2025, 1, 1)


def _metrics(**overrides) -> BacktestMetrics:
    defaults = dict(
        run_id="test-run-001",
        strategy="smart_hodler",
        symbol="BTCUSDT",
        start_date=T0,
        end_date=T0 + timedelta(days=30),
        initial_capital=Decimal("10000"),
        final_capital=Decimal("11000"),
        total_return=Decimal("10.0000"),
        total_trades=5,
        winning_trades=3,
        losing_trades=2,
        win_rate=Decimal("60.00"),
        avg_win=Decimal("5.0000"),
        avg_loss=Decimal("-2.0000"),
        max_drawdown=Decimal("3.5000"),
        sharpe_ratio=Decimal("1.5000"),
        profit_factor=Decimal("2.5000"),
        avg_trade_duration_minutes=120,
        max_consecutive_wins=2,
        max_consecutive_losses=1,
        buy_hold_return=Decimal("8.0000"),
        buy_hold_vs_strategy=Decimal("2.0000"),
    )
    defaults.update(overrides)
    return BacktestMetrics(**defaults)


def _trade(
    pnl_usdt: str = "100",
    pnl_pct: str = "1.0",
    idx: int = 0,
    exit_reason: ExitReason = ExitReason.EMA_CROSS,
) -> Trade:
    return Trade(
        symbol="BTCUSDT",
        strategy="smart_hodler",
        entry_price=Decimal("50000"),
        exit_price=Decimal("50000") + Decimal(pnl_usdt),
        size=Decimal("1"),
        entry_time=T0 + timedelta(hours=idx),
        exit_time=T0 + timedelta(hours=idx, minutes=60),
        pnl_usdt=Decimal(pnl_usdt),
        pnl_percent=Decimal(pnl_pct),
        duration_minutes=60,
        exit_reason=exit_reason,
    )


def _snap(minute: int, equity: str) -> PortfolioSnapshot:
    eq = Decimal(equity)
    return PortfolioSnapshot(
        timestamp=T0 + timedelta(minutes=minute * 15),
        usdt_balance=eq,
        positions_value=Decimal("0"),
        total_equity=eq,
        total_pnl=eq - Decimal("10000"),
    )


def _result(
    trades: List[Trade] | None = None,
    equity_curve: List[PortfolioSnapshot] | None = None,
    benchmark_first: str | None = None,
    benchmark_last: str | None = None,
) -> BacktestResult:
    tl = trades or []
    ec = equity_curve or []
    portfolio = Portfolio(
        usdt_balance=Decimal("11000"),
        equity=Decimal("11000"),
        trade_history=tl,
        equity_curve=ec,
    )
    return BacktestResult(
        strategy_name="smart_hodler",
        symbol="BTCUSDT",
        timeframe="15m",
        start_time=T0,
        end_time=T0 + timedelta(days=30),
        candles_processed=100,
        portfolio=portfolio,
        trades=tl,
        equity_curve=ec,
        benchmark_first_price=Decimal(benchmark_first) if benchmark_first else None,
        benchmark_last_price=Decimal(benchmark_last) if benchmark_last else None,
    )


def _capture_console() -> tuple[Console, StringIO]:
    """Create a Console that writes to a StringIO buffer."""
    buf = StringIO()
    con = Console(file=buf, force_terminal=True, width=120)
    return con, buf


# ── Console tests ────────────────────────────────────────────────────────────


class TestConsoleReport:
    """format_console_report produces expected rich output."""

    def test_summary_contains_strategy(self):
        con, buf = _capture_console()
        format_console_report(_metrics(), console=con)
        text = buf.getvalue()
        assert "smart_hodler" in text

    def test_summary_contains_symbol(self):
        con, buf = _capture_console()
        format_console_report(_metrics(), console=con)
        text = buf.getvalue()
        assert "BTCUSDT" in text

    def test_summary_contains_dates(self):
        con, buf = _capture_console()
        format_console_report(_metrics(), console=con)
        text = buf.getvalue()
        assert "2025-01-01" in text
        assert "2025-01-31" in text

    def test_summary_contains_capital(self):
        con, buf = _capture_console()
        format_console_report(_metrics(), console=con)
        text = buf.getvalue()
        assert "10,000.00" in text
        assert "11,000.00" in text

    def test_total_return_displayed(self):
        con, buf = _capture_console()
        format_console_report(_metrics(), console=con)
        text = buf.getvalue()
        assert "10.00%" in text

    def test_max_drawdown_displayed(self):
        con, buf = _capture_console()
        format_console_report(_metrics(), console=con)
        text = buf.getvalue()
        assert "3.50%" in text

    def test_sharpe_ratio_displayed(self):
        con, buf = _capture_console()
        format_console_report(_metrics(), console=con)
        text = buf.getvalue()
        assert "1.5000" in text

    def test_win_rate_displayed(self):
        con, buf = _capture_console()
        format_console_report(_metrics(), console=con)
        text = buf.getvalue()
        assert "60.00%" in text

    def test_profit_factor_displayed(self):
        con, buf = _capture_console()
        format_console_report(_metrics(), console=con)
        text = buf.getvalue()
        assert "2.5000" in text

    def test_buy_hold_displayed(self):
        con, buf = _capture_console()
        format_console_report(_metrics(), console=con)
        text = buf.getvalue()
        assert "Buy & Hold" in text
        assert "8.00%" in text

    def test_buy_hold_vs_strategy_displayed(self):
        con, buf = _capture_console()
        format_console_report(_metrics(), console=con)
        text = buf.getvalue()
        assert "Strategy vs B&H" in text
        assert "+2.00%" in text

    def test_no_buy_hold_when_none(self):
        con, buf = _capture_console()
        format_console_report(
            _metrics(buy_hold_return=None, buy_hold_vs_strategy=None),
            console=con,
        )
        text = buf.getvalue()
        assert "Buy & Hold" not in text

    def test_trade_table_displayed(self):
        trades = [_trade("100", "1.0", idx=i) for i in range(3)]
        con, buf = _capture_console()
        format_console_report(_metrics(), trades=trades, console=con)
        text = buf.getvalue()
        assert "Trades" in text
        assert "showing 3 of 3" in text

    def test_trade_table_max_trades_limits_rows(self):
        trades = [_trade("100", "1.0", idx=i) for i in range(15)]
        con, buf = _capture_console()
        format_console_report(_metrics(), trades=trades, console=con, max_trades=5)
        text = buf.getvalue()
        assert "showing 5 of 15" in text

    def test_no_trade_table_when_no_trades(self):
        con, buf = _capture_console()
        format_console_report(_metrics(), trades=[], console=con)
        text = buf.getvalue()
        # Should NOT contain a trade detail table header
        assert "showing" not in text

    def test_losing_trade_pnl_in_output(self):
        trades = [_trade("-200", "-2.0")]
        con, buf = _capture_console()
        format_console_report(_metrics(), trades=trades, console=con)
        text = buf.getvalue()
        assert "-200" in text

    def test_consecutive_wins_loses_displayed(self):
        con, buf = _capture_console()
        format_console_report(_metrics(max_consecutive_wins=5, max_consecutive_losses=3), console=con)
        text = buf.getvalue()
        assert "5" in text
        assert "3" in text

    def test_avg_duration_displayed(self):
        con, buf = _capture_console()
        format_console_report(_metrics(avg_trade_duration_minutes=90), console=con)
        text = buf.getvalue()
        assert "90 min" in text

    def test_avg_duration_none(self):
        con, buf = _capture_console()
        format_console_report(_metrics(avg_trade_duration_minutes=None), console=con)
        text = buf.getvalue()
        assert "—" in text


# ── Database persistence tests ───────────────────────────────────────────────


class TestPersistToDatabase:
    """persist_to_database inserts correct ORM objects."""

    def _make_mock_session(self):
        session = MagicMock()
        session.add = MagicMock()
        session.add_all = MagicMock()
        session.flush = MagicMock()
        return session

    def test_save_metrics_inserts_backtest_result(self):
        session = self._make_mock_session()
        metrics = _metrics()
        result = _result()
        persist_to_database(
            metrics=metrics,
            result=result,
            backtest_config={"save_metrics": True, "save_trades": False, "save_equity_curve": False},
            session=session,
        )
        session.add.assert_called_once()
        orm_obj = session.add.call_args[0][0]
        from db.models import BacktestResult as ORMBacktestResult
        assert isinstance(orm_obj, ORMBacktestResult)
        assert orm_obj.run_id == "test-run-001"

    def test_save_metrics_false_skips_insert(self):
        session = self._make_mock_session()
        persist_to_database(
            metrics=_metrics(),
            result=_result(),
            backtest_config={"save_metrics": False, "save_trades": False, "save_equity_curve": False},
            session=session,
        )
        session.add.assert_not_called()

    def test_save_trades_inserts_trade_records_with_run_id(self):
        session = self._make_mock_session()
        trades = [_trade("100", "1.0", idx=i) for i in range(3)]
        persist_to_database(
            metrics=_metrics(),
            result=_result(trades=trades),
            backtest_config={"save_metrics": False, "save_trades": True, "save_equity_curve": False},
            session=session,
        )
        session.add_all.assert_called_once()
        records = session.add_all.call_args[0][0]
        assert len(records) == 3
        from db.models import TradeRecord
        assert all(isinstance(r, TradeRecord) for r in records)
        assert all(r.run_id == "test-run-001" for r in records)

    def test_save_trades_false_skips(self):
        session = self._make_mock_session()
        trades = [_trade("100", "1.0")]
        persist_to_database(
            metrics=_metrics(),
            result=_result(trades=trades),
            backtest_config={"save_metrics": False, "save_trades": False, "save_equity_curve": False},
            session=session,
        )
        session.add_all.assert_not_called()

    def test_save_equity_curve_inserts_snapshots_with_run_id(self):
        session = self._make_mock_session()
        curve = [_snap(i, str(10000 + i * 10)) for i in range(5)]
        persist_to_database(
            metrics=_metrics(),
            result=_result(equity_curve=curve),
            backtest_config={"save_metrics": False, "save_trades": False, "save_equity_curve": True},
            session=session,
        )
        session.add_all.assert_called_once()
        records = session.add_all.call_args[0][0]
        assert len(records) == 5
        from db.models import PortfolioSnapshot as ORMSnapshot
        assert all(isinstance(r, ORMSnapshot) for r in records)
        assert all(r.run_id == "test-run-001" for r in records)

    def test_save_equity_curve_false_skips(self):
        session = self._make_mock_session()
        curve = [_snap(0, "10000")]
        persist_to_database(
            metrics=_metrics(),
            result=_result(equity_curve=curve),
            backtest_config={"save_metrics": False, "save_trades": False, "save_equity_curve": False},
            session=session,
        )
        session.add_all.assert_not_called()

    def test_config_snapshot_stored_on_orm(self):
        session = self._make_mock_session()
        snapshot_cfg = {"strategy": "smart_hodler", "risk": {"max_drawdown": 0.03}}
        persist_to_database(
            metrics=_metrics(),
            result=_result(),
            backtest_config={"save_metrics": True, "save_trades": False, "save_equity_curve": False},
            config_snapshot=snapshot_cfg,
            session=session,
        )
        orm_obj = session.add.call_args[0][0]
        assert orm_obj.config_snapshot == snapshot_cfg

    def test_buy_hold_columns_on_orm(self):
        session = self._make_mock_session()
        persist_to_database(
            metrics=_metrics(buy_hold_return=Decimal("8.0000"), buy_hold_vs_strategy=Decimal("2.0000")),
            result=_result(),
            backtest_config={"save_metrics": True, "save_trades": False, "save_equity_curve": False},
            session=session,
        )
        orm_obj = session.add.call_args[0][0]
        assert orm_obj.buy_hold_return == Decimal("8.0000")
        assert orm_obj.buy_hold_vs_strategy == Decimal("2.0000")

    def test_all_saves_together(self):
        session = self._make_mock_session()
        trades = [_trade("100", "1.0")]
        curve = [_snap(0, "10000"), _snap(1, "10100")]
        persist_to_database(
            metrics=_metrics(),
            result=_result(trades=trades, equity_curve=curve),
            backtest_config={"save_metrics": True, "save_trades": True, "save_equity_curve": True},
            session=session,
        )
        # add called once for metrics
        session.add.assert_called_once()
        # add_all called twice: once for trades, once for snapshots
        assert session.add_all.call_count == 2

    def test_defaults_to_save_all(self):
        """When config keys are missing, defaults are True."""
        session = self._make_mock_session()
        trades = [_trade("100", "1.0")]
        curve = [_snap(0, "10000")]
        persist_to_database(
            metrics=_metrics(),
            result=_result(trades=trades, equity_curve=curve),
            backtest_config={},
            session=session,
        )
        session.add.assert_called_once()
        assert session.add_all.call_count == 2


# ── Orchestrator tests ───────────────────────────────────────────────────────


class TestReportBacktest:
    """report_backtest dispatches to console / database based on output_format."""

    @patch("core.backtest.reporting.persist_to_database")
    @patch("core.backtest.reporting.format_console_report")
    def test_console_only(self, mock_console, mock_db):
        report_backtest(
            result=_result(),
            metrics=_metrics(),
            backtest_config={"output_format": "console"},
        )
        mock_console.assert_called_once()
        mock_db.assert_not_called()

    @patch("core.backtest.reporting.persist_to_database")
    @patch("core.backtest.reporting.format_console_report")
    def test_database_only(self, mock_console, mock_db):
        report_backtest(
            result=_result(),
            metrics=_metrics(),
            backtest_config={"output_format": "database"},
        )
        mock_console.assert_not_called()
        mock_db.assert_called_once()

    @patch("core.backtest.reporting.persist_to_database")
    @patch("core.backtest.reporting.format_console_report")
    def test_both(self, mock_console, mock_db):
        report_backtest(
            result=_result(),
            metrics=_metrics(),
            backtest_config={"output_format": "both"},
        )
        mock_console.assert_called_once()
        mock_db.assert_called_once()

    @patch("core.backtest.reporting.persist_to_database")
    @patch("core.backtest.reporting.format_console_report")
    def test_default_is_both(self, mock_console, mock_db):
        report_backtest(
            result=_result(),
            metrics=_metrics(),
            backtest_config={},
        )
        mock_console.assert_called_once()
        mock_db.assert_called_once()

    @patch("core.backtest.reporting.persist_to_database")
    @patch("core.backtest.reporting.format_console_report")
    def test_config_snapshot_passed_to_db(self, mock_console, mock_db):
        snap = {"key": "value"}
        report_backtest(
            result=_result(),
            metrics=_metrics(),
            backtest_config={"output_format": "database"},
            config_snapshot=snap,
        )
        _, kwargs = mock_db.call_args
        assert kwargs["config_snapshot"] == snap
