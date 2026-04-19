"""
Multi-strategy backtest — run all strategies on the same data with
regime-based allocation, producing a combined equity curve and
per-strategy breakdown.

Usage::

    from core.backtest.multi_strategy import run_multi_strategy_backtest

    result = run_multi_strategy_backtest(symbols=["BTCUSDT"], days=90)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional

import pandas as pd
import structlog

from core.allocation import compute_allocations
from core.backtest import BacktestRunner
from core.backtest.metrics import compute_metrics
from core.models import BacktestMetrics, PortfolioSnapshot
from core.regime import MarketRegime, RegimeClassification, RegimeDetector
from core.strategies.registry import get_registry

logger = structlog.get_logger(__name__)


@dataclass
class RegimeEvent:
    timestamp: str
    regime: str
    confidence: float
    adx: float


@dataclass
class StrategyBreakdown:
    strategy: str
    symbol: str
    allocated_capital: str
    metrics: BacktestMetrics


@dataclass
class MultiStrategyResult:
    combined_equity_curve: List[Dict[str, Any]] = field(default_factory=list)
    per_strategy: List[StrategyBreakdown] = field(default_factory=list)
    regime_timeline: List[RegimeEvent] = field(default_factory=list)
    total_initial_capital: str = "0"
    total_final_equity: str = "0"
    total_return_pct: str = "0"
    date_range: Optional[str] = None
    days: int = 0
    errors: List[Dict[str, str]] = field(default_factory=list)


def run_multi_strategy_backtest(
    symbols: List[str] | None = None,
    days: int = 90,
    strategy_names: List[str] | None = None,
    regime_shift_pct: float | None = None,
) -> MultiStrategyResult:
    """
    Run all strategies on the same data window with regime-based allocation.

    Each strategy gets its own ``BacktestRunner`` with allocated capital.
    The combined equity curve sums all strategies' equity at each timestamp.
    A regime timeline is computed from the primary symbol's ADX.

    Args:
        symbols: Trading pairs. Defaults to all enabled.
        days: Lookback window in days.
        strategy_names: Strategies to include. Defaults to all registered.
        regime_shift_pct: Override for regime shift percentage. None = use config.
    """
    from core.config import get_settings
    from core.data.binance_client import BinanceClient
    from core.data.loader import DataLoader
    from db import get_session

    settings = get_settings()
    registry = get_registry()
    regime_cfg = settings.regime

    if strategy_names is None:
        strategy_names = [n for n in registry if _strategy_enabled(n, settings)]

    if symbols is None:
        symbols = [s.symbol for s in settings.enabled_symbols]

    if regime_shift_pct is None:
        regime_shift_pct = regime_cfg.regime_shift_pct

    result = MultiStrategyResult(days=days)
    total_capital = Decimal(str(settings.backtest.initial_capital))
    result.total_initial_capital = str(total_capital)

    # Build strategy configs
    valid_strategies: List[str] = []
    strategy_configs: Dict[str, dict] = {}
    for name in strategy_names:
        if name not in registry:
            result.errors.append({"strategy": name, "error": "unknown strategy"})
            continue
        cfg = settings.strategies.get_strategy_config(name)
        if cfg is None:
            result.errors.append({"strategy": name, "error": "no config"})
            continue
        strategy_configs[name] = cfg if isinstance(cfg, dict) else cfg.model_dump()
        valid_strategies.append(name)

    if not valid_strategies or not symbols:
        return result

    # Detect regime from primary symbol for allocation + timeline
    primary_symbol = symbols[0]
    regime_timeline: List[RegimeEvent] = []
    regime_for_alloc: str | None = None
    regime_confidence: float = 0.0

    try:
        primary_cfg = strategy_configs[valid_strategies[0]]
        primary_tf = primary_cfg.get("timeframe_primary", "15m")

        with get_session() as session:
            client = BinanceClient()
            loader = DataLoader(session, client)
            primary_md = loader.get_multi_timeframe_market_data(
                symbol=primary_symbol,
                primary_timeframe=primary_tf,
                secondary_timeframe=primary_cfg.get("timeframe_confirmation", "1h"),
                strategy_config=primary_cfg,
                lookback_days=days,
            )

        if not primary_md.candles.empty and "adx" in primary_md.indicators:
            detector = RegimeDetector(
                trending_threshold=regime_cfg.adx_trending_threshold,
                ranging_threshold=regime_cfg.adx_ranging_threshold,
                smoothing_window=regime_cfg.smoothing_window,
            )

            # Build regime timeline by scanning ADX candle by candle
            adx_series = primary_md.indicators["adx"].dropna()
            warmup = max(int(primary_cfg.get("ema_slow", 200)), int(primary_cfg.get("bb_period", 20)))
            prev_regime: str | None = None

            for i in range(warmup, len(adx_series)):
                window = adx_series.iloc[max(0, i - regime_cfg.smoothing_window + 1):i + 1]
                indicators_slice = {"adx": window}
                classification = detector.detect(indicators_slice)

                if classification.regime.value != prev_regime:
                    ts = adx_series.index[i]
                    if hasattr(ts, "isoformat"):
                        ts_str = ts.isoformat()
                    else:
                        ts_str = str(ts)

                    regime_timeline.append(RegimeEvent(
                        timestamp=ts_str,
                        regime=classification.regime.value,
                        confidence=round(classification.confidence, 3),
                        adx=round(classification.smoothed_adx, 2),
                    ))
                    prev_regime = classification.regime.value

            # Use final regime for allocation
            if adx_series.size > 0:
                final_class = detector.detect({"adx": adx_series})
                regime_for_alloc = final_class.regime.value
                regime_confidence = final_class.confidence

    except Exception as exc:
        logger.warning("multi_backtest: regime detection failed", error=str(exc))

    result.regime_timeline = regime_timeline

    # Compute allocations with regime adjustment
    allocations = compute_allocations(
        total_capital=total_capital,
        enabled_strategies=valid_strategies,
        strategy_configs=strategy_configs,
        symbols=symbols,
        regime=regime_for_alloc,
        regime_confidence=regime_confidence,
        regime_shift_pct=regime_shift_pct,
        strategy_regime_map=regime_cfg.strategy_regime_map,
    )

    # Run each strategy independently
    all_equity_curves: Dict[str, List[PortfolioSnapshot]] = {}

    for strategy_name in valid_strategies:
        strategy_config = strategy_configs[strategy_name]
        primary_tf = strategy_config.get("timeframe_primary", "15m")
        secondary_tf = strategy_config.get("timeframe_confirmation", "1h")
        strategy_cls = registry[strategy_name]

        for symbol in symbols:
            key = f"{strategy_name}:{symbol}"
            alloc_key = (strategy_name, symbol)
            allocated = allocations.get(alloc_key, total_capital / len(valid_strategies) / len(symbols))

            try:
                strategy = strategy_cls(strategy_config)

                bt_config = settings.backtest.model_dump()
                bt_config["initial_capital"] = float(allocated)

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
                    result.errors.append({"strategy": strategy_name, "symbol": symbol, "error": "no candles"})
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
                result.per_strategy.append(StrategyBreakdown(
                    strategy=strategy_name,
                    symbol=symbol,
                    allocated_capital=str(allocated),
                    metrics=metrics,
                ))
                all_equity_curves[key] = bt_result.equity_curve

                if result.date_range is None and bt_result.start_time and bt_result.end_time:
                    result.date_range = (
                        f"{bt_result.start_time.strftime('%Y-%m-%d')} → "
                        f"{bt_result.end_time.strftime('%Y-%m-%d')}"
                    )

            except Exception as exc:
                logger.error("multi_backtest: strategy failed", strategy=strategy_name, symbol=symbol, error=str(exc))
                result.errors.append({"strategy": strategy_name, "symbol": symbol, "error": str(exc)})

    # Merge equity curves by timestamp
    result.combined_equity_curve = _merge_equity_curves(all_equity_curves)

    # Total final equity
    if result.combined_equity_curve:
        final_eq = Decimal(str(result.combined_equity_curve[-1]["equity"]))
        result.total_final_equity = str(final_eq)
        if total_capital > 0:
            ret = (final_eq - total_capital) / total_capital * 100
            result.total_return_pct = str(ret.quantize(Decimal("0.01")))

    return result


def _merge_equity_curves(
    curves: Dict[str, List[PortfolioSnapshot]],
) -> List[Dict[str, Any]]:
    """Sum equity across all strategies at each timestamp."""
    if not curves:
        return []

    # Collect all timestamps
    ts_equity: Dict[datetime, Decimal] = {}

    for key, snapshots in curves.items():
        for snap in snapshots:
            ts = snap.timestamp
            ts_equity[ts] = ts_equity.get(ts, Decimal("0")) + snap.total_equity

    sorted_points = sorted(ts_equity.items())

    return [
        {
            "timestamp": ts.isoformat() if hasattr(ts, "isoformat") else str(ts),
            "equity": str(eq.quantize(Decimal("0.01"))),
        }
        for ts, eq in sorted_points
    ]


def _strategy_enabled(name: str, settings) -> bool:  # noqa: ANN001
    cfg = settings.strategies.get_strategy_config(name)
    if cfg is None:
        return False
    if isinstance(cfg, dict):
        return cfg.get("enabled", False)
    return getattr(cfg, "enabled", False)
