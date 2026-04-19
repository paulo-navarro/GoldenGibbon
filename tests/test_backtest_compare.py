"""
Tests for the strategy comparison module (task 5.3).

Verifies that ``compare_strategies`` runs backtests for multiple
(strategy, symbol) pairs and collects side-by-side metrics.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

from core.models import MarketData


# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_multi_tf_market_data(symbol: str = "BTCUSDT") -> MarketData:
    """Build a synthetic MarketData with primary + secondary candles."""
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


class TestCompareStrategies:
    """Tests for compare_strategies()."""

    @patch(_PATCH_KLINES, return_value=[])
    @patch(_PATCH_LOADER)
    def test_returns_rows_for_all_pairs(self, mock_loader, mock_klines):
        mock_loader.return_value = _make_multi_tf_market_data()

        from core.backtest.compare import compare_strategies

        result = compare_strategies(
            symbols=["BTCUSDT"],
            days=30,
            strategy_names=["smart_hodler", "mean_reversion"],
        )

        assert len(result.rows) == 2
        strategies = {r.strategy for r in result.rows}
        assert strategies == {"smart_hodler", "mean_reversion"}

    @patch(_PATCH_KLINES, return_value=[])
    @patch(_PATCH_LOADER)
    def test_metrics_have_expected_fields(self, mock_loader, mock_klines):
        mock_loader.return_value = _make_multi_tf_market_data()

        from core.backtest.compare import compare_strategies

        result = compare_strategies(
            symbols=["BTCUSDT"],
            days=30,
            strategy_names=["smart_hodler"],
        )

        assert len(result.rows) == 1
        m = result.rows[0].metrics
        assert m.strategy == "smart_hodler"
        assert m.symbol == "BTCUSDT"
        assert m.total_trades >= 0
        assert m.win_rate is not None
        assert m.max_drawdown is not None

    @patch(_PATCH_KLINES, return_value=[])
    @patch(_PATCH_LOADER)
    def test_multiple_symbols(self, mock_loader, mock_klines):
        mock_loader.return_value = _make_multi_tf_market_data()

        from core.backtest.compare import compare_strategies

        result = compare_strategies(
            symbols=["BTCUSDT", "ETHUSDT"],
            days=30,
            strategy_names=["smart_hodler"],
        )

        assert len(result.rows) == 2
        symbols = {r.symbol for r in result.rows}
        assert symbols == {"BTCUSDT", "ETHUSDT"}

    @patch(_PATCH_KLINES, return_value=[])
    @patch(_PATCH_LOADER)
    def test_unknown_strategy_in_errors(self, mock_loader, mock_klines):
        from core.backtest.compare import compare_strategies

        result = compare_strategies(
            symbols=["BTCUSDT"],
            days=30,
            strategy_names=["nonexistent_strategy"],
        )

        assert len(result.rows) == 0
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

        from core.backtest.compare import compare_strategies

        result = compare_strategies(
            symbols=["BTCUSDT"],
            days=30,
            strategy_names=["smart_hodler"],
        )

        assert len(result.rows) == 0
        assert len(result.errors) == 1
        assert "no candles" in result.errors[0]["error"]

    @patch(_PATCH_KLINES, return_value=[])
    @patch(_PATCH_LOADER)
    def test_date_range_populated(self, mock_loader, mock_klines):
        mock_loader.return_value = _make_multi_tf_market_data()

        from core.backtest.compare import compare_strategies

        result = compare_strategies(
            symbols=["BTCUSDT"],
            days=30,
            strategy_names=["smart_hodler"],
        )

        assert result.date_range is not None
        assert "→" in result.date_range

    @patch(_PATCH_KLINES, return_value=[])
    @patch(_PATCH_LOADER)
    def test_defaults_to_all_strategies(self, mock_loader, mock_klines):
        """Passing strategy_names=None runs all registered strategies."""
        mock_loader.return_value = _make_multi_tf_market_data()

        from core.backtest.compare import compare_strategies

        result = compare_strategies(
            symbols=["BTCUSDT"],
            days=30,
        )

        strategies = {r.strategy for r in result.rows}
        assert "smart_hodler" in strategies
        assert "mean_reversion" in strategies
