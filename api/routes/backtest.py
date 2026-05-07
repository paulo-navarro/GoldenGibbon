"""
Backtest REST endpoints.

Mounted at ``/api/backtest`` by :func:`api.main._include_routes`.

Endpoints
---------
GET /compare
    Run backtests for multiple strategies on the same data window
    and return side-by-side metrics.

GET /multi-strategy
    Run all strategies simultaneously with regime-based allocation
    and return a combined equity curve.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Query
from pydantic import BaseModel

router = APIRouter()


# ── Response models ──────────────────────────────────────────────────────────


class MetricsRow(BaseModel):
    strategy: str
    symbol: str
    total_return: str
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: str
    max_drawdown: str
    sharpe_ratio: Optional[str] = None
    profit_factor: Optional[str] = None
    avg_trade_duration_minutes: Optional[int] = None
    max_consecutive_wins: Optional[int] = None
    max_consecutive_losses: Optional[int] = None
    buy_hold_return: Optional[str] = None
    buy_hold_vs_strategy: Optional[str] = None
    initial_capital: str
    final_capital: str


class EquityPoint(BaseModel):
    timestamp: str
    equity: str


class ComparisonResponse(BaseModel):
    rows: List[MetricsRow]
    days: int
    date_range: Optional[str] = None
    errors: List[Dict[str, str]]
    per_strategy_curves: Dict[str, List[EquityPoint]] = {}


# ── Endpoint ─────────────────────────────────────────────────────────────────


@router.get("/compare", response_model=ComparisonResponse)
def compare_strategies(
    symbols: Optional[str] = Query(None, description="Comma-separated symbols (default: all enabled)"),
    days: int = Query(90, ge=7, le=730, description="Lookback window in days"),
    strategies: Optional[str] = Query(None, description="Comma-separated strategy names (default: all)"),
) -> ComparisonResponse:
    from core.backtest.compare import compare_strategies as _compare

    symbol_list = [s.strip() for s in symbols.split(",")] if symbols else None
    strategy_list = [s.strip() for s in strategies.split(",")] if strategies else None

    result = _compare(
        symbols=symbol_list,
        days=days,
        strategy_names=strategy_list,
    )

    rows = []
    for row in result.rows:
        m = row.metrics
        rows.append(MetricsRow(
            strategy=row.strategy,
            symbol=row.symbol,
            total_return=str(m.total_return),
            total_trades=m.total_trades,
            winning_trades=m.winning_trades,
            losing_trades=m.losing_trades,
            win_rate=str(m.win_rate),
            max_drawdown=str(m.max_drawdown),
            sharpe_ratio=str(m.sharpe_ratio) if m.sharpe_ratio is not None else None,
            profit_factor=str(m.profit_factor) if m.profit_factor is not None else None,
            avg_trade_duration_minutes=m.avg_trade_duration_minutes,
            max_consecutive_wins=m.max_consecutive_wins,
            max_consecutive_losses=m.max_consecutive_losses,
            buy_hold_return=str(m.buy_hold_return) if m.buy_hold_return is not None else None,
            buy_hold_vs_strategy=str(m.buy_hold_vs_strategy) if m.buy_hold_vs_strategy is not None else None,
            initial_capital=str(m.initial_capital),
            final_capital=str(m.final_capital),
        ))

    per_strategy_curves: Dict[str, List[EquityPoint]] = {}
    for row in result.rows:
        key = f"{row.strategy}:{row.symbol}"
        per_strategy_curves[key] = [
            EquityPoint(
                timestamp=snap.timestamp.isoformat() if hasattr(snap.timestamp, "isoformat") else str(snap.timestamp),
                equity=str(snap.total_equity.quantize(Decimal("0.01"))),
            )
            for snap in row.equity_curve
        ]

    return ComparisonResponse(
        rows=rows,
        days=result.days,
        date_range=result.date_range,
        errors=result.errors,
        per_strategy_curves=per_strategy_curves,
    )


# ── Multi-strategy backtest (task 5.4d) ─────────────────────────────────────


class RegimeEventResponse(BaseModel):
    timestamp: str
    regime: str
    confidence: float
    adx: float


class StrategyBreakdownResponse(BaseModel):
    strategy: str
    symbol: str
    allocated_capital: str
    total_return: str
    total_trades: int
    win_rate: str
    max_drawdown: str
    sharpe_ratio: Optional[str] = None


class MultiStrategyResponse(BaseModel):
    combined_equity_curve: List[Dict[str, Any]]
    per_strategy: List[StrategyBreakdownResponse]
    regime_timeline: List[RegimeEventResponse]
    total_initial_capital: str
    total_final_equity: str
    total_return_pct: str
    date_range: Optional[str] = None
    days: int
    errors: List[Dict[str, str]]


@router.get("/multi-strategy", response_model=MultiStrategyResponse)
def multi_strategy_backtest(
    symbols: Optional[str] = Query(None, description="Comma-separated symbols"),
    days: int = Query(90, ge=7, le=730, description="Lookback window in days"),
    strategies: Optional[str] = Query(None, description="Comma-separated strategy names"),
) -> MultiStrategyResponse:
    from core.backtest.multi_strategy import run_multi_strategy_backtest

    symbol_list = [s.strip() for s in symbols.split(",")] if symbols else None
    strategy_list = [s.strip() for s in strategies.split(",")] if strategies else None

    result = run_multi_strategy_backtest(
        symbols=symbol_list,
        days=days,
        strategy_names=strategy_list,
    )

    per_strategy = []
    for bd in result.per_strategy:
        m = bd.metrics
        per_strategy.append(StrategyBreakdownResponse(
            strategy=bd.strategy,
            symbol=bd.symbol,
            allocated_capital=bd.allocated_capital,
            total_return=str(m.total_return),
            total_trades=m.total_trades,
            win_rate=str(m.win_rate),
            max_drawdown=str(m.max_drawdown),
            sharpe_ratio=str(m.sharpe_ratio) if m.sharpe_ratio is not None else None,
        ))

    regime_timeline = [
        RegimeEventResponse(
            timestamp=e.timestamp,
            regime=e.regime,
            confidence=e.confidence,
            adx=e.adx,
        )
        for e in result.regime_timeline
    ]

    return MultiStrategyResponse(
        combined_equity_curve=result.combined_equity_curve,
        per_strategy=per_strategy,
        regime_timeline=regime_timeline,
        total_initial_capital=result.total_initial_capital,
        total_final_equity=result.total_final_equity,
        total_return_pct=result.total_return_pct,
        date_range=result.date_range,
        days=result.days,
        errors=result.errors,
    )


# ── Parameter Optimization (task 5.6) ──────────────────────────────────────


class GridSearchRowResponse(BaseModel):
    params: Dict[str, Any]
    sharpe_ratio: Optional[float] = None
    total_return: float = 0.0
    max_drawdown: float = 0.0
    win_rate: float = 0.0
    profit_factor: Optional[float] = None
    total_trades: int = 0


class OptimizationRequest(BaseModel):
    strategy: str
    symbol: str = "BTCUSDT"
    days: int = 90
    param_grid: Dict[str, List[Any]]
    metric: str = "sharpe_ratio"


class OptimizationResponse(BaseModel):
    strategy: str
    symbol: str
    metric: str
    days: int
    total_combos: int
    results: List[GridSearchRowResponse]
    best_params: Dict[str, Any]
    errors: List[str]


class WalkForwardFoldResponse(BaseModel):
    fold: int
    train_range: str
    test_range: str
    best_params: Dict[str, Any]
    train_sharpe: Optional[float] = None
    train_return: float = 0.0
    test_sharpe: Optional[float] = None
    test_return: float = 0.0
    overfit: bool = False


class WalkForwardRequest(BaseModel):
    strategy: str
    symbol: str = "BTCUSDT"
    days: int = 90
    param_grid: Dict[str, List[Any]]
    metric: str = "sharpe_ratio"
    n_folds: int = 3
    train_pct: float = 0.7


class WalkForwardResponse(BaseModel):
    strategy: str
    symbol: str
    metric: str
    days: int
    n_folds: int
    folds: List[WalkForwardFoldResponse]
    avg_test_sharpe: Optional[float] = None
    avg_test_return: float = 0.0
    errors: List[str]


@router.post("/optimize", response_model=OptimizationResponse)
def run_optimization(req: OptimizationRequest) -> OptimizationResponse:
    from core.backtest.optimize import grid_search

    result = grid_search(
        strategy_name=req.strategy,
        symbol=req.symbol,
        days=req.days,
        param_grid=req.param_grid,
        metric=req.metric,
    )

    return OptimizationResponse(
        strategy=result.strategy,
        symbol=result.symbol,
        metric=result.metric,
        days=result.days,
        total_combos=result.total_combos,
        results=[
            GridSearchRowResponse(
                params=r.params,
                sharpe_ratio=r.sharpe_ratio,
                total_return=r.total_return,
                max_drawdown=r.max_drawdown,
                win_rate=r.win_rate,
                profit_factor=r.profit_factor,
                total_trades=r.total_trades,
            )
            for r in result.results
        ],
        best_params=result.best_params,
        errors=result.errors,
    )


@router.post("/walk-forward", response_model=WalkForwardResponse)
def run_walk_forward(req: WalkForwardRequest) -> WalkForwardResponse:
    from core.backtest.optimize import walk_forward

    result = walk_forward(
        strategy_name=req.strategy,
        symbol=req.symbol,
        days=req.days,
        param_grid=req.param_grid,
        metric=req.metric,
        n_folds=req.n_folds,
        train_pct=req.train_pct,
    )

    return WalkForwardResponse(
        strategy=result.strategy,
        symbol=result.symbol,
        metric=result.metric,
        days=result.days,
        n_folds=result.n_folds,
        folds=[
            WalkForwardFoldResponse(
                fold=f.fold,
                train_range=f.train_range,
                test_range=f.test_range,
                best_params=f.best_params,
                train_sharpe=f.train_sharpe,
                train_return=f.train_return,
                test_sharpe=f.test_sharpe,
                test_return=f.test_return,
                overfit=f.overfit,
            )
            for f in result.folds
        ],
        avg_test_sharpe=result.avg_test_sharpe,
        avg_test_return=result.avg_test_return,
        errors=result.errors,
    )
