"""
Tests for ``api.main`` – FastAPI application setup.

Verifies:
  - App factory returns a properly configured FastAPI instance
  - ``GET /api/system/health`` returns 200 with connection statuses
  - CORS middleware allows configured origins
  - Lifespan initialises and tears down the event publisher
  - Route modules are mounted (or gracefully skipped when absent)
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.main import create_app


# ── Helpers ──────────────────────────────────────────────────────────────────


@pytest.fixture()
def client():
    """
    TestClient that patches Redis so tests don't need a live instance.

    The sync EventPublisher and async Redis client are both mocked.
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
def client_no_redis():
    """
    TestClient where Redis is unavailable.
    """
    with (
        patch("api.main.init_publisher") as mock_init,
        patch("api.main.reset_publisher"),
        patch("api.main.check_connection", return_value=True),
        patch("redis.asyncio.from_url", side_effect=ConnectionError("refused")),
    ):
        mock_pub = MagicMock()
        mock_pub.enabled = False
        mock_init.return_value = mock_pub

        app = create_app()
        with TestClient(app) as tc:
            yield tc


@pytest.fixture()
def client_no_db():
    """
    TestClient where Postgres is unavailable.
    """
    mock_async_redis = AsyncMock()
    mock_async_redis.ping = AsyncMock(return_value=True)
    mock_async_redis.close = AsyncMock()

    with (
        patch("api.main.init_publisher") as mock_init,
        patch("api.main.reset_publisher"),
        patch("api.main.check_connection", return_value=False),
        patch("api.routes.system.check_connection", return_value=False),
        patch("redis.asyncio.from_url", return_value=mock_async_redis),
    ):
        mock_pub = MagicMock()
        mock_pub.enabled = True
        mock_init.return_value = mock_pub

        app = create_app()
        with TestClient(app) as tc:
            yield tc


# ── App Factory ──────────────────────────────────────────────────────────────


class TestCreateApp:
    """create_app() returns a correctly configured FastAPI instance."""

    def test_returns_fastapi_instance(self):
        with (
            patch("api.main.init_publisher", return_value=MagicMock()),
            patch("api.main.reset_publisher"),
            patch("api.main.check_connection", return_value=True),
            patch("redis.asyncio.from_url", return_value=AsyncMock()),
        ):
            app = create_app()
        assert isinstance(app, FastAPI)

    def test_app_title(self):
        with (
            patch("api.main.init_publisher", return_value=MagicMock()),
            patch("api.main.reset_publisher"),
            patch("api.main.check_connection", return_value=True),
            patch("redis.asyncio.from_url", return_value=AsyncMock()),
        ):
            app = create_app()
        assert app.title == "GoldenGibbon"

    def test_app_version(self):
        with (
            patch("api.main.init_publisher", return_value=MagicMock()),
            patch("api.main.reset_publisher"),
            patch("api.main.check_connection", return_value=True),
            patch("redis.asyncio.from_url", return_value=AsyncMock()),
        ):
            app = create_app()
        assert app.version == "0.1.0"


# ── Health Endpoint ──────────────────────────────────────────────────────────


class TestHealth:
    """GET /api/system/health returns system connectivity status."""

    def test_health_returns_200(self, client):
        resp = client.get("/api/system/health")
        assert resp.status_code == 200

    def test_health_status_ok(self, client):
        data = client.get("/api/system/health").json()
        assert data["status"] == "ok"

    def test_health_db_true_when_connected(self, client):
        data = client.get("/api/system/health").json()
        assert data["db"] is True

    def test_health_redis_true_when_connected(self, client):
        data = client.get("/api/system/health").json()
        assert data["redis"] is True

    def test_health_db_false_when_down(self, client_no_db):
        data = client_no_db.get("/api/system/health").json()
        assert data["db"] is False

    def test_health_redis_false_when_down(self, client_no_redis):
        data = client_no_redis.get("/api/system/health").json()
        assert data["redis"] is False


# ── CORS ─────────────────────────────────────────────────────────────────────


class TestCORS:
    """CORS middleware is configured correctly."""

    def test_allows_default_origin(self, client):
        resp = client.options(
            "/api/system/health",
            headers={
                "Origin": "http://localhost:5858",
                "Access-Control-Request-Method": "GET",
            },
        )
        assert resp.headers.get("access-control-allow-origin") == "http://localhost:5858"

    def test_rejects_unknown_origin(self, client):
        resp = client.options(
            "/api/system/health",
            headers={
                "Origin": "http://evil.example.com",
                "Access-Control-Request-Method": "GET",
            },
        )
        # CORSMiddleware doesn't set the header for disallowed origins
        assert resp.headers.get("access-control-allow-origin") != "http://evil.example.com"

    @patch.dict("os.environ", {"CORS_ORIGINS": "http://a.com,http://b.com"})
    def test_custom_origins_from_env(self):
        mock_async_redis = AsyncMock()
        mock_async_redis.ping = AsyncMock(return_value=True)
        mock_async_redis.close = AsyncMock()

        with (
            patch("api.main.init_publisher", return_value=MagicMock()),
            patch("api.main.reset_publisher"),
            patch("api.main.check_connection", return_value=True),
            patch("redis.asyncio.from_url", return_value=mock_async_redis),
        ):
            app = create_app()
            with TestClient(app) as tc:
                resp = tc.options(
                    "/api/system/health",
                    headers={
                        "Origin": "http://b.com",
                        "Access-Control-Request-Method": "GET",
                    },
                )
                assert resp.headers.get("access-control-allow-origin") == "http://b.com"


# ── Lifespan ─────────────────────────────────────────────────────────────────


class TestLifespan:
    """Lifespan hooks initialise and clean up resources."""

    def test_publisher_initialised_on_startup(self):
        mock_async_redis = AsyncMock()
        mock_async_redis.ping = AsyncMock(return_value=True)
        mock_async_redis.close = AsyncMock()

        with (
            patch("api.main.init_publisher") as mock_init,
            patch("api.main.reset_publisher"),
            patch("api.main.check_connection", return_value=True),
            patch("redis.asyncio.from_url", return_value=mock_async_redis),
        ):
            mock_init.return_value = MagicMock()
            app = create_app()
            with TestClient(app):
                mock_init.assert_called_once()

    def test_publisher_reset_on_shutdown(self):
        mock_async_redis = AsyncMock()
        mock_async_redis.ping = AsyncMock(return_value=True)
        mock_async_redis.close = AsyncMock()

        with (
            patch("api.main.init_publisher", return_value=MagicMock()),
            patch("api.main.reset_publisher") as mock_reset,
            patch("api.main.check_connection", return_value=True),
            patch("redis.asyncio.from_url", return_value=mock_async_redis),
        ):
            app = create_app()
            with TestClient(app):
                pass  # enter + exit triggers startup + shutdown
            mock_reset.assert_called_once()

    def test_startup_event_published(self):
        mock_async_redis = AsyncMock()
        mock_async_redis.ping = AsyncMock(return_value=True)
        mock_async_redis.close = AsyncMock()

        mock_pub = MagicMock()
        with (
            patch("api.main.init_publisher", return_value=mock_pub),
            patch("api.main.reset_publisher"),
            patch("api.main.check_connection", return_value=True),
            patch("redis.asyncio.from_url", return_value=mock_async_redis),
        ):
            app = create_app()
            with TestClient(app):
                # First publish call should be STARTUP
                calls = mock_pub.publish.call_args_list
                assert len(calls) >= 1
                first_call = calls[0]
                assert first_call.args[1].value == "startup"

    def test_async_redis_stored_on_app_state(self):
        mock_async_redis = AsyncMock()
        mock_async_redis.ping = AsyncMock(return_value=True)
        mock_async_redis.close = AsyncMock()

        with (
            patch("api.main.init_publisher", return_value=MagicMock()),
            patch("api.main.reset_publisher"),
            patch("api.main.check_connection", return_value=True),
            patch("redis.asyncio.from_url", return_value=mock_async_redis),
        ):
            app = create_app()
            with TestClient(app):
                assert app.state.redis is mock_async_redis

    def test_async_redis_closed_on_shutdown(self):
        mock_async_redis = AsyncMock()
        mock_async_redis.ping = AsyncMock(return_value=True)
        mock_async_redis.close = AsyncMock()

        with (
            patch("api.main.init_publisher", return_value=MagicMock()),
            patch("api.main.reset_publisher"),
            patch("api.main.check_connection", return_value=True),
            patch("redis.asyncio.from_url", return_value=mock_async_redis),
        ):
            app = create_app()
            with TestClient(app):
                pass
            mock_async_redis.close.assert_awaited_once()

    def test_survives_redis_unavailable(self):
        """App starts even when Redis is completely down."""
        with (
            patch("api.main.init_publisher") as mock_init,
            patch("api.main.reset_publisher"),
            patch("api.main.check_connection", return_value=True),
            patch("redis.asyncio.from_url", side_effect=ConnectionError("refused")),
        ):
            mock_pub = MagicMock()
            mock_pub.enabled = False
            mock_init.return_value = mock_pub

            app = create_app()
            with TestClient(app) as tc:
                resp = tc.get("/api/system/health")
                assert resp.status_code == 200

    def test_survives_db_unavailable(self):
        """App starts even when Postgres is completely down."""
        mock_async_redis = AsyncMock()
        mock_async_redis.ping = AsyncMock(return_value=True)
        mock_async_redis.close = AsyncMock()

        with (
            patch("api.main.init_publisher", return_value=MagicMock()),
            patch("api.main.reset_publisher"),
            patch("api.main.check_connection", return_value=False),
            patch("redis.asyncio.from_url", return_value=mock_async_redis),
        ):
            app = create_app()
            with TestClient(app) as tc:
                resp = tc.get("/api/system/health")
                assert resp.status_code == 200


# ── Route Mounting ───────────────────────────────────────────────────────────


class TestRouteMounting:
    """Route modules are mounted when implemented."""

    def test_implemented_route_is_reachable(self, client):
        """Implemented route modules respond (not 404/500)."""
        resp = client.get("/api/market/candles/BTCUSDT")
        assert resp.status_code == 200

    def test_unimplemented_route_returns_404(self, client):
        """Routes with no module yet return 404 (not 500)."""
        resp = client.get("/api/nonexistent/foo")
        assert resp.status_code == 404

    def test_health_always_available(self, client):
        """Health endpoint is always present regardless of route modules."""
        resp = client.get("/api/system/health")
        assert resp.status_code == 200
