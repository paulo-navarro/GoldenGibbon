"""
Tests for ``api.routes.trades`` – trade list and stats endpoints.

Verifies:
  - ``GET /api/trades/`` returns filtered trades chronologically
  - Respects run_id, symbol, strategy, exit_reason, limit, start/end
  - Defaults to latest run_id when not provided
  - ``GET /api/trades/stats`` returns aggregate statistics
  - Stats are computed correctly (win rate, PnL, profit factor, …)
  - Returns zeroed stats when no trades match
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
from db.models import BacktestResult, TradeRecord


# ── Helpers ──────────────────────────────────────────────────────────────────

BASE_TIME = datetime(2026, 2, 1, 0, 0, 0, tzinfo=timezone.utc)


def _seed_backtest_result(db: Session, run_id: str) -> BacktestResult:
    """Insert a minimal BacktestResult so FK on trade_records is satisfied."""
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


def _seed_trades(
    db: Session,
    *,
    run_id: str = "run-abc",
    count: int = 10,
    base_time: datetime = BASE_TIME,
    symbol: str = "BTCUSDT",
    strategy: str = "smart_hodler",
    trading_mode: str = "paper",
) -> list[TradeRecord]:
    """Insert *count* trade records with alternating win/loss."""
    records = []
    exit_reasons = [
        "trailing_stop", "ema_cross", "hard_stop",
        "momentum_fade", "trailing_stop", "time_stop",
        "close_below_ema200", "trailing_stop", "hard_stop", "manual",
    ]
    for i in range(count):
        is_win = i % 3 != 0  # 2 out of 3 are winners
        pnl = Decimal("150") + i * 10 if is_win else Decimal("-80") - i * 5
        pnl_pct = Decimal("3.5") + i if is_win else Decimal("-2.0") - i

        r = TradeRecord(
            run_id=run_id,
            symbol=symbol,
            strategy=strategy,
            side="long",
            entry_price=Decimal("42000") + i * 100,
            exit_price=Decimal("42000") + i * 100 + pnl,
            size=Decimal("0.1"),
            entry_time=base_time + timedelta(hours=i * 2),
            exit_time=base_time + timedelta(hours=i * 2 + 1),
            pnl_usdt=pnl,
            pnl_percent=pnl_pct,
            duration_minutes=60 + i * 5,
            exit_reason=exit_reasons[i % len(exit_reasons)],
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
    """TestClient with 15 trades pre-seeded in a single run."""
    p1, p2, p3, p4 = _make_client()
    with p1 as mock_init, p2, p3, p4:
        mock_pub = MagicMock()
        mock_pub.enabled = True
        mock_init.return_value = mock_pub

        app = create_app()

        with get_session() as db:
            _seed_backtest_result(db, "run-abc")
            _seed_trades(db, run_id="run-abc", count=15)

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
            _seed_trades(
                db, run_id="run-old", count=5,
                base_time=BASE_TIME - timedelta(days=7),
                trading_mode="live",
            )
            _seed_backtest_result(db, "run-new")
            _seed_trades(
                db, run_id="run-new", count=10,
                base_time=BASE_TIME,
            )

        with TestClient(app) as tc:
            yield tc


@pytest.fixture()
def mixed_client():
    """TestClient with trades across different symbols and strategies."""
    p1, p2, p3, p4 = _make_client()
    with p1 as mock_init, p2, p3, p4:
        mock_pub = MagicMock()
        mock_pub.enabled = True
        mock_init.return_value = mock_pub

        app = create_app()

        with get_session() as db:
            _seed_backtest_result(db, "run-mix")
            _seed_trades(db, run_id="run-mix", count=5, symbol="BTCUSDT", strategy="smart_hodler")
            _seed_trades(db, run_id="run-mix", count=5, symbol="ETHUSDT", strategy="smart_hodler",
                         base_time=BASE_TIME + timedelta(days=1))
            _seed_trades(db, run_id="run-mix", count=5, symbol="BTCUSDT", strategy="mean_reversion",
                         base_time=BASE_TIME + timedelta(days=2))

        with TestClient(app) as tc:
            yield tc


# ── GET /api/trades/ ─────────────────────────────────────────────────────────


class TestGetTrades:
    """Trade list endpoint."""

    def test_returns_200(self, seeded_client):
        resp = seeded_client.get("/api/trades/")
        assert resp.status_code == 200

    def test_returns_list(self, seeded_client):
        data = seeded_client.get("/api/trades/").json()
        assert isinstance(data, list)

    def test_returns_all_trades(self, seeded_client):
        data = seeded_client.get("/api/trades/").json()
        assert len(data) == 15

    def test_chronological_order(self, seeded_client):
        data = seeded_client.get("/api/trades/").json()
        exit_times = [t["exit_time"] for t in data]
        assert exit_times == sorted(exit_times)

    def test_trade_shape(self, seeded_client):
        data = seeded_client.get("/api/trades/").json()
        trade = data[0]
        expected_keys = {
            "symbol", "strategy", "entry_price", "exit_price", "size",
            "entry_time", "exit_time", "pnl_usdt", "pnl_percent",
            "duration_minutes", "exit_reason",
            "max_favorable_excursion", "max_adverse_excursion",
            "exit_price_estimated",
        }
        assert expected_keys == set(trade.keys())

    def test_limit_param(self, seeded_client):
        data = seeded_client.get("/api/trades/", params={"limit": 5}).json()
        assert len(data) == 5

    def test_limit_returns_latest(self, seeded_client):
        """When limit < total, returns the most recent trades."""
        data = seeded_client.get("/api/trades/", params={"limit": 3}).json()
        assert len(data) == 3
        # Should be chronological (latest 3)
        exit_times = [t["exit_time"] for t in data]
        assert exit_times == sorted(exit_times)

    def test_limit_bounds_min(self, seeded_client):
        resp = seeded_client.get("/api/trades/", params={"limit": 0})
        assert resp.status_code == 422

    def test_limit_bounds_max(self, seeded_client):
        resp = seeded_client.get("/api/trades/", params={"limit": 10001})
        assert resp.status_code == 422

    def test_defaults_to_paper_mode(self, multi_run_client):
        """Without params, returns only paper trading_mode trades."""
        data = multi_run_client.get("/api/trades/").json()
        # run-new (paper) has 10 trades; run-old (live) is excluded
        assert len(data) == 10

    def test_explicit_run_id(self, multi_run_client):
        data = multi_run_client.get(
            "/api/trades/", params={"run_id": "run-old"}
        ).json()
        assert len(data) == 5

    def test_unknown_run_id_returns_empty(self, seeded_client):
        data = seeded_client.get(
            "/api/trades/", params={"run_id": "nonexistent"}
        ).json()
        assert data == []

    def test_empty_when_no_trades(self, client):
        data = client.get("/api/trades/").json()
        assert data == []

    def test_symbol_filter(self, mixed_client):
        data = mixed_client.get(
            "/api/trades/", params={"symbol": "ETHUSDT"}
        ).json()
        assert len(data) == 5
        assert all(t["symbol"] == "ETHUSDT" for t in data)

    def test_symbol_filter_case_insensitive(self, mixed_client):
        data = mixed_client.get(
            "/api/trades/", params={"symbol": "ethusdt"}
        ).json()
        assert len(data) == 5

    def test_strategy_filter(self, mixed_client):
        data = mixed_client.get(
            "/api/trades/", params={"strategy": "mean_reversion"}
        ).json()
        assert len(data) == 5
        assert all(t["strategy"] == "mean_reversion" for t in data)

    def test_exit_reason_filter(self, seeded_client):
        data = seeded_client.get(
            "/api/trades/", params={"exit_reason": "trailing_stop"}
        ).json()
        assert len(data) > 0
        assert all(t["exit_reason"] == "trailing_stop" for t in data)

    def test_start_filter(self, seeded_client):
        start = (BASE_TIME + timedelta(hours=20)).strftime("%Y-%m-%dT%H:%M:%S")
        data = seeded_client.get(
            "/api/trades/", params={"start": start}
        ).json()
        for t in data:
            assert t["exit_time"] >= start

    def test_end_filter(self, seeded_client):
        end = (BASE_TIME + timedelta(hours=5)).strftime("%Y-%m-%dT%H:%M:%S")
        data = seeded_client.get(
            "/api/trades/", params={"end": end}
        ).json()
        for t in data:
            assert t["exit_time"] <= end + "Z"  # may have trailing Z

    def test_start_and_end_filter(self, seeded_client):
        start = (BASE_TIME + timedelta(hours=5)).strftime("%Y-%m-%dT%H:%M:%S")
        end = (BASE_TIME + timedelta(hours=15)).strftime("%Y-%m-%dT%H:%M:%S")
        data = seeded_client.get(
            "/api/trades/", params={"start": start, "end": end},
        ).json()
        assert len(data) > 0
        assert len(data) < 15


# ── GET /api/trades/stats ────────────────────────────────────────────────────


class TestGetTradeStats:
    """Trade statistics endpoint."""

    def test_returns_200(self, seeded_client):
        resp = seeded_client.get("/api/trades/stats")
        assert resp.status_code == 200

    def test_response_shape(self, seeded_client):
        data = seeded_client.get("/api/trades/stats").json()
        expected_keys = {
            "total_trades", "winning_trades", "losing_trades",
            "win_rate", "total_pnl", "avg_win_percent",
            "avg_loss_percent", "profit_factor",
            "avg_duration_minutes", "max_consecutive_wins",
            "max_consecutive_losses",
        }
        assert set(data.keys()) == expected_keys

    def test_total_trades(self, seeded_client):
        data = seeded_client.get("/api/trades/stats").json()
        assert data["total_trades"] == 15

    def test_win_loss_counts(self, seeded_client):
        data = seeded_client.get("/api/trades/stats").json()
        # Pattern: i%3 != 0 is winner → indices 1,2,4,5,7,8,10,11,13,14 = 10 wins
        assert data["winning_trades"] == 10
        assert data["losing_trades"] == 5

    def test_win_rate(self, seeded_client):
        data = seeded_client.get("/api/trades/stats").json()
        wr = Decimal(data["win_rate"])
        # 10/15 ≈ 66.666...
        assert Decimal("66") < wr < Decimal("67")

    def test_total_pnl_positive(self, seeded_client):
        """With 10 winners and 5 losers the net should be positive."""
        data = seeded_client.get("/api/trades/stats").json()
        assert Decimal(data["total_pnl"]) > 0

    def test_profit_factor(self, seeded_client):
        data = seeded_client.get("/api/trades/stats").json()
        assert data["profit_factor"] is not None
        assert Decimal(data["profit_factor"]) > 0

    def test_avg_duration(self, seeded_client):
        data = seeded_client.get("/api/trades/stats").json()
        assert data["avg_duration_minutes"] is not None
        assert data["avg_duration_minutes"] > 0

    def test_consecutive_streaks(self, seeded_client):
        data = seeded_client.get("/api/trades/stats").json()
        assert data["max_consecutive_wins"] >= 1
        assert data["max_consecutive_losses"] >= 1

    def test_empty_stats_when_no_trades(self, client):
        data = client.get("/api/trades/stats").json()
        assert data["total_trades"] == 0
        assert data["win_rate"] == "0"
        assert data["total_pnl"] == "0"
        assert data["profit_factor"] is None

    def test_stats_respect_symbol_filter(self, mixed_client):
        data = mixed_client.get(
            "/api/trades/stats", params={"symbol": "ETHUSDT"}
        ).json()
        assert data["total_trades"] == 5

    def test_stats_respect_strategy_filter(self, mixed_client):
        data = mixed_client.get(
            "/api/trades/stats", params={"strategy": "mean_reversion"}
        ).json()
        assert data["total_trades"] == 5
