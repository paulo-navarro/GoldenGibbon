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

from core.config import get_settings
from core.models import Order
from db import get_db
from db.models import OrderRecord
from db.utils import orm_to_order


def _default_trading_mode() -> str:
    settings = get_settings()
    return "live" if settings.live_trading.enabled else "paper"

router = APIRouter()


# ── Helpers ──────────────────────────────────────────────────────────────────


# ── Endpoints ────────────────────────────────────────────────────────────────


@router.get("/", response_model=list[Order])
def get_orders(
    run_id: Optional[str] = Query(None, description="Backtest/live run ID (overrides trading_mode filter)"),
    trading_mode: Optional[str] = Query(None, description="Filter by trading mode (paper/live). Defaults to live_trading.enabled setting."),
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

    Filtered by ``trading_mode`` (defaults to current config).
    An explicit ``run_id`` overrides the trading_mode filter.
    Sorted chronologically by created_at (oldest first).
    """
    if run_id is not None:
        stmt = select(OrderRecord).where(OrderRecord.run_id == run_id)
    else:
        mode = trading_mode or _default_trading_mode()
        stmt = select(OrderRecord).where(OrderRecord.trading_mode == mode)

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
