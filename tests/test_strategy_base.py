"""
Tests for the Strategy abstract base class.
"""

import pytest
from unittest.mock import MagicMock, patch

from core.events import EventChannel, EventType
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


# ── Helpers for evaluate() tests ─────────────────────────────────────────────


class StatefulStrategy(Strategy):
    """Strategy that can be configured to transition state/conditions."""

    @property
    def name(self) -> str:
        return "stateful_test"

    def decide(self, market_data: MarketData, portfolio: Portfolio) -> Signal:
        """Transition based on pre-configured targets."""
        if self._next_state is not None:
            self._state = self._next_state
        if self._next_conditions is not None:
            self._conditions = self._next_conditions
        return self._next_signal

    def setup(
        self,
        signal: Signal = Signal.HOLD,
        next_state: StrategyState | None = None,
        next_conditions: StrategyConditions | None = None,
    ) -> "StatefulStrategy":
        self._next_signal = signal
        self._next_state = next_state
        self._next_conditions = next_conditions
        return self


def _md(symbol: str = "BTCUSDT") -> MagicMock:
    md = MagicMock(spec=MarketData)
    md.symbol = symbol
    return md


def _pf() -> MagicMock:
    return MagicMock(spec=Portfolio)


# ── evaluate() tests ────────────────────────────────────────────────────────


class TestStrategyEvaluate:
    """Verify evaluate() publishes correct events around decide()."""

    @patch("core.strategies.base.get_publisher")
    def test_returns_same_signal_as_decide(self, mock_get_pub):
        mock_get_pub.return_value = MagicMock()
        s = StatefulStrategy({}).setup(signal=Signal.BUY)
        result = s.evaluate(_md(), _pf())
        assert result == Signal.BUY

    @patch("core.strategies.base.get_publisher")
    def test_publishes_signal_generated_on_every_call(self, mock_get_pub):
        pub = MagicMock()
        mock_get_pub.return_value = pub

        s = StatefulStrategy({}).setup(signal=Signal.HOLD)
        s.evaluate(_md("ETHUSDT"), _pf())

        pub.publish.assert_any_call(
            EventChannel.STRATEGY,
            EventType.SIGNAL_GENERATED,
            {
                "strategy": "stateful_test",
                "symbol": "ETHUSDT",
                "signal": "hold",
                "state": "flat",
            },
        )

    @patch("core.strategies.base.get_publisher")
    def test_publishes_state_changed_on_transition(self, mock_get_pub):
        pub = MagicMock()
        mock_get_pub.return_value = pub

        s = StatefulStrategy({}).setup(
            signal=Signal.BUY, next_state=StrategyState.POSITION
        )
        s.evaluate(_md(), _pf())

        pub.publish.assert_any_call(
            EventChannel.STRATEGY,
            EventType.STATE_CHANGED,
            {
                "strategy": "stateful_test",
                "symbol": "BTCUSDT",
                "old_state": "flat",
                "new_state": "position",
            },
        )

    @patch("core.strategies.base.get_publisher")
    def test_no_state_changed_when_state_unchanged(self, mock_get_pub):
        pub = MagicMock()
        mock_get_pub.return_value = pub

        s = StatefulStrategy({}).setup(signal=Signal.HOLD)
        s.evaluate(_md(), _pf())

        state_changed_calls = [
            c
            for c in pub.publish.call_args_list
            if c[0][1] == EventType.STATE_CHANGED
        ]
        assert state_changed_calls == []

    @patch("core.strategies.base.get_publisher")
    def test_publishes_conditions_evaluated_on_change(self, mock_get_pub):
        pub = MagicMock()
        mock_get_pub.return_value = pub

        new_conds = StrategyConditions(ema_cross_bullish=True)
        s = StatefulStrategy({}).setup(
            signal=Signal.HOLD, next_conditions=new_conds
        )
        s.evaluate(_md("BTCUSDT"), _pf())

        pub.publish.assert_any_call(
            EventChannel.STRATEGY,
            EventType.CONDITIONS_EVALUATED,
            {
                "strategy": "stateful_test",
                "symbol": "BTCUSDT",
                **new_conds.model_dump(mode="json"),
            },
        )

    @patch("core.strategies.base.get_publisher")
    def test_no_conditions_evaluated_when_unchanged(self, mock_get_pub):
        pub = MagicMock()
        mock_get_pub.return_value = pub

        s = StatefulStrategy({}).setup(signal=Signal.HOLD)
        s.evaluate(_md(), _pf())

        cond_calls = [
            c
            for c in pub.publish.call_args_list
            if c[0][1] == EventType.CONDITIONS_EVALUATED
        ]
        assert cond_calls == []

    @patch("core.strategies.base.get_publisher")
    def test_exactly_one_event_when_no_transitions(self, mock_get_pub):
        """HOLD with no state/condition changes → only SIGNAL_GENERATED."""
        pub = MagicMock()
        mock_get_pub.return_value = pub

        s = StatefulStrategy({}).setup(signal=Signal.HOLD)
        s.evaluate(_md(), _pf())

        assert pub.publish.call_count == 1
        assert pub.publish.call_args_list[0][0][1] == EventType.SIGNAL_GENERATED

    @patch("core.strategies.base.get_publisher")
    def test_all_three_events_on_full_transition(self, mock_get_pub):
        """BUY that changes state + conditions → 3 publish calls."""
        pub = MagicMock()
        mock_get_pub.return_value = pub

        new_conds = StrategyConditions(
            ema_cross_bullish=True, adx_above_threshold=True
        )
        s = StatefulStrategy({}).setup(
            signal=Signal.BUY,
            next_state=StrategyState.POSITION,
            next_conditions=new_conds,
        )
        s.evaluate(_md(), _pf())

        assert pub.publish.call_count == 3
        event_types = [c[0][1] for c in pub.publish.call_args_list]
        assert event_types == [
            EventType.SIGNAL_GENERATED,
            EventType.STATE_CHANGED,
            EventType.CONDITIONS_EVALUATED,
        ]

    @patch("core.strategies.base.get_publisher")
    def test_publisher_disabled_does_not_raise(self, mock_get_pub):
        """When publisher is disabled, evaluate() still returns the signal."""
        pub = MagicMock()
        pub.publish.side_effect = None  # no-op
        mock_get_pub.return_value = pub

        s = StatefulStrategy({}).setup(signal=Signal.SELL_FULL)
        result = s.evaluate(_md(), _pf())
        assert result == Signal.SELL_FULL

    def test_import_from_package(self):
        """Strategy is importable from core.strategies (not just core.strategies.base)."""
        assert Strategy is StrategyDirect


# ── BUG-017: public cooldown entry ────────────────────────────────────────────


class TestEnterCooldown:
    """Verify the public enter_cooldown() / configured_cooldown_candles path."""

    def test_configured_cooldown_reads_config(self):
        s = ConcreteStrategy({"cooldown_candles": 42})
        assert s.configured_cooldown_candles == 42

    def test_configured_cooldown_falls_back_to_class_default(self):
        # ConcreteStrategy inherits DEFAULT_COOLDOWN_CANDLES = 0 from base.
        s = ConcreteStrategy({})
        assert s.configured_cooldown_candles == 0

        class WithDefault(ConcreteStrategy):
            DEFAULT_COOLDOWN_CANDLES = 16

        assert WithDefault({}).configured_cooldown_candles == 16

    def test_enter_cooldown_sets_state_and_counter_from_config(self):
        s = ConcreteStrategy({"cooldown_candles": 20})
        s.enter_cooldown(reason="exchange_stop")
        assert s.state == StrategyState.COOLDOWN
        assert s._cooldown_remaining == 20

    def test_enter_cooldown_explicit_candles_override(self):
        s = ConcreteStrategy({"cooldown_candles": 20})
        s.enter_cooldown(reason="stop", candles=5)
        assert s.state == StrategyState.COOLDOWN
        assert s._cooldown_remaining == 5

    def test_enter_cooldown_no_magic_16_default(self):
        """Regression: the old getattr(_cooldown_candles, 16) always applied 16.

        A strategy configured for 192 candles (48h) must get 192, not 16.
        """
        s = ConcreteStrategy({"cooldown_candles": 192})
        s.enter_cooldown(reason="exchange_stop")
        assert s._cooldown_remaining == 192
