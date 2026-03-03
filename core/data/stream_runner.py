"""
Binance kline stream runner — entrypoint for the ``ws-feed`` Docker service.

Connects to Binance's WebSocket kline stream for all enabled symbol/timeframe
pairs and publishes two kinds of events via Redis pub/sub:

* **PRICE_UPDATE** on every kline message (~2 s per stream) — consumed by
  the React dashboard for live price display.
* **CANDLE_RECEIVED** when a candle closes (``kline.x == True``) — followed
  by an immediate ``run_strategy_tick.delay()`` so the pipeline reacts
  within seconds instead of waiting for the next Beat tick.

A deduplication guard tracks the last closed candle per ``(symbol,
timeframe)`` so that stream reconnects never re-trigger the same tick.

Usage::

    python -m core.data.stream_runner
"""

from __future__ import annotations

import asyncio
import signal
import sys
from typing import Dict, Set, Tuple

import structlog

logger = structlog.get_logger(__name__)

# Track already-triggered candle closes to prevent duplicates.
# Key: (symbol, timeframe, open_time_iso).  Used as a bounded set.
_closed_candles: Set[Tuple[str, str, str]] = set()
_MAX_CLOSED_CACHE = 500  # trim when exceeded


def _make_close_key(update: dict) -> Tuple[str, str, str]:
    """Build a dedup key from a KlineUpdate dict."""
    return (
        update["symbol"],
        update["timeframe"],
        update["open_time"].isoformat(),
    )


def on_update(update: dict) -> None:
    """Publish a PRICE_UPDATE event for live dashboard prices."""
    from core.events import EventChannel, EventType, get_publisher

    publisher = get_publisher()
    publisher.publish(
        EventChannel.MARKET_DATA,
        EventType.PRICE_UPDATE,
        {
            "symbol": update["symbol"],
            "timeframe": update["timeframe"],
            "price": str(update["close"]),
            "open": str(update["open"]),
            "high": str(update["high"]),
            "low": str(update["low"]),
            "volume": str(update["volume"]),
            "open_time": update["open_time"].isoformat(),
        },
    )


def on_close(update: dict) -> None:
    """
    Publish CANDLE_RECEIVED + trigger ``run_strategy_tick`` on candle close.

    Deduplicates by ``(symbol, timeframe, open_time)`` so reconnects
    don't re-fire the tick for an already-processed candle.
    """
    global _closed_candles

    key = _make_close_key(update)
    if key in _closed_candles:
        logger.debug("stream_runner.duplicate_close_skipped", key=key)
        return

    _closed_candles.add(key)
    if len(_closed_candles) > _MAX_CLOSED_CACHE:
        # Trim oldest half.  set() is unordered but we only need a rough
        # bound — precision doesn't matter.
        excess = len(_closed_candles) - _MAX_CLOSED_CACHE // 2
        for _ in range(excess):
            _closed_candles.pop()

    from core.events import EventChannel, EventType, get_publisher

    publisher = get_publisher()

    # 1. Publish CANDLE_RECEIVED
    publisher.publish(
        EventChannel.MARKET_DATA,
        EventType.CANDLE_RECEIVED,
        {
            "symbol": update["symbol"],
            "timeframe": update["timeframe"],
            "open_time": update["open_time"].isoformat(),
            "close": str(update["close"]),
            "source": "websocket",
        },
    )

    logger.info(
        "stream_runner.candle_closed",
        symbol=update["symbol"],
        timeframe=update["timeframe"],
        close=str(update["close"]),
        open_time=update["open_time"].isoformat(),
    )

    # 2. Trigger strategy tick immediately (non-blocking .delay())
    try:
        from core.tasks import run_strategy_tick

        run_strategy_tick.delay()
        logger.info("stream_runner.tick_triggered", symbol=update["symbol"])
    except Exception as exc:
        logger.error("stream_runner.tick_trigger_failed", error=str(exc))


async def _run() -> None:  # pragma: no cover – integration entrypoint
    """Main async loop: configure logging, build stream, run forever."""
    from core.config import get_settings
    from core.data.binance_ws import BinanceKlineStream
    from core.events import init_publisher
    from core.logging import setup_logging

    settings = get_settings()
    setup_logging(settings.logging)

    init_publisher()

    ws_cfg = settings.ws_feed
    if not ws_cfg.enabled:
        logger.warning("stream_runner.disabled", hint="Set ws_feed.enabled=true in settings.yaml")
        return

    symbols = [s.symbol for s in settings.enabled_symbols]
    # Collect unique timeframes across all enabled symbols
    timeframes = sorted(
        {tf for s in settings.enabled_symbols for tf in s.timeframes}
    )

    if not symbols:
        logger.error("stream_runner.no_symbols")
        return

    stream = BinanceKlineStream(
        base_url=ws_cfg.base_url,
        reconnect_delay=ws_cfg.reconnect_delay,
        max_reconnect_delay=ws_cfg.max_reconnect_delay,
        ping_interval=ws_cfg.ping_interval,
        ping_timeout=ws_cfg.ping_timeout,
    )

    # Wire SIGTERM / SIGINT for graceful shutdown
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stream.stop)

    logger.info(
        "stream_runner.starting",
        symbols=symbols,
        timeframes=timeframes,
        base_url=ws_cfg.base_url,
    )

    await stream.run(
        symbols=symbols,
        timeframes=timeframes,
        on_update=on_update,
        on_close=on_close,
    )

    logger.info("stream_runner.exited")


def main() -> None:  # pragma: no cover
    """Synchronous entrypoint for ``python -m core.data.stream_runner``."""
    asyncio.run(_run())


if __name__ == "__main__":  # pragma: no cover
    main()
