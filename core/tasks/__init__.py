"""
Celery tasks for the trading pipeline.

Each task is implemented incrementally as the corresponding kanban item lands:

* :func:`fetch_candles` → **3.4** ✓
* :func:`run_strategy_tick` → **3.5** ✓
* :func:`run_reconciliation` → **3.9** (stub)

The module is auto-discovered by :mod:`core.celery_app` via
``app.autodiscover_tasks(["core.tasks"])``.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Dict, Optional, Tuple

import structlog

from core.celery_app import app

logger = structlog.get_logger(__name__)

# ── Constants ────────────────────────────────────────────────────────────────

# Number of recent candles to fetch per tick (task 3.4).
_FETCH_LIMIT = 5

# Lookback window for loading candles from the DB cache before computing
# indicators.  Must be large enough for the slowest indicator warmup
# (ema_slow=200 on 15m ≈ 2.1 days).  7 days gives comfortable headroom.
_LOOKBACK_DAYS = 7

# Lifetime (seconds) of the per-candle idempotency lock in Redis.
# Must be longer than the tick execution time (~5 s) but short enough
# that a stuck lock self-heals.  120 s is conservative.
_TICK_LOCK_TTL = 120


# ── Tick idempotency (task 3.6) ──────────────────────────────────────────────


def _acquire_tick_lock(candle_open_time_iso: str | None) -> bool:
    """
    Acquire a Redis ``SET NX EX`` lock for the current tick.

    When both Celery Beat *and* the WebSocket stream trigger
    ``run_strategy_tick`` for the same candle, only the first caller
    wins.  The lock key includes the most recent candle's open_time so
    that different candles never collide.

    Returns ``True`` if the lock was acquired (caller should proceed),
    or ``False`` if the tick is already being handled.
    """
    if candle_open_time_iso is None:
        # No candle time available → always proceed (tests, first run).
        return True

    import os
    try:
        import redis as redis_lib
    except ImportError:
        return True  # Redis not installed → skip locking (tests)

    url = os.environ.get("REDIS_URL", "redis://redis:6379/0")
    try:
        client = redis_lib.Redis.from_url(url, socket_connect_timeout=2)
        key = f"gg:tick_lock:{candle_open_time_iso}"
        acquired = client.set(key, "1", nx=True, ex=_TICK_LOCK_TTL)
        return bool(acquired)
    except Exception as exc:
        logger.warning("tick_lock.failed", error=str(exc))
        return True  # fail-open: better to double-run than skip

# ── Strategy registry ────────────────────────────────────────────────────────

# Maps strategy name (as it appears in strategies.yaml) → class.
# A future plugin system (task 5.1) can replace this with dynamic discovery.
_STRATEGY_REGISTRY: Dict[str, type] = {}


def _get_strategy_registry() -> Dict[str, type]:
    """Lazily populate and return the strategy registry."""
    if not _STRATEGY_REGISTRY:
        from core.strategies import MeanReversion, SmartHodler

        _STRATEGY_REGISTRY["smart_hodler"] = SmartHodler
        _STRATEGY_REGISTRY["mean_reversion"] = MeanReversion
    return _STRATEGY_REGISTRY


# ── Worker-process state ─────────────────────────────────────────────────────

# Keeps strategy, portfolio-manager, risk-engine and executor instances alive
# across ticks *within the same worker process*.  Keyed by (strategy, symbol).
#
# This is a pragmatic in-memory cache.  Task 3.8 will add proper DB-backed
# state persistence so that strategy position survives worker restarts.

_WorkerStateKey = Tuple[str, str]  # (strategy_name, symbol)


class _TickComponents:
    """Holds the live objects for one (strategy, symbol) pair."""

    __slots__ = ("strategy", "pm", "risk_engine", "executor")

    def __init__(self, strategy, pm, risk_engine, executor):  # noqa: ANN001
        self.strategy = strategy
        self.pm = pm
        self.risk_engine = risk_engine
        self.executor = executor


_worker_state: Dict[_WorkerStateKey, _TickComponents] = {}


def clear_worker_state() -> None:
    """Reset worker-process state.  Used in tests for isolation."""
    _worker_state.clear()


def _get_or_create_components(
    strategy_name: str,
    symbol: str,
    strategy_config: dict,
    risk_config: dict,
    execution_config: dict,
    paper_config: dict,
    initial_capital: float,
) -> _TickComponents:
    """
    Return cached tick-pipeline components, creating them on first call.
    """
    key: _WorkerStateKey = (strategy_name, symbol)

    if key not in _worker_state:
        from core.execution.paper import PaperExecutor
        from core.portfolio import PortfolioManager
        from core.risk import RiskEngine

        registry = _get_strategy_registry()
        strategy_cls = registry[strategy_name]

        strategy = strategy_cls(strategy_config)

        cap = Decimal(str(initial_capital))
        taker_fee = Decimal(str(execution_config.get("taker_fee", 0.001)))

        pm = PortfolioManager(initial_capital=cap, taker_fee=taker_fee)
        risk_engine = RiskEngine(strategy_name, strategy_config, risk_config)
        executor = PaperExecutor(
            strategy_name=strategy_name,
            portfolio_manager=pm,
            slippage=Decimal(str(execution_config.get("slippage", 0.001))),
            simulate_slippage=paper_config.get("simulate_slippage", True),
        )

        _worker_state[key] = _TickComponents(strategy, pm, risk_engine, executor)
        logger.info(
            "tick_components: created",
            strategy=strategy_name,
            symbol=symbol,
            initial_capital=str(cap),
        )

    return _worker_state[key]


# ── fetch_candles (task 3.4) ─────────────────────────────────────────────────


@app.task(bind=True, name="core.tasks.fetch_candles")
def fetch_candles(self) -> Dict[str, Any]:  # noqa: ANN001
    """
    Pull the latest candles for every enabled symbol/timeframe pair.

    Called every 15 minutes by Celery Beat (``fetch-candles-15m``).  For
    each ``(symbol, timeframe)`` pair the task:

    1. Calls ``BinanceClient.fetch_klines()`` to get the 5 most-recent
       candles (no ``start_time`` → Binance returns the trailing window).
    2. Upserts them via ``DataLoader.cache_candles()`` (PostgreSQL
       ``ON CONFLICT DO NOTHING`` — duplicates are silently skipped).
    3. Publishes a ``CANDLE_RECEIVED`` event on the ``MARKET_DATA`` channel
       so the dashboard can update in real-time.

    Errors are isolated per pair: one failing symbol never blocks the rest.

    Returns:
        Summary dict ``{"pairs_processed": N, "pairs_failed": M,
        "total_new_candles": K}`` stored in the Celery result backend.
    """
    from core.config import get_settings
    from core.data.binance_client import BinanceClient
    from core.data.loader import DataLoader
    from core.events import EventChannel, EventType, get_publisher
    from db import get_session

    settings = get_settings()
    publisher = get_publisher()

    pairs_processed = 0
    pairs_failed = 0
    total_new_candles = 0

    for symbol_cfg in settings.enabled_symbols:
        symbol = symbol_cfg.symbol

        for timeframe in symbol_cfg.timeframes:
            log = logger.bind(symbol=symbol, timeframe=timeframe, task_id=self.request.id)

            try:
                with get_session() as session:
                    client = BinanceClient()
                    loader = DataLoader(session, client)

                    candles = client.fetch_klines(symbol, timeframe, limit=_FETCH_LIMIT)
                    new_count = loader.cache_candles(candles, symbol, timeframe)

                    total_new_candles += new_count
                    pairs_processed += 1

                    log.info(
                        "fetch_candles: candles cached",
                        candles_fetched=len(candles),
                        new_candles=new_count,
                    )

                    publisher.publish(
                        EventChannel.MARKET_DATA,
                        EventType.CANDLE_RECEIVED,
                        {
                            "symbol": symbol,
                            "timeframe": timeframe,
                            "candles_fetched": len(candles),
                            "new_candles": new_count,
                        },
                    )

            except Exception as exc:
                pairs_failed += 1
                log.error(
                    "fetch_candles: pair failed",
                    error=str(exc),
                    exc_info=True,
                )
                publisher.publish(
                    EventChannel.SYSTEM,
                    EventType.ERROR,
                    {
                        "task": "fetch_candles",
                        "symbol": symbol,
                        "timeframe": timeframe,
                        "error": str(exc),
                    },
                )

    summary = {
        "pairs_processed": pairs_processed,
        "pairs_failed": pairs_failed,
        "total_new_candles": total_new_candles,
    }
    logger.info("fetch_candles: complete", **summary, task_id=self.request.id)
    return summary


# ── run_strategy_tick (task 3.5) ─────────────────────────────────────────────


@app.task(bind=True, name="core.tasks.run_strategy_tick")
def run_strategy_tick(self) -> Dict[str, Any]:  # noqa: ANN001
    """
    Run the indicator → strategy → risk → execute pipeline for the latest candle.

    Called every 15 minutes by Celery Beat (``run-strategy-tick-15m``),
    offset 1 minute after :func:`fetch_candles` to give it time to
    populate the DB cache.  Also triggered immediately by the WebSocket
    stream runner (task 3.6) when a candle closes.

    A Redis ``SET NX EX`` lock keyed by the latest candle's open_time
    prevents double-execution when both Beat and the stream trigger the
    task for the same candle.

    For each ``(enabled_strategy, enabled_symbol)`` combination the task:

    1. Loads recent candles + indicators via
       ``DataLoader.get_multi_timeframe_market_data()``.
    2. Checks stops via ``RiskEngine.check_stops()``.
    3. Evaluates the strategy via ``Strategy.evaluate()``.
    4. Passes the signal through ``RiskEngine.evaluate()`` for sizing.
    5. Executes via ``PaperExecutor.execute()``.
    6. Takes a portfolio snapshot.

    Strategy, PortfolioManager, RiskEngine and PaperExecutor instances
    are kept alive in worker-process memory across ticks.  Task 3.8 will
    add DB-backed state persistence for crash recovery.

    Returns:
        Summary dict with tick counts and signals per symbol.
        Returns ``{"skipped": True}`` if the idempotency lock was not acquired.
    """
    from core.config import get_settings
    from core.data.binance_client import BinanceClient
    from core.data.loader import DataLoader
    from core.events import EventChannel, EventType, get_publisher
    from core.models import Signal, StrategyState
    from db import get_session

    settings = get_settings()
    publisher = get_publisher()
    registry = _get_strategy_registry()

    # ── Idempotency lock (task 3.6) ──────────────────────────────────
    # Peek at the latest candle timestamp from the DB to build the lock
    # key.  If we can't determine it, proceed anyway (first run / tests).
    _latest_open_time: str | None = None
    try:
        from db.models import CandleRecord

        with get_session() as session:
            row = (
                session.query(CandleRecord.open_time)
                .filter_by(timeframe="15m")
                .order_by(CandleRecord.open_time.desc())
                .first()
            )
            if row:
                _latest_open_time = row[0].isoformat()
    except Exception:
        pass  # fail-open

    if not _acquire_tick_lock(_latest_open_time):
        logger.info(
            "run_strategy_tick: skipped (idempotency lock held)",
            candle_time=_latest_open_time,
            task_id=self.request.id,
        )
        return {"skipped": True, "candle_time": _latest_open_time}

    ticks_processed = 0
    ticks_failed = 0
    signals: Dict[str, str] = {}

    # Iterate over enabled strategies
    strategy_configs = {
        "smart_hodler": settings.strategies.smart_hodler,
        "mean_reversion": settings.strategies.mean_reversion,
    }

    for strategy_name, strat_cfg in strategy_configs.items():
        if not strat_cfg.enabled:
            continue
        if strategy_name not in registry:
            continue

        strategy_config = strat_cfg.model_dump()
        primary_tf = strategy_config.get("timeframe_primary", "15m")
        secondary_tf = strategy_config.get("timeframe_confirmation", "1h")

        for symbol_cfg in settings.enabled_symbols:
            symbol = symbol_cfg.symbol
            log = logger.bind(
                strategy=strategy_name,
                symbol=symbol,
                task_id=self.request.id,
            )

            try:
                comp = _get_or_create_components(
                    strategy_name=strategy_name,
                    symbol=symbol,
                    strategy_config=strategy_config,
                    risk_config=settings.risk.model_dump(),
                    execution_config=settings.execution.model_dump(),
                    paper_config=settings.paper_trading.model_dump(),
                    initial_capital=settings.backtest.initial_capital,
                )

                # ── 1. Load data + indicators from DB cache ──────────
                with get_session() as session:
                    client = BinanceClient()
                    loader = DataLoader(session, client)

                    market_data = loader.get_multi_timeframe_market_data(
                        symbol=symbol,
                        primary_timeframe=primary_tf,
                        secondary_timeframe=secondary_tf,
                        strategy_config=strategy_config,
                        lookback_days=_LOOKBACK_DAYS,
                    )

                if market_data.candles.empty:
                    log.warning("run_strategy_tick: no candles in cache")
                    ticks_failed += 1
                    continue

                # Current candle info
                candle_time = market_data.candles.index[-1]
                if hasattr(candle_time, "to_pydatetime"):
                    candle_time = candle_time.to_pydatetime()
                close = Decimal(str(market_data.candles["close"].iloc[-1]))

                # ── 2. Check stops ───────────────────────────────────
                stop_result = comp.risk_engine.check_stops(
                    market_data, comp.pm.portfolio,
                )

                if stop_result.stop_hit:
                    log.info(
                        "run_strategy_tick: stop hit",
                        exit_reason=str(stop_result.decision.exit_reason),
                        close=str(close),
                    )
                    comp.executor.execute(stop_result.decision, candle_time)
                    # Enter cooldown via strategy state
                    if stop_result.cooldown_candles and stop_result.cooldown_candles > 0:
                        comp.strategy._cooldown_remaining = stop_result.cooldown_candles
                        comp.strategy._state = StrategyState.COOLDOWN
                elif comp.pm.has_position(symbol):
                    comp.pm.update_stops(
                        symbol,
                        highest_close=stop_result.highest_close,
                        trailing_stop_price=stop_result.trailing_stop_price,
                    )

                # ── 3. Strategy decision ─────────────────────────────
                signal = Signal.HOLD
                if not stop_result.stop_hit:
                    signal = comp.strategy.evaluate(market_data, comp.pm.portfolio)

                    # ── 4. Risk evaluation ───────────────────────────
                    decision = comp.risk_engine.evaluate(
                        signal, market_data, comp.pm.portfolio,
                    )

                    # ── 5. Execute ───────────────────────────────────
                    comp.executor.execute(decision, candle_time)

                # ── 6. Equity snapshot ───────────────────────────────
                comp.pm.take_snapshot(candle_time, {symbol: close})

                tick_key = f"{strategy_name}:{symbol}"
                signals[tick_key] = signal.value
                ticks_processed += 1

                log.info(
                    "run_strategy_tick: tick complete",
                    signal=signal.value,
                    state=comp.strategy.state.value,
                    close=str(close),
                    equity=str(comp.pm.portfolio.equity),
                )

            except Exception as exc:
                ticks_failed += 1
                log.error(
                    "run_strategy_tick: tick failed",
                    error=str(exc),
                    exc_info=True,
                )
                publisher.publish(
                    EventChannel.SYSTEM,
                    EventType.ERROR,
                    {
                        "task": "run_strategy_tick",
                        "strategy": strategy_name,
                        "symbol": symbol,
                        "error": str(exc),
                    },
                )

    summary: Dict[str, Any] = {
        "ticks_processed": ticks_processed,
        "ticks_failed": ticks_failed,
        "signals": signals,
    }
    logger.info("run_strategy_tick: complete", **summary, task_id=self.request.id)
    return summary


# ── run_reconciliation (task 3.9, stub) ──────────────────────────────────────


@app.task(bind=True, name="core.tasks.run_reconciliation")
def run_reconciliation(self) -> None:  # noqa: ANN001
    """
    Reconcile local state against expected state.

    Called every 4 hours by Celery Beat (``reconciliation-4h``).
    Stub – will be implemented in task **3.9**.
    """
    logger.info(
        "run_reconciliation: not yet implemented",
        task_id=self.request.id,
    )
