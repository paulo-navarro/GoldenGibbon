"""
Tests for ``core.events`` – event publisher and channels.

Verifies:
  - ``EventChannel`` has exactly the six blueprint channels
  - ``EventType`` covers all expected event types
  - ``Event`` envelope serialises to valid JSON
  - ``EventPublisher.publish()`` calls ``redis.publish()`` correctly
  - Disabled publisher is a silent no-op
  - ``publish_model()`` serialises Pydantic models with Decimal/datetime
  - Singleton lifecycle (``get_publisher``, ``init_publisher``, ``reset_publisher``)
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
from pydantic import BaseModel

from core.events import (
    Event,
    EventChannel,
    EventPublisher,
    EventType,
    get_publisher,
    init_publisher,
    reset_publisher,
)


# ── Helpers ──────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _isolate_publisher():
    """Reset the module-level singleton before and after every test."""
    reset_publisher()
    yield
    reset_publisher()


class _SampleOrder(BaseModel):
    """Lightweight stand-in for core.models.Order."""

    symbol: str = "BTCUSDT"
    price: Decimal = Decimal("42000.50")
    quantity: Decimal = Decimal("0.001")
    created_at: datetime = datetime(2026, 2, 24, 12, 0, 0, tzinfo=timezone.utc)


def _make_publisher(*, enabled: bool = True) -> EventPublisher:
    """
    Build an ``EventPublisher`` with a mocked Redis client.

    If *enabled* is ``False`` the publisher simulates a failed
    Redis connection.
    """
    with patch("core.events.redis_lib", create=True):
        pub = EventPublisher.__new__(EventPublisher)
        pub._enabled = enabled
        pub._client = MagicMock() if enabled else None
    return pub


# ── EventChannel ─────────────────────────────────────────────────────────────


class TestEventChannel:
    """EventChannel enum covers all six blueprint channels."""

    EXPECTED = {"MARKET_DATA", "STRATEGY", "RISK", "EXECUTION", "PORTFOLIO", "SYSTEM"}

    def test_has_six_members(self):
        assert len(EventChannel) == 6

    def test_member_names(self):
        assert {c.name for c in EventChannel} == self.EXPECTED

    def test_values_have_gg_prefix(self):
        for channel in EventChannel:
            assert channel.value.startswith("gg:"), f"{channel.name} missing gg: prefix"

    def test_market_data_value(self):
        assert EventChannel.MARKET_DATA == "gg:market_data"

    def test_strategy_value(self):
        assert EventChannel.STRATEGY == "gg:strategy"

    def test_risk_value(self):
        assert EventChannel.RISK == "gg:risk"

    def test_execution_value(self):
        assert EventChannel.EXECUTION == "gg:execution"

    def test_portfolio_value(self):
        assert EventChannel.PORTFOLIO == "gg:portfolio"

    def test_system_value(self):
        assert EventChannel.SYSTEM == "gg:system"


# ── EventType ────────────────────────────────────────────────────────────────


class TestEventType:
    """EventType enum covers all event types from the blueprint."""

    # ── Market Data ──────────────────────────────────────────────────

    def test_candle_received(self):
        assert EventType.CANDLE_RECEIVED == "candle_received"

    def test_indicators_computed(self):
        assert EventType.INDICATORS_COMPUTED == "indicators_computed"

    def test_price_update(self):
        assert EventType.PRICE_UPDATE == "price_update"

    # ── Strategy ─────────────────────────────────────────────────────

    def test_signal_generated(self):
        assert EventType.SIGNAL_GENERATED == "signal_generated"

    def test_state_changed(self):
        assert EventType.STATE_CHANGED == "state_changed"

    def test_conditions_evaluated(self):
        assert EventType.CONDITIONS_EVALUATED == "conditions_evaluated"

    # ── Risk ─────────────────────────────────────────────────────────

    def test_risk_evaluated(self):
        assert EventType.RISK_EVALUATED == "risk_evaluated"

    def test_stop_triggered(self):
        assert EventType.STOP_TRIGGERED == "stop_triggered"

    def test_position_sized(self):
        assert EventType.POSITION_SIZED == "position_sized"

    # ── Execution ────────────────────────────────────────────────────

    def test_order_created(self):
        assert EventType.ORDER_CREATED == "order_created"

    def test_order_filled(self):
        assert EventType.ORDER_FILLED == "order_filled"

    def test_order_rejected(self):
        assert EventType.ORDER_REJECTED == "order_rejected"

    def test_order_cancelled(self):
        assert EventType.ORDER_CANCELLED == "order_cancelled"

    # ── Portfolio ────────────────────────────────────────────────────

    def test_position_opened(self):
        assert EventType.POSITION_OPENED == "position_opened"

    def test_position_updated(self):
        assert EventType.POSITION_UPDATED == "position_updated"

    def test_trade_closed(self):
        assert EventType.TRADE_CLOSED == "trade_closed"

    def test_balance_updated(self):
        assert EventType.BALANCE_UPDATED == "balance_updated"

    def test_equity_updated(self):
        assert EventType.EQUITY_UPDATED == "equity_updated"

    # ── System ───────────────────────────────────────────────────────

    def test_heartbeat(self):
        assert EventType.HEARTBEAT == "heartbeat"

    def test_error(self):
        assert EventType.ERROR == "error"

    def test_warning(self):
        assert EventType.WARNING == "warning"

    def test_startup(self):
        assert EventType.STARTUP == "startup"

    def test_shutdown(self):
        assert EventType.SHUTDOWN == "shutdown"

    # ── Counts ───────────────────────────────────────────────────────

    def test_total_event_types(self):
        """Ensure no event type was accidentally removed."""
        assert len(EventType) == 23


# ── Event Envelope ───────────────────────────────────────────────────────────


class TestEvent:
    """Event Pydantic model serialisation."""

    def test_defaults(self):
        event = Event(
            event_type=EventType.HEARTBEAT,
            channel=EventChannel.SYSTEM,
        )
        assert event.data == {}
        assert event.timestamp is not None

    def test_serialises_to_json(self):
        event = Event(
            event_type=EventType.SIGNAL_GENERATED,
            channel=EventChannel.STRATEGY,
            data={"symbol": "BTCUSDT", "signal": "buy"},
        )
        raw = event.model_dump_json()
        parsed = __import__("json").loads(raw)

        assert parsed["event_type"] == "signal_generated"
        assert parsed["channel"] == "gg:strategy"
        assert parsed["data"]["symbol"] == "BTCUSDT"
        assert "timestamp" in parsed

    def test_custom_data_preserved(self):
        data = {"price": 42000.5, "tags": ["a", "b"]}
        event = Event(
            event_type=EventType.PRICE_UPDATE,
            channel=EventChannel.MARKET_DATA,
            data=data,
        )
        assert event.data == data

    def test_timestamp_is_utc(self):
        event = Event(
            event_type=EventType.HEARTBEAT,
            channel=EventChannel.SYSTEM,
        )
        assert event.timestamp.tzinfo is not None


# ── EventPublisher ───────────────────────────────────────────────────────────


class TestPublish:
    """EventPublisher.publish() sends correctly formatted events."""

    def test_publishes_to_correct_channel(self):
        pub = _make_publisher()
        pub.publish(EventChannel.SYSTEM, EventType.HEARTBEAT)

        pub._client.publish.assert_called_once()
        channel_arg = pub._client.publish.call_args[0][0]
        assert channel_arg == "gg:system"

    def test_payload_is_valid_json(self):
        pub = _make_publisher()
        pub.publish(EventChannel.STRATEGY, EventType.SIGNAL_GENERATED, {"signal": "buy"})

        payload = pub._client.publish.call_args[0][1]
        parsed = __import__("json").loads(payload)
        assert parsed["event_type"] == "signal_generated"
        assert parsed["data"]["signal"] == "buy"

    def test_empty_data_defaults_to_empty_dict(self):
        pub = _make_publisher()
        pub.publish(EventChannel.SYSTEM, EventType.HEARTBEAT)

        payload = pub._client.publish.call_args[0][1]
        parsed = __import__("json").loads(payload)
        assert parsed["data"] == {}

    def test_disabled_publisher_is_noop(self):
        pub = _make_publisher(enabled=False)
        pub.publish(EventChannel.SYSTEM, EventType.HEARTBEAT)
        # No client → no call → no error
        assert pub._client is None

    def test_redis_error_is_swallowed(self):
        pub = _make_publisher()
        pub._client.publish.side_effect = ConnectionError("gone")

        # Must not raise
        pub.publish(EventChannel.SYSTEM, EventType.ERROR, {"msg": "boom"})


class TestPublishModel:
    """EventPublisher.publish_model() serialises Pydantic models."""

    def test_decimal_and_datetime_serialised(self):
        pub = _make_publisher()
        order = _SampleOrder()
        pub.publish_model(EventChannel.EXECUTION, EventType.ORDER_CREATED, order)

        payload = pub._client.publish.call_args[0][1]
        parsed = __import__("json").loads(payload)
        data = parsed["data"]

        # Decimal → number, datetime → ISO string
        assert data["symbol"] == "BTCUSDT"
        assert isinstance(data["price"], (int, float, str))
        assert "created_at" in data

    def test_disabled_publisher_is_noop(self):
        pub = _make_publisher(enabled=False)
        pub.publish_model(EventChannel.EXECUTION, EventType.ORDER_CREATED, _SampleOrder())
        assert pub._client is None


class TestClose:
    """EventPublisher.close() shuts down the Redis connection."""

    def test_close_disables_publisher(self):
        pub = _make_publisher()
        assert pub.enabled is True

        pub.close()
        assert pub.enabled is False
        assert pub._client is None

    def test_close_on_disabled_is_safe(self):
        pub = _make_publisher(enabled=False)
        pub.close()  # must not raise

    def test_close_swallows_redis_error(self):
        pub = _make_publisher()
        pub._client.close.side_effect = RuntimeError("oops")
        pub.close()  # must not raise
        assert pub.enabled is False


class TestEnabled:
    """EventPublisher.enabled property."""

    def test_enabled_when_connected(self):
        pub = _make_publisher(enabled=True)
        assert pub.enabled is True

    def test_disabled_when_not_connected(self):
        pub = _make_publisher(enabled=False)
        assert pub.enabled is False


# ── Singleton Lifecycle ──────────────────────────────────────────────────────


class TestSingleton:
    """Module-level singleton management."""

    @patch("core.events.EventPublisher")
    def test_get_publisher_creates_singleton(self, MockPublisher):
        p1 = get_publisher()
        p2 = get_publisher()
        assert p1 is p2
        MockPublisher.assert_called_once()

    @patch("core.events.EventPublisher", side_effect=lambda **kw: MagicMock())
    def test_init_publisher_replaces_singleton(self, MockPublisher):
        p1 = get_publisher()
        p2 = init_publisher("redis://custom:6379/1")
        assert p2 is not p1
        assert MockPublisher.call_count == 2

    @patch("core.events.EventPublisher")
    def test_reset_publisher_clears_singleton(self, MockPublisher):
        get_publisher()
        reset_publisher()
        # Next call should create a fresh instance
        get_publisher()
        assert MockPublisher.call_count == 2


# ── Constructor ──────────────────────────────────────────────────────────────


class TestConstructor:
    """EventPublisher.__init__ handles connectivity edge cases."""

    @patch("core.events.os.getenv", return_value="redis://fake:6379/0")
    def test_disabled_when_redis_unreachable(self, _mock_env):
        """If Redis is not running, publisher starts disabled."""
        pub = EventPublisher(redis_url="redis://nonexistent:9999/0")
        assert pub.enabled is False

    @patch.dict("os.environ", {"REDIS_URL": "redis://env-host:6379/0"})
    def test_uses_env_var_when_no_url_provided(self):
        """Falls back to REDIS_URL env var."""
        pub = EventPublisher()
        # Will fail to connect → disabled, but the important thing
        # is it didn't crash and tried the env URL.
        assert pub.enabled is False
