"""
Strategy state and signal REST endpoints.

Provides current strategy state and derived signal snapshots from the
PostgreSQL ``strategy_state`` table.  Mounted at ``/api/strategy`` by
:func:`api.main._include_routes`.

Endpoints
---------
GET /state
    Current state-machine status for each symbol/strategy pair.

GET /signals
    Derived signal snapshots with conditions extracted from state_data.
    Signal derivation is basic (always HOLD) until Phase 3 task 3.8
    implements live state persistence.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from core.models import StrategySignalSnapshot, StrategyStateResponse
from db import get_db
from db.models import StrategyStateRecord
from db.utils import orm_to_signal_snapshot, orm_to_strategy_state

_STALE_THRESHOLD = timedelta(hours=2)

router = APIRouter()


# ── Helpers ──────────────────────────────────────────────────────────────────


def _filtered_query(
    db: Session,
    *,
    symbol: Optional[str],
    strategy: Optional[str],
):
    """Build and execute a filtered query on strategy_state, return records."""
    cutoff = datetime.now(timezone.utc) - _STALE_THRESHOLD
    stmt = select(StrategyStateRecord).where(StrategyStateRecord.updated_at >= cutoff)

    if symbol is not None:
        stmt = stmt.where(StrategyStateRecord.symbol == symbol.upper())
    if strategy is not None:
        stmt = stmt.where(StrategyStateRecord.strategy == strategy)

    stmt = stmt.order_by(StrategyStateRecord.symbol, StrategyStateRecord.strategy)
    return list(db.execute(stmt).scalars().all())


# ── Endpoints ────────────────────────────────────────────────────────────────


@router.get("/state", response_model=list[StrategyStateResponse])
def get_strategy_state(
    symbol: Optional[str] = Query(None, description="Filter by symbol (e.g. BTCUSDT)"),
    strategy: Optional[str] = Query(None, description="Filter by strategy name"),
    db: Session = Depends(get_db),
) -> list[StrategyStateResponse]:
    """
    Return current state-machine status for each symbol/strategy pair.

    Results are ordered by symbol then strategy.
    """
    records = _filtered_query(db, symbol=symbol, strategy=strategy)
    return [orm_to_strategy_state(r) for r in records]


@router.get("/signals", response_model=list[StrategySignalSnapshot])
def get_strategy_signals(
    symbol: Optional[str] = Query(None, description="Filter by symbol (e.g. BTCUSDT)"),
    strategy: Optional[str] = Query(None, description="Filter by strategy name"),
    db: Session = Depends(get_db),
) -> list[StrategySignalSnapshot]:
    """
    Return derived signal snapshots for each symbol/strategy pair.

    Results are ordered by most recently updated first.
    """
    cutoff = datetime.now(timezone.utc) - _STALE_THRESHOLD
    stmt = select(StrategyStateRecord).where(StrategyStateRecord.updated_at >= cutoff)
    if symbol is not None:
        stmt = stmt.where(StrategyStateRecord.symbol == symbol.upper())
    if strategy is not None:
        stmt = stmt.where(StrategyStateRecord.strategy == strategy)
    stmt = stmt.order_by(StrategyStateRecord.id.desc())
    records = list(db.execute(stmt).scalars().all())
    return [orm_to_signal_snapshot(r) for r in records]
