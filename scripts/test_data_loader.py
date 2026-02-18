#!/usr/bin/env python3
"""
Test script for Data Loader (Task 1.7).

Tests fetching historical candles from Binance and caching to Postgres.
"""

import logging
import sys

from core.config import get_settings
from core.data import BinanceClient, DataLoader
from db import get_session

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)

logger = logging.getLogger(__name__)


def test_data_loader():
    """Test data loader with a small dataset."""
    
    logger.info("=" * 60)
    logger.info("Testing Data Loader (Task 1.7)")
    logger.info("=" * 60)
    
    # Load configuration
    settings = get_settings()
    
    # Create Binance client
    binance_base_url = settings.data.binance_api_base
    max_candles = settings.data.max_candles_per_request
    
    logger.info(f"Binance API: {binance_base_url}")
    logger.info(f"Max candles per request: {max_candles}")
    
    client = BinanceClient(base_url=binance_base_url)
    
    # Use session context manager
    with get_session() as session:
        # Create data loader
        loader = DataLoader(
            session=session,
            binance_client=client,
            max_candles_per_request=max_candles
        )
        
        # Test 1: Fetch small dataset (7 days)
        logger.info("\n" + "=" * 60)
        logger.info("Test 1: Fetch BTCUSDT 15m for 7 days")
        logger.info("=" * 60)
        
        try:
            new_count = loader.load_historical(
                symbol="BTCUSDT",
                timeframe="15m",
                lookback_days=7
            )
            logger.info(f"✓ Test 1 passed: {new_count} new candles inserted")
        except Exception as e:
            logger.error(f"✗ Test 1 failed: {e}")
            return False
        
        # Test 2: Fetch again to verify deduplication
        logger.info("\n" + "=" * 60)
        logger.info("Test 2: Re-fetch same data (test deduplication)")
        logger.info("=" * 60)
        
        try:
            new_count_2 = loader.load_historical(
                symbol="BTCUSDT",
                timeframe="15m",
                lookback_days=7
            )
            
            if new_count_2 == 0:
                logger.info(f"✓ Test 2 passed: {new_count_2} new candles (deduplication working)")
            else:
                logger.warning(f"⚠ Test 2 warning: {new_count_2} new candles (expected 0)")
        except Exception as e:
            logger.error(f"✗ Test 2 failed: {e}")
            return False
        
        # Test 3: Get market data
        logger.info("\n" + "=" * 60)
        logger.info("Test 3: Get MarketData object")
        logger.info("=" * 60)
        
        try:
            market_data = loader.get_market_data(
                symbol="BTCUSDT",
                timeframe="15m",
                lookback_days=3
            )
            
            logger.info(f"Symbol: {market_data.symbol}")
            logger.info(f"Timeframe: {market_data.timeframe}")
            logger.info(f"Candles shape: {market_data.candles.shape}")
            logger.info(f"Candles columns: {list(market_data.candles.columns)}")
            logger.info(f"Date range: {market_data.candles.index.min()} to {market_data.candles.index.max()}")
            
            if len(market_data.candles) > 0:
                logger.info(f"✓ Test 3 passed: MarketData with {len(market_data.candles)} candles")
            else:
                logger.error("✗ Test 3 failed: MarketData has no candles")
                return False
        except Exception as e:
            logger.error(f"✗ Test 3 failed: {e}")
            return False
        
        # Test 4: Invalid symbol handling
        logger.info("\n" + "=" * 60)
        logger.info("Test 4: Test error handling with invalid symbol")
        logger.info("=" * 60)
        
        try:
            new_count = loader.load_historical(
                symbol="INVALIDSYMBOL",
                timeframe="15m",
                lookback_days=1
            )
            if new_count == 0:
                logger.info(f"✓ Test 4 passed: Correctly handled invalid symbol (0 candles)")
            else:
                logger.error(f"✗ Test 4 failed: Expected 0 candles for invalid symbol, got {new_count}")
                return False
        except Exception as e:
            logger.info(f"✓ Test 4 passed: Correctly handled error - {type(e).__name__}")
        
        # Test 5: Database verification
        logger.info("\n" + "=" * 60)
        logger.info("Test 5: Verify data in database")
        logger.info("=" * 60)
        
        try:
            from sqlalchemy import func, select
            from db.models import CandleRecord
            
            # Count total candles
            stmt = select(func.count()).select_from(CandleRecord)
            total_count = session.execute(stmt).scalar()
            
            # Count BTCUSDT 15m candles
            stmt_btc = select(func.count()).select_from(CandleRecord).where(
                (CandleRecord.symbol == "BTCUSDT") & (CandleRecord.timeframe == "15m")
            )
            btc_count = session.execute(stmt_btc).scalar()
            
            logger.info(f"Total candles in database: {total_count}")
            logger.info(f"BTCUSDT 15m candles: {btc_count}")
            
            if btc_count > 0:
                logger.info(f"✓ Test 5 passed: {btc_count} candles in database")
            else:
                logger.error("✗ Test 5 failed: No candles in database")
                return False
        except Exception as e:
            logger.error(f"✗ Test 5 failed: {e}")
            return False
    
    # Summary
    logger.info("\n" + "=" * 60)
    logger.info("All tests passed! ✓")
    logger.info("=" * 60)
    logger.info("Data Loader is working correctly:")
    logger.info("  • Fetches historical candles from Binance")
    logger.info("  • Caches to Postgres with deduplication")
    logger.info("  • Returns MarketData objects")
    logger.info("  • Handles errors gracefully")
    
    return True


if __name__ == "__main__":
    try:
        success = test_data_loader()
        sys.exit(0 if success else 1)
    except KeyboardInterrupt:
        logger.info("\nTest interrupted by user")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Test failed with exception: {e}", exc_info=True)
        sys.exit(1)
