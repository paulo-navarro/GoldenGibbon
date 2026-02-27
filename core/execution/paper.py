"""
PaperExecutor – simulated order execution with always-adverse slippage.

Pipeline position:  Risk → **Executor** → Portfolio

The executor translates a ``RiskDecision`` into a filled ``Order`` by:

1. Applying slippage to the reference price (buy → higher, sell → lower).
2. Delegating balance / position mutations to ``PortfolioManager``.
3. Returning an ``ExecutionResult`` that bundles the Order with the
   resulting Position (buy-side) or Trade (sell-side).

Fees are **not** applied here – ``PortfolioManager`` handles them
internally via its ``taker_fee``.
"""

from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional

import structlog

from core.events import EventChannel, EventType, get_publisher
from core.models import (
    ExecutionResult,
    ExitReason,
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
    Position,
    RiskAction,
    RiskDecision,
    Trade,
)
from core.portfolio import PortfolioManager

logger = structlog.get_logger(__name__)


class PaperExecutor:
    """
    Simulated executor for backtesting and paper-trading.

    Immediately fills every order at the slippage-adjusted price.
    No latency simulation, no partial fills – one decision, one fill.
    """

    # ── Construction ─────────────────────────────────────────────────────

    def __init__(
        self,
        strategy_name: str,
        portfolio_manager: PortfolioManager,
        slippage: Decimal = Decimal("0.001"),
        simulate_slippage: bool = True,
    ) -> None:
        """
        Args:
            strategy_name: Passed through to PortfolioManager on every
                           open / close call (e.g. ``"smart_hodler"``).
            portfolio_manager: The PM instance that owns balance / positions.
            slippage: Fractional slippage rate (0.001 = 0.1 %).
            simulate_slippage: When False, slippage is skipped (fill at
                               reference price).
        """
        self._strategy = strategy_name
        self._pm = portfolio_manager
        self._slippage = slippage
        self._simulate_slippage = simulate_slippage

    # ── Properties ───────────────────────────────────────────────────────

    @property
    def strategy_name(self) -> str:
        return self._strategy

    @property
    def portfolio_manager(self) -> PortfolioManager:
        return self._pm

    # ── Main entry point ─────────────────────────────────────────────────

    def execute(
        self,
        decision: RiskDecision,
        timestamp: datetime,
    ) -> Optional[ExecutionResult]:
        """
        Execute a risk decision.

        Args:
            decision: Output of ``RiskEngine.evaluate()`` or
                      ``check_stops()``.
            timestamp: Candle-open time (backtest-controlled clock).

        Returns:
            ``ExecutionResult`` for actionable decisions, ``None`` for
            HOLD.
        """
        action = decision.action

        if action == RiskAction.HOLD:
            return None

        result: Optional[ExecutionResult] = None

        if action == RiskAction.OPEN:
            result = self._execute_open(decision, timestamp)
        elif action == RiskAction.SCALE_IN:
            result = self._execute_scale_in(decision, timestamp)
        elif action == RiskAction.CLOSE:
            result = self._execute_close(decision, timestamp)
        elif action == RiskAction.REDUCE:
            result = self._execute_reduce(decision, timestamp)

        if result is not None:
            publisher = get_publisher()
            publisher.publish(
                EventChannel.EXECUTION,
                EventType.ORDER_FILLED,
                {
                    "action": action.value,
                    **result.order.model_dump(mode="json"),
                },
            )

        return result

    # ── Private: buy-side ────────────────────────────────────────────────

    def _execute_open(
        self,
        decision: RiskDecision,
        timestamp: datetime,
    ) -> ExecutionResult:
        """OPEN → buy at slippage-adjusted price, open new position."""
        fill_price = self._apply_slippage(decision.price, OrderSide.BUY)

        position = self._pm.open_position(
            symbol=decision.symbol,
            size=decision.size,
            entry_price=fill_price,
            entry_time=timestamp,
            hard_stop_price=decision.hard_stop_price or Decimal("0"),
            trailing_stop_price=decision.trailing_stop_price or Decimal("0"),
            strategy=self._strategy,
        )

        logger.info(
            "execution.open",
            symbol=decision.symbol,
            size=str(decision.size),
            fill_price=str(fill_price),
            slippage=str(self._slippage),
        )

        order = self._build_order(
            decision=decision,
            side=OrderSide.BUY,
            fill_price=fill_price,
            timestamp=timestamp,
        )
        return ExecutionResult(order=order, position=position)

    def _execute_scale_in(
        self,
        decision: RiskDecision,
        timestamp: datetime,
    ) -> ExecutionResult:
        """SCALE_IN → buy at slippage-adjusted price, add to position."""
        fill_price = self._apply_slippage(decision.price, OrderSide.BUY)

        position = self._pm.scale_in(
            symbol=decision.symbol,
            additional_size=decision.size,
            price=fill_price,
            time=timestamp,
        )

        logger.info(
            "execution.scale_in",
            symbol=decision.symbol,
            size=str(decision.size),
            fill_price=str(fill_price),
        )

        order = self._build_order(
            decision=decision,
            side=OrderSide.BUY,
            fill_price=fill_price,
            timestamp=timestamp,
        )
        return ExecutionResult(order=order, position=position)

    # ── Private: sell-side ───────────────────────────────────────────────

    def _execute_close(
        self,
        decision: RiskDecision,
        timestamp: datetime,
    ) -> ExecutionResult:
        """CLOSE → sell all at slippage-adjusted price."""
        fill_price = self._apply_slippage(decision.price, OrderSide.SELL)

        trade = self._pm.close_position(
            symbol=decision.symbol,
            exit_price=fill_price,
            exit_time=timestamp,
            exit_reason=decision.exit_reason or ExitReason.MANUAL,
            strategy=self._strategy,
        )

        logger.info(
            "execution.close",
            symbol=decision.symbol,
            size=str(trade.size),
            fill_price=str(fill_price),
            pnl_usdt=str(trade.pnl_usdt),
            pnl_percent=str(trade.pnl_percent),
            exit_reason=str(decision.exit_reason),
        )

        order = self._build_order(
            decision=decision,
            side=OrderSide.SELL,
            fill_price=fill_price,
            timestamp=timestamp,
        )
        return ExecutionResult(order=order, trade=trade)

    def _execute_reduce(
        self,
        decision: RiskDecision,
        timestamp: datetime,
    ) -> ExecutionResult:
        """REDUCE → sell fraction at slippage-adjusted price."""
        fill_price = self._apply_slippage(decision.price, OrderSide.SELL)

        trade = self._pm.reduce_position(
            symbol=decision.symbol,
            sell_fraction=decision.sell_fraction or Decimal("0.5"),
            exit_price=fill_price,
            exit_time=timestamp,
            exit_reason=decision.exit_reason or ExitReason.MOMENTUM_FADE,
            strategy=self._strategy,
        )

        logger.info(
            "execution.reduce",
            symbol=decision.symbol,
            size=str(trade.size),
            fill_price=str(fill_price),
            pnl_usdt=str(trade.pnl_usdt),
            sell_fraction=str(decision.sell_fraction),
        )

        order = self._build_order(
            decision=decision,
            side=OrderSide.SELL,
            fill_price=fill_price,
            timestamp=timestamp,
        )
        return ExecutionResult(order=order, trade=trade)

    # ── Private: helpers ─────────────────────────────────────────────────

    def _apply_slippage(self, price: Decimal, side: OrderSide) -> Decimal:
        """
        Apply always-adverse slippage.

        Buy  → price × (1 + slippage)
        Sell → price × (1 − slippage)
        """
        if not self._simulate_slippage:
            return price

        if side == OrderSide.BUY:
            factor = Decimal("1") + self._slippage
        else:
            factor = Decimal("1") - self._slippage

        return (price * factor).quantize(
            Decimal("0.00000001"), rounding=ROUND_HALF_UP
        )

    def _build_order(
        self,
        decision: RiskDecision,
        side: OrderSide,
        fill_price: Decimal,
        timestamp: datetime,
    ) -> Order:
        """Build a filled Order record."""
        slippage_pct = (
            abs(fill_price - decision.price) / decision.price * 100
            if decision.price > 0
            else Decimal("0")
        ).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)

        order = Order(
            symbol=decision.symbol,
            side=side,
            order_type=OrderType.MARKET,
            amount=decision.size,
            price=decision.price,
            status=OrderStatus.FILLED,
            filled_amount=decision.size,
            avg_fill_price=fill_price,
            slippage_percent=slippage_pct,
            created_at=timestamp,
            updated_at=timestamp,
            filled_at=timestamp,
        )

        publisher = get_publisher()
        publisher.publish_model(
            EventChannel.EXECUTION, EventType.ORDER_CREATED, order
        )

        return order
