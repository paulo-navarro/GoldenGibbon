"""
Tests for the run_strategy_tick Celery task (kanban 3.5).

All tests run in eager mode (no broker/worker needed).
DataLoader, BinanceClient, and EventPublisher are mocked – no real network
calls and no Redis dependency for event assertions.
A real Postgres connection is used (via the ``clean_database`` autouse
fixture) but the task itself only reads from the candle cache, so it
doesn't require pre-seeded data when the loader mock returns a
MarketData directly.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Dict
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

from core.config import SymbolConfig
from core.models import (
    MarketData,
    Portfolio,
    RiskAction,
    RiskDecision,
    Signal,
    StopCheckResult,
    StrategyState,
)

# Fixed test symbols – isolates tests from config/symbols.yaml
_TEST_SYMBOLS = [
    SymbolConfig(symbol="BTCUSDT", timeframes=["15m", "1h"]),
    SymbolConfig(symbol="ETHUSDT", timeframes=["15m", "1h"]),
]


# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_market_data(
    symbol: str = "BTCUSDT",
    timeframe: str = "15m",
    rows: int = 50,
    base_price: float = 50000.0,
) -> MarketData:
    """Build a synthetic MarketData with candles + placeholder indicators."""
    np.random.seed(42)
    dates = pd.date_range(
        start="2025-03-01", periods=rows, freq="15min", tz="UTC"
    )
    close = base_price + np.cumsum(np.random.randn(rows) * 10)
    df = pd.DataFrame(
        {
            "open": close - 5,
            "high": close + 10,
            "low": close - 10,
            "close": close,
            "volume": np.abs(np.random.randn(rows) * 1000 + 5000),
        },
        index=dates,
    )
    return MarketData(
        symbol=symbol,
        timeframe=timeframe,
        candles=df,
        indicators={},
    )


def _empty_market_data(symbol: str = "BTCUSDT") -> MarketData:
    """MarketData with an empty candle DataFrame."""
    df = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    return MarketData(
        symbol=symbol,
        timeframe="15m",
        candles=df,
        indicators={},
    )


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _eager_celery():
    """Run Celery tasks synchronously in-process for every test."""
    from core.celery_app import app

    app.conf.task_always_eager = True
    app.conf.task_eager_propagates = True
    yield
    app.conf.task_always_eager = False
    app.conf.task_eager_propagates = False


@pytest.fixture(autouse=True)
def _reset_worker_state():
    """Clear module-level worker state between tests."""
    import core.tasks as tasks_mod

    tasks_mod._worker_state.clear()
    tasks_mod._STRATEGY_REGISTRY.clear()
    yield
    tasks_mod._worker_state.clear()
    tasks_mod._STRATEGY_REGISTRY.clear()


@pytest.fixture(autouse=True)
def _enable_paper_trading():
    """Enable paper trading so mode gate doesn't skip the tick."""
    from core.config import get_settings

    settings = get_settings()
    original = settings.paper_trading.enabled
    settings.paper_trading.enabled = True
    yield
    settings.paper_trading.enabled = original


@pytest.fixture(autouse=True)
def _pin_symbols():
    """Pin enabled_symbols to 2 test symbols so tests don't depend on config."""
    from core.config import get_settings

    settings = get_settings()
    original = settings.symbols
    settings.symbols = list(_TEST_SYMBOLS)
    yield
    settings.symbols = original


# ── Patch targets ────────────────────────────────────────────────────────────

_PATCH_LOADER = "core.data.loader.DataLoader.get_multi_timeframe_market_data"
_PATCH_PUBLISH = "core.events.EventPublisher.publish"
_PATCH_KLINES = "core.data.binance_client.BinanceClient.fetch_klines"


# ── Happy path ───────────────────────────────────────────────────────────────


class TestRunStrategyTickHappyPath:
    """run_strategy_tick succeeds for all enabled strategy/symbol pairs."""

    @patch(_PATCH_PUBLISH)
    @patch(_PATCH_KLINES, return_value=[])
    @patch(_PATCH_LOADER)
    def test_returns_correct_summary_shape(
        self, mock_loader, mock_klines, mock_publish
    ):
        """Summary dict has ticks_processed, ticks_failed, signals keys."""
        mock_loader.return_value = _make_market_data()

        from core.tasks import run_strategy_tick

        result = run_strategy_tick.apply()
        assert result.successful()
        summary = result.result

        assert "ticks_processed" in summary
        assert "ticks_failed" in summary
        assert "signals" in summary

    @patch(_PATCH_PUBLISH)
    @patch(_PATCH_KLINES, return_value=[])
    @patch(_PATCH_LOADER)
    def test_ticks_processed_matches_enabled_pairs(
        self, mock_loader, mock_klines, mock_publish
    ):
        """Should process one tick per (enabled_strategy, enabled_symbol)."""
        mock_loader.return_value = _make_market_data()

        from core.tasks import run_strategy_tick

        result = run_strategy_tick.apply()
        summary = result.result

        # 2 strategies × 2 test symbols = 4
        assert summary["ticks_processed"] == 4
        assert summary["ticks_failed"] == 0

    @patch(_PATCH_PUBLISH)
    @patch(_PATCH_KLINES, return_value=[])
    @patch(_PATCH_LOADER)
    def test_signals_keyed_by_strategy_symbol(
        self, mock_loader, mock_klines, mock_publish
    ):
        """The signals dict keys are 'strategy_name:SYMBOL'."""
        mock_loader.return_value = _make_market_data()

        from core.tasks import run_strategy_tick

        result = run_strategy_tick.apply()
        signals = result.result["signals"]

        assert "smart_hodler:BTCUSDT" in signals
        assert "smart_hodler:ETHUSDT" in signals
        assert "mean_reversion:BTCUSDT" in signals
        assert "mean_reversion:ETHUSDT" in signals

    @patch(_PATCH_PUBLISH)
    @patch(_PATCH_KLINES, return_value=[])
    @patch(_PATCH_LOADER)
    def test_signal_values_are_valid(self, mock_loader, mock_klines, mock_publish):
        """Every signal value is one of 'buy', 'sell_full', 'sell_half', 'hold'."""
        mock_loader.return_value = _make_market_data()

        from core.tasks import run_strategy_tick

        result = run_strategy_tick.apply()
        signals = result.result["signals"]
        valid = {s.value for s in Signal}

        for sig in signals.values():
            assert sig in valid


# ── Worker state caching ─────────────────────────────────────────────────────


class TestWorkerStateCaching:
    """Components are created once and reused across ticks."""

    @patch(_PATCH_PUBLISH)
    @patch(_PATCH_KLINES, return_value=[])
    @patch(_PATCH_LOADER)
    def test_components_reused_on_second_tick(
        self, mock_loader, mock_klines, mock_publish
    ):
        """Worker state caches strategy/PM/risk/executor between ticks."""
        mock_loader.return_value = _make_market_data()

        from core.tasks import run_strategy_tick
        import core.tasks as tasks_mod

        run_strategy_tick.apply()
        state_after_first = dict(tasks_mod._worker_state)

        run_strategy_tick.apply()
        state_after_second = dict(tasks_mod._worker_state)

        # Same keys, same objects
        assert state_after_first.keys() == state_after_second.keys()
        for key in state_after_first:
            assert state_after_first[key] is state_after_second[key]

    @patch(_PATCH_PUBLISH)
    @patch(_PATCH_KLINES, return_value=[])
    @patch(_PATCH_LOADER)
    def test_worker_state_keys_match_pairs(
        self, mock_loader, mock_klines, mock_publish
    ):
        """Worker state is keyed by (strategy_name, symbol) tuples."""
        mock_loader.return_value = _make_market_data()

        from core.tasks import run_strategy_tick
        import core.tasks as tasks_mod

        run_strategy_tick.apply()

        expected_keys = {
            ("smart_hodler", "BTCUSDT"),
            ("smart_hodler", "ETHUSDT"),
            ("mean_reversion", "BTCUSDT"),
            ("mean_reversion", "ETHUSDT"),
        }
        assert set(tasks_mod._worker_state.keys()) == expected_keys


# ── Empty / missing data ────────────────────────────────────────────────────


class TestTickWithEmptyData:
    """run_strategy_tick handles empty candle cache gracefully."""

    @patch(_PATCH_PUBLISH)
    @patch(_PATCH_KLINES, return_value=[])
    @patch(_PATCH_LOADER)
    def test_empty_candles_counted_as_failed(
        self, mock_loader, mock_klines, mock_publish
    ):
        """When candles are empty, tick is counted as failed."""
        mock_loader.return_value = _empty_market_data()

        from core.tasks import run_strategy_tick

        result = run_strategy_tick.apply()
        summary = result.result

        assert summary["ticks_processed"] == 0
        assert summary["ticks_failed"] == 4  # all empty


# ── Error isolation ──────────────────────────────────────────────────────────


class TestTickErrorIsolation:
    """One failing strategy/symbol pair doesn't block others."""

    @patch(_PATCH_PUBLISH)
    @patch(_PATCH_KLINES, return_value=[])
    @patch(_PATCH_LOADER)
    def test_one_exception_does_not_block_others(
        self, mock_loader, mock_klines, mock_publish
    ):
        """If one pair raises, the rest still process."""
        call_count = {"n": 0}

        def _side_effect(*args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise RuntimeError("Simulated data error")
            return _make_market_data()

        mock_loader.side_effect = _side_effect

        from core.tasks import run_strategy_tick

        result = run_strategy_tick.apply()
        summary = result.result

        assert summary["ticks_failed"] == 1
        assert summary["ticks_processed"] == 3

    @patch(_PATCH_PUBLISH)
    @patch(_PATCH_KLINES, return_value=[])
    @patch(_PATCH_LOADER)
    def test_error_published_as_system_error_event(
        self, mock_loader, mock_klines, mock_publish
    ):
        """Failures publish an ERROR event on the SYSTEM channel."""
        from core.events import EventChannel, EventType

        mock_loader.side_effect = RuntimeError("boom")

        from core.tasks import run_strategy_tick

        run_strategy_tick.apply()

        error_calls = [
            c
            for c in mock_publish.call_args_list
            if len(c[0]) >= 2 and c[0][1] == EventType.ERROR
        ]
        # All 4 pairs should have failed → 4 error events
        assert len(error_calls) == 4
        for c in error_calls:
            data = c[0][2]
            assert data["task"] == "run_strategy_tick"
            assert "error" in data


# ── Beat schedule ────────────────────────────────────────────────────────────


class TestBeatScheduleIncludesTick:
    """Celery Beat schedule has the strategy tick entry."""

    def test_beat_schedule_has_strategy_tick(self):
        """run-strategy-tick-15m is in beat_schedule."""
        from core.celery_app import app

        assert "run-strategy-tick-15m" in app.conf.beat_schedule

    def test_tick_schedule_offset_after_fetch(self):
        """Tick runs at :02,:17,:32,:47 — 1 min after fetch (:01,:16,:31,:46)."""
        from core.celery_app import app

        entry = app.conf.beat_schedule["run-strategy-tick-15m"]
        cron = entry["schedule"]
        assert cron.minute == {2, 17, 32, 47}

    def test_tick_schedule_targets_correct_task(self):
        """Beat schedule points at core.tasks.run_strategy_tick."""
        from core.celery_app import app

        entry = app.conf.beat_schedule["run-strategy-tick-15m"]
        assert entry["task"] == "core.tasks.run_strategy_tick"


# ── Strategy registry ────────────────────────────────────────────────────────


class TestStrategyRegistry:
    """Lazy strategy registry is populated correctly."""

    def test_registry_contains_smart_hodler(self):
        from core.tasks import _get_strategy_registry
        from core.strategies import SmartHodler

        registry = _get_strategy_registry()
        assert registry["smart_hodler"] is SmartHodler

    def test_registry_contains_mean_reversion(self):
        from core.tasks import _get_strategy_registry
        from core.strategies import MeanReversion

        registry = _get_strategy_registry()
        assert registry["mean_reversion"] is MeanReversion
