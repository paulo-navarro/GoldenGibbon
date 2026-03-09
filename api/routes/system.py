"""
System REST endpoints – health check and log viewer.

Provides system status and operational visibility. Mounted at
``/api/system`` by :func:`api.main._include_routes`.

Endpoints
---------
GET /health
    Liveness / readiness probe with DB, Redis, environment, and timestamp.

GET /logs
    Tail the structured log file with optional line count and level filter.
"""

from __future__ import annotations

import asyncio
import os
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from db import check_connection

router = APIRouter()


# ── Response Models ──────────────────────────────────────────────────────────


class HealthResponse(BaseModel):
    status: str
    db: bool
    redis: bool
    celery_worker: bool
    celery_beat: bool
    environment: str
    timestamp: str


class LogsResponse(BaseModel):
    file: str
    total_lines: int
    lines: list[str]


# ── Helpers ──────────────────────────────────────────────────────────────────


def _get_log_path() -> str:
    """Return the log file path from settings, with a safe fallback."""
    try:
        from core.config import get_settings
        return get_settings().logging.file_path
    except Exception:
        return "logs/trading.log"


def _probe_celery_worker() -> bool:
    """
    Ping Celery workers with a short timeout.

    Returns ``True`` if at least one worker responds.
    """
    try:
        from core.celery_app import app as celery_app

        responses = celery_app.control.ping(timeout=1.0)
        return len(responses) > 0
    except Exception:
        return False


def _check_heartbeat_key() -> bool:
    """
    Check whether the ``gg:heartbeat:last`` Redis key exists.

    This key is written by the :func:`core.tasks.emit_heartbeat` task
    with a 120 s TTL.  If it exists, both Beat (scheduled the task)
    and a worker (executed it) were alive within that window.
    """
    try:
        import redis as _redis

        redis_url = os.environ.get("REDIS_URL", "redis://redis:6379/0")
        r = _redis.Redis.from_url(redis_url)
        return bool(r.exists("gg:heartbeat:last"))
    except Exception:
        return False


def _tail_file(path: str, n: int, level_filter: Optional[str] = None) -> list[str]:
    """
    Read the last *n* lines from *path*, optionally filtering by log level.

    Uses a deque for memory-efficient tailing regardless of file size.
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(path)

    with p.open("r", encoding="utf-8", errors="replace") as f:
        if level_filter is None:
            lines = list(deque(f, maxlen=n))
        else:
            # Read all lines and filter, then take last N
            needle = f'"level": "{level_filter.lower()}"'
            filtered = [line for line in f if needle in line.lower()]
            lines = filtered[-n:]

    # Strip trailing newlines
    return [line.rstrip("\n") for line in lines]


# ── Endpoints ────────────────────────────────────────────────────────────────


@router.get("/health", response_model=HealthResponse)
async def health(request: Request) -> HealthResponse:
    """
    Liveness / readiness probe.

    Returns the connection status of Postgres, Redis, Celery worker,
    Celery Beat, the current environment, and server timestamp.
    """
    redis_ok = False
    if hasattr(request.app.state, "redis") and request.app.state.redis is not None:
        try:
            await request.app.state.redis.ping()
            redis_ok = True
        except Exception:
            pass

    # Run blocking Celery probes in a thread to avoid blocking the event loop.
    worker_ok, beat_ok = await asyncio.gather(
        asyncio.to_thread(_probe_celery_worker),
        asyncio.to_thread(_check_heartbeat_key),
    )

    return HealthResponse(
        status="ok",
        db=check_connection(),
        redis=redis_ok,
        celery_worker=worker_ok,
        celery_beat=beat_ok,
        environment=os.getenv("ENVIRONMENT", "development"),
        timestamp=datetime.now(timezone.utc).isoformat(),
    )


@router.get("/logs", response_model=LogsResponse)
def get_logs(
    lines: int = Query(100, ge=1, le=5000, description="Number of lines to return (from tail)"),
    level: Optional[str] = Query(None, description="Filter by log level (e.g. info, error, warning)"),
) -> LogsResponse:
    """
    Return the last *lines* entries from the structured log file.

    Reads directly from disk. The log file path is determined by
    ``settings.yaml`` logging configuration.
    """
    log_path = _get_log_path()

    try:
        result = _tail_file(log_path, lines, level_filter=level)
    except FileNotFoundError:
        raise HTTPException(
            status_code=404,
            detail=f"Log file not found: {log_path}",
        )

    return LogsResponse(
        file=log_path,
        total_lines=len(result),
        lines=result,
    )
