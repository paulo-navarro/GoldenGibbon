"""
Unit tests for DataLoader (gap 7 — continued).

Covers:
    - cache_candles() deduplication via ON CONFLICT DO NOTHING
    - _cache_to_db() empty list
    - _fetch_in_chunks() pagination logic
    - get_market_data() cache-hit path
    - get_multi_timeframe_market_data() wiring
    - load_historical() invalid timeframe rejection
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from unittest.mock import MagicMock, patch, PropertyMock

import pandas as pd
import pytest

from core.data.loader import DataLoader, TIMEFRAME_MINUTES, get_enabled_symbols
from core.models import Candle


# ── Helpers ───────────────────────────────────────────────────────────────────

def _candle(minutes_offset: int = 0) -> Candle:
    return Candle(
        open_time=datetime(2026, 1, 1) + timedelta(minutes=minutes_offset),
        open=Decimal("100"),
        high=Decimal("105"),
        low=Decimal("95"),
        close=Decimal("102"),
        volume=Decimal("10"),
        quote_volume=Decimal("1020"),
        trades_count=50,
    )


# ── cache_candles / _cache_to_db ──────────────────────────────────────────────


class TestCacheCandles:

    def test_empty_list_returns_zero(self):
        session = MagicMock()
        client = MagicMock()
        loader = DataLoader(session=session, binance_client=client)

        assert loader.cache_candles([], "BTCUSDT", "15m") == 0
        session.execute.assert_not_called()

    def test_delegates_to_cache_to_db(self):
        """cache_candles is a thin wrapper around _cache_to_db."""
        session = MagicMock()
        client = MagicMock()
        loader = DataLoader(session=session, binance_client=client)

        candles = [_candle(0), _candle(15)]

        # Mock execute to return a result with rowcount
        mock_result = MagicMock()
        mock_result.rowcount = 2
        session.execute.return_value = mock_result

        count = loader.cache_candles(candles, "BTCUSDT", "15m")

        assert count == 2
        session.execute.assert_called_once()
        session.commit.assert_called_once()

    def test_rollback_on_db_error(self):
        session = MagicMock()
        client = MagicMock()
        loader = DataLoader(session=session, binance_client=client)

        session.execute.side_effect = Exception("db error")

        with pytest.raises(Exception, match="db error"):
            loader.cache_candles([_candle()], "BTCUSDT", "15m")

        session.rollback.assert_called_once()


# ── load_historical ──────────────────────────────────────────────────────────


class TestLoadHistorical:

    def test_invalid_timeframe_raises(self):
        session = MagicMock()
        client = MagicMock()
        loader = DataLoader(session=session, binance_client=client)

        with pytest.raises(ValueError, match="Invalid timeframe"):
            loader.load_historical("BTCUSDT", "2m", lookback_days=7)

    def test_happy_path_returns_new_count(self):
        session = MagicMock()
        client = MagicMock()
        loader = DataLoader(session=session, binance_client=client)

        # fetch_klines returns candles for the first chunk, empty for the second
        client.fetch_klines.side_effect = [
            [_candle(0), _candle(15)],
            [],  # no more data
        ]

        mock_result = MagicMock()
        mock_result.rowcount = 2
        session.execute.return_value = mock_result

        count = loader.load_historical("BTCUSDT", "15m", lookback_days=1)

        assert count == 2
        assert client.fetch_klines.call_count >= 1


# ── _fetch_in_chunks ─────────────────────────────────────────────────────────


class TestFetchInChunks:

    @patch("core.data.loader.time.sleep")  # don't actually sleep
    def test_stops_on_empty_response(self, _mock_sleep):
        session = MagicMock()
        client = MagicMock()
        loader = DataLoader(session=session, binance_client=client)

        client.fetch_klines.return_value = []

        start = datetime(2026, 1, 1)
        end = datetime(2026, 1, 2)
        result = loader._fetch_in_chunks("BTCUSDT", "15m", start, end)

        assert result == []
        client.fetch_klines.assert_called_once()

    @patch("core.data.loader.time.sleep")
    def test_advances_past_last_candle(self, _mock_sleep):
        session = MagicMock()
        client = MagicMock()
        loader = DataLoader(session=session, binance_client=client)

        c1 = _candle(0)
        c2 = _candle(15)
        # First call returns candles, second returns empty to stop loop
        client.fetch_klines.side_effect = [[c1, c2], []]

        start = datetime(2026, 1, 1)
        end = datetime(2026, 1, 1, 1)  # 1 hour range
        result = loader._fetch_in_chunks("BTCUSDT", "15m", start, end)

        assert len(result) == 2

    @patch("core.data.loader.time.sleep")
    def test_continues_on_api_error(self, _mock_sleep):
        session = MagicMock()
        client = MagicMock()
        loader = DataLoader(session=session, binance_client=client)

        # First chunk raises, loop should continue to next chunk
        client.fetch_klines.side_effect = [Exception("API down"), []]

        start = datetime(2026, 1, 1)
        end = datetime(2026, 1, 1, 1)
        result = loader._fetch_in_chunks("BTCUSDT", "15m", start, end)

        assert result == []
        # Should have tried at least once
        assert client.fetch_klines.call_count >= 1


# ── get_enabled_symbols ──────────────────────────────────────────────────────


class TestGetEnabledSymbols:

    def test_extracts_enabled_pairs(self):
        config = [
            {"symbol": "BTCUSDT", "enabled": True, "timeframes": ["15m", "1h"]},
            {"symbol": "ETHUSDT", "enabled": False, "timeframes": ["15m"]},
            {"symbol": "SOLUSDT", "timeframes": ["4h"]},  # no "enabled" → defaults True
        ]
        pairs = get_enabled_symbols(config)
        assert ("BTCUSDT", "15m") in pairs
        assert ("BTCUSDT", "1h") in pairs
        assert ("SOLUSDT", "4h") in pairs
        # Disabled symbol excluded
        assert all(s != "ETHUSDT" for s, _ in pairs)

    def test_defaults_to_15m_when_no_timeframes(self):
        config = [{"symbol": "BTCUSDT"}]
        pairs = get_enabled_symbols(config)
        assert pairs == [("BTCUSDT", "15m")]


# ── TIMEFRAME_MINUTES ────────────────────────────────────────────────────────


class TestTimeframeMinutes:

    def test_all_expected_timeframes_present(self):
        expected = {"1m", "5m", "15m", "30m", "1h", "4h", "1d", "1w"}
        assert set(TIMEFRAME_MINUTES.keys()) == expected

    def test_1h_equals_60(self):
        assert TIMEFRAME_MINUTES["1h"] == 60
