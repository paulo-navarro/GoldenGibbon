"""
Backtest metrics computation — pure-function API.

Usage::

    from core.backtest.metrics import compute_metrics

    result: BacktestResult = runner.run(...)
    metrics: BacktestMetrics = compute_metrics(result)

All inputs come from ``BacktestResult``.  The function has **no side
effects** and never touches the database or filesystem.
"""

import math
import uuid
from decimal import Decimal, ROUND_HALF_UP
from typing import List

from core.models import BacktestMetrics, BacktestResult, PortfolioSnapshot, Trade

# Annualisation factor: √(365.25 × 24 × 4) snapshots per year when
# each snapshot is one 15-minute candle.  We derive dynamically from
# the equity-curve length and date range so callers don't need to
# specify the timeframe.

_ZERO = Decimal("0")
_HUNDRED = Decimal("100")

# ── Public API ───────────────────────────────────────────────────────────────


def compute_metrics(result: BacktestResult) -> BacktestMetrics:
    """
    Derive a full ``BacktestMetrics`` from a ``BacktestResult``.

    Args:
        result: Output of ``BacktestRunner.run()``.

    Returns:
        A populated ``BacktestMetrics`` model.
    """
    initial_capital = result.portfolio.equity_curve[0].total_equity if result.equity_curve else result.portfolio.usdt_balance + result.portfolio.positions_value
    final_capital = result.equity_curve[-1].total_equity if result.equity_curve else initial_capital

    total_return = _pct(final_capital, initial_capital)

    trades = result.trades

    winning = [t for t in trades if t.pnl_usdt > _ZERO]
    losing = [t for t in trades if t.pnl_usdt < _ZERO]
    breakeven = [t for t in trades if t.pnl_usdt == _ZERO]

    total_trades = len(trades)
    winning_count = len(winning)
    losing_count = len(losing) + len(breakeven)  # breakeven counted as loss

    win_rate = (
        _dec(winning_count) / _dec(total_trades) * _HUNDRED
        if total_trades > 0
        else _ZERO
    )

    avg_win = (
        sum(t.pnl_percent for t in winning) / _dec(winning_count)
        if winning_count > 0
        else _ZERO
    )
    avg_loss = (
        sum(t.pnl_percent for t in losing) / _dec(len(losing))
        if losing
        else _ZERO
    )

    max_dd = _max_drawdown(result.equity_curve)

    sharpe = _sharpe_ratio(result.equity_curve)

    profit_factor = _profit_factor(trades)

    avg_dur = _avg_duration(trades)

    max_wins, max_losses = _consecutive_streaks(trades)

    buy_hold_return = _buy_hold_return(result)
    buy_hold_vs = (
        total_return - buy_hold_return
        if buy_hold_return is not None
        else None
    )

    return BacktestMetrics(
        run_id=str(uuid.uuid4()),
        strategy=result.strategy_name,
        symbol=result.symbol,
        start_date=result.start_time,
        end_date=result.end_time,
        initial_capital=initial_capital,
        final_capital=final_capital,
        total_return=_round4(total_return),
        total_trades=total_trades,
        winning_trades=winning_count,
        losing_trades=losing_count,
        win_rate=_round2(win_rate),
        avg_win=_round4(avg_win),
        avg_loss=_round4(avg_loss),
        max_drawdown=_round4(max_dd),
        sharpe_ratio=_round4(sharpe) if sharpe is not None else None,
        profit_factor=_round4(profit_factor) if profit_factor is not None else None,
        avg_trade_duration_minutes=avg_dur,
        max_consecutive_wins=max_wins,
        max_consecutive_losses=max_losses,
        buy_hold_return=_round4(buy_hold_return) if buy_hold_return is not None else None,
        buy_hold_vs_strategy=_round4(buy_hold_vs) if buy_hold_vs is not None else None,
    )


# ── Private helpers ──────────────────────────────────────────────────────────


def _dec(v: int | float | Decimal) -> Decimal:
    return Decimal(str(v))


def _pct(final: Decimal, initial: Decimal) -> Decimal:
    """Return percentage change from *initial* to *final*."""
    if initial == _ZERO:
        return _ZERO
    return (final - initial) / initial * _HUNDRED


def _round2(v: Decimal) -> Decimal:
    return v.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _round4(v: Decimal) -> Decimal:
    return v.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


def _max_drawdown(equity_curve: List[PortfolioSnapshot]) -> Decimal:
    """
    Maximum peak-to-trough drawdown as a *positive* percentage.

    Returns ``Decimal("0")`` when the curve is empty or monotonically
    rising.
    """
    if not equity_curve:
        return _ZERO

    peak = equity_curve[0].total_equity
    max_dd = _ZERO

    for snap in equity_curve:
        equity = snap.total_equity
        if equity > peak:
            peak = equity
        if peak > _ZERO:
            dd = (peak - equity) / peak * _HUNDRED
            if dd > max_dd:
                max_dd = dd

    return max_dd


def _sharpe_ratio(equity_curve: List[PortfolioSnapshot]) -> Decimal | None:
    """
    Annualised Sharpe ratio from per-snapshot simple returns.

    Assumes a risk-free rate of 0.  Returns ``None`` when the standard
    deviation is zero (flat equity) or there are fewer than 2 snapshots.

    The annualisation factor is derived from the date range and number
    of snapshots so it works for any timeframe.
    """
    if len(equity_curve) < 2:
        return None

    # Per-snapshot simple returns
    returns: list[float] = []
    for i in range(1, len(equity_curve)):
        prev = float(equity_curve[i - 1].total_equity)
        curr = float(equity_curve[i].total_equity)
        if prev != 0:
            returns.append((curr - prev) / prev)
        else:
            returns.append(0.0)

    if not returns:
        return None

    mean_ret = sum(returns) / len(returns)
    variance = sum((r - mean_ret) ** 2 for r in returns) / len(returns)
    std_ret = math.sqrt(variance)

    if std_ret == 0.0:
        return None

    # Derive annualisation factor from the equity curve's time span
    total_seconds = (
        equity_curve[-1].timestamp - equity_curve[0].timestamp
    ).total_seconds()
    if total_seconds <= 0:
        return None

    n_snapshots = len(equity_curve)
    snapshots_per_year = n_snapshots / total_seconds * 365.25 * 24 * 3600

    annualisation = math.sqrt(snapshots_per_year)

    sharpe = (mean_ret / std_ret) * annualisation
    return _dec(sharpe)


def _profit_factor(trades: List[Trade]) -> Decimal | None:
    """
    Gross profit / gross loss.

    Returns ``None`` when there are no losing trades (infinite PF)
    or no trades at all.
    """
    gross_profit = sum((t.pnl_usdt for t in trades if t.pnl_usdt > _ZERO), _ZERO)
    gross_loss = sum((abs(t.pnl_usdt) for t in trades if t.pnl_usdt < _ZERO), _ZERO)

    if gross_loss == _ZERO:
        return None
    return gross_profit / gross_loss


def _avg_duration(trades: List[Trade]) -> int | None:
    """Average trade duration in minutes, or ``None`` if no trades."""
    if not trades:
        return None
    total = sum(t.duration_minutes for t in trades)
    return total // len(trades)


def _consecutive_streaks(trades: List[Trade]) -> tuple[int, int]:
    """
    Return (max_consecutive_wins, max_consecutive_losses).

    Returns ``(0, 0)`` when there are no trades.
    """
    max_wins = 0
    max_losses = 0
    cur_wins = 0
    cur_losses = 0

    for t in trades:
        if t.pnl_usdt > _ZERO:
            cur_wins += 1
            cur_losses = 0
        else:
            cur_losses += 1
            cur_wins = 0
        max_wins = max(max_wins, cur_wins)
        max_losses = max(max_losses, cur_losses)

    return max_wins, max_losses


def _buy_hold_return(result: BacktestResult) -> Decimal | None:
    """
    Buy & Hold return percentage.

    Uses ``benchmark_first_price`` and ``benchmark_last_price``
    stored on the result by the ``BacktestRunner``.  Returns ``None``
    when the prices aren't available.
    """
    first = result.benchmark_first_price
    last = result.benchmark_last_price
    if first is None or last is None or first == _ZERO:
        return None
    return (last - first) / first * _HUNDRED
