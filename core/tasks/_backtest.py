"""
Backtest Celery task (task 9.1) + persistence (task 9.2).

Backtests are CPU-bound pandas loops. Running them inside the API request
process holds the GIL, starves the event loop, kills WebSocket heartbeats and
takes the whole dashboard down. This module moves every backtest job
(compare / multi-strategy / optimize / walk-forward) into a Celery worker:

- ``run_backtest_job(job_type, params)`` executes the job and returns the
  same JSON payload the old synchronous endpoints produced, stored in the
  Celery result backend (Redis, 1h TTL) keyed by task id.
- The API enqueues and immediately returns the ``job_id`` (HTTP 202); status
  and result are read back via ``GET /api/backtest/jobs/{job_id}``.
- ``compare`` and ``multi_strategy`` runs persist one row per
  ``(strategy, symbol)`` into ``backtest_results`` sharing the job's
  ``run_id`` (task 9.2), with ``config_snapshot`` for reproducibility.
  Optimize/walk-forward grids don't produce full metrics per combo, so they
  are not persisted here — the validation gate (task 9.9) persists its own
  final runs.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Dict, List

import structlog

from core.celery_app import app

logger = structlog.get_logger(__name__)


# ── Serializers (mirror the legacy synchronous endpoint payloads) ────────────


def _serialize_compare(result) -> Dict[str, Any]:  # noqa: ANN001
    rows: List[Dict[str, Any]] = []
    for row in result.rows:
        m = row.metrics
        rows.append({
            "strategy": row.strategy,
            "symbol": row.symbol,
            "total_return": str(m.total_return),
            "total_trades": m.total_trades,
            "winning_trades": m.winning_trades,
            "losing_trades": m.losing_trades,
            "win_rate": str(m.win_rate),
            "max_drawdown": str(m.max_drawdown),
            "sharpe_ratio": str(m.sharpe_ratio) if m.sharpe_ratio is not None else None,
            "profit_factor": str(m.profit_factor) if m.profit_factor is not None else None,
            "avg_trade_duration_minutes": m.avg_trade_duration_minutes,
            "max_consecutive_wins": m.max_consecutive_wins,
            "max_consecutive_losses": m.max_consecutive_losses,
            "buy_hold_return": str(m.buy_hold_return) if m.buy_hold_return is not None else None,
            "buy_hold_vs_strategy": str(m.buy_hold_vs_strategy) if m.buy_hold_vs_strategy is not None else None,
            "initial_capital": str(m.initial_capital),
            "final_capital": str(m.final_capital),
        })

    per_strategy_curves: Dict[str, List[Dict[str, str]]] = {}
    for row in result.rows:
        key = f"{row.strategy}:{row.symbol}"
        per_strategy_curves[key] = [
            {
                "timestamp": snap.timestamp.isoformat() if hasattr(snap.timestamp, "isoformat") else str(snap.timestamp),
                "equity": str(snap.total_equity.quantize(Decimal("0.01"))),
            }
            for snap in row.equity_curve
        ]

    return {
        "rows": rows,
        "days": result.days,
        "date_range": result.date_range,
        "errors": result.errors,
        "per_strategy_curves": per_strategy_curves,
    }


def _serialize_multi_strategy(result) -> Dict[str, Any]:  # noqa: ANN001
    per_strategy = []
    for bd in result.per_strategy:
        m = bd.metrics
        per_strategy.append({
            "strategy": bd.strategy,
            "symbol": bd.symbol,
            "allocated_capital": bd.allocated_capital,
            "total_return": str(m.total_return),
            "total_trades": m.total_trades,
            "win_rate": str(m.win_rate),
            "max_drawdown": str(m.max_drawdown),
            "sharpe_ratio": str(m.sharpe_ratio) if m.sharpe_ratio is not None else None,
        })

    regime_timeline = [
        {
            "timestamp": e.timestamp,
            "regime": e.regime,
            "confidence": e.confidence,
            "adx": e.adx,
        }
        for e in result.regime_timeline
    ]

    return {
        "combined_equity_curve": result.combined_equity_curve,
        "per_strategy": per_strategy,
        "regime_timeline": regime_timeline,
        "total_initial_capital": result.total_initial_capital,
        "total_final_equity": result.total_final_equity,
        "total_return_pct": result.total_return_pct,
        "date_range": result.date_range,
        "days": result.days,
        "errors": result.errors,
    }


def _serialize_optimize(result) -> Dict[str, Any]:  # noqa: ANN001
    return {
        "strategy": result.strategy,
        "symbol": result.symbol,
        "metric": result.metric,
        "days": result.days,
        "total_combos": result.total_combos,
        "results": [
            {
                "params": r.params,
                "sharpe_ratio": r.sharpe_ratio,
                "total_return": r.total_return,
                "max_drawdown": r.max_drawdown,
                "win_rate": r.win_rate,
                "profit_factor": r.profit_factor,
                "total_trades": r.total_trades,
            }
            for r in result.results
        ],
        "best_params": result.best_params,
        "errors": result.errors,
    }


def _serialize_walk_forward(result) -> Dict[str, Any]:  # noqa: ANN001
    return {
        "strategy": result.strategy,
        "symbol": result.symbol,
        "metric": result.metric,
        "days": result.days,
        "n_folds": result.n_folds,
        "folds": [
            {
                "fold": f.fold,
                "train_range": f.train_range,
                "test_range": f.test_range,
                "best_params": f.best_params,
                "train_sharpe": f.train_sharpe,
                "train_return": f.train_return,
                "test_sharpe": f.test_sharpe,
                "test_return": f.test_return,
                "overfit": f.overfit,
            }
            for f in result.folds
        ],
        "avg_test_sharpe": result.avg_test_sharpe,
        "avg_test_return": result.avg_test_return,
        "errors": result.errors,
    }


# ── Persistence (task 9.2) ───────────────────────────────────────────────────


def _persist_metrics_rows(
    run_id: str,
    job_type: str,
    params: Dict[str, Any],
    metrics_rows,  # noqa: ANN001 — iterable of (strategy, symbol, BacktestMetrics)
) -> int:
    """
    Persist one ``BacktestResult`` row per (strategy, symbol) under a shared
    ``run_id`` so a whole job can be grouped and compared later.

    ``config_snapshot`` captures the job parameters plus the full strategy
    config used, making the run reproducible.
    """
    from core.config import get_settings
    from db import get_session
    from db.models import BacktestResult

    settings = get_settings()
    persisted = 0

    with get_session() as session:
        for strategy_name, symbol, m in metrics_rows:
            strat_cfg = settings.strategies.get_strategy_config(strategy_name)
            strategy_config = (
                strat_cfg if isinstance(strat_cfg, dict) or strat_cfg is None
                else strat_cfg.model_dump(mode="json")
            )

            session.add(BacktestResult(
                run_id=run_id,
                strategy=strategy_name,
                symbol=symbol,
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
                    "job_type": job_type,
                    "params": params,
                    "strategy_config": strategy_config,
                },
            ))
            persisted += 1
        session.commit()

    return persisted


# ── The task ─────────────────────────────────────────────────────────────────

_VALID_JOB_TYPES = ("compare", "multi_strategy", "optimize", "walk_forward")


@app.task(bind=True, name="core.tasks.run_backtest_job")
def run_backtest_job(self, job_type: str, params: Dict[str, Any]) -> Dict[str, Any]:  # noqa: ANN001
    """
    Execute a backtest job in a Celery worker (task 9.1).

    Args:
        job_type: One of ``compare``, ``multi_strategy``, ``optimize``,
            ``walk_forward``.
        params: Job parameters (same fields the old sync endpoints accepted).

    Returns the serialized result payload (stored by Celery in Redis, read
    back via ``GET /api/backtest/jobs/{job_id}``).
    """
    from core.events import EventChannel, EventType, get_publisher

    if job_type not in _VALID_JOB_TYPES:
        raise ValueError(f"unknown job_type: {job_type!r} (expected one of {_VALID_JOB_TYPES})")

    job_id = self.request.id or "sync"
    run_id = f"job:{job_id}"
    publisher = get_publisher()

    publisher.publish(
        EventChannel.SYSTEM,
        EventType.BACKTEST_JOB_STARTED,
        {"job_id": job_id, "job_type": job_type},
    )
    logger.info("backtest_job: started", job_id=job_id, job_type=job_type, category="system")

    try:
        if job_type == "compare":
            from core.backtest.compare import compare_strategies

            result = compare_strategies(
                symbols=params.get("symbols"),
                days=int(params.get("days", 90)),
                strategy_names=params.get("strategies"),
            )
            payload = _serialize_compare(result)
            persisted = _persist_metrics_rows(
                run_id, job_type, params,
                ((row.strategy, row.symbol, row.metrics) for row in result.rows),
            )

        elif job_type == "multi_strategy":
            from core.backtest.multi_strategy import run_multi_strategy_backtest

            result = run_multi_strategy_backtest(
                symbols=params.get("symbols"),
                days=int(params.get("days", 90)),
                strategy_names=params.get("strategies"),
            )
            payload = _serialize_multi_strategy(result)
            persisted = _persist_metrics_rows(
                run_id, job_type, params,
                ((bd.strategy, bd.symbol, bd.metrics) for bd in result.per_strategy),
            )

        elif job_type == "optimize":
            from core.backtest.optimize import grid_search

            result = grid_search(
                strategy_name=params["strategy"],
                symbol=params.get("symbol", "BTCUSDT"),
                days=int(params.get("days", 90)),
                param_grid=params["param_grid"],
                metric=params.get("metric", "sharpe_ratio"),
            )
            payload = _serialize_optimize(result)
            persisted = 0  # grid combos lack full metrics; gate (9.9) persists

        else:  # walk_forward
            from core.backtest.optimize import walk_forward

            result = walk_forward(
                strategy_name=params["strategy"],
                symbol=params.get("symbol", "BTCUSDT"),
                days=int(params.get("days", 90)),
                param_grid=params["param_grid"],
                metric=params.get("metric", "sharpe_ratio"),
                n_folds=int(params.get("n_folds", 3)),
                train_pct=float(params.get("train_pct", 0.7)),
            )
            payload = _serialize_walk_forward(result)
            persisted = 0

        payload["run_id"] = run_id
        payload["job_type"] = job_type

        publisher.publish(
            EventChannel.SYSTEM,
            EventType.BACKTEST_JOB_COMPLETED,
            {"job_id": job_id, "job_type": job_type, "persisted_rows": persisted},
        )
        logger.info(
            "backtest_job: completed",
            job_id=job_id, job_type=job_type, persisted_rows=persisted,
            category="system",
        )
        return payload

    except Exception as exc:
        publisher.publish(
            EventChannel.SYSTEM,
            EventType.BACKTEST_JOB_FAILED,
            {"job_id": job_id, "job_type": job_type, "error": str(exc)},
        )
        logger.error(
            "backtest_job: failed",
            job_id=job_id, job_type=job_type, error=str(exc),
            exc_info=True, category="system",
        )
        raise
