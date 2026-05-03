"""
Position-sizing and guard-rail mixin for RiskEngine.

Handles capital allocation, daily trade limits, per-symbol exposure caps,
and per-trade notional caps.
"""

from datetime import date, datetime, timezone
from decimal import Decimal, ROUND_DOWN
from typing import Dict, Optional

import structlog

from core.models import Portfolio

logger = structlog.get_logger(__name__)


class SizingMixin:
    """Position-sizing and guard methods extracted from RiskEngine."""

    # ── Sizing ───────────────────────────────────────────────────────

    def _calculate_initial_size(
        self,
        portfolio: Portfolio,
        price: Decimal,
    ) -> Decimal:
        """Compute the base-asset quantity for the first entry."""
        return self._size_from_pct(self._initial_pct, portfolio, price)

    def _size_from_pct(
        self,
        pct: Decimal,
        portfolio: Portfolio,
        price: Decimal,
    ) -> Decimal:
        """
        Convert a capital-percentage into a base-asset quantity,
        respecting the global max position size cap and per-trade cap.
        """
        if price <= 0:
            return Decimal("0")

        desired_notional = portfolio.available_capital * pct

        # Cap at max_position_size_pct × equity
        max_notional = portfolio.equity * self._max_position_pct
        notional = min(desired_notional, max_notional)

        # Per-trade absolute notional cap (4.6)
        if self._max_trade_size_usdt is not None:
            notional = min(notional, self._max_trade_size_usdt)

        size = (notional / price).quantize(
            Decimal("0.00000001"), rounding=ROUND_DOWN
        )
        if size <= 0:
            return Decimal("0")
        return size

    # ── Daily trade limit (4.4) ──────────────────────────────────────

    def _today_utc(self) -> date:
        return datetime.now(timezone.utc).date()

    def _is_daily_limit_reached(self) -> bool:
        """Return True if max_trades_per_day has been reached for today."""
        if self._max_daily_trades is None:
            return False
        return self.get_daily_opens() >= self._max_daily_trades

    def _increment_daily_opens(self) -> None:
        """Record one more entry open for today."""
        today = self._today_utc()
        self._daily_opens[today] = self._daily_opens.get(today, 0) + 1

    # ── Per-symbol exposure (4.6) ────────────────────────────────────

    def _is_symbol_exposure_exceeded(
        self,
        symbol: str,
        portfolio: Portfolio,
        close: Decimal,
    ) -> bool:
        """
        Return True if opening a new position would breach the per-symbol
        exposure cap.
        """
        if self._max_symbol_exposure_usdt is None:
            return False
        position = portfolio.positions.get(symbol)
        if position is None:
            return False
        current_exposure = position.size * close
        return current_exposure >= self._max_symbol_exposure_usdt

    # ── Timeframe parsing ────────────────────────────────────────────

    @staticmethod
    def _parse_timeframe_minutes(tf: str) -> int:
        """Convert timeframe string (e.g. '15m', '1h') to minutes."""
        mapping = {
            "1m": 1, "5m": 5, "15m": 15, "30m": 30,
            "1h": 60, "4h": 240, "1d": 1440, "1w": 10080,
        }
        return mapping.get(tf, 15)
