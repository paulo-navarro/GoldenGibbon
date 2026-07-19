"""
DB persistence and alerting helpers for tick execution.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict

import structlog

from core.tasks._state import _TickComponents, _MAX_MEMORY_TRADES, _MAX_MEMORY_SNAPSHOTS

logger = structlog.get_logger(__name__)


# ── DB persistence helpers (tasks 3.7 / 3.8) ────────────────────────────────


def _persist_tick_results(
    session,  # noqa: ANN001 – SQLAlchemy Session
    comp: _TickComponents,
    strategy_name: str,
    symbol: str,
    execution_result,  # noqa: ANN001 – Optional[ExecutionResult]
    snapshot,  # noqa: ANN001 – PortfolioSnapshot
    signal_value: str,
    candle_time: datetime,
) -> None:
    """
    Persist execution audit trail and state snapshot to the DB.

    Called once per tick after execution + snapshot.  Writes:

    * OrderRecord (if an order was filled)
    * TradeRecord (if a trade closed)
    * PortfolioSnapshot
    * PositionRecord (upsert or delete)
    * StrategyStateRecord (upsert)
    """
    from db.models import (
        OrderRecord,
        PositionRecord,
        PortfolioSnapshot as PortfolioSnapshotORM,
        StrategyStateRecord,
    )
    from db.utils import (
        order_to_orm,
        portfolio_snapshot_to_orm,
        position_to_orm,
        trade_to_orm,
    )

    run_id = comp.run_id

    # ── 1. Order + Trade audit trail ─────────────────────────────────
    if execution_result is not None:
        # Check if write-ahead already created an OrderRecord for this order
        existing_order_rec = None
        if execution_result.order.exchange_order_id:
            existing_order_rec = (
                session.query(OrderRecord)
                .filter_by(exchange_order_id=execution_result.order.exchange_order_id)
                .first()
            )

        if existing_order_rec is None:
            order_rec = order_to_orm(execution_result.order, run_id=run_id, trading_mode=comp.trading_mode)
            session.add(order_rec)
        else:
            # Write-ahead record exists — update it with final fill data
            o = execution_result.order
            existing_order_rec.status = o.status.value
            existing_order_rec.filled_amount = o.filled_amount
            existing_order_rec.avg_fill_price = o.avg_fill_price
            existing_order_rec.fee_usdt = o.fee_usdt
            existing_order_rec.slippage_percent = o.slippage_percent
            existing_order_rec.exchange_status = o.exchange_status
            existing_order_rec.filled_at = o.filled_at
            existing_order_rec.run_id = run_id
            if o.reject_reason:
                existing_order_rec.reject_reason = o.reject_reason

        if execution_result.trade is not None:
            trade_rec = trade_to_orm(execution_result.trade, run_id=run_id, trading_mode=comp.trading_mode)
            session.add(trade_rec)

    # ── 2. Portfolio snapshot ────────────────────────────────────────
    # Task 9.11: upsert on (run_id, timestamp) — a re-run of the same tick
    # (acks_late redelivery, lock race) must not duplicate the row. Prod
    # data showed 2× rows at whole-hour timestamps doubling the curve.
    existing_snap = (
        session.query(PortfolioSnapshotORM)
        .filter_by(run_id=run_id, timestamp=snapshot.timestamp)
        .first()
    )
    if existing_snap is None:
        snap_rec = portfolio_snapshot_to_orm(snapshot, run_id=run_id, trading_mode=comp.trading_mode)
        session.add(snap_rec)
    else:
        existing_snap.usdt_balance = snapshot.usdt_balance
        existing_snap.positions_value = snapshot.positions_value
        existing_snap.total_equity = snapshot.total_equity
        existing_snap.daily_pnl = snapshot.daily_pnl
        existing_snap.total_pnl = snapshot.total_pnl
        existing_snap.open_positions_count = snapshot.open_positions_count
        existing_snap.trading_mode = comp.trading_mode

    # ── 3. Position upsert / delete ──────────────────────────────────
    existing_pos = (
        session.query(PositionRecord)
        .filter_by(symbol=symbol, strategy=strategy_name)
        .first()
    )

    if comp.pm.has_position(symbol):
        position = comp.pm.get_position(symbol)
        if existing_pos is not None:
            existing_pos.side = position.side.value if hasattr(position.side, 'value') else position.side
            existing_pos.size = position.size
            existing_pos.entry_price = position.entry_price
            existing_pos.entry_time = position.entry_time
            existing_pos.highest_close = position.highest_close
            existing_pos.trailing_stop_price = position.trailing_stop_price
            existing_pos.hard_stop_price = position.hard_stop_price
            existing_pos.exchange_stop_order_id = position.exchange_stop_order_id
            existing_pos.scale_in_count = position.scale_in_count
            existing_pos.buy_signal_candles = position.buy_signal_candles
        else:
            new_pos = position_to_orm(position)
            session.add(new_pos)
    else:
        # Position closed — remove from DB
        if existing_pos is not None:
            session.delete(existing_pos)

    # ── 4. Strategy state upsert ─────────────────────────────────────
    state_data = {
        "run_id": run_id,
        "signal": signal_value,
        "usdt_balance": str(comp.pm.portfolio.usdt_balance),
        "equity": str(comp.pm.portfolio.equity),
        "total_pnl": str(comp.pm.portfolio.total_pnl),
    }

    # Strategy-specific state via plugin interface
    state_data.update(comp.strategy.to_state_dict())

    # Conditions
    if hasattr(comp.strategy, "conditions") and comp.strategy.conditions is not None:
        state_data["conditions"] = comp.strategy.conditions.model_dump(mode="json")

    # Kill-switch state (task 4.5)
    if comp.kill_switch is not None:
        state_data.update(comp.kill_switch.to_dict(trading_mode=comp.trading_mode))

    buy_candles = comp.risk_engine.get_buy_signal_candles(symbol)

    # Derive last_exit_time from most recent trade
    last_exit: datetime | None = None
    if comp.pm.portfolio.trade_history:
        last_exit = comp.pm.portfolio.trade_history[-1].exit_time

    existing_state = (
        session.query(StrategyStateRecord)
        .filter_by(symbol=symbol, strategy=strategy_name)
        .first()
    )
    if existing_state is not None:
        existing_state.state = comp.strategy.state.value
        existing_state.consecutive_buy_candles = buy_candles
        existing_state.state_data = state_data
        existing_state.last_exit_time = last_exit
        existing_state.updated_at = datetime.now(timezone.utc)
    else:
        new_state = StrategyStateRecord(
            symbol=symbol,
            strategy=strategy_name,
            state=comp.strategy.state.value,
            consecutive_buy_candles=buy_candles,
            state_data=state_data,
            last_exit_time=last_exit,
        )
        session.add(new_state)



def _trim_in_memory_history(pm) -> None:  # noqa: ANN001
    """
    Trim in-memory trade history and equity curve to bounded sizes.

    Older entries are persisted in the DB; no need to keep them in RAM
    for a long-running paper session.
    """
    if len(pm.portfolio.trade_history) > _MAX_MEMORY_TRADES:
        pm._portfolio.trade_history = pm._portfolio.trade_history[-_MAX_MEMORY_TRADES:]
    if len(pm.portfolio.equity_curve) > _MAX_MEMORY_SNAPSHOTS:
        pm._portfolio.equity_curve = pm._portfolio.equity_curve[-_MAX_MEMORY_SNAPSHOTS:]



def _emergency_position_cleanup(
    strategy_name: str,
    symbol: str,
    new_state: str,
    log,  # noqa: ANN001
) -> None:
    """
    Last-resort cleanup when the full persist fails after a sell.

    Opens a minimal, independent session to delete the stale
    ``PositionRecord`` and update ``StrategyStateRecord.state`` so that
    a worker restart doesn't resurrect the position.
    """
    from db import get_session
    from db.models import PositionRecord, StrategyStateRecord

    with get_session() as session:
        pos = (
            session.query(PositionRecord)
            .filter_by(symbol=symbol, strategy=strategy_name)
            .first()
        )
        if pos is not None:
            session.delete(pos)

        state_rec = (
            session.query(StrategyStateRecord)
            .filter_by(symbol=symbol, strategy=strategy_name)
            .first()
        )
        if state_rec is not None and state_rec.state in ("position", "reduced"):
            state_rec.state = new_state

    log.warning(
        "single_tick: emergency position cleanup succeeded",
        strategy=strategy_name,
        symbol=symbol,
        new_state=new_state,
    )




# ── Alerting helper (task 4.9) ─────────────────────────────────────────────


def _send_tick_alerts(
    *,
    strategy_name: str,
    symbol: str,
    execution_result,  # noqa: ANN001 – Optional[ExecutionResult]
    stop_hit: bool,
    stop_type: str | None,
    close: Decimal,
    comp: _TickComponents,
    settings,  # noqa: ANN001 – Settings
) -> None:
    """
    Send Telegram alerts for notable events in the current tick.

    Called once per tick after persistence.  Checks ``AlertingConfig``
    flags before sending.  Never raises.
    """
    cfg = settings.alerting
    if not cfg.enabled:
        return

    try:
        from core.alerting import get_alerter

        alerter = get_alerter()
        if not alerter.enabled:
            return

        # ── Stop hit ────────────────────────────────────────────
        if stop_hit and cfg.alert_on_stop:
            alerter.alert_stop(
                symbol=symbol,
                stop_type=stop_type or "unknown",
                price=str(close),
                strategy=strategy_name,
            )

        # ── Order fill ──────────────────────────────────────────
        if execution_result is not None and cfg.alert_on_fill:
            order = execution_result.order
            pnl_str = None
            if execution_result.trade is not None:
                pnl_str = f"{execution_result.trade.pnl_usdt} USDT"
            alerter.alert_fill(
                symbol=symbol,
                side=order.side.value,
                size=str(order.filled_amount),
                price=str(order.avg_fill_price or order.price or close),
                strategy=strategy_name,
                pnl=pnl_str,
            )

        # ── Kill-switch just triggered ──────────────────────────
        if (
            cfg.alert_on_kill_switch
            and comp.kill_switch
            and comp.kill_switch.is_triggered
        ):
            alerter.alert_kill_switch(
                reason=comp.kill_switch.trigger_reason or "unknown",
                equity=str(comp.pm.portfolio.equity),
                peak_equity=str(comp.kill_switch.peak_equity),
            )

    except Exception as exc:
        logger.debug("alerting: send failed (non-fatal)", error=str(exc))

