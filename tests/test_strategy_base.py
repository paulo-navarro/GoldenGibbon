"""
Tests for the Strategy abstract base class.
"""

import pytest
from unittest.mock import MagicMock

from core.models import MarketData, Portfolio, Signal, StrategyConditions, StrategyState
from core.strategies import Strategy
from core.strategies.base import Strategy as StrategyDirect


# ── Concrete test implementation ─────────────────────────────────────────────


class ConcreteStrategy(Strategy):
    """A minimal strategy used for testing."""

    @property
    def name(self) -> str:
        return "test_strategy"

    def decide(self, market_data: MarketData, portfolio: Portfolio) -> Signal:
        """Return BUY when symbol starts with 'B', HOLD otherwise."""
        if market_data.symbol.startswith("B"):
            return Signal.BUY
        return Signal.HOLD


# ── Tests ────────────────────────────────────────────────────────────────────


class TestStrategyABC:
    """Verify abstract-class enforcement."""

    def test_cannot_instantiate_directly(self):
        with pytest.raises(TypeError):
            Strategy({})

    def test_must_implement_name(self):
        class MissingName(Strategy):
            def decide(self, market_data, portfolio):
                return Signal.HOLD

        with pytest.raises(TypeError):
            MissingName({})

    def test_must_implement_decide(self):
        class MissingDecide(Strategy):
            @property
            def name(self):
                return "incomplete"

        with pytest.raises(TypeError):
            MissingDecide({})


class TestStrategyInit:
    """Verify constructor and default state."""

    def test_config_stored(self):
        config = {"ema_fast": 50, "ema_slow": 200}
        s = ConcreteStrategy(config)
        assert s.config is config

    def test_initial_state_is_flat(self):
        s = ConcreteStrategy({})
        assert s.state == StrategyState.FLAT

    def test_initial_conditions_all_false(self):
        s = ConcreteStrategy({})
        cond = s.conditions
        assert isinstance(cond, StrategyConditions)
        assert not cond.all_buy_conditions_met


class TestStrategyDecide:
    """Verify decide() contract on a concrete subclass."""

    def test_returns_buy(self):
        s = ConcreteStrategy({})
        md = MagicMock(spec=MarketData)
        md.symbol = "BTCUSDT"
        pf = MagicMock(spec=Portfolio)
        assert s.decide(md, pf) == Signal.BUY

    def test_returns_hold(self):
        s = ConcreteStrategy({})
        md = MagicMock(spec=MarketData)
        md.symbol = "ETHUSDT"
        pf = MagicMock(spec=Portfolio)
        assert s.decide(md, pf) == Signal.HOLD


class TestStrategyReset:
    """Verify reset() restores initial state."""

    def test_reset_clears_state(self):
        s = ConcreteStrategy({})
        s._state = StrategyState.POSITION
        s.reset()
        assert s.state == StrategyState.FLAT

    def test_reset_clears_conditions(self):
        s = ConcreteStrategy({})
        s._conditions.ema_cross_bullish = True
        s.reset()
        assert not s.conditions.ema_cross_bullish


class TestStrategyProperties:
    """Verify name, description, and re-export."""

    def test_name(self):
        s = ConcreteStrategy({})
        assert s.name == "test_strategy"

    def test_description_from_docstring(self):
        s = ConcreteStrategy({})
        assert "minimal strategy" in s.description.lower()

    def test_import_from_package(self):
        """Strategy is importable from core.strategies (not just core.strategies.base)."""
        assert Strategy is StrategyDirect
