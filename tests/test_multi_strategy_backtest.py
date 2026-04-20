"""
Tests for the multi-strategy backtest module (task 5.4d).

Verifies that ``run_multi_strategy_backtest`` produces a combined equity
curve, per-strategy breakdown, and regime timeline.
"""

from __future__ import annotations

from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from core.models import MarketData


# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_multi_tf_market_data(symbol: str = "BTCUSDT") -> MarketData:
    np.random.seed(42)
    rows = 250
    dates_15m = pd.date_range("2025-01-01", periods=rows, freq="15min", tz="UTC")
    close = 50000.0 + np.cumsum(np.random.randn(rows) * 10)
    df_15m = pd.DataFrame(
        {
            "open": close - 5,
            "high": close + 10,
            "low": close - 10,
            "close": close,
            "volume": np.abs(np.random.randn(rows) * 1000 + 5000),
        },
        index=dates_15m,
    )

    rows_1h = rows // 4
    dates_1h = pd.date_range("2025-01-01", periods=rows_1h, freq="1h", tz="UTC")
    close_1h = 50000.0 + np.cumsum(np.random.randn(rows_1h) * 20)
    df_1h = pd.DataFrame(
        {
            "open": close_1h - 10,
            "high": close_1h + 20,
            "low": close_1h - 20,
            "close": close_1h,
            "volume": np.abs(np.random.randn(rows_1h) * 4000 + 20000),
        },
        index=dates_1h,
    )

    indicators = {
        "ema_fast": pd.Series(close, index=dates_15m),
        "ema_slow": pd.Series(close, index=dates_15m),
        "adx": pd.Series(np.full(rows, 25.0), index=dates_15m),
        "atr": pd.Series(np.full(rows, 100.0), index=dates_15m),
        "rsi": pd.Series(np.full(rows, 50.0), index=dates_15m),
        "volume_sma": pd.Series(np.full(rows, 5000.0), index=dates_15m),
        "bb_upper": pd.Series(close + 200, index=dates_15m),
        "bb_middle": pd.Series(close, index=dates_15m),
        "bb_lower": pd.Series(close - 200, index=dates_15m),
    }

    sec_indicators = {
        "ema_fast": pd.Series(close_1h, index=dates_1h),
        "ema_slow": pd.Series(close_1h, index=dates_1h),
        "adx": pd.Series(np.full(rows_1h, 25.0), index=dates_1h),
        "atr": pd.Series(np.full(rows_1h, 100.0), index=dates_1h),
        "rsi": pd.Series(np.full(rows_1h, 50.0), index=dates_1h),
        "volume_sma": pd.Series(np.full(rows_1h, 20000.0), index=dates_1h),
        "bb_upper": pd.Series(close_1h + 400, index=dates_1h),
        "bb_middle": pd.Series(close_1h, index=dates_1h),
        "bb_lower": pd.Series(close_1h - 400, index=dates_1h),
    }

    return MarketData(
        symbol=symbol,
        timeframe="15m",
        candles=df_15m,
        indicators=indicators,
        secondary_timeframe="1h",
        secondary_candles=df_1h,
        secondary_indicators=sec_indicators,
    )


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _reset_registry():
    from core.strategies.registry import reset_registry
    reset_registry()
    yield
    reset_registry()


# ── Tests ────────────────────────────────────────────────────────────────────

_PATCH_LOADER = "core.data.loader.DataLoader.get_multi_timeframe_market_data"
_PATCH_KLINES = "core.data.binance_client.BinanceClient.fetch_klines"


class TestMultiStrategyBacktest:

    @patch(_PATCH_KLINES, return_value=[])
    @patch(_PATCH_LOADER)
    def test_returns_per_strategy_breakdown(self, mock_loader, mock_klines):
        mock_loader.return_value = _make_multi_tf_market_data()

        from core.backtest.multi_strategy import run_multi_strategy_backtest

        result = run_multi_strategy_backtest(
            symbols=["BTCUSDT"],
            days=30,
            strategy_names=["smart_hodler", "mean_reversion"],
        )

        assert len(result.per_strategy) == 2
        strategies = {s.strategy for s in result.per_strategy}
        assert strategies == {"smart_hodler", "mean_reversion"}

    @patch(_PATCH_KLINES, return_value=[])
    @patch(_PATCH_LOADER)
    def test_combined_equity_curve_not_empty(self, mock_loader, mock_klines):
        mock_loader.return_value = _make_multi_tf_market_data()

        from core.backtest.multi_strategy import run_multi_strategy_backtest

        result = run_multi_strategy_backtest(
            symbols=["BTCUSDT"],
            days=30,
            strategy_names=["smart_hodler"],
        )

        assert len(result.combined_equity_curve) > 0
        point = result.combined_equity_curve[0]
        assert "timestamp" in point
        assert "equity" in point

    @patch(_PATCH_KLINES, return_value=[])
    @patch(_PATCH_LOADER)
    def test_total_return_computed(self, mock_loader, mock_klines):
        mock_loader.return_value = _make_multi_tf_market_data()

        from core.backtest.multi_strategy import run_multi_strategy_backtest

        result = run_multi_strategy_backtest(
            symbols=["BTCUSDT"],
            days=30,
            strategy_names=["smart_hodler"],
        )

        assert result.total_initial_capital != "0"
        assert result.total_final_equity != "0"

    @patch(_PATCH_KLINES, return_value=[])
    @patch(_PATCH_LOADER)
    def test_regime_timeline_populated(self, mock_loader, mock_klines):
        mock_loader.return_value = _make_multi_tf_market_data()

        from core.backtest.multi_strategy import run_multi_strategy_backtest

        result = run_multi_strategy_backtest(
            symbols=["BTCUSDT"],
            days=30,
            strategy_names=["smart_hodler"],
        )

        # With constant ADX=25 (on the boundary), we should get at least one regime event
        for evt in result.regime_timeline:
            assert evt.regime in ("trending", "ranging", "uncertain")
            assert 0 <= evt.confidence <= 1

    @patch(_PATCH_KLINES, return_value=[])
    @patch(_PATCH_LOADER)
    def test_unknown_strategy_in_errors(self, mock_loader, mock_klines):
        from core.backtest.multi_strategy import run_multi_strategy_backtest

        result = run_multi_strategy_backtest(
            symbols=["BTCUSDT"],
            days=30,
            strategy_names=["nonexistent_strategy"],
        )

        assert len(result.per_strategy) == 0
        assert len(result.errors) == 1
        assert "unknown" in result.errors[0]["error"]

    @patch(_PATCH_KLINES, return_value=[])
    @patch(_PATCH_LOADER)
    def test_empty_candles_in_errors(self, mock_loader, mock_klines):
        empty_md = MarketData(
            symbol="BTCUSDT",
            timeframe="15m",
            candles=pd.DataFrame(columns=["open", "high", "low", "close", "volume"]),
            indicators={},
        )
        mock_loader.return_value = empty_md

        from core.backtest.multi_strategy import run_multi_strategy_backtest

        result = run_multi_strategy_backtest(
            symbols=["BTCUSDT"],
            days=30,
            strategy_names=["smart_hodler"],
        )

        assert len(result.per_strategy) == 0
        assert any("no candles" in e.get("error", "") for e in result.errors)

    @patch(_PATCH_KLINES, return_value=[])
    @patch(_PATCH_LOADER)
    def test_multiple_symbols(self, mock_loader, mock_klines):
        mock_loader.return_value = _make_multi_tf_market_data()

        from core.backtest.multi_strategy import run_multi_strategy_backtest

        result = run_multi_strategy_backtest(
            symbols=["BTCUSDT", "ETHUSDT"],
            days=30,
            strategy_names=["smart_hodler"],
        )

        assert len(result.per_strategy) == 2
        symbols = {s.symbol for s in result.per_strategy}
        assert symbols == {"BTCUSDT", "ETHUSDT"}

    @patch(_PATCH_KLINES, return_value=[])
    @patch(_PATCH_LOADER)
    def test_date_range_populated(self, mock_loader, mock_klines):
        mock_loader.return_value = _make_multi_tf_market_data()

        from core.backtest.multi_strategy import run_multi_strategy_backtest

        result = run_multi_strategy_backtest(
            symbols=["BTCUSDT"],
            days=30,
            strategy_names=["smart_hodler"],
        )

        assert result.date_range is not None
        assert "→" in result.date_range

    @patch(_PATCH_KLINES, return_value=[])
    @patch(_PATCH_LOADER)
    def test_allocated_capital_in_breakdown(self, mock_loader, mock_klines):
        mock_loader.return_value = _make_multi_tf_market_data()

        from core.backtest.multi_strategy import run_multi_strategy_backtest

        result = run_multi_strategy_backtest(
            symbols=["BTCUSDT"],
            days=30,
            strategy_names=["smart_hodler", "mean_reversion"],
        )

        for bd in result.per_strategy:
            assert float(bd.allocated_capital) > 0

    @patch(_PATCH_KLINES, return_value=[])
    @patch(_PATCH_LOADER)
    def test_empty_strategies_returns_empty(self, mock_loader, mock_klines):
        from core.backtest.multi_strategy import run_multi_strategy_backtest

        result = run_multi_strategy_backtest(
            symbols=["BTCUSDT"],
            days=30,
            strategy_names=[],
        )

        assert result.per_strategy == []
        assert result.combined_equity_curve == []

    @patch(_PATCH_KLINES, return_value=[])
    @patch(_PATCH_LOADER)
    def test_empty_symbols_returns_empty(self, mock_loader, mock_klines):
        from core.backtest.multi_strategy import run_multi_strategy_backtest

        result = run_multi_strategy_backtest(
            symbols=[],
            days=30,
            strategy_names=["smart_hodler"],
        )

        assert result.per_strategy == []
        assert result.combined_equity_curve == []


class TestMergeEquityCurves:

    def test_merges_by_timestamp(self):
        from datetime import datetime
        from decimal import Decimal
        from core.backtest.multi_strategy import _merge_equity_curves
        from core.models import PortfolioSnapshot

        ts1 = datetime(2025, 1, 1, 0, 0)
        ts2 = datetime(2025, 1, 1, 0, 15)

        snap_kwargs = dict(usdt_balance=Decimal("0"), positions_value=Decimal("0"))
        curves = {
            "a:BTC": [
                PortfolioSnapshot(timestamp=ts1, total_equity=Decimal("5000"), total_pnl=Decimal("0"), **snap_kwargs),
                PortfolioSnapshot(timestamp=ts2, total_equity=Decimal("5100"), total_pnl=Decimal("100"), **snap_kwargs),
            ],
            "b:BTC": [
                PortfolioSnapshot(timestamp=ts1, total_equity=Decimal("3000"), total_pnl=Decimal("0"), **snap_kwargs),
                PortfolioSnapshot(timestamp=ts2, total_equity=Decimal("2900"), total_pnl=Decimal("-100"), **snap_kwargs),
            ],
        }

        merged = _merge_equity_curves(curves)

        assert len(merged) == 2
        assert merged[0]["equity"] == "8000.00"
        assert merged[1]["equity"] == "8000.00"

    def test_empty_curves(self):
        from core.backtest.multi_strategy import _merge_equity_curves

        assert _merge_equity_curves({}) == []
