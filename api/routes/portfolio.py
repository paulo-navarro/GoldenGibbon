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

from core.models import Position, PortfolioSnapshot
from db import get_db
from db.models import PositionRecord, TradeRecord
from db.models import PortfolioSnapshot as PortfolioSnapshotRecord
from db.utils import orm_to_position, orm_to_portfolio_snapshot

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
    db: Session = Depends(get_db),
) -> PortfolioResponse:
    """
    Return the current portfolio state.

    Combines open positions from the ``positions`` table with the
    latest ``portfolio_snapshots`` row for balance and equity figures.
    When no snapshot exists yet, all monetary fields default to ``"0"``.
    """
    # Fetch open positions
    pos_stmt = select(PositionRecord).order_by(PositionRecord.entry_time)
    position_records = list(db.execute(pos_stmt).scalars().all())
    positions = [orm_to_position(r) for r in position_records]

    # Fetch latest snapshot for balance/equity summary
    snap_stmt = (
        select(PortfolioSnapshotRecord)
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

    total_pnl = Decimal("0")
    if snapshot.run_id:
        total_pnl = (
            db.execute(
                select(func.sum(TradeRecord.pnl_usdt)).where(
                    TradeRecord.run_id == snapshot.run_id
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
        None, description="Backtest run ID (defaults to latest run)"
    ),
    limit: int = Query(
        500, ge=1, le=10000, description="Max snapshots to return"
    ),
    start: Optional[datetime] = Query(None, description="Start time (ISO 8601)"),
    end: Optional[datetime] = Query(None, description="End time (ISO 8601)"),
    db: Session = Depends(get_db),
) -> list[PortfolioSnapshot]:
    """
    Return equity-curve snapshots for a backtest run.

    Results are sorted chronologically (oldest first).  When no
    ``run_id`` is given, the most recent run is used.  When neither
    ``start`` nor ``end`` is supplied the most recent *limit* snapshots
    are returned.
    """
    # Resolve run_id — default to the latest run
    effective_run_id = run_id
    if effective_run_id is None:
        latest_stmt = (
            select(PortfolioSnapshotRecord.run_id)
            .where(PortfolioSnapshotRecord.run_id.is_not(None))
            .order_by(PortfolioSnapshotRecord.timestamp.desc())
            .limit(1)
        )
        row = db.execute(latest_stmt).scalars().first()
        if row is None:
            return []
        effective_run_id = row

    stmt = select(PortfolioSnapshotRecord).where(
        PortfolioSnapshotRecord.run_id == effective_run_id,
    )

    if start is not None:
        stmt = stmt.where(PortfolioSnapshotRecord.timestamp >= start)
    if end is not None:
        stmt = stmt.where(PortfolioSnapshotRecord.timestamp <= end)

    # When no date range, return latest snapshots (DESC + limit + reverse).
    if start is None and end is None:
        stmt = stmt.order_by(PortfolioSnapshotRecord.timestamp.desc()).limit(limit)
        records = list(db.execute(stmt).scalars().all())
        records.reverse()
    else:
        stmt = stmt.order_by(PortfolioSnapshotRecord.timestamp).limit(limit)
        records = list(db.execute(stmt).scalars().all())

    return [orm_to_portfolio_snapshot(r) for r in records]
