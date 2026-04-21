"""
Tests for the strategy tick Celery tasks (kanban 3.5 + 5.2).

Tests cover both the per-pair ``run_single_strategy_tick`` task and the
``run_strategy_tick`` dispatcher that fans out work via ``celery.group()``.

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

# Fixed test symbols – isolates tests from runtime config
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
    from core.strategies.registry import reset_registry
    reset_registry()
    yield
    tasks_mod._worker_state.clear()
    reset_registry()


@pytest.fixture(autouse=True)
def _enable_paper_trading():
    """Enable paper trading and disable live trading so tests use paper mode."""
    from core.config import get_settings

    settings = get_settings()
    original_paper = settings.paper_trading.enabled
    original_live = settings.live_trading.enabled
    settings.paper_trading.enabled = True
    settings.live_trading.enabled = False
    yield
    settings.paper_trading.enabled = original_paper
    settings.live_trading.enabled = original_live


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


# ── Single tick happy path ──────────────────────────────────────────────────


class TestRunSingleStrategyTick:
    """run_single_strategy_tick processes a single (strategy, symbol) pair."""

    @patch(_PATCH_PUBLISH)
    @patch(_PATCH_KLINES, return_value=[])
    @patch(_PATCH_LOADER)
    def test_returns_strategy_symbol_signal(
        self, mock_loader, mock_klines, mock_publish
    ):
        """Result dict contains strategy, symbol, and signal."""
        mock_loader.return_value = _make_market_data()

        from core.tasks import run_single_strategy_tick

        result = run_single_strategy_tick.apply(args=["smart_hodler", "BTCUSDT"])
        assert result.successful()
        summary = result.result

        assert summary["strategy"] == "smart_hodler"
        assert summary["symbol"] == "BTCUSDT"
        assert "signal" in summary

    @patch(_PATCH_PUBLISH)
    @patch(_PATCH_KLINES, return_value=[])
    @patch(_PATCH_LOADER)
    def test_signal_value_is_valid(self, mock_loader, mock_klines, mock_publish):
        """Signal value is one of 'buy', 'sell_full', 'sell_half', 'hold'."""
        mock_loader.return_value = _make_market_data()

        from core.tasks import run_single_strategy_tick

        result = run_single_strategy_tick.apply(args=["smart_hodler", "BTCUSDT"])
        valid = {s.value for s in Signal}
        assert result.result["signal"] in valid

    @patch(_PATCH_PUBLISH)
    @patch(_PATCH_KLINES, return_value=[])
    @patch(_PATCH_LOADER)
    def test_mean_reversion_tick(self, mock_loader, mock_klines, mock_publish):
        """Mean reversion strategy also processes correctly."""
        mock_loader.return_value = _make_market_data()

        from core.tasks import run_single_strategy_tick

        result = run_single_strategy_tick.apply(args=["mean_reversion", "ETHUSDT"])
        assert result.successful()
        assert result.result["strategy"] == "mean_reversion"
        assert result.result["symbol"] == "ETHUSDT"


# ── Dispatcher (task 5.2) ──────────────────────────────────────────────────


class TestRunStrategyTickDispatcher:
    """run_strategy_tick fans out to run_single_strategy_tick via group()."""

    @patch(_PATCH_PUBLISH)
    @patch(_PATCH_KLINES, return_value=[])
    @patch(_PATCH_LOADER)
    def test_returns_dispatcher_shape(
        self, mock_loader, mock_klines, mock_publish
    ):
        """Dispatcher returns dispatched count, pairs list, and group_id."""
        mock_loader.return_value = _make_market_data()

        from core.tasks import run_strategy_tick

        result = run_strategy_tick.apply()
        assert result.successful()
        summary = result.result

        assert "dispatched" in summary
        assert "pairs" in summary
        assert "group_id" in summary

    @patch(_PATCH_PUBLISH)
    @patch(_PATCH_KLINES, return_value=[])
    @patch(_PATCH_LOADER)
    def test_dispatched_matches_enabled_pairs(
        self, mock_loader, mock_klines, mock_publish
    ):
        """Should dispatch one sub-task per (enabled_strategy, enabled_symbol)."""
        mock_loader.return_value = _make_market_data()

        from core.tasks import run_strategy_tick

        result = run_strategy_tick.apply()
        summary = result.result

        # 2 strategies × 2 test symbols = 4
        assert summary["dispatched"] == 4

    @patch(_PATCH_PUBLISH)
    @patch(_PATCH_KLINES, return_value=[])
    @patch(_PATCH_LOADER)
    def test_pairs_list_contains_all_combos(
        self, mock_loader, mock_klines, mock_publish
    ):
        """The pairs list contains all strategy:symbol combinations."""
        mock_loader.return_value = _make_market_data()

        from core.tasks import run_strategy_tick

        result = run_strategy_tick.apply()
        pairs = result.result["pairs"]

        assert "smart_hodler:BTCUSDT" in pairs
        assert "smart_hodler:ETHUSDT" in pairs
        assert "mean_reversion:BTCUSDT" in pairs
        assert "mean_reversion:ETHUSDT" in pairs


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

        from core.tasks import run_single_strategy_tick
        import core.tasks as tasks_mod

        run_single_strategy_tick.apply(args=["smart_hodler", "BTCUSDT"])
        state_after_first = dict(tasks_mod._worker_state)

        run_single_strategy_tick.apply(args=["smart_hodler", "BTCUSDT"])
        state_after_second = dict(tasks_mod._worker_state)

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
    """run_single_strategy_tick handles empty candle cache gracefully."""

    @patch(_PATCH_PUBLISH)
    @patch(_PATCH_KLINES, return_value=[])
    @patch(_PATCH_LOADER)
    def test_empty_candles_returns_error(
        self, mock_loader, mock_klines, mock_publish
    ):
        """When candles are empty, result contains error key."""
        mock_loader.return_value = _empty_market_data()

        from core.tasks import run_single_strategy_tick

        result = run_single_strategy_tick.apply(args=["smart_hodler", "BTCUSDT"])
        summary = result.result

        assert summary["signal"] is None
        assert summary["error"] == "no_candles"


# ── Error isolation ──────────────────────────────────────────────────────────


class TestTickErrorIsolation:
    """One failing strategy/symbol pair doesn't block others."""

    @patch(_PATCH_PUBLISH)
    @patch(_PATCH_KLINES, return_value=[])
    @patch(_PATCH_LOADER)
    def test_one_exception_does_not_block_others(
        self, mock_loader, mock_klines, mock_publish
    ):
        """If one pair raises, the rest still process (via dispatcher)."""
        call_count = {"n": 0}

        def _side_effect(*args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise RuntimeError("Simulated data error")
            return _make_market_data()

        mock_loader.side_effect = _side_effect

        from core.tasks import run_strategy_tick
        import core.tasks as tasks_mod

        result = run_strategy_tick.apply()
        summary = result.result

        # Dispatcher still reports all 4 dispatched
        assert summary["dispatched"] == 4

        # 3 of 4 worker state entries should exist (1 failed before creating)
        # The failed pair still gets a worker state entry since the error
        # happens after component creation — check that we have all 4
        # (error is caught inside the task, not raised)
        assert len(tasks_mod._worker_state) >= 3

    @patch(_PATCH_PUBLISH)
    @patch(_PATCH_KLINES, return_value=[])
    @patch(_PATCH_LOADER)
    def test_error_published_as_system_error_event(
        self, mock_loader, mock_klines, mock_publish
    ):
        """Failures publish an ERROR event on the SYSTEM channel."""
        from core.events import EventChannel, EventType

        mock_loader.side_effect = RuntimeError("boom")

        from core.tasks import run_single_strategy_tick

        run_single_strategy_tick.apply(args=["smart_hodler", "BTCUSDT"])

        error_calls = [
            c
            for c in mock_publish.call_args_list
            if len(c[0]) >= 2 and c[0][1] == EventType.ERROR
        ]
        assert len(error_calls) >= 1
        data = error_calls[0][0][2]
        assert data["task"] == "run_single_strategy_tick"
        assert "error" in data

    @patch(_PATCH_PUBLISH)
    @patch(_PATCH_KLINES, return_value=[])
    @patch(_PATCH_LOADER)
    def test_error_returns_error_in_result(
        self, mock_loader, mock_klines, mock_publish
    ):
        """A failing tick returns an error key instead of raising."""
        mock_loader.side_effect = RuntimeError("boom")

        from core.tasks import run_single_strategy_tick

        result = run_single_strategy_tick.apply(args=["smart_hodler", "BTCUSDT"])
        summary = result.result

        assert summary["signal"] is None
        assert "error" in summary
        assert "boom" in summary["error"]


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


# ── Trading mode switch ────────────────────────────────────────────────────


_PATCH_BINANCE_EXECUTOR = "core.execution.binance.BinanceExecutor.from_settings"
_PATCH_GET_SETTINGS = "core.config.get_settings"


class TestTradingModeSwitch:
    """
    When live_trading.enabled is toggled on, workers must rebuild their
    cached components with a BinanceExecutor instead of reusing the
    stale PaperExecutor from the previous paper-trading session.
    """

    @patch(_PATCH_PUBLISH)
    @patch(_PATCH_KLINES, return_value=[])
    @patch(_PATCH_LOADER)
    def test_settings_reloaded_on_every_tick(self, mock_loader, mock_klines, mock_publish):
        """get_settings is always called with reload=True so workers see latest config."""
        mock_loader.return_value = _make_market_data()

        from core.config import get_settings

        # Build a controlled paper-mode settings object and patch get_settings
        # so that reload=True inside the task returns it (no real DB query).
        paper_settings = get_settings()
        paper_settings.paper_trading.enabled = True
        paper_settings.live_trading.enabled = False

        with patch(_PATCH_GET_SETTINGS, return_value=paper_settings) as spy:
            from core.tasks import run_single_strategy_tick
            run_single_strategy_tick.apply(args=["smart_hodler", "BTCUSDT"])

        reload_calls = [
            c for c in spy.call_args_list
            if c.kwargs.get("reload") is True or (c.args and c.args[0] is True)
        ]
        assert len(reload_calls) >= 1, "get_settings must be called with reload=True during tick"

    @patch(_PATCH_PUBLISH)
    @patch(_PATCH_KLINES, return_value=[])
    @patch(_PATCH_LOADER)
    def test_mode_switch_paper_to_live_evicts_worker_state(self, mock_loader, mock_klines, mock_publish):
        """
        After a paper tick has cached _TickComponents, switching to live mode
        on the next tick must evict the stale entry and rebuild with
        BinanceExecutor (mocked here to avoid real credentials).
        """
        from decimal import Decimal
        from core.execution.paper import PaperExecutor
        from core.tasks import _worker_state, run_single_strategy_tick
        from core.config import get_settings
        from unittest.mock import MagicMock

        # Shared settings object — we mutate mode flags between steps.
        settings = get_settings()

        # ── 1. Paper tick ────────────────────────────────────────────────
        settings.paper_trading.enabled = True
        settings.live_trading.enabled = False

        with patch(_PATCH_GET_SETTINGS, return_value=settings):
            mock_loader.return_value = _make_market_data()
            run_single_strategy_tick.apply(args=["smart_hodler", "BTCUSDT"])

        key = ("smart_hodler", "BTCUSDT")
        assert key in _worker_state
        assert _worker_state[key].trading_mode == "paper"
        assert isinstance(_worker_state[key].executor, PaperExecutor)

        # ── 2. Switch to live mode (mutate same settings object) ─────────────
        settings.paper_trading.enabled = False
        settings.live_trading.enabled = True
        settings.live_trading.api_key = "fake_key"
        settings.live_trading.api_secret = "fake_secret"

        mock_executor = MagicMock()
        mock_executor.load_exchange_filters = MagicMock()
        mock_executor.get_account_info.return_value = {
            "balances": {"USDT": {"free": Decimal("5000"), "locked": Decimal("0")}},
            "can_trade": True,
        }

        with patch(_PATCH_GET_SETTINGS, return_value=settings):
            with patch(_PATCH_BINANCE_EXECUTOR, return_value=mock_executor):
                mock_loader.return_value = _make_market_data()
                run_single_strategy_tick.apply(args=["smart_hodler", "BTCUSDT"])

        # ── 3. Assert stale entry was evicted and rebuilt as live ──────────
        assert key in _worker_state
        assert _worker_state[key].trading_mode == "live", (
            "stale paper entry was not evicted; worker kept using PaperExecutor"
        )
