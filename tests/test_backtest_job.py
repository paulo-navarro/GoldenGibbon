"""
Tests for the async backtest Celery task (tasks 9.1 / 9.2).

Covers:
    - run_backtest_job rejects unknown job types before touching Redis/DB
    - _persist_metrics_rows writes one BacktestResult row per (strategy,
      symbol) sharing the job's run_id (the unique constraint on run_id was
      dropped in migration m8n9o0p1q234)
    - config_snapshot captures job_type, params and the strategy config
"""

from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from core.models import BacktestMetrics
from core.tasks._backtest import _persist_metrics_rows, run_backtest_job
from db import get_session
from db.models import BacktestResult

T0 = datetime(2025, 1, 1)

RUN_ID = "job:test-9-2-persistence"


def _metrics(strategy: str, symbol: str) -> BacktestMetrics:
    return BacktestMetrics(
        run_id="unused",  # _persist_metrics_rows uses the job run_id instead
        strategy=strategy,
        symbol=symbol,
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


@pytest.fixture()
def clean_run_rows():
    def _cleanup():
        with get_session() as session:
            session.query(BacktestResult).filter(
                BacktestResult.run_id == RUN_ID
            ).delete()
            session.commit()

    _cleanup()
    yield
    _cleanup()


class TestRunBacktestJob:
    def test_invalid_job_type_raises(self):
        with pytest.raises(ValueError, match="unknown job_type"):
            run_backtest_job("bogus", {})


class TestPersistMetricsRows:
    def test_rows_share_run_id(self, clean_run_rows):
        rows = [
            ("smart_hodler", "BTCUSDT", _metrics("smart_hodler", "BTCUSDT")),
            ("smart_hodler", "ETHUSDT", _metrics("smart_hodler", "ETHUSDT")),
        ]
        persisted = _persist_metrics_rows(RUN_ID, "compare", {"days": 30}, rows)
        assert persisted == 2

        with get_session() as session:
            saved = (
                session.query(BacktestResult)
                .filter(BacktestResult.run_id == RUN_ID)
                .order_by(BacktestResult.symbol)
                .all()
            )
            assert [r.symbol for r in saved] == ["BTCUSDT", "ETHUSDT"]
            assert all(r.strategy == "smart_hodler" for r in saved)
            assert saved[0].total_trades == 5
            assert saved[0].total_return == Decimal("10.0000")

    def test_config_snapshot_captures_job(self, clean_run_rows):
        rows = [("smart_hodler", "BTCUSDT", _metrics("smart_hodler", "BTCUSDT"))]
        _persist_metrics_rows(RUN_ID, "compare", {"days": 30, "symbols": None}, rows)

        with get_session() as session:
            saved = (
                session.query(BacktestResult)
                .filter(BacktestResult.run_id == RUN_ID)
                .one()
            )
            snap = saved.config_snapshot
            assert snap["job_type"] == "compare"
            assert snap["params"] == {"days": 30, "symbols": None}
            assert "strategy_config" in snap
