"""
Reconciliation status and history REST endpoints (tasks 6.8.4/6.8.5).

Mounted at ``/api/reconciliation`` by :func:`api.main._include_routes`.

Endpoints
---------
GET /status
    Latest reconciliation result + active divergences.

GET /history
    Paginated list of past reconciliation runs with severity/type filters.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from core.models import (
    ReconciliationEvent,
    ReconciliationHistoryEntry,
    ReconciliationStatusResponse,
)
from db import get_db
from db.models import OrderRecord, ReconciliationRunRecord

router = APIRouter()


@router.get("/status", response_model=ReconciliationStatusResponse)
def get_reconciliation_status(
    db: Session = Depends(get_db),
) -> ReconciliationStatusResponse:
    """Return the latest reconciliation run result plus live order counts."""
    latest = (
        db.execute(
            select(ReconciliationRunRecord)
            .order_by(ReconciliationRunRecord.started_at.desc())
            .limit(1)
        )
        .scalars()
        .first()
    )

    pending_orders = db.execute(
        select(func.count()).select_from(OrderRecord).where(
            OrderRecord.status == "pending",
        )
    ).scalar() or 0

    orphan_orders = db.execute(
        select(func.count()).select_from(OrderRecord).where(
            OrderRecord.reconciliation_status == "orphan",
        )
    ).scalar() or 0

    if latest is None:
        return ReconciliationStatusResponse(
            pending_orders=pending_orders,
            orphan_orders=orphan_orders,
        )

    events = [ReconciliationEvent(**e) for e in (latest.events or [])]

    return ReconciliationStatusResponse(
        last_run_at=latest.finished_at,
        status=latest.status,
        pairs_checked=latest.pairs_checked,
        mismatches=latest.mismatches,
        repairs=latest.repairs,
        events=events,
        pending_orders=pending_orders,
        orphan_orders=orphan_orders,
        summary=latest.summary,
    )


@router.get("/history", response_model=list[ReconciliationHistoryEntry])
def get_reconciliation_history(
    severity: Optional[str] = Query(None, description="Min severity filter (info/warning/critical)"),
    event_type: Optional[str] = Query(None, description="Filter runs containing this event type"),
    status: Optional[str] = Query(None, description="Filter by run status (ok/mismatch/error)"),
    start: Optional[datetime] = Query(None, description="Start time filter (ISO 8601)"),
    end: Optional[datetime] = Query(None, description="End time filter (ISO 8601)"),
    limit: int = Query(50, ge=1, le=500, description="Max runs to return"),
    offset: int = Query(0, ge=0, description="Offset for pagination"),
    db: Session = Depends(get_db),
) -> list[ReconciliationHistoryEntry]:
    """Return paginated reconciliation run history, newest first."""
    stmt = select(ReconciliationRunRecord)

    if status is not None:
        stmt = stmt.where(ReconciliationRunRecord.status == status)
    if start is not None:
        stmt = stmt.where(ReconciliationRunRecord.started_at >= start)
    if end is not None:
        stmt = stmt.where(ReconciliationRunRecord.started_at <= end)

    stmt = stmt.order_by(ReconciliationRunRecord.started_at.desc())
    stmt = stmt.limit(limit).offset(offset)

    records = list(db.execute(stmt).scalars().all())

    _SEVERITY_ORDER = {"info": 0, "warning": 1, "critical": 2}

    results = []
    for rec in records:
        events = [ReconciliationEvent(**e) for e in (rec.events or [])]

        if severity is not None:
            threshold = _SEVERITY_ORDER.get(severity, 0)
            if not any(_SEVERITY_ORDER.get(e.severity, 0) >= threshold for e in events):
                continue

        if event_type is not None:
            if not any(e.type == event_type for e in events):
                continue

        results.append(ReconciliationHistoryEntry(
            id=rec.id,
            started_at=rec.started_at,
            finished_at=rec.finished_at,
            status=rec.status,
            pairs_checked=rec.pairs_checked,
            mismatches=rec.mismatches,
            repairs=rec.repairs,
            events=events,
        ))

    return results
