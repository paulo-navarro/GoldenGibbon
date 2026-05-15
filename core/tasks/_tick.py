"""
Strategy tick Celery tasks and helpers.

Contains the core tick pipeline: idempotency lock, capital resolution,
market data loading, strategy evaluation, risk check, execution, and
DB persistence — dispatched per (strategy, symbol) pair.
"""

from __future__ import annotations

import time as _time
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict

import structlog

from core.celery_app import app
from core.tasks._state import (
    _get_or_create_components,
    _get_strategy_registry,
    _TickComponents,
    _worker_state,
    _WorkerStateKey,
)
from core.tasks._persistence import (
    _emergency_position_cleanup,
    _persist_tick_results,
    _send_tick_alerts,
    _trim_in_memory_history,
)

logger = structlog.get_logger(__name__)

# Number of days of candle history to load per tick.
_LOOKBACK_DAYS = 7

# Lifetime (seconds) of the per-candle idempotency lock in Redis.
_TICK_LOCK_TTL = 120


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



# ── Enabled strategy pairs ───────────────────────────────────────────────────


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




# ── Capital allocation ───────────────────────────────────────────────────────


_cached_exchange_capital: tuple[Decimal, float] | None = None
_CAPITAL_CACHE_TTL = 300  # 5 minutes


def _get_live_total_capital(settings) -> Decimal:  # noqa: ANN001
    """Fetch total USDT from the exchange, cached for 5 minutes."""
    global _cached_exchange_capital
    now = _time.monotonic()
    if _cached_exchange_capital and (now - _cached_exchange_capital[1]) < _CAPITAL_CACHE_TTL:
        return _cached_exchange_capital[0]
    try:
        from core.execution.binance import BinanceExecutor
        from core.portfolio import PortfolioManager as _PM

        executor = BinanceExecutor.from_settings(
            strategy_name="_allocation",
            portfolio_manager=_PM(initial_capital=Decimal("1")),
        )
        account = executor.get_account_info()
        usdt = account["balances"].get("USDT", {})
        total = usdt.get("free", Decimal("0")) + usdt.get("locked", Decimal("0"))
        _cached_exchange_capital = (total, now)
        logger.info("allocation: fetched exchange capital", exchange_usdt=str(total))
        return total
    except Exception as exc:
        logger.warning("allocation: failed to fetch exchange capital", error=str(exc))
        if _cached_exchange_capital:
            return _cached_exchange_capital[0]
        return Decimal(str(settings.live_trading.max_order_size_usdt))


def _resolve_allocated_capital(settings) -> float:  # noqa: ANN001
    """Return per-slot capital: total_capital / max_concurrent_positions."""
    if settings.live_trading.enabled:
        total_capital = _get_live_total_capital(settings)
    else:
        total_capital = Decimal(str(settings.paper_trading.initial_capital))

    max_pos = settings.risk.max_concurrent_positions
    slot = total_capital / max_pos
    logger.debug("allocation: slot capital", total=str(total_capital), max_pos=max_pos, slot=str(slot))
    return float(slot)


def _count_global_open_positions() -> int:
    """Count open positions across all cached worker-state entries."""
    count = 0
    for comp in _worker_state.values():
        if comp.pm.portfolio.positions:
            count += len(comp.pm.portfolio.positions)
    return count





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




# ── run_single_strategy_tick (task 3.5) ──────────────────────────────────────


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
    from core.models import Signal, StopCheckResult, StrategyState
    from db import get_session

    # Always reload settings from DB so workers pick up config changes
    # (e.g. live_trading.enabled toggled via Settings UI) without restart.
    settings = get_settings(reload=True)
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

    # Evict stale components if trading mode changed (e.g. paper → live).
    # Forces _get_or_create_components to rebuild with the correct executor.
    _tick_key: _WorkerStateKey = (strategy_name, symbol)
    if _tick_key in _worker_state and _worker_state[_tick_key].trading_mode != trading_mode:
        log.info(
            "single_tick: trading mode changed, evicting stale components",
            old_mode=_worker_state[_tick_key].trading_mode,
            new_mode=trading_mode,
        )
        del _worker_state[_tick_key]

    try:
        initial_capital = _resolve_allocated_capital(settings)

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

        # Update slot_capital every tick (exchange balance changes over time)
        comp.pm.portfolio.slot_capital = Decimal(str(initial_capital))

        # ── 0b. Sync exchange stop config from live settings ──
        if hasattr(comp.executor, "_exchange_stops_enabled"):
            comp.executor._exchange_stops_enabled = settings.execution.exchange_stop_orders_enabled
            comp.executor._stop_slippage = Decimal(str(settings.execution.stop_limit_slippage_pct))

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

        # ── Kill-switch: detect external reset (e.g. reset_kill_switches.py) ──
        if comp.kill_switch and comp.kill_switch.is_triggered:
            try:
                from db.models import StrategyStateRecord as _SSR

                with get_session() as session:
                    _state = (
                        session.query(_SSR)
                        .filter_by(symbol=symbol, strategy=strategy_name)
                        .first()
                    )
                    if _state and not (_state.state_data or {}).get(
                        "kill_switch_triggered", False
                    ):
                        log.info(
                            "single_tick: kill-switch reset detected in DB, syncing",
                            strategy=strategy_name,
                            symbol=symbol,
                        )
                        comp.kill_switch.hard_reset()
            except Exception as reset_exc:
                log.error(
                    "single_tick: kill-switch reset check failed",
                    error=str(reset_exc),
                )

        # ── Kill-switch gate (task 4.5) ─────────────────────
        if comp.kill_switch and comp.kill_switch.is_triggered:
            log.warning(
                "single_tick: kill-switch active",
                reason=comp.kill_switch.trigger_reason,
            )
            snapshot = comp.pm.take_snapshot(candle_time, {symbol: close})
            try:
                with get_session() as session:
                    _persist_tick_results(
                        session=session,
                        comp=comp,
                        strategy_name=strategy_name,
                        symbol=symbol,
                        execution_result=None,
                        snapshot=snapshot,
                        signal_value="hold",
                        candle_time=candle_time,
                    )
            except Exception as persist_exc:
                log.error("single_tick: kill-switch persist failed", error=str(persist_exc))
            _send_tick_alerts(
                strategy_name=strategy_name,
                symbol=symbol,
                execution_result=None,
                stop_hit=False,
                stop_type=None,
                close=close,
                comp=comp,
                settings=settings,
            )
            return {"strategy": strategy_name, "symbol": symbol, "signal": None, "kill_switch": True}

        # ── 1b. Sync exchange stop orders ─────────────────────
        if comp.pm.has_position(symbol) and hasattr(comp.executor, "check_exchange_stop"):
            stop_fill = comp.executor.check_exchange_stop(symbol)
            if stop_fill and stop_fill.get("status") == "FILLED":
                from core.models import ExitReason, RiskAction, RiskDecision
                fill_price = stop_fill["fill_price"]
                position = comp.pm.get_position(symbol)
                trade = comp.pm.close_position(
                    symbol=symbol,
                    exit_price=fill_price,
                    exit_time=candle_time,
                    exit_reason=ExitReason.HARD_STOP,
                    strategy=strategy_name,
                )
                log.info(
                    "single_tick: exchange stop filled between ticks",
                    category="trade",
                    symbol=symbol,
                    fill_price=str(fill_price),
                    pnl_usdt=str(trade.pnl_usdt),
                )
                comp.strategy._cooldown_remaining = getattr(comp.strategy, "_cooldown_candles", 16)
                comp.strategy._state = StrategyState.COOLDOWN
                from core.models import ExecutionResult, Order, OrderSide, OrderStatus, OrderType
                synth_order = Order(
                    symbol=symbol,
                    side=OrderSide.SELL,
                    order_type=OrderType.STOP_LOSS_LIMIT,
                    amount=trade.size,
                    price=fill_price,
                    status=OrderStatus.FILLED,
                    avg_fill_price=fill_price,
                    filled_amount=trade.size,
                )
                execution_result = ExecutionResult(order=synth_order, trade=trade)
                snapshot = comp.pm.take_snapshot(candle_time, {symbol: close})
                try:
                    with get_session() as session:
                        _persist_tick_results(
                            session=session, comp=comp,
                            strategy_name=strategy_name, symbol=symbol,
                            execution_result=execution_result, snapshot=snapshot,
                            signal_value="hold", candle_time=candle_time,
                        )
                        session.commit()
                except Exception as exc:
                    log.error("single_tick: exchange stop persist failed", error=str(exc))
                _send_tick_alerts(
                    strategy_name=strategy_name,
                    symbol=symbol,
                    execution_result=execution_result,
                    stop_hit=True,
                    stop_type="hard_stop",
                    close=close,
                    comp=comp,
                    settings=settings,
                )
                return {
                    "strategy": strategy_name, "symbol": symbol,
                    "signal": "hold", "stop_hit": True,
                    "exit_reason": "hard_stop", "exchange_stop_sync": True,
                }

        # ── 2. Check stops ───────────────────────────────────
        stop_result = comp.risk_engine.check_stops(
            market_data, comp.pm.portfolio,
        )

        execution_result = None

        if stop_result.stop_hit:
            log.info(
                "single_tick: stop hit",
                category="risk",
                exit_reason=str(stop_result.decision.exit_reason),
                close=str(close),
            )
            execution_result = comp.executor.execute(
                stop_result.decision, candle_time,
            )
            if execution_result is None:
                log.error(
                    "single_tick: stop execution FAILED — position still open",
                    exit_reason=str(stop_result.decision.exit_reason),
                    symbol=symbol,
                    strategy=strategy_name,
                    close=str(close),
                )
                if settings.alerting.enabled:
                    try:
                        from core.alerting import get_alerter
                        get_alerter().alert_error(
                            task=f"stop:{strategy_name}:{symbol}",
                            error=f"Stop {stop_result.decision.exit_reason} triggered but sell order FAILED. Position still open at {close}.",
                        )
                    except Exception:
                        pass
            elif stop_result.cooldown_candles and stop_result.cooldown_candles > 0:
                comp.strategy._cooldown_remaining = stop_result.cooldown_candles
                comp.strategy._state = StrategyState.COOLDOWN
        elif comp.pm.has_position(symbol):
            old_pos = comp.pm.get_position(symbol)
            old_effective_stop = max(
                old_pos.hard_stop_price,
                old_pos.trailing_stop_price,
            ) if old_pos else Decimal("0")

            comp.pm.update_stops(
                symbol,
                highest_close=stop_result.highest_close,
                lowest_close=stop_result.lowest_close,
                trailing_stop_price=stop_result.trailing_stop_price,
                hard_stop_price=stop_result.hard_stop_price,
            )

            if hasattr(comp.executor, "_exchange_stops_enabled") and comp.executor._exchange_stops_enabled:
                updated_pos = comp.pm.get_position(symbol)
                if updated_pos:
                    from core.models import PositionSide as _PosSide
                    _is_short = updated_pos.side == _PosSide.SHORT

                    new_effective_stop = max(
                        updated_pos.hard_stop_price,
                        updated_pos.trailing_stop_price,
                    )

                    def _place_stop() -> str | None:
                        if _is_short:
                            return comp.executor._place_margin_stop_order(
                                symbol, updated_pos.size, new_effective_stop,
                            )
                        return comp.executor._place_stop_order(
                            symbol, updated_pos.size,
                            new_effective_stop, comp.executor._stop_slippage,
                        )

                    def _cancel_stop(oid: str) -> bool:
                        if _is_short:
                            return comp.executor._cancel_futures_order(symbol, oid)
                        return comp.executor._cancel_stop_order(symbol, oid)

                    def _force_stop_exit() -> None:
                        """Exchange says stop would trigger now — execute immediate close."""
                        nonlocal execution_result, stop_result
                        from core.models import ExitReason, RiskAction, RiskDecision
                        log.warning(
                            "single_tick: stop would trigger immediately — forcing exit",
                            symbol=symbol, stop_price=str(new_effective_stop),
                        )
                        forced_decision = RiskDecision(
                            action=RiskAction.CLOSE,
                            symbol=symbol,
                            price=close,
                            size=updated_pos.size,
                            exit_reason=ExitReason.HARD_STOP,
                        )
                        execution_result = comp.executor.execute(forced_decision, candle_time)
                        if execution_result is not None:
                            comp.strategy._cooldown_remaining = getattr(comp.strategy, "_cooldown_candles", 16)
                            comp.strategy._state = StrategyState.COOLDOWN
                            stop_result = StopCheckResult(
                                decision=forced_decision,
                                hard_stop_price=new_effective_stop,
                                cooldown_candles=getattr(comp.strategy, "_cooldown_candles", 16),
                            )

                    if not updated_pos.exchange_stop_order_id and new_effective_stop > 0:
                        log.info(
                            "single_tick: placing missing exchange stop order",
                            symbol=symbol, stop_price=str(new_effective_stop),
                        )
                        new_stop_id = _place_stop()
                        if new_stop_id == "TRIGGER_NOW":
                            _force_stop_exit()
                        elif new_stop_id:
                            comp.pm.update_stop_order_id(symbol, new_stop_id)
                    elif new_effective_stop > old_effective_stop and updated_pos.exchange_stop_order_id:
                        if _cancel_stop(updated_pos.exchange_stop_order_id):
                            new_stop_id = _place_stop()
                            if new_stop_id == "TRIGGER_NOW":
                                _force_stop_exit()
                            elif new_stop_id:
                                comp.pm.update_stop_order_id(symbol, new_stop_id)
                            else:
                                comp.pm.update_stop_order_id(symbol, None)
                                log.warning(
                                    "single_tick: stop ratchet placement failed, will retry",
                                    symbol=symbol,
                                    stop_price=str(new_effective_stop),
                                )

        # ── 3. Strategy decision ─────────────────────────────
        signal = Signal.HOLD
        pre_decide_state = comp.strategy.state
        if not stop_result.stop_hit:
            signal = comp.strategy.evaluate(market_data, comp.pm.portfolio)

            # ── 3b. Regime detection + gate ─────────────────
            if comp.regime_detector is not None:
                from core.regime import MarketRegime

                classification = comp.regime_detector.detect(market_data.indicators)
                detected = classification.regime
                gate_blocked = False

                if signal in (Signal.BUY, Signal.SHORT) and settings.regime.regime_gating_enabled:
                    expected = settings.regime.strategy_regime_map.get(strategy_name)
                    if (
                        expected is not None
                        and detected != MarketRegime.UNCERTAIN
                        and detected.value != expected
                    ):
                        log.info(
                            "regime.gate_blocked",
                            strategy=strategy_name,
                            symbol=symbol,
                            expected_regime=expected,
                            detected_regime=detected.value,
                            adx=classification.adx_value,
                        )
                        signal = Signal.HOLD
                        gate_blocked = True

                publisher.publish(
                    EventChannel.STRATEGY,
                    EventType.REGIME_DETECTED,
                    {
                        "symbol": symbol,
                        "strategy": strategy_name,
                        "regime": detected.value,
                        "confidence": classification.confidence,
                        "adx_value": classification.adx_value,
                        "gate_blocked": gate_blocked,
                    },
                )

            # ── 3c. Max concurrent positions guard ──────────
            if (
                signal in (Signal.BUY, Signal.SHORT)
                and not comp.pm.has_position(symbol)
                and _count_global_open_positions() >= settings.risk.max_concurrent_positions
            ):
                log.info(
                    "max_positions_reached",
                    symbol=symbol,
                    max=settings.risk.max_concurrent_positions,
                    current=_count_global_open_positions(),
                )
                signal = Signal.HOLD

            # ── 4. Risk evaluation ───────────────────────────
            decision = comp.risk_engine.evaluate(
                signal, market_data, comp.pm.portfolio,
            )

            # ── 5. Execute ───────────────────────────────────
            execution_result = comp.executor.execute(decision, candle_time)

            # ── 5b. Rollback state if execution didn't happen ─
            if execution_result is None and comp.strategy.state != pre_decide_state:
                comp.strategy._state = pre_decide_state

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
            # If a sell just closed the position but the full persist
            # failed, ensure the PositionRecord and strategy state are
            # still cleaned up so a worker restart doesn't resurrect
            # the position and trigger repeated SELL alerts.
            if not comp.pm.has_position(symbol):
                try:
                    _emergency_position_cleanup(
                        strategy_name, symbol, comp.strategy.state.value, log,
                    )
                except Exception as cleanup_exc:
                    log.error(
                        "single_tick: emergency position cleanup also failed",
                        error=str(cleanup_exc),
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
            category="signal",
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

    active_keys = {(s, sym) for s, sym, _ in pairs}
    stale_keys = [k for k in _worker_state if k not in active_keys]
    for k in stale_keys:
        del _worker_state[k]
        logger.info("run_strategy_tick: evicted stale worker state", category="state", strategy=k[0], symbol=k[1])

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

