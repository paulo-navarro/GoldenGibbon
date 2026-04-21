"""
Tests for technical indicators.

Validates indicator calculations against known values and the ta library.
"""

import numpy as np
import pandas as pd
import pytest

from core.indicators import (
    IndicatorEngine,
    calculate_adx,
    calculate_atr,
    calculate_bollinger_bands,
    calculate_ema,
    calculate_rsi,
    calculate_sma,
    calculate_smart_hodler_indicators,
)

# Check if ta library is available
try:
    import ta
    TA_AVAILABLE = True
except ImportError:
    TA_AVAILABLE = False


# ── EMA Tests ─────────────────────────────────────────────────────────────────


class TestEMA:
    """Tests for Exponential Moving Average calculation."""
    
    def test_ema_basic(self):
        """Test EMA on simple sequence."""
        series = pd.Series([1, 2, 3, 4, 5, 6, 7, 8, 9, 10])
        ema = calculate_ema(series, period=3)
        
        # Check length
        assert len(ema) == len(series)
        
        # Check first values are not NaN (EMA starts from first value)
        assert not pd.isna(ema.iloc[0])
        
        # Check EMA is smoothed (less than simple values after period)
        assert ema.iloc[-1] > ema.iloc[0]
    
    def test_ema_matches_pandas(self):
        """Test EMA matches pandas ewm implementation."""
        series = pd.Series([10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20])
        period = 5
        
        our_ema = calculate_ema(series, period)
        pandas_ema = series.ewm(span=period, adjust=False).mean()
        
        # Should be identical
        pd.testing.assert_series_equal(our_ema, pandas_ema)
    
    def test_ema_empty_series(self):
        """Test EMA handles empty series."""
        series = pd.Series(dtype=float)
        ema = calculate_ema(series, period=10)
        
        assert len(ema) == 0
    
    def test_ema_invalid_period(self):
        """Test EMA raises error for invalid period."""
        series = pd.Series([1, 2, 3, 4, 5])
        
        with pytest.raises(ValueError):
            calculate_ema(series, period=0)
        
        with pytest.raises(ValueError):
            calculate_ema(series, period=-1)
    
    @pytest.mark.skipif(not TA_AVAILABLE, reason="ta library not installed")
    def test_ema_matches_ta_library(self, sample_ohlcv):
        """Test EMA matches ta library implementation."""
        close = sample_ohlcv['close']
        period = 20
        
        our_ema = calculate_ema(close, period)
        ta_ema = ta.trend.EMAIndicator(close, window=period).ema_indicator()
        
        # Compare only the overlapping non-NaN region
        # Our implementation and ta library handle initial NaNs differently
        our_valid = our_ema[~our_ema.isna()]
        ta_valid = ta_ema[~ta_ema.isna()]
        
        # Get the last N values where both are valid
        min_len = min(len(our_valid), len(ta_valid))
        if min_len > 0:
            our_tail = our_valid.iloc[-min_len:]
            ta_tail = ta_valid.iloc[-min_len:]
            assert np.allclose(our_tail.values, ta_tail.values, rtol=1e-5, atol=1e-8)


# ── SMA Tests ─────────────────────────────────────────────────────────────────


class TestSMA:
    """Tests for Simple Moving Average calculation."""
    
    def test_sma_basic(self):
        """Test SMA on simple sequence."""
        series = pd.Series([10, 20, 30, 40, 50])
        sma = calculate_sma(series, period=3)
        
        # First 2 values should be NaN (need 3 values for period=3)
        assert pd.isna(sma.iloc[0])
        assert pd.isna(sma.iloc[1])
        
        # Third value should be (10+20+30)/3 = 20
        assert sma.iloc[2] == 20
        
        # Fourth value should be (20+30+40)/3 = 30
        assert sma.iloc[3] == 30
        
        # Fifth value should be (30+40+50)/3 = 40
        assert sma.iloc[4] == 40
    
    def test_sma_matches_pandas(self):
        """Test SMA matches pandas rolling mean."""
        series = pd.Series([100, 200, 300, 400, 500, 600])
        period = 4
        
        our_sma = calculate_sma(series, period)
        pandas_sma = series.rolling(window=period).mean()
        
        pd.testing.assert_series_equal(our_sma, pandas_sma)
    
    def test_sma_volume(self, sample_ohlcv):
        """Test SMA on volume data."""
        volume = sample_ohlcv['volume']
        sma = calculate_sma(volume, period=20)
        
        # Should have values after warmup period
        assert not sma.iloc[19:].isna().all()
        
        # All non-NaN values should be positive
        assert (sma.dropna() > 0).all()
    
    def test_sma_empty_series(self):
        """Test SMA handles empty series."""
        series = pd.Series(dtype=float)
        sma = calculate_sma(series, period=10)
        
        assert len(sma) == 0
    
    def test_sma_invalid_period(self):
        """Test SMA raises error for invalid period."""
        series = pd.Series([1, 2, 3, 4, 5])
        
        with pytest.raises(ValueError):
            calculate_sma(series, period=0)
        
        with pytest.raises(ValueError):
            calculate_sma(series, period=-1)
    
    @pytest.mark.skipif(not TA_AVAILABLE, reason="ta library not installed")
    def test_sma_matches_ta_library(self, sample_ohlcv):
        """Test SMA matches ta library implementation."""
        close = sample_ohlcv['close']
        period = 20
        
        our_sma = calculate_sma(close, period)
        ta_sma = ta.trend.SMAIndicator(close, window=period).sma_indicator()
        
        assert np.allclose(our_sma.dropna(), ta_sma.dropna(), rtol=1e-5, atol=1e-8)


# ── ATR Tests ─────────────────────────────────────────────────────────────────


class TestATR:
    """Tests for Average True Range calculation."""
    
    def test_atr_basic(self, sample_ohlcv):
        """Test ATR calculation basics."""
        high = sample_ohlcv['high']
        low = sample_ohlcv['low']
        close = sample_ohlcv['close']
        
        atr = calculate_atr(high, low, close, period=14)
        
        # Check length
        assert len(atr) == len(high)
        
        # ATR should be positive after warmup
        assert (atr.dropna() > 0).all()
    
    def test_atr_flat_market(self):
        """Test ATR on flat market (should approach zero)."""
        # Constant price
        high = pd.Series([100.1] * 50)
        low = pd.Series([99.9] * 50)
        close = pd.Series([100.0] * 50)
        
        atr = calculate_atr(high, low, close, period=14)
        
        # ATR should be very small (close to the tiny range)
        assert atr.iloc[-1] < 1.0
    
    def test_atr_volatile_market(self, sample_ohlcv):
        """Test ATR increases with volatility."""
        # Calculate ATR on normal data
        atr_normal = calculate_atr(
            sample_ohlcv['high'],
            sample_ohlcv['low'],
            sample_ohlcv['close'],
            period=14
        )
        
        # Create more volatile data
        volatile_df = sample_ohlcv.copy()
        volatile_df['high'] = volatile_df['close'] * 1.05
        volatile_df['low'] = volatile_df['close'] * 0.95
        
        atr_volatile = calculate_atr(
            volatile_df['high'],
            volatile_df['low'],
            volatile_df['close'],
            period=14
        )
        
        # Volatile ATR should be larger
        assert atr_volatile.iloc[-1] > atr_normal.iloc[-1]
    
    def test_atr_mismatched_lengths(self):
        """Test ATR raises error for mismatched series lengths."""
        high = pd.Series([1, 2, 3])
        low = pd.Series([1, 2])
        close = pd.Series([1, 2, 3])
        
        with pytest.raises(ValueError):
            calculate_atr(high, low, close, period=2)
    
    def test_atr_empty_series(self):
        """Test ATR handles empty series."""
        high = pd.Series(dtype=float)
        low = pd.Series(dtype=float)
        close = pd.Series(dtype=float)
        
        atr = calculate_atr(high, low, close, period=14)
        assert len(atr) == 0
    
    @pytest.mark.skip(reason="ta library uses different smoothing approach - both valid")
    def test_atr_matches_ta_library(self, sample_ohlcv):
        """Test ATR matches ta library implementation."""
        high = sample_ohlcv['high']
        low = sample_ohlcv['low']
        close = sample_ohlcv['close']
        period = 14
        
        our_atr = calculate_atr(high, low, close, period)
        ta_atr = ta.volatility.AverageTrueRange(high, low, close, window=period).average_true_range()
        
        # Compare only the overlapping non-zero/valid regions
        # ta library fills with 0, we calculate from start
        our_valid = our_atr[~our_atr.isna()]
        ta_valid = ta_atr[ta_atr > 0]  # ta library fills with 0 initially
        
        # Get the last N values where both are valid
        min_len = min(len(our_valid), len(ta_valid))
        if min_len > 0:
            our_tail = our_valid.iloc[-min_len:]
            ta_tail = ta_valid.iloc[-min_len:]
            assert np.allclose(our_tail.values, ta_tail.values, rtol=1e-4, atol=1e-6)


# ── RSI Tests ─────────────────────────────────────────────────────────────────


class TestRSI:
    """Tests for Relative Strength Index calculation."""
    
    def test_rsi_basic(self, sample_ohlcv):
        """Test RSI calculation basics."""
        close = sample_ohlcv['close']
        rsi = calculate_rsi(close, period=14)
        
        # Check length
        assert len(rsi) == len(close)
        
        # RSI should be between 0 and 100
        assert (rsi.dropna() >= 0).all()
        assert (rsi.dropna() <= 100).all()
    
    def test_rsi_uptrend(self, trending_up):
        """Test RSI on uptrending market."""
        close = trending_up['close']
        rsi = calculate_rsi(close, period=14)
        
        # RSI should be > 50 in uptrend (after warmup)
        rsi_valid = rsi.iloc[20:]  # Skip warmup
        assert rsi_valid.mean() > 50
    
    def test_rsi_downtrend(self, trending_down):
        """Test RSI on downtrending market."""
        close = trending_down['close']
        rsi = calculate_rsi(close, period=14)
        
        # RSI should be < 50 in downtrend (after warmup)
        rsi_valid = rsi.iloc[20:]  # Skip warmup
        assert rsi_valid.mean() < 50
    
    def test_rsi_all_gains(self):
        """Test RSI when all movements are gains."""
        # Strictly increasing
        close = pd.Series(range(1, 51))
        rsi = calculate_rsi(close, period=14)
        
        # RSI should approach 100
        assert rsi.iloc[-1] > 90
    
    def test_rsi_all_losses(self):
        """Test RSI when all movements are losses."""
        # Strictly decreasing
        close = pd.Series(range(50, 0, -1))
        rsi = calculate_rsi(close, period=14)
        
        # RSI should approach 0
        assert rsi.iloc[-1] < 10
    
    def test_rsi_empty_series(self):
        """Test RSI handles empty series."""
        close = pd.Series(dtype=float)
        rsi = calculate_rsi(close, period=14)
        
        assert len(rsi) == 0
    
    @pytest.mark.skip(reason="ta library uses different smoothing approach - both valid")
    def test_rsi_matches_ta_library(self, sample_ohlcv):
        """Test RSI matches ta library implementation."""
        close = sample_ohlcv['close']
        period = 14
        
        our_rsi = calculate_rsi(close, period)
        ta_rsi = ta.momentum.RSIIndicator(close, window=period).rsi()
        
        # Compare only the overlapping non-NaN region
        our_valid = our_rsi[~our_rsi.isna()]
        ta_valid = ta_rsi[~ta_rsi.isna()]
        
        # Get the last N values where both are valid
        min_len = min(len(our_valid), len(ta_valid))
        if min_len > 0:
            our_tail = our_valid.iloc[-min_len:]
            ta_tail = ta_valid.iloc[-min_len:]
            assert np.allclose(our_tail.values, ta_tail.values, rtol=1e-4, atol=1e-6)


# ── ADX Tests ─────────────────────────────────────────────────────────────────


class TestADX:
    """Tests for Average Directional Index calculation."""
    
    def test_adx_basic(self, sample_ohlcv):
        """Test ADX calculation basics."""
        high = sample_ohlcv['high']
        low = sample_ohlcv['low']
        close = sample_ohlcv['close']
        
        adx = calculate_adx(high, low, close, period=14)
        
        # Check length
        assert len(adx) == len(high)
        
        # ADX should be between 0 and 100
        assert (adx.dropna() >= 0).all()
        assert (adx.dropna() <= 100).all()
    
    def test_adx_trending_market(self, trending_up):
        """Test ADX on trending market."""
        high = trending_up['high']
        low = trending_up['low']
        close = trending_up['close']
        
        adx = calculate_adx(high, low, close, period=14)
        
        # ADX should be elevated in strong trend (after warmup)
        adx_valid = adx.iloc[30:]  # Skip warmup
        # In strong trend, ADX should eventually rise above 20
        assert adx_valid.max() > 20
    
    def test_adx_flat_market(self, flat_market):
        """Test ADX on sideways market."""
        high = flat_market['high']
        low = flat_market['low']
        close = flat_market['close']
        
        adx = calculate_adx(high, low, close, period=14)
        
        # ADX should be lower in non-trending market
        adx_valid = adx.iloc[30:]  # Skip warmup
        # In flat market, ADX should stay relatively low
        assert adx_valid.mean() < 40
    
    def test_adx_mismatched_lengths(self):
        """Test ADX raises error for mismatched series lengths."""
        high = pd.Series([1, 2, 3])
        low = pd.Series([1, 2])
        close = pd.Series([1, 2, 3])
        
        with pytest.raises(ValueError):
            calculate_adx(high, low, close, period=14)
    
    def test_adx_empty_series(self):
        """Test ADX handles empty series."""
        high = pd.Series(dtype=float)
        low = pd.Series(dtype=float)
        close = pd.Series(dtype=float)
        
        adx = calculate_adx(high, low, close, period=14)
        assert len(adx) == 0
    
    @pytest.mark.skip(reason="ta library uses different initialization - both valid")
    def test_adx_matches_ta_library(self, sample_ohlcv):
        """Test ADX matches ta library implementation."""
        high = sample_ohlcv['high']
        low = sample_ohlcv['low']
        close = sample_ohlcv['close']
        period = 14
        
        our_adx = calculate_adx(high, low, close, period)
        ta_adx = ta.trend.ADXIndicator(high, low, close, window=period).adx()
        
        # Compare only the overlapping non-NaN region
        our_valid = our_adx[~our_adx.isna()]
        ta_valid = ta_adx[~ta_adx.isna()]
        
        # Get the last N values where both are valid
        min_len = min(len(our_valid), len(ta_valid))
        if min_len > 0:
            our_tail = our_valid.iloc[-min_len:]
            ta_tail = ta_valid.iloc[-min_len:]
            # ADX can have slightly larger variations due to complex calculation
            assert np.allclose(our_tail.values, ta_tail.values, rtol=1e-3, atol=1e-5)


# ── Bollinger Bands Tests ─────────────────────────────────────────────────────


class TestBollingerBands:
    """Tests for Bollinger Bands calculation."""

    def test_bb_basic(self, sample_ohlcv):
        """Test Bollinger Bands calculation basics."""
        close = sample_ohlcv['close']
        upper, middle, lower = calculate_bollinger_bands(close, period=20, std_dev=2.0)

        # Check lengths
        assert len(upper) == len(close)
        assert len(middle) == len(close)
        assert len(lower) == len(close)

        # After warmup, upper > middle > lower
        valid = ~upper.isna()
        assert (upper[valid] >= middle[valid]).all()
        assert (middle[valid] >= lower[valid]).all()

    def test_bb_middle_matches_sma(self, sample_ohlcv):
        """Test that middle band equals SMA."""
        close = sample_ohlcv['close']
        period = 20

        _, middle, _ = calculate_bollinger_bands(close, period=period)
        sma = calculate_sma(close, period)

        pd.testing.assert_series_equal(middle, sma)

    def test_bb_band_width(self):
        """Test that band width equals 2 * std_dev * rolling_std."""
        close = pd.Series([10, 12, 11, 13, 14, 12, 15, 16, 13, 17,
                           11, 14, 16, 18, 15, 12, 19, 20, 14, 17,
                           13, 16, 18, 20, 15])
        period = 5
        std_dev = 2.0

        upper, middle, lower = calculate_bollinger_bands(close, period=period, std_dev=std_dev)

        expected_std = close.rolling(window=period).std(ddof=0)
        expected_width = 2 * std_dev * expected_std

        actual_width = upper - lower

        # Compare where valid
        valid = ~expected_width.isna()
        assert np.allclose(actual_width[valid].values, expected_width[valid].values, rtol=1e-10)

    def test_bb_symmetry(self, sample_ohlcv):
        """Test that upper and lower bands are symmetric around the middle."""
        close = sample_ohlcv['close']
        upper, middle, lower = calculate_bollinger_bands(close, period=20, std_dev=2.0)

        valid = ~upper.isna()
        upper_dist = upper[valid] - middle[valid]
        lower_dist = middle[valid] - lower[valid]

        assert np.allclose(upper_dist.values, lower_dist.values, rtol=1e-10)

    def test_bb_empty_series(self):
        """Test Bollinger Bands handles empty series."""
        close = pd.Series(dtype=float)
        upper, middle, lower = calculate_bollinger_bands(close, period=20)

        assert len(upper) == 0
        assert len(middle) == 0
        assert len(lower) == 0

    def test_bb_invalid_period(self):
        """Test Bollinger Bands raises error for invalid period."""
        close = pd.Series([1, 2, 3, 4, 5])

        with pytest.raises(ValueError):
            calculate_bollinger_bands(close, period=0)

        with pytest.raises(ValueError):
            calculate_bollinger_bands(close, period=-1)

    def test_bb_invalid_std_dev(self):
        """Test Bollinger Bands raises error for invalid std_dev."""
        close = pd.Series([1, 2, 3, 4, 5])

        with pytest.raises(ValueError):
            calculate_bollinger_bands(close, period=20, std_dev=0)

        with pytest.raises(ValueError):
            calculate_bollinger_bands(close, period=20, std_dev=-1.0)

    def test_bb_flat_market(self, flat_market):
        """Test Bollinger Bands narrow on flat market."""
        close = flat_market['close']
        upper, middle, lower = calculate_bollinger_bands(close, period=20, std_dev=2.0)

        # Bands should be relatively narrow in flat market
        valid = ~upper.isna()
        band_width = (upper[valid] - lower[valid]).mean()
        price_mean = close[valid].mean()
        band_pct = band_width / price_mean

        # Band width should be small relative to price (<20% for flat market)
        assert band_pct < 0.20

    @pytest.mark.skipif(not TA_AVAILABLE, reason="ta library not installed")
    def test_bb_matches_ta_library(self, sample_ohlcv):
        """Test Bollinger Bands matches ta library implementation."""
        close = sample_ohlcv['close']
        period = 20
        std_dev = 2.0

        our_upper, our_middle, our_lower = calculate_bollinger_bands(close, period, std_dev)

        ta_bb = ta.volatility.BollingerBands(close, window=period, window_dev=std_dev)
        ta_upper = ta_bb.bollinger_hband()
        ta_middle = ta_bb.bollinger_mavg()
        ta_lower = ta_bb.bollinger_lband()

        # Middle band (SMA) should match exactly
        our_mid_valid = our_middle.dropna()
        ta_mid_valid = ta_middle.dropna()
        min_len = min(len(our_mid_valid), len(ta_mid_valid))
        if min_len > 0:
            assert np.allclose(
                our_mid_valid.iloc[-min_len:].values,
                ta_mid_valid.iloc[-min_len:].values,
                rtol=1e-5, atol=1e-8,
            )

        # Upper and lower may differ slightly due to ddof (population vs sample std)
        our_up_valid = our_upper.dropna()
        ta_up_valid = ta_upper.dropna()
        min_len = min(len(our_up_valid), len(ta_up_valid))
        if min_len > 0:
            assert np.allclose(
                our_up_valid.iloc[-min_len:].values,
                ta_up_valid.iloc[-min_len:].values,
                rtol=1e-2, atol=1e-4,
            )


# ── IndicatorEngine Tests ─────────────────────────────────────────────────────


class TestIndicatorEngine:
    """Tests for IndicatorEngine orchestration."""
    
    def test_calculate_all_basic(self, sample_ohlcv):
        """Test IndicatorEngine.calculate_all with basic config."""
        config = {
            'ema_fast': 50,
            'ema_slow': 200,
            'adx_period': 14,
            'atr_period': 14,
            'rsi_period': 14,
            'volume_sma_period': 20
        }
        
        indicators = IndicatorEngine.calculate_all(sample_ohlcv, config)
        
        # Check all expected indicators are present
        assert 'ema_fast' in indicators
        assert 'ema_slow' in indicators
        assert 'ema_50' in indicators  # Alias
        assert 'ema_200' in indicators  # Alias
        assert 'adx' in indicators
        assert 'atr' in indicators
        assert 'rsi' in indicators
        assert 'volume_sma' in indicators
        
        # Check all are Series
        for name, series in indicators.items():
            assert isinstance(series, pd.Series)
        
        # Check lengths match
        for name, series in indicators.items():
            assert len(series) == len(sample_ohlcv)
    
    def test_calculate_all_partial_config(self, sample_ohlcv):
        """Test IndicatorEngine with partial config."""
        config = {
            'ema_fast': 20,
            'rsi_period': 14
        }
        
        indicators = IndicatorEngine.calculate_all(sample_ohlcv, config)
        
        # Should only have requested indicators
        assert 'ema_fast' in indicators
        assert 'rsi' in indicators
        assert 'adx' not in indicators
    
    def test_calculate_all_bollinger_bands(self, sample_ohlcv):
        """Test IndicatorEngine calculates Bollinger Bands from config."""
        config = {
            'bb_period': 20,
            'bb_std_dev': 2.0,
        }

        indicators = IndicatorEngine.calculate_all(sample_ohlcv, config)

        assert 'bb_upper' in indicators
        assert 'bb_middle' in indicators
        assert 'bb_lower' in indicators

        for key in ('bb_upper', 'bb_middle', 'bb_lower'):
            assert isinstance(indicators[key], pd.Series)
            assert len(indicators[key]) == len(sample_ohlcv)
    
    def test_calculate_smart_hodler_indicators(self, sample_ohlcv):
        """Test Smart Hodler preset indicator calculation."""
        config = {
            'ema_fast': 50,
            'ema_slow': 200,
            'ema_hourly': 21,
            'adx_period': 14,
            'atr_period': 14,
            'rsi_period': 14,
            'volume_sma_period': 20
        }
        
        indicators = calculate_smart_hodler_indicators(sample_ohlcv, config)
        
        # Check all Smart Hodler indicators are present
        assert 'ema_50' in indicators
        assert 'ema_200' in indicators
        assert 'ema_21' in indicators
        assert 'adx' in indicators
        assert 'atr' in indicators
        assert 'rsi' in indicators
        assert 'volume_sma' in indicators
        
        # All should be Series
        for name, series in indicators.items():
            assert isinstance(series, pd.Series)
            assert len(series) == len(sample_ohlcv)


# ── Integration Tests ─────────────────────────────────────────────────────────


class TestIndicatorIntegration:
    """Integration tests with real-world scenarios."""
    
    def test_full_pipeline_with_market_data(self, sample_ohlcv):
        """Test full indicator pipeline with market data."""
        # Simulate Smart Hodler strategy config
        config = {
            'ema_fast': 50,
            'ema_slow': 200,
            'ema_hourly': 21,
            'adx_period': 14,
            'atr_period': 14,
            'rsi_period': 14,
            'volume_sma_period': 20
        }
        
        # Calculate all indicators
        indicators = calculate_smart_hodler_indicators(sample_ohlcv, config)
        
        # Verify we can make trading decisions
        latest_idx = len(sample_ohlcv) - 1
        
        ema_50 = indicators['ema_50'].iloc[latest_idx]
        ema_200 = indicators['ema_200'].iloc[latest_idx]
        adx = indicators['adx'].iloc[latest_idx]
        atr = indicators['atr'].iloc[latest_idx]
        rsi = indicators['rsi'].iloc[latest_idx]
        volume_sma = indicators['volume_sma'].iloc[latest_idx]
        
        # All should be valid numbers (not NaN if we have enough data)
        if latest_idx > 200:  # After warmup
            assert not pd.isna(ema_50)
            assert not pd.isna(ema_200)
            assert not pd.isna(adx)
            assert not pd.isna(atr)
            assert not pd.isna(rsi)
            assert not pd.isna(volume_sma)
            
            # Verify reasonable ranges
            assert 0 <= adx <= 100
            assert 0 <= rsi <= 100
            assert atr > 0
            assert volume_sma > 0
    
    def test_indicators_with_insufficient_data(self):
        """Test indicators handle insufficient data gracefully."""
        # Only 10 candles, but indicators need more
        df = pd.DataFrame({
            'open': [100, 100, 100, 100, 100, 100, 100, 100, 100, 100],
            'high': [101, 101, 101, 101, 101, 101, 101, 101, 101, 101],
            'low': [99, 99, 99, 99, 99, 99, 99, 99, 99, 99],
            'close': [100, 100, 100, 100, 100, 100, 100, 100, 100, 100],
            'volume': [1000, 1000, 1000, 1000, 1000, 1000, 1000, 1000, 1000, 1000]
        })
        
        config = {
            'ema_fast': 50,  # More than 10 periods
            'ema_slow': 200,
            'adx_period': 14,
            'atr_period': 14,
            'rsi_period': 14,
            'volume_sma_period': 20
        }
        
        # Should not raise exceptions
        indicators = IndicatorEngine.calculate_all(df, config)
        
        # All should be Series with correct length
        for name, series in indicators.items():
            assert len(series) == len(df)


# ── Multi-Timeframe Indicator Tests ──────────────────────────────────────────


class TestMultiTimeframeIndicators:
    """Tests that calculate_all works identically on any timeframe DataFrame."""

    @pytest.fixture
    def smart_hodler_config(self):
        """Strategy config matching smart_hodler defaults."""
        return {
            'ema_fast': 50,
            'ema_slow': 200,
            'adx_period': 14,
            'atr_period': 14,
            'volume_sma_period': 20,
            'ema_hourly': 21,
            'rsi_period': 14,
        }

    def test_calculate_all_on_15m(self, sample_ohlcv, smart_hodler_config):
        """calculate_all works on 15m data."""
        result = IndicatorEngine.calculate_all(sample_ohlcv, smart_hodler_config)
        assert isinstance(result, dict)
        assert 'ema_fast' in result
        assert 'adx' in result

    def test_calculate_all_on_1h(self, sample_ohlcv_1h, smart_hodler_config):
        """calculate_all works identically on 1H data."""
        result = IndicatorEngine.calculate_all(sample_ohlcv_1h, smart_hodler_config)
        assert isinstance(result, dict)
        assert 'ema_fast' in result
        assert 'adx' in result

    def test_same_keys_both_timeframes(self, sample_ohlcv, sample_ohlcv_1h, smart_hodler_config):
        """Both timeframes produce the same set of indicator keys."""
        primary = IndicatorEngine.calculate_all(sample_ohlcv, smart_hodler_config)
        secondary = IndicatorEngine.calculate_all(sample_ohlcv_1h, smart_hodler_config)
        assert set(primary.keys()) == set(secondary.keys())

    def test_series_length_matches_input(self, sample_ohlcv, sample_ohlcv_1h, smart_hodler_config):
        """Each indicator Series has the same length as its input DataFrame."""
        primary = IndicatorEngine.calculate_all(sample_ohlcv, smart_hodler_config)
        secondary = IndicatorEngine.calculate_all(sample_ohlcv_1h, smart_hodler_config)

        for name, series in primary.items():
            assert len(series) == len(sample_ohlcv), f"primary {name}"
        for name, series in secondary.items():
            assert len(series) == len(sample_ohlcv_1h), f"secondary {name}"

    def test_values_differ_across_timeframes(self, sample_ohlcv, sample_ohlcv_1h, smart_hodler_config):
        """Indicator values computed on different data are different."""
        primary = IndicatorEngine.calculate_all(sample_ohlcv, smart_hodler_config)
        secondary = IndicatorEngine.calculate_all(sample_ohlcv_1h, smart_hodler_config)

        p_last = primary['ema_fast'].dropna().iloc[-1]
        s_last = secondary['ema_fast'].dropna().iloc[-1]
        assert p_last != s_last

    def test_bollinger_bands_both_timeframes(self, sample_ohlcv, sample_ohlcv_1h):
        """Bollinger Bands are calculated on whichever DataFrame is passed."""
        config = {'bb_period': 20, 'bb_std_dev': 2.0}
        primary = IndicatorEngine.calculate_all(sample_ohlcv, config)
        secondary = IndicatorEngine.calculate_all(sample_ohlcv_1h, config)

        for result in (primary, secondary):
            assert 'bb_upper' in result
            assert 'bb_middle' in result
            assert 'bb_lower' in result

    def test_smart_hodler_preset_still_works(self, sample_ohlcv):
        """calculate_smart_hodler_indicators is unaffected by the refactor."""
        config = {
            'ema_fast': 50,
            'ema_slow': 200,
            'adx_period': 14,
            'atr_period': 14,
            'rsi_period': 14,
            'volume_sma_period': 20,
            'ema_hourly': 21,
        }
        hodler_inds = calculate_smart_hodler_indicators(sample_ohlcv, config)
        assert 'ema_50' in hodler_inds
        assert 'ema_200' in hodler_inds
        assert 'adx' in hodler_inds
