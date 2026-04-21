"""
Tests for the fetch_candles Celery task (kanban 3.4).

All tests run in eager mode (no broker/worker needed).
BinanceClient is mocked so no real network calls are made.
EventPublisher is mocked so no Redis is required for event assertions.
A real Postgres connection is used for DB assertions (via the
``clean_database`` autouse fixture in conftest.py).
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import List
from unittest.mock import MagicMock, call, patch

import pytest

from core.config import SymbolConfig
from core.models import Candle
from db import get_session
from db.models import CandleRecord

# Fixed test symbols – isolates tests from runtime config
_TEST_SYMBOLS = [
    SymbolConfig(symbol="BTCUSDT", timeframes=["15m", "1h"]),
    SymbolConfig(symbol="ETHUSDT", timeframes=["15m", "1h"]),
]


# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_candle(index: int, base_price: Decimal = Decimal("50000")) -> Candle:
    """Create a deterministic synthetic Candle."""
    ts = datetime(2025, 3, 3, index, 0, tzinfo=timezone.utc)  # vary by hour
    price = base_price + Decimal(str(index * 100))
    return Candle(
        open_time=ts,
        open=price,
        high=price + Decimal("100"),
        low=price - Decimal("100"),
        close=price + Decimal("50"),
        volume=Decimal("10.5"),
        quote_volume=Decimal("525000"),
        trades_count=100 + index,
    )


SAMPLE_CANDLES: List[Candle] = [_make_candle(i) for i in range(5)]


def _count_candle_records(symbol: str, timeframe: str) -> int:
    """Query the candle_records table and return the row count."""
    with get_session() as session:
        result = session.query(CandleRecord).filter_by(
            symbol=symbol, timeframe=timeframe
        )
        return result.count()


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
def _pin_symbols():
    """Pin enabled_symbols to 2 test symbols so tests don't depend on config."""
    from core.config import get_settings

    settings = get_settings()
    original = settings.symbols
    settings.symbols = list(_TEST_SYMBOLS)
    yield
    settings.symbols = original


# ── Happy path ───────────────────────────────────────────────────────────────


class TestFetchCandlesHappyPath:
    """fetch_candles succeeds for all enabled symbol/timeframe pairs."""

    @patch("core.events.EventPublisher.publish")
    @patch("core.data.binance_client.BinanceClient.fetch_klines")
    def test_task_returns_correct_summary(self, mock_klines, mock_publish):
        """Return dict counts pairs_processed, pairs_failed, total_new_candles."""
        mock_klines.return_value = SAMPLE_CANDLES

        from core.tasks import fetch_candles

        result = fetch_candles.apply()
        assert result.successful()

        summary = result.result
        # 2 test symbols × 2 timeframes = 4 pairs
        assert summary["pairs_processed"] == 4
        assert summary["pairs_failed"] == 0
        assert summary["total_new_candles"] == 4 * len(SAMPLE_CANDLES)

    @patch("core.events.EventPublisher.publish")
    @patch("core.data.binance_client.BinanceClient.fetch_klines")
    def test_candles_inserted_to_database(self, mock_klines, mock_publish):
        """Candles are upserted into CandleRecord for each pair."""
        mock_klines.return_value = SAMPLE_CANDLES

        from core.tasks import fetch_candles

        fetch_candles.apply()

        # Both test symbols/timeframes should have candles in the DB
        assert _count_candle_records("BTCUSDT", "15m") == len(SAMPLE_CANDLES)
        assert _count_candle_records("BTCUSDT", "1h") == len(SAMPLE_CANDLES)
        assert _count_candle_records("ETHUSDT", "15m") == len(SAMPLE_CANDLES)
        assert _count_candle_records("ETHUSDT", "1h") == len(SAMPLE_CANDLES)

    @patch("core.events.EventPublisher.publish")
    @patch("core.data.binance_client.BinanceClient.fetch_klines")
    def test_candle_received_events_published(self, mock_klines, mock_publish):
        """A CANDLE_RECEIVED event is published for each (symbol, timeframe)."""
        from core.events import EventChannel, EventType

        mock_klines.return_value = SAMPLE_CANDLES

        from core.tasks import fetch_candles

        fetch_candles.apply()

        # Collect all published events
        published_types = [
            call_args[0][1]  # second positional arg is EventType
            for call_args in mock_publish.call_args_list
        ]
        candle_received_calls = [
            t for t in published_types if t == EventType.CANDLE_RECEIVED
        ]
        assert len(candle_received_calls) == 4  # one per pair

    @patch("core.events.EventPublisher.publish")
    @patch("core.data.binance_client.BinanceClient.fetch_klines")
    def test_candle_received_event_data_shape(self, mock_klines, mock_publish):
        """CANDLE_RECEIVED event data contains expected keys."""
        from core.events import EventChannel, EventType

        mock_klines.return_value = SAMPLE_CANDLES

        from core.tasks import fetch_candles

        fetch_candles.apply()

        candle_calls = [
            c for c in mock_publish.call_args_list
            if c[0][1] == EventType.CANDLE_RECEIVED
        ]
        assert len(candle_calls) == 4
        for c in candle_calls:
            data = c[0][2]
            assert "symbol" in data
            assert "timeframe" in data
            assert data["candles_fetched"] == len(SAMPLE_CANDLES)
            assert data["new_candles"] == len(SAMPLE_CANDLES)

    @patch("core.events.EventPublisher.publish")
    @patch("core.data.binance_client.BinanceClient.fetch_klines")
    def test_fetch_klines_called_with_limit_5(self, mock_klines, mock_publish):
        """fetch_klines is called with limit=5 (the _FETCH_LIMIT constant)."""
        mock_klines.return_value = SAMPLE_CANDLES

        from core.tasks import fetch_candles

        fetch_candles.apply()

        for c in mock_klines.call_args_list:
            assert c.kwargs.get("limit") == 5 or c[1].get("limit") == 5 or c[0][2] == 5


# ── Idempotency ───────────────────────────────────────────────────────────────


class TestFetchCandlesIdempotency:
    """Running the task twice with identical candles must not duplicate records."""

    @patch("core.events.EventPublisher.publish")
    @patch("core.data.binance_client.BinanceClient.fetch_klines")
    def test_duplicate_candles_not_inserted(self, mock_klines, mock_publish):
        """Second run with same candles produces new_candles=0 (ON CONFLICT DO NOTHING)."""
        mock_klines.return_value = SAMPLE_CANDLES

        from core.tasks import fetch_candles

        result1 = fetch_candles.apply()
        result2 = fetch_candles.apply()

        assert result1.result["total_new_candles"] == 4 * len(SAMPLE_CANDLES)
        assert result2.result["total_new_candles"] == 0

    @patch("core.events.EventPublisher.publish")
    @patch("core.data.binance_client.BinanceClient.fetch_klines")
    def test_row_count_stable_after_second_run(self, mock_klines, mock_publish):
        """DB row count doesn't increase on second identical run."""
        mock_klines.return_value = SAMPLE_CANDLES

        from core.tasks import fetch_candles

        fetch_candles.apply()
        count_after_first = _count_candle_records("BTCUSDT", "15m")

        fetch_candles.apply()
        count_after_second = _count_candle_records("BTCUSDT", "15m")

        assert count_after_first == count_after_second == len(SAMPLE_CANDLES)


# ── Partial failure ───────────────────────────────────────────────────────────


class TestFetchCandlesPartialFailure:
    """One failing pair must not block others; task must always complete."""

    @patch("core.events.EventPublisher.publish")
    @patch("core.data.binance_client.BinanceClient.fetch_klines")
    def test_task_completes_when_one_pair_fails(self, mock_klines, mock_publish):
        """Task returns a summary dict even when a pair errors."""
        import requests

        def _klines_side_effect(symbol, timeframe, **kwargs):
            if symbol == "ETHUSDT":
                raise requests.exceptions.ConnectionError("network error")
            return SAMPLE_CANDLES

        mock_klines.side_effect = _klines_side_effect

        from core.tasks import fetch_candles

        result = fetch_candles.apply()
        assert result.successful()

        summary = result.result
        # ETHUSDT has 2 timeframes → 2 failures, BTCUSDT has 2 → 2 successes
        assert summary["pairs_failed"] == 2
        assert summary["pairs_processed"] == 2

    @patch("core.events.EventPublisher.publish")
    @patch("core.data.binance_client.BinanceClient.fetch_klines")
    def test_successful_pairs_still_cached(self, mock_klines, mock_publish):
        """Candles from successful pairs are written to DB despite another failing."""
        import requests

        def _klines_side_effect(symbol, timeframe, **kwargs):
            if symbol == "ETHUSDT":
                raise requests.exceptions.ConnectionError("network error")
            return SAMPLE_CANDLES

        mock_klines.side_effect = _klines_side_effect

        from core.tasks import fetch_candles

        fetch_candles.apply()

        assert _count_candle_records("BTCUSDT", "15m") == len(SAMPLE_CANDLES)
        assert _count_candle_records("BTCUSDT", "1h") == len(SAMPLE_CANDLES)
        # ETHUSDT rows must be absent
        assert _count_candle_records("ETHUSDT", "15m") == 0
        assert _count_candle_records("ETHUSDT", "1h") == 0

    @patch("core.events.EventPublisher.publish")
    @patch("core.data.binance_client.BinanceClient.fetch_klines")
    def test_error_event_published_for_failed_pair(self, mock_klines, mock_publish):
        """An ERROR event is published on the SYSTEM channel for each failing pair."""
        import requests
        from core.events import EventChannel, EventType

        def _klines_side_effect(symbol, timeframe, **kwargs):
            if symbol == "ETHUSDT":
                raise requests.exceptions.ConnectionError("simulated error")
            return SAMPLE_CANDLES

        mock_klines.side_effect = _klines_side_effect

        from core.tasks import fetch_candles

        fetch_candles.apply()

        error_calls = [
            c for c in mock_publish.call_args_list
            if c[0][1] == EventType.ERROR and c[0][0] == EventChannel.SYSTEM
        ]
        assert len(error_calls) == 2  # one per ETHUSDT timeframe


# ── Edge cases ────────────────────────────────────────────────────────────────


class TestFetchCandlesEdgeCases:
    """Edge cases: empty responses, no enabled symbols."""

    @patch("core.events.EventPublisher.publish")
    @patch("core.data.binance_client.BinanceClient.fetch_klines")
    def test_empty_klines_response(self, mock_klines, mock_publish):
        """Task handles empty list from Binance gracefully."""
        mock_klines.return_value = []

        from core.tasks import fetch_candles

        result = fetch_candles.apply()
        assert result.successful()

        summary = result.result
        assert summary["pairs_processed"] == 4
        assert summary["total_new_candles"] == 0
        assert _count_candle_records("BTCUSDT", "15m") == 0

    @patch("core.events.EventPublisher.publish")
    @patch("core.tasks.fetch_candles.__wrapped__", create=True)  # harmless if absent
    def test_no_enabled_symbols(self, *_mocks):
        """Task returns zero counts when no symbols are enabled."""
        from unittest.mock import MagicMock

        fake_settings = MagicMock()
        fake_settings.enabled_symbols = []

        with patch("core.config.get_settings", return_value=fake_settings):
            from core.tasks import fetch_candles

            result = fetch_candles.apply()
            assert result.successful()

            summary = result.result
            assert summary["pairs_processed"] == 0
            assert summary["pairs_failed"] == 0
            assert summary["total_new_candles"] == 0
