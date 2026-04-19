"""
Tests for the parameter optimization module (task 5.6).

Verifies grid search ranking, walk-forward fold splitting, and edge cases.
"""

from __future__ import annotations

from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from core.models import MarketData


# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_multi_tf_market_data(symbol: str = "BTCUSDT", rows: int = 250) -> MarketData:
    np.random.seed(42)
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


class TestGridSearch:

    @patch(_PATCH_KLINES, return_value=[])
    @patch(_PATCH_LOADER)
    def test_returns_sorted_results(self, mock_loader, mock_klines):
        mock_loader.return_value = _make_multi_tf_market_data()

        from core.backtest.optimize import grid_search

        result = grid_search(
            strategy_name="smart_hodler",
            symbol="BTCUSDT",
            days=30,
            param_grid={"adx_threshold": [20, 25, 30]},
            metric="sharpe_ratio",
        )

        assert result.total_combos == 3
        assert len(result.results) == 3
        # Results should be sorted by sharpe (descending)
        sharpes = [r.sharpe_ratio for r in result.results if r.sharpe_ratio is not None]
        assert sharpes == sorted(sharpes, reverse=True) or len(sharpes) == 0

    @patch(_PATCH_KLINES, return_value=[])
    @patch(_PATCH_LOADER)
    def test_best_params_set(self, mock_loader, mock_klines):
        mock_loader.return_value = _make_multi_tf_market_data()

        from core.backtest.optimize import grid_search

        result = grid_search(
            strategy_name="smart_hodler",
            symbol="BTCUSDT",
            days=30,
            param_grid={"adx_threshold": [20, 25]},
        )

        assert result.best_params is not None
        assert "adx_threshold" in result.best_params

    @patch(_PATCH_KLINES, return_value=[])
    @patch(_PATCH_LOADER)
    def test_multi_param_grid(self, mock_loader, mock_klines):
        mock_loader.return_value = _make_multi_tf_market_data()

        from core.backtest.optimize import grid_search

        result = grid_search(
            strategy_name="smart_hodler",
            symbol="BTCUSDT",
            days=30,
            param_grid={"adx_threshold": [20, 25], "ema_fast": [30, 50]},
        )

        assert result.total_combos == 4
        assert len(result.results) == 4

    def test_unknown_strategy(self):
        from core.backtest.optimize import grid_search

        result = grid_search(
            strategy_name="nonexistent",
            symbol="BTCUSDT",
            days=30,
            param_grid={"x": [1]},
        )

        assert len(result.errors) > 0
        assert "Unknown" in result.errors[0]

    def test_empty_grid(self):
        from core.backtest.optimize import grid_search

        result = grid_search(
            strategy_name="smart_hodler",
            symbol="BTCUSDT",
            days=30,
            param_grid={},
        )

        assert len(result.errors) > 0
        assert "Empty" in result.errors[0]

    @patch(_PATCH_KLINES, return_value=[])
    @patch(_PATCH_LOADER)
    def test_empty_candles(self, mock_loader, mock_klines):
        empty_md = MarketData(
            symbol="BTCUSDT",
            timeframe="15m",
            candles=pd.DataFrame(columns=["open", "high", "low", "close", "volume"]),
            indicators={},
        )
        mock_loader.return_value = empty_md

        from core.backtest.optimize import grid_search

        result = grid_search(
            strategy_name="smart_hodler",
            symbol="BTCUSDT",
            days=30,
            param_grid={"adx_threshold": [20]},
        )

        assert len(result.errors) > 0
        assert "No candle" in result.errors[0]

    @patch(_PATCH_KLINES, return_value=[])
    @patch(_PATCH_LOADER)
    def test_results_have_metrics(self, mock_loader, mock_klines):
        mock_loader.return_value = _make_multi_tf_market_data()

        from core.backtest.optimize import grid_search

        result = grid_search(
            strategy_name="smart_hodler",
            symbol="BTCUSDT",
            days=30,
            param_grid={"adx_threshold": [25]},
        )

        assert len(result.results) == 1
        row = result.results[0]
        assert row.total_trades >= 0
        assert row.win_rate >= 0
        assert row.max_drawdown <= 0 or row.total_trades == 0

    @patch(_PATCH_KLINES, return_value=[])
    @patch(_PATCH_LOADER)
    def test_sort_by_total_return(self, mock_loader, mock_klines):
        mock_loader.return_value = _make_multi_tf_market_data()

        from core.backtest.optimize import grid_search

        result = grid_search(
            strategy_name="smart_hodler",
            symbol="BTCUSDT",
            days=30,
            param_grid={"adx_threshold": [20, 25, 30]},
            metric="total_return",
        )

        returns = [r.total_return for r in result.results]
        assert returns == sorted(returns, reverse=True)


class TestWalkForward:

    @patch(_PATCH_KLINES, return_value=[])
    @patch(_PATCH_LOADER)
    def test_returns_folds(self, mock_loader, mock_klines):
        mock_loader.return_value = _make_multi_tf_market_data(rows=500)

        from core.backtest.optimize import walk_forward

        result = walk_forward(
            strategy_name="smart_hodler",
            symbol="BTCUSDT",
            days=60,
            param_grid={"adx_threshold": [20, 25]},
            n_folds=3,
        )

        assert result.n_folds == 3
        assert len(result.folds) <= 3
        for fold in result.folds:
            assert fold.fold >= 1
            assert fold.best_params is not None

    @patch(_PATCH_KLINES, return_value=[])
    @patch(_PATCH_LOADER)
    def test_fold_has_train_and_test(self, mock_loader, mock_klines):
        mock_loader.return_value = _make_multi_tf_market_data(rows=500)

        from core.backtest.optimize import walk_forward

        result = walk_forward(
            strategy_name="smart_hodler",
            symbol="BTCUSDT",
            days=60,
            param_grid={"adx_threshold": [20, 25]},
            n_folds=2,
        )

        for fold in result.folds:
            assert fold.train_range
            assert fold.test_range

    def test_unknown_strategy(self):
        from core.backtest.optimize import walk_forward

        result = walk_forward(
            strategy_name="nonexistent",
            symbol="BTCUSDT",
            days=30,
            param_grid={"x": [1]},
        )

        assert len(result.errors) > 0

    def test_empty_grid(self):
        from core.backtest.optimize import walk_forward

        result = walk_forward(
            strategy_name="smart_hodler",
            symbol="BTCUSDT",
            days=30,
            param_grid={},
        )

        assert len(result.errors) > 0

    @patch(_PATCH_KLINES, return_value=[])
    @patch(_PATCH_LOADER)
    def test_too_few_candles(self, mock_loader, mock_klines):
        small_md = _make_multi_tf_market_data(rows=30)
        mock_loader.return_value = small_md

        from core.backtest.optimize import walk_forward

        result = walk_forward(
            strategy_name="smart_hodler",
            symbol="BTCUSDT",
            days=30,
            param_grid={"adx_threshold": [20]},
            n_folds=10,
        )

        assert len(result.errors) > 0 or len(result.folds) == 0

    @patch(_PATCH_KLINES, return_value=[])
    @patch(_PATCH_LOADER)
    def test_averages_computed(self, mock_loader, mock_klines):
        mock_loader.return_value = _make_multi_tf_market_data(rows=500)

        from core.backtest.optimize import walk_forward

        result = walk_forward(
            strategy_name="smart_hodler",
            symbol="BTCUSDT",
            days=60,
            param_grid={"adx_threshold": [20, 25]},
            n_folds=2,
        )

        if result.folds:
            assert result.avg_test_return is not None


class TestHelpers:

    def test_sort_key_higher_is_better(self):
        from core.backtest.optimize import GridSearchRow, _sort_key

        row_a = GridSearchRow(params={}, sharpe_ratio=2.0)
        row_b = GridSearchRow(params={}, sharpe_ratio=1.0)

        assert _sort_key(row_a, "sharpe_ratio") > _sort_key(row_b, "sharpe_ratio")

    def test_sort_key_lower_is_better(self):
        from core.backtest.optimize import GridSearchRow, _sort_key

        row_a = GridSearchRow(params={}, max_drawdown=-5.0)
        row_b = GridSearchRow(params={}, max_drawdown=-10.0)

        # max_drawdown: lower (less negative) is better → higher sort key
        assert _sort_key(row_a, "max_drawdown") > _sort_key(row_b, "max_drawdown")

    def test_sort_key_none_handled(self):
        from core.backtest.optimize import GridSearchRow, _sort_key

        row = GridSearchRow(params={}, sharpe_ratio=None)

        assert _sort_key(row, "sharpe_ratio") == float("-inf")
