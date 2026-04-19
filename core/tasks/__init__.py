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


def _acquire_tick_lock(
    candle_open_time_iso: str | None,
    strategy_name: str | None = None,
    symbol: str | None = None,
) -> bool:
    """
    Acquire a Redis ``SET NX EX`` lock for the current tick.

    When both Celery Beat *and* the WebSocket stream trigger
    ``run_strategy_tick`` for the same candle, only the first caller
    wins.  The lock key includes the most recent candle's open_time
    and optionally the (strategy, symbol) pair so that different
    strategies can run concurrently.

    Returns ``True`` if the lock was acquired (caller should proceed),
    or ``False`` if the tick is already being handled.
    """
    if candle_open_time_iso is None:
        return True

    import os
    try:
        import redis as redis_lib
    except ImportError:
        return True

    url = os.environ.get("REDIS_URL", "redis://redis:6379/0")
    try:
        client = redis_lib.Redis.from_url(url, socket_connect_timeout=2)
        suffix = f":{strategy_name}:{symbol}" if strategy_name and symbol else ""
        key = f"gg:tick_lock:{candle_open_time_iso}{suffix}"
        acquired = client.set(key, "1", nx=True, ex=_TICK_LOCK_TTL)
        return bool(acquired)
    except Exception as exc:
        logger.warning("tick_lock.failed", error=str(exc))
        return True

# ── Strategy registry (task 5.1 – plugin system) ────────────────────────────

def _get_strategy_registry() -> Dict[str, type]:
    """Return the auto-discovered strategy registry."""
    from core.strategies.registry import get_registry

    return get_registry()


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

    __slots__ = ("strategy", "pm", "risk_engine", "executor", "run_id", "kill_switch")

    def __init__(self, strategy, pm, risk_engine, executor, run_id: str, kill_switch=None):  # noqa: ANN001
        self.strategy = strategy
        self.pm = pm
        self.risk_engine = risk_engine
        self.executor = executor
        self.run_id = run_id
        self.kill_switch = kill_switch


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
    kill_switch=None,  # noqa: ANN001 – KillSwitch instance
) -> str | None:
    """
    Attempt to restore strategy / portfolio / risk state from the DB.

    Recovers:
      1. Strategy state (state machine, cooldown, counters)
      2. Open position
      3. Recent trade history (task 4.7)
      4. Equity curve snapshots (task 4.7)

    Returns the previous ``run_id`` if recovery succeeded, or ``None``
    if no persisted state was found.
    """
    from core.models import StrategyState
    from db import get_session
    from db.models import PositionRecord, PortfolioSnapshot, StrategyStateRecord, TradeRecord
    from db.utils import orm_to_portfolio_snapshot, orm_to_position, orm_to_trade

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
                data = state_rec.state_data or {}
                data["state"] = state_rec.state

                strategy.from_state_dict(data)

                # Risk engine: consecutive BUY candle counter
                risk_engine._buy_signal_candles[symbol] = (
                    state_rec.consecutive_buy_candles
                )

                recovered_run_id = data.get("run_id")

                # Kill-switch state (task 4.5)
                if kill_switch is not None:
                    kill_switch.restore_from_dict(data)

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

            # 3. Restore recent trade history (task 4.7) ──────────────
            if recovered_run_id is not None:
                trade_recs = (
                    session.query(TradeRecord)
                    .filter_by(strategy=strategy_name, symbol=symbol)
                    .order_by(TradeRecord.exit_time.desc())
                    .limit(_MAX_MEMORY_TRADES)
                    .all()
                )
                if trade_recs:
                    # Reverse so oldest first (chronological order)
                    pm._portfolio.trade_history = [
                        orm_to_trade(r) for r in reversed(trade_recs)
                    ]
                    logger.info(
                        "state_recovery: trade history restored",
                        strategy=strategy_name,
                        symbol=symbol,
                        count=len(trade_recs),
                    )

            # 4. Restore equity curve snapshots (task 4.7) ────────────
            if recovered_run_id is not None:
                snap_recs = (
                    session.query(PortfolioSnapshot)
                    .filter_by(run_id=recovered_run_id)
                    .order_by(PortfolioSnapshot.timestamp.desc())
                    .limit(_MAX_MEMORY_SNAPSHOTS)
                    .all()
                )
                if snap_recs:
                    pm._portfolio.equity_curve = [
                        orm_to_portfolio_snapshot(r) for r in reversed(snap_recs)
                    ]
                    logger.info(
                        "state_recovery: equity curve restored",
                        strategy=strategy_name,
                        symbol=symbol,
                        count=len(snap_recs),
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
    trading_mode: str = "paper",
) -> _TickComponents:
    """
    Return cached tick-pipeline components, creating them on first call.

    On first creation, attempts to recover persisted state from the DB
    (task 3.8).  Falls back to a fresh session if no state is found.

    Args:
        trading_mode: ``"paper"`` for PaperExecutor, ``"live"`` for
                      BinanceExecutor.
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

        if trading_mode == "live":
            from core.execution.binance import BinanceExecutor

            executor = BinanceExecutor.from_settings(
                strategy_name=strategy_name,
                portfolio_manager=pm,
            )
            # Load exchange filters for quantity/price formatting
            executor.load_exchange_filters([symbol])
        else:
            executor = PaperExecutor(
                strategy_name=strategy_name,
                portfolio_manager=pm,
                slippage=Decimal(str(execution_config.get("slippage", 0.001))),
                simulate_slippage=paper_config.get("simulate_slippage", True),
            )

        # Kill-switch (task 4.5)
        from core.risk.kill_switch import KillSwitch
        from core.config import get_settings as _get_settings

        _settings = _get_settings()
        kill_switch = KillSwitch(
            max_drawdown_pct=_settings.live_trading.kill_switch_max_drawdown,
            max_daily_loss_pct=_settings.risk.max_daily_loss_pct,
        )

        # Attempt state recovery from DB
        recovered_run_id = _recover_state(
            strategy_name, symbol, strategy, pm, risk_engine,
            kill_switch=kill_switch,
        )

        # Generate or reuse run_id
        now_tag = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        prefix = "live" if trading_mode == "live" else "paper"
        run_id = recovered_run_id or f"{prefix}_{strategy_name}_{symbol}_{now_tag}"

        _worker_state[key] = _TickComponents(
            strategy, pm, risk_engine, executor, run_id, kill_switch,
        )

        logger.info(
            "tick_components: created",
            strategy=strategy_name,
            symbol=symbol,
            initial_capital=str(cap),
            run_id=run_id,
            recovered=recovered_run_id is not None,
            trading_mode=trading_mode,
        )

        # Publish startup event (task 3.7)
        publisher = get_publisher()
        publisher.publish(
            EventChannel.SYSTEM,
            EventType.STARTUP,
            {
                "mode": f"{trading_mode}_trading",
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
        order_rec = order_to_orm(execution_result.order, run_id=run_id)
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
        "usdt_balance": str(comp.pm.portfolio.usdt_balance),
        "equity": str(comp.pm.portfolio.equity),
        "total_pnl": str(comp.pm.portfolio.total_pnl),
    }

    # Strategy-specific state via plugin interface
    state_data.update(comp.strategy.to_state_dict())

    # Conditions
    if hasattr(comp.strategy, "conditions") and comp.strategy.conditions is not None:
        state_data["conditions"] = comp.strategy.conditions.model_dump(mode="json")

    # Kill-switch state (task 4.5)
    if comp.kill_switch is not None:
        state_data.update(comp.kill_switch.to_dict())

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


# ── Alerting helper (task 4.9) ─────────────────────────────────────────────


def _send_tick_alerts(
    *,
    strategy_name: str,
    symbol: str,
    execution_result,  # noqa: ANN001 – Optional[ExecutionResult]
    stop_hit: bool,
    stop_type: str | None,
    close: Decimal,
    comp: _TickComponents,
    settings,  # noqa: ANN001 – Settings
) -> None:
    """
    Send Telegram alerts for notable events in the current tick.

    Called once per tick after persistence.  Checks ``AlertingConfig``
    flags before sending.  Never raises.
    """
    cfg = settings.alerting
    if not cfg.enabled:
        return

    try:
        from core.alerting import get_alerter

        alerter = get_alerter()
        if not alerter.enabled:
            return

        # ── Stop hit ────────────────────────────────────────────
        if stop_hit and cfg.alert_on_stop:
            alerter.alert_stop(
                symbol=symbol,
                stop_type=stop_type or "unknown",
                price=str(close),
                strategy=strategy_name,
            )

        # ── Order fill ──────────────────────────────────────────
        if execution_result is not None and cfg.alert_on_fill:
            order = execution_result.order
            pnl_str = None
            if execution_result.trade is not None:
                pnl_str = f"{execution_result.trade.pnl_usdt} USDT"
            alerter.alert_fill(
                symbol=symbol,
                side=order.side.value,
                size=str(order.filled_amount),
                price=str(order.avg_fill_price or order.price or close),
                strategy=strategy_name,
                pnl=pnl_str,
            )

        # ── Kill-switch just triggered ──────────────────────────
        if (
            cfg.alert_on_kill_switch
            and comp.kill_switch
            and comp.kill_switch.is_triggered
        ):
            alerter.alert_kill_switch(
                reason=comp.kill_switch.trigger_reason or "unknown",
                equity=str(comp.pm.portfolio.equity),
                peak_equity=str(comp.kill_switch.peak_equity),
            )

    except Exception as exc:
        logger.debug("alerting: send failed (non-fatal)", error=str(exc))


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


def _get_enabled_strategy_pairs() -> list[tuple[str, str, dict]]:
    """
    Return ``[(strategy_name, symbol, strategy_config), ...]`` for all
    enabled (strategy, symbol) combinations.
    """
    from core.config import get_settings

    settings = get_settings()
    registry = _get_strategy_registry()
    pairs: list[tuple[str, str, dict]] = []

    for strategy_name in registry:
        strat_cfg = settings.strategies.get_strategy_config(strategy_name)
        if strat_cfg is None:
            continue
        if isinstance(strat_cfg, dict):
            if not strat_cfg.get("enabled", False):
                continue
            strategy_config = strat_cfg
        else:
            if not strat_cfg.enabled:
                continue
            strategy_config = strat_cfg.model_dump()

        for symbol_cfg in settings.enabled_symbols:
            pairs.append((strategy_name, symbol_cfg.symbol, strategy_config))

    return pairs


def _resolve_allocated_capital(
    strategy_name: str,
    symbol: str,
    strategy_config: dict,
    settings,  # noqa: ANN001
) -> float:
    """
    Resolve the capital allocated to a (strategy, symbol) pair.

    Uses the allocation model (task 5.4a) to distribute the total
    capital pool based on per-strategy ``allocation_pct`` weights.
    Falls back to the old behavior if allocation cannot be computed.
    """
    from core.allocation import compute_allocations

    total_capital = Decimal(str(
        settings.live_trading.max_order_size_usdt
        if settings.live_trading.enabled
        else settings.paper_trading.initial_capital
    ))

    registry = _get_strategy_registry()
    pairs = _get_enabled_strategy_pairs()
    enabled_strategies = list(dict.fromkeys(s for s, _, _ in pairs))
    symbols = list(dict.fromkeys(sym for _, sym, _ in pairs))

    configs: dict[str, dict] = {}
    for sname in enabled_strategies:
        cfg = settings.strategies.get_strategy_config(sname)
        if cfg is not None:
            configs[sname] = cfg if isinstance(cfg, dict) else cfg.model_dump()

    # Regime-based rebalancing (task 5.4c)
    regime_kwargs: dict = {}
    regime_cfg = settings.regime
    if regime_cfg.rebalance_enabled:
        try:
            from core.regime import RegimeDetector

            detector = RegimeDetector(
                trending_threshold=regime_cfg.adx_trending_threshold,
                ranging_threshold=regime_cfg.adx_ranging_threshold,
                smoothing_window=regime_cfg.smoothing_window,
            )
            adx_indicators = _fetch_latest_adx(symbols[0] if symbols else "BTCUSDT")
            classification = detector.detect(adx_indicators)
            regime_kwargs = {
                "regime": classification.regime.value,
                "regime_confidence": classification.confidence,
                "regime_shift_pct": regime_cfg.regime_shift_pct,
                "strategy_regime_map": regime_cfg.strategy_regime_map,
            }
            logger.debug(
                "allocation: regime adjustment",
                regime=classification.regime.value,
                confidence=classification.confidence,
                adx=classification.smoothed_adx,
            )
        except Exception as exc:
            logger.warning("allocation: regime detection failed, using base weights", error=str(exc))

    allocations = compute_allocations(
        total_capital=total_capital,
        enabled_strategies=enabled_strategies,
        strategy_configs=configs,
        symbols=symbols,
        **regime_kwargs,
    )

    key = (strategy_name, symbol)
    if key in allocations:
        return float(allocations[key])

    return strategy_config.get(
        "initial_capital", settings.paper_trading.initial_capital,
    )


def _fetch_latest_adx(symbol: str, lookback: int = 50) -> Dict[str, Any]:
    """
    Fetch recent ADX values from cached candles for regime detection.

    Returns a dict suitable for ``RegimeDetector.detect()``.
    """
    import pandas as pd

    try:
        from core.data.binance_client import BinanceClient
        from core.data.loader import DataLoader
        from core.indicators.technical import calculate_adx
        from db import get_session

        with get_session() as session:
            client = BinanceClient()
            loader = DataLoader(session, client)
            md = loader.get_market_data(
                symbol=symbol,
                timeframe="15m",
                lookback_days=7,
            )

        if md.candles.empty or len(md.candles) < 20:
            return {}

        adx = calculate_adx(
            md.candles["high"],
            md.candles["low"],
            md.candles["close"],
        )
        return {"adx": adx}

    except Exception as exc:
        logger.warning("_fetch_latest_adx: failed", error=str(exc))
        return {}


def _peek_latest_candle_time() -> str | None:
    """Return ISO timestamp of the most recent 15m candle, or None."""
    try:
        from db.models import CandleRecord
        from db import get_session

        with get_session() as session:
            row = (
                session.query(CandleRecord.open_time)
                .filter_by(timeframe="15m")
                .order_by(CandleRecord.open_time.desc())
                .first()
            )
            if row:
                return row[0].isoformat()
    except Exception:
        pass
    return None


@app.task(bind=True, name="core.tasks.run_single_strategy_tick")
def run_single_strategy_tick(
    self,  # noqa: ANN001
    strategy_name: str,
    symbol: str,
) -> Dict[str, Any]:
    """
    Run the full tick pipeline for a single (strategy, symbol) pair.

    Called by :func:`run_strategy_tick` dispatcher via ``celery.group()``
    so that multiple strategies execute in parallel across workers.

    Also callable directly for testing or manual triggers.
    """
    from core.config import get_settings
    from core.data.binance_client import BinanceClient
    from core.data.loader import DataLoader
    from core.events import EventChannel, EventType, get_publisher
    from core.models import Signal, StrategyState
    from db import get_session

    settings = get_settings()
    publisher = get_publisher()

    # ── Mode gate ───────────────────────────────────────────────────
    if not settings.paper_trading.enabled and not settings.live_trading.enabled:
        return {"skipped": True, "reason": "no_trading_mode_enabled"}
    trading_mode = "live" if settings.live_trading.enabled else "paper"

    # ── Idempotency lock (per strategy+symbol) ──────────────────────
    candle_time_iso = _peek_latest_candle_time()
    if not _acquire_tick_lock(candle_time_iso, strategy_name, symbol):
        logger.info(
            "single_tick: skipped (lock held)",
            strategy=strategy_name, symbol=symbol,
            candle_time=candle_time_iso,
        )
        return {"skipped": True, "strategy": strategy_name, "symbol": symbol}

    # ── Resolve strategy config ─────────────────────────────────────
    strat_cfg = settings.strategies.get_strategy_config(strategy_name)
    if strat_cfg is None:
        return {"skipped": True, "reason": "no_config"}
    strategy_config = strat_cfg if isinstance(strat_cfg, dict) else strat_cfg.model_dump()

    primary_tf = strategy_config.get("timeframe_primary", "15m")
    secondary_tf = strategy_config.get("timeframe_confirmation", "1h")

    log = logger.bind(strategy=strategy_name, symbol=symbol, task_id=self.request.id)

    try:
        initial_capital = _resolve_allocated_capital(
            strategy_name, symbol, strategy_config, settings,
        )

        comp = _get_or_create_components(
            strategy_name=strategy_name,
            symbol=symbol,
            strategy_config=strategy_config,
            risk_config=settings.risk.model_dump(),
            execution_config=settings.execution.model_dump(),
            paper_config=settings.paper_trading.model_dump(),
            initial_capital=initial_capital,
            trading_mode=trading_mode,
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
            log.warning("single_tick: no candles in cache")
            return {"strategy": strategy_name, "symbol": symbol, "signal": None, "error": "no_candles"}

        # Current candle info
        candle_time = market_data.candles.index[-1]
        if hasattr(candle_time, "to_pydatetime"):
            candle_time = candle_time.to_pydatetime()
        close = Decimal(str(market_data.candles["close"].iloc[-1]))

        # ── Kill-switch gate (task 4.5) ─────────────────────
        if comp.kill_switch and comp.kill_switch.is_triggered:
            log.warning(
                "single_tick: kill-switch active",
                reason=comp.kill_switch.trigger_reason,
            )
            comp.pm.update_equity(candle_time, {symbol: close})
            return {"strategy": strategy_name, "symbol": symbol, "signal": None, "kill_switch": True}

        # ── 2. Check stops ───────────────────────────────────
        stop_result = comp.risk_engine.check_stops(
            market_data, comp.pm.portfolio,
        )

        execution_result = None

        if stop_result.stop_hit:
            log.info(
                "single_tick: stop hit",
                exit_reason=str(stop_result.decision.exit_reason),
                close=str(close),
            )
            execution_result = comp.executor.execute(
                stop_result.decision, candle_time,
            )
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

        # ── Kill-switch check (task 4.5) ────────────────────
        if comp.kill_switch:
            comp.kill_switch.check(comp.pm.portfolio.equity, candle_time)

        # ── 7. Persist to DB ────────────────────────────────
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
                "single_tick: persist failed (tick still valid)",
                error=str(persist_exc),
                exc_info=True,
            )

        # ── 8. Trim in-memory history ────────────────────────
        _trim_in_memory_history(comp.pm)

        # ── 9. Alerts (task 4.9) ──────────────────────────────
        _send_tick_alerts(
            strategy_name=strategy_name,
            symbol=symbol,
            execution_result=execution_result,
            stop_hit=stop_result.stop_hit,
            stop_type=(
                str(stop_result.decision.exit_reason)
                if stop_result.stop_hit
                else None
            ),
            close=close,
            comp=comp,
            settings=settings,
        )

        log.info(
            "single_tick: complete",
            signal=signal.value,
            state=comp.strategy.state.value,
            close=str(close),
            equity=str(comp.pm.portfolio.equity),
            run_id=comp.run_id,
        )

        return {
            "strategy": strategy_name,
            "symbol": symbol,
            "signal": signal.value,
        }

    except Exception as exc:
        log.error("single_tick: failed", error=str(exc), exc_info=True)
        publisher.publish(
            EventChannel.SYSTEM,
            EventType.ERROR,
            {
                "task": "run_single_strategy_tick",
                "strategy": strategy_name,
                "symbol": symbol,
                "error": str(exc),
            },
        )
        if settings.alerting.enabled and settings.alerting.alert_on_error:
            try:
                from core.alerting import get_alerter

                get_alerter().alert_error(
                    task=f"tick:{strategy_name}:{symbol}",
                    error=str(exc),
                )
            except Exception:
                pass

        return {"strategy": strategy_name, "symbol": symbol, "signal": None, "error": str(exc)}


@app.task(bind=True, name="core.tasks.run_strategy_tick")
def run_strategy_tick(self) -> Dict[str, Any]:  # noqa: ANN001
    """
    Dispatcher that fans out tick processing across workers.

    For each enabled (strategy, symbol) pair, dispatches a
    :func:`run_single_strategy_tick` sub-task.  When multiple Celery
    workers are running, these execute in parallel (task 5.2).

    With a single worker, tasks run sequentially in the worker's
    prefetch queue — same behavior as before but with per-pair
    idempotency locks.
    """
    from celery import group

    pairs = _get_enabled_strategy_pairs()

    if not pairs:
        logger.info(
            "run_strategy_tick: no enabled strategy/symbol pairs",
            task_id=self.request.id,
        )
        return {"skipped": True, "reason": "no_enabled_pairs"}

    job = group(
        run_single_strategy_tick.s(strategy_name, symbol)
        for strategy_name, symbol, _ in pairs
    )

    result = job.apply_async()

    logger.info(
        "run_strategy_tick: dispatched",
        pairs=len(pairs),
        group_id=result.id,
        task_id=self.request.id,
    )

    return {
        "dispatched": len(pairs),
        "pairs": [f"{s}:{sym}" for s, sym, _ in pairs],
        "group_id": result.id,
    }


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


# ── Exchange reconciliation (task 4.8) ────────────────────────────────────────

# Tolerance for balance/position comparison against exchange.
# Binance balances include dust from rounding, fees, etc.
_EXCHANGE_BALANCE_TOLERANCE = Decimal("0.01")  # USDT
_EXCHANGE_POSITION_TOLERANCE = Decimal("0.00000100")  # asset qty (1 satoshi for BTC)


def _reconcile_with_exchange(
    session,  # noqa: ANN001 – SQLAlchemy Session
    executor,  # noqa: ANN001 – BinanceExecutor
    symbols: list[str],
) -> Dict[str, Any]:
    """
    Compare local DB state against Binance account balances.

    Runs two advisory checks per tracked symbol:

    **Check D – USDT balance**

    * ``state_data.usdt_balance`` ≈ Binance ``USDT`` free balance.

    **Check E – Position size**

    * ``PositionRecord.size`` ≈ Binance base-asset balance (e.g. ``BTC``
      free + locked for ``BTCUSDT``).

    All mismatches are **advisory** — no auto-repair is performed for
    exchange discrepancies.  Differences are logged and published as
    events for dashboard visibility.

    Returns a result dict::

        {
            "status": "ok" | "mismatch",
            "checks": [...],
            "exchange_balances": {...},
        }
    """
    from db.models import PositionRecord, StrategyStateRecord

    checks: list[Dict[str, Any]] = []
    has_mismatch = False

    # Fetch exchange state
    try:
        account = executor.get_account_info()
    except Exception as exc:
        logger.error(
            "reconcile_exchange: failed to fetch account",
            error=str(exc),
        )
        return {
            "status": "error",
            "checks": [{"check": "exchange_account", "result": "error", "detail": str(exc)}],
            "exchange_balances": {},
        }

    exchange_balances = account["balances"]

    # ── Check D: USDT balance ────────────────────────────────────────
    # Aggregate local USDT balance across all strategy state records.
    state_recs = session.query(StrategyStateRecord).all()
    local_usdt_total = Decimal("0")
    for rec in state_recs:
        data = rec.state_data or {}
        local_usdt_total += Decimal(str(data.get("usdt_balance", 0)))

    exchange_usdt = Decimal("0")
    if "USDT" in exchange_balances:
        exchange_usdt = exchange_balances["USDT"]["free"] + exchange_balances["USDT"]["locked"]

    usdt_delta = abs(local_usdt_total - exchange_usdt)
    if usdt_delta <= _EXCHANGE_BALANCE_TOLERANCE:
        checks.append({"check": "exchange_usdt", "result": "ok"})
    else:
        has_mismatch = True
        checks.append({
            "check": "exchange_usdt",
            "result": "warning",
            "detail": (
                f"local_usdt={local_usdt_total} vs "
                f"exchange_usdt={exchange_usdt} (delta={usdt_delta})"
            ),
        })
        logger.warning(
            "reconcile_exchange: USDT balance mismatch",
            local=str(local_usdt_total),
            exchange=str(exchange_usdt),
            delta=str(usdt_delta),
        )

    # ── Check E: Position sizes ──────────────────────────────────────
    for symbol in symbols:
        # Derive base asset: BTCUSDT → BTC, ETHUSDT → ETH
        base_asset = symbol.replace("USDT", "")

        # Local position size across all strategies for this symbol
        pos_recs = (
            session.query(PositionRecord)
            .filter_by(symbol=symbol)
            .all()
        )
        local_size = sum((r.size for r in pos_recs), Decimal("0"))

        # Exchange balance for base asset
        exchange_size = Decimal("0")
        if base_asset in exchange_balances:
            exchange_size = (
                exchange_balances[base_asset]["free"]
                + exchange_balances[base_asset]["locked"]
            )

        size_delta = abs(local_size - exchange_size)
        if size_delta <= _EXCHANGE_POSITION_TOLERANCE:
            checks.append({
                "check": f"exchange_position_{symbol}",
                "result": "ok",
            })
        else:
            has_mismatch = True
            checks.append({
                "check": f"exchange_position_{symbol}",
                "result": "warning",
                "detail": (
                    f"local_size={local_size} vs "
                    f"exchange_size={exchange_size} (delta={size_delta})"
                ),
            })
            logger.warning(
                "reconcile_exchange: position size mismatch",
                symbol=symbol,
                local=str(local_size),
                exchange=str(exchange_size),
                delta=str(size_delta),
            )

    return {
        "status": "mismatch" if has_mismatch else "ok",
        "checks": checks,
        "exchange_balances": {
            asset: {k: str(v) for k, v in bals.items()}
            for asset, bals in exchange_balances.items()
        },
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

    pairs_checked = 0
    total_mismatches = 0
    total_repairs = 0
    details: list[Dict[str, Any]] = []

    for strategy_name in registry:
        strat_cfg = settings.strategies.get_strategy_config(strategy_name)
        if strat_cfg is None:
            continue
        enabled = strat_cfg.get("enabled", False) if isinstance(strat_cfg, dict) else strat_cfg.enabled
        if not enabled:
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

    # ── Exchange reconciliation (task 4.8) ────────────────────────────
    exchange_result: Dict[str, Any] | None = None
    if settings.live_trading.enabled:
        try:
            from core.execution.binance import BinanceExecutor

            # Build a temporary executor just for account queries
            from core.portfolio import PortfolioManager as _PM

            executor = BinanceExecutor.from_settings(
                strategy_name="reconciliation",
                portfolio_manager=_PM(initial_capital=Decimal("0")),
            )
            tracked_symbols = [s.symbol for s in settings.enabled_symbols]

            with get_session() as session:
                exchange_result = _reconcile_with_exchange(
                    session, executor, tracked_symbols,
                )

            if exchange_result["status"] == "mismatch":
                total_mismatches += 1
                publisher.publish(
                    EventChannel.SYSTEM,
                    EventType.RECONCILIATION_MISMATCH,
                    {
                        "source": "exchange",
                        "checks": exchange_result["checks"],
                    },
                )

            logger.info(
                "reconcile: exchange check done",
                status=exchange_result["status"],
            )
        except Exception as exc:
            logger.error(
                "reconcile: exchange check failed",
                error=str(exc),
                exc_info=True,
            )
            exchange_result = {"status": "error", "error": str(exc)}

    summary: Dict[str, Any] = {
        "pairs_checked": pairs_checked,
        "mismatches": total_mismatches,
        "repairs": total_repairs,
        "details": details,
    }
    if exchange_result is not None:
        summary["exchange"] = exchange_result

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

    # ── Alert on mismatches (task 4.9) ──────────────────────────────
    if total_mismatches > 0 and settings.alerting.enabled:
        try:
            from core.alerting import get_alerter

            alerter = get_alerter()
            if alerter.enabled and settings.alerting.alert_on_reconciliation:
                mismatch_checks = [
                    c
                    for d in details
                    for c in d.get("checks", [])
                    if c.get("result") in ("mismatch", "warning")
                ]
                detail_lines = [c.get("detail", c["check"]) for c in mismatch_checks[:5]]
                if exchange_result and exchange_result.get("status") == "mismatch":
                    for c in exchange_result.get("checks", []):
                        if c.get("result") == "warning":
                            detail_lines.append(c.get("detail", c["check"]))
                alerter.alert_reconciliation_mismatch(
                    details="\n".join(detail_lines) or f"{total_mismatches} mismatch(es) found",
                )
        except Exception:
            pass  # fire-and-forget

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
