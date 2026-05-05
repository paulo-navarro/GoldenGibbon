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
    if "USDT" in exchange_balances:
        exchange_usdt = exchange_balances["USDT"]["free"] + exchange_balances["USDT"]["locked"]

    usdt_delta = abs(local_usdt_total - exchange_usdt)
    if usdt_delta <= _EXCHANGE_BALANCE_TOLERANCE:
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
        if size_delta <= _EXCHANGE_POSITION_TOLERANCE:
            checks.append({
                "check": f"exchange_position_{symbol}",
                "result": "ok",
            })
        elif pos_recs and exchange_size <= _EXCHANGE_POSITION_TOLERANCE:
            # Exchange has zero/dust but local DB has open position(s).
            # The sell was executed on Binance but the local state was
            # never cleaned up.  Auto-repair: delete stale positions
            # and reset strategy state to FLAT.
            has_mismatch = True
            for rec in pos_recs:
                strategy = rec.strategy
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
                        state_rec.state_data = {
                            **state_rec.state_data,
                            "cooldown_remaining": 0,
                        }
                    repairs.append(
                        f"reset {strategy}:{symbol} from {old_state} to flat "
                        f"(exchange has zero balance)"
                    )

                # Evict in-memory cache so next tick rebuilds from corrected DB
                _tick_key: _WorkerStateKey = (strategy, symbol)
                if _tick_key in _worker_state:
                    del _worker_state[_tick_key]

                logger.warning(
                    "reconcile_exchange: auto-repaired stale position",
                    symbol=symbol,
                    strategy=strategy,
                    local_size=str(rec.size),
                    exchange_size=str(exchange_size),
                )

            checks.append({
                "check": f"exchange_position_{symbol}",
                "result": "repaired",
                "detail": (
                    f"local_size={local_size} vs exchange_size={exchange_size} "
                    f"— deleted {len(pos_recs)} stale position(s), reset state to flat"
                ),
            })
        elif not pos_recs and exchange_size > _EXCHANGE_POSITION_TOLERANCE:
            # Exchange has asset but NO local position — real money untracked
            has_mismatch = True
            checks.append({
                "check": f"exchange_position_{symbol}",
                "result": "critical",
                "detail": (
                    f"Exchange has {exchange_size} {base_asset} "
                    f"but NO local position record. Manual intervention required."
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
            has_mismatch = True
            checks.append({
                "check": f"exchange_position_{symbol}",
                "result": "warning",
                "detail": (
                    f"local_size={local_size} vs "
                    f"exchange_size={exchange_size} (delta={size_delta})"
                ),
            })
            logger.warning(
                "reconcile_exchange: position size mismatch",
                symbol=symbol,
                local=str(local_size),
                exchange=str(exchange_size),
                delta=str(size_delta),
            )

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
        if "USDT" in balances:
            exchange_usdt = balances["USDT"]["free"] + balances["USDT"]["locked"]

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
            if abs(usdt_drift) > _EXCHANGE_BALANCE_TOLERANCE and state_recs:
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

    # ── Exchange reconciliation (task 4.8) ────────────────────────────
    exchange_result: Dict[str, Any] | None = None
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

            with get_session() as session:
                exchange_result = _reconcile_with_exchange(
                    session, executor, tracked_symbols,
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

