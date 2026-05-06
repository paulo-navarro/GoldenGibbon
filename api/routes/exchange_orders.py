"""
Exchange orders REST endpoint (task 6.8.6).

Lists all orders relevant to the exchange account — both GG-created
and orphan-detected — with a ``source`` field for identification.

Mounted at ``/api/exchange/orders`` by :func:`api.main._include_routes`.

Endpoints
---------
GET /
    All exchange orders with source/symbol/status filters.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from core.models import ExchangeOrder
from db import get_db
from db.models import OrderRecord

router = APIRouter()

_STATUS_MAP = {
    "open": ["pending", "new", "partially_filled"],
    "filled": ["filled"],
    "cancelled": ["cancelled", "rejected", "expired"],
}


def _to_exchange_order(rec: OrderRecord) -> ExchangeOrder:
    source = "orphan" if rec.reconciliation_status == "orphan" else "gg"
    return ExchangeOrder(
        exchange_order_id=rec.exchange_order_id,
        symbol=rec.symbol,
        side=rec.side,
        type=rec.order_type,
        status=rec.exchange_status or rec.status,
        orig_qty=rec.amount,
        executed_qty=rec.filled_amount,
        price=rec.price or rec.avg_fill_price,
        stop_price=rec.limit_price,
        time=rec.created_at,
        source=source,
        intent=rec.intent if source == "gg" else None,
    )


@router.get("/", response_model=list[ExchangeOrder])
def get_exchange_orders(
    source: Optional[str] = Query(None, description="Filter by source: gg / orphan"),
    symbol: Optional[str] = Query(None, description="Filter by symbol (e.g. BTCUSDT)"),
    status: Optional[str] = Query(None, description="Filter by status group: open / filled / cancelled"),
    limit: int = Query(500, ge=1, le=5000, description="Max orders to return"),
    offset: int = Query(0, ge=0, description="Offset for pagination"),
    db: Session = Depends(get_db),
) -> list[ExchangeOrder]:
    """Return all exchange-account orders (GG + orphans), newest first."""
    stmt = select(OrderRecord).where(OrderRecord.trading_mode == "live")

    if source == "orphan":
        stmt = stmt.where(OrderRecord.reconciliation_status == "orphan")
    elif source == "gg":
        stmt = stmt.where(OrderRecord.reconciliation_status != "orphan")

    if symbol is not None:
        stmt = stmt.where(OrderRecord.symbol == symbol.upper())

    if status is not None:
        db_statuses = _STATUS_MAP.get(status)
        if db_statuses:
            stmt = stmt.where(OrderRecord.status.in_(db_statuses))

    stmt = stmt.order_by(OrderRecord.created_at.desc()).limit(limit).offset(offset)

    records = list(db.execute(stmt).scalars().all())
    return [_to_exchange_order(r) for r in records]
