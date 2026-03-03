"""
Data loader for fetching and caching historical market data.

Orchestrates fetching candles from Binance API and caching to Postgres database.
"""

import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

import pandas as pd
from sqlalchemy import and_, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from core.data.binance_client import BinanceClient
from core.indicators.engine import IndicatorEngine
from core.models import Candle, MarketData
from db.models import CandleRecord
from db.utils import candle_to_orm, candles_to_dataframe, orm_to_candle

logger = logging.getLogger(__name__)


# Timeframe to minutes mapping
TIMEFRAME_MINUTES = {
    "1m": 1,
    "5m": 5,
    "15m": 15,
    "30m": 30,
    "1h": 60,
    "4h": 240,
    "1d": 1440,
    "1w": 10080
}


class DataLoader:
    """
    Data loader for fetching and caching historical candles.
    
    Handles pagination, deduplication, and conversion between API and database formats.
    """
    
    def __init__(
        self,
        session: Session,
        binance_client: BinanceClient,
        max_candles_per_request: int = 1000
    ):
        """
        Initialize DataLoader.
        
        Args:
            session: SQLAlchemy database session
            binance_client: Binance API client
            max_candles_per_request: Max candles per API request (default 1000)
        """
        self.session = session
        self.client = binance_client
        self.max_candles_per_request = max_candles_per_request
    
    def load_historical(
        self,
        symbol: str,
        timeframe: str,
        lookback_days: int
    ) -> int:
        """
        Load historical candles from Binance and cache to database.
        
        Fetches data in chunks respecting API limits, deduplicates via database
        unique constraint, and returns count of new candles inserted.
        
        Args:
            symbol: Trading symbol (e.g., "BTCUSDT")
            timeframe: Timeframe string (e.g., "15m", "1h")
            lookback_days: Number of days to look back
        
        Returns:
            Count of newly inserted candles
        
        Raises:
            ValueError: If timeframe is invalid
            requests.exceptions.RequestException: On API errors
        """
        if timeframe not in TIMEFRAME_MINUTES:
            raise ValueError(
                f"Invalid timeframe: {timeframe}. "
                f"Must be one of {list(TIMEFRAME_MINUTES.keys())}"
            )
        
        # Calculate date range
        end_date = datetime.utcnow()
        start_date = end_date - timedelta(days=lookback_days)
        
        logger.info(
            f"Loading historical data: {symbol} {timeframe} "
            f"from {start_date.date()} to {end_date.date()}"
        )
        
        # Fetch candles in chunks
        all_candles = self._fetch_in_chunks(symbol, timeframe, start_date, end_date)
        
        # Cache to database
        new_count = self._cache_to_db(all_candles, symbol, timeframe)
        
        logger.info(
            f"Completed: {symbol} {timeframe} - "
            f"{len(all_candles)} fetched, {new_count} new, "
            f"{len(all_candles) - new_count} cached"
        )
        
        return new_count
    
    def _fetch_in_chunks(
        self,
        symbol: str,
        timeframe: str,
        start_date: datetime,
        end_date: datetime
    ) -> List[Candle]:
        """
        Fetch candles in chunks respecting API limits.
        
        Args:
            symbol: Trading symbol
            timeframe: Timeframe string
            start_date: Start date
            end_date: End date
        
        Returns:
            List of all fetched Candle objects
        """
        all_candles = []
        
        # Calculate chunk size in time
        timeframe_minutes = TIMEFRAME_MINUTES[timeframe]
        chunk_duration = timedelta(minutes=timeframe_minutes * self.max_candles_per_request)
        
        current_start = start_date
        total_expected = int((end_date - start_date).total_seconds() / 60 / timeframe_minutes)
        
        logger.debug(
            f"Fetching {symbol} {timeframe}: "
            f"~{total_expected} candles in chunks of {self.max_candles_per_request}"
        )
        
        while current_start < end_date:
            current_end = min(current_start + chunk_duration, end_date)
            
            # Convert to milliseconds for Binance API
            start_ms = int(current_start.timestamp() * 1000)
            end_ms = int(current_end.timestamp() * 1000)
            
            try:
                candles = self.client.fetch_klines(
                    symbol=symbol,
                    interval=timeframe,
                    start_time=start_ms,
                    end_time=end_ms,
                    limit=self.max_candles_per_request
                )
                
                all_candles.extend(candles)
                
                logger.debug(
                    f"Fetched {len(candles)} candles: "
                    f"{current_start.date()} to {current_end.date()} "
                    f"(total: {len(all_candles)}/{total_expected})"
                )
                
                # Move to next chunk
                if candles:
                    # Start next chunk after last candle to avoid overlap
                    last_candle_time = candles[-1].open_time
                    current_start = last_candle_time + timedelta(minutes=timeframe_minutes)
                else:
                    # No more data available
                    break
                    
            except Exception as e:
                logger.error(
                    f"Failed to fetch chunk {current_start.date()} to {current_end.date()}: {e}"
                )
                # Continue with next chunk on error
                current_start = current_end
        
        return all_candles
    
    def _cache_to_db(
        self,
        candles: List[Candle],
        symbol: str,
        timeframe: str
    ) -> int:
        """
        Cache candles to database with deduplication.
        
        Uses PostgreSQL ON CONFLICT DO NOTHING to handle duplicates efficiently.
        
        Args:
            candles: List of Candle objects to cache
            symbol: Trading symbol
            timeframe: Timeframe string
        
        Returns:
            Count of newly inserted records
        """
        if not candles:
            return 0
        
        # Convert to ORM models
        records = [
            candle_to_orm(candle, symbol, timeframe)
            for candle in candles
        ]
        
        # Prepare insert statement with ON CONFLICT DO NOTHING
        stmt = insert(CandleRecord).values(
            [
                {
                    "symbol": r.symbol,
                    "timeframe": r.timeframe,
                    "open_time": r.open_time,
                    "open": r.open,
                    "high": r.high,
                    "low": r.low,
                    "close": r.close,
                    "volume": r.volume,
                    "quote_volume": r.quote_volume,
                    "trades_count": r.trades_count,
                }
                for r in records
            ]
        )
        
        # Use ON CONFLICT DO NOTHING for deduplication
        stmt = stmt.on_conflict_do_nothing(
            index_elements=["symbol", "timeframe", "open_time"]
        )
        
        try:
            result = self.session.execute(stmt)
            self.session.commit()
            
            # rowcount gives number of inserted rows (excluding duplicates)
            new_count = result.rowcount
            
            logger.debug(
                f"Cached to DB: {new_count} new, "
                f"{len(candles) - new_count} duplicates skipped"
            )
            
            return new_count
            
        except Exception as e:
            self.session.rollback()
            logger.error(f"Failed to cache candles: {e}")
            raise
    
    def cache_candles(
        self,
        candles: List[Candle],
        symbol: str,
        timeframe: str,
    ) -> int:
        """
        Upsert candles into the database cache.

        Public wrapper around :meth:`_cache_to_db`.  Used by Celery tasks
        that fetch the latest few candles and want to persist them without
        going through the full :meth:`load_historical` pipeline.

        Args:
            candles: Candle objects returned by :class:`BinanceClient`.
            symbol: Trading symbol (e.g. ``"BTCUSDT"``).
            timeframe: Timeframe string (e.g. ``"15m"``).

        Returns:
            Count of newly inserted records (duplicates silently skipped).
        """
        return self._cache_to_db(candles, symbol, timeframe)

    def get_market_data(
        self,
        symbol: str,
        timeframe: str,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        lookback_days: Optional[int] = None
    ) -> MarketData:
        """
        Get market data from cache, fetching missing data if needed.
        
        Args:
            symbol: Trading symbol
            timeframe: Timeframe string
            start_date: Start date (optional, mutually exclusive with lookback_days)
            end_date: End date (optional, defaults to now)
            lookback_days: Days to look back (optional, mutually exclusive with start_date)
        
        Returns:
            MarketData object with candles DataFrame
        
        Raises:
            ValueError: If arguments are invalid
        """
        # Validate arguments
        if start_date and lookback_days:
            raise ValueError("Cannot specify both start_date and lookback_days")
        
        # Calculate date range
        if end_date is None:
            end_date = datetime.utcnow()
        
        if lookback_days:
            start_date = end_date - timedelta(days=lookback_days)
        elif start_date is None:
            # Default to 30 days
            start_date = end_date - timedelta(days=30)
        
        # Query from cache
        records = self._query_from_cache(symbol, timeframe, start_date, end_date)
        
        # Check for gaps and fetch if needed
        if records:
            expected_count = self._calculate_expected_candles(
                start_date, end_date, timeframe
            )
            actual_count = len(records)
            
            # If significant gap (more than 10% missing), try to fetch missing data
            if actual_count < expected_count * 0.9:
                logger.info(
                    f"Cache incomplete for {symbol} {timeframe}: "
                    f"{actual_count}/{expected_count} candles. Fetching missing data..."
                )
                
                # Fetch to fill gaps
                lookback = (end_date - start_date).days
                self.load_historical(symbol, timeframe, lookback)
                
                # Query again
                records = self._query_from_cache(symbol, timeframe, start_date, end_date)
        else:
            # No data in cache, fetch it
            logger.info(f"No cached data for {symbol} {timeframe}. Fetching...")
            lookback = (end_date - start_date).days
            self.load_historical(symbol, timeframe, lookback)
            records = self._query_from_cache(symbol, timeframe, start_date, end_date)
        
        # Convert to DataFrame
        df = candles_to_dataframe(records)
        
        # Create MarketData
        market_data = MarketData(
            symbol=symbol,
            timeframe=timeframe,
            candles=df,
            indicators={}
        )
        
        logger.debug(
            f"Loaded MarketData: {symbol} {timeframe} with {len(df)} candles"
        )
        
        return market_data
    
    def get_multi_timeframe_market_data(
        self,
        symbol: str,
        primary_timeframe: str,
        secondary_timeframe: str,
        strategy_config: Dict,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        lookback_days: Optional[int] = None,
    ) -> MarketData:
        """
        Load market data for two timeframes and return a single MarketData object.
        
        Fetches candles for both the primary and secondary timeframes, calculates
        indicators for each using the strategy config, and returns a unified
        MarketData instance with ``secondary_*`` fields populated.
        
        Args:
            symbol: Trading symbol (e.g. "BTCUSDT").
            primary_timeframe: Primary timeframe (e.g. "15m").
            secondary_timeframe: Confirmation timeframe (e.g. "1h").
            strategy_config: Strategy config dict with indicator parameters.
            start_date: Start date (optional, mutually exclusive with lookback_days).
            end_date: End date (optional, defaults to now).
            lookback_days: Days to look back (optional).
        
        Returns:
            MarketData with both primary and secondary candles/indicators populated.
        """
        # Load primary timeframe
        primary_md = self.get_market_data(
            symbol=symbol,
            timeframe=primary_timeframe,
            start_date=start_date,
            end_date=end_date,
            lookback_days=lookback_days,
        )
        
        # Load secondary timeframe
        secondary_md = self.get_market_data(
            symbol=symbol,
            timeframe=secondary_timeframe,
            start_date=start_date,
            end_date=end_date,
            lookback_days=lookback_days,
        )
        
        # Calculate indicators — same function, different data
        primary_indicators = IndicatorEngine.calculate_all(
            primary_md.candles, strategy_config
        )
        secondary_indicators = IndicatorEngine.calculate_all(
            secondary_md.candles, strategy_config
        )
        
        # Build unified MarketData
        market_data = MarketData(
            symbol=symbol,
            timeframe=primary_timeframe,
            candles=primary_md.candles,
            indicators=primary_indicators,
            secondary_timeframe=secondary_timeframe,
            secondary_candles=secondary_md.candles,
            secondary_indicators=secondary_indicators,
        )
        
        logger.info(
            f"Loaded multi-TF MarketData: {symbol} "
            f"{primary_timeframe} ({len(primary_md.candles)} candles, "
            f"{len(primary_indicators)} indicators) + "
            f"{secondary_timeframe} ({len(secondary_md.candles)} candles, "
            f"{len(secondary_indicators)} indicators)"
        )
        
        return market_data
    
    def _query_from_cache(
        self,
        symbol: str,
        timeframe: str,
        start_date: datetime,
        end_date: datetime
    ) -> List[CandleRecord]:
        """
        Query candles from database cache.
        
        Args:
            symbol: Trading symbol
            timeframe: Timeframe string
            start_date: Start date
            end_date: End date
        
        Returns:
            List of CandleRecord ORM objects
        """
        stmt = (
            select(CandleRecord)
            .where(
                and_(
                    CandleRecord.symbol == symbol,
                    CandleRecord.timeframe == timeframe,
                    CandleRecord.open_time >= start_date,
                    CandleRecord.open_time <= end_date
                )
            )
            .order_by(CandleRecord.open_time)
        )
        
        result = self.session.execute(stmt)
        records = result.scalars().all()
        
        return list(records)
    
    def _calculate_expected_candles(
        self,
        start_date: datetime,
        end_date: datetime,
        timeframe: str
    ) -> int:
        """
        Calculate expected number of candles in date range.
        
        Args:
            start_date: Start date
            end_date: End date
            timeframe: Timeframe string
        
        Returns:
            Expected candle count
        """
        timeframe_minutes = TIMEFRAME_MINUTES[timeframe]
        duration_minutes = (end_date - start_date).total_seconds() / 60
        return int(duration_minutes / timeframe_minutes)
    
    def load_all_symbols(
        self,
        symbols_config: List[Dict],
        lookback_days: int
    ) -> Dict[str, int]:
        """
        Load historical data for all enabled symbols.
        
        Args:
            symbols_config: List of symbol config dicts from symbols.yaml
            lookback_days: Days to look back
        
        Returns:
            Dict mapping (symbol, timeframe) to new candle count
        """
        results = {}
        total_fetched = 0
        total_new = 0
        
        for symbol_config in symbols_config:
            if not symbol_config.get("enabled", True):
                continue
            
            symbol = symbol_config["symbol"]
            timeframes = symbol_config.get("timeframes", ["15m"])
            
            for timeframe in timeframes:
                try:
                    logger.info(f"Loading {symbol} {timeframe}...")
                    
                    new_count = self.load_historical(symbol, timeframe, lookback_days)
                    
                    key = f"{symbol}_{timeframe}"
                    results[key] = new_count
                    total_new += new_count
                    
                    # Get total count from DB for summary
                    stmt = select(CandleRecord).where(
                        and_(
                            CandleRecord.symbol == symbol,
                            CandleRecord.timeframe == timeframe
                        )
                    )
                    count = self.session.execute(
                        select(func.count()).select_from(stmt.subquery())
                    ).scalar()
                    total_fetched += count
                    
                except Exception as e:
                    logger.error(f"Failed to load {symbol} {timeframe}: {e}")
                    results[f"{symbol}_{timeframe}"] = -1  # Mark as failed
        
        logger.info(
            f"Batch load complete: {len(results)} symbol-timeframes, "
            f"{total_new} new candles, {total_fetched} total in cache"
        )
        
        return results


def get_enabled_symbols(symbols_config: List[Dict]) -> List[Tuple[str, str]]:
    """
    Extract enabled (symbol, timeframe) pairs from config.
    
    Args:
        symbols_config: List of symbol config dicts from symbols.yaml
    
    Returns:
        List of (symbol, timeframe) tuples
    """
    pairs = []
    
    for symbol_config in symbols_config:
        if not symbol_config.get("enabled", True):
            continue
        
        symbol = symbol_config["symbol"]
        timeframes = symbol_config.get("timeframes", ["15m"])
        
        for timeframe in timeframes:
            pairs.append((symbol, timeframe))
    
    return pairs
