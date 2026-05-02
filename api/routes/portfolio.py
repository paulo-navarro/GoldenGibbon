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
from core.models import ExitProximityResponse, Position, PortfolioSnapshot
from core.risk.proximity import compute_exit_proximity
from db import get_db
from db.models import CandleRecord, PositionRecord, StrategyStateRecord, TradeRecord
from db.models import PortfolioSnapshot as PortfolioSnapshotRecord
from db.utils import candles_to_dataframe, orm_to_position, orm_to_portfolio_snapshot


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

    # Calculate positions_value from actual open positions using latest candle close
    positions_value = Decimal("0")
    if position_records:
        position_symbols = list({r.symbol for r in position_records})
        # Get latest close price per symbol
        latest_prices: dict[str, Decimal] = {}
        for sym in position_symbols:
            latest_candle = db.execute(
                select(CandleRecord.close)
                .where(CandleRecord.symbol == sym)
                .order_by(CandleRecord.open_time.desc())
                .limit(1)
            ).scalar()
            if latest_candle is not None:
                latest_prices[sym] = latest_candle

        for rec in position_records:
            price = latest_prices.get(rec.symbol, rec.entry_price)
            positions_value += rec.size * price

    # Get USDT balance: for live mode, query Binance; for paper, use strategy state/snapshots.
    usdt_balance = Decimal("0")
    last_updated: datetime | None = None

    if mode == "live":
        # Live mode: get real USDT balance from exchange
        try:
            from core.execution.binance import BinanceExecutor
            from core.portfolio import PortfolioManager as _PM

            _pm = _PM(initial_capital=Decimal("0"))
            _executor = BinanceExecutor.from_settings(
                strategy_name="_portfolio_query", portfolio_manager=_pm,
            )
            account = _executor.get_account_info()
            balances = account.get("balances", {})
            usdt_bal = balances.get("USDT", {})
            usdt_balance = usdt_bal.get("free", Decimal("0")) + usdt_bal.get("locked", Decimal("0"))
        except Exception:
            # Fallback to latest snapshot if Binance is unreachable
            snap_stmt = (
                select(PortfolioSnapshotRecord)
                .where(PortfolioSnapshotRecord.trading_mode == "live")
                .order_by(PortfolioSnapshotRecord.timestamp.desc())
                .limit(1)
            )
            snapshot = db.execute(snap_stmt).scalars().first()
            if snapshot is not None:
                usdt_balance = snapshot.usdt_balance
                last_updated = snapshot.timestamp
    else:
        # Paper mode: aggregate from strategy state records, fallback to snapshot
        state_records = list(db.execute(select(StrategyStateRecord)).scalars().all())
        has_state_balance = False
        for sr in state_records:
            data = sr.state_data or {}
            bal = data.get("usdt_balance")
            if bal is not None:
                usdt_balance += Decimal(str(bal))
                has_state_balance = True
            if last_updated is None or sr.updated_at > last_updated:
                last_updated = sr.updated_at

        if not has_state_balance:
            snap_stmt = (
                select(PortfolioSnapshotRecord)
                .where(PortfolioSnapshotRecord.trading_mode == mode)
                .order_by(PortfolioSnapshotRecord.timestamp.desc())
                .limit(1)
            )
            snapshot = db.execute(snap_stmt).scalars().first()
            if snapshot is not None:
                usdt_balance = snapshot.usdt_balance
                last_updated = snapshot.timestamp

    equity = usdt_balance + positions_value

    total_pnl = (
        db.execute(
            select(func.sum(TradeRecord.pnl_usdt)).where(
                TradeRecord.trading_mode == mode
            )
        ).scalar()
        or Decimal("0")
    )

    return PortfolioResponse(
        usdt_balance=str(usdt_balance),
        equity=str(equity),
        positions_value=str(positions_value),
        total_pnl=str(total_pnl),
        open_positions_count=len(positions),
        positions=positions,
        last_updated=last_updated,
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


@router.get("/exit-proximity", response_model=list[ExitProximityResponse])
def get_exit_proximity(
    db: Session = Depends(get_db),
) -> list[ExitProximityResponse]:
    """
    Return exit proximity data for every open position.

    For each position, calculates how close the current price is to each
    exit trigger (hard stop, trailing stop, time stop).
    """
    settings = get_settings()

    position_records = list(
        db.execute(select(PositionRecord).order_by(PositionRecord.entry_time)).scalars().all()
    )
    if not position_records:
        return []

    positions = [orm_to_position(r) for r in position_records]
    results: list[ExitProximityResponse] = []

    for pos in positions:
        # Load last 250 candles for indicator calculation
        candle_records = list(
            db.execute(
                select(CandleRecord)
                .where(CandleRecord.symbol == pos.symbol, CandleRecord.timeframe == "15m")
                .order_by(CandleRecord.open_time.desc())
                .limit(250)
            ).scalars().all()
        )
        if not candle_records:
            continue
        candle_records.reverse()

        close = Decimal(str(candle_records[-1].close))
        df = candles_to_dataframe(candle_records)

        # Strategy config as dict for indicator engine
        strat_cfg = settings.strategies.get_strategy_config(pos.strategy)
        cfg_dict = strat_cfg.model_dump() if strat_cfg is not None else {}

        # Time stop info (MR only)
        candles_held: Optional[int] = None
        time_stop_candles: Optional[int] = None
        if pos.strategy == "mean_reversion":
            time_stop_candles = cfg_dict.get("time_stop_candles", 16)
            candles_held = db.execute(
                select(func.count(CandleRecord.id))
                .where(
                    CandleRecord.symbol == pos.symbol,
                    CandleRecord.timeframe == "15m",
                    CandleRecord.open_time >= pos.entry_time,
                )
            ).scalar() or 0

        results.append(compute_exit_proximity(
            symbol=pos.symbol,
            strategy=pos.strategy,
            close=close,
            position_hard_stop=pos.hard_stop_price,
            position_trailing_stop=pos.trailing_stop_price,
            entry_price=pos.entry_price,
            candles_held=candles_held,
            time_stop_candles=time_stop_candles,
            df=df,
            strategy_config=cfg_dict,
        ))

    return results
