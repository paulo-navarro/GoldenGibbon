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

POST /kill-switch/{symbol}/{strategy}/reset
    Reset the kill-switch for a single symbol/strategy pair.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

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


# ── Kill-switch reset ────────────────────────────────────────────────────────


@router.post("/kill-switch/{symbol}/{strategy}/reset")
def reset_kill_switch(
    symbol: str,
    strategy: str,
    db: Session = Depends(get_db),
) -> dict:
    """
    Reset the kill-switch for a single symbol/strategy pair.

    Clears the triggered flag and peak equity in the DB.  The running
    Celery worker detects the DB change on the next tick and syncs its
    in-memory state automatically.
    """
    rec = (
        db.query(StrategyStateRecord)
        .filter_by(symbol=symbol.upper(), strategy=strategy)
        .first()
    )
    if rec is None:
        raise HTTPException(
            status_code=404,
            detail=f"No state record for {strategy}:{symbol}",
        )

    data = rec.state_data or {}
    data.update(
        {
            "kill_switch_triggered": False,
            "kill_switch_reason": None,
            "kill_switch_trigger_time": None,
            "kill_switch_peak_equity": "0",
            "kill_switch_day_start_equity": "0",
            "kill_switch_current_day": None,
            "kill_switch_trading_mode": data.get("kill_switch_trading_mode", "live"),
        }
    )
    rec.state_data = {**data}
    flag_modified(rec, "state_data")
    db.commit()

    return {"status": "ok", "symbol": symbol.upper(), "strategy": strategy}
