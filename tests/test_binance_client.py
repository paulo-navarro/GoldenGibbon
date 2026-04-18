"""
Unit tests for BinanceClient (gap 7).

Covers:
    - fetch_klines() happy path with mocked HTTP
    - _parse_klines() field mapping (Binance array → Candle)
    - Empty response handling
    - HTTP error handling (429 rate limit, 400 bad request, timeout)
    - Malformed response (missing fields, non-numeric values)
    - get_server_time()
    - Limit clamping (max 1000)
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
import requests

from core.data.binance_client import BinanceClient


# ── Sample Binance kline data ──────────────────────────────────────────────

_KLINE = [
    1704067200000,   # 0  open_time (2024-01-01 00:00 UTC)
    "42000.50",      # 1  open
    "42500.00",      # 2  high
    "41800.00",      # 3  low
    "42300.75",      # 4  close
    "150.123",       # 5  volume
    1704070799999,   # 6  close_time
    "6345000.00",    # 7  quote_volume
    3500,            # 8  trades_count
    "80.000",        # 9  taker_buy_base
    "3384000.00",    # 10 taker_buy_quote
    "0",             # 11 ignored
]


def _mock_response(*, json_data=None, status_code=200, raise_for_status=None):
    resp = MagicMock()
    resp.json.return_value = json_data or []
    resp.status_code = status_code
    resp.ok = status_code < 400
    if raise_for_status:
        resp.raise_for_status.side_effect = raise_for_status
    else:
        resp.raise_for_status.return_value = None
    return resp


# ── parse_klines ───────────────────────────────────────────────────────────


class TestParseKlines:
    """BinanceClient._parse_klines maps Binance arrays to Candle objects."""

    def test_parses_single_kline(self):
        client = BinanceClient()
        candles = client._parse_klines([_KLINE])

        assert len(candles) == 1
        c = candles[0]
        assert c.open == Decimal("42000.50")
        assert c.high == Decimal("42500.00")
        assert c.low == Decimal("41800.00")
        assert c.close == Decimal("42300.75")
        assert c.volume == Decimal("150.123")
        assert c.quote_volume == Decimal("6345000.00")
        assert c.trades_count == 3500

    def test_parses_open_time(self):
        client = BinanceClient()
        candles = client._parse_klines([_KLINE])

        assert isinstance(candles[0].open_time, datetime)

    def test_parses_multiple_klines(self):
        client = BinanceClient()
        candles = client._parse_klines([_KLINE, _KLINE, _KLINE])
        assert len(candles) == 3

    def test_empty_data_returns_empty_list(self):
        client = BinanceClient()
        candles = client._parse_klines([])
        assert candles == []

    def test_malformed_kline_raises(self):
        client = BinanceClient()
        # Missing fields
        with pytest.raises((IndexError, Exception)):
            client._parse_klines([[1704067200000, "42000"]])


# ── fetch_klines ──────────────────────────────────────────────────────────


class TestFetchKlines:
    """BinanceClient.fetch_klines HTTP interaction."""

    @patch("core.data.binance_client.requests.Session")
    def test_happy_path(self, MockSession):
        mock_session = MagicMock()
        MockSession.return_value = mock_session
        mock_session.get.return_value = _mock_response(json_data=[_KLINE])

        client = BinanceClient()
        client.session = mock_session
        candles = client.fetch_klines("BTCUSDT", "15m", limit=5)

        assert len(candles) == 1
        assert candles[0].close == Decimal("42300.75")
        mock_session.get.assert_called_once()

    @patch("core.data.binance_client.requests.Session")
    def test_passes_params_correctly(self, MockSession):
        mock_session = MagicMock()
        MockSession.return_value = mock_session
        mock_session.get.return_value = _mock_response(json_data=[])

        client = BinanceClient()
        client.session = mock_session
        client.fetch_klines("ETHUSDT", "1h", start_time=1000, end_time=2000, limit=50)

        call_kwargs = mock_session.get.call_args
        params = call_kwargs[1]["params"] if "params" in call_kwargs[1] else call_kwargs[0][1]
        assert params["symbol"] == "ETHUSDT"
        assert params["interval"] == "1h"
        assert params["startTime"] == 1000
        assert params["endTime"] == 2000
        assert params["limit"] == 50

    @patch("core.data.binance_client.requests.Session")
    def test_limit_clamped_to_1000(self, MockSession):
        mock_session = MagicMock()
        MockSession.return_value = mock_session
        mock_session.get.return_value = _mock_response(json_data=[])

        client = BinanceClient()
        client.session = mock_session
        client.fetch_klines("BTCUSDT", "15m", limit=5000)

        params = mock_session.get.call_args[1]["params"]
        assert params["limit"] == 1000

    @patch("core.data.binance_client.requests.Session")
    def test_empty_response(self, MockSession):
        mock_session = MagicMock()
        MockSession.return_value = mock_session
        mock_session.get.return_value = _mock_response(json_data=[])

        client = BinanceClient()
        client.session = mock_session
        candles = client.fetch_klines("BTCUSDT", "15m")

        assert candles == []

    @patch("core.data.binance_client.requests.Session")
    def test_http_429_raises(self, MockSession):
        mock_session = MagicMock()
        MockSession.return_value = mock_session
        http_error = requests.exceptions.HTTPError(
            response=MagicMock(status_code=429, text="rate limit")
        )
        mock_session.get.return_value = _mock_response(
            status_code=429,
            raise_for_status=http_error,
        )

        client = BinanceClient()
        client.session = mock_session
        with pytest.raises(requests.exceptions.HTTPError):
            client.fetch_klines("BTCUSDT", "15m")

    @patch("core.data.binance_client.requests.Session")
    def test_http_400_raises(self, MockSession):
        mock_session = MagicMock()
        MockSession.return_value = mock_session
        http_error = requests.exceptions.HTTPError(
            response=MagicMock(status_code=400, text="bad request")
        )
        mock_session.get.return_value = _mock_response(
            status_code=400,
            raise_for_status=http_error,
        )

        client = BinanceClient()
        client.session = mock_session
        with pytest.raises(requests.exceptions.HTTPError):
            client.fetch_klines("BTCUSDT", "15m")

    @patch("core.data.binance_client.requests.Session")
    def test_timeout_raises(self, MockSession):
        mock_session = MagicMock()
        MockSession.return_value = mock_session
        mock_session.get.side_effect = requests.exceptions.Timeout("timed out")

        client = BinanceClient()
        client.session = mock_session
        with pytest.raises(requests.exceptions.Timeout):
            client.fetch_klines("BTCUSDT", "15m")


# ── get_server_time ───────────────────────────────────────────────────────


class TestGetServerTime:

    @patch("core.data.binance_client.requests.Session")
    def test_returns_datetime(self, MockSession):
        mock_session = MagicMock()
        MockSession.return_value = mock_session
        mock_session.get.return_value = _mock_response(
            json_data={"serverTime": 1704067200000}
        )

        client = BinanceClient()
        client.session = mock_session
        result = client.get_server_time()

        assert isinstance(result, datetime)

    @patch("core.data.binance_client.requests.Session")
    def test_api_error_raises(self, MockSession):
        mock_session = MagicMock()
        MockSession.return_value = mock_session
        mock_session.get.side_effect = requests.exceptions.ConnectionError("gone")

        client = BinanceClient()
        client.session = mock_session
        with pytest.raises(requests.exceptions.ConnectionError):
            client.get_server_time()
