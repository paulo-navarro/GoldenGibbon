"""
Tests for the Binance kline WebSocket client (kanban 3.6).

All tests run without a real WebSocket connection.  The stream's
``_listen`` method is tested by feeding it a mock async iterator of
raw JSON payloads.  Reconnection is tested by patching ``websockets.connect``
to raise ``ConnectionClosed`` on the first attempt.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.data.binance_ws import (
    BinanceKlineStream,
    KlineUpdate,
    _STREAM_TO_TF,
    _TF_TO_STREAM,
    parse_kline_message,
)

from contextlib import asynccontextmanager


# ── Sample payloads ─────────────────────────────────────────────────────────


def _make_kline_payload(
    symbol: str = "BTCUSDT",
    interval: str = "15m",
    close: str = "50000.00",
    is_closed: bool = False,
    open_time_ms: int = 1_709_424_000_000,  # 2024-03-03T00:00:00Z
) -> Dict[str, Any]:
    """Build a raw Binance combined-stream kline message."""
    return {
        "stream": f"{symbol.lower()}@kline_{interval}",
        "data": {
            "e": "kline",
            "E": open_time_ms + 1000,
            "s": symbol,
            "k": {
                "t": open_time_ms,
                "T": open_time_ms + 899_999,
                "s": symbol,
                "i": interval,
                "f": 100,
                "L": 200,
                "o": "49900.00",
                "c": close,
                "h": "50100.00",
                "l": "49800.00",
                "v": "123.456",
                "n": 500,
                "x": is_closed,
                "q": "6172800.00",
                "V": "61.728",
                "Q": "3086400.00",
                "B": "0",
            },
        },
    }


# ── parse_kline_message ─────────────────────────────────────────────────────


class TestParseKlineMessage:
    """Unit tests for the raw-payload parser."""

    def test_parses_valid_kline(self):
        raw = _make_kline_payload(close="51234.56", is_closed=False)
        result = parse_kline_message(raw)
        assert result is not None
        assert result["symbol"] == "BTCUSDT"
        assert result["timeframe"] == "15m"
        assert result["close"] == Decimal("51234.56")
        assert result["open"] == Decimal("49900.00")
        assert result["high"] == Decimal("50100.00")
        assert result["low"] == Decimal("49800.00")
        assert result["volume"] == Decimal("123.456")
        assert result["is_closed"] is False

    def test_parses_closed_candle(self):
        raw = _make_kline_payload(is_closed=True)
        result = parse_kline_message(raw)
        assert result is not None
        assert result["is_closed"] is True

    def test_open_time_is_utc_datetime(self):
        raw = _make_kline_payload(open_time_ms=1_709_424_000_000)
        result = parse_kline_message(raw)
        assert result is not None
        assert isinstance(result["open_time"], datetime)
        assert result["open_time"].tzinfo == timezone.utc

    def test_returns_none_for_non_kline_event(self):
        raw = {"data": {"e": "trade", "s": "BTCUSDT"}}
        assert parse_kline_message(raw) is None

    def test_returns_none_for_empty_dict(self):
        assert parse_kline_message({}) is None

    def test_returns_none_for_malformed_data(self):
        assert parse_kline_message({"data": {"e": "kline"}}) is None

    def test_handles_unwrapped_payload(self):
        """Parser also works when message is not wrapped in combined-stream envelope."""
        raw = _make_kline_payload()["data"]
        result = parse_kline_message(raw)
        assert result is not None
        assert result["symbol"] == "BTCUSDT"

    def test_timeframe_mapping(self):
        for tf in ["1m", "5m", "15m", "30m", "1h", "4h", "1d", "1w"]:
            raw = _make_kline_payload(interval=tf)
            result = parse_kline_message(raw)
            assert result is not None
            assert result["timeframe"] == tf


# ── URL builder ──────────────────────────────────────────────────────────────


class TestBuildStreamUrl:
    """Tests for the combined-stream URL builder."""

    def test_single_pair(self):
        stream = BinanceKlineStream()
        url = stream.build_stream_url(["BTCUSDT"], ["15m"])
        assert url == "wss://stream.binance.com:9443/stream?streams=btcusdt@kline_15m"

    def test_multiple_pairs(self):
        stream = BinanceKlineStream()
        url = stream.build_stream_url(["BTCUSDT", "ETHUSDT"], ["15m", "1h"])
        assert "btcusdt@kline_15m" in url
        assert "btcusdt@kline_1h" in url
        assert "ethusdt@kline_15m" in url
        assert "ethusdt@kline_1h" in url
        # Four streams separated by /
        parts = url.split("streams=")[1].split("/")
        assert len(parts) == 4

    def test_custom_base_url(self):
        stream = BinanceKlineStream(base_url="wss://custom:1234/")
        url = stream.build_stream_url(["BTCUSDT"], ["15m"])
        assert url.startswith("wss://custom:1234/stream?streams=")


# ── Callback dispatch ────────────────────────────────────────────────────────


class _MockWebSocket:
    """Async iterator that yields pre-defined raw JSON strings."""

    def __init__(self, messages: List[str]) -> None:
        self._messages = messages
        self._index = 0

    def __aiter__(self):
        return self

    async def __anext__(self) -> str:
        if self._index >= len(self._messages):
            raise StopAsyncIteration
        msg = self._messages[self._index]
        self._index += 1
        return msg


class TestCallbackDispatch:
    """The _listen loop dispatches to on_update and on_close correctly."""

    @pytest.mark.asyncio
    async def test_on_update_called_for_every_message(self):
        updates: List[KlineUpdate] = []

        def on_update(u: KlineUpdate) -> None:
            updates.append(u)

        def on_close(u: KlineUpdate) -> None:
            pass

        messages = [
            json.dumps(_make_kline_payload(close="50000", is_closed=False)),
            json.dumps(_make_kline_payload(close="50100", is_closed=False)),
            json.dumps(_make_kline_payload(close="50200", is_closed=True)),
        ]
        ws = _MockWebSocket(messages)
        stream = BinanceKlineStream()
        await stream._listen(ws, on_update, on_close)

        assert len(updates) == 3

    @pytest.mark.asyncio
    async def test_on_close_called_only_when_closed(self):
        closes: List[KlineUpdate] = []

        def on_update(u: KlineUpdate) -> None:
            pass

        def on_close(u: KlineUpdate) -> None:
            closes.append(u)

        messages = [
            json.dumps(_make_kline_payload(is_closed=False)),
            json.dumps(_make_kline_payload(is_closed=False)),
            json.dumps(_make_kline_payload(is_closed=True)),
        ]
        ws = _MockWebSocket(messages)
        stream = BinanceKlineStream()
        await stream._listen(ws, on_update, on_close)

        assert len(closes) == 1
        assert closes[0]["is_closed"] is True

    @pytest.mark.asyncio
    async def test_async_callbacks_awaited(self):
        updates: List[KlineUpdate] = []

        async def on_update(u: KlineUpdate) -> None:
            updates.append(u)

        async def on_close(u: KlineUpdate) -> None:
            pass

        messages = [
            json.dumps(_make_kline_payload()),
        ]
        ws = _MockWebSocket(messages)
        stream = BinanceKlineStream()
        await stream._listen(ws, on_update, on_close)

        assert len(updates) == 1

    @pytest.mark.asyncio
    async def test_invalid_json_skipped(self):
        updates: List[KlineUpdate] = []

        def on_update(u: KlineUpdate) -> None:
            updates.append(u)

        messages = [
            "not valid json",
            json.dumps(_make_kline_payload()),
        ]
        ws = _MockWebSocket(messages)
        stream = BinanceKlineStream()
        await stream._listen(ws, on_update, on_close=lambda u: None)

        assert len(updates) == 1

    @pytest.mark.asyncio
    async def test_non_kline_events_skipped(self):
        updates: List[KlineUpdate] = []

        def on_update(u: KlineUpdate) -> None:
            updates.append(u)

        messages = [
            json.dumps({"data": {"e": "trade", "s": "BTCUSDT"}}),
            json.dumps(_make_kline_payload()),
        ]
        ws = _MockWebSocket(messages)
        stream = BinanceKlineStream()
        await stream._listen(ws, on_update, on_close=lambda u: None)

        assert len(updates) == 1

    @pytest.mark.asyncio
    async def test_stop_event_halts_listen(self):
        """Setting the stop event should break out of _listen."""
        updates: List[KlineUpdate] = []

        def on_update(u: KlineUpdate) -> None:
            updates.append(u)
            stream.stop()  # stop after first message

        # Provide many messages, but stop should interrupt
        messages = [
            json.dumps(_make_kline_payload(close=str(50000 + i)))
            for i in range(10)
        ]
        ws = _MockWebSocket(messages)
        stream = BinanceKlineStream()
        await stream._listen(ws, on_update, on_close=lambda u: None)

        # Should have processed at most 1 (stop fires after first)
        assert len(updates) == 1


# ── Reconnection ─────────────────────────────────────────────────────────────


class TestReconnection:
    """The run() loop reconnects on failure with back-off."""

    @pytest.mark.asyncio
    async def test_reconnects_after_connection_error(self):
        """After one failure, run() reconnects and processes messages."""
        import websockets.exceptions

        call_count = {"n": 0}
        updates: List[KlineUpdate] = []
        stream = BinanceKlineStream(reconnect_delay=0.01, max_reconnect_delay=0.05)

        @asynccontextmanager
        async def fake_connect(*args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise websockets.exceptions.ConnectionClosed(None, None)
            # Second call: yield a mock WS that delivers one message then stops
            messages = [json.dumps(_make_kline_payload(is_closed=True))]
            ws = _MockWebSocket(messages)
            yield ws
            # After messages are consumed, stop the stream to exit run()
            stream.stop()

        with patch("websockets.connect", side_effect=fake_connect):
            await stream.run(
                symbols=["BTCUSDT"],
                timeframes=["15m"],
                on_update=lambda u: updates.append(u),
                on_close=lambda u: None,
            )

        assert call_count["n"] == 2
        assert len(updates) == 1

    @pytest.mark.asyncio
    async def test_stop_during_reconnect_wait(self):
        """Calling stop() during the backoff wait exits immediately."""
        stream = BinanceKlineStream(reconnect_delay=100)  # very long delay

        async def stop_after_short_delay():
            await asyncio.sleep(0.05)
            stream.stop()

        @asynccontextmanager
        async def fake_connect(*args, **kwargs):
            raise ConnectionError("refused")
            yield  # pragma: no cover

        asyncio.get_event_loop().create_task(stop_after_short_delay())

        with patch("websockets.connect", side_effect=fake_connect):
            await stream.run(
                symbols=["BTCUSDT"],
                timeframes=["15m"],
                on_update=lambda u: None,
                on_close=lambda u: None,
            )
        # If we get here, stop() worked during the wait.
        assert not stream.is_running


# ── Lifecycle ────────────────────────────────────────────────────────────────


class TestLifecycle:
    """stop() and is_running behave correctly."""

    def test_is_running_initially_true(self):
        stream = BinanceKlineStream()
        assert stream.is_running is True

    def test_stop_sets_event(self):
        stream = BinanceKlineStream()
        stream.stop()
        assert stream.is_running is False


# ── Timeframe mappings ───────────────────────────────────────────────────────


class TestTimeframeMappings:
    """Forward and reverse TF mappings are consistent."""

    def test_forward_map_covers_all_timeframes(self):
        expected = {"1m", "5m", "15m", "30m", "1h", "4h", "1d", "1w"}
        assert set(_TF_TO_STREAM.keys()) == expected

    def test_reverse_map_round_trips(self):
        for tf, stream_tf in _TF_TO_STREAM.items():
            assert _STREAM_TO_TF[stream_tf] == tf
