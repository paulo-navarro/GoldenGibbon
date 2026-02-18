"""
Indicator calculation engine.

Orchestrates indicator calculations for market data, providing config-driven
batch calculation and strategy-specific presets.
"""

from typing import Dict

import pandas as pd

from core.indicators.technical import (
    calculate_adx,
    calculate_atr,
    calculate_bollinger_bands,
    calculate_ema,
    calculate_rsi,
    calculate_sma,
)


class IndicatorEngine:
    """
    Engine for calculating technical indicators from market data.
    
    Provides batch calculation methods that read configuration and apply
    indicators to OHLCV dataframes.
    """
    
    @staticmethod
    def calculate_all(df: pd.DataFrame, config: Dict) -> Dict[str, pd.Series]:
        """
        Calculate all indicators specified in config.
        
        Args:
            df: DataFrame with OHLCV data (open_time as index, columns: open, high, low, close, volume)
            config: Configuration dict with indicator parameters
        
        Returns:
            Dict mapping indicator names to pandas Series
        
        Example:
            >>> engine = IndicatorEngine()
            >>> indicators = engine.calculate_all(df, {
            ...     'ema_fast': 50,
            ...     'ema_slow': 200,
            ...     'adx_period': 14,
            ...     'atr_period': 14,
            ...     'rsi_period': 14,
            ...     'volume_sma_period': 20
            ... })
        """
        indicators = {}
        
        # EMAs
        if 'ema_fast' in config:
            indicators['ema_fast'] = calculate_ema(df['close'], config['ema_fast'])
            indicators[f"ema_{config['ema_fast']}"] = indicators['ema_fast']  # Alias
        
        if 'ema_slow' in config:
            indicators['ema_slow'] = calculate_ema(df['close'], config['ema_slow'])
            indicators[f"ema_{config['ema_slow']}"] = indicators['ema_slow']  # Alias
        
        if 'ema_hourly' in config:
            indicators['ema_hourly'] = calculate_ema(df['close'], config['ema_hourly'])
            indicators[f"ema_{config['ema_hourly']}"] = indicators['ema_hourly']  # Alias
        
        # ADX
        if 'adx_period' in config:
            indicators['adx'] = calculate_adx(
                df['high'], df['low'], df['close'], config['adx_period']
            )
        
        # ATR
        if 'atr_period' in config:
            indicators['atr'] = calculate_atr(
                df['high'], df['low'], df['close'], config['atr_period']
            )
        
        # RSI
        if 'rsi_period' in config:
            indicators['rsi'] = calculate_rsi(df['close'], config['rsi_period'])
        
        # Volume SMA
        if 'volume_sma_period' in config:
            indicators['volume_sma'] = calculate_sma(df['volume'], config['volume_sma_period'])
        
        # Bollinger Bands
        if 'bb_period' in config:
            bb_upper, bb_middle, bb_lower = calculate_bollinger_bands(
                df['close'],
                config['bb_period'],
                config.get('bb_std_dev', 2.0),
            )
            indicators['bb_upper'] = bb_upper
            indicators['bb_middle'] = bb_middle
            indicators['bb_lower'] = bb_lower
        
        return indicators


def calculate_smart_hodler_indicators(df: pd.DataFrame, config: Dict) -> Dict[str, pd.Series]:
    """
    Calculate indicators specifically for Smart Hodler strategy.
    
    Convenience function that ensures all required indicators are calculated
    with the correct names for the Smart Hodler strategy.
    
    Required indicators:
        - EMA 50, EMA 200 (trend detection)
        - ADX (trend strength)
        - ATR (trailing stop calculation)
        - Volume SMA (volume confirmation)
        - RSI (hourly momentum)
        - EMA 21 (hourly trend)
    
    Args:
        df: DataFrame with OHLCV data
        config: Smart Hodler config dict from strategies.yaml
    
    Returns:
        Dict with keys: ema_50, ema_200, adx, atr, volume_sma, rsi, ema_21
    
    Example:
        >>> from core.config import get_settings
        >>> settings = get_settings()
        >>> config = settings.strategies.smart_hodler
        >>> indicators = calculate_smart_hodler_indicators(df, config.model_dump())
    """
    indicators = IndicatorEngine.calculate_all(df, config)
    
    # Ensure standard Smart Hodler keys exist (handle aliases)
    result = {}
    
    # EMA 50 and 200
    if 'ema_fast' in indicators:
        result['ema_50'] = indicators['ema_fast']
    elif 'ema_50' in indicators:
        result['ema_50'] = indicators['ema_50']
    
    if 'ema_slow' in indicators:
        result['ema_200'] = indicators['ema_slow']
    elif 'ema_200' in indicators:
        result['ema_200'] = indicators['ema_200']
    
    # EMA 21 (hourly)
    if 'ema_hourly' in indicators:
        result['ema_21'] = indicators['ema_hourly']
    elif 'ema_21' in indicators:
        result['ema_21'] = indicators['ema_21']
    
    # Other indicators
    if 'adx' in indicators:
        result['adx'] = indicators['adx']
    
    if 'atr' in indicators:
        result['atr'] = indicators['atr']
    
    if 'rsi' in indicators:
        result['rsi'] = indicators['rsi']
    
    if 'volume_sma' in indicators:
        result['volume_sma'] = indicators['volume_sma']
    
    return result
