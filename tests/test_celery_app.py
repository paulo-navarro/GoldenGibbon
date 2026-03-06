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
        assert "reconciliation-4h" in app.conf.beat_schedule

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

        entry = app.conf.beat_schedule["reconciliation-4h"]
        assert entry["task"] == "core.tasks.run_reconciliation"

        schedule = entry["schedule"]
        assert isinstance(schedule, crontab)
        assert schedule.minute == {5}
        assert schedule.hour == {0, 4, 8, 12, 16, 20}


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
                assert "ticks_processed" in summary
                assert "ticks_failed" in summary
                assert "signals" in summary
        finally:
            settings.paper_trading.enabled = original

    def test_run_reconciliation_returns_none(self):
        from core.tasks import run_reconciliation

        result = run_reconciliation.apply()
        assert result.result is None
        assert result.successful()
