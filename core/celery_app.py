"""
Celery application configuration.

Creates the Celery instance used by workers and Beat.  Both
``docker-compose.yml`` services reference this module via::

    celery -A core.celery_app worker --loglevel=info
    celery -A core.celery_app beat  --loglevel=info

Redis serves as **both** the message broker and the result backend.
The ``gg:`` pub/sub namespace (see :mod:`core.events`) is unrelated to
Celery's own Redis keys, so there are no collisions.

Periodic tasks are defined in :data:`beat_schedule` and target stub
tasks in :mod:`core.tasks`.  The stubs will be replaced with real
implementations in tasks 3.4, 3.5 and 3.9.
"""

from __future__ import annotations

import os

from celery import Celery
from celery.schedules import crontab
from celery.signals import worker_process_init, worker_ready

# ── Redis URL ────────────────────────────────────────────────────────────────

REDIS_URL: str = os.environ.get("REDIS_URL", "redis://redis:6379/0")

# ── Celery App ───────────────────────────────────────────────────────────────

app = Celery("golden_gibbon")

app.conf.update(
    # ── Broker & backend ─────────────────────────────────────────────
    broker_url=REDIS_URL,
    result_backend=REDIS_URL,

    # ── Serialisation ────────────────────────────────────────────────
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    result_expires=3600,  # discard results after 1 hour

    # ── Timezone ─────────────────────────────────────────────────────
    timezone="UTC",
    enable_utc=True,

    # ── Reliability ──────────────────────────────────────────────────
    task_acks_late=True,          # re-queue if worker dies mid-task
    task_reject_on_worker_lost=True,
    task_track_started=True,

    # ── Concurrency ──────────────────────────────────────────────────
    worker_prefetch_multiplier=1,   # one task at a time per worker
    worker_max_tasks_per_child=100, # recycle to prevent memory leaks

    # ── Logging ──────────────────────────────────────────────────────
    worker_hijack_root_logger=False,

    # ── Beat schedule (Task 3.3) ─────────────────────────────────────
    beat_schedule={
        "fetch-candles-15m": {
            "task": "core.tasks.fetch_candles",
            # 1 min past each 15m boundary — Binance needs a moment to
            # finalise the candle at the exact boundary.
            "schedule": crontab(minute="1,16,31,46"),
        },
        "run-strategy-tick-15m": {
            "task": "core.tasks.run_strategy_tick",
            # 2 min past each 15m boundary — gives fetch_candles
            # ~60 s to populate the DB cache before the tick runs.
            "schedule": crontab(minute="2,17,32,47"),
        },
        "reconciliation-15m": {
            "task": "core.tasks.run_reconciliation",
            # Every 15 minutes at :05, :20, :35, :50.
            "schedule": crontab(minute="5,20,35,50"),
        },
        "sync-exchange-balances-2m": {
            "task": "core.tasks.sync_exchange_balances",
            # Every 2 minutes — keeps dashboard USDT balance and equity
            # in sync with the exchange between strategy ticks.
            "schedule": 120.0,
        },
        "heartbeat-60s": {
            "task": "core.tasks.emit_heartbeat",
            # Every 60 seconds — proves both Beat and a worker are alive.
            "schedule": 60.0,
        },
    },
)

# ── Task auto-discovery ──────────────────────────────────────────────────────

app.autodiscover_tasks(["core.tasks"])


# ── Worker logging ───────────────────────────────────────────────────────────

@worker_process_init.connect
def _setup_worker_logging(**_kwargs: object) -> None:
    """Wire up structured logging inside every worker process."""
    try:
        from core.config import get_settings
        from core.logging import setup_logging

        settings = get_settings()
        setup_logging(settings.logging)
    except Exception:  # pragma: no cover – best-effort
        import logging

        logging.basicConfig(level=logging.INFO)
        logging.getLogger(__name__).warning(
            "Failed to configure structured logging in Celery worker; "
            "falling back to basic logging.",
            exc_info=True,
        )


# ── Startup reconciliation (task 3.9) ────────────────────────────────────────

@worker_ready.connect
def _reconcile_on_startup(**_kwargs: object) -> None:
    """
    Enqueue a reconciliation check when the Celery worker is ready.

    Controlled by ``live_trading.reconcile_on_startup`` in
    ``settings.yaml`` (defaults to ``True``).  The task runs
    asynchronously inside the worker so it doesn't block startup.
    """
    try:
        from core.config import get_settings

        settings = get_settings()
        if settings.live_trading.reconcile_on_startup:
            from core.tasks import run_reconciliation

            run_reconciliation.delay()
    except Exception:  # pragma: no cover – best-effort
        import logging

        logging.getLogger(__name__).warning(
            "Failed to enqueue startup reconciliation.",
            exc_info=True,
        )
