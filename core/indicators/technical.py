"""
Technical indicator calculations.

Pure functions for calculating technical indicators used in trading strategies.
All functions accept pandas Series and return pandas Series with the same length.
"""

import numpy as np
import pandas as pd


def calculate_ema(series: pd.Series, period: int) -> pd.Series:
    """
    Calculate Exponential Moving Average.
    
    EMA gives more weight to recent prices. Uses the standard EMA formula
    with span-based smoothing factor: α = 2 / (period + 1)
    
    Args:
        series: Price series (typically close prices)
        period: EMA period (e.g., 50, 200)
    
    Returns:
        Series with EMA values (same length as input, leading NaNs for warmup)
    
    Raises:
        ValueError: If period < 1 or series is empty
    
    Example:
        >>> close = pd.Series([10, 11, 12, 13, 14])
        >>> ema = calculate_ema(close, period=3)
    """
    if period < 1:
        raise ValueError(f"Period must be >= 1, got {period}")
    
    if len(series) == 0:
        return pd.Series(dtype=float)
    
    # Use pandas ewm with adjust=False for standard EMA formula
    # span = period means α = 2/(period+1)
    return series.ewm(span=period, adjust=False).mean()


def calculate_sma(series: pd.Series, period: int) -> pd.Series:
    """
    Calculate Simple Moving Average.
    
    SMA is the arithmetic mean of the last N periods.
    
    Args:
        series: Price or volume series
        period: SMA period (e.g., 20 for volume)
    
    Returns:
        Series with SMA values (same length as input, leading NaNs for warmup)
    
    Raises:
        ValueError: If period < 1 or series is empty
    
    Example:
        >>> volume = pd.Series([100, 200, 300, 400, 500])
        >>> sma = calculate_sma(volume, period=3)
    """
    if period < 1:
        raise ValueError(f"Period must be >= 1, got {period}")
    
    if len(series) == 0:
        return pd.Series(dtype=float)
    
    return series.rolling(window=period).mean()


def calculate_atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    """
    Calculate Average True Range.
    
    ATR measures market volatility by calculating the average of true ranges.
    True Range = max(high - low, abs(high - prev_close), abs(low - prev_close))
    
    Args:
        high: High prices
        low: Low prices
        close: Close prices
        period: ATR period (default 14)
    
    Returns:
        Series with ATR values (same length as input, leading NaNs for warmup)
    
    Raises:
        ValueError: If period < 1 or series are empty
        ValueError: If series lengths don't match
    
    Example:
        >>> atr = calculate_atr(df['high'], df['low'], df['close'], period=14)
    """
    if period < 1:
        raise ValueError(f"Period must be >= 1, got {period}")
    
    if len(high) == 0 or len(low) == 0 or len(close) == 0:
        return pd.Series(dtype=float)
    
    if not (len(high) == len(low) == len(close)):
        raise ValueError("High, low, and close series must have the same length")
    
    # Calculate True Range
    prev_close = close.shift(1)
    
    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    
    true_range = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    
    # ATR is the EMA of True Range
    atr = true_range.ewm(span=period, adjust=False).mean()
    
    return atr


def calculate_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """
    Calculate Relative Strength Index.
    
    RSI measures momentum by comparing the magnitude of recent gains to recent losses.
    RSI = 100 - (100 / (1 + RS)), where RS = average_gain / average_loss
    
    Args:
        close: Close prices
        period: RSI period (default 14)
    
    Returns:
        Series with RSI values (0-100 range, same length as input, leading NaNs for warmup)
    
    Raises:
        ValueError: If period < 1 or series is empty
    
    Example:
        >>> rsi = calculate_rsi(df['close'], period=14)
    """
    if period < 1:
        raise ValueError(f"Period must be >= 1, got {period}")
    
    if len(close) == 0:
        return pd.Series(dtype=float)
    
    # Calculate price changes
    delta = close.diff()
    
    # Separate gains and losses
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)
    
    # Calculate average gain and loss using EMA
    avg_gain = gain.ewm(span=period, adjust=False).mean()
    avg_loss = loss.ewm(span=period, adjust=False).mean()
    
    # Calculate RS and RSI
    # Handle division by zero: when avg_loss = 0, RSI = 100
    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    
    # Replace inf values (when avg_loss = 0) with 100
    rsi = rsi.replace([np.inf, -np.inf], 100)
    
    return rsi


def calculate_adx(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    """
    Calculate Average Directional Index.
    
    ADX measures trend strength (not direction) by comparing directional movement
    indicators. Higher ADX (>25) indicates stronger trend, lower ADX (<20) indicates
    weaker or ranging market.
    
    Calculation steps:
    1. Calculate +DM (positive directional movement) and -DM (negative)
    2. Calculate +DI and -DI (directional indicators) using ATR
    3. Calculate DX (directional index) from +DI and -DI
    4. ADX is the smoothed average of DX
    
    Args:
        high: High prices
        low: Low prices
        close: Close prices
        period: ADX period (default 14)
    
    Returns:
        Series with ADX values (0-100 range, same length as input, leading NaNs for warmup)
    
    Raises:
        ValueError: If period < 1 or series are empty
        ValueError: If series lengths don't match
    
    Example:
        >>> adx = calculate_adx(df['high'], df['low'], df['close'], period=14)
    """
    if period < 1:
        raise ValueError(f"Period must be >= 1, got {period}")
    
    if len(high) == 0 or len(low) == 0 or len(close) == 0:
        return pd.Series(dtype=float)
    
    if not (len(high) == len(low) == len(close)):
        raise ValueError("High, low, and close series must have the same length")
    
    # Calculate directional movements
    high_diff = high.diff()
    low_diff = -low.diff()
    
    # +DM when high_diff > low_diff and > 0, else 0
    plus_dm = high_diff.where((high_diff > low_diff) & (high_diff > 0), 0.0)
    
    # -DM when low_diff > high_diff and > 0, else 0
    minus_dm = low_diff.where((low_diff > high_diff) & (low_diff > 0), 0.0)
    
    # Calculate ATR
    atr = calculate_atr(high, low, close, period)
    
    # Smooth +DM and -DM using EMA
    plus_dm_smooth = plus_dm.ewm(span=period, adjust=False).mean()
    minus_dm_smooth = minus_dm.ewm(span=period, adjust=False).mean()
    
    # Calculate +DI and -DI (directional indicators)
    # Avoid division by zero
    plus_di = 100 * (plus_dm_smooth / atr).replace([np.inf, -np.inf], 0)
    minus_di = 100 * (minus_dm_smooth / atr).replace([np.inf, -np.inf], 0)
    
    # Calculate DX (directional index)
    di_sum = plus_di + minus_di
    di_diff = (plus_di - minus_di).abs()
    
    # DX = 100 * |+DI - -DI| / (+DI + -DI)
    # When sum = 0, DX = 0
    dx = 100 * (di_diff / di_sum).replace([np.inf, -np.inf], 0)
    
    # ADX is the EMA of DX
    adx = dx.ewm(span=period, adjust=False).mean()
    
    return adx
