"""
WebSocket endpoint for real-time event streaming.

Implements tasks **2.14** (WebSocket endpoint) and **2.15**
(ConnectionManager) from the Phase 2 kanban.

Clients connect to ``/ws`` (optionally filtering channels via the
``channels`` query parameter) and receive a live stream of events
published by the core trading loop through Redis pub/sub.

Architecture::

    Core loop  ─→  EventPublisher (sync)  ─→  Redis pub/sub
                                                     │
    Dashboard  ←─  WebSocket  ←─  ConnectionManager  ←┘
                                    (async subscriber)

Usage::

    # Subscribe to all channels (default):
    ws = new WebSocket("ws://localhost:8000/ws")

    # Subscribe to specific channels:
    ws = new WebSocket("ws://localhost:8000/ws?channels=gg:strategy,gg:execution")
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import structlog
from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState

from core.events import EventChannel

logger = structlog.get_logger(__name__)

# All valid channel values for validation
_VALID_CHANNELS: set[str] = {ch.value for ch in EventChannel}

# Heartbeat interval in seconds
HEARTBEAT_INTERVAL = 30

router = APIRouter()


# ── Connection Manager ───────────────────────────────────────────────────────


class ConnectionManager:
    """
    Track active WebSocket connections and their channel subscriptions.

    Each connected client may subscribe to a subset of
    :class:`~core.events.EventChannel` values.  The manager filters
    outbound messages so clients only receive events from their
    subscribed channels.

    Thread-safety is *not* required — all access is from the single
    asyncio event loop running the FastAPI server.
    """

    def __init__(self) -> None:
        self._connections: dict[WebSocket, set[str]] = {}

    # ── Lifecycle ────────────────────────────────────────────────────

    async def connect(self, ws: WebSocket, channels: set[str]) -> None:
        """Accept the WebSocket and register it with *channels*."""
        await ws.accept()
        self._connections[ws] = channels
        logger.info(
            "ws.connected",
            channels=sorted(channels),
            active=self.active_count,
        )

    def disconnect(self, ws: WebSocket) -> None:
        """Remove the WebSocket from the registry."""
        self._connections.pop(ws, None)
        logger.info("ws.disconnected", active=self.active_count)

    # ── Messaging ────────────────────────────────────────────────────

    async def send_personal(self, ws: WebSocket, data: dict[str, Any]) -> None:
        """Send JSON to a single client, ignoring channel filters."""
        if ws.client_state == WebSocketState.CONNECTED:
            try:
                await ws.send_json(data)
            except Exception:
                pass  # client already gone

    async def broadcast(self, channel: str, payload: str) -> None:
        """
        Forward *payload* to all clients subscribed to *channel*.

        *payload* is the raw JSON string from Redis (already serialised
        by :meth:`Event.model_dump_json`).  It is sent as a text frame
        to avoid an unnecessary decode → re-encode cycle.
        """
        stale: list[WebSocket] = []
        for ws, subscribed in self._connections.items():
            if channel not in subscribed:
                continue
            try:
                if ws.client_state == WebSocketState.CONNECTED:
                    await ws.send_text(payload)
            except Exception:
                stale.append(ws)

        for ws in stale:
            self.disconnect(ws)

    # ── Properties ───────────────────────────────────────────────────

    @property
    def active_count(self) -> int:
        """Number of currently connected clients."""
        return len(self._connections)

    def channels_for(self, ws: WebSocket) -> set[str] | None:
        """Return the channel set for *ws*, or ``None`` if not tracked."""
        return self._connections.get(ws)


# Module-level singleton
manager = ConnectionManager()


# ── Helpers ──────────────────────────────────────────────────────────────────


def _parse_channels(raw: str | None) -> set[str]:
    """
    Parse and validate the ``channels`` query parameter.

    Returns the set of valid channel names.  If *raw* is ``None`` or
    empty, returns **all** channels.
    """
    if not raw:
        return set(_VALID_CHANNELS)

    requested = {c.strip() for c in raw.split(",") if c.strip()}
    valid = requested & _VALID_CHANNELS

    if not valid:
        # None of the requested channels are recognised — fall back to all.
        return set(_VALID_CHANNELS)

    return valid


# ── WebSocket Endpoint ───────────────────────────────────────────────────────


@router.websocket("/ws")
async def websocket_endpoint(
    ws: WebSocket,
    channels: str | None = Query(default=None),
) -> None:
    """
    Stream real-time events over WebSocket.

    Clients may filter channels via the ``channels`` query parameter
    (comma-separated list of ``gg:*`` channel names).  Omitting the
    parameter subscribes to **all** channels.

    Protocol
    --------
    1. Server sends a ``welcome`` message with the subscribed channels.
    2. Server forwards Redis pub/sub events matching the subscription.
    3. Server sends ``ping`` frames every 30 s as a keep-alive heartbeat.
    4. Client can send ``close`` or any text; server ignores non-control
       frames.
    """
    redis = getattr(ws.app.state, "redis", None)

    if redis is None:
        await ws.accept()
        await ws.send_json({
            "type": "error",
            "message": "Real-time events unavailable (Redis not connected)",
        })
        await ws.close(code=1011, reason="Redis unavailable")
        return

    subscribed = _parse_channels(channels)
    await manager.connect(ws, subscribed)

    # Welcome message
    await manager.send_personal(ws, {
        "type": "welcome",
        "channels": sorted(subscribed),
        "message": "Connected to GoldenGibbon event stream",
    })

    # Subscribe to all channels on the Redis side and filter per-client
    # in the manager.  This keeps a single pubsub object per connection.
    pubsub = redis.pubsub()
    try:
        await pubsub.subscribe(*sorted(_VALID_CHANNELS))

        # Run listener, receiver, and heartbeat concurrently
        listener_task = asyncio.create_task(_redis_listener(ws, pubsub))
        receiver_task = asyncio.create_task(_client_receiver(ws))
        heartbeat_task = asyncio.create_task(_heartbeat(ws))

        # Wait until any task finishes (disconnect, error, etc.)
        done, pending = await asyncio.wait(
            [listener_task, receiver_task, heartbeat_task],
            return_when=asyncio.FIRST_COMPLETED,
        )

        # Cancel remaining tasks
        for task in pending:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

    except Exception as exc:
        logger.error("ws.error", error=str(exc))
    finally:
        await pubsub.unsubscribe()
        await pubsub.aclose()
        manager.disconnect(ws)


# ── Concurrent Tasks ─────────────────────────────────────────────────────────


async def _redis_listener(ws: WebSocket, pubsub: Any) -> None:
    """
    Read messages from Redis pub/sub and broadcast through the manager.

    Exits when the WebSocket disconnects or Redis becomes unavailable.
    """
    try:
        while ws.client_state == WebSocketState.CONNECTED:
            message = await pubsub.get_message(
                ignore_subscribe_messages=True,
                timeout=1.0,
            )
            if message is not None and message["type"] == "message":
                channel = message["channel"]
                payload = message["data"]
                await manager.broadcast(channel, payload)
            else:
                # Yield control when no message available
                await asyncio.sleep(0.05)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.debug("ws.listener_stopped", error=str(exc))


async def _client_receiver(ws: WebSocket) -> None:
    """
    Consume incoming frames from the client.

    We don't expect meaningful messages from the client, but we must
    read from the socket to detect disconnects (``WebSocketDisconnect``
    exception).
    """
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    except Exception:
        pass


async def _heartbeat(ws: WebSocket) -> None:
    """
    Send periodic ping payloads as keep-alive.

    Exits when the WebSocket is no longer connected.
    """
    try:
        while ws.client_state == WebSocketState.CONNECTED:
            await asyncio.sleep(HEARTBEAT_INTERVAL)
            if ws.client_state == WebSocketState.CONNECTED:
                await ws.send_json({"type": "ping"})
    except asyncio.CancelledError:
        raise
    except Exception:
        pass
