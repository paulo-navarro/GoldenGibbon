"""
Global kill-switch – emergency stop for live trading.

Monitors two thresholds and blocks all trading when either is breached:

1. **Global drawdown** – peak-to-current equity drop exceeds
   ``kill_switch_max_drawdown`` (default 15%).  Never resets
   automatically.

2. **Daily loss** – equity drop since midnight UTC exceeds
   ``max_daily_loss_pct`` (default 10%).  Resets at the start of
   each new trading day.

Once triggered the kill-switch stays latched until explicitly
reset via ``reset()``.  Existing positions keep their per-trade
stops but no new orders are placed.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Optional

import structlog

from core.events import EventChannel, EventType, get_publisher

logger = structlog.get_logger(__name__)


class KillSwitch:
    """
    Portfolio-level emergency stop.

    Lives inside ``_TickComponents`` and is checked on every tick
    *before* strategy evaluation.
    """

    def __init__(
        self,
        max_drawdown_pct: float = 0.15,
        max_daily_loss_pct: float = 0.10,
    ) -> None:
        self._max_drawdown = Decimal(str(max_drawdown_pct))
        self._max_daily_loss = Decimal(str(max_daily_loss_pct))

        # Peak equity (high-water mark) – only increases
        self._peak_equity: Decimal = Decimal("0")

        # Start-of-day equity – resets at midnight UTC
        self._day_start_equity: Decimal = Decimal("0")
        self._current_day: Optional[date] = None

        # Latch state
        self._triggered: bool = False
        self._trigger_reason: Optional[str] = None
        self._trigger_time: Optional[datetime] = None

    # ── Properties ───────────────────────────────────────────────────────

    @property
    def is_triggered(self) -> bool:
        return self._triggered

    @property
    def trigger_reason(self) -> Optional[str]:
        return self._trigger_reason

    @property
    def trigger_time(self) -> Optional[datetime]:
        return self._trigger_time

    @property
    def peak_equity(self) -> Decimal:
        return self._peak_equity

    @property
    def day_start_equity(self) -> Decimal:
        return self._day_start_equity

    # ── Main check ───────────────────────────────────────────────────────

    def check(
        self,
        current_equity: Decimal,
        timestamp: datetime,
    ) -> bool:
        """
        Evaluate kill-switch conditions.

        Call this after every portfolio snapshot.  Returns ``True`` if
        trading should be blocked (either newly triggered or already
        latched).

        Args:
            current_equity: Total portfolio value (USDT balance +
                            positions marked-to-market).
            timestamp: Current tick time (UTC).

        Returns:
            ``True`` if trading is blocked, ``False`` if clear.
        """
        if self._triggered:
            return True

        # ── Initialise on first call ─────────────────────────────────
        if self._peak_equity == Decimal("0"):
            self._peak_equity = current_equity
            self._day_start_equity = current_equity
            self._current_day = timestamp.date()
            return False

        # ── Update high-water mark ───────────────────────────────────
        if current_equity > self._peak_equity:
            self._peak_equity = current_equity

        # ── Daily reset ──────────────────────────────────────────────
        today = timestamp.date()
        if self._current_day != today:
            self._day_start_equity = current_equity
            self._current_day = today

        # ── Global drawdown check ────────────────────────────────────
        if self._peak_equity > 0:
            drawdown = (self._peak_equity - current_equity) / self._peak_equity
            if drawdown >= self._max_drawdown:
                self._trigger(
                    reason=(
                        f"Global drawdown {drawdown:.2%} exceeded "
                        f"threshold {self._max_drawdown:.2%}"
                    ),
                    timestamp=timestamp,
                    data={
                        "type": "global_drawdown",
                        "drawdown_pct": str(drawdown),
                        "threshold_pct": str(self._max_drawdown),
                        "peak_equity": str(self._peak_equity),
                        "current_equity": str(current_equity),
                    },
                )
                return True

        # ── Daily loss check ─────────────────────────────────────────
        if self._day_start_equity > 0:
            daily_loss = (
                (self._day_start_equity - current_equity) / self._day_start_equity
            )
            if daily_loss >= self._max_daily_loss:
                self._trigger(
                    reason=(
                        f"Daily loss {daily_loss:.2%} exceeded "
                        f"threshold {self._max_daily_loss:.2%}"
                    ),
                    timestamp=timestamp,
                    data={
                        "type": "daily_loss",
                        "daily_loss_pct": str(daily_loss),
                        "threshold_pct": str(self._max_daily_loss),
                        "day_start_equity": str(self._day_start_equity),
                        "current_equity": str(current_equity),
                    },
                )
                return True

        return False

    # ── Reset ────────────────────────────────────────────────────────────

    def reset(self) -> None:
        """
        Manually reset the kill-switch after investigation.

        Does NOT reset peak equity — the high-water mark persists so
        that drawdown is always measured from the true peak.
        """
        prev_reason = self._trigger_reason

        self._triggered = False
        self._trigger_reason = None
        self._trigger_time = None

        logger.info(
            "kill_switch.reset",
            category="risk",
            previous_reason=prev_reason,
            peak_equity=str(self._peak_equity),
        )

    def hard_reset(self) -> None:
        """Full reset including peak equity.

        Use when trading mode changes (paper -> live) or for manual
        recovery.  The next ``check()`` call will re-initialise peak
        from the current equity value.
        """
        prev_reason = self._trigger_reason
        prev_peak = self._peak_equity

        self._triggered = False
        self._trigger_reason = None
        self._trigger_time = None
        self._peak_equity = Decimal("0")
        self._day_start_equity = Decimal("0")
        self._current_day = None

        logger.info(
            "kill_switch.hard_reset",
            category="risk",
            previous_reason=prev_reason,
            previous_peak_equity=str(prev_peak),
        )

    # ── State serialisation (for persistence / recovery) ─────────────────

    def to_dict(self, trading_mode: Optional[str] = None) -> dict:
        """Serialise state for persistence in StrategyStateRecord.state_data."""
        d: dict = {
            "kill_switch_triggered": self._triggered,
            "kill_switch_reason": self._trigger_reason,
            "kill_switch_trigger_time": (
                self._trigger_time.isoformat() if self._trigger_time else None
            ),
            "kill_switch_peak_equity": str(self._peak_equity),
            "kill_switch_day_start_equity": str(self._day_start_equity),
            "kill_switch_current_day": (
                self._current_day.isoformat() if self._current_day else None
            ),
        }
        if trading_mode is not None:
            d["kill_switch_trading_mode"] = trading_mode
        return d

    def restore_from_dict(
        self, data: dict, current_trading_mode: Optional[str] = None,
    ) -> None:
        """Restore state from persisted state_data dict."""
        saved_mode = data.get("kill_switch_trading_mode")
        if (
            current_trading_mode
            and saved_mode
            and current_trading_mode != saved_mode
        ):
            logger.warning(
                "kill_switch.mode_changed_skip_restore",
                saved_mode=saved_mode,
                current_mode=current_trading_mode,
                saved_peak=data.get("kill_switch_peak_equity"),
            )
            return

        self._triggered = data.get("kill_switch_triggered", False)
        self._trigger_reason = data.get("kill_switch_reason")

        trigger_time = data.get("kill_switch_trigger_time")
        if trigger_time:
            self._trigger_time = datetime.fromisoformat(trigger_time)

        peak = data.get("kill_switch_peak_equity")
        if peak:
            self._peak_equity = Decimal(peak)

        day_start = data.get("kill_switch_day_start_equity")
        if day_start:
            self._day_start_equity = Decimal(day_start)

        current_day = data.get("kill_switch_current_day")
        if current_day:
            self._current_day = date.fromisoformat(current_day)

        if self._triggered:
            logger.warning(
                "kill_switch.restored_triggered",
                category="risk",
                reason=self._trigger_reason,
                peak_equity=str(self._peak_equity),
            )

    # ── Private ──────────────────────────────────────────────────────────

    def _trigger(
        self,
        reason: str,
        timestamp: datetime,
        data: dict,
    ) -> None:
        """Latch the kill-switch and publish an event."""
        self._triggered = True
        self._trigger_reason = reason
        self._trigger_time = timestamp

        logger.critical(
            "kill_switch.TRIGGERED",
            category="risk",
            reason=reason,
            **data,
        )

        publisher = get_publisher()
        publisher.publish(
            EventChannel.SYSTEM,
            EventType.KILL_SWITCH_TRIGGERED,
            {"reason": reason, "timestamp": timestamp.isoformat(), **data},
        )
