"""
Tests for ``api.routes.portfolio`` – portfolio and equity-curve endpoints.

Verifies:
  - ``GET /api/portfolio`` returns current balance, equity, and positions
  - Returns zero defaults when no snapshot exists
  - Includes open positions from the positions table
  - ``GET /api/portfolio/equity-curve`` returns snapshots chronologically
  - Defaults to the latest run_id when not specified
  - Respects explicit run_id, limit, start/end query params
  - Returns empty list when no snapshots exist
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
from db.models import BacktestResult, PositionRecord
from db.models import PortfolioSnapshot as PortfolioSnapshotRecord


# ── Helpers ──────────────────────────────────────────────────────────────────

BASE_TIME = datetime(2026, 2, 1, 0, 0, 0, tzinfo=timezone.utc)


def _seed_positions(
    db: Session,
    *,
    count: int = 2,
    base_time: datetime = BASE_TIME,
) -> list[PositionRecord]:
    """Insert *count* open position records."""
    symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"]
    records = []
    for i in range(count):
        r = PositionRecord(
            symbol=symbols[i % len(symbols)],
            side="long",
            size=Decimal("0.5") + i,
            entry_price=Decimal("42000") + i * 1000,
            entry_time=base_time + timedelta(hours=i),
            highest_close=Decimal("43000") + i * 1000,
            trailing_stop_price=Decimal("41500") + i * 1000,
            hard_stop_price=Decimal("40740") + i * 1000,
            strategy="smart_hodler",
            scale_in_count=min(i, 2),
            buy_signal_candles=i,
        )
        db.add(r)
        records.append(r)
    db.commit()
    return records


def _seed_backtest_result(db: Session, run_id: str) -> BacktestResult:
    """Insert a minimal BacktestResult so FK on snapshots is satisfied."""
    br = BacktestResult(
        run_id=run_id,
        strategy="smart_hodler",
        symbol="BTCUSDT",
        start_date=BASE_TIME,
        end_date=BASE_TIME + timedelta(days=30),
        initial_capital=Decimal("10000"),
        final_capital=Decimal("11000"),
        total_return=Decimal("10.0000"),
        total_trades=5,
        winning_trades=3,
        losing_trades=2,
        win_rate=Decimal("60.0000"),
        avg_win=Decimal("500"),
        avg_loss=Decimal("250"),
        max_drawdown=Decimal("5.0000"),
    )
    db.add(br)
    db.commit()
    return br


def _seed_snapshots(
    db: Session,
    *,
    run_id: str = "run-abc",
    count: int = 10,
    base_time: datetime = BASE_TIME,
    base_equity: Decimal = Decimal("10000"),
    trading_mode: str = "paper",
) -> list[PortfolioSnapshotRecord]:
    """Insert *count* portfolio snapshots for a given run."""
    records = []
    for i in range(count):
        r = PortfolioSnapshotRecord(
            run_id=run_id,
            timestamp=base_time + timedelta(hours=i),
            usdt_balance=base_equity + i * 50,
            positions_value=Decimal("500") + i * 10,
            total_equity=base_equity + i * 50 + 500 + i * 10,
            daily_pnl=Decimal("50") if i > 0 else None,
            total_pnl=Decimal(str(i * 50)),
            open_positions_count=2,
            trading_mode=trading_mode,
        )
        db.add(r)
        records.append(r)
    db.commit()
    return records


# ── Fixtures ─────────────────────────────────────────────────────────────────


def _make_client(mode: str = "paper"):
    """Create a TestClient with mocked Redis."""
    mock_async_redis = AsyncMock()
    mock_async_redis.ping = AsyncMock(return_value=True)
    mock_async_redis.close = AsyncMock()

    return (
        patch("api.main.init_publisher"),
        patch("api.main.reset_publisher"),
        patch("api.main.check_connection", return_value=True),
        patch("redis.asyncio.from_url", return_value=mock_async_redis),
        patch("api.routes.portfolio._default_trading_mode", return_value=mode),
    )


@pytest.fixture()
def client():
    """TestClient with real DB session but mocked Redis — no seed data."""
    p1, p2, p3, p4, p5 = _make_client()
    with p1 as mock_init, p2, p3, p4, p5:
        mock_pub = MagicMock()
        mock_pub.enabled = True
        mock_init.return_value = mock_pub

        app = create_app()
        with TestClient(app) as tc:
            yield tc


@pytest.fixture()
def seeded_client():
    """TestClient with positions + snapshots pre-seeded."""
    p1, p2, p3, p4, p5 = _make_client()
    with p1 as mock_init, p2, p3, p4, p5:
        mock_pub = MagicMock()
        mock_pub.enabled = True
        mock_init.return_value = mock_pub

        app = create_app()

        with get_session() as db:
            _seed_positions(db, count=3)
            _seed_backtest_result(db, "run-abc")
            _seed_snapshots(db, run_id="run-abc", count=15)

        with TestClient(app) as tc:
            yield tc


@pytest.fixture()
def multi_run_client():
    """TestClient with two different run_ids seeded."""
    p1, p2, p3, p4, p5 = _make_client()
    with p1 as mock_init, p2, p3, p4, p5:
        mock_pub = MagicMock()
        mock_pub.enabled = True
        mock_init.return_value = mock_pub

        app = create_app()

        with get_session() as db:
            _seed_backtest_result(db, "run-old")
            _seed_snapshots(
                db, run_id="run-old", count=5,
                base_time=BASE_TIME - timedelta(days=7),
                base_equity=Decimal("8000"),
                trading_mode="live",
            )
            _seed_backtest_result(db, "run-new")
            _seed_snapshots(
                db, run_id="run-new", count=10,
                base_time=BASE_TIME,
                base_equity=Decimal("10000"),
            )

        with TestClient(app) as tc:
            yield tc


# ── GET /api/portfolio ───────────────────────────────────────────────────────


class TestGetPortfolio:
    """Current portfolio state endpoint."""

    def test_returns_200(self, seeded_client):
        resp = seeded_client.get("/api/portfolio/")
        assert resp.status_code == 200

    def test_response_shape(self, seeded_client):
        data = seeded_client.get("/api/portfolio/").json()
        expected_keys = {
            "usdt_balance", "equity", "positions_value",
            "total_pnl", "open_positions_count", "positions",
            "last_updated",
        }
        assert set(data.keys()) == expected_keys

    def test_positions_count(self, seeded_client):
        data = seeded_client.get("/api/portfolio/").json()
        assert data["open_positions_count"] == 3

    def test_positions_are_listed(self, seeded_client):
        data = seeded_client.get("/api/portfolio/").json()
        assert len(data["positions"]) == 3

    def test_positions_have_symbol(self, seeded_client):
        data = seeded_client.get("/api/portfolio/").json()
        symbols = [p["symbol"] for p in data["positions"]]
        assert "BTCUSDT" in symbols
        assert "ETHUSDT" in symbols

    def test_balance_from_snapshot(self, seeded_client):
        """Balance comes from the latest snapshot."""
        data = seeded_client.get("/api/portfolio/").json()
        # Last of 15 snapshots: base_equity + 14 * 50 = 10700
        assert data["usdt_balance"] == "10700.00000000"

    def test_equity_from_snapshot(self, seeded_client):
        """Equity = usdt_balance + positions_value (MTM from entry prices when no candles)."""
        data = seeded_client.get("/api/portfolio/").json()
        # usdt_balance from snapshot fallback: 10700
        # positions: 0.5*42000 + 1.5*43000 + 2.5*44000 = 195500
        # equity = 10700 + 195500 = 206200
        usdt_balance = Decimal(data["usdt_balance"])
        positions_value = Decimal(data["positions_value"])
        equity = Decimal(data["equity"])
        assert equity == usdt_balance + positions_value

    def test_last_updated_set(self, seeded_client):
        data = seeded_client.get("/api/portfolio/").json()
        assert data["last_updated"] is not None

    def test_defaults_when_no_snapshot(self, client):
        """Returns zero defaults when no snapshot in DB."""
        data = client.get("/api/portfolio/").json()
        assert data["usdt_balance"] == "0"
        assert data["equity"] == "0"
        assert data["open_positions_count"] == 0
        assert data["positions"] == []
        assert data["last_updated"] is None

    def test_positions_without_snapshot(self, client):
        """Positions appear even if no snapshot exists."""
        with get_session() as db:
            _seed_positions(db, count=1)

        data = client.get("/api/portfolio/").json()
        assert data["open_positions_count"] == 1
        assert len(data["positions"]) == 1
        assert data["usdt_balance"] == "0"

    def test_position_fields(self, seeded_client):
        data = seeded_client.get("/api/portfolio/").json()
        pos = data["positions"][0]
        assert "symbol" in pos
        assert "size" in pos
        assert "entry_price" in pos
        assert "trailing_stop_price" in pos


# ── GET /api/portfolio/equity-curve ──────────────────────────────────────────


class TestGetEquityCurve:
    """Equity curve endpoint."""

    def test_returns_200(self, seeded_client):
        resp = seeded_client.get("/api/portfolio/equity-curve")
        assert resp.status_code == 200

    def test_returns_list(self, seeded_client):
        data = seeded_client.get("/api/portfolio/equity-curve").json()
        assert isinstance(data, list)

    def test_returns_all_snapshots(self, seeded_client):
        data = seeded_client.get("/api/portfolio/equity-curve").json()
        assert len(data) == 15

    def test_chronological_order(self, seeded_client):
        data = seeded_client.get("/api/portfolio/equity-curve").json()
        timestamps = [s["timestamp"] for s in data]
        assert timestamps == sorted(timestamps)

    def test_snapshot_shape(self, seeded_client):
        data = seeded_client.get("/api/portfolio/equity-curve").json()
        expected_keys = {
            "timestamp", "usdt_balance", "positions_value",
            "total_equity", "daily_pnl", "total_pnl",
            "open_positions_count",
        }
        assert set(data[0].keys()) == expected_keys

    def test_limit_param(self, seeded_client):
        data = seeded_client.get(
            "/api/portfolio/equity-curve", params={"limit": 5}
        ).json()
        assert len(data) == 5

    def test_limit_returns_latest(self, seeded_client):
        """When limit < total, returns the most recent snapshots."""
        data = seeded_client.get(
            "/api/portfolio/equity-curve", params={"limit": 3}
        ).json()
        # Last 3 of 15 snapshots — check they're chronological
        assert len(data) == 3
        timestamps = [s["timestamp"] for s in data]
        assert timestamps == sorted(timestamps)
        # The last snapshot should be the newest
        last_equity = data[-1]["total_equity"]
        assert Decimal(last_equity) > Decimal(data[0]["total_equity"])

    def test_limit_bounds_min(self, seeded_client):
        resp = seeded_client.get(
            "/api/portfolio/equity-curve", params={"limit": 0}
        )
        assert resp.status_code == 422

    def test_limit_bounds_max(self, seeded_client):
        resp = seeded_client.get(
            "/api/portfolio/equity-curve", params={"limit": 10001}
        )
        assert resp.status_code == 422

    def test_start_filter(self, seeded_client):
        """Start filter narrows results."""
        start = (BASE_TIME + timedelta(hours=10)).strftime("%Y-%m-%dT%H:%M:%S")
        data = seeded_client.get(
            "/api/portfolio/equity-curve", params={"start": start}
        ).json()
        # Hours 10..14 → 5 snapshots
        assert len(data) == 5

    def test_end_filter(self, seeded_client):
        """End filter narrows results."""
        end = (BASE_TIME + timedelta(hours=4)).strftime("%Y-%m-%dT%H:%M:%S")
        data = seeded_client.get(
            "/api/portfolio/equity-curve", params={"end": end}
        ).json()
        # Hours 0..4 → 5 snapshots
        assert len(data) == 5

    def test_start_and_end_filter(self, seeded_client):
        start = (BASE_TIME + timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M:%S")
        end = (BASE_TIME + timedelta(hours=7)).strftime("%Y-%m-%dT%H:%M:%S")
        data = seeded_client.get(
            "/api/portfolio/equity-curve",
            params={"start": start, "end": end},
        ).json()
        # Hours 3..7 → 5 snapshots
        assert len(data) == 5

    def test_defaults_to_paper_mode(self, multi_run_client):
        """Without params, returns only paper trading_mode snapshots."""
        data = multi_run_client.get("/api/portfolio/equity-curve").json()
        # run-new (paper) has 10 snapshots; run-old (live) is excluded
        assert len(data) == 10

    def test_explicit_run_id(self, multi_run_client):
        data = multi_run_client.get(
            "/api/portfolio/equity-curve", params={"run_id": "run-old"}
        ).json()
        assert len(data) == 5

    def test_unknown_run_id_returns_empty(self, seeded_client):
        data = seeded_client.get(
            "/api/portfolio/equity-curve",
            params={"run_id": "nonexistent"},
        ).json()
        assert data == []


# ── Live mode: account-level curve (task 9.11) ───────────────────────────────


@pytest.fixture()
def live_client():
    """TestClient in live mode with per-slice AND account-level snapshots."""
    p1, p2, p3, p4, p5 = _make_client(mode="live")
    with p1 as mock_init, p2, p3, p4, p5:
        mock_pub = MagicMock()
        mock_pub.enabled = True
        mock_init.return_value = mock_pub

        app = create_app()

        with get_session() as db:
            # Per-slice tick snapshots (legacy curve pollution)
            _seed_snapshots(
                db, run_id="live_smart_hodler_BTCUSDT", count=5,
                base_equity=Decimal("3000"), trading_mode="live",
            )
            _seed_snapshots(
                db, run_id="live_smart_hodler_ETHUSDT", count=5,
                base_equity=Decimal("2000"), trading_mode="live",
            )
            # Account-level truth written by sync_exchange_balances
            _seed_snapshots(
                db, run_id="account", count=3,
                base_equity=Decimal("50"), trading_mode="live",
            )

        with TestClient(app) as tc:
            yield tc


class TestEquityCurveLiveAccountLevel:
    """In live mode the curve must contain only account-level snapshots."""

    def test_returns_only_account_rows(self, live_client):
        data = live_client.get("/api/portfolio/equity-curve").json()
        assert len(data) == 3

    def test_account_equity_values(self, live_client):
        """Slice equities (3000/2000) must not leak into the curve."""
        data = live_client.get("/api/portfolio/equity-curve").json()
        for snap in data:
            assert Decimal(snap["total_equity"]) < Decimal("1000")

    def test_explicit_slice_run_id_still_works(self, live_client):
        data = live_client.get(
            "/api/portfolio/equity-curve",
            params={"run_id": "live_smart_hodler_BTCUSDT"},
        ).json()
        assert len(data) == 5


# ── Snapshot uniqueness (task 9.11) ──────────────────────────────────────────


class TestSnapshotUniqueness:
    """(run_id, timestamp) is unique — double-writes must be rejected."""

    def test_duplicate_run_ts_rejected(self):
        from sqlalchemy.exc import IntegrityError

        def _row():
            return PortfolioSnapshotRecord(
                run_id="dup-run",
                timestamp=BASE_TIME,
                usdt_balance=Decimal("100"),
                positions_value=Decimal("0"),
                total_equity=Decimal("100"),
                total_pnl=Decimal("0"),
                open_positions_count=0,
                trading_mode="live",
            )

        with pytest.raises(IntegrityError):
            with get_session() as db:
                db.add(_row())
                db.commit()
                db.add(_row())
                db.commit()

    def test_empty_when_no_snapshots(self, client):
        data = client.get("/api/portfolio/equity-curve").json()
        assert data == []
