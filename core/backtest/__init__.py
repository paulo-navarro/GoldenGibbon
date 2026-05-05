"""
Backtest engine – candle-by-candle simulation loop.

Orchestrates the full pipeline for each bar:

    check stops → strategy.decide → risk.evaluate → executor.execute
    → update stops → take snapshot

The runner is **pure in-memory** — the caller loads candles and
pre-computes indicators before calling ``run()``.  No database access
happens inside the loop.
"""

from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional

import pandas as pd
import structlog

from core.execution.paper import PaperExecutor
from core.models import (
    BacktestResult,
    MarketData,
    PortfolioSnapshot,
    Signal,
    StrategyState,
    Trade,
)
from core.config import get_settings
from core.portfolio import PortfolioManager
from core.risk import RiskEngine
from core.strategies.base import Strategy


logger = structlog.get_logger(__name__)


class BacktestRunner:
    """
    Candle-by-candle backtest engine.

    Usage::

        runner = BacktestRunner(strategy, strategy_config)
        result = runner.run(
            symbol="BTCUSDT",
            primary_candles=df_15m,
            primary_indicators=indicators_15m,
            secondary_timeframe="1h",
            secondary_candles=df_1h,
            secondary_indicators=indicators_1h,
        )
    """

    # ── Construction ─────────────────────────────────────────────────

    def __init__(
        self,
        strategy: Strategy,
        strategy_config: Dict[str, Any],
        risk_config: Optional[Dict[str, Any]] = None,
        execution_config: Optional[Dict[str, Any]] = None,
        backtest_config: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        Args:
            strategy: An initialised Strategy subclass.
            strategy_config: Strategy section from strategies.yaml
                             (as a plain dict).
            risk_config: Global risk section (optional).
            execution_config: Execution section – slippage / fees (optional).
            backtest_config: Backtest section – initial_capital etc. (optional).
        """
        self._strategy = strategy
        self._strategy_config = strategy_config
        self._risk_config = risk_config or {}
        self._execution_config = execution_config or {}
        self._backtest_config = backtest_config or {}

        # Resolved parameters
        self._initial_capital = Decimal(
            str(self._backtest_config.get("initial_capital", 10000))
        )
        self._warmup = int(
            self._strategy_config.get(
                "warmup_periods",
                max(
                    self._strategy_config.get("ema_slow", 200),
                    self._strategy_config.get("bb_period", 20),
                ),
            )
        )

    # ── Main entry point ─────────────────────────────────────────────

    def run(
        self,
        symbol: str,
        primary_candles: pd.DataFrame,
        primary_indicators: Dict[str, pd.Series],
        secondary_timeframe: Optional[str] = None,
        secondary_candles: Optional[pd.DataFrame] = None,
        secondary_indicators: Optional[Dict[str, pd.Series]] = None,
    ) -> BacktestResult:
        """
        Execute a full backtest over the supplied data.

        Args:
            symbol: Trading pair (e.g. ``"BTCUSDT"``).
            primary_candles: OHLCV DataFrame indexed by ``open_time``.
            primary_indicators: Pre-computed indicator Series (full length).
            secondary_timeframe: Confirmation timeframe string (e.g. ``"1h"``).
            secondary_candles: Confirmation OHLCV DataFrame (optional).
            secondary_indicators: Confirmation indicators dict (optional).

        Returns:
            ``BacktestResult`` with portfolio, trades, and equity curve.
        """
        timeframe = self._strategy_config.get("timeframe_primary", "15m")

        # ── Wire up internal components ──────────────────────────────
        pm = PortfolioManager(
            initial_capital=self._initial_capital,
            taker_fee=Decimal(str(self._execution_config.get("taker_fee", 0.001))),
        )
        risk_engine = RiskEngine(
            strategy_name=self._strategy.name,
            strategy_config=self._strategy_config,
            risk_config=self._risk_config,
            shorts_enabled=get_settings().shorts.enabled,
        )
        executor = PaperExecutor(
            strategy_name=self._strategy.name,
            portfolio_manager=pm,
            slippage=Decimal(str(self._execution_config.get("slippage", 0.001))),
            simulate_slippage=True,
        )

        # Reset strategy state
        self._strategy.reset()

        n_candles = len(primary_candles)

        logger.info(
            "backtest.start",
            strategy=self._strategy.name,
            symbol=symbol,
            timeframe=timeframe,
            total_candles=n_candles,
            warmup=self._warmup,
            initial_capital=str(self._initial_capital),
        )

        # ── Main loop ────────────────────────────────────────────────
        candles_processed = 0

        for i in range(self._warmup, n_candles):
            candles_processed += 1

            # ── 1. Build windowed MarketData ─────────────────────────
            market_data = self._build_market_data(
                symbol=symbol,
                timeframe=timeframe,
                primary_candles=primary_candles,
                primary_indicators=primary_indicators,
                idx=i,
                secondary_timeframe=secondary_timeframe,
                secondary_candles=secondary_candles,
                secondary_indicators=secondary_indicators,
            )

            candle_time = primary_candles.index[i]
            if hasattr(candle_time, "to_pydatetime"):
                candle_time = candle_time.to_pydatetime()

            close = Decimal(str(primary_candles["close"].iloc[i]))

            logger.debug(
                "backtest.candle",
                symbol=symbol,
                idx=i,
                close=str(close),
                timestamp=str(candle_time),
            )

            # ── 2. Check stops ───────────────────────────────────────
            stop_result = risk_engine.check_stops(market_data, pm.portfolio)

            if stop_result.stop_hit:
                logger.info(
                    "backtest.stop_hit",
                    symbol=symbol,
                    exit_reason=str(stop_result.decision.exit_reason),
                    close=str(close),
                    cooldown=stop_result.cooldown_candles,
                )
                executor.execute(stop_result.decision, candle_time)
                # Enter cooldown via the strategy's own state
                if stop_result.cooldown_candles and stop_result.cooldown_candles > 0:
                    self._strategy._cooldown_remaining = stop_result.cooldown_candles
                    self._strategy._state = StrategyState.COOLDOWN
            elif pm.has_position(symbol):
                # Update stop levels on the position
                pm.update_stops(
                    symbol,
                    highest_close=stop_result.highest_close,
                    trailing_stop_price=stop_result.trailing_stop_price,
                )

            # ── 3. Strategy decision ─────────────────────────────────
            if not stop_result.stop_hit:
                signal = self._strategy.evaluate(market_data, pm.portfolio)

                if signal != Signal.HOLD:
                    logger.info(
                        "strategy.signal",
                        strategy=self._strategy.name,
                        symbol=symbol,
                        signal=signal.value,
                        state=self._strategy.state.value,
                    )

                # ── 4. Risk evaluation ───────────────────────────────
                decision = risk_engine.evaluate(signal, market_data, pm.portfolio)

                # ── 5. Execute ───────────────────────────────────────
                executor.execute(decision, candle_time)

            # ── 6. Equity snapshot ───────────────────────────────────
            pm.take_snapshot(candle_time, {symbol: close})

        # ── Build result ─────────────────────────────────────────────
        if candles_processed > 0:
            start_time = primary_candles.index[self._warmup]
            end_time = primary_candles.index[-1]
            if hasattr(start_time, "to_pydatetime"):
                start_time = start_time.to_pydatetime()
            if hasattr(end_time, "to_pydatetime"):
                end_time = end_time.to_pydatetime()
        else:
            # No candles processed — use first/last available times
            start_time = primary_candles.index[0]
            end_time = primary_candles.index[-1]
            if hasattr(start_time, "to_pydatetime"):
                start_time = start_time.to_pydatetime()
            if hasattr(end_time, "to_pydatetime"):
                end_time = end_time.to_pydatetime()

        # ── Benchmark prices for Buy & Hold comparison ────────────
        benchmark_first: Decimal | None = None
        benchmark_last: Decimal | None = None
        if candles_processed > 0:
            benchmark_first = Decimal(str(primary_candles["close"].iloc[self._warmup]))
            benchmark_last = Decimal(str(primary_candles["close"].iloc[-1]))

        logger.info(
            "backtest.complete",
            strategy=self._strategy.name,
            symbol=symbol,
            candles_processed=candles_processed,
            total_trades=len(pm.portfolio.trade_history),
            final_equity=str(pm.portfolio.equity),
        )

        return BacktestResult(
            strategy_name=self._strategy.name,
            symbol=symbol,
            timeframe=timeframe,
            start_time=start_time,
            end_time=end_time,
            candles_processed=candles_processed,
            portfolio=pm.portfolio,
            trades=list(pm.portfolio.trade_history),
            equity_curve=list(pm.portfolio.equity_curve),
            benchmark_first_price=benchmark_first,
            benchmark_last_price=benchmark_last,
        )

    # ── Private helpers ──────────────────────────────────────────────

    def _build_market_data(
        self,
        symbol: str,
        timeframe: str,
        primary_candles: pd.DataFrame,
        primary_indicators: Dict[str, pd.Series],
        idx: int,
        secondary_timeframe: Optional[str],
        secondary_candles: Optional[pd.DataFrame],
        secondary_indicators: Optional[Dict[str, pd.Series]],
    ) -> MarketData:
        """
        Build a ``MarketData`` windowed up to candle *idx*.

        Primary data is sliced to ``[0 : idx+1]``.  Secondary data
        is aligned so that the last secondary candle has
        ``open_time <= primary_candles.index[idx]``.
        """
        # Primary slice
        p_slice = primary_candles.iloc[: idx + 1]
        p_ind = {
            k: v.iloc[: idx + 1]
            for k, v in primary_indicators.items()
        }

        # Secondary alignment
        sec_tf = None
        sec_candles = None
        sec_ind: Dict[str, pd.Series] = {}

        if (
            secondary_timeframe is not None
            and secondary_candles is not None
            and secondary_indicators is not None
        ):
            current_time = primary_candles.index[idx]
            # Find last secondary candle at or before current primary time
            mask = secondary_candles.index <= current_time
            if mask.any():
                sec_tf = secondary_timeframe
                sec_candles = secondary_candles.loc[mask]
                sec_end = len(sec_candles)
                sec_ind = {}
                for k, v in secondary_indicators.items():
                    # Align by position: take first sec_end values
                    # (secondary index is sorted and matches candles)
                    if hasattr(v, "iloc"):
                        sec_ind[k] = v.iloc[:sec_end]

        return MarketData(
            symbol=symbol,
            timeframe=timeframe,
            candles=p_slice,
            indicators=p_ind,
            secondary_timeframe=sec_tf,
            secondary_candles=sec_candles,
            secondary_indicators=sec_ind,
        )
