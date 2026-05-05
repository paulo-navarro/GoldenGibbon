"""
Reconciliation Celery task and helpers.

DB-only consistency checks plus exchange reconciliation for live mode.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Dict

import structlog

from core.celery_app import app
from core.tasks._state import _get_strategy_registry, _worker_state, _WorkerStateKey

logger = structlog.get_logger(__name__)


# ── Reconciliation helpers (task 3.9) ────────────────────────────────────────

# Tolerance for trade PnL arithmetic comparison (advisory check).
_PNL_TOLERANCE = Decimal("0.01")


def _reconcile_pair(
    session,  # noqa: ANN001 – SQLAlchemy Session
    strategy_name: str,
    symbol: str,
) -> Dict[str, Any]:
    """
    Run DB-only consistency checks for one ``(strategy, symbol)`` pair.

    **Check A – State ↔ Position consistency**

    * ``POSITION`` / ``REDUCED`` → ``PositionRecord`` must exist.
    * ``FLAT`` / ``COOLDOWN`` → no ``PositionRecord`` should exist.

    **Check B – Balance sanity**

    * ``usdt_balance`` and ``equity`` must be non-negative.
    * When ``FLAT``, ``usdt_balance`` ≈ ``equity``.

    **Check C – Trade PnL arithmetic (advisory)**

    * Sum of ``TradeRecord.pnl_usdt`` for the current ``run_id`` should
      match ``state_data.total_pnl`` within :data:`_PNL_TOLERANCE`.

    Returns a result dict::

        {
            "pair": "strategy:symbol",
            "status": "ok" | "mismatch",
            "checks": [...],
            "repairs": [...],
        }
    """
    from db.models import PositionRecord, StrategyStateRecord, TradeRecord

    pair_label = f"{strategy_name}:{symbol}"
    checks: list[Dict[str, Any]] = []
    repairs: list[str] = []
    has_mismatch = False

    state_rec = (
        session.query(StrategyStateRecord)
        .filter_by(symbol=symbol, strategy=strategy_name)
        .first()
    )
    pos_rec = (
        session.query(PositionRecord)
        .filter_by(symbol=symbol, strategy=strategy_name)
        .first()
    )

    # ── Check A: State ↔ Position ────────────────────────────────────
    if state_rec is None and pos_rec is None:
        checks.append({"check": "state_position", "result": "ok", "detail": "no state, no position"})
    elif state_rec is None and pos_rec is not None:
        # Orphan position with no strategy state at all
        has_mismatch = True
        checks.append({
            "check": "state_position",
            "result": "mismatch",
            "detail": "PositionRecord exists but no StrategyStateRecord",
        })
        session.delete(pos_rec)
        repairs.append("deleted orphan PositionRecord (no strategy state)")
        logger.warning(
            "reconcile: orphan position deleted",
            strategy=strategy_name, symbol=symbol,
        )
    elif state_rec is not None:
        state_value = state_rec.state  # e.g. "position", "flat"
        expects_position = state_value in ("position", "reduced")

        if expects_position and pos_rec is None:
            # State says open position but none in DB → reset to FLAT
            has_mismatch = True
            checks.append({
                "check": "state_position",
                "result": "mismatch",
                "detail": f"state={state_value} but no PositionRecord",
            })
            state_rec.state = "flat"
            if state_rec.state_data:
                state_rec.state_data = {
                    **state_rec.state_data,
                    "cooldown_remaining": 0,
                }
            repairs.append(f"reset strategy state from {state_value} to flat")
            logger.warning(
                "reconcile: state reset to flat (missing position)",
                strategy=strategy_name, symbol=symbol, was=state_value,
            )
        elif not expects_position and pos_rec is not None:
            # State is FLAT/COOLDOWN but a position still exists → delete
            has_mismatch = True
            checks.append({
                "check": "state_position",
                "result": "mismatch",
                "detail": f"state={state_value} but PositionRecord exists",
            })
            session.delete(pos_rec)
            repairs.append(f"deleted orphan PositionRecord (state={state_value})")
            logger.warning(
                "reconcile: orphan position deleted",
                strategy=strategy_name, symbol=symbol, state=state_value,
            )
        else:
            checks.append({"check": "state_position", "result": "ok"})

    # ── Check B: Balance sanity ──────────────────────────────────────
    if state_rec is not None and state_rec.state_data:
        data = state_rec.state_data
        usdt_balance = Decimal(str(data.get("usdt_balance", 0)))
        equity = Decimal(str(data.get("equity", 0)))

        balance_ok = True
        details: list[str] = []

        if usdt_balance < 0:
            balance_ok = False
            details.append(f"negative usdt_balance={usdt_balance}")
        if equity < 0:
            balance_ok = False
            details.append(f"negative equity={equity}")

        # If FLAT, balance should equal equity (no position value)
        state_value = state_rec.state
        if state_value == "flat" and abs(usdt_balance - equity) > _PNL_TOLERANCE:
            balance_ok = False
            details.append(
                f"FLAT but usdt_balance={usdt_balance} != equity={equity}"
            )

        if balance_ok:
            checks.append({"check": "balance_sanity", "result": "ok"})
        else:
            has_mismatch = True
            checks.append({
                "check": "balance_sanity",
                "result": "warning",
                "detail": "; ".join(details),
            })
            logger.warning(
                "reconcile: balance sanity warning",
                strategy=strategy_name, symbol=symbol,
                details=details,
            )
    else:
        checks.append({"check": "balance_sanity", "result": "ok", "detail": "no state data"})

    # ── Check C: Trade PnL arithmetic (advisory) ─────────────────────
    if state_rec is not None and state_rec.state_data:
        data = state_rec.state_data
        run_id = data.get("run_id")
        saved_total_pnl = Decimal(str(data.get("total_pnl", 0)))

        if run_id:
            from sqlalchemy import func as sa_func

            row = (
                session.query(sa_func.coalesce(sa_func.sum(TradeRecord.pnl_usdt), 0))
                .filter_by(run_id=run_id)
                .scalar()
            )
            db_total_pnl = Decimal(str(row))
            delta = abs(saved_total_pnl - db_total_pnl)

            if delta <= _PNL_TOLERANCE:
                checks.append({"check": "trade_pnl", "result": "ok"})
            else:
                has_mismatch = True
                checks.append({
                    "check": "trade_pnl",
                    "result": "warning",
                    "detail": (
                        f"state_data.total_pnl={saved_total_pnl} vs "
                        f"sum(TradeRecord.pnl_usdt)={db_total_pnl} "
                        f"(delta={delta})"
                    ),
                })
                logger.warning(
                    "reconcile: trade PnL drift",
                    strategy=strategy_name, symbol=symbol,
                    saved=str(saved_total_pnl), computed=str(db_total_pnl),
                    delta=str(delta),
                )
        else:
            checks.append({"check": "trade_pnl", "result": "ok", "detail": "no run_id"})
    else:
        checks.append({"check": "trade_pnl", "result": "ok", "detail": "no state data"})

    return {
        "pair": pair_label,
        "status": "mismatch" if has_mismatch else "ok",
        "checks": checks,
        "repairs": repairs,
    }




# ── Exchange reconciliation (task 4.8) ────────────────────────────────────────

# Tolerance for balance/position comparison against exchange.
_EXCHANGE_BALANCE_TOLERANCE = Decimal("0.01")  # USDT
_EXCHANGE_POSITION_TOLERANCE = Decimal("0.00000100")  # asset qty


def _reconcile_with_exchange(
    session,  # noqa: ANN001 – SQLAlchemy Session
    executor,  # noqa: ANN001 – BinanceExecutor
    symbols: list[str],
    *,
    balance_tolerance: Decimal = _EXCHANGE_BALANCE_TOLERANCE,
    position_tolerance: Decimal = _EXCHANGE_POSITION_TOLERANCE,
) -> Dict[str, Any]:
    """
    Compare local DB state against Binance account balances.

    Runs two advisory checks per tracked symbol:

    **Check D – USDT balance**

    * ``state_data.usdt_balance`` ≈ Binance ``USDT`` free balance.

    **Check E – Position size**

    * ``PositionRecord.size`` ≈ Binance base-asset balance (e.g. ``BTC``
      free + locked for ``BTCUSDT``).

    All mismatches are **advisory** — no auto-repair is performed for
    exchange discrepancies.  Differences are logged and published as
    events for dashboard visibility.

    Returns a result dict::

        {
            "status": "ok" | "mismatch",
            "checks": [...],
            "exchange_balances": {...},
        }
    """
    from db.models import PositionRecord, StrategyStateRecord

    checks: list[Dict[str, Any]] = []
    has_mismatch = False

    # Fetch exchange state
    try:
        account = executor.get_account_info()
    except Exception as exc:
        logger.error(
            "reconcile_exchange: failed to fetch account",
            error=str(exc),
        )
        return {
            "status": "error",
            "checks": [{"check": "exchange_account", "result": "error", "detail": str(exc)}],
            "repairs": [],
            "exchange_balances": {},
        }

    exchange_balances = account["balances"]

    # ── Check D: USDT balance ────────────────────────────────────────
    # Aggregate local USDT balance across all strategy state records.
    state_recs = session.query(StrategyStateRecord).all()
    local_usdt_total = Decimal("0")
    for rec in state_recs:
        data = rec.state_data or {}
        local_usdt_total += Decimal(str(data.get("usdt_balance", 0)))

    exchange_usdt = Decimal("0")
    usdt_data = exchange_balances.get("USDT", {})
    if usdt_data:
        exchange_usdt = usdt_data.get("free", Decimal("0")) + usdt_data.get("locked", Decimal("0"))

    usdt_delta = abs(local_usdt_total - exchange_usdt)
    if usdt_delta <= balance_tolerance:
        checks.append({"check": "exchange_usdt", "result": "ok"})
    else:
        has_mismatch = True
        checks.append({
            "check": "exchange_usdt",
            "result": "warning",
            "detail": (
                f"local_usdt={local_usdt_total} vs "
                f"exchange_usdt={exchange_usdt} (delta={usdt_delta})"
            ),
        })
        logger.warning(
            "reconcile_exchange: USDT balance mismatch",
            local=str(local_usdt_total),
            exchange=str(exchange_usdt),
            delta=str(usdt_delta),
        )

    # ── Check E: Position sizes ──────────────────────────────────────
    repairs: list[str] = []

    # Fetch current prices for PnL calculations (best-effort)
    try:
        ticker_prices = executor.get_ticker_prices()
    except Exception:
        ticker_prices = {}

    for symbol in symbols:
        # Derive base asset: BTCUSDT → BTC, ETHUSDT → ETH
        base_asset = symbol.replace("USDT", "")

        # Local position size across all strategies for this symbol
        pos_recs = (
            session.query(PositionRecord)
            .filter_by(symbol=symbol)
            .all()
        )
        local_size = sum((r.size for r in pos_recs), Decimal("0"))

        # Exchange balance for base asset
        exchange_size = Decimal("0")
        if base_asset in exchange_balances:
            exchange_size = (
                exchange_balances[base_asset]["free"]
                + exchange_balances[base_asset]["locked"]
            )

        size_delta = abs(local_size - exchange_size)
        if size_delta <= position_tolerance:
            checks.append({
                "check": f"exchange_position_{symbol}",
                "result": "ok",
            })
        elif pos_recs and exchange_size <= position_tolerance:
            # Exchange has zero/dust but local DB has open position(s).
            # The sell was executed on Binance but the local state was
            # never cleaned up.  Auto-repair: delete stale positions,
            # create emergency TradeRecord, and reset state to FLAT.
            from datetime import datetime, timezone
            from db.models import TradeRecord

            has_mismatch = True
            now = datetime.now(timezone.utc)
            current_price = ticker_prices.get(symbol, None)

            for rec in pos_recs:
                strategy = rec.strategy

                # Create emergency TradeRecord so PnL is not lost
                exit_price = current_price if current_price else rec.entry_price
                pnl = (exit_price - rec.entry_price) * rec.size
                pnl_percent = (
                    (pnl / (rec.entry_price * rec.size)) * 100
                    if rec.entry_price * rec.size > 0
                    else Decimal("0")
                )
                duration_minutes = int((now - rec.entry_time).total_seconds() / 60)

                trade_rec = TradeRecord(
                    symbol=symbol,
                    strategy=strategy,
                    side=rec.side or "long",
                    entry_price=rec.entry_price,
                    exit_price=exit_price,
                    size=rec.size,
                    entry_time=rec.entry_time,
                    exit_time=now,
                    pnl_usdt=pnl,
                    pnl_percent=pnl_percent,
                    duration_minutes=duration_minutes,
                    exit_reason="reconciliation_force_close",
                    trading_mode="live",
                )
                session.add(trade_rec)

                session.delete(rec)

                state_rec = (
                    session.query(StrategyStateRecord)
                    .filter_by(symbol=symbol, strategy=strategy)
                    .first()
                )
                if state_rec is not None and state_rec.state in ("position", "reduced"):
                    old_state = state_rec.state
                    state_rec.state = "flat"
                    if state_rec.state_data:
                        state_data = dict(state_rec.state_data)
                        balance = Decimal(str(state_data.get("usdt_balance", "0")))
                        proceeds = exit_price * rec.size
                        total_pnl = Decimal(str(state_data.get("total_pnl", "0")))
                        state_data["usdt_balance"] = str(balance + proceeds)
                        state_data["total_pnl"] = str(total_pnl + pnl)
                        state_data["cooldown_remaining"] = 0
                        state_rec.state_data = state_data
                    repairs.append(
                        f"force-closed {strategy}:{symbol} from {old_state} to flat "
                        f"(exchange has zero balance, PnL={pnl:.2f} USDT)"
                    )

                # Evict in-memory cache so next tick rebuilds from corrected DB
                _tick_key: _WorkerStateKey = (strategy, symbol)
                if _tick_key in _worker_state:
                    del _worker_state[_tick_key]

                logger.warning(
                    "reconcile_exchange: force-closed stale position",
                    symbol=symbol,
                    strategy=strategy,
                    local_size=str(rec.size),
                    exchange_size=str(exchange_size),
                    pnl=str(pnl),
                )

            checks.append({
                "check": f"exchange_position_{symbol}",
                "result": "repaired",
                "detail": (
                    f"local_size={local_size} vs exchange_size={exchange_size} "
                    f"— force-closed {len(pos_recs)} stale position(s), "
                    f"created TradeRecord(s), reset state to flat"
                ),
            })

            # Publish repair event
            from core.events import EventChannel, EventType, get_publisher
            publisher = get_publisher()
            publisher.publish(
                EventChannel.SYSTEM,
                EventType.RECONCILIATION_REPAIRED,
                {
                    "repair_type": "position_force_closed",
                    "symbol": symbol,
                    "positions_closed": len(pos_recs),
                    "local_size": str(local_size),
                },
            )

        elif not pos_recs and exchange_size > position_tolerance:
            # Exchange has asset but NO local position — attempt reconstruction
            from datetime import datetime, timezone

            has_mismatch = True
            reconstructed = False

            try:
                trades = executor.get_my_trades(symbol, limit=50)
            except Exception as exc:
                logger.error(
                    "reconcile_exchange: failed to fetch trades for reconstruction",
                    symbol=symbol,
                    error=str(exc),
                )
                trades = []

            if trades:
                # Aggregate net position from recent trades
                net_qty = Decimal("0")
                buy_cost = Decimal("0")
                buy_qty = Decimal("0")

                for t in trades:
                    qty = Decimal(str(t.get("qty", "0")))
                    price = Decimal(str(t.get("price", "0")))
                    if t.get("side") == "BUY":
                        net_qty += qty
                        buy_cost += qty * price
                        buy_qty += qty
                    else:
                        net_qty -= qty

                # Only reconstruct if net qty matches exchange balance (within tolerance)
                if net_qty > 0 and abs(net_qty - exchange_size) / exchange_size <= Decimal("0.05"):
                    avg_entry_price = buy_cost / buy_qty if buy_qty > 0 else Decimal("0")
                    # Use the timestamp of the earliest unmatched buy
                    entry_time_ms = trades[-1].get("time", 0)
                    for t in trades:
                        if t.get("side") == "BUY":
                            entry_time_ms = t["time"]
                            break
                    entry_time = datetime.fromtimestamp(entry_time_ms / 1000, tz=timezone.utc)

                    # Determine strategy — use first non-flat strategy state for this symbol
                    state_rec = (
                        session.query(StrategyStateRecord)
                        .filter_by(symbol=symbol)
                        .first()
                    )
                    strategy_name = state_rec.strategy if state_rec else "smart_hodler"

                    # Create PositionRecord
                    new_pos = PositionRecord(
                        symbol=symbol,
                        strategy=strategy_name,
                        side="long",
                        size=exchange_size,
                        entry_price=avg_entry_price,
                        entry_time=entry_time,
                        highest_close=avg_entry_price,
                        trailing_stop_price=avg_entry_price * Decimal("0.95"),
                        hard_stop_price=avg_entry_price * Decimal("0.97"),
                        scale_in_count=0,
                        buy_signal_candles=0,
                    )
                    session.add(new_pos)

                    # Update strategy state
                    if state_rec is not None:
                        state_rec.state = "position"
                        if state_rec.state_data:
                            state_data = dict(state_rec.state_data)
                            balance = Decimal(str(state_data.get("usdt_balance", "0")))
                            cost = exchange_size * avg_entry_price
                            state_data["usdt_balance"] = str(balance - cost)
                            state_rec.state_data = state_data

                    repairs.append(
                        f"reconstructed {strategy_name}:{symbol} position "
                        f"(size={exchange_size}, entry={avg_entry_price:.4f}) from exchange trades"
                    )
                    reconstructed = True

                    logger.warning(
                        "reconcile_exchange: reconstructed position from trades",
                        symbol=symbol,
                        strategy=strategy_name,
                        size=str(exchange_size),
                        entry_price=str(avg_entry_price),
                    )

                    from core.events import EventChannel, EventType, get_publisher
                    publisher = get_publisher()
                    publisher.publish(
                        EventChannel.SYSTEM,
                        EventType.RECONCILIATION_REPAIRED,
                        {
                            "repair_type": "position_reconstructed",
                            "symbol": symbol,
                            "size": str(exchange_size),
                            "entry_price": str(avg_entry_price),
                        },
                    )

            if not reconstructed:
                # Could not auto-reconstruct — leave as critical alert
                checks.append({
                    "check": f"exchange_position_{symbol}",
                    "result": "critical",
                    "detail": (
                        f"Exchange has {exchange_size} {base_asset} "
                        f"but NO local position record. Could not reconstruct from trade history."
                    ),
                })
                logger.critical(
                    "reconcile_exchange: UNTRACKED ASSET on exchange",
                    symbol=symbol,
                    exchange_size=str(exchange_size),
                )
                from core.events import EventChannel, EventType, get_publisher
                publisher = get_publisher()
                publisher.publish(
                    EventChannel.SYSTEM,
                    EventType.RECONCILIATION_MISMATCH,
                    {
                        "severity": "critical",
                        "source": "exchange_untracked_asset",
                        "symbol": symbol,
                        "exchange_size": str(exchange_size),
                    },
                )
            else:
                checks.append({
                    "check": f"exchange_position_{symbol}",
                    "result": "repaired",
                    "detail": (
                        f"Reconstructed position for {symbol} "
                        f"(exchange_size={exchange_size}) from trade history"
                    ),
                })

        else:
            # Size mismatch — both sides have a position but sizes differ
            # Auto-repair: trust exchange, adjust local to match
            has_mismatch = True

            # Find the primary position record and adjust its size
            if pos_recs:
                # Adjust the largest position record
                largest_rec = max(pos_recs, key=lambda r: r.size)
                old_size = largest_rec.size
                adjustment = exchange_size - local_size
                largest_rec.size = largest_rec.size + adjustment

                repairs.append(
                    f"adjusted {largest_rec.strategy}:{symbol} size "
                    f"from {old_size} to {largest_rec.size} "
                    f"(exchange={exchange_size}, delta={adjustment})"
                )

                logger.warning(
                    "reconcile_exchange: auto-repaired size mismatch",
                    symbol=symbol,
                    strategy=largest_rec.strategy,
                    old_size=str(old_size),
                    new_size=str(largest_rec.size),
                    exchange_size=str(exchange_size),
                )

                from core.events import EventChannel, EventType, get_publisher
                publisher = get_publisher()
                publisher.publish(
                    EventChannel.SYSTEM,
                    EventType.RECONCILIATION_REPAIRED,
                    {
                        "repair_type": "size_adjusted",
                        "symbol": symbol,
                        "old_size": str(old_size),
                        "new_size": str(largest_rec.size),
                        "exchange_size": str(exchange_size),
                    },
                )

            checks.append({
                "check": f"exchange_position_{symbol}",
                "result": "repaired",
                "detail": (
                    f"local_size={local_size} → {exchange_size} "
                    f"(adjusted to match exchange)"
                ),
            })

    return {
        "status": "mismatch" if has_mismatch else "ok",
        "checks": checks,
        "repairs": repairs,
        "exchange_balances": {
            asset: {k: str(v) for k, v in bals.items()}
            for asset, bals in exchange_balances.items()
        },
    }



# ── Periodic balance sync (phase 6 — exchange source of truth) ──────────────


@app.task(bind=True, name="core.tasks.sync_exchange_balances")
def sync_exchange_balances(self) -> Dict[str, Any]:  # noqa: ANN001
    """
    Sync USDT balance and equity with Binance every 2 minutes.

    Fetches real account balances and live ticker prices, calculates
    true equity, publishes WebSocket events so the dashboard stays
    current between ticks, and auto-repairs balance drift in
    ``StrategyStateRecord`` when it exceeds a threshold.

    Only runs when ``live_trading.enabled`` is True.
    """
    from core.config import get_settings
    from core.events import EventChannel, EventType, get_publisher
    from db import get_session
    from db.models import PositionRecord, StrategyStateRecord

    settings = get_settings()
    publisher = get_publisher()

    if not settings.live_trading.enabled:
        return {"status": "skipped", "reason": "live_trading not enabled"}

    try:
        from core.execution.binance import BinanceExecutor
        from core.portfolio import PortfolioManager as _PM

        executor = BinanceExecutor.from_settings(
            strategy_name="_balance_sync",
            portfolio_manager=_PM(initial_capital=Decimal("1")),
        )

        account = executor.get_account_info()
        balances = account["balances"]

        exchange_usdt = Decimal("0")
        usdt_data = balances.get("USDT", {})
        if usdt_data:
            exchange_usdt = usdt_data.get("free", Decimal("0")) + usdt_data.get("locked", Decimal("0"))

        # Fetch live prices for open positions to calculate real equity
        with get_session() as session:
            pos_recs = list(session.query(PositionRecord).all())

            positions_value = Decimal("0")
            if pos_recs:
                symbols = list({r.symbol for r in pos_recs})
                live_prices = executor.get_ticker_prices(symbols)

                for rec in pos_recs:
                    price = live_prices.get(rec.symbol, rec.entry_price)
                    positions_value += rec.size * price

            real_equity = exchange_usdt + positions_value

            # Publish events so frontend updates immediately
            from datetime import datetime, timezone

            now = datetime.now(timezone.utc)

            publisher.publish(
                EventChannel.PORTFOLIO,
                EventType.BALANCE_UPDATED,
                {
                    "usdt_balance": str(exchange_usdt),
                    "total_pnl": "0",
                    "open_trades_count": len(pos_recs),
                    "source": "exchange_sync",
                },
            )

            from core.models import PortfolioSnapshot

            snapshot = PortfolioSnapshot(
                timestamp=now,
                usdt_balance=exchange_usdt,
                positions_value=positions_value,
                total_equity=real_equity,
                total_pnl=Decimal("0"),
                open_positions_count=len(pos_recs),
            )
            publisher.publish_model(
                EventChannel.PORTFOLIO, EventType.EQUITY_UPDATED, snapshot,
            )

            # Auto-repair balance drift if above threshold
            state_recs = list(session.query(StrategyStateRecord).all())
            local_usdt_total = Decimal("0")
            for rec in state_recs:
                data = rec.state_data or {}
                local_usdt_total += Decimal(str(data.get("usdt_balance", 0)))

            usdt_drift = exchange_usdt - local_usdt_total

            repaired = False
            bal_threshold = settings.reconciliation.balance_drift_threshold
            if abs(usdt_drift) > bal_threshold and state_recs:
                # Distribute the drift proportionally across strategy states
                if local_usdt_total > 0:
                    for rec in state_recs:
                        data = rec.state_data or {}
                        old_bal = Decimal(str(data.get("usdt_balance", 0)))
                        if old_bal <= 0:
                            continue
                        share = old_bal / local_usdt_total
                        new_bal = old_bal + (usdt_drift * share)
                        rec.state_data = {
                            **data,
                            "usdt_balance": str(new_bal),
                            "equity": str(new_bal + positions_value),
                        }
                else:
                    # All states at zero — assign evenly
                    per_state = exchange_usdt / len(state_recs)
                    for rec in state_recs:
                        data = rec.state_data or {}
                        rec.state_data = {
                            **data,
                            "usdt_balance": str(per_state),
                            "equity": str(per_state),
                        }
                repaired = True

                logger.warning(
                    "balance_sync: auto-repaired USDT drift",
                    local=str(local_usdt_total),
                    exchange=str(exchange_usdt),
                    drift=str(usdt_drift),
                )

        summary: Dict[str, Any] = {
            "status": "ok",
            "exchange_usdt": str(exchange_usdt),
            "positions_value": str(positions_value),
            "equity": str(real_equity),
            "local_usdt": str(local_usdt_total),
            "drift": str(usdt_drift),
            "repaired": repaired,
        }
        logger.info("sync_exchange_balances: done", **summary)
        return summary

    except Exception as exc:
        logger.error(
            "sync_exchange_balances: failed",
            error=str(exc),
            exc_info=True,
        )
        return {"status": "error", "error": str(exc)}


# ── Open order sync (phase 6.4) ──────────────────────────────────────────────


def _sync_open_orders(
    session,  # noqa: ANN001 – SQLAlchemy Session
    executor,  # noqa: ANN001 – BinanceExecutor
) -> Dict[str, Any]:
    """
    Sync exchange open orders with local OrderRecord ledger.

    For each open order on the exchange:
    - If a matching OrderRecord exists → update reconciled_at, mark synced.
    - If no match → create an OrderRecord with reconciliation_status='orphan'.

    Returns summary dict with orphans_found and orders_synced counts.
    """
    from datetime import datetime, timezone

    from db.models import OrderRecord

    try:
        open_orders = executor.get_open_orders()
    except Exception as exc:
        logger.error("sync_open_orders: failed to fetch", error=str(exc))
        return {"status": "error", "error": str(exc)}

    now = datetime.now(timezone.utc)
    orders_synced = 0
    orphans_found = 0

    for order in open_orders:
        exchange_order_id = str(order["orderId"])

        existing = (
            session.query(OrderRecord)
            .filter_by(exchange_order_id=exchange_order_id)
            .first()
        )

        if existing is not None:
            # Known order — update reconciled_at and exchange status
            existing.reconciled_at = now
            existing.reconciliation_status = "synced"
            existing.exchange_status = order.get("status", existing.exchange_status)
            orders_synced += 1
        else:
            # Orphan — order on exchange with no local record
            orphan = OrderRecord(
                symbol=order["symbol"],
                side=order["side"].lower(),
                order_type=order["type"].lower()[:10],
                amount=order["origQty"],
                price=order.get("price"),
                status=order.get("status", "NEW").lower(),
                filled_amount=order.get("executedQty", 0),
                exchange_order_id=exchange_order_id,
                exchange_status=order.get("status", "NEW"),
                reconciliation_status="orphan",
                reconciled_at=now,
                trading_mode="live",
            )
            session.add(orphan)
            orphans_found += 1
            logger.warning(
                "sync_open_orders: orphan detected",
                symbol=order["symbol"],
                order_id=exchange_order_id,
                side=order["side"],
                type=order["type"],
            )

    return {
        "status": "ok",
        "orders_synced": orders_synced,
        "orphans_found": orphans_found,
    }


def _check_pending_stop_orders(
    session,  # noqa: ANN001 – SQLAlchemy Session
    executor,  # noqa: ANN001 – BinanceExecutor
) -> Dict[str, Any]:
    """
    Check status of pending stop orders against the exchange.

    Queries OrderRecords with intent='stop_loss' and status='pending',
    then verifies their current state on Binance:
    - FILLED → update exchange_status (fill recovery in 6.5 will pick it up)
    - CANCELED/EXPIRED → update to cancelled locally
    - NEW → still open, just update reconciled_at
    """
    from datetime import datetime, timezone

    from core.execution.binance import BinanceAPIError
    from db.models import OrderRecord

    pending_stops = (
        session.query(OrderRecord)
        .filter_by(intent="stop_loss", status="pending")
        .filter(OrderRecord.exchange_order_id.isnot(None))
        .all()
    )

    now = datetime.now(timezone.utc)
    filled_stops = 0
    cancelled_stops = 0
    expired_stops = 0

    for record in pending_stops:
        try:
            exchange_data = executor.get_order_status(
                record.symbol, int(record.exchange_order_id)
            )
        except BinanceAPIError as exc:
            if exc.code == -2013:
                # Order does not exist on exchange — mark as expired
                record.status = "cancelled"
                record.exchange_status = "EXPIRED"
                record.reconciliation_status = "synced"
                record.reconciled_at = now
                expired_stops += 1
                logger.warning(
                    "check_pending_stops: order not found on exchange",
                    symbol=record.symbol,
                    order_id=record.exchange_order_id,
                )
                continue
            logger.error(
                "check_pending_stops: API error",
                symbol=record.symbol,
                order_id=record.exchange_order_id,
                error=str(exc),
            )
            continue
        except Exception as exc:
            logger.error(
                "check_pending_stops: unexpected error",
                symbol=record.symbol,
                order_id=record.exchange_order_id,
                error=str(exc),
            )
            continue

        exchange_status = exchange_data.get("status", "")
        record.reconciled_at = now

        if exchange_status == "FILLED":
            # Stop was triggered — mark for fill recovery
            record.exchange_status = "FILLED"
            record.reconciliation_status = "pending_sync"
            filled_stops += 1
            logger.info(
                "check_pending_stops: stop order filled on exchange",
                symbol=record.symbol,
                order_id=record.exchange_order_id,
            )
        elif exchange_status in ("CANCELED", "EXPIRED", "REJECTED"):
            record.status = "cancelled"
            record.exchange_status = exchange_status
            record.reconciliation_status = "synced"
            cancelled_stops += 1
        elif exchange_status in ("NEW", "PARTIALLY_FILLED"):
            # Still open — no action needed
            record.reconciliation_status = "synced"
        else:
            record.exchange_status = exchange_status

    return {
        "status": "ok",
        "pending_stops_checked": len(pending_stops),
        "filled_stops": filled_stops,
        "cancelled_stops": cancelled_stops,
        "expired_stops": expired_stops,
    }


def _cancel_orphan_stop_orders(
    session,  # noqa: ANN001 – SQLAlchemy Session
    executor,  # noqa: ANN001 – BinanceExecutor
    open_orders: list[dict],
) -> Dict[str, Any]:
    """
    Cancel stop-loss orders on the exchange that have no corresponding position.

    Takes the already-fetched open_orders list (from _sync_open_orders context)
    and checks if each STOP_LOSS_LIMIT order has a matching PositionRecord.
    If not, cancels it on the exchange.
    """
    from db.models import PositionRecord

    stop_orders = [
        o for o in open_orders
        if o.get("type") in ("STOP_LOSS_LIMIT", "STOP_LOSS")
    ]

    cancelled = 0

    for order in stop_orders:
        symbol = order["symbol"]
        order_id = order["orderId"]

        # Check if any position exists for this symbol
        has_position = (
            session.query(PositionRecord)
            .filter_by(symbol=symbol)
            .first()
        ) is not None

        if has_position:
            continue

        # No position — cancel the orphan stop
        try:
            executor.cancel_order(symbol, int(order_id))
            cancelled += 1
            logger.warning(
                "cancel_orphan_stops: cancelled stop without position",
                symbol=symbol,
                order_id=str(order_id),
            )
        except Exception as exc:
            logger.error(
                "cancel_orphan_stops: cancel failed",
                symbol=symbol,
                order_id=str(order_id),
                error=str(exc),
            )

    return {
        "status": "ok",
        "stop_orders_checked": len(stop_orders),
        "orphan_stops_cancelled": cancelled,
    }


# ── Fill Recovery (phase 6.5) ────────────────────────────────────────────────

_RECOVERY_AGE_SECONDS = 120  # 2 minutes before considering an order stale
_LOST_AGE_SECONDS = 300      # 5 minutes before marking an order as lost
_TIME_MATCH_TOLERANCE_MS = 30_000  # ±30 seconds for timestamp matching


def _apply_fill_recovery(
    session,  # noqa: ANN001 – SQLAlchemy Session
    order_record,  # noqa: ANN001 – OrderRecord
    exchange_data: dict,
) -> str:
    """
    Apply fill recovery for a single order that was FILLED on exchange.

    Updates the OrderRecord, then reconstructs the missed state change
    (PositionRecord, TradeRecord, StrategyStateRecord) based on intent.

    Returns a short description of what was recovered.
    """
    from datetime import datetime, timedelta, timezone
    from decimal import Decimal

    from db.models import OrderRecord, PositionRecord, StrategyStateRecord, TradeRecord

    now = datetime.now(timezone.utc)
    symbol = order_record.symbol
    intent = order_record.intent

    # ── Update OrderRecord with exchange fill data ────────────────────
    filled_qty = Decimal(str(exchange_data.get("executedQty", order_record.amount)))
    avg_price = Decimal(str(exchange_data.get("price", "0")))
    # Prefer cummulativeQuoteQty / executedQty for avg price if available
    cum_quote = exchange_data.get("cummulativeQuoteQty")
    if cum_quote and filled_qty > 0:
        avg_price = Decimal(str(cum_quote)) / filled_qty

    order_record.status = "filled"
    order_record.filled_amount = float(filled_qty)
    order_record.avg_fill_price = float(avg_price)
    order_record.exchange_status = exchange_data.get("status", "FILLED")
    order_record.reconciliation_status = "recovered"
    order_record.reconciled_at = now

    # ── Determine strategy for this symbol ────────────────────────────
    state_rec = (
        session.query(StrategyStateRecord)
        .filter_by(symbol=symbol)
        .first()
    )
    strategy_name = state_rec.strategy if state_rec else "unknown"

    # ── Route by intent ───────────────────────────────────────────────
    if intent in ("open_long", "open_short"):
        # Idempotency: check if position already exists
        existing_pos = (
            session.query(PositionRecord)
            .filter_by(symbol=symbol, strategy=strategy_name)
            .first()
        )
        if existing_pos is not None:
            logger.info(
                "fill_recovery: position already exists, skipping open",
                symbol=symbol,
                intent=intent,
            )
            return f"open skipped (position exists): {symbol}"

        # Create PositionRecord
        side = "long" if intent == "open_long" else "short"
        cost = filled_qty * avg_price
        fee = Decimal(str(order_record.fee_usdt or 0))

        new_pos = PositionRecord(
            symbol=symbol,
            strategy=strategy_name,
            side=side,
            size=filled_qty,
            entry_price=avg_price,
            entry_time=now,
            highest_close=avg_price,
            trailing_stop_price=avg_price * Decimal("0.95"),
            hard_stop_price=avg_price * Decimal("0.97"),
            scale_in_count=0,
            buy_signal_candles=0,
        )
        session.add(new_pos)

        # Update strategy state
        if state_rec is not None:
            state_rec.state = "position"
            state_data = dict(state_rec.state_data or {})
            balance = Decimal(state_data.get("usdt_balance", "0"))
            state_data["usdt_balance"] = str(balance - cost - fee)
            state_rec.state_data = state_data
            state_rec.updated_at = now

        return f"opened {side}: {symbol} @ {avg_price}"

    elif intent in ("close_long", "close_short", "stop_loss"):
        # Idempotency: check if trade already exists for this fill
        existing_pos = (
            session.query(PositionRecord)
            .filter_by(symbol=symbol, strategy=strategy_name)
            .first()
        )

        if existing_pos is None:
            logger.info(
                "fill_recovery: no position to close, skipping",
                symbol=symbol,
                intent=intent,
            )
            return f"close skipped (no position): {symbol}"

        # Calculate PnL
        entry_price = existing_pos.entry_price
        size = existing_pos.size
        fee = Decimal(str(order_record.fee_usdt or 0))
        side = existing_pos.side

        if side == "long":
            pnl = (avg_price - entry_price) * size - fee
        else:
            pnl = (entry_price - avg_price) * size - fee

        pnl_percent = (pnl / (entry_price * size)) * 100 if entry_price * size > 0 else Decimal("0")
        duration_minutes = int((now - existing_pos.entry_time).total_seconds() / 60)

        # Determine exit_reason
        if intent == "stop_loss":
            exit_reason = "trailing_stop"
        else:
            exit_reason = "manual"

        # Create TradeRecord — idempotency check
        existing_trade = (
            session.query(TradeRecord)
            .filter_by(symbol=symbol, strategy=strategy_name)
            .filter(TradeRecord.exit_time >= now - timedelta(seconds=60))
            .first()
        )
        if existing_trade is None:
            trade_rec = TradeRecord(
                symbol=symbol,
                strategy=strategy_name,
                side=side,
                entry_price=entry_price,
                exit_price=avg_price,
                size=size,
                entry_time=existing_pos.entry_time,
                exit_time=now,
                pnl_usdt=pnl,
                pnl_percent=pnl_percent,
                duration_minutes=duration_minutes,
                exit_reason=exit_reason,
                trading_mode="live",
            )
            session.add(trade_rec)

        # Delete position
        session.delete(existing_pos)

        # Update strategy state
        if state_rec is not None:
            proceeds = avg_price * size - fee
            state_rec.state = "flat"
            state_data = dict(state_rec.state_data or {})
            balance = Decimal(state_data.get("usdt_balance", "0"))
            total_pnl = Decimal(state_data.get("total_pnl", "0"))
            state_data["usdt_balance"] = str(balance + proceeds)
            state_data["total_pnl"] = str(total_pnl + pnl)
            state_rec.state_data = state_data
            state_rec.updated_at = now

        return f"closed {side}: {symbol} @ {avg_price} (PnL: {pnl:.2f})"

    elif intent == "scale_in":
        # Update existing position with weighted average
        existing_pos = (
            session.query(PositionRecord)
            .filter_by(symbol=symbol, strategy=strategy_name)
            .first()
        )
        if existing_pos is None:
            logger.warning(
                "fill_recovery: no position for scale_in, treating as open",
                symbol=symbol,
            )
            # Fallback: create position
            new_pos = PositionRecord(
                symbol=symbol,
                strategy=strategy_name,
                side="long",
                size=filled_qty,
                entry_price=avg_price,
                entry_time=now,
                highest_close=avg_price,
                trailing_stop_price=avg_price * Decimal("0.95"),
                hard_stop_price=avg_price * Decimal("0.97"),
                scale_in_count=1,
                buy_signal_candles=0,
            )
            session.add(new_pos)
            return f"scale_in (new position): {symbol} @ {avg_price}"

        # Weighted average entry price
        old_cost = existing_pos.entry_price * existing_pos.size
        new_cost = avg_price * filled_qty
        total_size = existing_pos.size + filled_qty
        existing_pos.entry_price = (old_cost + new_cost) / total_size
        existing_pos.size = total_size
        existing_pos.scale_in_count = (existing_pos.scale_in_count or 0) + 1

        # Deduct cost from balance
        fee = Decimal(str(order_record.fee_usdt or 0))
        cost = filled_qty * avg_price
        if state_rec is not None:
            state_data = dict(state_rec.state_data or {})
            balance = Decimal(state_data.get("usdt_balance", "0"))
            state_data["usdt_balance"] = str(balance - cost - fee)
            state_rec.state_data = state_data
            state_rec.updated_at = now

        return f"scale_in: {symbol} +{filled_qty} @ {avg_price}"

    elif intent == "reduce":
        existing_pos = (
            session.query(PositionRecord)
            .filter_by(symbol=symbol, strategy=strategy_name)
            .first()
        )
        if existing_pos is None:
            logger.info(
                "fill_recovery: no position for reduce, skipping",
                symbol=symbol,
            )
            return f"reduce skipped (no position): {symbol}"

        entry_price = existing_pos.entry_price
        side = existing_pos.side
        fee = Decimal(str(order_record.fee_usdt or 0))

        if side == "long":
            pnl = (avg_price - entry_price) * filled_qty - fee
        else:
            pnl = (entry_price - avg_price) * filled_qty - fee

        pnl_percent = (pnl / (entry_price * filled_qty)) * 100 if entry_price * filled_qty > 0 else Decimal("0")
        duration_minutes = int((now - existing_pos.entry_time).total_seconds() / 60)

        # Create TradeRecord for the partial exit
        trade_rec = TradeRecord(
            symbol=symbol,
            strategy=strategy_name,
            side=side,
            entry_price=entry_price,
            exit_price=avg_price,
            size=filled_qty,
            entry_time=existing_pos.entry_time,
            exit_time=now,
            pnl_usdt=pnl,
            pnl_percent=pnl_percent,
            duration_minutes=duration_minutes,
            exit_reason="momentum_fade",
            trading_mode="live",
        )
        session.add(trade_rec)

        # Update position
        remaining = existing_pos.size - filled_qty
        if remaining <= 0:
            session.delete(existing_pos)
            if state_rec is not None:
                state_rec.state = "flat"
        else:
            existing_pos.size = remaining

        # Credit proceeds
        proceeds = avg_price * filled_qty - fee
        if state_rec is not None:
            state_data = dict(state_rec.state_data or {})
            balance = Decimal(state_data.get("usdt_balance", "0"))
            total_pnl = Decimal(state_data.get("total_pnl", "0"))
            state_data["usdt_balance"] = str(balance + proceeds)
            state_data["total_pnl"] = str(total_pnl + pnl)
            state_rec.state_data = state_data
            state_rec.updated_at = now

        return f"reduce: {symbol} -{filled_qty} @ {avg_price} (PnL: {pnl:.2f})"

    else:
        # Unknown intent — just mark as recovered, no state change
        logger.warning(
            "fill_recovery: unknown intent, no state reconstruction",
            symbol=symbol,
            intent=intent,
        )
        return f"unknown intent ({intent}): {symbol}"


def _recover_pending_orders(
    session,  # noqa: ANN001 – SQLAlchemy Session
    executor,  # noqa: ANN001 – BinanceExecutor
    *,
    recovery_age_seconds: int = _RECOVERY_AGE_SECONDS,
) -> Dict[str, Any]:
    """
    Recover fills for pending orders that have an exchange_order_id.

    Queries stale pending orders (>2 min old), checks exchange status,
    and applies fill recovery for FILLED orders.
    """
    from datetime import datetime, timedelta, timezone

    from core.execution.binance import BinanceAPIError
    from db.models import OrderRecord

    cutoff = datetime.now(timezone.utc) - timedelta(seconds=recovery_age_seconds)

    pending_orders = (
        session.query(OrderRecord)
        .filter_by(status="pending")
        .filter(OrderRecord.exchange_order_id.isnot(None))
        .filter(OrderRecord.created_at < cutoff)
        .all()
    )

    recovered = 0
    cancelled = 0
    still_open = 0

    for record in pending_orders:
        try:
            exchange_data = executor.get_order_status(
                record.symbol, int(record.exchange_order_id)
            )
        except BinanceAPIError as exc:
            if exc.code == -2013:
                # Order doesn't exist on exchange — mark as lost
                record.status = "cancelled"
                record.exchange_status = "NOT_FOUND"
                record.reconciliation_status = "lost"
                record.reconciled_at = datetime.now(timezone.utc)
                cancelled += 1
                logger.warning(
                    "recover_pending: order not found on exchange",
                    symbol=record.symbol,
                    order_id=record.exchange_order_id,
                )
                continue
            logger.error(
                "recover_pending: API error",
                symbol=record.symbol,
                order_id=record.exchange_order_id,
                error=str(exc),
            )
            continue
        except Exception as exc:
            logger.error(
                "recover_pending: unexpected error",
                symbol=record.symbol,
                order_id=record.exchange_order_id,
                error=str(exc),
            )
            continue

        exchange_status = exchange_data.get("status", "")

        if exchange_status == "FILLED":
            description = _apply_fill_recovery(session, record, exchange_data)
            recovered += 1
            logger.info(
                "recover_pending: fill recovered",
                symbol=record.symbol,
                order_id=record.exchange_order_id,
                description=description,
            )
        elif exchange_status in ("CANCELED", "EXPIRED", "REJECTED"):
            record.status = "cancelled"
            record.exchange_status = exchange_status
            record.reconciliation_status = "synced"
            record.reconciled_at = datetime.now(timezone.utc)
            cancelled += 1
        elif exchange_status in ("NEW", "PARTIALLY_FILLED"):
            # Still open on exchange — leave for next cycle
            still_open += 1
        else:
            record.exchange_status = exchange_status

    return {
        "status": "ok",
        "orders_checked": len(pending_orders),
        "recovered": recovered,
        "cancelled": cancelled,
        "still_open": still_open,
    }


def _recover_pending_orders_without_id(
    session,  # noqa: ANN001 – SQLAlchemy Session
    executor,  # noqa: ANN001 – BinanceExecutor
    *,
    lost_age_seconds: int = _LOST_AGE_SECONDS,
) -> Dict[str, Any]:
    """
    Recover orders that crashed before receiving exchange response.

    These have exchange_order_id=NULL. Attempts to match them against
    exchange order history by (symbol, side, qty, timestamp ±30s).
    """
    from datetime import datetime, timedelta, timezone
    from decimal import Decimal

    from db.models import OrderRecord

    cutoff = datetime.now(timezone.utc) - timedelta(seconds=lost_age_seconds)

    orphan_orders = (
        session.query(OrderRecord)
        .filter_by(status="pending")
        .filter(OrderRecord.exchange_order_id.is_(None))
        .filter(OrderRecord.created_at < cutoff)
        .all()
    )

    matched = 0
    lost = 0

    for record in orphan_orders:
        # Query exchange for recent orders on this symbol
        start_time_ms = int(record.created_at.timestamp() * 1000) - _TIME_MATCH_TOLERANCE_MS
        try:
            exchange_orders = executor.get_all_orders(
                record.symbol, start_time=start_time_ms, limit=100,
            )
        except Exception as exc:
            logger.error(
                "recover_without_id: failed to query exchange",
                symbol=record.symbol,
                error=str(exc),
            )
            continue

        # Try to match by side + qty + timestamp
        order_side = record.side.upper()
        order_qty = Decimal(str(record.amount))
        order_time_ms = int(record.created_at.timestamp() * 1000)

        match_found = None
        for ex_order in exchange_orders:
            if ex_order.get("side") != order_side:
                continue
            ex_qty = Decimal(str(ex_order.get("origQty", "0")))
            # Qty within 1% tolerance
            if order_qty > 0 and abs(ex_qty - order_qty) / order_qty > Decimal("0.01"):
                continue
            # Timestamp within tolerance
            ex_time = ex_order.get("time", 0)
            if abs(ex_time - order_time_ms) > _TIME_MATCH_TOLERANCE_MS:
                continue
            # Check it's not already claimed by another OrderRecord
            ex_id = str(ex_order["orderId"])
            existing = (
                session.query(OrderRecord)
                .filter_by(exchange_order_id=ex_id)
                .first()
            )
            if existing is not None:
                continue
            match_found = ex_order
            break

        if match_found is not None:
            # Associate the exchange_order_id and process
            record.exchange_order_id = str(match_found["orderId"])
            exchange_status = match_found.get("status", "")

            if exchange_status == "FILLED":
                _apply_fill_recovery(session, record, match_found)
            elif exchange_status in ("CANCELED", "EXPIRED", "REJECTED"):
                record.status = "cancelled"
                record.exchange_status = exchange_status
                record.reconciliation_status = "synced"
                record.reconciled_at = datetime.now(timezone.utc)
            elif exchange_status in ("NEW", "PARTIALLY_FILLED"):
                record.reconciliation_status = "synced"
                record.reconciled_at = datetime.now(timezone.utc)
            else:
                record.exchange_status = exchange_status

            matched += 1
            logger.info(
                "recover_without_id: matched to exchange order",
                symbol=record.symbol,
                exchange_order_id=record.exchange_order_id,
                exchange_status=exchange_status,
            )
        else:
            # No match found — order likely never reached exchange
            record.status = "cancelled"
            record.reconciliation_status = "lost"
            record.reconciled_at = datetime.now(timezone.utc)
            lost += 1
            logger.warning(
                "recover_without_id: no match, marking as lost",
                symbol=record.symbol,
                created_at=str(record.created_at),
            )

    return {
        "status": "ok",
        "orders_checked": len(orphan_orders),
        "matched": matched,
        "lost": lost,
    }


# ── run_reconciliation (task 3.9) ────────────────────────────────────────────


@app.task(bind=True, name="core.tasks.run_reconciliation")
def run_reconciliation(self) -> Dict[str, Any]:  # noqa: ANN001
    """
    Reconcile local DB state against expected invariants.

    Called every 15 minutes by Celery Beat (``reconciliation-15m``) and
    once on worker startup (``worker_ready`` signal).  For each
    ``(strategy, symbol)`` pair the task runs three consistency
    checks via :func:`_reconcile_pair`:

    * **State ↔ Position** – strategy state machine matches
      ``PositionRecord`` presence.
    * **Balance sanity** – non-negative balances, FLAT equity match.
    * **Trade PnL arithmetic** – ``state_data.total_pnl`` agrees with
      ``sum(TradeRecord.pnl_usdt)`` for the current ``run_id``.

    Mismatches are auto-repaired (state/position) where safe, and
    published as events on ``EventChannel.SYSTEM`` for dashboard
    visibility.

    Returns:
        Summary dict ``{"pairs_checked": N, "mismatches": M,
        "repairs": R, "details": [...]}``.
    """
    from core.config import get_settings
    from core.events import EventChannel, EventType, get_publisher
    from db import get_session

    settings = get_settings()

    if not settings.reconciliation.enabled:
        logger.info("run_reconciliation: disabled via config")
        return {"status": "disabled", "pairs_checked": 0, "mismatches": 0, "repairs": 0, "details": []}

    publisher = get_publisher()
    registry = _get_strategy_registry()

    pairs_checked = 0
    total_mismatches = 0
    total_repairs = 0
    details: list[Dict[str, Any]] = []

    for strategy_name in registry:
        strat_cfg = settings.strategies.get_strategy_config(strategy_name)
        if strat_cfg is None:
            continue
        enabled = strat_cfg.get("enabled", False) if isinstance(strat_cfg, dict) else strat_cfg.enabled
        if not enabled:
            continue

        for symbol_cfg in settings.enabled_symbols:
            symbol = symbol_cfg.symbol
            log = logger.bind(
                strategy=strategy_name,
                symbol=symbol,
                task_id=self.request.id,
            )

            try:
                with get_session() as session:
                    result = _reconcile_pair(session, strategy_name, symbol)
                    pairs_checked += 1
                    details.append(result)

                    if result["status"] == "mismatch":
                        total_mismatches += 1
                        total_repairs += len(result["repairs"])

                        # Publish per-pair mismatch event
                        publisher.publish(
                            EventChannel.SYSTEM,
                            EventType.RECONCILIATION_MISMATCH,
                            {
                                "pair": result["pair"],
                                "checks": result["checks"],
                                "repairs": result["repairs"],
                            },
                        )

                        if result["repairs"]:
                            publisher.publish(
                                EventChannel.SYSTEM,
                                EventType.RECONCILIATION_REPAIRED,
                                {
                                    "pair": result["pair"],
                                    "repairs": result["repairs"],
                                },
                            )

                    log.info(
                        "reconcile: pair checked",
                        status=result["status"],
                        repairs=len(result["repairs"]),
                    )

            except Exception as exc:
                log.error(
                    "reconcile: pair failed",
                    error=str(exc),
                    exc_info=True,
                )
                details.append({
                    "pair": f"{strategy_name}:{symbol}",
                    "status": "error",
                    "error": str(exc),
                })

    # ── Exchange reconciliation (6.5 → 6.4 → 6.6) ─────────────────────
    exchange_result: Dict[str, Any] | None = None
    sync_result: Dict[str, Any] | None = None
    stop_result: Dict[str, Any] | None = None
    orphan_stop_result: Dict[str, Any] | None = None
    recovery_result: Dict[str, Any] | None = None
    lost_result: Dict[str, Any] | None = None
    if settings.live_trading.enabled:
        try:
            from core.execution.binance import BinanceExecutor

            # Build a temporary executor just for account queries
            from core.portfolio import PortfolioManager as _PM

            executor = BinanceExecutor.from_settings(
                strategy_name="reconciliation",
                portfolio_manager=_PM(initial_capital=Decimal("1")),
            )
            tracked_symbols = [s.symbol for s in settings.enabled_symbols]
            recon_cfg = settings.reconciliation

            # ── Phase 6.5: Fill Recovery ──────────────────────────────────
            # Recovery runs FIRST so stale PENDING orders (including filled
            # stops) are resolved before open-order sync re-checks them.
            with get_session() as session:
                recovery_result = _recover_pending_orders(
                    session, executor,
                    recovery_age_seconds=recon_cfg.recovery_age_seconds,
                )

            with get_session() as session:
                lost_result = _recover_pending_orders_without_id(
                    session, executor,
                    lost_age_seconds=recon_cfg.lost_age_seconds,
                )

            logger.info(
                "reconcile: fill recovery done",
                recovery=recovery_result,
                lost=lost_result,
            )

            # ── Phase 6.4: Open order sync ────────────────────────────────
            with get_session() as session:
                sync_result = _sync_open_orders(session, executor)

            with get_session() as session:
                stop_result = _check_pending_stop_orders(session, executor)

            # Cancel orphan stops (needs fresh open orders list)
            try:
                open_orders_list = executor.get_open_orders()
            except Exception:
                open_orders_list = []
            if open_orders_list:
                with get_session() as session:
                    orphan_stop_result = _cancel_orphan_stop_orders(
                        session, executor, open_orders_list,
                    )
            else:
                orphan_stop_result = {
                    "status": "ok",
                    "stop_orders_checked": 0,
                    "orphan_stops_cancelled": 0,
                }

            logger.info(
                "reconcile: open order sync done",
                sync=sync_result,
                stops=stop_result,
                orphan_stops=orphan_stop_result,
            )

            # ── Phase 6.6: Position reconciliation v2 ────────────────────
            with get_session() as session:
                exchange_result = _reconcile_with_exchange(
                    session, executor, tracked_symbols,
                    balance_tolerance=recon_cfg.balance_drift_threshold,
                    position_tolerance=recon_cfg.size_mismatch_threshold,
                )

            if exchange_result["status"] == "mismatch":
                total_mismatches += 1
                exchange_repairs = exchange_result.get("repairs", [])
                total_repairs += len(exchange_repairs)
                publisher.publish(
                    EventChannel.SYSTEM,
                    EventType.RECONCILIATION_MISMATCH,
                    {
                        "source": "exchange",
                        "checks": exchange_result["checks"],
                    },
                )
                if exchange_repairs:
                    publisher.publish(
                        EventChannel.SYSTEM,
                        EventType.RECONCILIATION_REPAIRED,
                        {
                            "source": "exchange",
                            "repairs": exchange_repairs,
                        },
                    )

            logger.info(
                "reconcile: exchange check done",
                status=exchange_result["status"],
                repairs=len(exchange_result.get("repairs", [])),
            )
        except Exception as exc:
            logger.error(
                "reconcile: exchange check failed",
                error=str(exc),
                exc_info=True,
            )
            exchange_result = {"status": "error", "error": str(exc)}

    summary: Dict[str, Any] = {
        "pairs_checked": pairs_checked,
        "mismatches": total_mismatches,
        "repairs": total_repairs,
        "details": details,
    }
    if exchange_result is not None:
        summary["exchange"] = exchange_result
    if recovery_result is not None:
        summary["fill_recovery"] = {
            "recovery": recovery_result,
            "lost_orders": lost_result,
        }
    if sync_result is not None:
        summary["order_sync"] = {
            "sync": sync_result,
            "stops": stop_result,
            "orphan_stops": orphan_stop_result,
        }

    # Publish summary event
    if total_mismatches == 0:
        publisher.publish(
            EventChannel.SYSTEM,
            EventType.RECONCILIATION_OK,
            summary,
        )
    else:
        publisher.publish(
            EventChannel.SYSTEM,
            EventType.RECONCILIATION_MISMATCH,
            summary,
        )

    # ── Alert on mismatches (task 4.9) ──────────────────────────────
    if total_mismatches > 0 and settings.alerting.enabled:
        try:
            from core.alerting import get_alerter

            alerter = get_alerter()
            if alerter.enabled and settings.alerting.alert_on_reconciliation:
                mismatch_checks = [
                    c
                    for d in details
                    for c in d.get("checks", [])
                    if c.get("result") in ("mismatch", "warning")
                ]
                detail_lines = [c.get("detail", c["check"]) for c in mismatch_checks[:5]]
                if exchange_result and exchange_result.get("status") == "mismatch":
                    for c in exchange_result.get("checks", []):
                        if c.get("result") == "warning":
                            detail_lines.append(c.get("detail", c["check"]))
                alerter.alert_reconciliation_mismatch(
                    details="\n".join(detail_lines) or f"{total_mismatches} mismatch(es) found",
                )
        except Exception:
            pass  # fire-and-forget

    logger.info("run_reconciliation: complete", **summary, task_id=self.request.id)
    return summary

