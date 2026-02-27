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

    def __init__(self, config: Dict[str, Any]) -> None:
        """
        Initialize strategy with configuration.

        Args:
            config: Strategy-specific parameters (from strategies.yaml).
        """
        self.config = config
        self._state: StrategyState = StrategyState.FLAT
        self._conditions: StrategyConditions = StrategyConditions()

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

    def reset(self) -> None:
        """
        Reset strategy to initial state.

        Called before a new backtest run or system restart.
        Subclasses should call super().reset() then clear their own counters.
        """
        self._state = StrategyState.FLAT
        self._conditions = StrategyConditions()
