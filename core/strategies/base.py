"""
Base class for all trading strategies.

Defines the interface that strategies must implement to interact with the
backtesting engine and execution system.

Strategies are pure decision engines: they receive market data and portfolio
state, and return a trading signal. They never execute orders, call APIs,
or mutate portfolio state directly.
"""

from abc import ABC, abstractmethod
from typing import Any, Dict

from core.events import EventChannel, EventType, get_publisher
from core.models import MarketData, Portfolio, Signal, StrategyConditions, StrategyState


class Strategy(ABC):
    """
    Abstract base class for trading strategies.

    Pipeline position: Indicators → **Strategy** → Risk → Execution

    Subclasses must implement:
        - name (property): unique identifier string
        - decide(): MarketData + Portfolio → Signal
    """

    # Default COOLDOWN length in candles when the config omits
    # ``cooldown_candles``. Subclasses override to match their own spec.
    DEFAULT_COOLDOWN_CANDLES: int = 0

    def __init__(self, config: Dict[str, Any]) -> None:
        """
        Initialize strategy with configuration.

        Args:
            config: Strategy-specific parameters (from strategies.yaml).
        """
        self.config = config
        self._state: StrategyState = StrategyState.FLAT
        self._conditions: StrategyConditions = StrategyConditions()
        self._cooldown_remaining: int = 0

    # ── Abstract interface ───────────────────────────────────────────────

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique identifier for the strategy (e.g. 'smart_hodler')."""

    @abstractmethod
    def decide(self, market_data: MarketData, portfolio: Portfolio) -> Signal:
        """
        Analyze market data and return a trading signal.

        Called on every candle close. Must be pure logic — no side effects.

        Args:
            market_data: Current candles + calculated indicators.
            portfolio: Current balance + open positions.

        Returns:
            Signal: BUY, SELL_FULL, SELL_HALF, or HOLD.
        """

    # ── Concrete helpers ─────────────────────────────────────────────────

    @property
    def description(self) -> str:
        """Human-readable description (defaults to class docstring)."""
        return (self.__class__.__doc__ or "").strip()

    @property
    def state(self) -> StrategyState:
        """Current state-machine state."""
        return self._state

    @property
    def conditions(self) -> StrategyConditions:
        """Current conditions snapshot (for debugging / UI)."""
        return self._conditions

    @property
    def configured_cooldown_candles(self) -> int:
        """The ``COOLDOWN`` length (in candles) this strategy is configured for.

        Reads ``cooldown_candles`` from the config, falling back to the
        subclass's :attr:`DEFAULT_COOLDOWN_CANDLES`. This replaces the old
        ``getattr(strategy, "_cooldown_candles", 16)`` pattern (BUG-017), which
        referenced an attribute that never existed and so always applied a
        hardcoded 16.
        """
        return int(self.config.get("cooldown_candles", self.DEFAULT_COOLDOWN_CANDLES))

    def enter_cooldown(self, reason: str = "", candles: int | None = None) -> None:
        """
        Transition the strategy into the ``COOLDOWN`` state.

        This is the single public entry point for entering cooldown. By default
        the countdown uses :attr:`configured_cooldown_candles` (the strategy's
        real config) — there is **no** magic hardcoded length. Used both by
        internal exit paths and by the live tick loop when an exchange stop
        fills between ticks (BUG-017), so every COOLDOWN transition goes through
        one consistent path instead of poking ``_state`` directly.

        Args:
            reason: Optional short label for why cooldown was entered
                (e.g. ``"hard_stop"``, ``"exchange_stop"``) — for logging.
            candles: Explicit countdown length. When ``None`` (default), uses
                the configured length. Callers that already have an
                engine-computed value (e.g. ``StopCheckResult.cooldown_candles``)
                pass it here.
        """
        self._cooldown_remaining = int(
            candles if candles is not None else self.configured_cooldown_candles
        )
        self._state = StrategyState.COOLDOWN

    def evaluate(
        self, market_data: MarketData, portfolio: Portfolio
    ) -> Signal:
        """
        Wrapper around :meth:`decide` that publishes strategy events.

        Captures state and conditions before calling ``decide()``, then
        publishes ``SIGNAL_GENERATED`` (always), ``STATE_CHANGED`` (when the
        state-machine transitions), and ``CONDITIONS_EVALUATED`` (when the
        conditions snapshot changes).

        Callers that need event visibility (backtest runner, live tick loop)
        should call this method instead of ``decide()`` directly.

        Args:
            market_data: Current candles + calculated indicators.
            portfolio: Current balance + open positions.

        Returns:
            Signal: The same signal that ``decide()`` returns.
        """
        old_state = self._state
        old_conditions = self._conditions.model_copy()

        signal = self.decide(market_data, portfolio)

        publisher = get_publisher()
        publisher.publish(
            EventChannel.STRATEGY,
            EventType.SIGNAL_GENERATED,
            {
                "strategy": self.name,
                "symbol": market_data.symbol,
                "signal": signal.value,
                "state": self._state.value,
            },
        )

        if self._state != old_state:
            publisher.publish(
                EventChannel.STRATEGY,
                EventType.STATE_CHANGED,
                {
                    "strategy": self.name,
                    "symbol": market_data.symbol,
                    "old_state": old_state.value,
                    "new_state": self._state.value,
                },
            )

        if self._conditions != old_conditions:
            publisher.publish(
                EventChannel.STRATEGY,
                EventType.CONDITIONS_EVALUATED,
                {
                    "strategy": self.name,
                    "symbol": market_data.symbol,
                    **self._conditions.model_dump(mode="json"),
                },
            )

        return signal

    def to_state_dict(self) -> Dict[str, Any]:
        """
        Serialize strategy-specific state for persistence.

        Subclasses should override and include their own fields::

            def to_state_dict(self):
                d = super().to_state_dict()
                d["my_counter"] = self._my_counter
                return d
        """
        return {
            "state": self._state.value,
            "cooldown_remaining": getattr(self, "_cooldown_remaining", 0),
        }

    def from_state_dict(self, data: Dict[str, Any]) -> None:
        """
        Restore strategy-specific state from a persisted dict.

        Subclasses should override and restore their own fields::

            def from_state_dict(self, data):
                super().from_state_dict(data)
                self._my_counter = data.get("my_counter", 0)
        """
        self._state = StrategyState(data.get("state", "flat"))
        self._cooldown_remaining = data.get("cooldown_remaining", 0)

    def reset(self) -> None:
        """
        Reset strategy to initial state.

        Called before a new backtest run or system restart.
        Subclasses should call super().reset() then clear their own counters.
        """
        self._state = StrategyState.FLAT
        self._conditions = StrategyConditions()
