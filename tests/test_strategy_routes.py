"""
Tests for ``api.routes.strategy`` – strategy state and signal endpoints.

Verifies:
  - ``GET /api/strategy/state`` returns state records with filters
  - ``GET /api/strategy/signals`` returns derived signal snapshots
  - Symbol and strategy filters work correctly
  - Empty responses when no state records exist
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from api.main import create_app
from db import get_session
from db.models import StrategyStateRecord


# ── Helpers ──────────────────────────────────────────────────────────────────

BASE_TIME = datetime(2026, 2, 1, 0, 0, 0, tzinfo=timezone.utc)


def _seed_strategy_states(db: Session) -> list[StrategyStateRecord]:
    """
    Insert strategy state records covering varied symbols/strategies/states.

    Creates 4 records:
      - BTCUSDT / smart_hodler  → POSITION
      - BTCUSDT / mean_reversion → FLAT
      - ETHUSDT / smart_hodler  → COOLDOWN (with cooldown_until)
      - SOLUSDT / smart_hodler  → REDUCED
    """
    records = [
        StrategyStateRecord(
            symbol="BTCUSDT",
            strategy="smart_hodler",
            state="position",
            consecutive_buy_candles=3,
            state_data={"entry_count": 2, "scaled_in": True},
            created_at=BASE_TIME,
            updated_at=BASE_TIME + timedelta(hours=1),
        ),
        StrategyStateRecord(
            symbol="BTCUSDT",
            strategy="mean_reversion",
            state="flat",
            consecutive_buy_candles=0,
            state_data=None,
            created_at=BASE_TIME,
            updated_at=BASE_TIME + timedelta(hours=2),
        ),
        StrategyStateRecord(
            symbol="ETHUSDT",
            strategy="smart_hodler",
            state="cooldown",
            cooldown_until=BASE_TIME + timedelta(hours=12),
            consecutive_buy_candles=0,
            last_exit_time=BASE_TIME - timedelta(hours=1),
            state_data={"exit_reason": "hard_stop"},
            created_at=BASE_TIME - timedelta(days=1),
            updated_at=BASE_TIME,
        ),
        StrategyStateRecord(
            symbol="SOLUSDT",
            strategy="smart_hodler",
            state="reduced",
            consecutive_buy_candles=5,
            state_data=None,
            created_at=BASE_TIME,
            updated_at=BASE_TIME + timedelta(hours=3),
        ),
    ]
    for r in records:
        db.add(r)
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
    """TestClient with 4 strategy state records pre-seeded."""
    p1, p2, p3, p4 = _make_client()
    with p1 as mock_init, p2, p3, p4:
        mock_pub = MagicMock()
        mock_pub.enabled = True
        mock_init.return_value = mock_pub

        app = create_app()

        with get_session() as db:
            _seed_strategy_states(db)

        with TestClient(app) as tc:
            yield tc


# ── GET /api/strategy/state ──────────────────────────────────────────────────


class TestGetStrategyState:
    """Strategy state endpoint."""

    def test_returns_200(self, seeded_client):
        resp = seeded_client.get("/api/strategy/state")
        assert resp.status_code == 200

    def test_returns_list(self, seeded_client):
        data = seeded_client.get("/api/strategy/state").json()
        assert isinstance(data, list)

    def test_returns_all_states(self, seeded_client):
        data = seeded_client.get("/api/strategy/state").json()
        assert len(data) == 4

    def test_response_shape(self, seeded_client):
        data = seeded_client.get("/api/strategy/state").json()
        state = data[0]
        expected_keys = {
            "symbol", "strategy", "state", "cooldown_until",
            "consecutive_buy_candles", "last_exit_time",
            "state_data", "created_at", "updated_at",
        }
        assert expected_keys == set(state.keys())

    def test_state_values(self, seeded_client):
        data = seeded_client.get("/api/strategy/state").json()
        states_by_key = {
            (s["symbol"], s["strategy"]): s for s in data
        }
        assert states_by_key[("BTCUSDT", "smart_hodler")]["state"] == "position"
        assert states_by_key[("BTCUSDT", "mean_reversion")]["state"] == "flat"
        assert states_by_key[("ETHUSDT", "smart_hodler")]["state"] == "cooldown"
        assert states_by_key[("SOLUSDT", "smart_hodler")]["state"] == "reduced"

    def test_cooldown_until_present(self, seeded_client):
        data = seeded_client.get("/api/strategy/state").json()
        cooldown = [s for s in data if s["state"] == "cooldown"][0]
        assert cooldown["cooldown_until"] is not None

    def test_state_data_present(self, seeded_client):
        data = seeded_client.get("/api/strategy/state").json()
        states_by_key = {
            (s["symbol"], s["strategy"]): s for s in data
        }
        assert states_by_key[("BTCUSDT", "smart_hodler")]["state_data"] == {
            "entry_count": 2, "scaled_in": True,
        }

    def test_state_data_null(self, seeded_client):
        data = seeded_client.get("/api/strategy/state").json()
        states_by_key = {
            (s["symbol"], s["strategy"]): s for s in data
        }
        assert states_by_key[("BTCUSDT", "mean_reversion")]["state_data"] is None

    def test_symbol_filter(self, seeded_client):
        data = seeded_client.get(
            "/api/strategy/state", params={"symbol": "BTCUSDT"}
        ).json()
        assert len(data) == 2
        assert all(s["symbol"] == "BTCUSDT" for s in data)

    def test_symbol_filter_case_insensitive(self, seeded_client):
        data = seeded_client.get(
            "/api/strategy/state", params={"symbol": "ethusdt"}
        ).json()
        assert len(data) == 1
        assert data[0]["symbol"] == "ETHUSDT"

    def test_strategy_filter(self, seeded_client):
        data = seeded_client.get(
            "/api/strategy/state", params={"strategy": "smart_hodler"}
        ).json()
        assert len(data) == 3
        assert all(s["strategy"] == "smart_hodler" for s in data)

    def test_combined_filters(self, seeded_client):
        data = seeded_client.get(
            "/api/strategy/state",
            params={"symbol": "BTCUSDT", "strategy": "mean_reversion"},
        ).json()
        assert len(data) == 1
        assert data[0]["symbol"] == "BTCUSDT"
        assert data[0]["strategy"] == "mean_reversion"

    def test_empty_when_no_states(self, client):
        data = client.get("/api/strategy/state").json()
        assert data == []

    def test_no_match_returns_empty(self, seeded_client):
        data = seeded_client.get(
            "/api/strategy/state", params={"symbol": "DOGEUSDT"}
        ).json()
        assert data == []

    def test_ordered_by_symbol_strategy(self, seeded_client):
        data = seeded_client.get("/api/strategy/state").json()
        keys = [(s["symbol"], s["strategy"]) for s in data]
        assert keys == sorted(keys)


# ── GET /api/strategy/signals ────────────────────────────────────────────────


class TestGetStrategySignals:
    """Strategy signals endpoint."""

    def test_returns_200(self, seeded_client):
        resp = seeded_client.get("/api/strategy/signals")
        assert resp.status_code == 200

    def test_returns_list(self, seeded_client):
        data = seeded_client.get("/api/strategy/signals").json()
        assert isinstance(data, list)

    def test_returns_all_signals(self, seeded_client):
        data = seeded_client.get("/api/strategy/signals").json()
        assert len(data) == 4

    def test_signal_shape(self, seeded_client):
        data = seeded_client.get("/api/strategy/signals").json()
        signal = data[0]
        expected_keys = {
            "symbol", "strategy", "state", "signal",
            "conditions", "updated_at",
        }
        assert expected_keys == set(signal.keys())

    def test_signal_is_hold(self, seeded_client):
        """All signals are HOLD until Phase 3 task 3.8 implements live conditions."""
        data = seeded_client.get("/api/strategy/signals").json()
        assert all(s["signal"] == "hold" for s in data)

    def test_conditions_from_state_data(self, seeded_client):
        data = seeded_client.get("/api/strategy/signals").json()
        signals_by_key = {
            (s["symbol"], s["strategy"]): s for s in data
        }
        assert signals_by_key[("BTCUSDT", "smart_hodler")]["conditions"] == {
            "entry_count": 2, "scaled_in": True,
        }

    def test_conditions_null_when_no_state_data(self, seeded_client):
        data = seeded_client.get("/api/strategy/signals").json()
        signals_by_key = {
            (s["symbol"], s["strategy"]): s for s in data
        }
        assert signals_by_key[("BTCUSDT", "mean_reversion")]["conditions"] is None

    def test_symbol_filter(self, seeded_client):
        data = seeded_client.get(
            "/api/strategy/signals", params={"symbol": "ETHUSDT"}
        ).json()
        assert len(data) == 1
        assert data[0]["symbol"] == "ETHUSDT"

    def test_strategy_filter(self, seeded_client):
        data = seeded_client.get(
            "/api/strategy/signals", params={"strategy": "mean_reversion"}
        ).json()
        assert len(data) == 1
        assert data[0]["strategy"] == "mean_reversion"

    def test_empty_when_no_states(self, client):
        data = client.get("/api/strategy/signals").json()
        assert data == []
