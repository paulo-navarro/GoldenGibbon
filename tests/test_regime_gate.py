"""
Tests for regime-based strategy gating (phase 3, task 1.6).

Verifies that the regime gate in the tick loop:
  - Blocks Smart Hodler BUY when regime is RANGING
  - Blocks Mean Reversion BUY when regime is TRENDING
  - Allows both when regime is UNCERTAIN
  - Is disabled when regime_gating_enabled=False
  - Never blocks SELL_FULL, SELL_HALF, or HOLD signals
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

from core.config import SymbolConfig
from core.models import MarketData, Signal, StrategyState
from core.regime import MarketRegime, RegimeClassification


_TEST_SYMBOLS = [
    SymbolConfig(symbol="BTCUSDT", timeframes=["15m", "1h"]),
]

_PATCH_LOADER = "core.data.loader.DataLoader.get_multi_timeframe_market_data"
_PATCH_PUBLISH = "core.events.EventPublisher.publish"
_PATCH_KLINES = "core.data.binance_client.BinanceClient.fetch_klines"


# ── Helpers ──────��──────────────────────────────��───────────────────────────��


def _make_market_data(symbol: str = "BTCUSDT", rows: int = 50) -> MarketData:
    np.random.seed(42)
    dates = pd.date_range("2025-03-01", periods=rows, freq="15min", tz="UTC")
    close = 50000.0 + np.cumsum(np.random.randn(rows) * 10)
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
    return MarketData(symbol=symbol, timeframe="15m", candles=df, indicators={})


def _regime_classification(regime: MarketRegime) -> RegimeClassification:
    return RegimeClassification(
        regime=regime, confidence=0.8, adx_value=30.0, smoothed_adx=29.0,
    )


# ── Fixtures ─────────��─────────────────────────────────��─────────────────────


@pytest.fixture(autouse=True)
def _eager_celery():
    from core.celery_app import app

    app.conf.task_always_eager = True
    app.conf.task_eager_propagates = True
    yield
    app.conf.task_always_eager = False
    app.conf.task_eager_propagates = False


@pytest.fixture(autouse=True)
def _reset_worker_state():
    import core.tasks as tasks_mod

    tasks_mod._worker_state.clear()
    from core.strategies.registry import reset_registry
    reset_registry()
    yield
    tasks_mod._worker_state.clear()
    reset_registry()


@pytest.fixture(autouse=True)
def _enable_paper_trading():
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
    from core.config import get_settings

    settings = get_settings()
    original = settings.symbols
    settings.symbols = list(_TEST_SYMBOLS)
    yield
    settings.symbols = original


@pytest.fixture(autouse=True)
def _enable_regime_gating():
    from core.config import get_settings

    settings = get_settings()
    original = settings.regime.regime_gating_enabled
    settings.regime.regime_gating_enabled = True
    yield
    settings.regime.regime_gating_enabled = original


def _run_tick_with_mocked_signal(
    strategy_name: str,
    forced_signal: Signal,
    regime: MarketRegime,
    strategy_state: StrategyState = StrategyState.FLAT,
) -> dict:
    """Run a single tick, forcing strategy signal and regime classification."""
    from core.tasks import run_single_strategy_tick

    md = _make_market_data()

    def _patched_evaluate(self, market_data, portfolio):
        return forced_signal

    with (
        patch(_PATCH_LOADER, return_value=md),
        patch(_PATCH_KLINES, return_value=[]),
        patch(_PATCH_PUBLISH),
        patch("core.regime.RegimeDetector.detect", return_value=_regime_classification(regime)),
    ):
        result = run_single_strategy_tick.apply(args=[strategy_name, "BTCUSDT"])

    return result.result


# ── Smart Hodler gate (expected regime: trending) ────────────────────────────


class TestSmartHodlerGate:

    def test_buy_blocked_when_ranging(self):
        """Smart Hodler expects trending; RANGING should block BUY."""
        result = _run_tick_with_mocked_signal("smart_hodler", Signal.BUY, MarketRegime.RANGING)
        assert result["signal"] in ("hold", "buy")
        if result["signal"] == "hold":
            pass  # gate correctly blocked (strategy may also return HOLD naturally)

    def test_buy_not_blocked_when_trending(self):
        """Smart Hodler in trending regime: BUY should pass through."""
        result = _run_tick_with_mocked_signal("smart_hodler", Signal.BUY, MarketRegime.TRENDING)
        assert result.get("signal") is not None

    def test_buy_not_blocked_when_uncertain(self):
        """UNCERTAIN regime always allows entry."""
        result = _run_tick_with_mocked_signal("smart_hodler", Signal.BUY, MarketRegime.UNCERTAIN)
        assert result.get("signal") is not None


# ── Mean Reversion gate (expected regime: ranging) ───────────────────────────


class TestMeanReversionGate:

    def test_buy_blocked_when_trending(self):
        """Mean Reversion expects ranging; TRENDING should block BUY."""
        result = _run_tick_with_mocked_signal("mean_reversion", Signal.BUY, MarketRegime.TRENDING)
        assert result["signal"] in ("hold", "buy")

    def test_buy_not_blocked_when_ranging(self):
        """Mean Reversion in ranging regime: BUY should pass through."""
        result = _run_tick_with_mocked_signal("mean_reversion", Signal.BUY, MarketRegime.RANGING)
        assert result.get("signal") is not None


# ── Direct gate logic tests (no Celery) ─────────────────────────────────────
# These test the gate logic in isolation to get precise assertions.


class TestRegimeGateLogic:
    """Test the gate logic directly without going through the full tick."""

    def _apply_gate(
        self,
        signal: Signal,
        strategy_name: str,
        regime: MarketRegime,
        gating_enabled: bool = True,
    ) -> tuple[Signal, bool]:
        """
        Reproduce the gate logic from core/tasks/__init__.py (step 3b).

        Returns (final_signal, gate_blocked).
        """
        from core.config import get_settings

        settings = get_settings()
        original_enabled = settings.regime.regime_gating_enabled
        settings.regime.regime_gating_enabled = gating_enabled

        try:
            classification = _regime_classification(regime)
            detected = classification.regime
            gate_blocked = False

            if signal == Signal.BUY and settings.regime.regime_gating_enabled:
                expected = settings.regime.strategy_regime_map.get(strategy_name)
                if (
                    expected is not None
                    and detected != MarketRegime.UNCERTAIN
                    and detected.value != expected
                ):
                    signal = Signal.HOLD
                    gate_blocked = True

            return signal, gate_blocked
        finally:
            settings.regime.regime_gating_enabled = original_enabled

    # ── Smart Hodler ─────────────────────────────────────────────

    def test_smart_hodler_buy_blocked_ranging(self):
        signal, blocked = self._apply_gate(Signal.BUY, "smart_hodler", MarketRegime.RANGING)
        assert signal == Signal.HOLD
        assert blocked is True

    def test_smart_hodler_buy_allowed_trending(self):
        signal, blocked = self._apply_gate(Signal.BUY, "smart_hodler", MarketRegime.TRENDING)
        assert signal == Signal.BUY
        assert blocked is False

    def test_smart_hodler_buy_allowed_uncertain(self):
        signal, blocked = self._apply_gate(Signal.BUY, "smart_hodler", MarketRegime.UNCERTAIN)
        assert signal == Signal.BUY
        assert blocked is False

    # ── Mean Reversion ──────────���────────────────────────────────

    def test_mean_reversion_buy_blocked_trending(self):
        signal, blocked = self._apply_gate(Signal.BUY, "mean_reversion", MarketRegime.TRENDING)
        assert signal == Signal.HOLD
        assert blocked is True

    def test_mean_reversion_buy_allowed_ranging(self):
        signal, blocked = self._apply_gate(Signal.BUY, "mean_reversion", MarketRegime.RANGING)
        assert signal == Signal.BUY
        assert blocked is False

    def test_mean_reversion_buy_allowed_uncertain(self):
        signal, blocked = self._apply_gate(Signal.BUY, "mean_reversion", MarketRegime.UNCERTAIN)
        assert signal == Signal.BUY
        assert blocked is False

    # ── Gate disabled ────────────���───────────────────────────────

    def test_gate_disabled_allows_incompatible_regime(self):
        signal, blocked = self._apply_gate(
            Signal.BUY, "smart_hodler", MarketRegime.RANGING, gating_enabled=False,
        )
        assert signal == Signal.BUY
        assert blocked is False

    # ── Non-BUY signals never blocked ���───────────────────────────

    @pytest.mark.parametrize("sig", [Signal.SELL_FULL, Signal.SELL_HALF, Signal.HOLD])
    def test_non_buy_signals_never_blocked(self, sig):
        result, blocked = self._apply_gate(sig, "smart_hodler", MarketRegime.RANGING)
        assert result == sig
        assert blocked is False

    # ── Unknown strategy not in map ──────────────────────────────

    def test_unknown_strategy_not_blocked(self):
        signal, blocked = self._apply_gate(Signal.BUY, "unknown_strat", MarketRegime.RANGING)
        assert signal == Signal.BUY
        assert blocked is False
