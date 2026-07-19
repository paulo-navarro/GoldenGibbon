"""
Backtest REST endpoints (async job model — task 9.1).

Mounted at ``/api/backtest`` by :func:`api.main._include_routes`.

Backtests are CPU-bound; running them inside the API request froze the event
loop and took the dashboard down. Every run endpoint now **enqueues** a
Celery job and returns ``202`` with a ``job_id`` immediately. Poll
``GET /jobs/{job_id}`` for status and the result payload.

Endpoints
---------
POST /compare          → enqueue strategy comparison        → {job_id}
POST /multi-strategy   → enqueue multi-strategy backtest    → {job_id}
POST /optimize         → enqueue grid-search optimization   → {job_id}
POST /walk-forward     → enqueue walk-forward validation    → {job_id}
GET  /jobs/{job_id}    → job status + result when done
GET  /history          → persisted backtest_results rows (task 9.2)
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Query
from pydantic import BaseModel

router = APIRouter()


# ── Request / response models ────────────────────────────────────────────────


class CompareRequest(BaseModel):
    symbols: Optional[List[str]] = None
    days: int = 90
    strategies: Optional[List[str]] = None


class MultiStrategyRequest(BaseModel):
    symbols: Optional[List[str]] = None
    days: int = 90
    strategies: Optional[List[str]] = None


class OptimizationRequest(BaseModel):
    strategy: str
    symbol: str = "BTCUSDT"
    days: int = 90
    param_grid: Dict[str, List[Any]]
    metric: str = "sharpe_ratio"


class WalkForwardRequest(BaseModel):
    strategy: str
    symbol: str = "BTCUSDT"
    days: int = 90
    param_grid: Dict[str, List[Any]]
    metric: str = "sharpe_ratio"
    n_folds: int = 3
    train_pct: float = 0.7


class JobEnqueuedResponse(BaseModel):
    job_id: str
    job_type: str
    status: str = "pending"


class JobStatusResponse(BaseModel):
    job_id: str
    status: str  # pending | running | done | failed
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None


class BacktestHistoryRow(BaseModel):
    run_id: str
    strategy: str
    symbol: str
    start_date: str
    end_date: str
    initial_capital: str
    final_capital: str
    total_return: str
    total_trades: int
    win_rate: str
    max_drawdown: str
    sharpe_ratio: Optional[str] = None
    profit_factor: Optional[str] = None
    created_at: str
    config_snapshot: Optional[Dict[str, Any]] = None


class BacktestHistoryResponse(BaseModel):
    rows: List[BacktestHistoryRow]
    total_count: int


# ── Helpers ──────────────────────────────────────────────────────────────────


def _enqueue(job_type: str, params: Dict[str, Any]) -> JobEnqueuedResponse:
    from core.tasks import run_backtest_job

    async_result = run_backtest_job.delay(job_type, params)
    return JobEnqueuedResponse(job_id=async_result.id, job_type=job_type)


_CELERY_STATE_MAP = {
    "PENDING": "pending",
    "RECEIVED": "pending",
    "STARTED": "running",
    "RETRY": "running",
    "SUCCESS": "done",
    "FAILURE": "failed",
    "REVOKED": "failed",
}


# ── Run endpoints (enqueue, HTTP 202) ────────────────────────────────────────


@router.post("/compare", response_model=JobEnqueuedResponse, status_code=202)
def enqueue_compare(req: CompareRequest) -> JobEnqueuedResponse:
    return _enqueue("compare", req.model_dump())


@router.post("/multi-strategy", response_model=JobEnqueuedResponse, status_code=202)
def enqueue_multi_strategy(req: MultiStrategyRequest) -> JobEnqueuedResponse:
    return _enqueue("multi_strategy", req.model_dump())


@router.post("/optimize", response_model=JobEnqueuedResponse, status_code=202)
def enqueue_optimize(req: OptimizationRequest) -> JobEnqueuedResponse:
    return _enqueue("optimize", req.model_dump())


@router.post("/walk-forward", response_model=JobEnqueuedResponse, status_code=202)
def enqueue_walk_forward(req: WalkForwardRequest) -> JobEnqueuedResponse:
    return _enqueue("walk_forward", req.model_dump())


# ── Job status ───────────────────────────────────────────────────────────────


@router.get("/jobs/{job_id}", response_model=JobStatusResponse)
def get_job_status(job_id: str) -> JobStatusResponse:
    """
    Return status of a backtest job; includes the result payload when done.

    Celery reports unknown ids as PENDING — an id that was never enqueued
    will therefore also show as ``pending``. Results expire from Redis after
    1 hour (``result_expires``); persisted metrics remain in ``/history``.
    """
    from celery.result import AsyncResult

    from core.celery_app import app as celery_app

    async_result = AsyncResult(job_id, app=celery_app)
    status = _CELERY_STATE_MAP.get(async_result.state, "pending")

    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    if status == "done":
        result = async_result.result
    elif status == "failed":
        error = str(async_result.result)

    return JobStatusResponse(job_id=job_id, status=status, result=result, error=error)


# ── Persisted history (task 9.2) ─────────────────────────────────────────────


@router.get("/history", response_model=BacktestHistoryResponse)
def get_backtest_history(
    strategy: Optional[str] = Query(None, description="Filter by strategy name"),
    symbol: Optional[str] = Query(None, description="Filter by symbol"),
    run_id: Optional[str] = Query(None, description="Filter by run_id (one job)"),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
) -> BacktestHistoryResponse:
    """List persisted backtest results, newest first."""
    from sqlalchemy import func as sa_func, select

    from db import get_session
    from db.models import BacktestResult

    with get_session() as session:
        stmt = select(BacktestResult)
        count_stmt = select(sa_func.count(BacktestResult.id))
        if strategy is not None:
            stmt = stmt.where(BacktestResult.strategy == strategy)
            count_stmt = count_stmt.where(BacktestResult.strategy == strategy)
        if symbol is not None:
            stmt = stmt.where(BacktestResult.symbol == symbol.upper())
            count_stmt = count_stmt.where(BacktestResult.symbol == symbol.upper())
        if run_id is not None:
            stmt = stmt.where(BacktestResult.run_id == run_id)
            count_stmt = count_stmt.where(BacktestResult.run_id == run_id)

        total_count = session.execute(count_stmt).scalar() or 0
        records = list(
            session.execute(
                stmt.order_by(BacktestResult.created_at.desc())
                .offset(offset)
                .limit(limit)
            ).scalars()
        )

        rows = [
            BacktestHistoryRow(
                run_id=r.run_id,
                strategy=r.strategy,
                symbol=r.symbol,
                start_date=r.start_date.isoformat(),
                end_date=r.end_date.isoformat(),
                initial_capital=str(r.initial_capital),
                final_capital=str(r.final_capital),
                total_return=str(r.total_return),
                total_trades=r.total_trades,
                win_rate=str(r.win_rate),
                max_drawdown=str(r.max_drawdown),
                sharpe_ratio=str(r.sharpe_ratio) if r.sharpe_ratio is not None else None,
                profit_factor=str(r.profit_factor) if r.profit_factor is not None else None,
                created_at=r.created_at.isoformat(),
                config_snapshot=r.config_snapshot,
            )
            for r in records
        ]

    return BacktestHistoryResponse(rows=rows, total_count=total_count)
