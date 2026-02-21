"""
Pytest configuration and shared fixtures.
"""

import numpy as np
import pandas as pd
import pytest

from db import get_session
from db.models import (
    BacktestResult,
    CandleRecord,
    OrderRecord,
    PortfolioSnapshot,
    PositionRecord,
    StrategyStateRecord,
    TradeRecord,
)


@pytest.fixture(scope="function", autouse=True)
def clean_database():
    """
    Clean database before each test function.
    
    This fixture runs automatically before each test to ensure
    test isolation and prevent duplicate key conflicts.
    """
    with get_session() as session:
        # Delete in reverse dependency order
        session.query(BacktestResult).delete()
        session.query(OrderRecord).delete()
        session.query(TradeRecord).delete()
        session.query(PositionRecord).delete()
        session.query(PortfolioSnapshot).delete()
        session.query(StrategyStateRecord).delete()
        session.query(CandleRecord).delete()
        session.commit()
    
    yield  # Run the test
    
    # Cleanup after test (optional, but good practice)
    with get_session() as session:
        session.query(BacktestResult).delete()
        session.query(OrderRecord).delete()
        session.query(TradeRecord).delete()
        session.query(PositionRecord).delete()
        session.query(PortfolioSnapshot).delete()
        session.query(StrategyStateRecord).delete()
        session.query(CandleRecord).delete()
        session.commit()


@pytest.fixture(scope="session")
def database_connection():
    """
    Verify database connection once per test session.
    
    This runs once before all tests to ensure database is accessible.
    """
    from db import check_connection
    
    assert check_connection(), "Database connection failed"
    yield


# ── Indicator Test Fixtures ──────────────────────────────────────────────────


@pytest.fixture
def sample_ohlcv():
    """
    Sample OHLCV data for indicator testing.
    
    Returns 100 candles with synthetic but realistic data.
    """
    np.random.seed(42)  # For reproducibility
    
    dates = pd.date_range(start='2020-01-01', periods=100, freq='15min')
    
    # Generate random walk for close prices
    close = 100 + np.cumsum(np.random.randn(100) * 0.5)
    
    # Generate OHLC based on close
    high = close + np.abs(np.random.randn(100) * 0.3)
    low = close - np.abs(np.random.randn(100) * 0.3)
    open_price = close + np.random.randn(100) * 0.2
    
    # Generate volume
    volume = np.abs(np.random.randn(100) * 1000 + 5000)
    
    df = pd.DataFrame({
        'open': open_price,
        'high': high,
        'low': low,
        'close': close,
        'volume': volume
    }, index=dates)
    
    return df


@pytest.fixture
def trending_up():
    """
    Uptrending market data for testing trend indicators.
    
    RSI should be > 50, ADX should be > 25 after warmup.
    """
    np.random.seed(42)
    
    dates = pd.date_range(start='2020-01-01', periods=100, freq='15min')
    
    # Strong uptrend with noise
    trend = np.linspace(100, 120, 100)
    noise = np.random.randn(100) * 0.3
    close = trend + noise
    
    high = close + np.abs(np.random.randn(100) * 0.2)
    low = close - np.abs(np.random.randn(100) * 0.2)
    open_price = close + np.random.randn(100) * 0.1
    volume = np.abs(np.random.randn(100) * 1000 + 5000)
    
    df = pd.DataFrame({
        'open': open_price,
        'high': high,
        'low': low,
        'close': close,
        'volume': volume
    }, index=dates)
    
    return df


@pytest.fixture
def trending_down():
    """
    Downtrending market data for testing trend indicators.
    
    RSI should be < 50, ADX should be > 25 after warmup.
    """
    np.random.seed(42)
    
    dates = pd.date_range(start='2020-01-01', periods=100, freq='15min')
    
    # Strong downtrend with noise
    trend = np.linspace(120, 100, 100)
    noise = np.random.randn(100) * 0.3
    close = trend + noise
    
    high = close + np.abs(np.random.randn(100) * 0.2)
    low = close - np.abs(np.random.randn(100) * 0.2)
    open_price = close + np.random.randn(100) * 0.1
    volume = np.abs(np.random.randn(100) * 1000 + 5000)
    
    df = pd.DataFrame({
        'open': open_price,
        'high': high,
        'low': low,
        'close': close,
        'volume': volume
    }, index=dates)
    
    return df


@pytest.fixture
def flat_market():
    """
    Sideways/ranging market data for testing.
    
    ADX should be < 20 (weak trend), RSI should oscillate around 50.
    """
    np.random.seed(42)
    
    dates = pd.date_range(start='2020-01-01', periods=100, freq='15min')
    
    # Oscillating price with no clear trend
    close = 100 + np.sin(np.linspace(0, 4 * np.pi, 100)) * 2 + np.random.randn(100) * 0.3
    
    high = close + np.abs(np.random.randn(100) * 0.2)
    low = close - np.abs(np.random.randn(100) * 0.2)
    open_price = close + np.random.randn(100) * 0.1
    volume = np.abs(np.random.randn(100) * 1000 + 5000)
    
    df = pd.DataFrame({
        'open': open_price,
        'high': high,
        'low': low,
        'close': close,
        'volume': volume
    }, index=dates)
    
    return df


@pytest.fixture
def sample_ohlcv_1h():
    """
    Sample 1H OHLCV data for multi-timeframe indicator testing.
    
    Returns 25 candles (≈ same time span as 100 × 15m candles) with
    synthetic but realistic hourly data.
    """
    np.random.seed(42)  # Same seed for reproducibility
    
    dates = pd.date_range(start='2020-01-01', periods=25, freq='1h')
    
    # Random walk at hourly scale
    close = 100 + np.cumsum(np.random.randn(25) * 1.0)
    
    high = close + np.abs(np.random.randn(25) * 0.6)
    low = close - np.abs(np.random.randn(25) * 0.6)
    open_price = close + np.random.randn(25) * 0.4
    volume = np.abs(np.random.randn(25) * 4000 + 20000)
    
    df = pd.DataFrame({
        'open': open_price,
        'high': high,
        'low': low,
        'close': close,
        'volume': volume
    }, index=dates)
    
    return df


# ── Portfolio Fixtures ───────────────────────────────────────────────────────


@pytest.fixture
def portfolio_manager():
    """
    Fresh PortfolioManager with 10 000 USDT and default 0.1 % taker fee.
    """
    from decimal import Decimal
    from core.portfolio import PortfolioManager

    return PortfolioManager(initial_capital=Decimal("10000"))
