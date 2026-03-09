"""
Tests for task 3.10 – monitoring: health checks for workers, beat, Redis.

Covers:
  - ``emit_heartbeat`` task publishes HEARTBEAT event + sets Redis key
  - ``_probe_celery_worker`` helper returns True/False based on ping
  - ``_check_heartbeat_key`` helper returns True/False based on Redis key
  - ``GET /health`` includes ``celery_worker`` and ``celery_beat`` fields
  - Beat schedule includes the heartbeat-60s entry
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from api.main import create_app


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _eager_celery():
    """Run Celery tasks synchronously in-process."""
    from core.celery_app import app

    app.conf.task_always_eager = True
    app.conf.task_eager_propagates = True
    yield
    app.conf.task_always_eager = False
    app.conf.task_eager_propagates = False


def _make_client(*, worker_ok: bool = True, beat_ok: bool = True):
    """Create a TestClient with mocked Redis + celery probes."""
    mock_async_redis = AsyncMock()
    mock_async_redis.ping = AsyncMock(return_value=True)
    mock_async_redis.close = AsyncMock()

    return (
        patch("api.main.init_publisher"),
        patch("api.main.reset_publisher"),
        patch("api.main.check_connection", return_value=True),
        patch("redis.asyncio.from_url", return_value=mock_async_redis),
        patch("api.routes.system._probe_celery_worker", return_value=worker_ok),
        patch("api.routes.system._check_heartbeat_key", return_value=beat_ok),
    )


@pytest.fixture()
def client():
    """TestClient with all probes returning True."""
    p1, p2, p3, p4, p5, p6 = _make_client(worker_ok=True, beat_ok=True)
    with p1 as mock_init, p2, p3, p4, p5, p6:
        mock_pub = MagicMock()
        mock_pub.enabled = True
        mock_init.return_value = mock_pub
        app = create_app()
        with TestClient(app) as tc:
            yield tc


# ── emit_heartbeat task ──────────────────────────────────────────────────────


class TestEmitHeartbeat:
    """Tests for the emit_heartbeat canary task."""

    def test_registered_in_celery(self):
        from core.celery_app import app
        import core.tasks  # noqa: F401

        assert "core.tasks.emit_heartbeat" in app.tasks

    def test_returns_hostname_and_timestamp(self):
        with (
            patch("core.events.get_publisher") as mock_gp,
            patch("redis.Redis.from_url") as mock_redis_cls,
        ):
            mock_pub = MagicMock()
            mock_gp.return_value = mock_pub
            mock_redis_cls.return_value = MagicMock()

            from core.tasks import emit_heartbeat

            result = emit_heartbeat.apply()
            assert result.successful()
            data = result.result
            assert "hostname" in data
            assert "timestamp" in data

    def test_publishes_heartbeat_event(self):
        from core.events import EventChannel, EventType

        with (
            patch("core.events.get_publisher") as mock_gp,
            patch("redis.Redis.from_url") as mock_redis_cls,
        ):
            mock_pub = MagicMock()
            mock_gp.return_value = mock_pub
            mock_redis_cls.return_value = MagicMock()

            from core.tasks import emit_heartbeat

            emit_heartbeat.apply()

            mock_pub.publish.assert_called_once()
            call_args = mock_pub.publish.call_args
            assert call_args[0][0] == EventChannel.SYSTEM
            assert call_args[0][1] == EventType.HEARTBEAT
            assert call_args[0][2]["source"] == "worker"

    def test_sets_redis_heartbeat_key(self):
        with (
            patch("core.events.get_publisher") as mock_gp,
            patch("redis.Redis.from_url") as mock_redis_cls,
        ):
            mock_pub = MagicMock()
            mock_gp.return_value = mock_pub
            mock_redis = MagicMock()
            mock_redis_cls.return_value = mock_redis

            from core.tasks import emit_heartbeat, _HEARTBEAT_KEY, _HEARTBEAT_TTL

            emit_heartbeat.apply()

            mock_redis.set.assert_called_once()
            args, kwargs = mock_redis.set.call_args
            assert args[0] == _HEARTBEAT_KEY
            assert kwargs["ex"] == _HEARTBEAT_TTL

    def test_redis_failure_does_not_raise(self):
        """If Redis key write fails, the task still succeeds."""
        with (
            patch("core.events.get_publisher") as mock_gp,
            patch("redis.Redis.from_url", side_effect=ConnectionError("refused")),
        ):
            mock_pub = MagicMock()
            mock_gp.return_value = mock_pub

            from core.tasks import emit_heartbeat

            result = emit_heartbeat.apply()
            assert result.successful()


# ── Probe helpers ────────────────────────────────────────────────────────────


class TestProbeCeleryWorker:
    """Tests for _probe_celery_worker."""

    def test_returns_true_when_workers_respond(self):
        from api.routes.system import _probe_celery_worker

        with patch("core.celery_app.app") as mock_app:
            mock_app.control.ping.return_value = [{"worker1": {"ok": "pong"}}]
            assert _probe_celery_worker() is True

    def test_returns_false_when_no_workers(self):
        from api.routes.system import _probe_celery_worker

        with patch("core.celery_app.app") as mock_app:
            mock_app.control.ping.return_value = []
            assert _probe_celery_worker() is False

    def test_returns_false_on_exception(self):
        from api.routes.system import _probe_celery_worker

        with patch("core.celery_app.app") as mock_app:
            mock_app.control.ping.side_effect = ConnectionError("refused")
            assert _probe_celery_worker() is False


class TestCheckHeartbeatKey:
    """Tests for _check_heartbeat_key."""

    def test_returns_true_when_key_exists(self):
        from api.routes.system import _check_heartbeat_key

        with patch("redis.Redis.from_url") as mock_cls:
            mock_r = MagicMock()
            mock_r.exists.return_value = 1
            mock_cls.return_value = mock_r
            assert _check_heartbeat_key() is True

    def test_returns_false_when_key_missing(self):
        from api.routes.system import _check_heartbeat_key

        with patch("redis.Redis.from_url") as mock_cls:
            mock_r = MagicMock()
            mock_r.exists.return_value = 0
            mock_cls.return_value = mock_r
            assert _check_heartbeat_key() is False

    def test_returns_false_on_connection_error(self):
        from api.routes.system import _check_heartbeat_key

        with patch("redis.Redis.from_url", side_effect=ConnectionError("refused")):
            assert _check_heartbeat_key() is False


# ── GET /health – extended fields ────────────────────────────────────────────


class TestHealthCeleryFields:
    """Verify /health includes celery_worker and celery_beat."""

    def test_response_includes_celery_fields(self, client):
        data = client.get("/api/system/health").json()
        assert "celery_worker" in data
        assert "celery_beat" in data

    def test_celery_worker_true(self, client):
        data = client.get("/api/system/health").json()
        assert data["celery_worker"] is True

    def test_celery_beat_true(self, client):
        data = client.get("/api/system/health").json()
        assert data["celery_beat"] is True

    def test_celery_worker_false_when_no_workers(self):
        p1, p2, p3, p4, p5, p6 = _make_client(worker_ok=False, beat_ok=True)
        with p1 as mock_init, p2, p3, p4, p5, p6:
            mock_pub = MagicMock()
            mock_pub.enabled = True
            mock_init.return_value = mock_pub
            app = create_app()
            with TestClient(app) as tc:
                data = tc.get("/api/system/health").json()
                assert data["celery_worker"] is False
                assert data["celery_beat"] is True

    def test_celery_beat_false_when_no_heartbeat(self):
        p1, p2, p3, p4, p5, p6 = _make_client(worker_ok=True, beat_ok=False)
        with p1 as mock_init, p2, p3, p4, p5, p6:
            mock_pub = MagicMock()
            mock_pub.enabled = True
            mock_init.return_value = mock_pub
            app = create_app()
            with TestClient(app) as tc:
                data = tc.get("/api/system/health").json()
                assert data["celery_worker"] is True
                assert data["celery_beat"] is False

    def test_both_false(self):
        p1, p2, p3, p4, p5, p6 = _make_client(worker_ok=False, beat_ok=False)
        with p1 as mock_init, p2, p3, p4, p5, p6:
            mock_pub = MagicMock()
            mock_pub.enabled = True
            mock_init.return_value = mock_pub
            app = create_app()
            with TestClient(app) as tc:
                data = tc.get("/api/system/health").json()
                assert data["celery_worker"] is False
                assert data["celery_beat"] is False

    def test_full_response_shape(self, client):
        data = client.get("/api/system/health").json()
        expected_keys = {
            "status", "db", "redis", "celery_worker", "celery_beat",
            "environment", "timestamp",
        }
        assert expected_keys == set(data.keys())


# ── Beat schedule ────────────────────────────────────────────────────────────


class TestHeartbeatBeatSchedule:
    """Verify Beat schedule includes the heartbeat entry."""

    def test_beat_schedule_has_heartbeat(self):
        from core.celery_app import app

        assert "heartbeat-60s" in app.conf.beat_schedule

    def test_heartbeat_targets_correct_task(self):
        from core.celery_app import app

        entry = app.conf.beat_schedule["heartbeat-60s"]
        assert entry["task"] == "core.tasks.emit_heartbeat"

    def test_heartbeat_interval_60s(self):
        from core.celery_app import app

        entry = app.conf.beat_schedule["heartbeat-60s"]
        assert entry["schedule"] == 60.0
