"""
Backtest capital resolution (task 9.4).

Backtests used to run on the fictitious ``backtest.initial_capital``
($10k default) while the real account holds ~$50 — making every result
optimistic exactly where min-notional friction bites hardest. In live
mode the total capital now comes from the exchange (cached 5 min by
``_get_live_total_capital``); paper/offline keeps the configured value.
"""

from __future__ import annotations

from decimal import Decimal

import structlog

logger = structlog.get_logger(__name__)


def resolve_total_capital(settings) -> Decimal:  # noqa: ANN001
    """Real account USDT in live mode; ``backtest.initial_capital`` otherwise."""
    if settings.live_trading.enabled:
        try:
            from core.tasks._tick import _get_live_total_capital

            capital = _get_live_total_capital(settings)
            if capital > 0:
                logger.info(
                    "backtest.capital: using live account capital",
                    capital=str(capital),
                )
                return capital
        except Exception as exc:
            logger.warning(
                "backtest.capital: live fetch failed, using configured value",
                error=str(exc),
            )
    return Decimal(str(settings.backtest.initial_capital))
