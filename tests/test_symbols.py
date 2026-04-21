"""
Tests for symbol management — API endpoints (task 7.12).
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


# ── API Endpoint Tests ──────────────────────────────────────────────────────


class TestSymbolEndpoints:
    """Tests for the symbols REST API via FastAPI test client."""

    @pytest.fixture
    def client(self):
        from api.main import create_app
        from fastapi.testclient import TestClient

        app = create_app()
        return TestClient(app)

    def test_list_symbols(self, client):
        resp = client.get("/api/config/symbols")
        assert resp.status_code == 200
        data = resp.json()
        assert "symbols" in data
        assert isinstance(data["symbols"], list)
        assert len(data["symbols"]) > 0
        for s in data["symbols"]:
            assert "symbol" in s
            assert "exchange" in s
            assert "timeframes" in s
            assert "enabled" in s
            assert "source" in s

    def test_add_symbol_duplicate(self, client):
        resp = client.post(
            "/api/config/symbols",
            json={"symbol": "BTCUSDT", "timeframes": ["15m"]},
        )
        assert resp.status_code == 409
        assert "already exists" in resp.json()["detail"]

    def test_add_symbol_invalid_format(self, client):
        resp = client.post(
            "/api/config/symbols",
            json={"symbol": "INVALID", "timeframes": ["15m"]},
        )
        assert resp.status_code == 422

    def test_add_symbol_invalid_timeframe(self, client):
        resp = client.post(
            "/api/config/symbols",
            json={"symbol": "XRPUSDT", "timeframes": ["3m"]},
        )
        assert resp.status_code == 422

    @patch("api.routes.symbols._validate_symbol_on_binance", return_value=False)
    def test_add_symbol_not_on_binance(self, mock_validate, client):
        resp = client.post(
            "/api/config/symbols",
            json={"symbol": "FAKEUSDT", "timeframes": ["15m"]},
        )
        assert resp.status_code == 400
        assert "not found on Binance" in resp.json()["detail"]

    @patch("api.routes.symbols._validate_symbol_on_binance", return_value=True)
    @patch("api.routes.symbols.save_symbol")
    @patch("api.routes.symbols.reload_settings")
    def test_add_symbol_success(self, mock_reload, mock_save, mock_validate, client):
        resp = client.post(
            "/api/config/symbols",
            json={"symbol": "ADAUSDT", "timeframes": ["15m", "1h"], "description": "Cardano"},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["symbol"]["symbol"] == "ADAUSDT"
        assert data["message"] == "ADAUSDT added successfully"
        mock_save.assert_called_once()
        mock_reload.assert_called_once()

    def test_delete_symbol_not_found(self, client):
        resp = client.delete("/api/config/symbols/NONEUSDT")
        assert resp.status_code == 404

    @patch("api.routes.symbols.get_symbol_source", return_value="default")
    @patch("api.routes.symbols.save_symbol")
    @patch("api.routes.symbols.reload_settings")
    def test_delete_default_symbol_disables(self, mock_reload, mock_save, mock_source, client):
        resp = client.delete("/api/config/symbols/BTCUSDT")
        assert resp.status_code == 200
        assert "disabled" in resp.json()["message"]
        mock_save.assert_called_once_with(symbol="BTCUSDT", enabled=False)

    def test_patch_symbol_not_found(self, client):
        resp = client.patch(
            "/api/config/symbols/NONEUSDT",
            json={"enabled": False},
        )
        assert resp.status_code == 404

    @patch("api.routes.symbols.save_symbol")
    @patch("api.routes.symbols.reload_settings")
    def test_patch_symbol_toggle_enabled(self, mock_reload, mock_save, client):
        resp = client.patch(
            "/api/config/symbols/BTCUSDT",
            json={"enabled": False},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["symbol"] == "BTCUSDT"
        assert data["enabled"] is False
        mock_save.assert_called_once()

    def test_patch_symbol_invalid_timeframe(self, client):
        resp = client.patch(
            "/api/config/symbols/BTCUSDT",
            json={"timeframes": ["99x"]},
        )
        assert resp.status_code == 422

    @patch("api.routes.symbols.save_symbol")
    @patch("api.routes.symbols.reload_settings")
    def test_patch_symbol_update_timeframes(self, mock_reload, mock_save, client):
        resp = client.patch(
            "/api/config/symbols/BTCUSDT",
            json={"timeframes": ["15m", "1h", "4h"]},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "4h" in data["timeframes"]


# ── Open Position Block Test ─────────────────────────────────────────────────


class TestDeleteWithOpenPositions:
    """Test that delete is blocked when open positions exist."""

    @pytest.fixture
    def client(self):
        from api.main import create_app
        from fastapi.testclient import TestClient

        app = create_app()
        return TestClient(app)

    @patch("api.routes.symbols.get_symbol_source", return_value="db")
    @patch("api.routes.symbols.delete_symbol")
    @patch("api.routes.symbols.reload_settings")
    def test_delete_blocked_by_open_positions(self, mock_reload, mock_delete, mock_source, client):
        mock_session = MagicMock()
        mock_session.__enter__ = MagicMock(return_value=mock_session)
        mock_session.__exit__ = MagicMock(return_value=False)
        mock_session.execute.return_value.scalar.return_value = 2

        with patch("api.routes.symbols.get_settings") as mock_settings:
            mock_sym = MagicMock()
            mock_sym.symbol = "BTCUSDT"
            mock_settings.return_value.symbols = [mock_sym]

            with patch("db.get_session", return_value=mock_session):
                resp = client.delete("/api/config/symbols/BTCUSDT")

        assert resp.status_code == 409
        assert "open position" in resp.json()["detail"]
        mock_delete.assert_not_called()
