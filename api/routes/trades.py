"""
Trade history REST endpoints.

Provides filtered trade listings and aggregate statistics from the
PostgreSQL ``trade_records`` table.  Mounted at ``/api/trades`` by
:func:`api.main._include_routes`.

Endpoints
---------
GET /
    Historical trades with optional run_id, symbol, strategy,
    exit_reason, date-range, and limit filters.

GET /stats
    Aggregate statistics (win rate, PnL, profit factor, …) computed
    over the same filterable trade set.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from core.backtest.metrics import _avg_duration, _consecutive_streaks, _profit_factor
from core.config import get_settings
from core.models import Trade
from db import get_db
from db.models import TradeRecord
from db.utils import orm_to_trade


def _default_trading_mode() -> str:
    settings = get_settings()
    return "live" if settings.live_trading.enabled else "paper"

router = APIRouter()

_ZERO = Decimal("0")
_HUNDRED = Decimal("100")


# ── Response Models ──────────────────────────────────────────────────────────


class TradeStatsResponse(BaseModel):
    """Aggregate statistics for a filtered set of trades."""

    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    win_rate: str = "0"           # percentage as string
    total_pnl: str = "0"         # USDT as string
    avg_win_percent: str = "0"
    avg_loss_percent: str = "0"
    profit_factor: Optional[str] = None   # None when no losers
    avg_duration_minutes: Optional[int] = None
    max_consecutive_wins: int = 0
    max_consecutive_losses: int = 0


# ── Helpers ──────────────────────────────────────────────────────────────────


def _base_query(
    db: Session,
    *,
    run_id: Optional[str],
    trading_mode: Optional[str],
    symbol: Optional[str],
    strategy: Optional[str],
    exit_reason: Optional[str],
    exit_price_estimated: Optional[bool],
    start: Optional[datetime],
    end: Optional[datetime],
    limit: int,
):
    """Build and execute filtered trade query, return list[TradeRecord]."""
    if run_id is not None:
        stmt = select(TradeRecord).where(TradeRecord.run_id == run_id)
    else:
        mode = trading_mode or _default_trading_mode()
        stmt = select(TradeRecord).where(TradeRecord.trading_mode == mode)

    if symbol is not None:
        stmt = stmt.where(TradeRecord.symbol == symbol.upper())
    if strategy is not None:
        stmt = stmt.where(TradeRecord.strategy == strategy)
    if exit_reason is not None:
        stmt = stmt.where(TradeRecord.exit_reason == exit_reason)
    if exit_price_estimated is not None:
        stmt = stmt.where(TradeRecord.exit_price_estimated == exit_price_estimated)

    if start is not None:
        stmt = stmt.where(TradeRecord.exit_time >= start)
    if end is not None:
        stmt = stmt.where(TradeRecord.exit_time <= end)

    # When no date range, return the latest `limit` trades (DESC + reverse).
    if start is None and end is None:
        stmt = stmt.order_by(TradeRecord.exit_time.desc()).limit(limit)
        records = list(db.execute(stmt).scalars().all())
        records.reverse()
    else:
        stmt = stmt.order_by(TradeRecord.exit_time).limit(limit)
        records = list(db.execute(stmt).scalars().all())

    return records


# ── Endpoints ────────────────────────────────────────────────────────────────


@router.get("/", response_model=list[Trade])
def get_trades(
    run_id: Optional[str] = Query(None, description="Backtest run ID (overrides trading_mode filter)"),
    trading_mode: Optional[str] = Query(None, description="Filter by trading mode (paper/live). Defaults to live_trading.enabled setting."),
    symbol: Optional[str] = Query(None, description="Filter by symbol (e.g. BTCUSDT)"),
    strategy: Optional[str] = Query(None, description="Filter by strategy name"),
    exit_reason: Optional[str] = Query(None, description="Filter by exit reason"),
    exit_price_estimated: Optional[bool] = Query(None, description="Filter by whether exit price was estimated (true) or resolved from a real fill (false)"),
    limit: int = Query(500, ge=1, le=10000, description="Max trades to return"),
    start: Optional[datetime] = Query(None, description="Start time filter on exit_time (ISO 8601)"),
    end: Optional[datetime] = Query(None, description="End time filter on exit_time (ISO 8601)"),
    db: Session = Depends(get_db),
) -> list[Trade]:
    """
    Return historical trades.

    Results are sorted chronologically by exit time (oldest first).
    Filtered by ``trading_mode`` (defaults to current config).
    An explicit ``run_id`` overrides the trading_mode filter.
    """
    records = _base_query(
        db,
        run_id=run_id,
        trading_mode=trading_mode,
        symbol=symbol,
        strategy=strategy,
        exit_reason=exit_reason,
        exit_price_estimated=exit_price_estimated,
        start=start,
        end=end,
        limit=limit,
    )
    return [orm_to_trade(r) for r in records]


@router.get("/stats", response_model=TradeStatsResponse)
def get_trade_stats(
    run_id: Optional[str] = Query(None, description="Backtest run ID (overrides trading_mode filter)"),
    trading_mode: Optional[str] = Query(None, description="Filter by trading mode (paper/live). Defaults to live_trading.enabled setting."),
    symbol: Optional[str] = Query(None, description="Filter by symbol (e.g. BTCUSDT)"),
    strategy: Optional[str] = Query(None, description="Filter by strategy name"),
    exit_reason: Optional[str] = Query(None, description="Filter by exit reason"),
    exit_price_estimated: Optional[bool] = Query(None, description="Filter by whether exit price was estimated (true) or resolved from a real fill (false)"),
    limit: int = Query(10000, ge=1, le=10000, description="Max trades to consider"),
    start: Optional[datetime] = Query(None, description="Start time filter on exit_time (ISO 8601)"),
    end: Optional[datetime] = Query(None, description="End time filter on exit_time (ISO 8601)"),
    db: Session = Depends(get_db),
) -> TradeStatsResponse:
    """
    Return aggregate statistics for trades matching the given filters.

    Uses the same filter logic as ``GET /``.  Returns zeroed stats
    when no trades match.
    """
    records = _base_query(
        db,
        run_id=run_id,
        trading_mode=trading_mode,
        symbol=symbol,
        strategy=strategy,
        exit_reason=exit_reason,
        exit_price_estimated=exit_price_estimated,
        start=start,
        end=end,
        limit=limit,
    )

    if not records:
        return TradeStatsResponse()

    trades = [orm_to_trade(r) for r in records]

    winning = [t for t in trades if t.pnl_usdt > _ZERO]
    losing = [t for t in trades if t.pnl_usdt <= _ZERO]

    total = len(trades)
    win_count = len(winning)
    lose_count = len(losing)

    win_rate = Decimal(str(win_count)) / Decimal(str(total)) * _HUNDRED if total > 0 else _ZERO

    total_pnl = sum((t.pnl_usdt for t in trades), _ZERO)

    avg_win_pct = (
        sum(t.pnl_percent for t in winning) / Decimal(str(win_count))
        if win_count > 0
        else _ZERO
    )
    avg_loss_pct = (
        sum(t.pnl_percent for t in losing) / Decimal(str(lose_count))
        if lose_count > 0
        else _ZERO
    )

    pf = _profit_factor(trades)
    avg_dur = _avg_duration(trades)
    max_wins, max_losses = _consecutive_streaks(trades)

    return TradeStatsResponse(
        total_trades=total,
        winning_trades=win_count,
        losing_trades=lose_count,
        win_rate=str(win_rate),
        total_pnl=str(total_pnl),
        avg_win_percent=str(avg_win_pct),
        avg_loss_percent=str(avg_loss_pct),
        profit_factor=str(pf) if pf is not None else None,
        avg_duration_minutes=avg_dur,
        max_consecutive_wins=max_wins,
        max_consecutive_losses=max_losses,
    )
