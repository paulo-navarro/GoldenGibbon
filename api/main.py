"""
FastAPI application for the GoldenGibbon trading platform.

Provides:

* **REST endpoints** for historical data, portfolio state, and system health
  (mounted under ``/api/...`` by route modules in :mod:`api.routes`).
* **WebSocket endpoint** for real-time event streaming (task 2.14).
* **Lifespan hooks** that initialise / tear down the event publisher,
  verify the database connection, and manage an async Redis client used
  by the WebSocket subscriber.

Start with::

    uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import AsyncGenerator

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from core.config import get_settings
from core.events import (
    EventChannel,
    EventType,
    init_publisher,
    reset_publisher,
)
from core.logging import setup_logging
from db import check_connection

logger = structlog.get_logger(__name__)


# ── Lifespan ─────────────────────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """
    Application lifespan handler (startup / shutdown).

    **Startup:**
    1. Configure structured logging.
    2. Verify database connectivity (warn if unreachable).
    3. Initialise the synchronous event publisher (core engine → Redis).
    4. Create an async Redis client for WebSocket pub/sub and store it
       on ``app.state.redis`` (consumed by the WS endpoint in task 2.14).
    5. Publish a ``STARTUP`` event.

    **Shutdown:**
    1. Publish a ``SHUTDOWN`` event.
    2. Close the async Redis client.
    3. Tear down the event publisher singleton.
    """
    # ── Startup ──────────────────────────────────────────────────────
    settings = get_settings()
    setup_logging(settings.logging)
    logger.info("api.startup", environment=settings.system.environment)

    # Database
    if check_connection():
        logger.info("api.db_connected")
    else:
        logger.warning("api.db_unreachable")

    # Sync event publisher (fire-and-forget from core components)
    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    publisher = init_publisher(redis_url)

    # Async Redis client (for WebSocket pub/sub subscriber)
    async_redis = None
    try:
        import redis.asyncio as aioredis

        async_redis = aioredis.from_url(
            redis_url,
            decode_responses=True,
            socket_connect_timeout=3,
        )
        await async_redis.ping()
        logger.info("api.async_redis_connected")
    except Exception as exc:
        logger.warning("api.async_redis_unavailable", error=str(exc))
        async_redis = None

    app.state.redis = async_redis
    app.state.settings = settings

    # Announce ready
    publisher.publish(EventChannel.SYSTEM, EventType.STARTUP, {
        "service": "api",
        "environment": settings.system.environment,
    })

    yield  # ── Application is running ────────────────────────────────

    # ── Shutdown ─────────────────────────────────────────────────────
    logger.info("api.shutdown")
    publisher.publish(EventChannel.SYSTEM, EventType.SHUTDOWN, {
        "service": "api",
    })

    if async_redis is not None:
        await async_redis.close()

    reset_publisher()
    logger.info("api.shutdown_complete")


# ── App Factory ──────────────────────────────────────────────────────────────


def create_app() -> FastAPI:
    """
    Build and return the fully-configured :class:`FastAPI` application.

    Uses a factory function so tests can create isolated app instances.
    """
    application = FastAPI(
        title="GoldenGibbon",
        version="0.1.0",
        description="Crypto quantitative trading platform — REST & WebSocket API",
        lifespan=lifespan,
    )

    # ── CORS ─────────────────────────────────────────────────────────
    origins_raw = os.getenv("CORS_ORIGINS", "http://localhost:5858")
    origins = [o.strip() for o in origins_raw.split(",") if o.strip()]

    application.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ── Routes ───────────────────────────────────────────────────────
    _include_routes(application)

    # ── WebSocket ────────────────────────────────────────────────────
    try:
        from api.websocket import router as ws_router

        application.include_router(ws_router)
        logger.debug("api.ws_mounted")
    except (ImportError, AttributeError):
        logger.debug("api.ws_skipped")

    return application


def _include_routes(application: FastAPI) -> None:
    """
    Mount all route modules.

    Each router is imported lazily so that missing route files
    (not yet implemented) don't prevent the app from starting.
    """
    route_modules = [
        ("api.routes.market", "/api/market", ["market"]),
        ("api.routes.portfolio", "/api/portfolio", ["portfolio"]),
        ("api.routes.trades", "/api/trades", ["trades"]),
        ("api.routes.orders", "/api/orders", ["orders"]),
        ("api.routes.strategy", "/api/strategy", ["strategy"]),
        ("api.routes.system", "/api/system", ["system"]),
        ("api.routes.backtest", "/api/backtest", ["backtest"]),
        ("api.routes.config", "/api/strategy/config", ["config"]),
        ("api.routes.symbols", "/api/config/symbols", ["symbols"]),
        ("api.routes.app_config", "/api/config", ["app-config"]),
    ]

    for module_path, prefix, tags in route_modules:
        try:
            import importlib

            mod = importlib.import_module(module_path)
            router = getattr(mod, "router", None)
            if router is not None:
                application.include_router(router, prefix=prefix, tags=tags)
                logger.debug("api.route_mounted", module=module_path, prefix=prefix)
        except (ImportError, AttributeError):
            logger.debug("api.route_skipped", module=module_path)


# ── Module-level app instance (uvicorn api.main:app) ────────────────────────

app = create_app()
