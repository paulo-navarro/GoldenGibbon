"""
Celery tasks for the trading pipeline.

Each task is implemented incrementally as the corresponding kanban item lands:

* :func:`fetch_candles` → **3.4** ✓
* :func:`run_strategy_tick` → **3.5** ✓  (+ **3.7** paper mode, **3.8** state persistence)
* :func:`run_reconciliation` → **3.9** ✓
* :func:`emit_heartbeat` → **3.10** ✓

The module is auto-discovered by :mod:`core.celery_app` via
``app.autodiscover_tasks(["core.tasks"])``.
"""

from __future__ import annotations

from datetime import datetime, timezone
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

# Maximum in-memory trade-history and equity-curve entries to keep
# after each persist.  Older entries live in the DB.
_MAX_MEMORY_TRADES = 50
_MAX_MEMORY_SNAPSHOTS = 100


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
# On first creation the factory attempts to restore state from the DB
# (tasks 3.7 / 3.8) so that paper-trading positions and strategy state
# survive worker restarts.

_WorkerStateKey = Tuple[str, str]  # (strategy_name, symbol)


class _TickComponents:
    """Holds the live objects for one (strategy, symbol) pair."""

    __slots__ = ("strategy", "pm", "risk_engine", "executor", "run_id")

    def __init__(self, strategy, pm, risk_engine, executor, run_id: str):  # noqa: ANN001
        self.strategy = strategy
        self.pm = pm
        self.risk_engine = risk_engine
        self.executor = executor
        self.run_id = run_id


_worker_state: Dict[_WorkerStateKey, _TickComponents] = {}


def clear_worker_state() -> None:
    """Reset worker-process state.  Used in tests for isolation."""
    _worker_state.clear()


# ── State recovery helpers (task 3.8) ────────────────────────────────────────


def _recover_state(
    strategy_name: str,
    symbol: str,
    strategy,  # noqa: ANN001 – Strategy instance
    pm,  # noqa: ANN001 – PortfolioManager
    risk_engine,  # noqa: ANN001 – RiskEngine
) -> str | None:
    """
    Attempt to restore strategy / portfolio / risk state from the DB.

    Returns the previous ``run_id`` if recovery succeeded, or ``None``
    if no persisted state was found.
    """
    from core.models import StrategyState
    from db import get_session
    from db.models import PositionRecord, StrategyStateRecord
    from db.utils import orm_to_position

    recovered_run_id: str | None = None

    try:
        with get_session() as session:
            # 1. Restore strategy state ────────────────────────────────
            state_rec = (
                session.query(StrategyStateRecord)
                .filter_by(symbol=symbol, strategy=strategy_name)
                .first()
            )
            if state_rec is not None:
                strategy._state = StrategyState(state_rec.state)
                data = state_rec.state_data or {}

                # Cooldown counter
                strategy._cooldown_remaining = data.get("cooldown_remaining", 0)

                # Strategy-specific fields
                if hasattr(strategy, "_consecutive_below_ema200"):
                    strategy._consecutive_below_ema200 = data.get(
                        "consecutive_below_ema200", 0
                    )

                # Risk engine: consecutive BUY candle counter
                risk_engine._buy_signal_candles[symbol] = (
                    state_rec.consecutive_buy_candles
                )

                recovered_run_id = data.get("run_id")

                logger.info(
                    "state_recovery: strategy state restored",
                    strategy=strategy_name,
                    symbol=symbol,
                    state=state_rec.state,
                    cooldown_remaining=strategy._cooldown_remaining,
                    buy_signal_candles=state_rec.consecutive_buy_candles,
                )

            # 2. Restore open position ─────────────────────────────────
            pos_rec = (
                session.query(PositionRecord)
                .filter_by(symbol=symbol, strategy=strategy_name)
                .first()
            )
            if pos_rec is not None:
                position = orm_to_position(pos_rec)
                # Restore USDT balance from state_data
                if state_rec is not None:
                    data = state_rec.state_data or {}
                    saved_balance = data.get("usdt_balance")
                    if saved_balance is not None:
                        pm._portfolio.usdt_balance = Decimal(str(saved_balance))

                # Inject position directly into PM without deducting cost
                # (cost was already deducted when originally opened).
                pm._portfolio.positions[symbol] = position
                pm._portfolio.open_trades_count = len(pm._portfolio.positions)

                logger.info(
                    "state_recovery: position restored",
                    strategy=strategy_name,
                    symbol=symbol,
                    size=str(position.size),
                    entry_price=str(position.entry_price),
                    scale_in_count=position.scale_in_count,
                )

    except Exception as exc:
        logger.warning(
            "state_recovery: failed (starting fresh)",
            strategy=strategy_name,
            symbol=symbol,
            error=str(exc),
        )

    return recovered_run_id


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

    On first creation, attempts to recover persisted state from the DB
    (task 3.8).  Falls back to a fresh session if no state is found.
    """
    key: _WorkerStateKey = (strategy_name, symbol)

    if key not in _worker_state:
        from core.execution.paper import PaperExecutor
        from core.events import EventChannel, EventType, get_publisher
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

        # Attempt state recovery from DB
        recovered_run_id = _recover_state(
            strategy_name, symbol, strategy, pm, risk_engine,
        )

        # Generate or reuse run_id
        now_tag = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        run_id = recovered_run_id or f"paper_{strategy_name}_{symbol}_{now_tag}"

        _worker_state[key] = _TickComponents(
            strategy, pm, risk_engine, executor, run_id,
        )

        logger.info(
            "tick_components: created",
            strategy=strategy_name,
            symbol=symbol,
            initial_capital=str(cap),
            run_id=run_id,
            recovered=recovered_run_id is not None,
        )

        # Publish startup event (task 3.7)
        publisher = get_publisher()
        publisher.publish(
            EventChannel.SYSTEM,
            EventType.STARTUP,
            {
                "mode": "paper_trading",
                "strategy": strategy_name,
                "symbol": symbol,
                "run_id": run_id,
                "initial_capital": str(cap),
                "recovered": recovered_run_id is not None,
            },
        )

    return _worker_state[key]


# ── DB persistence helpers (tasks 3.7 / 3.8) ────────────────────────────────


def _persist_tick_results(
    session,  # noqa: ANN001 – SQLAlchemy Session
    comp: _TickComponents,
    strategy_name: str,
    symbol: str,
    execution_result,  # noqa: ANN001 – Optional[ExecutionResult]
    snapshot,  # noqa: ANN001 – PortfolioSnapshot
    signal_value: str,
    candle_time: datetime,
) -> None:
    """
    Persist execution audit trail and state snapshot to the DB.

    Called once per tick after execution + snapshot.  Writes:

    * OrderRecord (if an order was filled)
    * TradeRecord (if a trade closed)
    * PortfolioSnapshot
    * PositionRecord (upsert or delete)
    * StrategyStateRecord (upsert)
    """
    from db.models import (
        OrderRecord,
        PositionRecord,
        PortfolioSnapshot as PortfolioSnapshotORM,
        StrategyStateRecord,
    )
    from db.utils import (
        order_to_orm,
        portfolio_snapshot_to_orm,
        position_to_orm,
        trade_to_orm,
    )

    run_id = comp.run_id

    # ── 1. Order + Trade audit trail ─────────────────────────────────
    if execution_result is not None:
        order_rec = order_to_orm(execution_result.order)
        order_rec.run_id = run_id
        session.add(order_rec)

        if execution_result.trade is not None:
            trade_rec = trade_to_orm(execution_result.trade, run_id=run_id)
            session.add(trade_rec)

    # ── 2. Portfolio snapshot ────────────────────────────────────────
    snap_rec = portfolio_snapshot_to_orm(snapshot, run_id=run_id)
    session.add(snap_rec)

    # ── 3. Position upsert / delete ──────────────────────────────────
    existing_pos = (
        session.query(PositionRecord)
        .filter_by(symbol=symbol, strategy=strategy_name)
        .first()
    )

    if comp.pm.has_position(symbol):
        position = comp.pm.get_position(symbol)
        if existing_pos is not None:
            existing_pos.size = position.size
            existing_pos.entry_price = position.entry_price
            existing_pos.entry_time = position.entry_time
            existing_pos.highest_close = position.highest_close
            existing_pos.trailing_stop_price = position.trailing_stop_price
            existing_pos.hard_stop_price = position.hard_stop_price
            existing_pos.scale_in_count = position.scale_in_count
            existing_pos.buy_signal_candles = position.buy_signal_candles
        else:
            new_pos = position_to_orm(position)
            session.add(new_pos)
    else:
        # Position closed — remove from DB
        if existing_pos is not None:
            session.delete(existing_pos)

    # ── 4. Strategy state upsert ─────────────────────────────────────
    state_data = {
        "run_id": run_id,
        "signal": signal_value,
        "cooldown_remaining": getattr(comp.strategy, "_cooldown_remaining", 0),
        "usdt_balance": str(comp.pm.portfolio.usdt_balance),
        "equity": str(comp.pm.portfolio.equity),
        "total_pnl": str(comp.pm.portfolio.total_pnl),
    }

    # Conditions
    if hasattr(comp.strategy, "conditions") and comp.strategy.conditions is not None:
        state_data["conditions"] = comp.strategy.conditions.model_dump(mode="json")

    # Strategy-specific state
    if hasattr(comp.strategy, "_consecutive_below_ema200"):
        state_data["consecutive_below_ema200"] = comp.strategy._consecutive_below_ema200

    buy_candles = comp.risk_engine.get_buy_signal_candles(symbol)

    # Derive last_exit_time from most recent trade
    last_exit: datetime | None = None
    if comp.pm.portfolio.trade_history:
        last_exit = comp.pm.portfolio.trade_history[-1].exit_time

    existing_state = (
        session.query(StrategyStateRecord)
        .filter_by(symbol=symbol, strategy=strategy_name)
        .first()
    )
    if existing_state is not None:
        existing_state.state = comp.strategy.state.value
        existing_state.consecutive_buy_candles = buy_candles
        existing_state.state_data = state_data
        existing_state.last_exit_time = last_exit
    else:
        new_state = StrategyStateRecord(
            symbol=symbol,
            strategy=strategy_name,
            state=comp.strategy.state.value,
            consecutive_buy_candles=buy_candles,
            state_data=state_data,
            last_exit_time=last_exit,
        )
        session.add(new_state)


def _trim_in_memory_history(pm) -> None:  # noqa: ANN001
    """
    Trim in-memory trade history and equity curve to bounded sizes.

    Older entries are persisted in the DB; no need to keep them in RAM
    for a long-running paper session.
    """
    if len(pm.portfolio.trade_history) > _MAX_MEMORY_TRADES:
        pm._portfolio.trade_history = pm._portfolio.trade_history[-_MAX_MEMORY_TRADES:]
    if len(pm.portfolio.equity_curve) > _MAX_MEMORY_SNAPSHOTS:
        pm._portfolio.equity_curve = pm._portfolio.equity_curve[-_MAX_MEMORY_SNAPSHOTS:]


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

    **Task 3.7 – Paper trading mode:**
    Only runs when ``paper_trading.enabled`` is ``True`` in config.
    Uses ``paper_trading.initial_capital`` for the starting balance.

    **Task 3.8 – State persistence:**
    After each tick, persists orders, trades, portfolio snapshots,
    positions, and strategy state to the DB.  On worker restart the
    factory recovers state automatically.

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
    7. Persists execution results + state to the DB.

    Strategy, PortfolioManager, RiskEngine and PaperExecutor instances
    are kept alive in worker-process memory across ticks.

    Returns:
        Summary dict with tick counts and signals per symbol.
        Returns ``{"skipped": True}`` if the task should not run.
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

    # ── Mode gate (task 3.7) ─────────────────────────────────────────
    if not settings.paper_trading.enabled:
        logger.info(
            "run_strategy_tick: skipped (paper_trading not enabled)",
            task_id=self.request.id,
        )
        return {"skipped": True, "reason": "paper_trading_not_enabled"}

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
                    initial_capital=settings.paper_trading.initial_capital,
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

                execution_result = None

                if stop_result.stop_hit:
                    log.info(
                        "run_strategy_tick: stop hit",
                        exit_reason=str(stop_result.decision.exit_reason),
                        close=str(close),
                    )
                    execution_result = comp.executor.execute(
                        stop_result.decision, candle_time,
                    )
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
                    execution_result = comp.executor.execute(decision, candle_time)

                # ── 6. Equity snapshot ───────────────────────────────
                snapshot = comp.pm.take_snapshot(candle_time, {symbol: close})

                # ── 7. Persist to DB (tasks 3.7 / 3.8) ──────────────
                try:
                    with get_session() as session:
                        _persist_tick_results(
                            session=session,
                            comp=comp,
                            strategy_name=strategy_name,
                            symbol=symbol,
                            execution_result=execution_result,
                            snapshot=snapshot,
                            signal_value=signal.value,
                            candle_time=candle_time,
                        )
                except Exception as persist_exc:
                    log.error(
                        "run_strategy_tick: persist failed (tick still valid)",
                        error=str(persist_exc),
                        exc_info=True,
                    )

                # ── 8. Trim in-memory history ────────────────────────
                _trim_in_memory_history(comp.pm)

                tick_key = f"{strategy_name}:{symbol}"
                signals[tick_key] = signal.value
                ticks_processed += 1

                log.info(
                    "run_strategy_tick: tick complete",
                    signal=signal.value,
                    state=comp.strategy.state.value,
                    close=str(close),
                    equity=str(comp.pm.portfolio.equity),
                    run_id=comp.run_id,
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


# ── Reconciliation helpers (task 3.9) ────────────────────────────────────────

# Tolerance for trade PnL arithmetic comparison (advisory check).
_PNL_TOLERANCE = Decimal("0.01")


def _reconcile_pair(
    session,  # noqa: ANN001 – SQLAlchemy Session
    strategy_name: str,
    symbol: str,
) -> Dict[str, Any]:
    """
    Run DB-only consistency checks for one ``(strategy, symbol)`` pair.

    **Check A – State ↔ Position consistency**

    * ``POSITION`` / ``REDUCED`` → ``PositionRecord`` must exist.
    * ``FLAT`` / ``COOLDOWN`` → no ``PositionRecord`` should exist.

    **Check B – Balance sanity**

    * ``usdt_balance`` and ``equity`` must be non-negative.
    * When ``FLAT``, ``usdt_balance`` ≈ ``equity``.

    **Check C – Trade PnL arithmetic (advisory)**

    * Sum of ``TradeRecord.pnl_usdt`` for the current ``run_id`` should
      match ``state_data.total_pnl`` within :data:`_PNL_TOLERANCE`.

    Returns a result dict::

        {
            "pair": "strategy:symbol",
            "status": "ok" | "mismatch",
            "checks": [...],
            "repairs": [...],
        }
    """
    from db.models import PositionRecord, StrategyStateRecord, TradeRecord

    pair_label = f"{strategy_name}:{symbol}"
    checks: list[Dict[str, Any]] = []
    repairs: list[str] = []
    has_mismatch = False

    state_rec = (
        session.query(StrategyStateRecord)
        .filter_by(symbol=symbol, strategy=strategy_name)
        .first()
    )
    pos_rec = (
        session.query(PositionRecord)
        .filter_by(symbol=symbol, strategy=strategy_name)
        .first()
    )

    # ── Check A: State ↔ Position ────────────────────────────────────
    if state_rec is None and pos_rec is None:
        checks.append({"check": "state_position", "result": "ok", "detail": "no state, no position"})
    elif state_rec is None and pos_rec is not None:
        # Orphan position with no strategy state at all
        has_mismatch = True
        checks.append({
            "check": "state_position",
            "result": "mismatch",
            "detail": "PositionRecord exists but no StrategyStateRecord",
        })
        session.delete(pos_rec)
        repairs.append("deleted orphan PositionRecord (no strategy state)")
        logger.warning(
            "reconcile: orphan position deleted",
            strategy=strategy_name, symbol=symbol,
        )
    elif state_rec is not None:
        state_value = state_rec.state  # e.g. "position", "flat"
        expects_position = state_value in ("position", "reduced")

        if expects_position and pos_rec is None:
            # State says open position but none in DB → reset to FLAT
            has_mismatch = True
            checks.append({
                "check": "state_position",
                "result": "mismatch",
                "detail": f"state={state_value} but no PositionRecord",
            })
            state_rec.state = "flat"
            if state_rec.state_data:
                state_rec.state_data = {
                    **state_rec.state_data,
                    "cooldown_remaining": 0,
                }
            repairs.append(f"reset strategy state from {state_value} to flat")
            logger.warning(
                "reconcile: state reset to flat (missing position)",
                strategy=strategy_name, symbol=symbol, was=state_value,
            )
        elif not expects_position and pos_rec is not None:
            # State is FLAT/COOLDOWN but a position still exists → delete
            has_mismatch = True
            checks.append({
                "check": "state_position",
                "result": "mismatch",
                "detail": f"state={state_value} but PositionRecord exists",
            })
            session.delete(pos_rec)
            repairs.append(f"deleted orphan PositionRecord (state={state_value})")
            logger.warning(
                "reconcile: orphan position deleted",
                strategy=strategy_name, symbol=symbol, state=state_value,
            )
        else:
            checks.append({"check": "state_position", "result": "ok"})

    # ── Check B: Balance sanity ──────────────────────────────────────
    if state_rec is not None and state_rec.state_data:
        data = state_rec.state_data
        usdt_balance = Decimal(str(data.get("usdt_balance", 0)))
        equity = Decimal(str(data.get("equity", 0)))

        balance_ok = True
        details: list[str] = []

        if usdt_balance < 0:
            balance_ok = False
            details.append(f"negative usdt_balance={usdt_balance}")
        if equity < 0:
            balance_ok = False
            details.append(f"negative equity={equity}")

        # If FLAT, balance should equal equity (no position value)
        state_value = state_rec.state
        if state_value == "flat" and abs(usdt_balance - equity) > _PNL_TOLERANCE:
            balance_ok = False
            details.append(
                f"FLAT but usdt_balance={usdt_balance} != equity={equity}"
            )

        if balance_ok:
            checks.append({"check": "balance_sanity", "result": "ok"})
        else:
            has_mismatch = True
            checks.append({
                "check": "balance_sanity",
                "result": "warning",
                "detail": "; ".join(details),
            })
            logger.warning(
                "reconcile: balance sanity warning",
                strategy=strategy_name, symbol=symbol,
                details=details,
            )
    else:
        checks.append({"check": "balance_sanity", "result": "ok", "detail": "no state data"})

    # ── Check C: Trade PnL arithmetic (advisory) ─────────────────────
    if state_rec is not None and state_rec.state_data:
        data = state_rec.state_data
        run_id = data.get("run_id")
        saved_total_pnl = Decimal(str(data.get("total_pnl", 0)))

        if run_id:
            from sqlalchemy import func as sa_func

            row = (
                session.query(sa_func.coalesce(sa_func.sum(TradeRecord.pnl_usdt), 0))
                .filter_by(run_id=run_id)
                .scalar()
            )
            db_total_pnl = Decimal(str(row))
            delta = abs(saved_total_pnl - db_total_pnl)

            if delta <= _PNL_TOLERANCE:
                checks.append({"check": "trade_pnl", "result": "ok"})
            else:
                has_mismatch = True
                checks.append({
                    "check": "trade_pnl",
                    "result": "warning",
                    "detail": (
                        f"state_data.total_pnl={saved_total_pnl} vs "
                        f"sum(TradeRecord.pnl_usdt)={db_total_pnl} "
                        f"(delta={delta})"
                    ),
                })
                logger.warning(
                    "reconcile: trade PnL drift",
                    strategy=strategy_name, symbol=symbol,
                    saved=str(saved_total_pnl), computed=str(db_total_pnl),
                    delta=str(delta),
                )
        else:
            checks.append({"check": "trade_pnl", "result": "ok", "detail": "no run_id"})
    else:
        checks.append({"check": "trade_pnl", "result": "ok", "detail": "no state data"})

    return {
        "pair": pair_label,
        "status": "mismatch" if has_mismatch else "ok",
        "checks": checks,
        "repairs": repairs,
    }


# ── run_reconciliation (task 3.9) ────────────────────────────────────────────


@app.task(bind=True, name="core.tasks.run_reconciliation")
def run_reconciliation(self) -> Dict[str, Any]:  # noqa: ANN001
    """
    Reconcile local DB state against expected invariants.

    Called every 4 hours by Celery Beat (``reconciliation-4h``) and
    once on worker startup (``worker_ready`` signal).  For each
    ``(strategy, symbol)`` pair the task runs three consistency
    checks via :func:`_reconcile_pair`:

    * **State ↔ Position** – strategy state machine matches
      ``PositionRecord`` presence.
    * **Balance sanity** – non-negative balances, FLAT equity match.
    * **Trade PnL arithmetic** – ``state_data.total_pnl`` agrees with
      ``sum(TradeRecord.pnl_usdt)`` for the current ``run_id``.

    Mismatches are auto-repaired (state/position) where safe, and
    published as events on ``EventChannel.SYSTEM`` for dashboard
    visibility.

    Returns:
        Summary dict ``{"pairs_checked": N, "mismatches": M,
        "repairs": R, "details": [...]}``.
    """
    from core.config import get_settings
    from core.events import EventChannel, EventType, get_publisher
    from db import get_session

    settings = get_settings()
    publisher = get_publisher()
    registry = _get_strategy_registry()

    strategy_configs = {
        "smart_hodler": settings.strategies.smart_hodler,
        "mean_reversion": settings.strategies.mean_reversion,
    }

    pairs_checked = 0
    total_mismatches = 0
    total_repairs = 0
    details: list[Dict[str, Any]] = []

    for strategy_name, strat_cfg in strategy_configs.items():
        if not strat_cfg.enabled:
            continue
        if strategy_name not in registry:
            continue

        for symbol_cfg in settings.enabled_symbols:
            symbol = symbol_cfg.symbol
            log = logger.bind(
                strategy=strategy_name,
                symbol=symbol,
                task_id=self.request.id,
            )

            try:
                with get_session() as session:
                    result = _reconcile_pair(session, strategy_name, symbol)
                    pairs_checked += 1
                    details.append(result)

                    if result["status"] == "mismatch":
                        total_mismatches += 1
                        total_repairs += len(result["repairs"])

                        # Publish per-pair mismatch event
                        publisher.publish(
                            EventChannel.SYSTEM,
                            EventType.RECONCILIATION_MISMATCH,
                            {
                                "pair": result["pair"],
                                "checks": result["checks"],
                                "repairs": result["repairs"],
                            },
                        )

                        if result["repairs"]:
                            publisher.publish(
                                EventChannel.SYSTEM,
                                EventType.RECONCILIATION_REPAIRED,
                                {
                                    "pair": result["pair"],
                                    "repairs": result["repairs"],
                                },
                            )

                    log.info(
                        "reconcile: pair checked",
                        status=result["status"],
                        repairs=len(result["repairs"]),
                    )

            except Exception as exc:
                log.error(
                    "reconcile: pair failed",
                    error=str(exc),
                    exc_info=True,
                )
                details.append({
                    "pair": f"{strategy_name}:{symbol}",
                    "status": "error",
                    "error": str(exc),
                })

    summary: Dict[str, Any] = {
        "pairs_checked": pairs_checked,
        "mismatches": total_mismatches,
        "repairs": total_repairs,
        "details": details,
    }

    # Publish summary event
    if total_mismatches == 0:
        publisher.publish(
            EventChannel.SYSTEM,
            EventType.RECONCILIATION_OK,
            summary,
        )
    else:
        publisher.publish(
            EventChannel.SYSTEM,
            EventType.RECONCILIATION_MISMATCH,
            summary,
        )

    logger.info("run_reconciliation: complete", **summary, task_id=self.request.id)
    return summary


# ── Heartbeat (task 3.10) ────────────────────────────────────────────────────

# Redis key written by each heartbeat so the /health endpoint can detect
# beat+worker liveness without a slow Celery inspect call.
_HEARTBEAT_KEY = "gg:heartbeat:last"
_HEARTBEAT_TTL = 120  # 2× the 60-second emit interval


@app.task(name="core.tasks.emit_heartbeat", bind=True)
def emit_heartbeat(self: Any) -> Dict[str, Any]:  # noqa: ANN401
    """
    Lightweight canary task proving **both** Beat and the worker are alive.

    Beat schedules this every 60 s; when a worker executes it the task:

    1. Publishes a ``HEARTBEAT`` event on ``gg:system`` so the React
       dashboard can update its liveness indicator in real time.
    2. Sets a Redis key (``gg:heartbeat:last``) with a 120 s TTL so
       the ``GET /health`` endpoint can report beat/worker status
       without the latency of ``app.control.ping()``.
    """
    import os
    import redis as _redis

    from core.events import EventChannel, EventType, get_publisher

    now = datetime.now(timezone.utc).isoformat()
    hostname = getattr(self.request, "hostname", None) or "unknown"

    # 1. Publish HEARTBEAT event
    publisher = get_publisher()
    publisher.publish(
        EventChannel.SYSTEM,
        EventType.HEARTBEAT,
        {"source": "worker", "hostname": hostname, "timestamp": now},
    )

    # 2. Set Redis canary key
    redis_url = os.environ.get("REDIS_URL", "redis://redis:6379/0")
    try:
        r = _redis.Redis.from_url(redis_url)
        r.set(_HEARTBEAT_KEY, now, ex=_HEARTBEAT_TTL)
    except Exception as exc:
        logger.warning("emit_heartbeat: redis key write failed", error=str(exc))

    logger.debug("emit_heartbeat: ok", hostname=hostname)
    return {"hostname": hostname, "timestamp": now}
