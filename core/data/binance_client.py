"""
Binance API client for fetching historical OHLCV data.

Provides a simple interface to Binance REST API for candle data retrieval.
"""

import logging
import time
from datetime import datetime
from decimal import Decimal
from typing import List, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from core.models import Candle

logger = logging.getLogger(__name__)


class BinanceClient:
    """
    Client for Binance REST API.
    
    Handles HTTP requests to Binance endpoints with retry logic and error handling.
    """
    
    def __init__(self, base_url: str = "https://api.binance.com"):
        """
        Initialize Binance client.
        
        Args:
            base_url: Binance API base URL
        """
        self.base_url = base_url.rstrip('/')
        self.session = self._create_session()
    
    def _create_session(self) -> requests.Session:
        """
        Create requests session with retry configuration.
        
        Returns:
            Configured requests Session
        """
        session = requests.Session()
        
        # Configure retry strategy
        retry_strategy = Retry(
            total=3,
            backoff_factor=1,  # Wait 1s, 2s, 4s between retries
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET"]
        )
        
        adapter = HTTPAdapter(max_retries=retry_strategy)
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        
        return session
    
    def fetch_klines(
        self,
        symbol: str,
        interval: str,
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
        limit: int = 1000
    ) -> List[Candle]:
        """
        Fetch OHLCV klines (candles) from Binance.
        
        Args:
            symbol: Trading pair symbol (e.g., "BTCUSDT")
            interval: Timeframe (1m, 5m, 15m, 30m, 1h, 4h, 1d, 1w)
            start_time: Start timestamp in milliseconds (optional)
            end_time: End timestamp in milliseconds (optional)
            limit: Number of candles to fetch (max 1000, default 1000)
        
        Returns:
            List of Candle objects
        
        Raises:
            requests.exceptions.RequestException: On API errors
        """
        endpoint = f"{self.base_url}/api/v3/klines"
        
        params = {
            "symbol": symbol,
            "interval": interval,
            "limit": min(limit, 1000)  # Binance max is 1000
        }
        
        if start_time is not None:
            params["startTime"] = start_time
        if end_time is not None:
            params["endTime"] = end_time
        
        try:
            logger.debug(
                f"Fetching klines: {symbol} {interval} "
                f"start={start_time} end={end_time} limit={limit}"
            )
            
            response = self.session.get(endpoint, params=params, timeout=10)
            response.raise_for_status()
            
            data = response.json()
            candles = self._parse_klines(data)
            
            logger.debug(f"Fetched {len(candles)} candles for {symbol} {interval}")
            
            return candles
            
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 429:
                logger.error("Rate limit exceeded. Wait before retrying.")
            elif e.response.status_code == 400:
                logger.error(f"Bad request: {e.response.text}")
            else:
                logger.error(f"HTTP error: {e}")
            raise
        
        except requests.exceptions.Timeout:
            logger.error(f"Request timeout for {symbol} {interval}")
            raise
        
        except requests.exceptions.RequestException as e:
            logger.error(f"Request failed: {e}")
            raise
    
    def _parse_klines(self, data: List[list]) -> List[Candle]:
        """
        Parse Binance klines response into Candle objects.
        
        Binance klines format:
        [
            open_time,        # 0
            open,             # 1
            high,             # 2
            low,              # 3
            close,            # 4
            volume,           # 5
            close_time,       # 6
            quote_volume,     # 7
            trades_count,     # 8
            taker_buy_base,   # 9
            taker_buy_quote,  # 10
            ignored           # 11
        ]
        
        Args:
            data: Raw klines data from Binance
        
        Returns:
            List of Candle objects
        """
        candles = []
        
        for kline in data:
            candle = Candle(
                open_time=datetime.fromtimestamp(kline[0] / 1000),  # ms to seconds
                open=Decimal(str(kline[1])),
                high=Decimal(str(kline[2])),
                low=Decimal(str(kline[3])),
                close=Decimal(str(kline[4])),
                volume=Decimal(str(kline[5])),
                quote_volume=Decimal(str(kline[7])),
                trades_count=int(kline[8])
            )
            candles.append(candle)
        
        return candles
    
    def get_server_time(self) -> datetime:
        """
        Get Binance server time.
        
        Returns:
            Server time as datetime
        
        Raises:
            requests.exceptions.RequestException: On API errors
        """
        endpoint = f"{self.base_url}/api/v3/time"
        
        try:
            response = self.session.get(endpoint, timeout=5)
            response.raise_for_status()
            data = response.json()
            
            server_time_ms = data["serverTime"]
            return datetime.fromtimestamp(server_time_ms / 1000)
            
        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to get server time: {e}")
            raise
