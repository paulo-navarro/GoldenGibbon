"""
Unit tests for ``compute_metrics()`` (task 1.20).

Covers:

    - No trades (empty result)
    - Single winning trade
    - Single losing trade
    - Mixed trades — win rate, profit factor, avg win/loss, streaks
    - Flat equity curve → Sharpe is None
    - Rising equity curve → positive Sharpe
    - Max drawdown calculation
    - Buy & Hold return / vs strategy
    - Benchmark fields absent → buy-hold is None
    - Edge cases: one snapshot, breakeven trade
"""

from datetime import datetime, timedelta
from decimal import Decimal
from typing import List, Optional

import pytest

from core.backtest.metrics import compute_metrics
from core.models import (
    BacktestMetrics,
    BacktestResult,
    ExitReason,
    Portfolio,
    PortfolioSnapshot,
    Trade,
)


# ── Helpers ──────────────────────────────────────────────────────────────────

T0 = datetime(2025, 1, 1)


def _snap(
    minute: int,
    equity: str,
    balance: str | None = None,
) -> PortfolioSnapshot:
    """Quick snapshot builder."""
    eq = Decimal(equity)
    bal = Decimal(balance) if balance is not None else eq
    return PortfolioSnapshot(
        timestamp=T0 + timedelta(minutes=minute * 15),
        usdt_balance=bal,
        positions_value=eq - bal,
        total_equity=eq,
        total_pnl=eq - Decimal("10000"),
    )


def _trade(
    pnl_usdt: str,
    pnl_pct: str,
    duration: int = 60,
    exit_reason: ExitReason = ExitReason.EMA_CROSS,
    idx: int = 0,
) -> Trade:
    """Quick trade builder."""
    return Trade(
        symbol="BTCUSDT",
        strategy="test",
        entry_price=Decimal("50000"),
        exit_price=Decimal("50000") + Decimal(pnl_usdt),
        size=Decimal("1"),
        entry_time=T0 + timedelta(hours=idx),
        exit_time=T0 + timedelta(hours=idx, minutes=duration),
        pnl_usdt=Decimal(pnl_usdt),
        pnl_percent=Decimal(pnl_pct),
        duration_minutes=duration,
        exit_reason=exit_reason,
    )


def _result(
    trades: List[Trade] | None = None,
    equity_curve: List[PortfolioSnapshot] | None = None,
    initial_capital: str = "10000",
    benchmark_first: str | None = None,
    benchmark_last: str | None = None,
) -> BacktestResult:
    """Quick BacktestResult builder."""
    ic = Decimal(initial_capital)
    final_eq = equity_curve[-1].total_equity if equity_curve else ic
    final_bal = equity_curve[-1].usdt_balance if equity_curve else ic

    portfolio = Portfolio(
        usdt_balance=final_bal,
        equity=final_eq,
        trade_history=trades or [],
        equity_curve=equity_curve or [],
    )

    return BacktestResult(
        strategy_name="test_strategy",
        symbol="BTCUSDT",
        timeframe="15m",
        start_time=T0,
        end_time=T0 + timedelta(days=7),
        candles_processed=100,
        portfolio=portfolio,
        trades=trades or [],
        equity_curve=equity_curve or [],
        benchmark_first_price=Decimal(benchmark_first) if benchmark_first else None,
        benchmark_last_price=Decimal(benchmark_last) if benchmark_last else None,
    )


# ── Tests: No trades ────────────────────────────────────────────────────────


class TestNoTrades:
    """Metrics when the strategy never opens a position."""

    def test_no_trades_total_return_zero(self):
        curve = [_snap(0, "10000"), _snap(1, "10000")]
        m = compute_metrics(_result(trades=[], equity_curve=curve))
        assert m.total_return == Decimal("0.0000")

    def test_no_trades_win_rate_zero(self):
        curve = [_snap(0, "10000"), _snap(1, "10000")]
        m = compute_metrics(_result(trades=[], equity_curve=curve))
        assert m.win_rate == Decimal("0.00")
        assert m.total_trades == 0

    def test_no_trades_drawdown_zero(self):
        curve = [_snap(0, "10000"), _snap(1, "10000")]
        m = compute_metrics(_result(trades=[], equity_curve=curve))
        assert m.max_drawdown == Decimal("0.0000")

    def test_no_trades_profit_factor_none(self):
        curve = [_snap(0, "10000"), _snap(1, "10000")]
        m = compute_metrics(_result(trades=[], equity_curve=curve))
        assert m.profit_factor is None

    def test_no_trades_duration_none(self):
        curve = [_snap(0, "10000"), _snap(1, "10000")]
        m = compute_metrics(_result(trades=[], equity_curve=curve))
        assert m.avg_trade_duration_minutes is None

    def test_no_trades_streaks_zero(self):
        curve = [_snap(0, "10000"), _snap(1, "10000")]
        m = compute_metrics(_result(trades=[], equity_curve=curve))
        assert m.max_consecutive_wins == 0
        assert m.max_consecutive_losses == 0


# ── Tests: Single trade ─────────────────────────────────────────────────────


class TestSingleTrade:
    """Metrics with exactly one trade."""

    def test_single_win(self):
        t = _trade("500", "5.0")
        curve = [_snap(0, "10000"), _snap(4, "10500")]
        m = compute_metrics(_result(trades=[t], equity_curve=curve))
        assert m.total_trades == 1
        assert m.winning_trades == 1
        assert m.losing_trades == 0
        assert m.win_rate == Decimal("100.00")
        assert m.avg_win == Decimal("5.0000")
        assert m.avg_loss == Decimal("0.0000")
        assert m.max_consecutive_wins == 1
        assert m.max_consecutive_losses == 0

    def test_single_loss(self):
        t = _trade("-300", "-3.0")
        curve = [_snap(0, "10000"), _snap(4, "9700")]
        m = compute_metrics(_result(trades=[t], equity_curve=curve))
        assert m.total_trades == 1
        assert m.winning_trades == 0
        assert m.losing_trades == 1
        assert m.win_rate == Decimal("0.00")
        assert m.avg_win == Decimal("0.0000")
        assert m.avg_loss == Decimal("-3.0000")
        assert m.profit_factor is not None  # one loss, pf = 0 / loss → 0 / ... 
        assert m.max_consecutive_wins == 0
        assert m.max_consecutive_losses == 1

    def test_single_win_profit_factor_none(self):
        """Profit factor is None when there are zero losses."""
        t = _trade("200", "2.0")
        curve = [_snap(0, "10000"), _snap(4, "10200")]
        m = compute_metrics(_result(trades=[t], equity_curve=curve))
        assert m.profit_factor is None

    def test_breakeven_trade_counted_as_loss(self):
        """A $0 PnL trade is binned as a loss."""
        t = _trade("0", "0.0")
        curve = [_snap(0, "10000"), _snap(4, "10000")]
        m = compute_metrics(_result(trades=[t], equity_curve=curve))
        assert m.winning_trades == 0
        assert m.losing_trades == 1


# ── Tests: Mixed trades ─────────────────────────────────────────────────────


class TestMixedTrades:
    """Multiple trades — win rate, profit factor, avg, streaks."""

    @pytest.fixture()
    def trades(self) -> List[Trade]:
        return [
            _trade("500", "5.0", idx=0),    # win
            _trade("300", "3.0", idx=1),    # win
            _trade("-200", "-2.0", idx=2),  # loss
            _trade("-100", "-1.0", idx=3),  # loss
            _trade("600", "6.0", idx=4),    # win
        ]

    @pytest.fixture()
    def curve(self) -> List[PortfolioSnapshot]:
        return [
            _snap(0, "10000"),
            _snap(4, "10500"),
            _snap(8, "10800"),
            _snap(12, "10600"),
            _snap(16, "10500"),
            _snap(20, "11100"),
        ]

    def test_win_rate(self, trades, curve):
        m = compute_metrics(_result(trades=trades, equity_curve=curve))
        # 3 wins / 5 trades = 60%
        assert m.win_rate == Decimal("60.00")

    def test_avg_win(self, trades, curve):
        m = compute_metrics(_result(trades=trades, equity_curve=curve))
        # (5 + 3 + 6) / 3 = 4.6667
        assert m.avg_win == Decimal("4.6667")

    def test_avg_loss(self, trades, curve):
        m = compute_metrics(_result(trades=trades, equity_curve=curve))
        # (-2 + -1) / 2 = -1.5
        assert m.avg_loss == Decimal("-1.5000")

    def test_profit_factor(self, trades, curve):
        m = compute_metrics(_result(trades=trades, equity_curve=curve))
        # gross profit 1400 / gross loss 300 = 4.6667
        assert m.profit_factor == Decimal("4.6667")

    def test_max_consecutive_wins(self, trades, curve):
        m = compute_metrics(_result(trades=trades, equity_curve=curve))
        assert m.max_consecutive_wins == 2  # first two trades

    def test_max_consecutive_losses(self, trades, curve):
        m = compute_metrics(_result(trades=trades, equity_curve=curve))
        assert m.max_consecutive_losses == 2  # trades 3 and 4

    def test_total_return(self, trades, curve):
        m = compute_metrics(_result(trades=trades, equity_curve=curve))
        # (11100 - 10000) / 10000 * 100 = 11%
        assert m.total_return == Decimal("11.0000")

    def test_avg_duration(self, trades, curve):
        m = compute_metrics(_result(trades=trades, equity_curve=curve))
        # all trades duration=60
        assert m.avg_trade_duration_minutes == 60


# ── Tests: Equity curve / drawdown ───────────────────────────────────────────


class TestDrawdown:
    """Max drawdown calculation from the equity curve."""

    def test_monotonic_rise_no_drawdown(self):
        curve = [_snap(i, str(10000 + i * 100)) for i in range(10)]
        m = compute_metrics(_result(equity_curve=curve))
        assert m.max_drawdown == Decimal("0.0000")

    def test_simple_drawdown(self):
        """Peak 11000, trough 10000 → 9.0909%."""
        curve = [
            _snap(0, "10000"),
            _snap(1, "11000"),  # peak
            _snap(2, "10000"),  # trough
            _snap(3, "10500"),
        ]
        m = compute_metrics(_result(equity_curve=curve))
        # (11000 - 10000) / 11000 * 100 = 9.0909
        assert m.max_drawdown == Decimal("9.0909")

    def test_deeper_second_drawdown(self):
        """Second dip is larger than the first."""
        curve = [
            _snap(0, "10000"),
            _snap(1, "11000"),
            _snap(2, "10500"),  # first dip: 4.5455%
            _snap(3, "12000"),  # new peak
            _snap(4, "10200"),  # second dip: 15%
            _snap(5, "11800"),
        ]
        m = compute_metrics(_result(equity_curve=curve))
        # (12000 - 10200) / 12000 * 100 = 15%
        assert m.max_drawdown == Decimal("15.0000")

    def test_single_snapshot_no_drawdown(self):
        curve = [_snap(0, "10000")]
        m = compute_metrics(_result(equity_curve=curve))
        assert m.max_drawdown == Decimal("0.0000")


# ── Tests: Sharpe ratio ─────────────────────────────────────────────────────


class TestSharpe:
    """Sharpe ratio edge cases and basic validation."""

    def test_flat_equity_sharpe_none(self):
        curve = [_snap(i, "10000") for i in range(20)]
        m = compute_metrics(_result(equity_curve=curve))
        assert m.sharpe_ratio is None

    def test_single_snapshot_sharpe_none(self):
        curve = [_snap(0, "10000")]
        m = compute_metrics(_result(equity_curve=curve))
        assert m.sharpe_ratio is None

    def test_rising_equity_positive_sharpe(self):
        """Monotonically rising equity → positive Sharpe."""
        curve = [_snap(i, str(10000 + i * 10)) for i in range(50)]
        m = compute_metrics(_result(equity_curve=curve))
        assert m.sharpe_ratio is not None
        assert m.sharpe_ratio > Decimal("0")

    def test_volatile_equity_lower_sharpe(self):
        """Volatile equity has lower Sharpe than smooth rising."""
        smooth = [_snap(i, str(10000 + i * 10)) for i in range(50)]
        volatile = []
        for i in range(50):
            eq = 10000 + i * 10 + (100 if i % 2 == 0 else -100)
            volatile.append(_snap(i, str(eq)))
        m_smooth = compute_metrics(_result(equity_curve=smooth))
        m_volatile = compute_metrics(_result(equity_curve=volatile))
        assert m_smooth.sharpe_ratio > m_volatile.sharpe_ratio


# ── Tests: Buy & Hold ───────────────────────────────────────────────────────


class TestBuyHold:
    """Buy & Hold return and comparison to strategy."""

    def test_buy_hold_return(self):
        """50000 → 55000 = +10%."""
        curve = [_snap(0, "10000"), _snap(4, "11100")]
        m = compute_metrics(_result(
            equity_curve=curve,
            benchmark_first="50000",
            benchmark_last="55000",
        ))
        assert m.buy_hold_return == Decimal("10.0000")

    def test_buy_hold_vs_strategy(self):
        """Strategy +11%, B&H +10% → excess = +1%."""
        curve = [_snap(0, "10000"), _snap(4, "11100")]
        m = compute_metrics(_result(
            equity_curve=curve,
            benchmark_first="50000",
            benchmark_last="55000",
        ))
        # total_return = 11%, buy_hold = 10% → diff = 1%
        assert m.buy_hold_vs_strategy == Decimal("1.0000")

    def test_no_benchmark_buy_hold_none(self):
        """Without benchmark prices, buy-hold fields are None."""
        curve = [_snap(0, "10000"), _snap(4, "11100")]
        m = compute_metrics(_result(equity_curve=curve))
        assert m.buy_hold_return is None
        assert m.buy_hold_vs_strategy is None

    def test_buy_hold_negative(self):
        """Benchmark drops: 50000 → 45000 = -10%."""
        curve = [_snap(0, "10000"), _snap(4, "10000")]
        m = compute_metrics(_result(
            equity_curve=curve,
            benchmark_first="50000",
            benchmark_last="45000",
        ))
        assert m.buy_hold_return == Decimal("-10.0000")


# ── Tests: Result metadata ──────────────────────────────────────────────────


class TestMetadata:
    """Verify metadata fields are passed through correctly."""

    def test_run_id_is_uuid(self):
        import uuid
        curve = [_snap(0, "10000"), _snap(1, "10000")]
        m = compute_metrics(_result(equity_curve=curve))
        # Should not raise
        uuid.UUID(m.run_id)

    def test_strategy_and_symbol(self):
        curve = [_snap(0, "10000"), _snap(1, "10000")]
        m = compute_metrics(_result(equity_curve=curve))
        assert m.strategy == "test_strategy"
        assert m.symbol == "BTCUSDT"

    def test_dates_match_result(self):
        curve = [_snap(0, "10000"), _snap(1, "10000")]
        r = _result(equity_curve=curve)
        m = compute_metrics(r)
        assert m.start_date == r.start_time
        assert m.end_date == r.end_time


# ── Tests: Edge cases ────────────────────────────────────────────────────────


class TestEdgeCases:
    """Edge cases and boundary conditions."""

    def test_all_winning_trades(self):
        trades = [_trade("100", "1.0", idx=i) for i in range(5)]
        curve = [_snap(0, "10000"), _snap(20, "10500")]
        m = compute_metrics(_result(trades=trades, equity_curve=curve))
        assert m.winning_trades == 5
        assert m.losing_trades == 0
        assert m.profit_factor is None  # no losses

    def test_all_losing_trades(self):
        trades = [_trade("-100", "-1.0", idx=i) for i in range(5)]
        curve = [_snap(0, "10000"), _snap(20, "9500")]
        m = compute_metrics(_result(trades=trades, equity_curve=curve))
        assert m.winning_trades == 0
        assert m.losing_trades == 5
        assert m.profit_factor is not None
        assert m.profit_factor == Decimal("0.0000")

    def test_empty_equity_curve(self):
        """No snapshots at all — should not crash."""
        m = compute_metrics(_result(trades=[], equity_curve=[]))
        assert m.total_return == Decimal("0.0000")
        assert m.max_drawdown == Decimal("0.0000")
        assert m.sharpe_ratio is None

    def test_initial_capital_from_first_snapshot(self):
        """initial_capital is read from the first snapshot's equity."""
        curve = [_snap(0, "5000"), _snap(4, "5500")]
        m = compute_metrics(_result(equity_curve=curve, initial_capital="5000"))
        assert m.initial_capital == Decimal("5000")
        assert m.total_return == Decimal("10.0000")
