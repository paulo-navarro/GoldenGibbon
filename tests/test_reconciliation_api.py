"""
Tests for reconciliation API endpoints (tasks 6.8.4/6.8.5).

Covers:
  - GET /api/reconciliation/status — latest run, pending/orphan counts
  - GET /api/reconciliation/history — paginated, filtered history
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from api.main import create_app
from db import get_session
from db.models import OrderRecord, ReconciliationRunRecord


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _clean_reconciliation_runs():
    """Remove all reconciliation_runs rows before each test."""
    with get_session() as session:
        session.query(ReconciliationRunRecord).delete()
        session.query(OrderRecord).filter(
            OrderRecord.reconciliation_status.in_(["orphan", "pending_sync"]),
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


def _seed_run(
    *,
    status: str = "ok",
    mismatches: int = 0,
    repairs: int = 0,
    events: list | None = None,
    started_at: datetime | None = None,
    finished_at: datetime | None = None,
) -> None:
    now = datetime.now(timezone.utc)
    with get_session() as session:
        session.add(ReconciliationRunRecord(
            started_at=started_at or now - timedelta(seconds=5),
            finished_at=finished_at or now,
            status=status,
            pairs_checked=6,
            mismatches=mismatches,
            repairs=repairs,
            summary={"pairs_checked": 6, "mismatches": mismatches},
            events=events or [],
        ))


def _seed_order(*, status: str = "pending", reconciliation_status: str = "pending_sync") -> None:
    with get_session() as session:
        session.add(OrderRecord(
            symbol="BTCUSDT",
            side="buy",
            order_type="market",
            amount=1.0,
            status=status,
            trading_mode="live",
            reconciliation_status=reconciliation_status,
        ))


# ── GET /api/reconciliation/status ────────────────────────────────────────────


class TestReconciliationStatus:
    def test_no_runs_returns_unknown(self, client):
        resp = client.get("/api/reconciliation/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "unknown"
        assert data["last_run_at"] is None
        assert data["pairs_checked"] == 0

    def test_returns_latest_run(self, client):
        _seed_run(status="ok", mismatches=0)
        resp = client.get("/api/reconciliation/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["last_run_at"] is not None
        assert data["pairs_checked"] == 6

    def test_returns_latest_not_oldest(self, client):
        old = datetime.now(timezone.utc) - timedelta(hours=1)
        _seed_run(status="mismatch", mismatches=2, started_at=old - timedelta(seconds=5), finished_at=old)
        _seed_run(status="ok", mismatches=0)
        resp = client.get("/api/reconciliation/status")
        data = resp.json()
        assert data["status"] == "ok"

    def test_includes_events(self, client):
        _seed_run(
            status="mismatch",
            mismatches=1,
            repairs=1,
            events=[
                {"type": "position_force_closed", "severity": "critical", "symbol": "BTCUSDT", "detail": "closed"},
            ],
        )
        resp = client.get("/api/reconciliation/status")
        data = resp.json()
        assert len(data["events"]) == 1
        assert data["events"][0]["severity"] == "critical"

    def test_includes_pending_and_orphan_counts(self, client):
        _seed_order(status="pending", reconciliation_status="pending_sync")
        _seed_order(status="pending", reconciliation_status="orphan")
        _seed_run()

        resp = client.get("/api/reconciliation/status")
        data = resp.json()
        assert data["pending_orders"] >= 1
        assert data["orphan_orders"] >= 1


# ── GET /api/reconciliation/history ───────────────────────────────────────────


class TestReconciliationHistory:
    def test_empty_history(self, client):
        resp = client.get("/api/reconciliation/history")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_returns_runs_newest_first(self, client):
        old = datetime.now(timezone.utc) - timedelta(hours=1)
        _seed_run(status="mismatch", started_at=old - timedelta(seconds=5), finished_at=old)
        _seed_run(status="ok")

        resp = client.get("/api/reconciliation/history")
        data = resp.json()
        assert len(data) == 2
        assert data[0]["status"] == "ok"
        assert data[1]["status"] == "mismatch"

    def test_pagination(self, client):
        for i in range(5):
            t = datetime.now(timezone.utc) - timedelta(minutes=i)
            _seed_run(started_at=t - timedelta(seconds=2), finished_at=t)

        resp = client.get("/api/reconciliation/history?limit=2&offset=0")
        assert len(resp.json()) == 2

        resp2 = client.get("/api/reconciliation/history?limit=2&offset=2")
        assert len(resp2.json()) == 2

    def test_filter_by_status(self, client):
        _seed_run(status="ok")
        _seed_run(status="mismatch", mismatches=1)

        resp = client.get("/api/reconciliation/history?status=mismatch")
        data = resp.json()
        assert all(d["status"] == "mismatch" for d in data)

    def test_filter_by_severity(self, client):
        _seed_run(status="ok")
        _seed_run(
            status="mismatch",
            events=[{"type": "fill_recovered", "severity": "critical", "detail": "recovered 1"}],
        )
        _seed_run(
            status="mismatch",
            events=[{"type": "order_orphan_detected", "severity": "warning", "detail": "1 orphan"}],
        )

        resp = client.get("/api/reconciliation/history?severity=critical")
        data = resp.json()
        assert len(data) == 1
        assert data[0]["events"][0]["severity"] == "critical"

    def test_filter_by_event_type(self, client):
        _seed_run(
            status="mismatch",
            events=[{"type": "fill_recovered", "severity": "critical", "detail": "recovered"}],
        )
        _seed_run(
            status="mismatch",
            events=[{"type": "order_orphan_detected", "severity": "warning", "detail": "orphan"}],
        )

        resp = client.get("/api/reconciliation/history?event_type=fill_recovered")
        data = resp.json()
        assert len(data) == 1
        assert data[0]["events"][0]["type"] == "fill_recovered"

    def test_filter_by_date_range(self, client):
        old = datetime.now(timezone.utc) - timedelta(days=2)
        recent = datetime.now(timezone.utc) - timedelta(minutes=5)
        _seed_run(started_at=old - timedelta(seconds=5), finished_at=old)
        _seed_run(started_at=recent - timedelta(seconds=5), finished_at=recent)

        cutoff = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        resp = client.get(f"/api/reconciliation/history?start={cutoff}")
        data = resp.json()
        assert len(data) == 1
