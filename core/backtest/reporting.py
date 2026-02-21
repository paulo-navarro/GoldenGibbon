"""
Backtest reporting – console output and database persistence.

Public API::

    from core.backtest.reporting import report_backtest

    report_backtest(result, metrics, backtest_config)

The ``backtest_config`` dict drives behaviour via the keys already
defined in ``config/settings.yaml``::

    output_format   "console" | "database" | "both"
    save_trades     bool
    save_equity_curve  bool
    save_metrics    bool
"""

from __future__ import annotations

import io
from decimal import Decimal
from typing import Any, Dict, List, Optional

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from core.models import BacktestMetrics, BacktestResult, Trade

# Re-export for convenience
__all__ = [
    "report_backtest",
    "format_console_report",
    "persist_to_database",
]


# ── Public orchestrator ──────────────────────────────────────────────────────


def report_backtest(
    result: BacktestResult,
    metrics: BacktestMetrics,
    backtest_config: Dict[str, Any] | None = None,
    *,
    config_snapshot: Dict[str, Any] | None = None,
    console: Console | None = None,
) -> None:
    """
    Report backtest results to console and/or database.

    Reads ``output_format`` from *backtest_config* to decide where
    output goes.

    Args:
        result: Raw backtest result.
        metrics: Computed metrics.
        backtest_config: ``backtest`` section of settings.yaml.
        config_snapshot: Full config dict to store in DB for reproducibility.
        console: Optional Rich Console (useful for testing / capturing).
    """
    cfg = backtest_config or {}
    output_format = cfg.get("output_format", "both")

    if output_format in ("console", "both"):
        format_console_report(metrics, result.trades, console=console)

    if output_format in ("database", "both"):
        persist_to_database(
            metrics=metrics,
            result=result,
            backtest_config=cfg,
            config_snapshot=config_snapshot,
        )


# ── Console reporting ────────────────────────────────────────────────────────


def format_console_report(
    metrics: BacktestMetrics,
    trades: List[Trade] | None = None,
    *,
    console: Console | None = None,
    max_trades: int = 10,
) -> None:
    """
    Print a rich-formatted backtest summary to the console.

    Args:
        metrics: Computed backtest metrics.
        trades: Optional list of trades for the trade table.
        console: Optional Rich Console override.
        max_trades: Maximum trades to display in detail table.
    """
    con = console or Console()

    # ── Summary panel ────────────────────────────────────────────
    summary = Table.grid(padding=(0, 2))
    summary.add_column(style="bold cyan", justify="right")
    summary.add_column()

    summary.add_row("Strategy", metrics.strategy)
    summary.add_row("Symbol", metrics.symbol)
    summary.add_row(
        "Period",
        f"{metrics.start_date:%Y-%m-%d} → {metrics.end_date:%Y-%m-%d}",
    )
    summary.add_row("Initial Capital", f"${metrics.initial_capital:,.2f}")
    summary.add_row("Final Capital", f"${metrics.final_capital:,.2f}")

    con.print(Panel(summary, title="[bold]Backtest Summary", border_style="blue"))

    # ── Metrics table ────────────────────────────────────────────
    mtable = Table(title="Performance Metrics", show_header=True, header_style="bold magenta")
    mtable.add_column("Metric", style="cyan")
    mtable.add_column("Value", justify="right")

    mtable.add_row("Total Return", _fmt_pct(metrics.total_return))
    mtable.add_row("Max Drawdown", _fmt_pct(metrics.max_drawdown))
    mtable.add_row("Sharpe Ratio", _fmt_dec(metrics.sharpe_ratio))
    mtable.add_row("Win Rate", _fmt_pct(metrics.win_rate))
    mtable.add_row("Profit Factor", _fmt_dec(metrics.profit_factor))
    mtable.add_row("Total Trades", str(metrics.total_trades))
    mtable.add_row("Winning / Losing", f"{metrics.winning_trades} / {metrics.losing_trades}")
    mtable.add_row("Avg Win", _fmt_pct(metrics.avg_win))
    mtable.add_row("Avg Loss", _fmt_pct(metrics.avg_loss))
    mtable.add_row(
        "Avg Trade Duration",
        f"{metrics.avg_trade_duration_minutes} min"
        if metrics.avg_trade_duration_minutes is not None
        else "—",
    )
    mtable.add_row("Max Consecutive Wins", _fmt_int(metrics.max_consecutive_wins))
    mtable.add_row("Max Consecutive Losses", _fmt_int(metrics.max_consecutive_losses))

    # Buy & Hold comparison
    if metrics.buy_hold_return is not None:
        mtable.add_row("Buy & Hold Return", _fmt_pct(metrics.buy_hold_return))
    if metrics.buy_hold_vs_strategy is not None:
        label = "Strategy vs B&H"
        val = metrics.buy_hold_vs_strategy
        colour = "green" if val >= Decimal("0") else "red"
        mtable.add_row(label, Text(f"{val:+.2f}%", style=colour))

    con.print(mtable)

    # ── Trade detail table (optional) ────────────────────────────
    if trades:
        ttable = Table(
            title=f"Trades (showing {min(len(trades), max_trades)} of {len(trades)})",
            show_header=True,
            header_style="bold green",
        )
        ttable.add_column("#", justify="right")
        ttable.add_column("Entry Time")
        ttable.add_column("Exit Time")
        ttable.add_column("Entry $", justify="right")
        ttable.add_column("Exit $", justify="right")
        ttable.add_column("PnL $", justify="right")
        ttable.add_column("PnL %", justify="right")
        ttable.add_column("Exit Reason")

        for i, t in enumerate(trades[:max_trades], 1):
            pnl_style = "green" if t.pnl_usdt >= Decimal("0") else "red"
            ttable.add_row(
                str(i),
                f"{t.entry_time:%Y-%m-%d %H:%M}",
                f"{t.exit_time:%Y-%m-%d %H:%M}",
                f"{t.entry_price:,.2f}",
                f"{t.exit_price:,.2f}",
                Text(f"{t.pnl_usdt:+,.2f}", style=pnl_style),
                Text(f"{t.pnl_percent:+.2f}%", style=pnl_style),
                t.exit_reason.value,
            )

        con.print(ttable)


# ── Database persistence ─────────────────────────────────────────────────────


def persist_to_database(
    metrics: BacktestMetrics,
    result: BacktestResult,
    backtest_config: Dict[str, Any] | None = None,
    *,
    config_snapshot: Dict[str, Any] | None = None,
    session: Any | None = None,
) -> None:
    """
    Persist metrics, trades, and equity-curve snapshots to Postgres.

    Respects ``save_trades``, ``save_equity_curve``, and
    ``save_metrics`` flags from *backtest_config*.

    Args:
        metrics: Computed backtest metrics.
        result: Raw backtest result.
        backtest_config: ``backtest`` section of settings.yaml.
        config_snapshot: Full config dict for reproducibility.
        session: Optional SQLAlchemy session (if None, opens one via ``get_session``).
    """
    from db import get_session
    from db.utils import (
        backtest_metrics_to_orm,
        portfolio_snapshot_to_orm,
        trade_to_orm,
    )

    cfg = backtest_config or {}
    save_metrics = cfg.get("save_metrics", True)
    save_trades = cfg.get("save_trades", True)
    save_equity_curve = cfg.get("save_equity_curve", True)

    run_id = metrics.run_id

    def _do_persist(sess: Any) -> None:
        # ── Metrics row ──────────────────────────────────────────
        if save_metrics:
            orm_result = backtest_metrics_to_orm(metrics)
            orm_result.config_snapshot = config_snapshot
            sess.add(orm_result)
            # Flush so FK references to run_id are valid
            sess.flush()

        # ── Trades ───────────────────────────────────────────────
        if save_trades and result.trades:
            trade_records = [
                trade_to_orm(t, run_id=run_id) for t in result.trades
            ]
            sess.add_all(trade_records)

        # ── Equity curve snapshots ───────────────────────────────
        if save_equity_curve and result.equity_curve:
            snap_records = [
                portfolio_snapshot_to_orm(s, run_id=run_id)
                for s in result.equity_curve
            ]
            sess.add_all(snap_records)

    if session is not None:
        _do_persist(session)
    else:
        with get_session() as sess:
            _do_persist(sess)


# ── Private formatting helpers ───────────────────────────────────────────────


def _fmt_pct(v: Optional[Decimal]) -> str:
    """Format Decimal as percentage string."""
    if v is None:
        return "—"
    return f"{v:.2f}%"


def _fmt_dec(v: Optional[Decimal]) -> str:
    """Format Decimal to 4 decimal places."""
    if v is None:
        return "—"
    return f"{v:.4f}"


def _fmt_int(v: Optional[int]) -> str:
    """Format optional int."""
    if v is None:
        return "—"
    return str(v)
