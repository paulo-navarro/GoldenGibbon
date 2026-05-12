"""
Tests for ``api.routes.system`` – health check and log viewer endpoints.

Verifies:
  - ``GET /api/system/health`` returns 200 with enriched status
  - ``GET /api/system/logs`` tails the log file with filters
  - Logs endpoint returns 404 when log file is missing
  - Line count and level filter params work correctly
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from api.main import create_app


# ── Fixtures ─────────────────────────────────────────────────────────────────


def _make_client():
    """Create patch contexts for mocked Redis and Celery probes."""
    mock_async_redis = AsyncMock()
    mock_async_redis.ping = AsyncMock(return_value=True)
    mock_async_redis.close = AsyncMock()

    return (
        patch("api.main.init_publisher"),
        patch("api.main.reset_publisher"),
        patch("api.main.check_connection", return_value=True),
        patch("redis.asyncio.from_url", return_value=mock_async_redis),
        patch("api.routes.system._probe_celery_worker", return_value=True),
        patch("api.routes.system._check_heartbeat_key", return_value=True),
    )


@pytest.fixture()
def client():
    """TestClient with real DB and mocked Redis."""
    p1, p2, p3, p4, p5, p6 = _make_client()
    with p1 as mock_init, p2, p3, p4, p5, p6:
        mock_pub = MagicMock()
        mock_pub.enabled = True
        mock_init.return_value = mock_pub

        app = create_app()
        with TestClient(app) as tc:
            yield tc


@pytest.fixture()
def log_file(tmp_path):
    """Create a temporary log file with sample structured log lines."""
    lines = [
        '{"level": "info", "logger": "core", "timestamp": "2026-02-01T00:00:00Z", "msg": "startup"}\n',
        '{"level": "info", "logger": "core", "timestamp": "2026-02-01T00:01:00Z", "msg": "candle received"}\n',
        '{"level": "warning", "logger": "risk", "timestamp": "2026-02-01T00:02:00Z", "msg": "drawdown high"}\n',
        '{"level": "error", "logger": "execution", "timestamp": "2026-02-01T00:03:00Z", "msg": "order rejected"}\n',
        '{"level": "info", "logger": "core", "timestamp": "2026-02-01T00:04:00Z", "msg": "tick complete"}\n',
        '{"level": "info", "logger": "core", "timestamp": "2026-02-01T00:05:00Z", "msg": "signal generated"}\n',
        '{"level": "warning", "logger": "risk", "timestamp": "2026-02-01T00:06:00Z", "msg": "stop triggered"}\n',
        '{"level": "info", "logger": "core", "timestamp": "2026-02-01T00:07:00Z", "msg": "trade closed"}\n',
        '{"level": "error", "logger": "db", "timestamp": "2026-02-01T00:08:00Z", "msg": "connection lost"}\n',
        '{"level": "info", "logger": "core", "timestamp": "2026-02-01T00:09:00Z", "msg": "reconnected"}\n',
    ]
    log_path = tmp_path / "test.log"
    log_path.write_text("".join(lines))
    return str(log_path)


@pytest.fixture()
def client_with_logs(log_file):
    """TestClient with a patched log file path."""
    p1, p2, p3, p4, p5, p6 = _make_client()
    with p1 as mock_init, p2, p3, p4, p5, p6:
        mock_pub = MagicMock()
        mock_pub.enabled = True
        mock_init.return_value = mock_pub

        app = create_app()
        with (
            patch("api.routes.system._get_log_path", return_value=log_file),
            TestClient(app) as tc,
        ):
            yield tc


@pytest.fixture()
def client_no_logs():
    """TestClient where log file does not exist."""
    p1, p2, p3, p4, p5, p6 = _make_client()
    with p1 as mock_init, p2, p3, p4, p5, p6:
        mock_pub = MagicMock()
        mock_pub.enabled = True
        mock_init.return_value = mock_pub

        app = create_app()
        with (
            patch("api.routes.system._get_log_path", return_value="/nonexistent/path/trading.log"),
            TestClient(app) as tc,
        ):
            yield tc


# ── GET /api/system/health ───────────────────────────────────────────────────


class TestGetHealth:
    """System health endpoint."""

    def test_returns_200(self, client):
        resp = client.get("/api/system/health")
        assert resp.status_code == 200

    def test_response_shape(self, client):
        data = client.get("/api/system/health").json()
        expected_keys = {"status", "db", "redis", "celery_worker", "celery_beat", "environment", "timestamp"}
        assert expected_keys == set(data.keys())

    def test_status_ok(self, client):
        data = client.get("/api/system/health").json()
        assert data["status"] == "ok"

    def test_db_true_when_connected(self, client):
        data = client.get("/api/system/health").json()
        assert data["db"] is True

    def test_redis_true_when_connected(self, client):
        data = client.get("/api/system/health").json()
        assert data["redis"] is True

    def test_environment_field(self, client):
        data = client.get("/api/system/health").json()
        assert isinstance(data["environment"], str)
        assert len(data["environment"]) > 0

    def test_timestamp_field(self, client):
        data = client.get("/api/system/health").json()
        assert isinstance(data["timestamp"], str)
        # Should be ISO 8601 parseable
        assert "T" in data["timestamp"]

    def test_redis_false_when_no_client(self):
        """Redis reports False when async client is None."""
        mock_async_redis = AsyncMock()
        mock_async_redis.ping = AsyncMock(return_value=True)
        mock_async_redis.close = AsyncMock()

        with (
            patch("api.main.init_publisher") as mock_init,
            patch("api.main.reset_publisher"),
            patch("api.main.check_connection", return_value=True),
            # Make redis.asyncio.from_url raise so app.state.redis is None
            patch("redis.asyncio.from_url", side_effect=ConnectionError("refused")),
            patch("api.routes.system._probe_celery_worker", return_value=True),
            patch("api.routes.system._check_heartbeat_key", return_value=True),
        ):
            mock_pub = MagicMock()
            mock_pub.enabled = False
            mock_init.return_value = mock_pub

            app = create_app()
            with TestClient(app) as tc:
                data = tc.get("/api/system/health").json()
                assert data["redis"] is False


# ── GET /api/system/logs ─────────────────────────────────────────────────────


class TestGetLogs:
    """System logs endpoint."""

    def test_returns_200(self, client_with_logs):
        resp = client_with_logs.get("/api/system/logs")
        assert resp.status_code == 200

    def test_response_shape(self, client_with_logs):
        data = client_with_logs.get("/api/system/logs").json()
        expected_keys = {"file", "total_count", "offset", "limit", "lines"}
        assert expected_keys == set(data.keys())

    def test_default_returns_all_lines(self, client_with_logs):
        """Default limit (100) exceeds file size, so all 10 lines returned."""
        data = client_with_logs.get("/api/system/logs").json()
        assert data["total_count"] == 10
        assert len(data["lines"]) == 10

    def test_lines_param(self, client_with_logs):
        data = client_with_logs.get("/api/system/logs", params={"limit": 3}).json()
        assert data["total_count"] == 10
        assert len(data["lines"]) == 3

    def test_lines_returns_tail(self, client_with_logs):
        """Limited results should be from the end of the file."""
        data = client_with_logs.get("/api/system/logs", params={"limit": 1}).json()
        assert "reconnected" in data["lines"][0]

    def test_lines_bounds_min(self, client_with_logs):
        resp = client_with_logs.get("/api/system/logs", params={"limit": 0})
        assert resp.status_code == 422

    def test_lines_bounds_max(self, client_with_logs):
        resp = client_with_logs.get("/api/system/logs", params={"limit": 5001})
        assert resp.status_code == 422

    def test_level_filter_info(self, client_with_logs):
        data = client_with_logs.get(
            "/api/system/logs", params={"level": "info"}
        ).json()
        assert data["total_count"] == 6
        assert all("info" in line.lower() for line in data["lines"])

    def test_level_filter_error(self, client_with_logs):
        data = client_with_logs.get(
            "/api/system/logs", params={"level": "error"}
        ).json()
        assert data["total_count"] == 2

    def test_level_filter_warning(self, client_with_logs):
        data = client_with_logs.get(
            "/api/system/logs", params={"level": "warning"}
        ).json()
        assert data["total_count"] == 2

    def test_404_when_no_log_file(self, client_no_logs):
        resp = client_no_logs.get("/api/system/logs")
        assert resp.status_code == 404
        assert "not found" in resp.json()["detail"].lower()

    def test_empty_log_file(self, tmp_path):
        """Empty log file returns zero lines."""
        empty_log = tmp_path / "empty.log"
        empty_log.write_text("")

        p1, p2, p3, p4, p5, p6 = _make_client()
        with p1 as mock_init, p2, p3, p4, p5, p6:
            mock_pub = MagicMock()
            mock_pub.enabled = True
            mock_init.return_value = mock_pub

            app = create_app()
            with (
                patch("api.routes.system._get_log_path", return_value=str(empty_log)),
                TestClient(app) as tc,
            ):
                data = tc.get("/api/system/logs").json()
                assert data["total_count"] == 0
                assert data["lines"] == []

    def test_file_path_in_response(self, client_with_logs, log_file):
        data = client_with_logs.get("/api/system/logs").json()
        assert data["file"] == log_file
