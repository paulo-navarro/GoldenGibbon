"""
Portfolio state management.

Provides PortfolioManager – the single source of truth for balance,
positions, trade history, and equity-curve tracking.  All mutations
(open, close, scale-in, reduce) go through this class so PnL
accounting and fee deductions stay centralised.
"""

from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Dict, Optional

import structlog

from core.models import (
    ExitReason,
    Portfolio,
    PortfolioSnapshot,
    Position,
    Trade,
)

logger = structlog.get_logger(__name__)


class PortfolioManager:
    """
    Manages portfolio state: balance, positions, trades, equity curve.

    All monetary mutations flow through this class so fee deductions,
    PnL calculations, and snapshot recording happen in one place.
    """

    # ── Construction ─────────────────────────────────────────────────────

    def __init__(
        self,
        initial_capital: Decimal,
        taker_fee: Decimal = Decimal("0.001"),
    ) -> None:
        """
        Create a fresh portfolio.

        Args:
            initial_capital: Starting USDT balance.
            taker_fee: Fee rate applied to each buy / sell (default 0.1 %).
        """
        if initial_capital <= 0:
            raise ValueError("initial_capital must be positive")
        self._taker_fee = taker_fee
        self._initial_capital = initial_capital
        self._portfolio = Portfolio(
            usdt_balance=initial_capital,
            equity=initial_capital,
        )

    # ── Read-only access ─────────────────────────────────────────────────

    @property
    def portfolio(self) -> Portfolio:
        """Expose the underlying Portfolio model (read-only intent)."""
        return self._portfolio

    @property
    def initial_capital(self) -> Decimal:
        return self._initial_capital

    # ── Position helpers ─────────────────────────────────────────────────

    def has_position(self, symbol: str) -> bool:
        """Return True if there is an open position for *symbol*."""
        return symbol in self._portfolio.positions

    def get_position(self, symbol: str) -> Optional[Position]:
        """Return the Position for *symbol*, or None."""
        return self._portfolio.positions.get(symbol)

    # ── Open position ────────────────────────────────────────────────────

    def open_position(
        self,
        symbol: str,
        size: Decimal,
        entry_price: Decimal,
        entry_time: datetime,
        hard_stop_price: Decimal = Decimal("0"),
        trailing_stop_price: Decimal = Decimal("0"),
        strategy: str = "smart_hodler",
    ) -> Position:
        """
        Open a new position, deducting cost + fee from balance.

        Args:
            symbol: Trading pair (e.g. "BTCUSDT").
            size: Amount of base asset to buy.
            entry_price: Fill price per unit.
            entry_time: Timestamp of the fill.
            hard_stop_price: Initial hard-stop level.
            trailing_stop_price: Initial trailing-stop level.
            strategy: Strategy name for later trade records.

        Returns:
            The newly created Position.

        Raises:
            ValueError: If a position already exists for this symbol.
            ValueError: If insufficient funds.
        """
        if self.has_position(symbol):
            raise ValueError(f"Position already exists for {symbol}")

        cost = size * entry_price
        fee = self._apply_fee(cost)
        total_cost = cost + fee

        if total_cost > self._portfolio.usdt_balance:
            raise ValueError(
                f"Insufficient funds: need {total_cost}, "
                f"have {self._portfolio.usdt_balance}"
            )

        self._portfolio.usdt_balance -= total_cost

        position = Position(
            symbol=symbol,
            size=size,
            entry_price=entry_price,
            entry_time=entry_time,
            highest_close=entry_price,
            trailing_stop_price=trailing_stop_price,
            hard_stop_price=hard_stop_price,
        )
        self._portfolio.positions[symbol] = position
        self._portfolio.open_trades_count = len(self._portfolio.positions)

        logger.info(
            "portfolio.open",
            symbol=symbol,
            size=str(size),
            entry_price=str(entry_price),
            cost=str(total_cost),
        )

        return position

    # ── Scale in ─────────────────────────────────────────────────────────

    def scale_in(
        self,
        symbol: str,
        additional_size: Decimal,
        price: Decimal,
        time: datetime,
    ) -> Position:
        """
        Increase an existing position (scaled entry).

        Updates the weighted-average entry price and increments
        ``scale_in_count`` (max 2).

        Args:
            symbol: Trading pair.
            additional_size: Extra base-asset amount to buy.
            price: Fill price for this tranche.
            time: Timestamp.

        Returns:
            The updated Position.

        Raises:
            ValueError: No existing position for symbol.
            ValueError: Insufficient funds.
            ValueError: scale_in_count already at max (2).
        """
        position = self.get_position(symbol)
        if position is None:
            raise ValueError(f"No position to scale into for {symbol}")

        if position.scale_in_count >= 2:
            raise ValueError(
                f"Max scale-in count (2) reached for {symbol}"
            )

        cost = additional_size * price
        fee = self._apply_fee(cost)
        total_cost = cost + fee

        if total_cost > self._portfolio.usdt_balance:
            raise ValueError(
                f"Insufficient funds: need {total_cost}, "
                f"have {self._portfolio.usdt_balance}"
            )

        self._portfolio.usdt_balance -= total_cost

        # Weighted-average entry price
        total_size = position.size + additional_size
        new_entry = (
            (position.entry_price * position.size + price * additional_size)
            / total_size
        ).quantize(Decimal("0.00000001"), rounding=ROUND_HALF_UP)

        # Mutate position via model_copy
        updated = position.model_copy(
            update={
                "size": total_size,
                "entry_price": new_entry,
                "scale_in_count": position.scale_in_count + 1,
            }
        )
        self._portfolio.positions[symbol] = updated

        logger.info(
            "portfolio.scale_in",
            symbol=symbol,
            additional_size=str(additional_size),
            price=str(price),
            new_avg_entry=str(new_entry),
            total_size=str(total_size),
            scale_in_count=position.scale_in_count + 1,
        )

        return updated

    # ── Reduce / close position ──────────────────────────────────────────

    def reduce_position(
        self,
        symbol: str,
        sell_fraction: Decimal,
        exit_price: Decimal,
        exit_time: datetime,
        exit_reason: ExitReason,
        strategy: str = "smart_hodler",
    ) -> Trade:
        """
        Sell a fraction (0 < fraction <= 1) of an existing position.

        Proceeds (minus fee) are credited to balance.  A Trade record
        is created and appended to ``trade_history``.  If the entire
        position is sold (fraction == 1), it is removed from the
        positions dict.

        Args:
            symbol: Trading pair.
            sell_fraction: Proportion to sell (e.g. 0.5 for half).
            exit_price: Fill price per unit.
            exit_time: Timestamp of the fill.
            exit_reason: Reason for exiting.
            strategy: Strategy name recorded on the Trade.

        Returns:
            The Trade record for this (partial) exit.

        Raises:
            ValueError: No position for symbol.
            ValueError: sell_fraction not in (0, 1].
        """
        position = self.get_position(symbol)
        if position is None:
            raise ValueError(f"No position to reduce for {symbol}")

        if sell_fraction <= 0 or sell_fraction > 1:
            raise ValueError("sell_fraction must be in (0, 1]")

        sold_size = (position.size * sell_fraction).quantize(
            Decimal("0.00000001"), rounding=ROUND_HALF_UP
        )
        proceeds = sold_size * exit_price
        fee = self._apply_fee(proceeds)
        net_proceeds = proceeds - fee

        # PnL (accounts for buy-side fee embedded in entry cost
        # and sell-side fee deducted here)
        entry_cost = sold_size * position.entry_price
        buy_fee = self._apply_fee(entry_cost)
        pnl_usdt = net_proceeds - (entry_cost + buy_fee)
        pnl_percent = (
            (pnl_usdt / (entry_cost + buy_fee)) * 100
        ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        duration = int((exit_time - position.entry_time).total_seconds() / 60)

        trade = Trade(
            symbol=symbol,
            strategy=strategy,
            entry_price=position.entry_price,
            exit_price=exit_price,
            size=sold_size,
            entry_time=position.entry_time,
            exit_time=exit_time,
            pnl_usdt=pnl_usdt,
            pnl_percent=pnl_percent,
            duration_minutes=duration,
            exit_reason=exit_reason,
        )

        self._portfolio.usdt_balance += net_proceeds
        self._portfolio.total_pnl += pnl_usdt
        self._portfolio.trade_history.append(trade)

        remaining = position.size - sold_size
        if remaining <= 0:
            del self._portfolio.positions[symbol]
        else:
            self._portfolio.positions[symbol] = position.model_copy(
                update={"size": remaining}
            )
        self._portfolio.open_trades_count = len(self._portfolio.positions)

        logger.info(
            "portfolio.reduce",
            symbol=symbol,
            sold_size=str(sold_size),
            exit_price=str(exit_price),
            pnl_usdt=str(pnl_usdt),
            pnl_percent=str(pnl_percent),
            exit_reason=exit_reason.value,
            remaining=str(remaining),
        )

        return trade

    def close_position(
        self,
        symbol: str,
        exit_price: Decimal,
        exit_time: datetime,
        exit_reason: ExitReason,
        strategy: str = "smart_hodler",
    ) -> Trade:
        """Convenience wrapper: sell 100 % of a position."""
        return self.reduce_position(
            symbol=symbol,
            sell_fraction=Decimal("1"),
            exit_price=exit_price,
            exit_time=exit_time,
            exit_reason=exit_reason,
            strategy=strategy,
        )

    # ── Stop-price updates ───────────────────────────────────────────────

    def update_stops(
        self,
        symbol: str,
        highest_close: Optional[Decimal] = None,
        trailing_stop_price: Optional[Decimal] = None,
        hard_stop_price: Optional[Decimal] = None,
    ) -> Position:
        """
        Update stop fields on an existing position.

        Only non-None values are applied.

        Returns:
            The updated Position.

        Raises:
            ValueError: No position for symbol.
        """
        position = self.get_position(symbol)
        if position is None:
            raise ValueError(f"No position to update for {symbol}")

        updates: Dict[str, Decimal] = {}
        if highest_close is not None:
            updates["highest_close"] = highest_close
        if trailing_stop_price is not None:
            updates["trailing_stop_price"] = trailing_stop_price
        if hard_stop_price is not None:
            updates["hard_stop_price"] = hard_stop_price

        updated = position.model_copy(update=updates)
        self._portfolio.positions[symbol] = updated
        return updated

    # ── Snapshots (equity curve) ─────────────────────────────────────────

    def take_snapshot(
        self,
        timestamp: datetime,
        current_prices: Dict[str, Decimal],
    ) -> PortfolioSnapshot:
        """
        Record a point-in-time portfolio snapshot.

        Recalculates equity at *current_prices*, builds a
        ``PortfolioSnapshot``, and appends it to the equity curve.

        Returns:
            The new PortfolioSnapshot.
        """
        self._portfolio.update_equity(current_prices)

        positions_value = Decimal("0")
        for sym, pos in self._portfolio.positions.items():
            price = current_prices.get(sym, pos.entry_price)
            positions_value += pos.size * price

        snapshot = PortfolioSnapshot(
            timestamp=timestamp,
            usdt_balance=self._portfolio.usdt_balance,
            positions_value=positions_value,
            total_equity=self._portfolio.equity,
            total_pnl=self._portfolio.total_pnl,
            open_positions_count=len(self._portfolio.positions),
        )
        self._portfolio.equity_curve.append(snapshot)
        return snapshot

    # ── Internal helpers ─────────────────────────────────────────────────

    def _apply_fee(self, amount: Decimal) -> Decimal:
        """Calculate the taker fee for a given notional *amount*."""
        return (amount * self._taker_fee).quantize(
            Decimal("0.00000001"), rounding=ROUND_HALF_UP
        )
