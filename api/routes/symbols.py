"""
Symbol management REST endpoints.

Mounted at ``/api/config/symbols`` by :func:`api.main._include_routes`.
"""

from __future__ import annotations

import os
from typing import List, Optional

import requests
import structlog
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, field_validator

from core.config import (
    get_settings,
    get_symbol_source,
    delete_symbol,
    reload_settings,
    save_symbol,
)

logger = structlog.get_logger(__name__)
router = APIRouter()

VALID_TIMEFRAMES = {"1m", "5m", "15m", "30m", "1h", "4h", "1d", "1w"}
BINANCE_BASE_URL = os.getenv("BINANCE_BASE_URL", "https://api.binance.com")


# ── Helpers ──────────────────────────────────────────────────────────────────


def _validate_symbol_on_binance(symbol: str) -> bool:
    """Check if a symbol exists on Binance via exchangeInfo."""
    try:
        resp = requests.get(
            f"{BINANCE_BASE_URL}/api/v3/exchangeInfo",
            params={"symbol": symbol},
            timeout=10,
        )
        if resp.status_code != 200:
            return False
        data = resp.json()
        return len(data.get("symbols", [])) > 0
    except Exception as exc:
        logger.warning("symbols.binance_validation_failed", symbol=symbol, error=str(exc))
        return False


# ── Request / Response models ────────────────────────────────────────────────


class SymbolItem(BaseModel):
    symbol: str
    exchange: str
    timeframes: List[str]
    enabled: bool
    description: Optional[str] = None
    source: str = "db"


class SymbolListResponse(BaseModel):
    symbols: List[SymbolItem]


class AddSymbolRequest(BaseModel):
    symbol: str
    exchange: str = "binance"
    timeframes: List[str] = ["15m", "1h"]
    enabled: bool = True
    description: Optional[str] = None

    @field_validator("symbol")
    @classmethod
    def validate_symbol(cls, v: str) -> str:
        v = v.upper().strip()
        if not v or not v.endswith("USDT"):
            raise ValueError("Symbol must end with USDT (e.g. BTCUSDT)")
        return v

    @field_validator("timeframes")
    @classmethod
    def validate_timeframes(cls, v: List[str]) -> List[str]:
        for tf in v:
            if tf not in VALID_TIMEFRAMES:
                raise ValueError(f"Invalid timeframe: {tf}. Must be one of {VALID_TIMEFRAMES}")
        return v


class AddSymbolResponse(BaseModel):
    symbol: SymbolItem
    message: str


class PatchSymbolRequest(BaseModel):
    enabled: Optional[bool] = None
    timeframes: Optional[List[str]] = None
    description: Optional[str] = None

    @field_validator("timeframes")
    @classmethod
    def validate_timeframes(cls, v: Optional[List[str]]) -> Optional[List[str]]:
        if v is not None:
            for tf in v:
                if tf not in VALID_TIMEFRAMES:
                    raise ValueError(f"Invalid timeframe: {tf}. Must be one of {VALID_TIMEFRAMES}")
        return v


# ── Endpoints ────────────────────────────────────────────────────────────────


@router.get("", response_model=SymbolListResponse)
def list_symbols() -> SymbolListResponse:
    """Return all symbols (enabled + disabled) with source label."""
    settings = get_settings()
    items: List[SymbolItem] = []
    for s in settings.symbols:
        items.append(SymbolItem(
            symbol=s.symbol,
            exchange=s.exchange,
            timeframes=s.timeframes,
            enabled=s.enabled,
            description=s.description,
            source=get_symbol_source(s.symbol),
        ))
    return SymbolListResponse(symbols=items)


@router.post("", response_model=AddSymbolResponse, status_code=201)
def add_symbol(req: AddSymbolRequest) -> AddSymbolResponse:
    """Add a new trading pair. Validates it exists on Binance, rejects duplicates."""
    settings = get_settings()
    existing = {s.symbol for s in settings.symbols}
    if req.symbol in existing:
        raise HTTPException(status_code=409, detail=f"Symbol {req.symbol} already exists")

    if not _validate_symbol_on_binance(req.symbol):
        raise HTTPException(status_code=400, detail=f"Symbol {req.symbol} not found on Binance")

    save_symbol(
        symbol=req.symbol,
        exchange=req.exchange,
        timeframes=req.timeframes,
        enabled=req.enabled,
        description=req.description,
    )
    reload_settings()

    item = SymbolItem(
        symbol=req.symbol,
        exchange=req.exchange,
        timeframes=req.timeframes,
        enabled=req.enabled,
        description=req.description,
        source="db",
    )
    return AddSymbolResponse(symbol=item, message=f"{req.symbol} added successfully")


@router.delete("/{symbol}")
def remove_symbol(symbol: str):
    """Remove a trading pair. Blocks if open positions exist for that symbol."""
    symbol = symbol.upper().strip()

    settings = get_settings()
    existing = {s.symbol for s in settings.symbols}
    if symbol not in existing:
        raise HTTPException(status_code=404, detail=f"Symbol {symbol} not found")

    # Check for open positions
    try:
        from db import get_session
        from db.models import PositionRecord
        from sqlalchemy import select, func

        with get_session() as session:
            count = session.execute(
                select(func.count()).select_from(PositionRecord).where(
                    PositionRecord.symbol == symbol
                )
            ).scalar() or 0

            if count > 0:
                raise HTTPException(
                    status_code=409,
                    detail=f"Cannot remove {symbol}: {count} open position(s) exist. Close them first.",
                )
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("symbols.position_check_failed", symbol=symbol, error=str(exc))

    source = get_symbol_source(symbol)
    if source == "default":
        save_symbol(symbol=symbol, enabled=False)
        reload_settings()
        return {"message": f"{symbol} disabled (built-in default, cannot delete)"}

    delete_symbol(symbol)
    reload_settings()
    return {"message": f"{symbol} removed successfully"}


@router.patch("/{symbol}", response_model=SymbolItem)
def update_symbol(symbol: str, req: PatchSymbolRequest):
    """Update a symbol's enabled flag, timeframes, or description."""
    symbol = symbol.upper().strip()

    settings = get_settings()
    current = next((s for s in settings.symbols if s.symbol == symbol), None)
    if current is None:
        raise HTTPException(status_code=404, detail=f"Symbol {symbol} not found")

    enabled = req.enabled if req.enabled is not None else current.enabled
    timeframes = req.timeframes if req.timeframes is not None else current.timeframes
    description = req.description if req.description is not None else current.description

    save_symbol(
        symbol=symbol,
        exchange=current.exchange,
        timeframes=timeframes,
        enabled=enabled,
        description=description,
    )
    reload_settings()

    return SymbolItem(
        symbol=symbol,
        exchange=current.exchange,
        timeframes=timeframes,
        enabled=enabled,
        description=description,
        source=get_symbol_source(symbol),
    )
