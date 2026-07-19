"""
Parameter optimization — grid search and walk-forward analysis.

Usage::

    from core.backtest.optimize import grid_search, walk_forward

    result = grid_search(
        strategy_name="smart_hodler",
        symbol="BTCUSDT",
        days=90,
        param_grid={"ema_fast": [20, 50], "adx_threshold": [20, 25, 30]},
        metric="sharpe_ratio",
    )
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional

import pandas as pd
import structlog

from core.backtest import BacktestRunner
from core.backtest.metrics import compute_metrics
from core.models import BacktestMetrics
from core.strategies.registry import get_registry

logger = structlog.get_logger(__name__)

RANKABLE_METRICS = {
    "sharpe_ratio": True,
    "total_return": True,
    "win_rate": True,
    "profit_factor": True,
    "max_drawdown": True,  # closer to 0 (less negative) is better
}


@dataclass
class GridSearchRow:
    params: Dict[str, Any]
    sharpe_ratio: Optional[float] = None
    total_return: float = 0.0
    max_drawdown: float = 0.0
    win_rate: float = 0.0
    profit_factor: Optional[float] = None
    total_trades: int = 0


@dataclass
class OptimizationResult:
    strategy: str
    symbol: str
    metric: str
    days: int
    total_combos: int = 0
    results: List[GridSearchRow] = field(default_factory=list)
    best_params: Dict[str, Any] = field(default_factory=dict)
    errors: List[str] = field(default_factory=list)


@dataclass
class WalkForwardFold:
    fold: int
    train_range: str
    test_range: str
    best_params: Dict[str, Any] = field(default_factory=dict)
    train_sharpe: Optional[float] = None
    train_return: float = 0.0
    test_sharpe: Optional[float] = None
    test_return: float = 0.0
    overfit: bool = False


@dataclass
class WalkForwardResult:
    strategy: str
    symbol: str
    metric: str
    days: int
    n_folds: int = 0
    folds: List[WalkForwardFold] = field(default_factory=list)
    avg_test_sharpe: Optional[float] = None
    avg_test_return: float = 0.0
    errors: List[str] = field(default_factory=list)


def _row_from_metrics(params: Dict[str, Any], m: BacktestMetrics) -> GridSearchRow:
    return GridSearchRow(
        params=params,
        sharpe_ratio=float(m.sharpe_ratio) if m.sharpe_ratio is not None else None,
        total_return=float(m.total_return),
        max_drawdown=float(m.max_drawdown),
        win_rate=float(m.win_rate),
        profit_factor=float(m.profit_factor) if m.profit_factor is not None else None,
        total_trades=m.total_trades,
    )


def _sort_key(row: GridSearchRow, metric: str):
    higher_is_better = RANKABLE_METRICS.get(metric, True)
    val = getattr(row, metric)
    if val is None:
        return float("-inf") if higher_is_better else float("inf")
    return val if higher_is_better else -val


def _load_market_data(symbol: str, strategy_config: dict, days: int):
    from core.data.binance_client import BinanceClient
    from core.data.loader import DataLoader
    from db import get_session

    primary_tf = strategy_config.get("timeframe_primary", "15m")
    secondary_tf = strategy_config.get("timeframe_confirmation", "1h")

    with get_session() as session:
        client = BinanceClient()
        loader = DataLoader(session, client)
        md = loader.get_multi_timeframe_market_data(
            symbol=symbol,
            primary_timeframe=primary_tf,
            secondary_timeframe=secondary_tf,
            strategy_config=strategy_config,
            lookback_days=days,
        )

    return md


def _run_single_backtest(
    strategy_name: str,
    symbol: str,
    base_config: dict,
    param_overrides: Dict[str, Any],
    primary_candles: pd.DataFrame,
    primary_indicators: dict,
    secondary_timeframe: Optional[str],
    secondary_candles: Optional[pd.DataFrame],
    secondary_indicators: Optional[dict],
) -> Optional[BacktestMetrics]:
    from core.config import get_settings

    settings = get_settings()
    registry = get_registry()

    config = {**base_config, **param_overrides}
    strategy_cls = registry[strategy_name]
    strategy = strategy_cls(config)

    # Task 9.4: optimize against per-slot capital derived from the real
    # account (live) or configured total, matching compare/multi-strategy.
    from core.backtest.capital import resolve_total_capital

    bt_config = settings.backtest.model_dump()
    bt_config["initial_capital"] = float(
        resolve_total_capital(settings) / settings.risk.max_concurrent_positions
    )

    runner = BacktestRunner(
        strategy=strategy,
        strategy_config=config,
        risk_config=settings.risk.model_dump(),
        execution_config=settings.execution.model_dump(),
        backtest_config=bt_config,
    )

    bt_result = runner.run(
        symbol=symbol,
        primary_candles=primary_candles,
        primary_indicators=primary_indicators,
        secondary_timeframe=secondary_timeframe,
        secondary_candles=secondary_candles,
        secondary_indicators=secondary_indicators or {},
    )

    return compute_metrics(bt_result)


def grid_search(
    strategy_name: str,
    symbol: str,
    days: int = 90,
    param_grid: Optional[Dict[str, List[Any]]] = None,
    metric: str = "sharpe_ratio",
) -> OptimizationResult:
    """
    Run backtests for all parameter combinations and rank by metric.
    """
    if metric not in RANKABLE_METRICS:
        metric = "sharpe_ratio"

    result = OptimizationResult(
        strategy=strategy_name, symbol=symbol, metric=metric, days=days,
    )

    registry = get_registry()
    if strategy_name not in registry:
        result.errors.append(f"Unknown strategy: {strategy_name}")
        return result

    from core.config import get_settings
    settings = get_settings()
    cfg = settings.strategies.get_strategy_config(strategy_name)
    if cfg is None:
        result.errors.append(f"No config for strategy: {strategy_name}")
        return result

    base_config = cfg if isinstance(cfg, dict) else cfg.model_dump()

    if not param_grid:
        result.errors.append("Empty parameter grid")
        return result

    param_names = list(param_grid.keys())
    param_values = list(param_grid.values())
    combos = list(itertools.product(*param_values))
    result.total_combos = len(combos)

    md = _load_market_data(symbol, base_config, days)
    if md.candles.empty:
        result.errors.append("No candle data available")
        return result

    for combo in combos:
        overrides = dict(zip(param_names, combo))
        try:
            metrics = _run_single_backtest(
                strategy_name=strategy_name,
                symbol=symbol,
                base_config=base_config,
                param_overrides=overrides,
                primary_candles=md.candles,
                primary_indicators=md.indicators,
                secondary_timeframe=md.secondary_timeframe,
                secondary_candles=md.secondary_candles,
                secondary_indicators=md.secondary_indicators,
            )
            if metrics:
                result.results.append(_row_from_metrics(overrides, metrics))
        except Exception as exc:
            logger.warning("optimize: combo failed", params=overrides, error=str(exc))

    result.results.sort(key=lambda r: _sort_key(r, metric), reverse=True)

    if result.results:
        result.best_params = result.results[0].params

    return result


def walk_forward(
    strategy_name: str,
    symbol: str,
    days: int = 90,
    param_grid: Optional[Dict[str, List[Any]]] = None,
    metric: str = "sharpe_ratio",
    n_folds: int = 3,
    train_pct: float = 0.7,
) -> WalkForwardResult:
    """
    Walk-forward optimization: split data into folds, optimize on train, validate on test.
    """
    if metric not in RANKABLE_METRICS:
        metric = "sharpe_ratio"

    result = WalkForwardResult(
        strategy=strategy_name, symbol=symbol, metric=metric,
        days=days, n_folds=n_folds,
    )

    registry = get_registry()
    if strategy_name not in registry:
        result.errors.append(f"Unknown strategy: {strategy_name}")
        return result

    from core.config import get_settings
    settings = get_settings()
    cfg = settings.strategies.get_strategy_config(strategy_name)
    if cfg is None:
        result.errors.append(f"No config for strategy: {strategy_name}")
        return result

    base_config = cfg if isinstance(cfg, dict) else cfg.model_dump()

    if not param_grid:
        result.errors.append("Empty parameter grid")
        return result

    md = _load_market_data(symbol, base_config, days)
    if md.candles.empty:
        result.errors.append("No candle data available")
        return result

    total_rows = len(md.candles)
    fold_size = total_rows // n_folds
    if fold_size < 50:
        result.errors.append(f"Not enough data for {n_folds} folds ({total_rows} candles)")
        return result

    param_names = list(param_grid.keys())
    param_values = list(param_grid.values())
    combos = list(itertools.product(*param_values))

    test_sharpes: List[float] = []
    test_returns: List[float] = []

    for fold_idx in range(n_folds):
        fold_start = fold_idx * fold_size
        fold_end = fold_start + fold_size if fold_idx < n_folds - 1 else total_rows
        fold_candles = md.candles.iloc[fold_start:fold_end]

        train_end = fold_start + int((fold_end - fold_start) * train_pct)
        train_candles = md.candles.iloc[fold_start:train_end]
        test_candles = md.candles.iloc[train_end:fold_end]

        if len(train_candles) < 30 or len(test_candles) < 10:
            continue

        train_range = f"{train_candles.index[0]} → {train_candles.index[-1]}"
        test_range = f"{test_candles.index[0]} → {test_candles.index[-1]}"

        def _slice_indicators(candles_slice: pd.DataFrame) -> dict:
            sliced = {}
            for k, v in md.indicators.items():
                if isinstance(v, pd.Series):
                    sliced[k] = v.reindex(candles_slice.index).dropna()
                else:
                    sliced[k] = v
            return sliced

        train_indicators = _slice_indicators(train_candles)

        # Grid search on train data
        best_row: Optional[GridSearchRow] = None
        best_overrides: Dict[str, Any] = {}

        for combo in combos:
            overrides = dict(zip(param_names, combo))
            try:
                metrics = _run_single_backtest(
                    strategy_name=strategy_name,
                    symbol=symbol,
                    base_config=base_config,
                    param_overrides=overrides,
                    primary_candles=train_candles,
                    primary_indicators=train_indicators,
                    secondary_timeframe=md.secondary_timeframe,
                    secondary_candles=md.secondary_candles,
                    secondary_indicators=md.secondary_indicators,
                )
                if metrics:
                    row = _row_from_metrics(overrides, metrics)
                    if best_row is None or _sort_key(row, metric) > _sort_key(best_row, metric):
                        best_row = row
                        best_overrides = overrides
            except Exception:
                pass

        if best_row is None:
            continue

        # Validate best params on test data
        test_indicators = _slice_indicators(test_candles)
        fold_result = WalkForwardFold(
            fold=fold_idx + 1,
            train_range=str(train_range),
            test_range=str(test_range),
            best_params=best_overrides,
            train_sharpe=best_row.sharpe_ratio,
            train_return=best_row.total_return,
        )

        try:
            test_metrics = _run_single_backtest(
                strategy_name=strategy_name,
                symbol=symbol,
                base_config=base_config,
                param_overrides=best_overrides,
                primary_candles=test_candles,
                primary_indicators=test_indicators,
                secondary_timeframe=md.secondary_timeframe,
                secondary_candles=md.secondary_candles,
                secondary_indicators=md.secondary_indicators,
            )
            if test_metrics:
                fold_result.test_sharpe = float(test_metrics.sharpe_ratio) if test_metrics.sharpe_ratio else None
                fold_result.test_return = float(test_metrics.total_return)

                if fold_result.train_sharpe and fold_result.test_sharpe is not None:
                    if fold_result.train_sharpe > 0 and fold_result.test_sharpe < fold_result.train_sharpe * 0.5:
                        fold_result.overfit = True

                if fold_result.test_sharpe is not None:
                    test_sharpes.append(fold_result.test_sharpe)
                test_returns.append(fold_result.test_return)

        except Exception as exc:
            logger.warning("walk_forward: test fold failed", fold=fold_idx, error=str(exc))

        result.folds.append(fold_result)

    if test_sharpes:
        result.avg_test_sharpe = round(sum(test_sharpes) / len(test_sharpes), 4)
    if test_returns:
        result.avg_test_return = round(sum(test_returns) / len(test_returns), 4)

    return result
