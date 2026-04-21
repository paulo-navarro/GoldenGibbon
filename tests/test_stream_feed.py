"""
Tests for tick idempotency lock + stream runner callbacks (kanban 3.6).

The idempotency lock prevents the same candle from being processed twice
when both Celery Beat and the WebSocket stream trigger ``run_strategy_tick``.

Stream runner callbacks (``on_update``, ``on_close``) are tested in
isolation with mocked ``EventPublisher`` to avoid a Redis dependency.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from core.data.binance_ws import KlineUpdate


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _eager_celery():
    """Run Celery tasks synchronously in-process for every test."""
    from core.celery_app import app

    app.conf.task_always_eager = True
    app.conf.task_eager_propagates = True
    yield
    app.conf.task_always_eager = False
    app.conf.task_eager_propagates = False


@pytest.fixture(autouse=True)
def _reset_worker_state():
    """Clear module-level worker state between tests."""
    import core.tasks as tasks_mod

    tasks_mod._worker_state.clear()
    from core.strategies.registry import reset_registry
    reset_registry()
    yield
    tasks_mod._worker_state.clear()
    reset_registry()


@pytest.fixture(autouse=True)
def _reset_dedup_cache():
    """Clear the stream runner's duplicate-close cache between tests."""
    from core.data import stream_runner

    stream_runner._closed_candles.clear()
    yield
    stream_runner._closed_candles.clear()


# ── Tick idempotency ─────────────────────────────────────────────────────────


class _FakeRedis:
    """Minimal in-memory Redis stub supporting ``set(key, val, nx, ex)``."""

    def __init__(self):
        self._store: dict[str, str] = {}

    def set(self, key: str, value: str, *, nx: bool = False, ex: int | None = None):  # noqa: ARG002
        if nx and key in self._store:
            return False
        self._store[key] = value
        return True


class TestAcquireTickLock:
    """Tests for ``_acquire_tick_lock``."""

    def test_none_candle_time_always_acquires(self):
        from core.tasks import _acquire_tick_lock

        assert _acquire_tick_lock(None) is True

    def test_first_call_acquires(self):
        """First lock attempt for a given candle time succeeds."""
        from core.tasks import _acquire_tick_lock

        fake = _FakeRedis()
        with patch("redis.Redis.from_url", return_value=fake):
            ts = datetime(2099, 1, 1, tzinfo=timezone.utc).isoformat()
            result = _acquire_tick_lock(ts)
            assert result is True

    def test_second_call_blocked(self):
        """Second lock attempt for the same candle time is rejected."""
        from core.tasks import _acquire_tick_lock

        fake = _FakeRedis()
        with patch("redis.Redis.from_url", return_value=fake):
            ts = datetime(2099, 1, 1, 0, 15, tzinfo=timezone.utc).isoformat()
            assert _acquire_tick_lock(ts) is True
            assert _acquire_tick_lock(ts) is False

    def test_different_times_independent(self):
        """Locks with different candle times don't interfere."""
        from core.tasks import _acquire_tick_lock

        fake = _FakeRedis()
        with patch("redis.Redis.from_url", return_value=fake):
            ts_a = datetime(2099, 2, 1, tzinfo=timezone.utc).isoformat()
            ts_b = datetime(2099, 2, 1, 0, 15, tzinfo=timezone.utc).isoformat()
            assert _acquire_tick_lock(ts_a) is True
            assert _acquire_tick_lock(ts_b) is True

    def test_redis_failure_returns_true(self):
        """If Redis is unreachable, fail-open (return True)."""
        from core.tasks import _acquire_tick_lock

        with patch("redis.Redis.from_url", side_effect=Exception("connection refused")):
            result = _acquire_tick_lock("2099-01-01T00:00:00+00:00")
            assert result is True


# ── Stream runner: on_update ─────────────────────────────────────────────────


class TestStreamRunnerOnUpdate:
    """Tests for the stream runner's on_update callback."""

    @patch("core.events.EventPublisher.publish")
    def test_publishes_price_update(self, mock_publish):
        from core.data.stream_runner import on_update
        from core.events import EventChannel, EventType

        update: KlineUpdate = {
            "symbol": "BTCUSDT",
            "timeframe": "15m",
            "open_time": datetime(2025, 3, 1, tzinfo=timezone.utc),
            "open": Decimal("49900"),
            "high": Decimal("50100"),
            "low": Decimal("49800"),
            "close": Decimal("50000"),
            "volume": Decimal("123.456"),
            "is_closed": False,
        }

        on_update(update)

        mock_publish.assert_called_once()
        call_args = mock_publish.call_args
        assert call_args[0][0] == EventChannel.MARKET_DATA
        assert call_args[0][1] == EventType.PRICE_UPDATE
        data = call_args[0][2]
        assert data["symbol"] == "BTCUSDT"
        assert data["price"] == "50000"
        assert data["timeframe"] == "15m"


# ── Stream runner: on_close ──────────────────────────────────────────────────


class TestStreamRunnerOnClose:
    """Tests for the stream runner's on_close callback."""

    @patch("core.tasks.run_strategy_tick.delay")
    @patch("core.events.EventPublisher.publish")
    def test_publishes_candle_received(self, mock_publish, mock_delay):
        from core.data.stream_runner import on_close
        from core.events import EventChannel, EventType

        update: KlineUpdate = {
            "symbol": "BTCUSDT",
            "timeframe": "15m",
            "open_time": datetime(2025, 3, 1, tzinfo=timezone.utc),
            "open": Decimal("49900"),
            "high": Decimal("50100"),
            "low": Decimal("49800"),
            "close": Decimal("50000"),
            "volume": Decimal("123.456"),
            "is_closed": True,
        }

        on_close(update)

        candle_calls = [
            c for c in mock_publish.call_args_list
            if c[0][1] == EventType.CANDLE_RECEIVED
        ]
        assert len(candle_calls) == 1
        data = candle_calls[0][0][2]
        assert data["source"] == "websocket"

    @patch("core.tasks.run_strategy_tick.delay")
    @patch("core.events.EventPublisher.publish")
    def test_triggers_strategy_tick(self, mock_publish, mock_delay):
        from core.data.stream_runner import on_close

        update: KlineUpdate = {
            "symbol": "BTCUSDT",
            "timeframe": "15m",
            "open_time": datetime(2025, 3, 1, 0, 0, tzinfo=timezone.utc),
            "open": Decimal("49900"),
            "high": Decimal("50100"),
            "low": Decimal("49800"),
            "close": Decimal("50000"),
            "volume": Decimal("123.456"),
            "is_closed": True,
        }

        on_close(update)
        mock_delay.assert_called_once()

    @patch("core.tasks.run_strategy_tick.delay")
    @patch("core.events.EventPublisher.publish")
    def test_deduplication_prevents_double_trigger(self, mock_publish, mock_delay):
        from core.data.stream_runner import on_close

        update: KlineUpdate = {
            "symbol": "BTCUSDT",
            "timeframe": "15m",
            "open_time": datetime(2025, 3, 1, 0, 15, tzinfo=timezone.utc),
            "open": Decimal("49900"),
            "high": Decimal("50100"),
            "low": Decimal("49800"),
            "close": Decimal("50000"),
            "volume": Decimal("123.456"),
            "is_closed": True,
        }

        on_close(update)
        on_close(update)  # duplicate

        # Only one tick trigger
        assert mock_delay.call_count == 1

    @patch("core.tasks.run_strategy_tick.delay")
    @patch("core.events.EventPublisher.publish")
    def test_different_candles_both_trigger(self, mock_publish, mock_delay):
        from core.data.stream_runner import on_close

        update_a: KlineUpdate = {
            "symbol": "BTCUSDT",
            "timeframe": "15m",
            "open_time": datetime(2025, 3, 1, 0, 30, tzinfo=timezone.utc),
            "open": Decimal("49900"),
            "high": Decimal("50100"),
            "low": Decimal("49800"),
            "close": Decimal("50000"),
            "volume": Decimal("123.456"),
            "is_closed": True,
        }
        update_b: KlineUpdate = {
            "symbol": "BTCUSDT",
            "timeframe": "15m",
            "open_time": datetime(2025, 3, 1, 0, 45, tzinfo=timezone.utc),
            "open": Decimal("50000"),
            "high": Decimal("50200"),
            "low": Decimal("49900"),
            "close": Decimal("50100"),
            "volume": Decimal("200.0"),
            "is_closed": True,
        }

        on_close(update_a)
        on_close(update_b)

        assert mock_delay.call_count == 2


# ── Config integration ───────────────────────────────────────────────────────


class TestWebSocketFeedConfig:
    """WebSocketFeedConfig model validates and loads correctly."""

    def test_defaults(self):
        from core.config import WebSocketFeedConfig

        cfg = WebSocketFeedConfig()
        assert cfg.enabled is False
        assert cfg.base_url == "wss://stream.binance.com:9443"
        assert cfg.reconnect_delay == 1.0
        assert cfg.max_reconnect_delay == 60.0
        assert cfg.ping_interval == 20
        assert cfg.ping_timeout == 10

    def test_loaded_from_settings(self):
        from core.config import get_settings

        settings = get_settings(reload=True)
        assert hasattr(settings, "ws_feed")
        assert settings.ws_feed.enabled is False
        assert settings.ws_feed.base_url == "wss://stream.binance.com:9443"
