"""
Worker-process state management and recovery.

Keeps strategy, portfolio-manager, risk-engine and executor instances
alive across ticks *within the same worker process*.  On first creation
the factory attempts to restore state from the DB so that paper-trading
positions and strategy state survive worker restarts.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, Optional, Tuple

import structlog

logger = structlog.get_logger(__name__)

# Maximum in-memory trade-history and equity-curve entries to keep
# after each persist.  Older entries live in the DB.
_MAX_MEMORY_TRADES = 50
_MAX_MEMORY_SNAPSHOTS = 100

# ── Strategy registry ────────────────────────────────────────────────────────


def _get_strategy_registry() -> Dict[str, type]:
    """Return the auto-discovered strategy registry."""
    from core.strategies.registry import get_registry

    return get_registry()


# ── Worker-process state ─────────────────────────────────────────────────────

_WorkerStateKey = Tuple[str, str]  # (strategy_name, symbol)


class _TickComponents:
    """Holds the live objects for one (strategy, symbol) pair."""

    __slots__ = (
        "strategy", "pm", "risk_engine", "executor", "run_id",
        "kill_switch", "trading_mode", "regime_detector",
    )

    def __init__(
        self, strategy, pm, risk_engine, executor, run_id: str,  # noqa: ANN001
        kill_switch=None, trading_mode: str = "paper", regime_detector=None,  # noqa: ANN001
    ) -> None:
        self.strategy = strategy
        self.pm = pm
        self.risk_engine = risk_engine
        self.executor = executor
        self.run_id = run_id
        self.kill_switch = kill_switch
        self.trading_mode = trading_mode
        self.regime_detector = regime_detector


_worker_state: Dict[_WorkerStateKey, _TickComponents] = {}


def clear_worker_state() -> None:
    """Reset worker-process state.  Used in tests for isolation."""
    _worker_state.clear()


# ── State recovery (task 3.8) ────────────────────────────────────────────────


def _recover_state(
    strategy_name: str,
    symbol: str,
    strategy,  # noqa: ANN001 – Strategy instance
    pm,  # noqa: ANN001 – PortfolioManager
    risk_engine,  # noqa: ANN001 – RiskEngine
    kill_switch=None,  # noqa: ANN001 – KillSwitch instance
    trading_mode: str = "paper",
) -> str | None:
    """
    Attempt to restore strategy / portfolio / risk state from the DB.

    Recovers:
      1. Strategy state (state machine, cooldown, counters)
      2. Open position
      3. Recent trade history (task 4.7)
      4. Equity curve snapshots (task 4.7)

    Returns the previous ``run_id`` if recovery succeeded, or ``None``
    if no persisted state was found.
    """
    from core.models import StrategyState
    from db import get_session
    from db.models import PositionRecord, PortfolioSnapshot, StrategyStateRecord, TradeRecord
    from db.utils import orm_to_portfolio_snapshot, orm_to_position, orm_to_trade

    recovered_run_id: str | None = None

    try:
        with get_session() as session:
            # 1. Restore strategy state ────────────────────────────────
            state_rec = (
                session.query(StrategyStateRecord)
                .filter_by(symbol=symbol, strategy=strategy_name)
                .first()
            )
            if state_rec is not None:
                data = state_rec.state_data or {}
                data["state"] = state_rec.state

                strategy.from_state_dict(data)

                # Risk engine: consecutive BUY candle counter
                risk_engine._buy_signal_candles[symbol] = (
                    state_rec.consecutive_buy_candles
                )

                recovered_run_id = data.get("run_id")

                # Kill-switch state (task 4.5)
                if kill_switch is not None:
                    saved_run_id = data.get("run_id", "")
                    saved_mode = data.get("kill_switch_trading_mode")
                    if (
                        not saved_mode
                        and saved_run_id.startswith("paper_")
                        and trading_mode == "live"
                    ):
                        kill_switch.hard_reset()
                    else:
                        kill_switch.restore_from_dict(
                            data, current_trading_mode=trading_mode,
                        )

                logger.info(
                    "state_recovery: strategy state restored",
                    strategy=strategy_name,
                    symbol=symbol,
                    state=state_rec.state,
                    cooldown_remaining=strategy._cooldown_remaining,
                    buy_signal_candles=state_rec.consecutive_buy_candles,
                )

            # 2a. Restore USDT balance and PnL (regardless of position)
            if state_rec is not None:
                data = state_rec.state_data or {}
                saved_balance = data.get("usdt_balance")
                if saved_balance is not None:
                    pm._portfolio.usdt_balance = Decimal(str(saved_balance))
                saved_total_pnl = data.get("total_pnl")
                if saved_total_pnl is not None:
                    pm._portfolio.total_pnl = Decimal(str(saved_total_pnl))

            # 2b. Restore open position ────────────────────────────────
            pos_rec = (
                session.query(PositionRecord)
                .filter_by(symbol=symbol, strategy=strategy_name)
                .first()
            )
            if pos_rec is not None:
                position = orm_to_position(pos_rec)

                # Inject position directly into PM without deducting cost
                # (cost was already deducted when originally opened).
                pm._portfolio.positions[symbol] = position
                pm._portfolio.open_trades_count = len(pm._portfolio.positions)

                logger.info(
                    "state_recovery: position restored",
                    strategy=strategy_name,
                    symbol=symbol,
                    size=str(position.size),
                    entry_price=str(position.entry_price),
                    scale_in_count=position.scale_in_count,
                )

            # 3. Restore recent trade history (task 4.7) ──────────────
            if recovered_run_id is not None:
                trade_recs = (
                    session.query(TradeRecord)
                    .filter_by(strategy=strategy_name, symbol=symbol)
                    .order_by(TradeRecord.exit_time.desc())
                    .limit(_MAX_MEMORY_TRADES)
                    .all()
                )
                if trade_recs:
                    # Reverse so oldest first (chronological order)
                    pm._portfolio.trade_history = [
                        orm_to_trade(r) for r in reversed(trade_recs)
                    ]
                    logger.info(
                        "state_recovery: trade history restored",
                        strategy=strategy_name,
                        symbol=symbol,
                        count=len(trade_recs),
                    )

            # 4. Restore equity curve snapshots (task 4.7) ────────────
            if recovered_run_id is not None:
                snap_recs = (
                    session.query(PortfolioSnapshot)
                    .filter_by(run_id=recovered_run_id)
                    .order_by(PortfolioSnapshot.timestamp.desc())
                    .limit(_MAX_MEMORY_SNAPSHOTS)
                    .all()
                )
                if snap_recs:
                    pm._portfolio.equity_curve = [
                        orm_to_portfolio_snapshot(r) for r in reversed(snap_recs)
                    ]
                    logger.info(
                        "state_recovery: equity curve restored",
                        strategy=strategy_name,
                        symbol=symbol,
                        count=len(snap_recs),
                    )

    except Exception as exc:
        logger.warning(
            "state_recovery: failed (starting fresh)",
            strategy=strategy_name,
            symbol=symbol,
            error=str(exc),
        )

    return recovered_run_id



def _get_or_create_components(
    strategy_name: str,
    symbol: str,
    strategy_config: dict,
    risk_config: dict,
    execution_config: dict,
    paper_config: dict,
    initial_capital: float,
    trading_mode: str = "paper",
) -> _TickComponents:
    """
    Return cached tick-pipeline components, creating them on first call.

    On first creation, attempts to recover persisted state from the DB
    (task 3.8).  Falls back to a fresh session if no state is found.

    Args:
        trading_mode: ``"paper"`` for PaperExecutor, ``"live"`` for
                      BinanceExecutor.
    """
    key: _WorkerStateKey = (strategy_name, symbol)

    if key not in _worker_state:
        from core.execution.paper import PaperExecutor
        from core.events import EventChannel, EventType, get_publisher
        from core.portfolio import PortfolioManager
        from core.risk import RiskEngine

        registry = _get_strategy_registry()
        strategy_cls = registry[strategy_name]

        strategy = strategy_cls(strategy_config)

        cap = Decimal(str(initial_capital))
        taker_fee = Decimal(str(execution_config.get("taker_fee", 0.001)))

        pm = PortfolioManager(initial_capital=cap, taker_fee=taker_fee)
        risk_engine = RiskEngine(strategy_name, strategy_config, risk_config)

        if trading_mode == "live":
            from core.execution.binance import BinanceExecutor

            executor = BinanceExecutor.from_settings(
                strategy_name=strategy_name,
                portfolio_manager=pm,
            )
            executor.load_exchange_filters([symbol])

            # Sync portfolio with real Binance balances
            try:
                account = executor.get_account_info()
                balances = account["balances"]

                usdt_bal = balances.get("USDT", {})
                real_usdt = usdt_bal.get("free", Decimal("0")) + usdt_bal.get("locked", Decimal("0"))

                # NOTE: We do NOT set pm._portfolio.usdt_balance = real_usdt here.
                # Each strategy pair tracks its own allocated capital independently.
                # Setting the full account balance would inflate equity for kill-switch
                # calculations and cause false drawdown triggers across all 68 pairs.

                # Check if this symbol's base asset has a balance on the exchange
                base_asset = symbol.replace("USDT", "")
                if base_asset in balances:
                    asset_bal = balances[base_asset]
                    qty = asset_bal.get("free", Decimal("0")) + asset_bal.get("locked", Decimal("0"))
                    if qty > 0:
                        prices = executor.get_ticker_prices([symbol])
                        price = prices.get(symbol)
                        value = qty * price if price else Decimal("0")
                        logger.warning(
                            "tick_components: asset found on exchange but no local position — "
                            "reconciliation will report this divergence",
                            strategy=strategy_name, symbol=symbol,
                            asset=base_asset, qty=str(qty),
                            value_usdt=str(value),
                        )

                logger.info(
                    "tick_components: real balance check",
                    strategy=strategy_name, symbol=symbol,
                    config_capital=str(cap), real_usdt=str(real_usdt),
                )
            except Exception as exc:
                logger.warning(
                    "tick_components: failed to fetch real balance, using config value",
                    strategy=strategy_name, symbol=symbol, error=str(exc),
                )
        else:
            executor = PaperExecutor(
                strategy_name=strategy_name,
                portfolio_manager=pm,
                slippage=Decimal(str(execution_config.get("slippage", 0.001))),
                simulate_slippage=paper_config.get("simulate_slippage", True),
            )

        # Regime detector (phase 3, task 1.1)
        from core.regime import RegimeDetector
        from core.config import get_settings as _get_settings

        _settings = _get_settings()
        regime_detector = RegimeDetector(
            trending_threshold=_settings.regime.adx_trending_threshold,
            ranging_threshold=_settings.regime.adx_ranging_threshold,
            smoothing_window=_settings.regime.smoothing_window,
        )

        # Kill-switch (task 4.5)
        from core.risk.kill_switch import KillSwitch
        kill_switch = KillSwitch(
            max_drawdown_pct=_settings.live_trading.kill_switch_max_drawdown,
            max_daily_loss_pct=_settings.risk.max_daily_loss_pct,
        )

        # Attempt state recovery from DB
        recovered_run_id = _recover_state(
            strategy_name, symbol, strategy, pm, risk_engine,
            kill_switch=kill_switch,
            trading_mode=trading_mode,
        )

        # Generate or reuse run_id
        now_tag = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        prefix = "live" if trading_mode == "live" else "paper"
        run_id = recovered_run_id or f"{prefix}_{strategy_name}_{symbol}_{now_tag}"

        _worker_state[key] = _TickComponents(
            strategy, pm, risk_engine, executor, run_id, kill_switch,
            trading_mode=trading_mode,
            regime_detector=regime_detector,
        )

        logger.info(
            "tick_components: created",
            strategy=strategy_name,
            symbol=symbol,
            initial_capital=str(cap),
            run_id=run_id,
            recovered=recovered_run_id is not None,
            trading_mode=trading_mode,
        )

        # Publish startup event (task 3.7)
        publisher = get_publisher()
        publisher.publish(
            EventChannel.SYSTEM,
            EventType.STARTUP,
            {
                "mode": f"{trading_mode}_trading",
                "strategy": strategy_name,
                "symbol": symbol,
                "run_id": run_id,
                "initial_capital": str(cap),
                "recovered": recovered_run_id is not None,
            },
        )

    return _worker_state[key]

