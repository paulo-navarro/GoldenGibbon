"""
Strategy comparison — run backtests for multiple strategies on the same
data window and return side-by-side metrics.

Usage::

    from core.backtest.compare import compare_strategies

    result = compare_strategies(
        symbols=["BTCUSDT", "ETHUSDT"],
        days=90,
        strategy_names=["smart_hodler", "mean_reversion"],
    )
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional

import structlog

from core.backtest import BacktestRunner
from core.backtest.metrics import compute_metrics
from core.models import BacktestMetrics, PortfolioSnapshot
from core.strategies.registry import get_registry

logger = structlog.get_logger(__name__)


@dataclass
class ComparisonRow:
    strategy: str
    symbol: str
    metrics: BacktestMetrics
    equity_curve: List[PortfolioSnapshot] = field(default_factory=list)


@dataclass
class ComparisonResult:
    rows: List[ComparisonRow] = field(default_factory=list)
    date_range: Optional[str] = None
    days: int = 0
    errors: List[Dict[str, str]] = field(default_factory=list)


def compare_strategies(
    symbols: List[str] | None = None,
    days: int = 90,
    strategy_names: List[str] | None = None,
) -> ComparisonResult:
    """
    Run backtests for each (strategy, symbol) pair and collect metrics.

    Args:
        symbols: Trading pairs to test. Defaults to all enabled symbols.
        days: Lookback window in days.
        strategy_names: Strategies to compare. Defaults to all registered.

    Returns:
        ``ComparisonResult`` with one row per (strategy, symbol).
    """
    from core.config import get_settings
    from core.data.binance_client import BinanceClient
    from core.data.loader import DataLoader
    from db import get_session

    settings = get_settings()
    registry = get_registry()

    if strategy_names is None:
        strategy_names = list(registry.keys())

    if symbols is None:
        symbols = [s.symbol for s in settings.enabled_symbols]

    result = ComparisonResult(days=days)

    from core.backtest.capital import resolve_total_capital

    total_capital = resolve_total_capital(settings)
    max_pos = settings.risk.max_concurrent_positions
    slot_capital = total_capital / max_pos

    for strategy_name in strategy_names:
        if strategy_name not in registry:
            result.errors.append({
                "strategy": strategy_name,
                "error": f"unknown strategy: {strategy_name}",
            })
            continue

        strat_cfg = settings.strategies.get_strategy_config(strategy_name)
        if strat_cfg is None:
            result.errors.append({
                "strategy": strategy_name,
                "error": "no config found",
            })
            continue

        strategy_config = strat_cfg if isinstance(strat_cfg, dict) else strat_cfg.model_dump()
        primary_tf = strategy_config.get("timeframe_primary", "15m")
        secondary_tf = strategy_config.get("timeframe_confirmation", "1h")

        strategy_cls = registry[strategy_name]

        for symbol in symbols:
            try:
                strategy = strategy_cls(strategy_config)

                bt_config = settings.backtest.model_dump()
                bt_config["initial_capital"] = float(slot_capital)

                runner = BacktestRunner(
                    strategy=strategy,
                    strategy_config=strategy_config,
                    risk_config=settings.risk.model_dump(),
                    execution_config=settings.execution.model_dump(),
                    backtest_config=bt_config,
                )

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

                if md.candles.empty:
                    result.errors.append({
                        "strategy": strategy_name,
                        "symbol": symbol,
                        "error": "no candles available",
                    })
                    continue

                bt_result = runner.run(
                    symbol=symbol,
                    primary_candles=md.candles,
                    primary_indicators=md.indicators,
                    secondary_timeframe=md.secondary_timeframe,
                    secondary_candles=md.secondary_candles,
                    secondary_indicators=md.secondary_indicators or {},
                )

                metrics = compute_metrics(bt_result)

                result.rows.append(ComparisonRow(
                    strategy=strategy_name,
                    symbol=symbol,
                    metrics=metrics,
                    equity_curve=bt_result.equity_curve,
                ))

                if result.date_range is None and bt_result.start_time and bt_result.end_time:
                    result.date_range = (
                        f"{bt_result.start_time.strftime('%Y-%m-%d')} → "
                        f"{bt_result.end_time.strftime('%Y-%m-%d')}"
                    )

                logger.info(
                    "compare: backtest complete",
                    strategy=strategy_name,
                    symbol=symbol,
                    total_return=str(metrics.total_return),
                    trades=metrics.total_trades,
                )

            except Exception as exc:
                logger.error(
                    "compare: backtest failed",
                    strategy=strategy_name,
                    symbol=symbol,
                    error=str(exc),
                    exc_info=True,
                )
                result.errors.append({
                    "strategy": strategy_name,
                    "symbol": symbol,
                    "error": str(exc),
                })

    return result
