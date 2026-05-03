"""
Celery tasks for the GoldenGibbon trading pipeline.

This package is split into focused submodules:
- _state.py:          Worker process state, recovery, component factory
- _persistence.py:    DB persistence and alerting helpers
- _tick.py:           Strategy-tick tasks and helpers
- _reconciliation.py: Portfolio reconciliation tasks

All public names are re-exported here so that existing imports
(``from core.tasks import X``) continue to work.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict

import structlog

from core.celery_app import app

logger = structlog.get_logger(__name__)


# ── Constants ─────────────────────────────────────────────────────────────────

_FETCH_LIMIT = 5
_HEARTBEAT_KEY = "gg:heartbeat"
_HEARTBEAT_TTL = 180  # seconds


# ── fetch_candles (task 3.4) ──────────────────────────────────────────────────


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


# ── Heartbeat (task 3.10) ─────────────────────────────────────────────────────


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


# ── Re-exports (public API) ───────────────────────────────────────────────────

from core.tasks._state import (  # noqa: E402, F401
    _get_strategy_registry,
    _recover_state,
    _TickComponents,
    _worker_state,
    _WorkerStateKey,
    clear_worker_state,
    _get_or_create_components,
    _MAX_MEMORY_TRADES,
    _MAX_MEMORY_SNAPSHOTS,
)
from core.tasks._persistence import (  # noqa: E402, F401
    _persist_tick_results,
    _trim_in_memory_history,
    _emergency_position_cleanup,
    _send_tick_alerts,
)
from core.tasks._tick import (  # noqa: E402, F401
    _acquire_tick_lock,
    _get_enabled_strategy_pairs,
    _resolve_allocated_capital,
    _fetch_latest_adx,
    _peek_latest_candle_time,
    run_single_strategy_tick,
    run_strategy_tick,
    _LOOKBACK_DAYS,
    _TICK_LOCK_TTL,
)
from core.tasks._reconciliation import (  # noqa: E402, F401
    _reconcile_pair,
    _reconcile_with_exchange,
    run_reconciliation,
    _PNL_TOLERANCE,
    _EXCHANGE_BALANCE_TOLERANCE,
    _EXCHANGE_POSITION_TOLERANCE,
)
