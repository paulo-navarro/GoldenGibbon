"""
Binance kline WebSocket client.

Connects to the Binance combined-stream endpoint and delivers
real-time kline updates to caller-supplied callbacks.

Architecture
~~~~~~~~~~~~

A single multiplexed WebSocket carries all symbol/timeframe pairs::

    wss://stream.binance.com:9443/stream?streams=btcusdt@kline_15m/ethusdt@kline_15m/...

Each incoming message is parsed into a :class:`KlineUpdate` dict and
dispatched to:

* ``on_update`` – called on **every** message (~2 s cadence per stream).
  Used for ``PRICE_UPDATE`` events so the dashboard shows live prices.
* ``on_close``  – called **only** when ``kline.x == True`` (candle is
  final).  Used for ``CANDLE_RECEIVED`` + immediate tick trigger.

The client reconnects automatically with exponential back-off (jitter)
and resets the delay on every successful connection.  Graceful shutdown
is coordinated via an ``asyncio.Event``.

Usage::

    stream = BinanceKlineStream(
        base_url="wss://stream.binance.com:9443",
        reconnect_delay=1.0,
        max_reconnect_delay=60.0,
    )
    await stream.run(
        symbols=["BTCUSDT", "ETHUSDT"],
        timeframes=["15m", "1h"],
        on_update=handle_price_update,
        on_close=handle_candle_close,
    )
"""

from __future__ import annotations

import asyncio
import json
import random
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Awaitable, Callable, Dict, List, Optional, TypedDict

import structlog

logger = structlog.get_logger(__name__)


# ── Parsed payload ───────────────────────────────────────────────────────────


class KlineUpdate(TypedDict):
    """Parsed fields forwarded to callbacks."""

    symbol: str
    timeframe: str
    open_time: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    is_closed: bool


# ── Timeframe mapping ───────────────────────────────────────────────────────

# Binance stream names use short codes that differ slightly from the REST
# interval parameter.  For the intervals we support they are identical, but
# the mapping keeps the conversion explicit.
_TF_TO_STREAM: Dict[str, str] = {
    "1m": "1m",
    "5m": "5m",
    "15m": "15m",
    "30m": "30m",
    "1h": "1h",
    "4h": "4h",
    "1d": "1d",
    "1w": "1w",
}

# Reverse mapping: Binance kline interval tag → our canonical timeframe.
_STREAM_TO_TF: Dict[str, str] = {v: k for k, v in _TF_TO_STREAM.items()}


# ── Stream Client ────────────────────────────────────────────────────────────

# Callback type: receives parsed KlineUpdate, may be sync or async.
OnUpdateCallback = Callable[[KlineUpdate], Any]
OnCloseCallback = Callable[[KlineUpdate], Any]


def parse_kline_message(raw: dict) -> Optional[KlineUpdate]:
    """
    Parse a Binance combined-stream kline payload into a :class:`KlineUpdate`.

    Returns ``None`` if the message structure is unrecognised.
    """
    try:
        data = raw.get("data", raw)  # combined-stream wraps in {"stream": ..., "data": ...}
        if data.get("e") != "kline":
            return None

        k = data["k"]
        interval = k["i"]
        timeframe = _STREAM_TO_TF.get(interval, interval)

        return KlineUpdate(
            symbol=data["s"],
            timeframe=timeframe,
            open_time=datetime.fromtimestamp(k["t"] / 1000, tz=timezone.utc),
            open=Decimal(k["o"]),
            high=Decimal(k["h"]),
            low=Decimal(k["l"]),
            close=Decimal(k["c"]),
            volume=Decimal(k["v"]),
            is_closed=k["x"],
        )
    except (KeyError, TypeError, ValueError) as exc:
        logger.warning("binance_ws.parse_failed", error=str(exc))
        return None


class BinanceKlineStream:
    """
    Async Binance kline WebSocket client with auto-reconnect.

    Parameters
    ----------
    base_url:
        Binance stream base URL (default ``wss://stream.binance.com:9443``).
    reconnect_delay:
        Initial reconnect delay in seconds.
    max_reconnect_delay:
        Maximum cap for exponential back-off.
    ping_interval:
        Seconds between WebSocket pings.
    ping_timeout:
        Seconds to wait for a pong before treating the connection as dead.
    """

    def __init__(
        self,
        base_url: str = "wss://stream.binance.com:9443",
        reconnect_delay: float = 1.0,
        max_reconnect_delay: float = 60.0,
        ping_interval: int = 20,
        ping_timeout: int = 10,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._reconnect_delay = reconnect_delay
        self._max_reconnect_delay = max_reconnect_delay
        self._ping_interval = ping_interval
        self._ping_timeout = ping_timeout
        self._stop_event: asyncio.Event = asyncio.Event()

    # ── URL builder ──────────────────────────────────────────────────

    def build_stream_url(
        self,
        symbols: List[str],
        timeframes: List[str],
    ) -> str:
        """
        Build the combined-stream URL for all symbol/timeframe pairs.

        Example::

            wss://stream.binance.com:9443/stream?streams=btcusdt@kline_15m/ethusdt@kline_15m
        """
        streams: List[str] = []
        for symbol in symbols:
            for tf in timeframes:
                stream_tf = _TF_TO_STREAM.get(tf, tf)
                streams.append(f"{symbol.lower()}@kline_{stream_tf}")

        return f"{self._base_url}/stream?streams={'/'.join(streams)}"

    # ── Main loop ────────────────────────────────────────────────────

    async def run(
        self,
        symbols: List[str],
        timeframes: List[str],
        on_update: OnUpdateCallback,
        on_close: OnCloseCallback,
    ) -> None:
        """
        Connect and listen forever (until :meth:`stop` is called).

        Reconnects automatically on connection failure with exponential
        back-off plus jitter.  Delay resets on each successful connect.
        """
        import websockets
        from websockets.exceptions import ConnectionClosed

        url = self.build_stream_url(symbols, timeframes)
        delay = self._reconnect_delay

        logger.info(
            "binance_ws.starting",
            url=url,
            symbols=symbols,
            timeframes=timeframes,
        )

        while not self._stop_event.is_set():
            try:
                async with websockets.connect(
                    url,
                    ping_interval=self._ping_interval,
                    ping_timeout=self._ping_timeout,
                ) as ws:
                    logger.info("binance_ws.connected", url=url)
                    delay = self._reconnect_delay  # reset on success

                    await self._listen(ws, on_update, on_close)

            except ConnectionClosed as exc:
                logger.warning(
                    "binance_ws.connection_closed",
                    code=exc.code,
                    reason=str(exc.reason),
                )
            except Exception as exc:
                logger.error(
                    "binance_ws.connection_error",
                    error=str(exc),
                )

            if self._stop_event.is_set():
                break

            # Exponential back-off with jitter
            jitter = random.uniform(0, delay * 0.5)
            wait = min(delay + jitter, self._max_reconnect_delay)
            logger.info("binance_ws.reconnecting", delay=round(wait, 2))

            try:
                await asyncio.wait_for(
                    self._stop_event.wait(), timeout=wait,
                )
                break  # stop_event was set during wait
            except asyncio.TimeoutError:
                pass  # normal — timeout expired, reconnect now

            delay = min(delay * 2, self._max_reconnect_delay)

        logger.info("binance_ws.stopped")

    # ── Message processing ───────────────────────────────────────────

    async def _listen(
        self,
        ws: Any,
        on_update: OnUpdateCallback,
        on_close: OnCloseCallback,
    ) -> None:
        """Read messages until the connection drops or stop is requested."""
        async for raw_msg in ws:
            if self._stop_event.is_set():
                break

            try:
                payload = json.loads(raw_msg)
            except json.JSONDecodeError:
                logger.warning("binance_ws.invalid_json")
                continue

            update = parse_kline_message(payload)
            if update is None:
                continue

            # Always call on_update (for live price display)
            result = on_update(update)
            if asyncio.iscoroutine(result):
                await result

            # Call on_close only when the candle is final
            if update["is_closed"]:
                result = on_close(update)
                if asyncio.iscoroutine(result):
                    await result

    # ── Lifecycle ────────────────────────────────────────────────────

    def stop(self) -> None:
        """Signal the run-loop to exit gracefully."""
        self._stop_event.set()

    @property
    def is_running(self) -> bool:
        """``True`` while the stream has not been stopped."""
        return not self._stop_event.is_set()
