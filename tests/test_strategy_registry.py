"""
Unit tests for the strategy plugin registry (task 5.1).

Verifies:
  - Auto-discovery finds built-in strategies
  - Registry returns correct class for each name
  - reset_registry clears state
  - to_state_dict / from_state_dict round-trip
"""

from __future__ import annotations

from core.models import StrategyState
from core.strategies.base import Strategy
from core.strategies.mean_reversion import MeanReversion
from core.strategies.registry import get_registry, reset_registry
from core.strategies.smart_hodler import SmartHodler


class TestAutoDiscovery:
    def setup_method(self):
        reset_registry()

    def teardown_method(self):
        reset_registry()

    def test_discovers_smart_hodler(self):
        registry = get_registry()
        assert "smart_hodler" in registry
        assert registry["smart_hodler"] is SmartHodler

    def test_discovers_mean_reversion(self):
        registry = get_registry()
        assert "mean_reversion" in registry
        assert registry["mean_reversion"] is MeanReversion

    def test_does_not_register_base_class(self):
        registry = get_registry()
        for cls in registry.values():
            assert cls is not Strategy

    def test_reset_clears_registry(self):
        get_registry()
        reset_registry()
        from core.strategies.registry import _registry, _discovered

        assert len(_registry) == 0
        assert _discovered is False

    def test_idempotent_discovery(self):
        r1 = get_registry()
        r2 = get_registry()
        assert r1 is r2


class TestStateDict:
    def test_smart_hodler_round_trip(self):
        s = SmartHodler({"cooldown_candles": 16})
        s._state = StrategyState.COOLDOWN
        s._cooldown_remaining = 5
        s._consecutive_below_ema200 = 3

        data = s.to_state_dict()
        assert data["state"] == "cooldown"
        assert data["cooldown_remaining"] == 5
        assert data["consecutive_below_ema200"] == 3

        s2 = SmartHodler({"cooldown_candles": 16})
        s2.from_state_dict(data)
        assert s2._state == StrategyState.COOLDOWN
        assert s2._cooldown_remaining == 5
        assert s2._consecutive_below_ema200 == 3

    def test_mean_reversion_round_trip(self):
        s = MeanReversion({"cooldown_candles": 8})
        s._state = StrategyState.POSITION
        s._cooldown_remaining = 0

        data = s.to_state_dict()
        assert data["state"] == "position"
        assert data["cooldown_remaining"] == 0

        s2 = MeanReversion({"cooldown_candles": 8})
        s2.from_state_dict(data)
        assert s2._state == StrategyState.POSITION

    def test_defaults_on_empty_dict(self):
        s = SmartHodler({"cooldown_candles": 16})
        s.from_state_dict({})
        assert s._state == StrategyState.FLAT
        assert s._cooldown_remaining == 0
        assert s._consecutive_below_ema200 == 0
