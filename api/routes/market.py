"""
Market data REST endpoints.

Provides candle history and latest-price lookups backed by the
PostgreSQL candle cache.  Mounted at ``/api/market`` by
:func:`api.main._include_routes`.

Endpoints
---------
GET /candles/{symbol}
    Historical OHLCV candles with optional timeframe, limit, and
    date-range filters.

GET /price/{symbol}
    Most recent close price for a symbol/timeframe pair.
"""

from __future__ import annotations

import json as _json
from datetime import datetime
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from core.models import Candle
from db import get_db
from db.models import CandleRecord
from db.utils import orm_to_candle

router = APIRouter()

# ── Constants ────────────────────────────────────────────────────────────────

VALID_TIMEFRAMES: set[str] = {"1m", "5m", "15m", "30m", "1h", "4h", "1d", "1w"}


# ── Response Models ──────────────────────────────────────────────────────────


class PriceResponse(BaseModel):
    """Latest price for a symbol from the candle cache."""

    symbol: str
    price: str  # Decimal as string to preserve precision
    open_time: datetime
    timeframe: str


class Ticker24hItem(BaseModel):
    symbol: str
    price_change_pct: str


class Ticker24hResponse(BaseModel):
    tickers: list[Ticker24hItem]


# ── Helpers ──────────────────────────────────────────────────────────────────


def _validate_timeframe(timeframe: str) -> str:
    """Raise 422 if *timeframe* is not recognised."""
    if timeframe not in VALID_TIMEFRAMES:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid timeframe '{timeframe}'. "
            f"Must be one of: {', '.join(sorted(VALID_TIMEFRAMES))}",
        )
    return timeframe


# ── Endpoints ────────────────────────────────────────────────────────────────


@router.get("/candles/{symbol}", response_model=list[Candle])
def get_candles(
    symbol: str,
    timeframe: str = Query("15m", description="Candle timeframe (e.g. 15m, 1h, 4h)"),
    limit: int = Query(100, ge=1, le=1000, description="Max candles to return"),
    start: Optional[datetime] = Query(None, description="Start time (ISO 8601)"),
    end: Optional[datetime] = Query(None, description="End time (ISO 8601)"),
    db: Session = Depends(get_db),
) -> list[Candle]:
    """
    Return historical OHLCV candles for *symbol*.

    Results are sorted chronologically (oldest first).  When neither
    ``start`` nor ``end`` is given the most recent *limit* candles are
    returned.
    """
    _validate_timeframe(timeframe)
    symbol = symbol.upper()

    stmt = select(CandleRecord).where(
        CandleRecord.symbol == symbol,
        CandleRecord.timeframe == timeframe,
    )

    if start is not None:
        stmt = stmt.where(CandleRecord.open_time >= start)
    if end is not None:
        stmt = stmt.where(CandleRecord.open_time <= end)

    # When no date range filter, return the *latest* candles.
    # Order DESC + limit, then reverse in Python for chronological output.
    if start is None and end is None:
        stmt = stmt.order_by(CandleRecord.open_time.desc()).limit(limit)
        records = list(db.execute(stmt).scalars().all())
        records.reverse()
    else:
        stmt = stmt.order_by(CandleRecord.open_time).limit(limit)
        records = list(db.execute(stmt).scalars().all())

    return [orm_to_candle(r) for r in records]


@router.get("/price/{symbol}", response_model=PriceResponse)
def get_price(
    symbol: str,
    timeframe: str = Query("15m", description="Timeframe to use for latest candle"),
    db: Session = Depends(get_db),
) -> PriceResponse:
    """
    Return the most recent close price for *symbol*.

    The price is sourced from the latest cached candle for the given
    timeframe.  Returns **404** if no candle data exists.
    """
    _validate_timeframe(timeframe)
    symbol = symbol.upper()

    stmt = (
        select(CandleRecord)
        .where(
            CandleRecord.symbol == symbol,
            CandleRecord.timeframe == timeframe,
        )
        .order_by(CandleRecord.open_time.desc())
        .limit(1)
    )

    record = db.execute(stmt).scalars().first()

    if record is None:
        raise HTTPException(
            status_code=404,
            detail=f"No candle data for {symbol} ({timeframe})",
        )

    return PriceResponse(
        symbol=record.symbol,
        price=str(record.close),
        open_time=record.open_time,
        timeframe=record.timeframe,
    )


@router.get("/ticker24h", response_model=Ticker24hResponse)
def get_ticker_24h() -> Ticker24hResponse:
    """Fetch 24h price change percentage for all enabled symbols from Binance."""
    from core.config import get_settings

    symbols = [s.symbol for s in get_settings().enabled_symbols]
    if not symbols:
        return Ticker24hResponse(tickers=[])

    import requests as _requests

    symbol_set = set(symbols)
    try:
        resp = _requests.get(
            "https://api.binance.com/api/v3/ticker/24hr",
            params={"symbols": _json.dumps(symbols, separators=(",", ":"))},
            timeout=10,
        )
        resp.raise_for_status()
        tickers = [
            Ticker24hItem(
                symbol=item["symbol"],
                price_change_pct=item["priceChangePercent"],
            )
            for item in resp.json()
            if item["symbol"] in symbol_set
        ]
    except Exception:
        tickers = []

    return Ticker24hResponse(tickers=tickers)
