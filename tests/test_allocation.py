"""
Tests for the capital allocation model (tasks 5.4a + 5.4c).

Verifies weight resolution, normalisation, capital distribution,
and regime-based rebalancing across (strategy, symbol) pairs.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from core.allocation import compute_allocations, regime_adjusted_weights


class TestEqualSplit:
    """When no allocation_pct is set, strategies get equal shares."""

    def test_two_strategies_one_symbol(self):
        allocs = compute_allocations(
            total_capital=Decimal("10000"),
            enabled_strategies=["smart_hodler", "mean_reversion"],
            strategy_configs={"smart_hodler": {}, "mean_reversion": {}},
            symbols=["BTCUSDT"],
        )

        assert allocs[("smart_hodler", "BTCUSDT")] == Decimal("5000.00")
        assert allocs[("mean_reversion", "BTCUSDT")] == Decimal("5000.00")

    def test_two_strategies_two_symbols(self):
        allocs = compute_allocations(
            total_capital=Decimal("10000"),
            enabled_strategies=["smart_hodler", "mean_reversion"],
            strategy_configs={"smart_hodler": {}, "mean_reversion": {}},
            symbols=["BTCUSDT", "ETHUSDT"],
        )

        # Each strategy gets 5000, split across 2 symbols = 2500 each
        assert allocs[("smart_hodler", "BTCUSDT")] == Decimal("2500.00")
        assert allocs[("smart_hodler", "ETHUSDT")] == Decimal("2500.00")
        assert allocs[("mean_reversion", "BTCUSDT")] == Decimal("2500.00")
        assert allocs[("mean_reversion", "ETHUSDT")] == Decimal("2500.00")

    def test_single_strategy(self):
        allocs = compute_allocations(
            total_capital=Decimal("10000"),
            enabled_strategies=["smart_hodler"],
            strategy_configs={"smart_hodler": {}},
            symbols=["BTCUSDT"],
        )

        assert allocs[("smart_hodler", "BTCUSDT")] == Decimal("10000.00")


class TestExplicitWeights:
    """When allocation_pct is set, capital is distributed proportionally."""

    def test_60_40_split(self):
        allocs = compute_allocations(
            total_capital=Decimal("10000"),
            enabled_strategies=["smart_hodler", "mean_reversion"],
            strategy_configs={
                "smart_hodler": {"allocation_pct": 0.6},
                "mean_reversion": {"allocation_pct": 0.4},
            },
            symbols=["BTCUSDT"],
        )

        assert allocs[("smart_hodler", "BTCUSDT")] == Decimal("6000.00")
        assert allocs[("mean_reversion", "BTCUSDT")] == Decimal("4000.00")

    def test_70_30_two_symbols(self):
        allocs = compute_allocations(
            total_capital=Decimal("10000"),
            enabled_strategies=["smart_hodler", "mean_reversion"],
            strategy_configs={
                "smart_hodler": {"allocation_pct": 0.7},
                "mean_reversion": {"allocation_pct": 0.3},
            },
            symbols=["BTCUSDT", "ETHUSDT"],
        )

        assert allocs[("smart_hodler", "BTCUSDT")] == Decimal("3500.00")
        assert allocs[("smart_hodler", "ETHUSDT")] == Decimal("3500.00")
        assert allocs[("mean_reversion", "BTCUSDT")] == Decimal("1500.00")
        assert allocs[("mean_reversion", "ETHUSDT")] == Decimal("1500.00")


class TestPartialWeights:
    """When only some strategies have allocation_pct, the rest share the remainder."""

    def test_one_explicit_one_default(self):
        allocs = compute_allocations(
            total_capital=Decimal("10000"),
            enabled_strategies=["smart_hodler", "mean_reversion"],
            strategy_configs={
                "smart_hodler": {"allocation_pct": 0.8},
                "mean_reversion": {},
            },
            symbols=["BTCUSDT"],
        )

        # smart_hodler: 0.8 → after normalise: 0.8/1.0 = 0.8
        # mean_reversion: remaining 0.2 → 0.2/1.0 = 0.2
        assert allocs[("smart_hodler", "BTCUSDT")] == Decimal("8000.00")
        assert allocs[("mean_reversion", "BTCUSDT")] == Decimal("2000.00")


class TestNormalisation:
    """Weights that don't sum to 1.0 are normalised."""

    def test_weights_summing_to_2(self):
        allocs = compute_allocations(
            total_capital=Decimal("10000"),
            enabled_strategies=["a", "b"],
            strategy_configs={
                "a": {"allocation_pct": 1.0},
                "b": {"allocation_pct": 1.0},
            },
            symbols=["BTCUSDT"],
        )

        # Normalised to 0.5 each
        assert allocs[("a", "BTCUSDT")] == Decimal("5000.00")
        assert allocs[("b", "BTCUSDT")] == Decimal("5000.00")

    def test_weights_summing_to_half(self):
        allocs = compute_allocations(
            total_capital=Decimal("10000"),
            enabled_strategies=["a", "b"],
            strategy_configs={
                "a": {"allocation_pct": 0.25},
                "b": {"allocation_pct": 0.25},
            },
            symbols=["BTCUSDT"],
        )

        # Normalised to 0.5 each
        assert allocs[("a", "BTCUSDT")] == Decimal("5000.00")
        assert allocs[("b", "BTCUSDT")] == Decimal("5000.00")


class TestEdgeCases:
    """Edge cases: empty inputs, zero capital."""

    def test_empty_strategies(self):
        allocs = compute_allocations(
            total_capital=Decimal("10000"),
            enabled_strategies=[],
            strategy_configs={},
            symbols=["BTCUSDT"],
        )
        assert allocs == {}

    def test_empty_symbols(self):
        allocs = compute_allocations(
            total_capital=Decimal("10000"),
            enabled_strategies=["smart_hodler"],
            strategy_configs={"smart_hodler": {}},
            symbols=[],
        )
        assert allocs == {}

    def test_zero_capital(self):
        allocs = compute_allocations(
            total_capital=Decimal("0"),
            enabled_strategies=["smart_hodler"],
            strategy_configs={"smart_hodler": {}},
            symbols=["BTCUSDT"],
        )
        assert allocs[("smart_hodler", "BTCUSDT")] == Decimal("0.00")

    def test_three_strategies_three_symbols(self):
        allocs = compute_allocations(
            total_capital=Decimal("9000"),
            enabled_strategies=["a", "b", "c"],
            strategy_configs={"a": {}, "b": {}, "c": {}},
            symbols=["X", "Y", "Z"],
        )

        # 3 strategies × 3 symbols = 9 pairs, 1000 each
        assert len(allocs) == 9
        for key, val in allocs.items():
            assert val == Decimal("1000.00")


# ── Regime-adjusted weights (task 5.4c) ──────────────────────────────────────

_REGIME_MAP = {"smart_hodler": "trending", "mean_reversion": "ranging"}


class TestRegimeAdjustedWeights:
    """regime_adjusted_weights shifts weights based on market regime."""

    def test_trending_boosts_smart_hodler(self):
        base = {"smart_hodler": Decimal("0.5"), "mean_reversion": Decimal("0.5")}
        adjusted = regime_adjusted_weights(
            base, regime="trending", confidence=1.0,
            regime_shift_pct=0.2, strategy_regime_map=_REGIME_MAP,
        )
        assert adjusted["smart_hodler"] > base["smart_hodler"]
        assert adjusted["mean_reversion"] < base["mean_reversion"]

    def test_ranging_boosts_mean_reversion(self):
        base = {"smart_hodler": Decimal("0.5"), "mean_reversion": Decimal("0.5")}
        adjusted = regime_adjusted_weights(
            base, regime="ranging", confidence=1.0,
            regime_shift_pct=0.2, strategy_regime_map=_REGIME_MAP,
        )
        assert adjusted["mean_reversion"] > base["mean_reversion"]
        assert adjusted["smart_hodler"] < base["smart_hodler"]

    def test_uncertain_no_change(self):
        base = {"smart_hodler": Decimal("0.5"), "mean_reversion": Decimal("0.5")}
        adjusted = regime_adjusted_weights(
            base, regime="uncertain", confidence=0.8,
            regime_shift_pct=0.2, strategy_regime_map=_REGIME_MAP,
        )
        assert adjusted == base

    def test_zero_confidence_no_change(self):
        base = {"smart_hodler": Decimal("0.5"), "mean_reversion": Decimal("0.5")}
        adjusted = regime_adjusted_weights(
            base, regime="trending", confidence=0.0,
            regime_shift_pct=0.2, strategy_regime_map=_REGIME_MAP,
        )
        assert adjusted == base

    def test_confidence_modulates_shift(self):
        base = {"smart_hodler": Decimal("0.5"), "mean_reversion": Decimal("0.5")}

        full = regime_adjusted_weights(
            base, regime="trending", confidence=1.0,
            regime_shift_pct=0.2, strategy_regime_map=_REGIME_MAP,
        )
        half = regime_adjusted_weights(
            base, regime="trending", confidence=0.5,
            regime_shift_pct=0.2, strategy_regime_map=_REGIME_MAP,
        )

        # Half confidence → half the boost
        full_boost = full["smart_hodler"] - base["smart_hodler"]
        half_boost = half["smart_hodler"] - base["smart_hodler"]
        assert half_boost == full_boost / 2

    def test_unfavored_never_below_floor(self):
        base = {"smart_hodler": Decimal("0.05"), "mean_reversion": Decimal("0.95")}
        adjusted = regime_adjusted_weights(
            base, regime="ranging", confidence=1.0,
            regime_shift_pct=0.5, strategy_regime_map=_REGIME_MAP,
        )
        assert adjusted["smart_hodler"] >= Decimal("0.01")


class TestRegimeAdjustedAllocations:
    """compute_allocations with regime parameters."""

    def test_trending_shifts_capital(self):
        allocs = compute_allocations(
            total_capital=Decimal("10000"),
            enabled_strategies=["smart_hodler", "mean_reversion"],
            strategy_configs={"smart_hodler": {}, "mean_reversion": {}},
            symbols=["BTCUSDT"],
            regime="trending",
            regime_confidence=1.0,
            regime_shift_pct=0.2,
            strategy_regime_map=_REGIME_MAP,
        )

        # smart_hodler should get more than 5000
        assert allocs[("smart_hodler", "BTCUSDT")] > Decimal("5000")
        assert allocs[("mean_reversion", "BTCUSDT")] < Decimal("5000")

    def test_no_regime_uses_base_weights(self):
        with_regime = compute_allocations(
            total_capital=Decimal("10000"),
            enabled_strategies=["smart_hodler", "mean_reversion"],
            strategy_configs={"smart_hodler": {}, "mean_reversion": {}},
            symbols=["BTCUSDT"],
        )
        without = compute_allocations(
            total_capital=Decimal("10000"),
            enabled_strategies=["smart_hodler", "mean_reversion"],
            strategy_configs={"smart_hodler": {}, "mean_reversion": {}},
            symbols=["BTCUSDT"],
            regime=None,
        )
        assert with_regime == without

    def test_regime_config_fields(self):
        from core.config import RegimeConfig

        cfg = RegimeConfig()
        assert cfg.rebalance_enabled is False
        assert cfg.regime_shift_pct == 0.2
        assert cfg.strategy_regime_map == {"smart_hodler": "trending", "mean_reversion": "ranging", "bear_guard": "trending"}
