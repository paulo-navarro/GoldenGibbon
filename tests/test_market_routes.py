"""
Tests for ``api.routes.market`` – candle and price endpoints.

Verifies:
  - ``GET /api/market/candles/{symbol}`` returns candles in order
  - Respects limit, timeframe, start/end query params
  - Returns empty list when no data matches
  - Rejects invalid timeframes with 422
  - ``GET /api/market/price/{symbol}`` returns latest close price
  - Returns 404 when no candle data exists for symbol
  - Symbol is case-insensitive (uppercased)
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from api.main import create_app
from db import get_db, get_session
from db.models import CandleRecord


# ── Helpers ──────────────────────────────────────────────────────────────────

BASE_TIME = datetime(2026, 2, 1, 0, 0, 0, tzinfo=timezone.utc)


def _seed_candles(
    db: Session,
    *,
    symbol: str = "BTCUSDT",
    timeframe: str = "15m",
    count: int = 10,
    base_price: Decimal = Decimal("42000"),
    base_time: datetime = BASE_TIME,
) -> list[CandleRecord]:
    """Insert *count* candle records and return them."""
    records = []
    for i in range(count):
        r = CandleRecord(
            symbol=symbol,
            timeframe=timeframe,
            open_time=base_time + timedelta(minutes=15 * i),
            open=base_price + i,
            high=base_price + i + 10,
            low=base_price + i - 5,
            close=base_price + i + 2,
            volume=Decimal("100.5"),
        )
        db.add(r)
        records.append(r)
    db.commit()
    return records


@pytest.fixture()
def client():
    """
    TestClient with real DB session (for route queries) but mocked Redis.
    """
    mock_async_redis = AsyncMock()
    mock_async_redis.ping = AsyncMock(return_value=True)
    mock_async_redis.close = AsyncMock()

    with (
        patch("api.main.init_publisher") as mock_init,
        patch("api.main.reset_publisher"),
        patch("api.main.check_connection", return_value=True),
        patch("redis.asyncio.from_url", return_value=mock_async_redis),
    ):
        mock_pub = MagicMock()
        mock_pub.enabled = True
        mock_init.return_value = mock_pub

        app = create_app()
        with TestClient(app) as tc:
            yield tc


@pytest.fixture()
def seeded_client():
    """
    TestClient with 20 BTCUSDT 15m candles pre-seeded in the database.
    """
    mock_async_redis = AsyncMock()
    mock_async_redis.ping = AsyncMock(return_value=True)
    mock_async_redis.close = AsyncMock()

    with (
        patch("api.main.init_publisher") as mock_init,
        patch("api.main.reset_publisher"),
        patch("api.main.check_connection", return_value=True),
        patch("redis.asyncio.from_url", return_value=mock_async_redis),
    ):
        mock_pub = MagicMock()
        mock_pub.enabled = True
        mock_init.return_value = mock_pub

        app = create_app()

        # Seed data before yielding
        with get_session() as db:
            _seed_candles(db, count=20)

        with TestClient(app) as tc:
            yield tc


# ── GET /api/market/candles/{symbol} ─────────────────────────────────────────


class TestGetCandles:
    """Candle history endpoint."""

    def test_returns_200(self, seeded_client):
        resp = seeded_client.get("/api/market/candles/BTCUSDT")
        assert resp.status_code == 200

    def test_returns_list(self, seeded_client):
        data = seeded_client.get("/api/market/candles/BTCUSDT").json()
        assert isinstance(data, list)

    def test_default_limit_100(self, seeded_client):
        data = seeded_client.get("/api/market/candles/BTCUSDT").json()
        # We seeded 20 candles, default limit is 100
        assert len(data) == 20

    def test_respects_limit(self, seeded_client):
        data = seeded_client.get("/api/market/candles/BTCUSDT?limit=5").json()
        assert len(data) == 5

    def test_chronological_order(self, seeded_client):
        data = seeded_client.get("/api/market/candles/BTCUSDT").json()
        times = [c["open_time"] for c in data]
        assert times == sorted(times)

    def test_limit_returns_latest_candles(self, seeded_client):
        """When no date range, limit returns the *most recent* candles."""
        all_data = seeded_client.get("/api/market/candles/BTCUSDT?limit=100").json()
        limited = seeded_client.get("/api/market/candles/BTCUSDT?limit=5").json()
        # The 5 limited candles should be the last 5 of the full set
        assert limited == all_data[-5:]

    def test_empty_list_for_unknown_symbol(self, client):
        data = client.get("/api/market/candles/XYZUSDT").json()
        assert data == []

    def test_empty_list_for_wrong_timeframe(self, seeded_client):
        data = seeded_client.get("/api/market/candles/BTCUSDT?timeframe=1h").json()
        assert data == []

    def test_symbol_uppercased(self, seeded_client):
        data = seeded_client.get("/api/market/candles/btcusdt").json()
        assert len(data) == 20

    def test_invalid_timeframe_returns_422(self, client):
        resp = client.get("/api/market/candles/BTCUSDT?timeframe=3m")
        assert resp.status_code == 422

    def test_candle_has_required_fields(self, seeded_client):
        data = seeded_client.get("/api/market/candles/BTCUSDT?limit=1").json()
        candle = data[0]
        for field in ("open_time", "open", "high", "low", "close", "volume"):
            assert field in candle

    def test_start_filter(self, seeded_client):
        # First 5 candles: BASE_TIME + 0..4 * 15m
        # Start at candle 10: BASE_TIME + 150m
        start_dt = BASE_TIME + timedelta(minutes=150)
        start = start_dt.strftime("%Y-%m-%dT%H:%M:%S")
        data = seeded_client.get(f"/api/market/candles/BTCUSDT?start={start}").json()
        assert len(data) == 10  # candles 10..19

    def test_end_filter(self, seeded_client):
        # End at candle 4: BASE_TIME + 60m (candles 0,1,2,3,4 = 5 candles)
        end_dt = BASE_TIME + timedelta(minutes=60)
        end = end_dt.strftime("%Y-%m-%dT%H:%M:%S")
        data = seeded_client.get(f"/api/market/candles/BTCUSDT?end={end}").json()
        assert len(data) == 5

    def test_start_and_end_filter(self, seeded_client):
        start = (BASE_TIME + timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M:%S")
        end = (BASE_TIME + timedelta(minutes=90)).strftime("%Y-%m-%dT%H:%M:%S")
        data = seeded_client.get(
            f"/api/market/candles/BTCUSDT?start={start}&end={end}"
        ).json()
        # Candles at 30m, 45m, 60m, 75m, 90m = indices 2,3,4,5,6
        assert len(data) == 5

    def test_limit_too_high_returns_422(self, client):
        resp = client.get("/api/market/candles/BTCUSDT?limit=2000")
        assert resp.status_code == 422

    def test_limit_zero_returns_422(self, client):
        resp = client.get("/api/market/candles/BTCUSDT?limit=0")
        assert resp.status_code == 422


# ── GET /api/market/price/{symbol} ───────────────────────────────────────────


class TestGetPrice:
    """Latest price endpoint."""

    def test_returns_200(self, seeded_client):
        resp = seeded_client.get("/api/market/price/BTCUSDT")
        assert resp.status_code == 200

    def test_returns_correct_fields(self, seeded_client):
        data = seeded_client.get("/api/market/price/BTCUSDT").json()
        assert "symbol" in data
        assert "price" in data
        assert "open_time" in data
        assert "timeframe" in data

    def test_symbol_value(self, seeded_client):
        data = seeded_client.get("/api/market/price/BTCUSDT").json()
        assert data["symbol"] == "BTCUSDT"

    def test_price_is_latest_close(self, seeded_client):
        """Price should be the close of the most recent candle."""
        data = seeded_client.get("/api/market/price/BTCUSDT").json()
        # Last candle (index 19): close = 42000 + 19 + 2 = 42021
        assert data["price"] == "42021.00000000"

    def test_timeframe_in_response(self, seeded_client):
        data = seeded_client.get("/api/market/price/BTCUSDT").json()
        assert data["timeframe"] == "15m"

    def test_404_for_unknown_symbol(self, client):
        resp = client.get("/api/market/price/XYZUSDT")
        assert resp.status_code == 404

    def test_404_for_wrong_timeframe(self, seeded_client):
        resp = seeded_client.get("/api/market/price/BTCUSDT?timeframe=4h")
        assert resp.status_code == 404

    def test_symbol_uppercased(self, seeded_client):
        resp = seeded_client.get("/api/market/price/btcusdt")
        assert resp.status_code == 200
        assert resp.json()["symbol"] == "BTCUSDT"

    def test_invalid_timeframe_returns_422(self, client):
        resp = client.get("/api/market/price/BTCUSDT?timeframe=3m")
        assert resp.status_code == 422
