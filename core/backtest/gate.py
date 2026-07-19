"""
Validation gate — mandatory walk-forward before any strategy goes live
(task 9.9).

Phase-9 golden rule: no strategy change reaches live trading without an
approved gate run. ``validate_strategy`` walk-forwards the candidate
parameters over >= 365 days with costs and exchange filters (task 9.4)
enabled, applies the approval criteria, and persists every out-of-sample
fold into ``backtest_results`` under a ``gate:``-prefixed run_id so the
evidence is queryable later (``GET /api/backtest/history?run_id=...``).

Approval criteria (all must hold):
- every test fold has positive net return (out-of-sample)
- profit factor > 1.2 in every fold that produced one
- no fold with max drawdown > 25%
- no fold flagged as overfit (test sharpe < 50% of train sharpe)
- history covers >= 365 days and >= 3 folds were evaluated

CLI:
    python -m core.backtest.gate smart_hodler '{"ema_fast": 12}' [SYMBOL]
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import structlog

logger = structlog.get_logger(__name__)

GATE_RUN_PREFIX = "gate:"

# ── Approval thresholds ──────────────────────────────────────────────────────

MIN_DAYS = 365
MIN_FOLDS = 3
MIN_PROFIT_FACTOR = 1.2
MAX_FOLD_DRAWDOWN_PCT = 25.0


@dataclass
class GateCheck:
    name: str
    passed: bool
    detail: str


@dataclass
class GateResult:
    strategy: str
    symbol: str
    days: int
    n_folds: int
    params: Dict[str, Any]
    run_id: str
    passed: bool = False
    checks: List[GateCheck] = field(default_factory=list)
    avg_test_return: Optional[float] = None
    avg_test_sharpe: Optional[float] = None
    persisted_rows: int = 0
    errors: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "strategy": self.strategy,
            "symbol": self.symbol,
            "days": self.days,
            "n_folds": self.n_folds,
            "params": self.params,
            "run_id": self.run_id,
            "passed": self.passed,
            "checks": [
                {"name": c.name, "passed": c.passed, "detail": c.detail}
                for c in self.checks
            ],
            "avg_test_return": self.avg_test_return,
            "avg_test_sharpe": self.avg_test_sharpe,
            "persisted_rows": self.persisted_rows,
            "errors": self.errors,
        }


def validate_strategy(
    strategy_name: str,
    params: Dict[str, Any],
    symbol: str = "BTCUSDT",
    days: int = MIN_DAYS,
    n_folds: int = MIN_FOLDS,
    metric: str = "sharpe_ratio",
    persist: bool = True,
) -> GateResult:
    """
    Run the mandatory pre-live validation gate for a parameter candidate.

    Args:
        strategy_name: Registered strategy name.
        params: The exact parameter overrides to validate (single
            candidate — each value becomes a 1-element grid). Must not
            be empty; to validate the current config as-is, pass one of
            its existing values explicitly.
        symbol: Trading pair to validate on.
        days: History window (gate requires >= 365).
        n_folds: Walk-forward folds (gate requires >= 3).
        metric: Ranking metric for the (degenerate) train grid search.
        persist: Persist per-fold test metrics under the gate run_id.

    Returns:
        ``GateResult`` with verdict, per-criterion checks and run_id.
    """
    from core.backtest.optimize import walk_forward

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_id = f"{GATE_RUN_PREFIX}{strategy_name}:{symbol}:{ts}"

    result = GateResult(
        strategy=strategy_name,
        symbol=symbol,
        days=days,
        n_folds=n_folds,
        params=params,
        run_id=run_id,
    )

    if not params:
        result.errors.append(
            "gate requires explicit candidate params (non-empty dict)"
        )
        return result

    param_grid = {k: [v] for k, v in params.items()}

    wf = walk_forward(
        strategy_name=strategy_name,
        symbol=symbol,
        days=days,
        param_grid=param_grid,
        metric=metric,
        n_folds=n_folds,
    )
    result.errors.extend(wf.errors)
    result.avg_test_return = wf.avg_test_return
    result.avg_test_sharpe = wf.avg_test_sharpe

    folds = wf.folds

    # ── Criteria ─────────────────────────────────────────────────────────
    result.checks.append(GateCheck(
        name="history_length",
        passed=days >= MIN_DAYS,
        detail=f"{days} days (min {MIN_DAYS})",
    ))

    result.checks.append(GateCheck(
        name="folds_evaluated",
        passed=len(folds) >= MIN_FOLDS and not wf.errors,
        detail=f"{len(folds)} folds evaluated (min {MIN_FOLDS})"
        + (f"; errors: {wf.errors}" if wf.errors else ""),
    ))

    negative = [f.fold for f in folds if f.test_return <= 0]
    result.checks.append(GateCheck(
        name="positive_test_returns",
        passed=len(folds) > 0 and not negative,
        detail="all test folds positive" if not negative
        else f"non-positive test return in folds {negative}",
    ))

    # Profit factor: None means no losing trades — zero-trade folds are
    # already rejected by positive_test_returns.
    weak_pf = [
        f.fold for f in folds
        if f.test_profit_factor is not None
        and f.test_profit_factor <= MIN_PROFIT_FACTOR
    ]
    result.checks.append(GateCheck(
        name="profit_factor",
        passed=not weak_pf,
        detail=f"all folds > {MIN_PROFIT_FACTOR}" if not weak_pf
        else f"profit factor <= {MIN_PROFIT_FACTOR} in folds {weak_pf}",
    ))

    deep_dd = [
        f.fold for f in folds
        if f.test_max_drawdown is None or f.test_max_drawdown > MAX_FOLD_DRAWDOWN_PCT
    ]
    result.checks.append(GateCheck(
        name="max_drawdown",
        passed=len(folds) > 0 and not deep_dd,
        detail=f"all folds <= {MAX_FOLD_DRAWDOWN_PCT}%" if not deep_dd
        else f"drawdown > {MAX_FOLD_DRAWDOWN_PCT}% (or missing) in folds {deep_dd}",
    ))

    overfit = [f.fold for f in folds if f.overfit]
    result.checks.append(GateCheck(
        name="overfit",
        passed=not overfit,
        detail="no overfit flags" if not overfit
        else f"overfit flagged in folds {overfit}",
    ))

    result.passed = all(c.passed for c in result.checks)

    # ── Persist evidence ─────────────────────────────────────────────────
    if persist:
        try:
            result.persisted_rows = _persist_gate_folds(result, folds)
        except Exception as exc:
            logger.error("gate: persistence failed", error=str(exc), exc_info=True)
            result.errors.append(f"persistence failed: {exc}")

    logger.info(
        "gate: verdict",
        strategy=strategy_name,
        symbol=symbol,
        run_id=run_id,
        passed=result.passed,
        checks={c.name: c.passed for c in result.checks},
        category="system",
    )
    return result


def _persist_gate_folds(result: GateResult, folds) -> int:  # noqa: ANN001
    """One ``backtest_results`` row per test fold, under the gate run_id."""
    from db import get_session
    from db.models import BacktestResult

    persisted = 0
    with get_session() as session:
        for f in folds:
            m = f.test_metrics
            if m is None:
                continue
            session.add(BacktestResult(
                run_id=result.run_id,
                strategy=result.strategy,
                symbol=result.symbol,
                start_date=m.start_date.replace(tzinfo=None) if m.start_date.tzinfo else m.start_date,
                end_date=m.end_date.replace(tzinfo=None) if m.end_date.tzinfo else m.end_date,
                initial_capital=m.initial_capital,
                final_capital=m.final_capital,
                total_return=m.total_return,
                total_trades=m.total_trades,
                winning_trades=m.winning_trades,
                losing_trades=m.losing_trades,
                win_rate=m.win_rate,
                avg_win=m.avg_win,
                avg_loss=m.avg_loss,
                max_drawdown=m.max_drawdown,
                sharpe_ratio=m.sharpe_ratio,
                profit_factor=m.profit_factor,
                avg_trade_duration_minutes=m.avg_trade_duration_minutes,
                max_consecutive_wins=m.max_consecutive_wins,
                max_consecutive_losses=m.max_consecutive_losses,
                buy_hold_return=m.buy_hold_return,
                buy_hold_vs_strategy=m.buy_hold_vs_strategy,
                config_snapshot={
                    "job_type": "gate",
                    "params": result.params,
                    "fold": f.fold,
                    "test_range": f.test_range,
                    "verdict": result.passed,
                    "checks": {c.name: c.passed for c in result.checks},
                },
            ))
            persisted += 1
        session.commit()
    return persisted


# ── CLI ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":  # pragma: no cover
    import json
    import sys

    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)

    gate = validate_strategy(
        strategy_name=sys.argv[1],
        params=json.loads(sys.argv[2]),
        symbol=sys.argv[3] if len(sys.argv) > 3 else "BTCUSDT",
    )
    print(json.dumps(gate.to_dict(), indent=2, default=str))
    sys.exit(0 if gate.passed else 2)
