"""
Tests for exchange orders endpoint (task 6.8.6).

Covers:
  - GET /api/exchange/orders — GG and orphan orders with source field
  - Filters: source, symbol, status
  - Only live orders returned
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from api.main import create_app
from db import get_session
from db.models import OrderRecord


@pytest.fixture(autouse=True)
def _clean_orders():
    with get_session() as session:
        session.query(OrderRecord).filter(
            OrderRecord.trading_mode == "live",
        ).delete(synchronize_session="fetch")


@pytest.fixture()
def client():
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


def _seed_gg_order(*, symbol="BTCUSDT", side="buy", status="filled", intent="open_long"):
    with get_session() as session:
        session.add(OrderRecord(
            symbol=symbol,
            side=side,
            order_type="market",
            amount=Decimal("1.0"),
            price=Decimal("50000"),
            status=status,
            filled_amount=Decimal("1.0"),
            avg_fill_price=Decimal("50000"),
            exchange_order_id="12345",
            exchange_status="FILLED",
            trading_mode="live",
            intent=intent,
            reconciliation_status="synced",
        ))


def _seed_orphan_order(*, symbol="ETHUSDT", side="sell", status="pending"):
    with get_session() as session:
        session.add(OrderRecord(
            symbol=symbol,
            side=side,
            order_type="limit",
            amount=Decimal("10.0"),
            price=Decimal("3500"),
            status=status,
            filled_amount=Decimal("0"),
            exchange_order_id="99999",
            exchange_status="NEW",
            trading_mode="live",
            reconciliation_status="orphan",
        ))


def _seed_paper_order():
    with get_session() as session:
        session.add(OrderRecord(
            symbol="BTCUSDT",
            side="buy",
            order_type="market",
            amount=Decimal("0.5"),
            status="filled",
            filled_amount=Decimal("0.5"),
            trading_mode="paper",
            reconciliation_status="pending_sync",
        ))


class TestExchangeOrders:
    def test_empty(self, client):
        resp = client.get("/api/exchange/orders")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_gg_order_has_source_gg(self, client):
        _seed_gg_order()
        resp = client.get("/api/exchange/orders")
        data = resp.json()
        assert len(data) == 1
        assert data[0]["source"] == "gg"
        assert data[0]["intent"] == "open_long"
        assert data[0]["exchange_order_id"] == "12345"

    def test_orphan_order_has_source_orphan(self, client):
        _seed_orphan_order()
        resp = client.get("/api/exchange/orders")
        data = resp.json()
        assert len(data) == 1
        assert data[0]["source"] == "orphan"
        assert data[0]["intent"] is None
        assert data[0]["symbol"] == "ETHUSDT"

    def test_mixed_orders(self, client):
        _seed_gg_order()
        _seed_orphan_order()
        resp = client.get("/api/exchange/orders")
        data = resp.json()
        assert len(data) == 2
        sources = {d["source"] for d in data}
        assert sources == {"gg", "orphan"}

    def test_filter_source_gg(self, client):
        _seed_gg_order()
        _seed_orphan_order()
        resp = client.get("/api/exchange/orders?source=gg")
        data = resp.json()
        assert all(d["source"] == "gg" for d in data)
        assert len(data) == 1

    def test_filter_source_orphan(self, client):
        _seed_gg_order()
        _seed_orphan_order()
        resp = client.get("/api/exchange/orders?source=orphan")
        data = resp.json()
        assert all(d["source"] == "orphan" for d in data)
        assert len(data) == 1

    def test_filter_symbol(self, client):
        _seed_gg_order(symbol="BTCUSDT")
        _seed_gg_order(symbol="ETHUSDT", intent="close_long")
        resp = client.get("/api/exchange/orders?symbol=BTCUSDT")
        data = resp.json()
        assert all(d["symbol"] == "BTCUSDT" for d in data)

    def test_filter_status_open(self, client):
        _seed_gg_order(status="filled")
        _seed_orphan_order(status="pending")
        resp = client.get("/api/exchange/orders?status=open")
        data = resp.json()
        assert len(data) == 1
        assert data[0]["source"] == "orphan"

    def test_filter_status_filled(self, client):
        _seed_gg_order(status="filled")
        _seed_orphan_order(status="pending")
        resp = client.get("/api/exchange/orders?status=filled")
        data = resp.json()
        assert len(data) == 1
        assert data[0]["source"] == "gg"

    def test_paper_orders_excluded(self, client):
        _seed_paper_order()
        _seed_gg_order()
        resp = client.get("/api/exchange/orders")
        data = resp.json()
        assert len(data) == 1
        assert data[0]["source"] == "gg"

    def test_response_fields(self, client):
        _seed_gg_order()
        data = client.get("/api/exchange/orders").json()[0]
        assert "exchange_order_id" in data
        assert "symbol" in data
        assert "side" in data
        assert "type" in data
        assert "status" in data
        assert "orig_qty" in data
        assert "executed_qty" in data
        assert "price" in data
        assert "time" in data
        assert "source" in data
        assert "intent" in data
