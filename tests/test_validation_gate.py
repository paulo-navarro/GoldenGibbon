"""
Tests for the pre-live validation gate (task 9.9).

Covers:
    - approval when every criterion holds
    - rejection on: negative test fold, weak profit factor, deep
      drawdown, overfit flag, too few folds, empty params
    - per-fold evidence persisted under a ``gate:`` run_id with the
      verdict in config_snapshot
"""

from datetime import datetime, timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest

from core.backtest.gate import GATE_RUN_PREFIX, validate_strategy
from core.backtest.optimize import WalkForwardFold, WalkForwardResult
from core.models import BacktestMetrics
from db import get_session
from db.models import BacktestResult

T0 = datetime(2025, 1, 1)


def _metrics(total_return: str = "10.0") -> BacktestMetrics:
    return BacktestMetrics(
        run_id="unused",
        strategy="smart_hodler",
        symbol="BTCUSDT",
        start_date=T0,
        end_date=T0 + timedelta(days=120),
        initial_capital=Decimal("50"),
        final_capital=Decimal("55"),
        total_return=Decimal(total_return),
        total_trades=8,
        winning_trades=5,
        losing_trades=3,
        win_rate=Decimal("62.50"),
        avg_win=Decimal("2.0"),
        avg_loss=Decimal("-1.0"),
        max_drawdown=Decimal("8.0"),
        sharpe_ratio=Decimal("1.4"),
        profit_factor=Decimal("1.8"),
    )


def _fold(
    fold: int = 1,
    test_return: float = 10.0,
    test_profit_factor: float | None = 1.8,
    test_max_drawdown: float | None = 8.0,
    overfit: bool = False,
) -> WalkForwardFold:
    return WalkForwardFold(
        fold=fold,
        train_range="a → b",
        test_range="b → c",
        best_params={"ema_fast": 12},
        train_sharpe=1.5,
        train_return=12.0,
        test_sharpe=1.2,
        test_return=test_return,
        overfit=overfit,
        test_profit_factor=test_profit_factor,
        test_max_drawdown=test_max_drawdown,
        test_metrics=_metrics(str(test_return)),
    )


def _wf(folds: list[WalkForwardFold], errors: list[str] | None = None) -> WalkForwardResult:
    return WalkForwardResult(
        strategy="smart_hodler",
        symbol="BTCUSDT",
        metric="sharpe_ratio",
        days=365,
        n_folds=len(folds),
        folds=folds,
        avg_test_sharpe=1.2,
        avg_test_return=10.0,
        errors=errors or [],
    )


def _run_gate(wf: WalkForwardResult, **kwargs):
    with patch("core.backtest.optimize.walk_forward", return_value=wf):
        return validate_strategy(
            "smart_hodler", {"ema_fast": 12}, persist=kwargs.pop("persist", False), **kwargs
        )


class TestGateVerdict:
    def test_all_criteria_pass(self):
        result = _run_gate(_wf([_fold(1), _fold(2), _fold(3)]))
        assert result.passed is True
        assert all(c.passed for c in result.checks)
        assert result.run_id.startswith(GATE_RUN_PREFIX)

    def test_negative_fold_rejects(self):
        result = _run_gate(_wf([_fold(1), _fold(2, test_return=-3.0), _fold(3)]))
        assert result.passed is False
        failed = {c.name for c in result.checks if not c.passed}
        assert failed == {"positive_test_returns"}

    def test_weak_profit_factor_rejects(self):
        result = _run_gate(_wf([_fold(1), _fold(2, test_profit_factor=1.1), _fold(3)]))
        assert result.passed is False
        failed = {c.name for c in result.checks if not c.passed}
        assert failed == {"profit_factor"}

    def test_none_profit_factor_passes(self):
        """PF None = no losing trades — not a rejection by itself."""
        result = _run_gate(_wf([_fold(1, test_profit_factor=None), _fold(2), _fold(3)]))
        assert result.passed is True

    def test_deep_drawdown_rejects(self):
        result = _run_gate(_wf([_fold(1), _fold(2, test_max_drawdown=30.0), _fold(3)]))
        assert result.passed is False
        failed = {c.name for c in result.checks if not c.passed}
        assert failed == {"max_drawdown"}

    def test_overfit_rejects(self):
        result = _run_gate(_wf([_fold(1), _fold(2, overfit=True), _fold(3)]))
        assert result.passed is False
        failed = {c.name for c in result.checks if not c.passed}
        assert failed == {"overfit"}

    def test_too_few_folds_rejects(self):
        result = _run_gate(_wf([_fold(1), _fold(2)]))
        assert result.passed is False
        assert not [c for c in result.checks if c.name == "folds_evaluated"][0].passed

    def test_short_history_rejects(self):
        result = _run_gate(_wf([_fold(1), _fold(2), _fold(3)]), days=90)
        assert result.passed is False
        assert not [c for c in result.checks if c.name == "history_length"][0].passed

    def test_empty_params_rejected(self):
        result = validate_strategy("smart_hodler", {}, persist=False)
        assert result.passed is False
        assert result.errors


class TestGatePersistence:
    def test_folds_persisted_with_gate_run_id(self):
        wf = _wf([_fold(1), _fold(2), _fold(3)])
        with patch("core.backtest.optimize.walk_forward", return_value=wf):
            result = validate_strategy("smart_hodler", {"ema_fast": 12}, persist=True)

        assert result.persisted_rows == 3
        with get_session() as session:
            rows = (
                session.query(BacktestResult)
                .filter(BacktestResult.run_id == result.run_id)
                .all()
            )
            assert len(rows) == 3
            snap = rows[0].config_snapshot
            assert snap["job_type"] == "gate"
            assert snap["params"] == {"ema_fast": 12}
            assert snap["verdict"] is True
            assert set(snap["checks"]) == {
                "history_length", "folds_evaluated", "positive_test_returns",
                "profit_factor", "max_drawdown", "overfit",
            }
