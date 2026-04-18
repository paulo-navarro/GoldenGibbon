"""
Event publisher for real-time visibility and monitoring.

Publishes structured events to Redis pub/sub channels so that
downstream consumers (FastAPI WebSocket → React dashboard) can
display live state without polling.

**Design principles:**

* **Fire-and-forget** – ``publish()`` never raises.  If Redis is
  unreachable the call is silently logged and skipped.
* **Synchronous** – the core trading loop is sync; Redis pub/sub
  publish calls are sub-millisecond inside Docker networking.
* **Singleton** – ``get_publisher()`` returns a module-level instance,
  matching the ``get_settings()`` pattern in ``core.config``.

Usage::

    from core.events import get_publisher, EventChannel, EventType

    publisher = get_publisher()
    publisher.publish(EventChannel.STRATEGY, EventType.SIGNAL_GENERATED, {
        "symbol": "BTCUSDT",
        "signal": "buy",
    })

    # Or publish a Pydantic model directly:
    publisher.publish_model(EventChannel.EXECUTION, EventType.ORDER_FILLED, order)
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from enum import Enum
from typing import Any

import structlog
from pydantic import BaseModel, Field

logger = structlog.get_logger(__name__)


# ── Event Channels ───────────────────────────────────────────────────────────


class EventChannel(str, Enum):
    """
    Redis pub/sub channels.

    Each channel groups related events.  Prefixed with ``gg:`` to
    namespace events and avoid collisions with Celery or other Redis
    users sharing the same instance.
    """

    MARKET_DATA = "gg:market_data"
    STRATEGY = "gg:strategy"
    RISK = "gg:risk"
    EXECUTION = "gg:execution"
    PORTFOLIO = "gg:portfolio"
    SYSTEM = "gg:system"


# ── Event Types ──────────────────────────────────────────────────────────────


class EventType(str, Enum):
    """
    Identifiers for every event the platform can emit.

    Grouped by the channel they are typically published on.
    """

    # ── Market Data ──────────────────────────────────────────────────
    CANDLE_RECEIVED = "candle_received"
    INDICATORS_COMPUTED = "indicators_computed"
    PRICE_UPDATE = "price_update"

    # ── Strategy ─────────────────────────────────────────────────────
    SIGNAL_GENERATED = "signal_generated"
    STATE_CHANGED = "state_changed"
    CONDITIONS_EVALUATED = "conditions_evaluated"

    # ── Risk ─────────────────────────────────────────────────────────
    RISK_EVALUATED = "risk_evaluated"
    STOP_TRIGGERED = "stop_triggered"
    POSITION_SIZED = "position_sized"

    # ── Execution ────────────────────────────────────────────────────
    ORDER_CREATED = "order_created"
    ORDER_FILLED = "order_filled"
    ORDER_REJECTED = "order_rejected"
    ORDER_CANCELLED = "order_cancelled"

    # ── Portfolio ────────────────────────────────────────────────────
    POSITION_OPENED = "position_opened"
    POSITION_UPDATED = "position_updated"
    TRADE_CLOSED = "trade_closed"
    BALANCE_UPDATED = "balance_updated"
    EQUITY_UPDATED = "equity_updated"

    # ── System ───────────────────────────────────────────────────────
    HEARTBEAT = "heartbeat"
    ERROR = "error"
    WARNING = "warning"
    STARTUP = "startup"
    SHUTDOWN = "shutdown"

    # ── Kill-switch (task 4.5) ──────────────────────────────────────
    KILL_SWITCH_TRIGGERED = "kill_switch_triggered"

    # ── Reconciliation (task 3.9) ────────────────────────────────────
    RECONCILIATION_OK = "reconciliation_ok"
    RECONCILIATION_MISMATCH = "reconciliation_mismatch"
    RECONCILIATION_REPAIRED = "reconciliation_repaired"


# ── Event Envelope ───────────────────────────────────────────────────────────


class Event(BaseModel):
    """
    Standard envelope for every published event.

    Serialised to JSON and sent over the Redis channel; consumers
    deserialise back into this model to access typed fields.
    """

    event_type: EventType
    channel: EventChannel
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    data: dict[str, Any] = Field(default_factory=dict)


# ── Event Publisher ──────────────────────────────────────────────────────────


class EventPublisher:
    """
    Publishes :class:`Event` instances to Redis pub/sub.

    Parameters
    ----------
    redis_url:
        Full Redis connection string.  Falls back to the ``REDIS_URL``
        environment variable, then ``redis://localhost:6379/0``.
    """

    def __init__(self, redis_url: str | None = None) -> None:
        self._enabled = False
        self._client: Any = None  # redis.Redis – typed as Any to defer import

        url = redis_url or os.getenv("REDIS_URL", "redis://localhost:6379/0")

        try:
            import redis as redis_lib

            self._client = redis_lib.Redis.from_url(
                url,
                decode_responses=True,
                socket_connect_timeout=3,
                socket_timeout=3,
                retry_on_timeout=False,
            )
            # Verify connectivity with a lightweight PING.
            self._client.ping()
            self._enabled = True
            logger.info("event_publisher.connected", redis_url=url)
        except Exception as exc:
            logger.warning(
                "event_publisher.disabled",
                reason=str(exc),
                redis_url=url,
            )

    # ── Public API ───────────────────────────────────────────────────

    @property
    def enabled(self) -> bool:
        """``True`` when Redis is connected and events will be sent."""
        return self._enabled

    def publish(
        self,
        channel: EventChannel,
        event_type: EventType,
        data: dict[str, Any] | None = None,
    ) -> None:
        """
        Build an :class:`Event` and publish it to *channel*.

        If Redis is unreachable or serialisation fails the error is
        logged and swallowed — the trading loop is never interrupted.

        Parameters
        ----------
        channel:
            Target Redis pub/sub channel.
        event_type:
            Discriminator for the event.
        data:
            JSON-safe dict payload.  For Pydantic models use
            :meth:`publish_model` instead.
        """
        if not self._enabled:
            return

        event = Event(
            event_type=event_type,
            channel=channel,
            data=data or {},
        )

        try:
            payload = event.model_dump_json()
            self._client.publish(channel.value, payload)
            logger.debug(
                "event_publisher.published",
                channel=channel.value,
                event_type=event_type.value,
            )
        except Exception as exc:
            logger.error(
                "event_publisher.publish_failed",
                channel=channel.value,
                event_type=event_type.value,
                error=str(exc),
            )

    def publish_model(
        self,
        channel: EventChannel,
        event_type: EventType,
        model: BaseModel,
    ) -> None:
        """
        Convenience wrapper that serialises a Pydantic model as the payload.

        Handles ``Decimal`` and ``datetime`` fields automatically via
        Pydantic's ``mode="json"`` serialisation.

        .. note::

           ``MarketData`` contains ``pd.DataFrame`` fields that are
           **not** JSON-serialisable.  Publish individual ``Candle``
           instances or pre-built dicts for market data events.
        """
        self.publish(channel, event_type, data=model.model_dump(mode="json"))

    def close(self) -> None:
        """Close the underlying Redis connection."""
        if self._client is not None:
            try:
                self._client.close()
                logger.info("event_publisher.closed")
            except Exception as exc:
                logger.warning("event_publisher.close_failed", error=str(exc))
            finally:
                self._enabled = False
                self._client = None


# ── Module-level Singleton ───────────────────────────────────────────────────

_publisher: EventPublisher | None = None


def get_publisher() -> EventPublisher:
    """
    Return the module-level :class:`EventPublisher` singleton.

    Creates the instance on first call (lazy initialisation).
    """
    global _publisher  # noqa: PLW0603
    if _publisher is None:
        _publisher = EventPublisher()
    return _publisher


def init_publisher(redis_url: str | None = None) -> EventPublisher:
    """
    (Re-)initialise the module-level :class:`EventPublisher`.

    Use this at application startup to override the default
    ``REDIS_URL`` or to force reconnection.
    """
    global _publisher  # noqa: PLW0603
    if _publisher is not None:
        _publisher.close()
    _publisher = EventPublisher(redis_url=redis_url)
    return _publisher


def reset_publisher() -> None:
    """
    Tear down the singleton.  Primarily used by tests.
    """
    global _publisher  # noqa: PLW0603
    if _publisher is not None:
        _publisher.close()
    _publisher = None
