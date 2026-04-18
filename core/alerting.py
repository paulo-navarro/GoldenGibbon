"""
Telegram alerting for trading events (task 4.9).

Sends notifications to a Telegram chat via the Bot API when
important events occur: order fills, stop hits, kill-switch
activation, reconciliation mismatches.

**Design principles:**

* **Fire-and-forget** – ``send()`` never raises.  If the Telegram
  API is unreachable the call is silently logged and skipped.
* **Synchronous** – matches the sync trading loop; Telegram API
  calls are fast (sub-second).
* **Singleton** – ``get_alerter()`` returns a module-level instance.

Usage::

    from core.alerting import get_alerter

    alerter = get_alerter()
    alerter.alert_fill(symbol="BTCUSDT", side="BUY", size="0.01",
                       price="65000", pnl="+150.00 USDT")
"""

from __future__ import annotations

import os
from decimal import Decimal
from typing import Any, Optional

import requests
import structlog

logger = structlog.get_logger(__name__)

_TELEGRAM_API = "https://api.telegram.org"


class Alerter:
    """
    Sends trading alerts to Telegram via Bot API.

    Parameters
    ----------
    bot_token:
        Telegram Bot API token.
    chat_id:
        Target chat/group ID.
    """

    def __init__(self, bot_token: str, chat_id: str) -> None:
        self._bot_token = bot_token
        self._chat_id = chat_id
        self._enabled = bool(bot_token and chat_id)

        if self._enabled:
            logger.info("alerter.connected", chat_id=chat_id)
        else:
            logger.warning("alerter.disabled", reason="missing bot_token or chat_id")

    # ── Properties ───────────────────────────────────────────────────

    @property
    def enabled(self) -> bool:
        return self._enabled

    # ── Low-level send ───────────────────────────────────────────────

    def send(self, message: str) -> None:
        """
        Send a text message to the configured Telegram chat.

        Uses MarkdownV2 parse mode.  Never raises — errors are logged.
        """
        if not self._enabled:
            return

        url = f"{_TELEGRAM_API}/bot{self._bot_token}/sendMessage"
        payload = {
            "chat_id": self._chat_id,
            "text": message,
            "parse_mode": "HTML",
        }

        try:
            resp = requests.post(url, json=payload, timeout=10)
            if not resp.ok:
                logger.warning(
                    "alerter.send_failed",
                    status=resp.status_code,
                    body=resp.text[:200],
                )
            else:
                logger.debug("alerter.sent", chat_id=self._chat_id)
        except Exception as exc:
            logger.error("alerter.send_error", error=str(exc))

    # ── High-level alert methods ─────────────────────────────────────

    def alert_fill(
        self,
        *,
        symbol: str,
        side: str,
        size: str,
        price: str,
        strategy: str,
        pnl: str | None = None,
    ) -> None:
        """Alert on order fill (trade open or close)."""
        icon = "\u2705" if side.upper() == "BUY" else "\U0001f534"  # ✅ or 🔴
        lines = [
            f"{icon} <b>{side.upper()} {symbol}</b>",
            f"Size: {size} @ {price}",
            f"Strategy: {strategy}",
        ]
        if pnl is not None:
            lines.append(f"PnL: {pnl}")
        self.send("\n".join(lines))

    def alert_stop(
        self,
        *,
        symbol: str,
        stop_type: str,
        price: str,
        strategy: str,
    ) -> None:
        """Alert on stop-loss trigger."""
        self.send(
            f"\u26a0\ufe0f <b>STOP HIT — {symbol}</b>\n"
            f"Type: {stop_type}\n"
            f"Price: {price}\n"
            f"Strategy: {strategy}"
        )

    def alert_kill_switch(
        self,
        *,
        reason: str,
        equity: str,
        peak_equity: str,
    ) -> None:
        """Alert on kill-switch activation."""
        self.send(
            f"\U0001f6a8 <b>KILL SWITCH ACTIVATED</b>\n"
            f"Reason: {reason}\n"
            f"Equity: {equity}\n"
            f"Peak: {peak_equity}"
        )

    def alert_reconciliation_mismatch(
        self,
        *,
        details: str,
    ) -> None:
        """Alert on reconciliation mismatch."""
        self.send(
            f"\u26a0\ufe0f <b>RECONCILIATION MISMATCH</b>\n"
            f"{details}"
        )

    def alert_error(
        self,
        *,
        task: str,
        error: str,
    ) -> None:
        """Alert on task error."""
        # Truncate long error messages
        error_short = error[:300] + "..." if len(error) > 300 else error
        self.send(
            f"\u274c <b>ERROR in {task}</b>\n"
            f"{error_short}"
        )


# ── Module-level Singleton ───────────────────────────────────────────────────

_alerter: Alerter | None = None


def get_alerter() -> Alerter:
    """
    Return the module-level :class:`Alerter` singleton.

    Creates the instance on first call using settings from
    ``AlertingConfig``.
    """
    global _alerter  # noqa: PLW0603
    if _alerter is None:
        from core.config import get_settings

        settings = get_settings()
        cfg = settings.alerting

        if not cfg.enabled:
            # Disabled alerter — all methods are no-ops
            _alerter = Alerter(bot_token="", chat_id="")
        else:
            bot_token = os.environ.get(cfg.bot_token_env, "")
            chat_id = os.environ.get(cfg.chat_id_env, "")
            _alerter = Alerter(bot_token=bot_token, chat_id=chat_id)

    return _alerter


def init_alerter(bot_token: str, chat_id: str) -> Alerter:
    """
    (Re-)initialise the module-level :class:`Alerter`.

    Use this at application startup or in tests to override defaults.
    """
    global _alerter  # noqa: PLW0603
    _alerter = Alerter(bot_token=bot_token, chat_id=chat_id)
    return _alerter


def reset_alerter() -> None:
    """Tear down the singleton.  Primarily used by tests."""
    global _alerter  # noqa: PLW0603
    _alerter = None
