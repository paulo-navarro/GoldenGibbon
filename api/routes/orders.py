"""
Order history REST endpoints.

Provides order listings and individual order lookup from the
PostgreSQL ``order_records`` table.  Mounted at ``/api/orders`` by
:func:`api.main._include_routes`.

Endpoints
---------
GET /
    Historical orders with optional run_id, symbol, side, status,
    date-range, and limit filters.

GET /{order_id}
    Single order by database ID.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from core.models import Order
from db import get_db
from db.models import OrderRecord
from db.utils import orm_to_order

router = APIRouter()


# ── Helpers ──────────────────────────────────────────────────────────────────


def _resolve_run_id(db: Session, run_id: Optional[str]) -> Optional[str]:
    """Return *run_id* or the latest run_id from the order_records table."""
    if run_id is not None:
        return run_id
    latest_stmt = (
        select(OrderRecord.run_id)
        .where(OrderRecord.run_id.is_not(None))
        .order_by(OrderRecord.created_at.desc())
        .limit(1)
    )
    return db.execute(latest_stmt).scalars().first()


# ── Endpoints ────────────────────────────────────────────────────────────────


@router.get("/", response_model=list[Order])
def get_orders(
    run_id: Optional[str] = Query(None, description="Backtest/live run ID (defaults to latest)"),
    symbol: Optional[str] = Query(None, description="Filter by symbol (e.g. BTCUSDT)"),
    side: Optional[str] = Query(None, description="Filter by side (buy/sell)"),
    status: Optional[str] = Query(None, description="Filter by status (pending/filled/partial/rejected/cancelled)"),
    limit: int = Query(500, ge=1, le=10000, description="Max orders to return"),
    start: Optional[datetime] = Query(None, description="Start time filter on created_at (ISO 8601)"),
    end: Optional[datetime] = Query(None, description="End time filter on created_at (ISO 8601)"),
    db: Session = Depends(get_db),
) -> list[Order]:
    """
    Return historical orders.

    Results are scoped to a single run_id (defaults to the latest run)
    and sorted chronologically by created_at (oldest first).
    """
    effective_run_id = _resolve_run_id(db, run_id)
    if effective_run_id is None:
        return []

    stmt = select(OrderRecord).where(OrderRecord.run_id == effective_run_id)

    if symbol is not None:
        stmt = stmt.where(OrderRecord.symbol == symbol.upper())
    if side is not None:
        stmt = stmt.where(OrderRecord.side == side.lower())
    if status is not None:
        stmt = stmt.where(OrderRecord.status == status.lower())

    if start is not None:
        stmt = stmt.where(OrderRecord.created_at >= start)
    if end is not None:
        stmt = stmt.where(OrderRecord.created_at <= end)

    # When no date range, return the latest `limit` orders (DESC + reverse).
    if start is None and end is None:
        stmt = stmt.order_by(OrderRecord.created_at.desc()).limit(limit)
        records = list(db.execute(stmt).scalars().all())
        records.reverse()
    else:
        stmt = stmt.order_by(OrderRecord.created_at).limit(limit)
        records = list(db.execute(stmt).scalars().all())

    return [orm_to_order(r) for r in records]


@router.get("/{order_id}", response_model=Order)
def get_order_by_id(
    order_id: int,
    db: Session = Depends(get_db),
) -> Order:
    """Return a single order by its database ID."""
    record = db.get(OrderRecord, order_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Order {order_id} not found")
    return orm_to_order(record)
