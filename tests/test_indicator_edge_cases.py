"""
Edge-case tests for indicator calculations (gap 8).

Covers:
    - ATR / ADX with fewer data points than the period
    - EMA with period > series length
    - SMA with period > series length
    - RSI with period > series length
    - Bollinger Bands with period > series length
    - Empty DataFrame through IndicatorEngine.calculate_all
    - Single data point
    - Period = 0 or negative raises ValueError
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from core.indicators.technical import (
    calculate_adx,
    calculate_atr,
    calculate_bollinger_bands,
    calculate_ema,
    calculate_rsi,
    calculate_sma,
)
from core.indicators.engine import IndicatorEngine


# ── Helpers ───────────────────────────────────────────────────────────────────

def _ohlcv(n: int) -> pd.DataFrame:
    """Create a minimal OHLCV DataFrame with *n* rows."""
    close = pd.Series(range(100, 100 + n), dtype=float)
    return pd.DataFrame({
        "open": close - 1,
        "high": close + 2,
        "low": close - 2,
        "close": close,
        "volume": [1000.0] * n,
    })


# ── Empty Series ──────────────────────────────────────────────────────────────


class TestEmptySeries:

    def test_ema_empty(self):
        result = calculate_ema(pd.Series(dtype=float), 14)
        assert len(result) == 0

    def test_sma_empty(self):
        result = calculate_sma(pd.Series(dtype=float), 14)
        assert len(result) == 0

    def test_atr_empty(self):
        empty = pd.Series(dtype=float)
        result = calculate_atr(empty, empty, empty, 14)
        assert len(result) == 0

    def test_rsi_empty(self):
        result = calculate_rsi(pd.Series(dtype=float), 14)
        assert len(result) == 0

    def test_adx_empty(self):
        empty = pd.Series(dtype=float)
        result = calculate_adx(empty, empty, empty, 14)
        assert len(result) == 0

    def test_bollinger_empty(self):
        upper, middle, lower = calculate_bollinger_bands(pd.Series(dtype=float), 20)
        assert len(upper) == 0
        assert len(middle) == 0
        assert len(lower) == 0


# ── Invalid Period ────────────────────────────────────────────────────────────


class TestInvalidPeriod:

    def test_ema_period_zero_raises(self):
        with pytest.raises(ValueError):
            calculate_ema(pd.Series([1.0, 2.0]), 0)

    def test_ema_negative_period_raises(self):
        with pytest.raises(ValueError):
            calculate_ema(pd.Series([1.0]), -5)

    def test_sma_period_zero_raises(self):
        with pytest.raises(ValueError):
            calculate_sma(pd.Series([1.0, 2.0]), 0)

    def test_atr_period_zero_raises(self):
        s = pd.Series([1.0, 2.0])
        with pytest.raises(ValueError):
            calculate_atr(s, s, s, 0)

    def test_rsi_period_zero_raises(self):
        with pytest.raises(ValueError):
            calculate_rsi(pd.Series([1.0, 2.0]), 0)

    def test_adx_period_zero_raises(self):
        s = pd.Series([1.0, 2.0])
        with pytest.raises(ValueError):
            calculate_adx(s, s, s, 0)

    def test_bollinger_period_zero_raises(self):
        with pytest.raises(ValueError):
            calculate_bollinger_bands(pd.Series([1.0, 2.0]), 0)

    def test_bollinger_std_dev_zero_raises(self):
        with pytest.raises(ValueError):
            calculate_bollinger_bands(pd.Series([1.0, 2.0]), 20, std_dev=0)


# ── Period > Series Length ────────────────────────────────────────────────────


class TestPeriodExceedsLength:
    """When period > len(series), indicators should still return a Series
    (possibly all-NaN for SMA/BB, or computed with EWM warmup for EMA/ATR)."""

    def test_ema_period_gt_length_returns_series(self):
        """EMA uses ewm which always produces values (warmup, not NaN)."""
        result = calculate_ema(pd.Series([100.0, 101.0, 102.0]), period=50)
        assert len(result) == 3
        # ewm with adjust=False still produces values
        assert not result.isna().all()

    def test_sma_period_gt_length_all_nan(self):
        """SMA rolling window produces NaN when period > data length."""
        result = calculate_sma(pd.Series([100.0, 101.0, 102.0]), period=50)
        assert len(result) == 3
        assert result.isna().all()

    def test_atr_period_gt_length_returns_series(self):
        df = _ohlcv(3)
        result = calculate_atr(df["high"], df["low"], df["close"], period=50)
        assert len(result) == 3

    def test_adx_period_gt_length_returns_series(self):
        df = _ohlcv(3)
        result = calculate_adx(df["high"], df["low"], df["close"], period=50)
        assert len(result) == 3

    def test_rsi_period_gt_length_returns_series(self):
        result = calculate_rsi(pd.Series([100.0, 101.0, 102.0]), period=50)
        assert len(result) == 3

    def test_bollinger_period_gt_length_all_nan(self):
        upper, middle, lower = calculate_bollinger_bands(
            pd.Series([100.0, 101.0, 102.0]), period=50
        )
        assert len(upper) == 3
        assert middle.isna().all()


# ── Single Data Point ─────────────────────────────────────────────────────────


class TestSingleDataPoint:

    def test_ema_single_value(self):
        result = calculate_ema(pd.Series([42.0]), period=14)
        assert len(result) == 1
        assert result.iloc[0] == pytest.approx(42.0)

    def test_rsi_single_value(self):
        result = calculate_rsi(pd.Series([42.0]), period=14)
        assert len(result) == 1

    def test_atr_single_value(self):
        result = calculate_atr(
            pd.Series([44.0]), pd.Series([40.0]), pd.Series([42.0]), period=14
        )
        assert len(result) == 1


# ── Mismatched Series Lengths ─────────────────────────────────────────────────


class TestMismatchedLengths:

    def test_atr_mismatched_raises(self):
        with pytest.raises(ValueError, match="same length"):
            calculate_atr(
                pd.Series([1.0, 2.0]),
                pd.Series([1.0]),
                pd.Series([1.0, 2.0]),
            )

    def test_adx_mismatched_raises(self):
        with pytest.raises(ValueError, match="same length"):
            calculate_adx(
                pd.Series([1.0, 2.0]),
                pd.Series([1.0]),
                pd.Series([1.0, 2.0]),
            )


# ── IndicatorEngine.calculate_all with Edge-Case DataFrames ──────────────────


class TestEngineEdgeCases:

    def test_empty_dataframe(self):
        df = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        config = {"ema_fast": 50, "atr_period": 14}
        indicators = IndicatorEngine.calculate_all(df, config)
        for v in indicators.values():
            assert len(v) == 0

    def test_insufficient_data_for_all_indicators(self):
        """5 rows with period=200 — should not crash."""
        df = _ohlcv(5)
        config = {
            "ema_fast": 50,
            "ema_slow": 200,
            "adx_period": 14,
            "atr_period": 14,
            "rsi_period": 14,
            "volume_sma_period": 20,
        }
        indicators = IndicatorEngine.calculate_all(df, config)
        # All indicators should be present as Series of length 5
        assert "ema_fast" in indicators
        assert "adx" in indicators
        assert len(indicators["ema_fast"]) == 5

    def test_empty_config_returns_empty_dict(self):
        df = _ohlcv(50)
        indicators = IndicatorEngine.calculate_all(df, {})
        assert indicators == {}

    def test_bollinger_bands_in_engine(self):
        df = _ohlcv(30)
        config = {"bb_period": 20, "bb_std_dev": 2.0}
        indicators = IndicatorEngine.calculate_all(df, config)
        assert "bb_upper" in indicators
        assert "bb_middle" in indicators
        assert "bb_lower" in indicators
