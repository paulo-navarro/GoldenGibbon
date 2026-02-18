#!/usr/bin/env python3
"""
Quick test script for indicator engine (Task 1.8 & 1.9).

Tests indicator calculations with sample data.
"""

import logging
import sys

import numpy as np
import pandas as pd

from core.indicators import (
    IndicatorEngine,
    calculate_adx,
    calculate_atr,
    calculate_ema,
    calculate_rsi,
    calculate_sma,
    calculate_smart_hodler_indicators,
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)

logger = logging.getLogger(__name__)


def test_indicators():
    """Test all indicators with sample data."""
    
    logger.info("=" * 60)
    logger.info("Testing Indicator Engine (Tasks 1.8 & 1.9)")
    logger.info("=" * 60)
    
    # Create sample OHLCV data
    np.random.seed(42)
    dates = pd.date_range(start='2020-01-01', periods=100, freq='15min')
    
    close = 100 + np.cumsum(np.random.randn(100) * 0.5)
    high = close + np.abs(np.random.randn(100) * 0.3)
    low = close - np.abs(np.random.randn(100) * 0.3)
    open_price = close + np.random.randn(100) * 0.2
    volume = np.abs(np.random.randn(100) * 1000 + 5000)
    
    df = pd.DataFrame({
        'open': open_price,
        'high': high,
        'low': low,
        'close': close,
        'volume': volume
    }, index=dates)
    
    logger.info(f"Created sample OHLCV data: {len(df)} candles")
    logger.info(f"Date range: {df.index[0]} to {df.index[-1]}")
    
    # Test 1: EMA
    logger.info("\n" + "=" * 60)
    logger.info("Test 1: Calculate EMA")
    logger.info("=" * 60)
    
    try:
        ema_50 = calculate_ema(df['close'], 50)
        ema_200 = calculate_ema(df['close'], 200)
        
        logger.info(f"EMA 50: {ema_50.iloc[-1]:.2f} (last value)")
        logger.info(f"EMA 200: {ema_200.iloc[-1]:.2f} (last value)")
        logger.info("✓ Test 1 passed: EMA calculation works")
    except Exception as e:
        logger.error(f"✗ Test 1 failed: {e}")
        return False
    
    # Test 2: SMA
    logger.info("\n" + "=" * 60)
    logger.info("Test 2: Calculate SMA")
    logger.info("=" * 60)
    
    try:
        volume_sma = calculate_sma(df['volume'], 20)
        logger.info(f"Volume SMA 20: {volume_sma.iloc[-1]:.2f} (last value)")
        logger.info("✓ Test 2 passed: SMA calculation works")
    except Exception as e:
        logger.error(f"✗ Test 2 failed: {e}")
        return False
    
    # Test 3: ATR
    logger.info("\n" + "=" * 60)
    logger.info("Test 3: Calculate ATR")
    logger.info("=" * 60)
    
    try:
        atr = calculate_atr(df['high'], df['low'], df['close'], 14)
        logger.info(f"ATR 14: {atr.iloc[-1]:.4f} (last value)")
        assert (atr.dropna() > 0).all(), "ATR should be positive"
        logger.info("✓ Test 3 passed: ATR calculation works")
    except Exception as e:
        logger.error(f"✗ Test 3 failed: {e}")
        return False
    
    # Test 4: RSI
    logger.info("\n" + "=" * 60)
    logger.info("Test 4: Calculate RSI")
    logger.info("=" * 60)
    
    try:
        rsi = calculate_rsi(df['close'], 14)
        logger.info(f"RSI 14: {rsi.iloc[-1]:.2f} (last value)")
        assert (rsi.dropna() >= 0).all() and (rsi.dropna() <= 100).all(), "RSI should be between 0-100"
        logger.info("✓ Test 4 passed: RSI calculation works")
    except Exception as e:
        logger.error(f"✗ Test 4 failed: {e}")
        return False
    
    # Test 5: ADX
    logger.info("\n" + "=" * 60)
    logger.info("Test 5: Calculate ADX")
    logger.info("=" * 60)
    
    try:
        adx = calculate_adx(df['high'], df['low'], df['close'], 14)
        logger.info(f"ADX 14: {adx.iloc[-1]:.2f} (last value)")
        assert (adx.dropna() >= 0).all() and (adx.dropna() <= 100).all(), "ADX should be between 0-100"
        logger.info("✓ Test 5 passed: ADX calculation works")
    except Exception as e:
        logger.error(f"✗ Test 5 failed: {e}")
        return False
    
    # Test 6: IndicatorEngine
    logger.info("\n" + "=" * 60)
    logger.info("Test 6: Test IndicatorEngine.calculate_all")
    logger.info("=" * 60)
    
    try:
        config = {
            'ema_fast': 50,
            'ema_slow': 200,
            'adx_period': 14,
            'atr_period': 14,
            'rsi_period': 14,
            'volume_sma_period': 20
        }
        
        indicators = IndicatorEngine.calculate_all(df, config)
        
        logger.info(f"Calculated {len(indicators)} indicators:")
        for name in indicators.keys():
            logger.info(f"  - {name}")
        
        assert 'ema_fast' in indicators, "ema_fast missing"
        assert 'ema_slow' in indicators, "ema_slow missing"
        assert 'adx' in indicators, "adx missing"
        assert 'atr' in indicators, "atr missing"
        assert 'rsi' in indicators, "rsi missing"
        assert 'volume_sma' in indicators, "volume_sma missing"
        
        logger.info("✓ Test 6 passed: IndicatorEngine works")
    except Exception as e:
        logger.error(f"✗ Test 6 failed: {e}")
        return False
    
    # Test 7: Smart Hodler preset
    logger.info("\n" + "=" * 60)
    logger.info("Test 7: Test Smart Hodler indicator preset")
    logger.info("=" * 60)
    
    try:
        config = {
            'ema_fast': 50,
            'ema_slow': 200,
            'ema_hourly': 21,
            'adx_period': 14,
            'atr_period': 14,
            'rsi_period': 14,
            'volume_sma_period': 20
        }
        
        indicators = calculate_smart_hodler_indicators(df, config)
        
        logger.info(f"Smart Hodler indicators:")
        for name, series in indicators.items():
            logger.info(f"  - {name}: {series.iloc[-1]:.2f}")
        
        assert 'ema_50' in indicators, "ema_50 missing"
        assert 'ema_200' in indicators, "ema_200 missing"
        assert 'ema_21' in indicators, "ema_21 missing"
        assert 'adx' in indicators, "adx missing"
        assert 'atr' in indicators, "atr missing"
        assert 'rsi' in indicators, "rsi missing"
        assert 'volume_sma' in indicators, "volume_sma missing"
        
        logger.info("✓ Test 7 passed: Smart Hodler preset works")
    except Exception as e:
        logger.error(f"✗ Test 7 failed: {e}")
        return False
    
    # Summary
    logger.info("\n" + "=" * 60)
    logger.info("All manual tests passed! ✓")
    logger.info("=" * 60)
    logger.info("Indicator Engine Summary:")
    logger.info("  • EMA: ✓")
    logger.info("  • SMA: ✓")
    logger.info("  • ATR: ✓")
    logger.info("  • RSI: ✓")
    logger.info("  • ADX: ✓")
    logger.info("  • IndicatorEngine: ✓")
    logger.info("  • Smart Hodler preset: ✓")
    
    return True


if __name__ == "__main__":
    try:
        success = test_indicators()
        sys.exit(0 if success else 1)
    except KeyboardInterrupt:
        logger.info("\nTest interrupted by user")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Test failed with exception: {e}", exc_info=True)
        sys.exit(1)
