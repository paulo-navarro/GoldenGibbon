"""
Tests for ``api.routes.orders`` – order list and single-order endpoints.

Verifies:
  - ``GET /api/orders/`` returns filtered orders chronologically
  - Respects run_id, symbol, side, status, limit, start/end
  - Defaults to latest run_id when not provided
  - ``GET /api/orders/{order_id}`` returns a single order or 404
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from api.main import create_app
from db import get_db, get_session
from db.models import BacktestResult, OrderRecord


# ── Helpers ──────────────────────────────────────────────────────────────────

BASE_TIME = datetime(2026, 2, 1, 0, 0, 0, tzinfo=timezone.utc)

SIDES = ["buy", "sell"]
STATUSES = ["pending", "filled", "partial", "rejected", "cancelled"]
ORDER_TYPES = ["market", "limit"]


def _seed_backtest_result(db: Session, run_id: str) -> BacktestResult:
    """Insert a minimal BacktestResult so FK on order_records is satisfied."""
    br = BacktestResult(
        run_id=run_id,
        strategy="smart_hodler",
        symbol="BTCUSDT",
        start_date=BASE_TIME,
        end_date=BASE_TIME + timedelta(days=30),
        initial_capital=Decimal("10000"),
        final_capital=Decimal("11000"),
        total_return=Decimal("10.0000"),
        total_trades=10,
        winning_trades=6,
        losing_trades=4,
        win_rate=Decimal("60.0000"),
        avg_win=Decimal("500"),
        avg_loss=Decimal("250"),
        max_drawdown=Decimal("5.0000"),
    )
    db.add(br)
    db.commit()
    return br


def _seed_orders(
    db: Session,
    *,
    run_id: str = "run-abc",
    count: int = 10,
    base_time: datetime = BASE_TIME,
    symbol: str = "BTCUSDT",
    side: str | None = None,
    status: str | None = None,
    trading_mode: str = "paper",
) -> list[OrderRecord]:
    """
    Insert *count* order records with rotating sides/statuses.

    If *side* or *status* is given, all orders use that value;
    otherwise values cycle through the module-level lists.
    """
    records: list[OrderRecord] = []
    for i in range(count):
        s = side if side is not None else SIDES[i % len(SIDES)]
        st = status if status is not None else STATUSES[i % len(STATUSES)]
        ot = ORDER_TYPES[i % len(ORDER_TYPES)]

        r = OrderRecord(
            run_id=run_id,
            symbol=symbol,
            side=s,
            order_type=ot,
            amount=Decimal("0.1") + Decimal("0.01") * i,
            price=Decimal("42000") + i * 100 if ot == "limit" else None,
            status=st,
            filled_amount=Decimal("0.1") + Decimal("0.01") * i if st == "filled" else Decimal("0"),
            avg_fill_price=Decimal("42000") + i * 100 if st == "filled" else None,
            slippage_percent=Decimal("0.05") if st == "filled" else None,
            fee_usdt=Decimal("4.20") if st == "filled" else None,
            exchange_order_id=f"binance-{i}" if st == "filled" else None,
            created_at=base_time + timedelta(hours=i),
            filled_at=base_time + timedelta(hours=i, minutes=30) if st == "filled" else None,
            trading_mode=trading_mode,
        )
        db.add(r)
        records.append(r)
    db.commit()
    return records


# ── Fixtures ─────────────────────────────────────────────────────────────────


def _make_client():
    """Create patch contexts for mocked Redis."""
    mock_async_redis = AsyncMock()
    mock_async_redis.ping = AsyncMock(return_value=True)
    mock_async_redis.close = AsyncMock()

    return (
        patch("api.main.init_publisher"),
        patch("api.main.reset_publisher"),
        patch("api.main.check_connection", return_value=True),
        patch("redis.asyncio.from_url", return_value=mock_async_redis),
    )


@pytest.fixture()
def client():
    """TestClient with real DB but no seed data."""
    p1, p2, p3, p4 = _make_client()
    with p1 as mock_init, p2, p3, p4:
        mock_pub = MagicMock()
        mock_pub.enabled = True
        mock_init.return_value = mock_pub

        app = create_app()
        with TestClient(app) as tc:
            yield tc


@pytest.fixture()
def seeded_client():
    """TestClient with 15 orders pre-seeded in a single run."""
    p1, p2, p3, p4 = _make_client()
    with p1 as mock_init, p2, p3, p4:
        mock_pub = MagicMock()
        mock_pub.enabled = True
        mock_init.return_value = mock_pub

        app = create_app()

        with get_session() as db:
            _seed_backtest_result(db, "run-abc")
            _seed_orders(db, run_id="run-abc", count=15)

        with TestClient(app) as tc:
            yield tc


@pytest.fixture()
def multi_run_client():
    """TestClient with two different run_ids seeded."""
    p1, p2, p3, p4 = _make_client()
    with p1 as mock_init, p2, p3, p4:
        mock_pub = MagicMock()
        mock_pub.enabled = True
        mock_init.return_value = mock_pub

        app = create_app()

        with get_session() as db:
            _seed_backtest_result(db, "run-old")
            _seed_orders(
                db, run_id="run-old", count=5,
                base_time=BASE_TIME - timedelta(days=7),
                trading_mode="live",
            )
            _seed_backtest_result(db, "run-new")
            _seed_orders(
                db, run_id="run-new", count=10,
                base_time=BASE_TIME,
            )

        with TestClient(app) as tc:
            yield tc


@pytest.fixture()
def mixed_client():
    """TestClient with orders across multiple symbols, sides, and statuses."""
    p1, p2, p3, p4 = _make_client()
    with p1 as mock_init, p2, p3, p4:
        mock_pub = MagicMock()
        mock_pub.enabled = True
        mock_init.return_value = mock_pub

        app = create_app()

        with get_session() as db:
            _seed_backtest_result(db, "run-mix")
            _seed_orders(db, run_id="run-mix", count=5, symbol="BTCUSDT", side="buy", status="pending")
            _seed_orders(
                db, run_id="run-mix", count=5, symbol="ETHUSDT", side="sell", status="cancelled",
                base_time=BASE_TIME + timedelta(days=1),
            )
            _seed_orders(
                db, run_id="run-mix", count=5, symbol="SOLUSDT", side="sell", status="filled",
                base_time=BASE_TIME + timedelta(days=2),
            )

        with TestClient(app) as tc:
            yield tc


# ── GET /api/orders/ ─────────────────────────────────────────────────────────


class TestGetOrders:
    """Order list endpoint."""

    def test_returns_200(self, seeded_client):
        resp = seeded_client.get("/api/orders/")
        assert resp.status_code == 200

    def test_returns_list(self, seeded_client):
        data = seeded_client.get("/api/orders/").json()
        assert isinstance(data, list)

    def test_returns_all_orders(self, seeded_client):
        data = seeded_client.get("/api/orders/").json()
        assert len(data) == 15

    def test_chronological_order(self, seeded_client):
        data = seeded_client.get("/api/orders/").json()
        created_times = [o["created_at"] for o in data]
        assert created_times == sorted(created_times)

    def test_order_shape(self, seeded_client):
        data = seeded_client.get("/api/orders/").json()
        order = data[0]
        expected_keys = {
            "run_id", "symbol", "side", "order_type", "amount", "price",
            "status", "filled_amount", "avg_fill_price", "slippage_percent",
            "fee_usdt", "exchange_order_id", "time_in_force", "limit_price",
            "reject_reason", "exchange_status", "created_at", "updated_at",
            "filled_at",
        }
        assert expected_keys == set(order.keys())

    def test_limit_param(self, seeded_client):
        data = seeded_client.get("/api/orders/", params={"limit": 5}).json()
        assert len(data) == 5

    def test_limit_returns_latest(self, seeded_client):
        """When limit < total, returns the most recent orders."""
        data = seeded_client.get("/api/orders/", params={"limit": 3}).json()
        assert len(data) == 3
        # Should be chronological (latest 3)
        created_times = [o["created_at"] for o in data]
        assert created_times == sorted(created_times)

    def test_limit_bounds_min(self, seeded_client):
        resp = seeded_client.get("/api/orders/", params={"limit": 0})
        assert resp.status_code == 422

    def test_limit_bounds_max(self, seeded_client):
        resp = seeded_client.get("/api/orders/", params={"limit": 10001})
        assert resp.status_code == 422

    def test_defaults_to_paper_mode(self, multi_run_client):
        """Without params, returns only paper trading_mode orders."""
        data = multi_run_client.get("/api/orders/").json()
        # run-new (paper) has 10 orders; run-old (live) is excluded
        assert len(data) == 10

    def test_explicit_run_id(self, multi_run_client):
        data = multi_run_client.get(
            "/api/orders/", params={"run_id": "run-old"}
        ).json()
        assert len(data) == 5

    def test_unknown_run_id_returns_empty(self, seeded_client):
        data = seeded_client.get(
            "/api/orders/", params={"run_id": "nonexistent"}
        ).json()
        assert data == []

    def test_empty_when_no_orders(self, client):
        data = client.get("/api/orders/").json()
        assert data == []

    def test_symbol_filter(self, mixed_client):
        data = mixed_client.get(
            "/api/orders/", params={"symbol": "ETHUSDT"}
        ).json()
        assert len(data) == 5
        assert all(o["symbol"] == "ETHUSDT" for o in data)

    def test_symbol_filter_case_insensitive(self, mixed_client):
        data = mixed_client.get(
            "/api/orders/", params={"symbol": "ethusdt"}
        ).json()
        assert len(data) == 5

    def test_side_filter(self, mixed_client):
        data = mixed_client.get(
            "/api/orders/", params={"side": "buy"}
        ).json()
        assert len(data) == 5
        assert all(o["side"] == "buy" for o in data)

    def test_status_filter(self, mixed_client):
        data = mixed_client.get(
            "/api/orders/", params={"status": "filled"}
        ).json()
        assert len(data) == 5
        assert all(o["status"] == "filled" for o in data)

    def test_start_filter(self, seeded_client):
        start = (BASE_TIME + timedelta(hours=10)).strftime("%Y-%m-%dT%H:%M:%S")
        data = seeded_client.get(
            "/api/orders/", params={"start": start}
        ).json()
        for o in data:
            assert o["created_at"] >= start

    def test_end_filter(self, seeded_client):
        end = (BASE_TIME + timedelta(hours=5)).strftime("%Y-%m-%dT%H:%M:%S")
        data = seeded_client.get(
            "/api/orders/", params={"end": end}
        ).json()
        for o in data:
            assert o["created_at"] <= end + "Z"  # may have trailing Z

    def test_start_and_end_filter(self, seeded_client):
        start = (BASE_TIME + timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M:%S")
        end = (BASE_TIME + timedelta(hours=10)).strftime("%Y-%m-%dT%H:%M:%S")
        data = seeded_client.get(
            "/api/orders/", params={"start": start, "end": end},
        ).json()
        assert len(data) > 0
        assert len(data) < 15


# ── GET /api/orders/{order_id} ───────────────────────────────────────────────


class TestGetOrderById:
    """Single order endpoint."""

    def test_returns_200(self, seeded_client):
        # Grab the first order from the list to get a valid ID
        orders = seeded_client.get("/api/orders/").json()
        # Need to fetch by DB id – get it via the list endpoint isn't possible
        # since Order model has no `id` field.  Instead, insert and query.
        with get_session() as db:
            from sqlalchemy import select
            record = db.execute(
                select(OrderRecord).order_by(OrderRecord.created_at).limit(1)
            ).scalars().first()
            order_id = record.id

        resp = seeded_client.get(f"/api/orders/{order_id}")
        assert resp.status_code == 200

    def test_response_shape(self, seeded_client):
        with get_session() as db:
            from sqlalchemy import select
            record = db.execute(
                select(OrderRecord).order_by(OrderRecord.created_at).limit(1)
            ).scalars().first()
            order_id = record.id

        data = seeded_client.get(f"/api/orders/{order_id}").json()
        expected_keys = {
            "run_id", "symbol", "side", "order_type", "amount", "price",
            "status", "filled_amount", "avg_fill_price", "slippage_percent",
            "fee_usdt", "exchange_order_id", "time_in_force", "limit_price",
            "reject_reason", "exchange_status", "created_at", "updated_at",
            "filled_at",
        }
        assert expected_keys == set(data.keys())

    def test_returns_correct_order(self, seeded_client):
        with get_session() as db:
            from sqlalchemy import select
            record = db.execute(
                select(OrderRecord).order_by(OrderRecord.created_at).limit(1)
            ).scalars().first()
            order_id = record.id
            expected_symbol = record.symbol
            expected_side = record.side

        data = seeded_client.get(f"/api/orders/{order_id}").json()
        assert data["symbol"] == expected_symbol
        assert data["side"] == expected_side

    def test_404_for_unknown_id(self, seeded_client):
        resp = seeded_client.get("/api/orders/999999")
        assert resp.status_code == 404
        assert "999999" in resp.json()["detail"]
