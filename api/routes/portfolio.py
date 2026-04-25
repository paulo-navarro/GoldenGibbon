"""
Portfolio REST endpoints.

Provides current portfolio state (balance + open positions) and
historical equity-curve snapshots from the PostgreSQL database.
Mounted at ``/api/portfolio`` by :func:`api.main._include_routes`.

Endpoints
---------
GET /
    Current portfolio summary with open positions.

GET /equity-curve
    Historical equity-curve snapshots with optional run_id,
    limit, and date-range filters.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from core.config import get_settings
from core.models import Position, PortfolioSnapshot
from db import get_db
from db.models import PositionRecord, TradeRecord
from db.models import PortfolioSnapshot as PortfolioSnapshotRecord
from db.utils import orm_to_position, orm_to_portfolio_snapshot


def _default_trading_mode() -> str:
    settings = get_settings()
    return "live" if settings.live_trading.enabled else "paper"

router = APIRouter()


# ── Response Models ──────────────────────────────────────────────────────────


class PortfolioResponse(BaseModel):
    """Current portfolio state assembled from DB tables."""

    usdt_balance: str          # Decimal as string
    equity: str                # Decimal as string
    positions_value: str       # Decimal as string
    total_pnl: str             # Decimal as string
    open_positions_count: int
    positions: list[Position]
    last_updated: Optional[datetime] = None


# ── Endpoints ────────────────────────────────────────────────────────────────


@router.get("/", response_model=PortfolioResponse)
def get_portfolio(
    trading_mode: Optional[str] = Query(None, description="Filter by trading mode (paper/live). Defaults to live_trading.enabled setting."),
    db: Session = Depends(get_db),
) -> PortfolioResponse:
    """
    Return the current portfolio state.

    Combines open positions from the ``positions`` table with the
    latest ``portfolio_snapshots`` row for balance and equity figures.
    When no snapshot exists yet, all monetary fields default to ``"0"``.
    """
    mode = trading_mode or _default_trading_mode()

    # Fetch open positions
    pos_stmt = select(PositionRecord).order_by(PositionRecord.entry_time)
    position_records = list(db.execute(pos_stmt).scalars().all())
    positions = [orm_to_position(r) for r in position_records]

    # Fetch latest snapshot for balance/equity summary, filtered by trading_mode
    snap_stmt = (
        select(PortfolioSnapshotRecord)
        .where(PortfolioSnapshotRecord.trading_mode == mode)
        .order_by(PortfolioSnapshotRecord.timestamp.desc())
        .limit(1)
    )
    snapshot = db.execute(snap_stmt).scalars().first()

    if snapshot is None:
        return PortfolioResponse(
            usdt_balance="0",
            equity="0",
            positions_value="0",
            total_pnl="0",
            open_positions_count=len(positions),
            positions=positions,
            last_updated=None,
        )

    total_pnl = (
        db.execute(
            select(func.sum(TradeRecord.pnl_usdt)).where(
                TradeRecord.trading_mode == mode
            )
        ).scalar()
        or Decimal("0")
    )

    return PortfolioResponse(
        usdt_balance=str(snapshot.usdt_balance),
        equity=str(snapshot.total_equity),
        positions_value=str(snapshot.positions_value),
        total_pnl=str(total_pnl),
        open_positions_count=len(positions),
        positions=positions,
        last_updated=snapshot.timestamp,
    )


@router.get("/equity-curve", response_model=list[PortfolioSnapshot])
def get_equity_curve(
    run_id: Optional[str] = Query(
        None, description="Backtest run ID (overrides trading_mode filter)"
    ),
    trading_mode: Optional[str] = Query(None, description="Filter by trading mode (paper/live). Defaults to live_trading.enabled setting."),
    limit: int = Query(
        500, ge=1, le=10000, description="Max snapshots to return"
    ),
    start: Optional[datetime] = Query(None, description="Start time (ISO 8601)"),
    end: Optional[datetime] = Query(None, description="End time (ISO 8601)"),
    db: Session = Depends(get_db),
) -> list[PortfolioSnapshot]:
    """
    Return equity-curve snapshots.

    Results are sorted chronologically (oldest first).  Filtered by
    ``trading_mode`` (defaults to current config).  An explicit
    ``run_id`` overrides the trading_mode filter.  When neither
    ``start`` nor ``end`` is supplied the most recent *limit* snapshots
    are returned.
    """
    if run_id is not None:
        stmt = select(PortfolioSnapshotRecord).where(
            PortfolioSnapshotRecord.run_id == run_id,
        )
    else:
        mode = trading_mode or _default_trading_mode()
        stmt = select(PortfolioSnapshotRecord).where(
            PortfolioSnapshotRecord.trading_mode == mode,
        )

    if start is not None:
        stmt = stmt.where(PortfolioSnapshotRecord.timestamp >= start)
    if end is not None:
        stmt = stmt.where(PortfolioSnapshotRecord.timestamp <= end)

    if start is None and end is None:
        stmt = stmt.order_by(PortfolioSnapshotRecord.timestamp.desc()).limit(limit)
        records = list(db.execute(stmt).scalars().all())
        records.reverse()
    else:
        stmt = stmt.order_by(PortfolioSnapshotRecord.timestamp).limit(limit)
        records = list(db.execute(stmt).scalars().all())

    return [orm_to_portfolio_snapshot(r) for r in records]
