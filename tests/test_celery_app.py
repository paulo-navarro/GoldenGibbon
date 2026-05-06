"""
Tests for the Celery application configuration and task stubs.

All tests run **in-process** with ``task_always_eager=True`` so no
Redis or Celery worker is required.
"""

from __future__ import annotations

import pytest
from celery import Celery
from celery.schedules import crontab


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _eager_celery():
    """
    Switch the Celery app to eager mode for every test in this module.

    Eager mode executes tasks synchronously in-process, so we don't
    need a running broker or worker.
    """
    from core.celery_app import app

    app.conf.task_always_eager = True
    app.conf.task_eager_propagates = True
    yield
    app.conf.task_always_eager = False
    app.conf.task_eager_propagates = False


# ── App configuration ────────────────────────────────────────────────────────


class TestCeleryApp:
    """Verify core.celery_app exposes a correctly configured Celery instance."""

    def test_app_is_celery_instance(self):
        from core.celery_app import app

        assert isinstance(app, Celery)

    def test_app_name(self):
        from core.celery_app import app

        assert app.main == "golden_gibbon"

    def test_timezone_utc(self):
        from core.celery_app import app

        assert app.conf.timezone == "UTC"
        assert app.conf.enable_utc is True

    def test_serialisation_json(self):
        from core.celery_app import app

        assert app.conf.task_serializer == "json"
        assert app.conf.result_serializer == "json"
        assert "json" in app.conf.accept_content

    def test_reliability_settings(self):
        from core.celery_app import app

        assert app.conf.task_acks_late is True
        assert app.conf.task_track_started is True
        assert app.conf.worker_prefetch_multiplier == 1

    def test_worker_recycling(self):
        from core.celery_app import app

        assert app.conf.worker_max_tasks_per_child == 100


# ── Beat schedule ────────────────────────────────────────────────────────────


class TestBeatSchedule:
    """Verify the periodic schedule targets the correct tasks and crontabs."""

    def test_schedule_has_expected_entries(self):
        from core.celery_app import app

        assert "fetch-candles-15m" in app.conf.beat_schedule
        assert "reconciliation-2m" in app.conf.beat_schedule
        assert "heartbeat-60s" in app.conf.beat_schedule

    def test_fetch_candles_schedule(self):
        from core.celery_app import app

        entry = app.conf.beat_schedule["fetch-candles-15m"]
        assert entry["task"] == "core.tasks.fetch_candles"

        schedule = entry["schedule"]
        assert isinstance(schedule, crontab)
        # 1 minute past each 15-min boundary
        assert schedule.minute == {1, 16, 31, 46}

    def test_reconciliation_schedule(self):
        from core.celery_app import app

        entry = app.conf.beat_schedule["reconciliation-2m"]
        assert entry["task"] == "core.tasks.run_reconciliation"
        assert entry["schedule"] == 120.0


# ── Startup Recovery ─────────────────────────────────────────────────────────


class TestStartupRecovery:
    """Verify _reconcile_on_startup runs fill recovery synchronously."""

    def test_calls_fill_recovery_before_async_reconciliation(self):
        """Startup handler runs _recover_pending_orders synchronously."""
        from unittest.mock import MagicMock, patch

        from core.celery_app import _reconcile_on_startup

        with (
            patch("core.config.get_settings") as mock_get_settings,
            patch("core.tasks._reconciliation._recover_pending_orders") as mock_recover,
            patch("core.tasks._reconciliation._recover_pending_orders_without_id") as mock_lost,
            patch("core.execution.binance.BinanceExecutor.from_settings") as mock_exec,
            patch("core.tasks.run_reconciliation") as mock_task,
        ):
            mock_settings = MagicMock()
            mock_settings.live_trading.reconcile_on_startup = True
            mock_settings.live_trading.enabled = True
            mock_settings.reconciliation.recovery_age_seconds = 120
            mock_settings.reconciliation.lost_age_seconds = 300
            mock_get_settings.return_value = mock_settings

            mock_recover.return_value = {"status": "ok", "recovered": 0}
            mock_lost.return_value = {"status": "ok", "matched": 0, "lost": 0}

            _reconcile_on_startup()

            # Fill recovery called synchronously
            assert mock_recover.call_count == 1
            assert mock_lost.call_count == 1
            # Full reconciliation enqueued async
            mock_task.delay.assert_called_once()

    def test_skipped_when_disabled(self):
        """When reconcile_on_startup=False, nothing runs."""
        from unittest.mock import MagicMock, patch

        from core.celery_app import _reconcile_on_startup

        with (
            patch("core.config.get_settings") as mock_get_settings,
            patch("core.tasks.run_reconciliation") as mock_task,
        ):
            mock_settings = MagicMock()
            mock_settings.live_trading.reconcile_on_startup = False
            mock_get_settings.return_value = mock_settings

            _reconcile_on_startup()

            mock_task.delay.assert_not_called()


# ── Task stubs ───────────────────────────────────────────────────────────────


class TestTaskStubs:
    """Verify placeholder tasks are registered and callable."""

    def test_fetch_candles_registered(self):
        from core.celery_app import app
        import core.tasks  # noqa: F401 – force task registration

        assert "core.tasks.fetch_candles" in app.tasks

    def test_run_strategy_tick_registered(self):
        from core.celery_app import app
        import core.tasks  # noqa: F401 – force task registration

        assert "core.tasks.run_strategy_tick" in app.tasks

    def test_run_reconciliation_registered(self):
        from core.celery_app import app
        import core.tasks  # noqa: F401 – force task registration

        assert "core.tasks.run_reconciliation" in app.tasks

    def test_emit_heartbeat_registered(self):
        from core.celery_app import app
        import core.tasks  # noqa: F401 – force task registration

        assert "core.tasks.emit_heartbeat" in app.tasks

    def test_fetch_candles_returns_summary_dict(self):
        """fetch_candles now returns a summary dict (not None) – verify shape."""
        from unittest.mock import patch

        with patch("core.data.binance_client.BinanceClient.fetch_klines", return_value=[]):
            from core.tasks import fetch_candles

            result = fetch_candles.apply()
            assert result.successful()
            summary = result.result
            assert isinstance(summary, dict)
            assert "pairs_processed" in summary
            assert "pairs_failed" in summary
            assert "total_new_candles" in summary

    def test_run_strategy_tick_returns_summary_dict(self):
        """run_strategy_tick now returns a summary dict – verify shape."""
        from unittest.mock import patch, MagicMock
        import pandas as pd
        import numpy as np
        from core.models import MarketData
        from core.config import get_settings

        # Enable paper trading so mode gate doesn't skip
        settings = get_settings()
        original = settings.paper_trading.enabled
        settings.paper_trading.enabled = True

        # Create minimal MarketData so the tick pipeline can run
        dates = pd.date_range("2025-01-01", periods=250, freq="15min")
        close = 100 + np.cumsum(np.random.randn(250) * 0.5)
        df = pd.DataFrame({
            "open": close, "high": close + 1, "low": close - 1,
            "close": close, "volume": np.ones(250) * 1000,
        }, index=dates)
        indicators = {"ema_fast": pd.Series(close, index=dates), "ema_slow": pd.Series(close, index=dates)}
        md = MarketData(symbol="BTCUSDT", timeframe="15m", candles=df, indicators=indicators)

        try:
            with patch("core.data.loader.DataLoader.get_multi_timeframe_market_data", return_value=md):
                from core.tasks import run_strategy_tick, clear_worker_state
                clear_worker_state()

                result = run_strategy_tick.apply()
                assert result.successful()
                summary = result.result
                assert isinstance(summary, dict)
                assert "dispatched" in summary
                assert "pairs" in summary
                assert "group_id" in summary
        finally:
            settings.paper_trading.enabled = original

    def test_run_reconciliation_returns_summary_dict(self):
        """run_reconciliation returns a summary dict with check counts."""
        from core.config import get_settings
        from core.tasks import run_reconciliation

        settings = get_settings()
        original = settings.live_trading.enabled
        settings.live_trading.enabled = False

        try:
            result = run_reconciliation.apply()
            assert result.successful()
            summary = result.result
            assert isinstance(summary, dict)
            assert "pairs_checked" in summary
            assert "mismatches" in summary
            assert "repairs" in summary
            assert "details" in summary
            # Clean DB → no mismatches expected
            assert summary["mismatches"] == 0
        finally:
            settings.live_trading.enabled = original
