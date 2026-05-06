"""
Unit tests for Telegram alerting (task 4.9).

Covers:
    - Alerter.send() calls Telegram Bot API correctly
    - Disabled alerter is a silent no-op
    - Fire-and-forget error handling (never raises)
    - High-level alert methods format messages correctly
    - Singleton lifecycle (get_alerter, init_alerter, reset_alerter)
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from core.alerting import (
    Alerter,
    AlertSeverity,
    get_alerter,
    init_alerter,
    reset_alerter,
)


# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _isolate_alerter():
    reset_alerter()
    yield
    reset_alerter()


def _make_alerter(*, enabled: bool = True) -> Alerter:
    if enabled:
        return Alerter(bot_token="123:ABC", chat_id="456")
    return Alerter(bot_token="", chat_id="")


# ── Enabled / Disabled ─────────────────────────────────────────────────────


class TestEnabled:
    def test_enabled_with_credentials(self):
        a = _make_alerter(enabled=True)
        assert a.enabled is True

    def test_disabled_without_credentials(self):
        a = _make_alerter(enabled=False)
        assert a.enabled is False

    def test_disabled_with_empty_token(self):
        a = Alerter(bot_token="", chat_id="456")
        assert a.enabled is False

    def test_disabled_with_empty_chat_id(self):
        a = Alerter(bot_token="123:ABC", chat_id="")
        assert a.enabled is False


# ── send() ─────────────────────────────────────────────────────────────────


class TestSend:
    @patch("core.alerting.requests.post")
    def test_sends_to_correct_url(self, mock_post):
        mock_post.return_value = MagicMock(ok=True)
        a = _make_alerter()
        a.send("hello")

        mock_post.assert_called_once()
        url = mock_post.call_args[0][0]
        assert "bot123:ABC" in url
        assert "sendMessage" in url

    @patch("core.alerting.requests.post")
    def test_sends_with_html_parse_mode(self, mock_post):
        mock_post.return_value = MagicMock(ok=True)
        a = _make_alerter()
        a.send("test")

        payload = mock_post.call_args[1]["json"]
        assert payload["parse_mode"] == "HTML"
        assert payload["chat_id"] == "456"
        assert payload["text"] == "test"

    @patch("core.alerting.requests.post")
    def test_disabled_send_is_noop(self, mock_post):
        a = _make_alerter(enabled=False)
        a.send("hello")
        mock_post.assert_not_called()

    @patch("core.alerting.requests.post")
    def test_send_swallows_network_error(self, mock_post):
        mock_post.side_effect = ConnectionError("timeout")
        a = _make_alerter()
        # Must not raise
        a.send("hello")

    @patch("core.alerting.requests.post")
    def test_send_handles_api_error(self, mock_post):
        mock_post.return_value = MagicMock(ok=False, status_code=400, text="Bad Request")
        a = _make_alerter()
        # Must not raise
        a.send("hello")


# ── High-level alerts ──────────────────────────────────────────────────────


class TestAlertFill:
    @patch("core.alerting.requests.post")
    def test_buy_fill_message(self, mock_post):
        mock_post.return_value = MagicMock(ok=True)
        a = _make_alerter()
        a.alert_fill(
            symbol="BTCUSDT",
            side="BUY",
            size="0.01",
            price="65000",
            strategy="smart_hodler",
        )

        text = mock_post.call_args[1]["json"]["text"]
        assert "BUY" in text
        assert "BTCUSDT" in text
        assert "0.01" in text
        assert "65000" in text

    @patch("core.alerting.requests.post")
    def test_sell_fill_with_pnl(self, mock_post):
        mock_post.return_value = MagicMock(ok=True)
        a = _make_alerter()
        a.alert_fill(
            symbol="ETHUSDT",
            side="SELL",
            size="1.0",
            price="3500",
            strategy="mean_reversion",
            pnl="+50.00 USDT",
        )

        text = mock_post.call_args[1]["json"]["text"]
        assert "SELL" in text
        assert "+50.00 USDT" in text


class TestAlertStop:
    @patch("core.alerting.requests.post")
    def test_stop_message(self, mock_post):
        mock_post.return_value = MagicMock(ok=True)
        a = _make_alerter()
        a.alert_stop(
            symbol="BTCUSDT",
            stop_type="trailing_stop",
            price="62000",
            strategy="smart_hodler",
        )

        text = mock_post.call_args[1]["json"]["text"]
        assert "STOP HIT" in text
        assert "BTCUSDT" in text
        assert "trailing_stop" in text


class TestAlertKillSwitch:
    @patch("core.alerting.requests.post")
    def test_kill_switch_message(self, mock_post):
        mock_post.return_value = MagicMock(ok=True)
        a = _make_alerter()
        a.alert_kill_switch(
            reason="Global drawdown 16.0% >= 15.0%",
            equity="8400",
            peak_equity="10000",
        )

        text = mock_post.call_args[1]["json"]["text"]
        assert "KILL SWITCH" in text
        assert "16.0%" in text
        assert "8400" in text
        assert "10000" in text


class TestAlertReconciliation:
    @patch("core.alerting.requests.post")
    def test_reconciliation_message(self, mock_post):
        mock_post.return_value = MagicMock(ok=True)
        a = _make_alerter()
        a.alert_reconciliation_mismatch(
            details="USDT balance: local=10000 vs exchange=8000",
        )

        text = mock_post.call_args[1]["json"]["text"]
        assert "RECONCILIATION" in text
        assert "10000" in text


class TestAlertError:
    @patch("core.alerting.requests.post")
    def test_error_message(self, mock_post):
        mock_post.return_value = MagicMock(ok=True)
        a = _make_alerter()
        a.alert_error(task="tick:smart_hodler:BTCUSDT", error="division by zero")

        text = mock_post.call_args[1]["json"]["text"]
        assert "ERROR" in text
        assert "division by zero" in text

    @patch("core.alerting.requests.post")
    def test_long_error_truncated(self, mock_post):
        mock_post.return_value = MagicMock(ok=True)
        a = _make_alerter()
        a.alert_error(task="test", error="x" * 500)

        text = mock_post.call_args[1]["json"]["text"]
        assert "..." in text
        assert len(text) < 600  # truncation worked


# ── Singleton lifecycle ────────────────────────────────────────────────────


class TestSingleton:
    def test_get_alerter_returns_same_instance(self):
        a1 = get_alerter()
        a2 = get_alerter()
        assert a1 is a2

    def test_init_alerter_replaces_singleton(self):
        a1 = get_alerter()
        a2 = init_alerter("token", "chat")
        assert a2 is not a1
        assert a2.enabled is True
        assert get_alerter() is a2

    def test_reset_clears_singleton(self):
        a1 = get_alerter()
        reset_alerter()
        a2 = get_alerter()
        assert a2 is not a1

    def test_get_alerter_disabled_by_default(self):
        """Default config has alerting.enabled=False → disabled alerter."""
        a = get_alerter()
        assert a.enabled is False


# ── AlertSeverity ──────────────────────────────────────────────────────────


class TestAlertSeverity:
    def test_enum_values(self):
        assert AlertSeverity.INFO == "info"
        assert AlertSeverity.WARNING == "warning"
        assert AlertSeverity.CRITICAL == "critical"

    def test_meets_threshold_critical_vs_warning(self):
        assert AlertSeverity.meets_threshold(AlertSeverity.CRITICAL, AlertSeverity.WARNING) is True

    def test_meets_threshold_warning_vs_warning(self):
        assert AlertSeverity.meets_threshold(AlertSeverity.WARNING, AlertSeverity.WARNING) is True

    def test_meets_threshold_info_below_warning(self):
        assert AlertSeverity.meets_threshold(AlertSeverity.INFO, AlertSeverity.WARNING) is False

    def test_meets_threshold_info_vs_info(self):
        assert AlertSeverity.meets_threshold(AlertSeverity.INFO, AlertSeverity.INFO) is True

    def test_meets_threshold_warning_below_critical(self):
        assert AlertSeverity.meets_threshold(AlertSeverity.WARNING, AlertSeverity.CRITICAL) is False


# ── alert_reconciliation() ─────────────────────────────────────────────────


class TestAlertReconciliationSeverity:
    @patch("core.alerting.requests.post")
    def test_critical_sends_alert(self, mock_post):
        mock_post.return_value = MagicMock(ok=True)
        a = _make_alerter()
        a.alert_reconciliation(
            severity=AlertSeverity.CRITICAL,
            event_type="position_force_closed",
            details="BTCUSDT: 2 positions closed",
            min_severity=AlertSeverity.WARNING,
        )
        mock_post.assert_called_once()
        text = mock_post.call_args[1]["json"]["text"]
        assert "CRITICAL RECONCILIATION" in text
        assert "position_force_closed" in text
        assert "BTCUSDT" in text

    @patch("core.alerting.requests.post")
    def test_warning_sends_with_min_warning(self, mock_post):
        mock_post.return_value = MagicMock(ok=True)
        a = _make_alerter()
        a.alert_reconciliation(
            severity=AlertSeverity.WARNING,
            event_type="order_orphan_detected",
            details="Orphan BUY order on ETHUSDT",
            min_severity=AlertSeverity.WARNING,
        )
        mock_post.assert_called_once()
        text = mock_post.call_args[1]["json"]["text"]
        assert "RECONCILIATION WARNING" in text
        assert "order_orphan_detected" in text

    @patch("core.alerting.requests.post")
    def test_info_not_sent_with_min_warning(self, mock_post):
        a = _make_alerter()
        a.alert_reconciliation(
            severity=AlertSeverity.INFO,
            event_type="stop_order_synced",
            details="Stop synced OK",
            min_severity=AlertSeverity.WARNING,
        )
        mock_post.assert_not_called()

    @patch("core.alerting.requests.post")
    def test_info_sent_with_min_info(self, mock_post):
        mock_post.return_value = MagicMock(ok=True)
        a = _make_alerter()
        a.alert_reconciliation(
            severity=AlertSeverity.INFO,
            event_type="stop_order_synced",
            details="Stop synced OK",
            min_severity=AlertSeverity.INFO,
        )
        mock_post.assert_called_once()

    @patch("core.alerting.requests.post")
    def test_warning_not_sent_with_min_critical(self, mock_post):
        a = _make_alerter()
        a.alert_reconciliation(
            severity=AlertSeverity.WARNING,
            event_type="order_orphan_detected",
            details="Orphan order",
            min_severity=AlertSeverity.CRITICAL,
        )
        mock_post.assert_not_called()

    @patch("core.alerting.requests.post")
    def test_disabled_alerter_no_error(self, mock_post):
        a = _make_alerter(enabled=False)
        a.alert_reconciliation(
            severity=AlertSeverity.CRITICAL,
            event_type="position_force_closed",
            details="test",
            min_severity=AlertSeverity.WARNING,
        )
        mock_post.assert_not_called()
