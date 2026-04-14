"""
Unit tests for scripts/pull_historical_data.py

All tests mock DataLoader and get_session — no real DB or Binance API calls.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

# Ensure scripts/ is importable as a module
sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.pull_historical_data import build_pairs, parse_args, print_summary, run


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_sym(symbol: str, timeframes: list, enabled: bool = True):
    sym = MagicMock()
    sym.symbol = symbol
    sym.timeframes = timeframes
    sym.enabled = enabled
    return sym


def _make_settings(symbols):
    settings = MagicMock()
    settings.enabled_symbols = [s for s in symbols if s.enabled]
    settings.data.binance_api_base = "https://api.binance.com"
    settings.data.max_candles_per_request = 1000
    return settings


# ── 3b.3: --days argument ─────────────────────────────────────────────────────

class TestParseArgs:
    def test_default_days_is_730(self):
        with patch("sys.argv", ["pull_historical_data.py"]):
            args = parse_args()
        assert args.days == 730

    def test_custom_days_override(self):
        with patch("sys.argv", ["pull_historical_data.py", "--days", "30"]):
            args = parse_args()
        assert args.days == 30

    def test_symbol_filter_defaults_to_none(self):
        with patch("sys.argv", ["pull_historical_data.py"]):
            args = parse_args()
        assert args.symbol is None

    def test_timeframe_filter_defaults_to_none(self):
        with patch("sys.argv", ["pull_historical_data.py"]):
            args = parse_args()
        assert args.timeframe is None

    def test_symbol_filter_is_parsed(self):
        with patch("sys.argv", ["pull_historical_data.py", "--symbol", "BTCUSDT"]):
            args = parse_args()
        assert args.symbol == "BTCUSDT"

    def test_timeframe_filter_is_parsed(self):
        with patch("sys.argv", ["pull_historical_data.py", "--timeframe", "15m"]):
            args = parse_args()
        assert args.timeframe == "15m"


# ── build_pairs ───────────────────────────────────────────────────────────────

class TestBuildPairs:
    def test_all_enabled_symbols_returned(self):
        settings = _make_settings([
            _make_sym("BTCUSDT", ["15m", "1h"]),
            _make_sym("ETHUSDT", ["15m", "1h"]),
        ])
        pairs = build_pairs(settings, None, None)
        assert len(pairs) == 4
        assert ("BTCUSDT", "15m") in pairs
        assert ("BTCUSDT", "1h") in pairs
        assert ("ETHUSDT", "15m") in pairs

    def test_disabled_symbols_excluded(self):
        settings = _make_settings([
            _make_sym("BTCUSDT", ["15m", "1h"], enabled=True),
            _make_sym("ETHUSDT", ["15m", "1h"], enabled=False),
        ])
        pairs = build_pairs(settings, None, None)
        assert all(s == "BTCUSDT" for s, _ in pairs)

    def test_symbol_filter_limits_results(self):
        settings = _make_settings([
            _make_sym("BTCUSDT", ["15m", "1h"]),
            _make_sym("ETHUSDT", ["15m", "1h"]),
        ])
        pairs = build_pairs(settings, "BTCUSDT", None)
        assert all(s == "BTCUSDT" for s, _ in pairs)
        assert len(pairs) == 2

    def test_timeframe_filter_limits_results(self):
        settings = _make_settings([
            _make_sym("BTCUSDT", ["15m", "1h"]),
            _make_sym("ETHUSDT", ["15m", "1h"]),
        ])
        pairs = build_pairs(settings, None, "15m")
        assert all(tf == "15m" for _, tf in pairs)
        assert len(pairs) == 2

    def test_combined_filters(self):
        settings = _make_settings([
            _make_sym("BTCUSDT", ["15m", "1h"]),
            _make_sym("ETHUSDT", ["15m", "1h"]),
        ])
        pairs = build_pairs(settings, "BTCUSDT", "1h")
        assert pairs == [("BTCUSDT", "1h")]


# ── run(): core logic ─────────────────────────────────────────────────────────

class TestRun:
    PAIRS = [
        ("BTCUSDT", "15m"), ("BTCUSDT", "1h"),
        ("ETHUSDT", "15m"), ("ETHUSDT", "1h"),
    ]

    def _make_loader(self, return_values):
        """Return a mock DataLoader whose load_historical returns values in sequence."""
        loader = MagicMock()
        loader.load_historical.side_effect = return_values
        return loader

    @patch("scripts.pull_historical_data.get_session")
    @patch("scripts.pull_historical_data.DataLoader")
    @patch("scripts.pull_historical_data.BinanceClient")
    def test_all_pairs_are_processed(self, mock_client_cls, mock_loader_cls, mock_get_session):
        mock_get_session.return_value.__enter__ = MagicMock(return_value=MagicMock())
        mock_get_session.return_value.__exit__ = MagicMock(return_value=False)
        mock_loader_cls.return_value.load_historical.return_value = 100

        results = run(self.PAIRS, days=30, binance_base_url="https://api.binance.com", max_candles=1000)

        assert len(results) == 4
        assert mock_loader_cls.return_value.load_historical.call_count == 4

    @patch("scripts.pull_historical_data.get_session")
    @patch("scripts.pull_historical_data.DataLoader")
    @patch("scripts.pull_historical_data.BinanceClient")
    def test_failed_pair_does_not_abort_run(self, mock_client_cls, mock_loader_cls, mock_get_session):
        mock_get_session.return_value.__enter__ = MagicMock(return_value=MagicMock())
        mock_get_session.return_value.__exit__ = MagicMock(return_value=False)

        # Pair index 1 (BTCUSDT 1h) raises — remaining 3 must still be called
        mock_loader_cls.return_value.load_historical.side_effect = [
            50,
            RuntimeError("API timeout"),
            75,
            90,
        ]

        results = run(self.PAIRS, days=30, binance_base_url="https://api.binance.com", max_candles=1000)

        assert len(results) == 4
        assert results[("BTCUSDT", "15m")]["status"] == "OK"
        assert results[("BTCUSDT", "1h")]["status"] == "FAILED"
        assert results[("ETHUSDT", "15m")]["status"] == "OK"
        assert results[("ETHUSDT", "1h")]["status"] == "OK"

    @patch("scripts.pull_historical_data.get_session")
    @patch("scripts.pull_historical_data.DataLoader")
    @patch("scripts.pull_historical_data.BinanceClient")
    def test_new_candle_counts_recorded(self, mock_client_cls, mock_loader_cls, mock_get_session):
        mock_get_session.return_value.__enter__ = MagicMock(return_value=MagicMock())
        mock_get_session.return_value.__exit__ = MagicMock(return_value=False)
        mock_loader_cls.return_value.load_historical.side_effect = [100, 200, 300, 400]

        results = run(self.PAIRS, days=30, binance_base_url="https://api.binance.com", max_candles=1000)

        assert results[("BTCUSDT", "15m")]["new"] == 100
        assert results[("BTCUSDT", "1h")]["new"] == 200
        assert results[("ETHUSDT", "15m")]["new"] == 300
        assert results[("ETHUSDT", "1h")]["new"] == 400

    @patch("scripts.pull_historical_data.get_session")
    @patch("scripts.pull_historical_data.DataLoader")
    @patch("scripts.pull_historical_data.BinanceClient")
    def test_failed_pair_error_message_captured(self, mock_client_cls, mock_loader_cls, mock_get_session):
        mock_get_session.return_value.__enter__ = MagicMock(return_value=MagicMock())
        mock_get_session.return_value.__exit__ = MagicMock(return_value=False)
        mock_loader_cls.return_value.load_historical.side_effect = RuntimeError("Binance 429")

        results = run([("BTCUSDT", "15m")], days=7, binance_base_url="https://api.binance.com", max_candles=1000)

        assert "Binance 429" in results[("BTCUSDT", "15m")]["error"]

    @patch("scripts.pull_historical_data.get_session")
    @patch("scripts.pull_historical_data.DataLoader")
    @patch("scripts.pull_historical_data.BinanceClient")
    def test_load_historical_called_with_correct_days(self, mock_client_cls, mock_loader_cls, mock_get_session):
        mock_get_session.return_value.__enter__ = MagicMock(return_value=MagicMock())
        mock_get_session.return_value.__exit__ = MagicMock(return_value=False)
        mock_loader_cls.return_value.load_historical.return_value = 0

        run([("BTCUSDT", "15m")], days=42, binance_base_url="https://api.binance.com", max_candles=1000)

        mock_loader_cls.return_value.load_historical.assert_called_once_with(
            symbol="BTCUSDT", timeframe="15m", lookback_days=42
        )
