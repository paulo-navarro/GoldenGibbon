"""
Tests for the reconciliation task (task 3.9).

Verifies DB-only consistency checks:
  A. Strategy state ↔ position record consistency
  B. Balance sanity (non-negative, FLAT equity match)
  C. Trade PnL arithmetic (advisory)

Also verifies event publishing and the worker_ready startup trigger.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from db import get_session
from db.models import PositionRecord, StrategyStateRecord, TradeRecord


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _eager_celery():
    """Switch Celery to eager mode for in-process execution."""
    from core.celery_app import app

    app.conf.task_always_eager = True
    app.conf.task_eager_propagates = True
    yield
    app.conf.task_always_eager = False
    app.conf.task_eager_propagates = False


@pytest.fixture()
def _seed_state():
    """
    Factory fixture that inserts a StrategyStateRecord and optionally
    a PositionRecord for testing.
    """

    def _create(
        *,
        strategy: str = "smart_hodler",
        symbol: str = "BTCUSDT",
        state: str = "flat",
        state_data: dict | None = None,
        position: bool = False,
        position_size: Decimal = Decimal("0.01"),
        entry_price: Decimal = Decimal("50000"),
    ) -> None:
        with get_session() as session:
            state_data = state_data or {
                "run_id": "paper_smart_hodler_BTCUSDT_20260101T000000",
                "usdt_balance": "10000",
                "equity": "10000",
                "total_pnl": "0",
                "cooldown_remaining": 0,
            }
            session.add(
                StrategyStateRecord(
                    symbol=symbol,
                    strategy=strategy,
                    state=state,
                    consecutive_buy_candles=0,
                    state_data=state_data,
                )
            )
            if position:
                now = datetime.now(timezone.utc)
                session.add(
                    PositionRecord(
                        symbol=symbol,
                        strategy=strategy,
                        side="long",
                        size=position_size,
                        entry_price=entry_price,
                        entry_time=now,
                        highest_close=entry_price,
                        trailing_stop_price=entry_price * Decimal("0.95"),
                        hard_stop_price=entry_price * Decimal("0.97"),
                        scale_in_count=0,
                        buy_signal_candles=0,
                    )
                )

    return _create


# ── Check A: State ↔ Position consistency ────────────────────────────────────


class TestReconcilePairStatePosition:
    """Verify Check A: strategy state vs position record invariants."""

    def test_ok_flat_no_position(self, _seed_state):
        """FLAT state + no PositionRecord → ok."""
        _seed_state(state="flat", position=False)

        from core.tasks import _reconcile_pair

        with get_session() as session:
            result = _reconcile_pair(session, "smart_hodler", "BTCUSDT")

        assert result["status"] == "ok"
        assert result["pair"] == "smart_hodler:BTCUSDT"
        state_check = next(c for c in result["checks"] if c["check"] == "state_position")
        assert state_check["result"] == "ok"

    def test_ok_position_with_position_record(self, _seed_state):
        """POSITION state + PositionRecord present → ok."""
        _seed_state(state="position", position=True)

        from core.tasks import _reconcile_pair

        with get_session() as session:
            result = _reconcile_pair(session, "smart_hodler", "BTCUSDT")

        state_check = next(c for c in result["checks"] if c["check"] == "state_position")
        assert state_check["result"] == "ok"

    def test_ok_reduced_with_position_record(self, _seed_state):
        """REDUCED state + PositionRecord present → ok."""
        _seed_state(state="reduced", position=True)

        from core.tasks import _reconcile_pair

        with get_session() as session:
            result = _reconcile_pair(session, "smart_hodler", "BTCUSDT")

        state_check = next(c for c in result["checks"] if c["check"] == "state_position")
        assert state_check["result"] == "ok"

    def test_mismatch_position_state_no_position_record(self, _seed_state):
        """POSITION state but no PositionRecord → mismatch, repaired to FLAT."""
        _seed_state(state="position", position=False)

        from core.tasks import _reconcile_pair

        with get_session() as session:
            result = _reconcile_pair(session, "smart_hodler", "BTCUSDT")

        assert result["status"] == "mismatch"
        assert len(result["repairs"]) == 1
        assert "reset strategy state from position to flat" in result["repairs"][0]

        # Verify repair was applied in the session
        with get_session() as session:
            rec = (
                session.query(StrategyStateRecord)
                .filter_by(symbol="BTCUSDT", strategy="smart_hodler")
                .first()
            )
            assert rec.state == "flat"

    def test_mismatch_reduced_state_no_position_record(self, _seed_state):
        """REDUCED state but no PositionRecord → mismatch, repaired to FLAT."""
        _seed_state(state="reduced", position=False)

        from core.tasks import _reconcile_pair

        with get_session() as session:
            result = _reconcile_pair(session, "smart_hodler", "BTCUSDT")

        assert result["status"] == "mismatch"
        assert any("reset strategy state" in r for r in result["repairs"])

    def test_mismatch_flat_with_orphan_position(self, _seed_state):
        """FLAT state but PositionRecord exists → mismatch, orphan deleted."""
        _seed_state(state="flat", position=True)

        from core.tasks import _reconcile_pair

        with get_session() as session:
            result = _reconcile_pair(session, "smart_hodler", "BTCUSDT")

        assert result["status"] == "mismatch"
        assert any("deleted orphan" in r for r in result["repairs"])

        # Verify orphan was deleted
        with get_session() as session:
            pos = (
                session.query(PositionRecord)
                .filter_by(symbol="BTCUSDT", strategy="smart_hodler")
                .first()
            )
            assert pos is None

    def test_mismatch_cooldown_with_orphan_position(self, _seed_state):
        """COOLDOWN state but PositionRecord exists → mismatch, orphan deleted."""
        _seed_state(state="cooldown", position=True)

        from core.tasks import _reconcile_pair

        with get_session() as session:
            result = _reconcile_pair(session, "smart_hodler", "BTCUSDT")

        assert result["status"] == "mismatch"
        assert any("deleted orphan" in r for r in result["repairs"])

    def test_no_state_no_position(self):
        """No StrategyStateRecord, no PositionRecord → ok (new pair)."""
        from core.tasks import _reconcile_pair

        with get_session() as session:
            result = _reconcile_pair(session, "smart_hodler", "BTCUSDT")

        assert result["status"] == "ok"

    def test_orphan_position_no_state_record(self):
        """PositionRecord exists but no StrategyStateRecord → mismatch."""
        now = datetime.now(timezone.utc)
        with get_session() as session:
            session.add(
                PositionRecord(
                    symbol="BTCUSDT",
                    strategy="smart_hodler",
                    side="long",
                    size=Decimal("0.01"),
                    entry_price=Decimal("50000"),
                    entry_time=now,
                    highest_close=Decimal("50000"),
                    trailing_stop_price=Decimal("47500"),
                    hard_stop_price=Decimal("48500"),
                    scale_in_count=0,
                    buy_signal_candles=0,
                )
            )

        from core.tasks import _reconcile_pair

        with get_session() as session:
            result = _reconcile_pair(session, "smart_hodler", "BTCUSDT")

        assert result["status"] == "mismatch"
        assert any("deleted orphan" in r for r in result["repairs"])


# ── Check B: Balance sanity ──────────────────────────────────────────────────


class TestReconcileBalanceSanity:
    """Verify Check B: balance / equity sanity checks."""

    def test_ok_positive_balance(self, _seed_state):
        """Positive balance + equity → ok."""
        _seed_state(
            state="flat",
            state_data={
                "run_id": "test",
                "usdt_balance": "10000",
                "equity": "10000",
                "total_pnl": "0",
            },
        )

        from core.tasks import _reconcile_pair

        with get_session() as session:
            result = _reconcile_pair(session, "smart_hodler", "BTCUSDT")

        balance_check = next(c for c in result["checks"] if c["check"] == "balance_sanity")
        assert balance_check["result"] == "ok"

    def test_warning_negative_balance(self, _seed_state):
        """Negative usdt_balance → warning."""
        _seed_state(
            state="flat",
            state_data={
                "run_id": "test",
                "usdt_balance": "-50",
                "equity": "-50",
                "total_pnl": "-10050",
            },
        )

        from core.tasks import _reconcile_pair

        with get_session() as session:
            result = _reconcile_pair(session, "smart_hodler", "BTCUSDT")

        assert result["status"] == "mismatch"
        balance_check = next(c for c in result["checks"] if c["check"] == "balance_sanity")
        assert balance_check["result"] == "warning"
        assert "negative" in balance_check["detail"]

    def test_warning_flat_balance_equity_mismatch(self, _seed_state):
        """FLAT state but usdt_balance ≠ equity → warning."""
        _seed_state(
            state="flat",
            state_data={
                "run_id": "test",
                "usdt_balance": "10000",
                "equity": "9500",
                "total_pnl": "0",
            },
        )

        from core.tasks import _reconcile_pair

        with get_session() as session:
            result = _reconcile_pair(session, "smart_hodler", "BTCUSDT")

        balance_check = next(c for c in result["checks"] if c["check"] == "balance_sanity")
        assert balance_check["result"] == "warning"
        assert "FLAT" in balance_check["detail"]

    def test_ok_position_balance_equity_differ(self, _seed_state):
        """POSITION state: balance ≠ equity is normal (position value)."""
        _seed_state(
            state="position",
            position=True,
            state_data={
                "run_id": "test",
                "usdt_balance": "5000",
                "equity": "10000",
                "total_pnl": "0",
            },
        )

        from core.tasks import _reconcile_pair

        with get_session() as session:
            result = _reconcile_pair(session, "smart_hodler", "BTCUSDT")

        balance_check = next(c for c in result["checks"] if c["check"] == "balance_sanity")
        assert balance_check["result"] == "ok"


# ── Check C: Trade PnL arithmetic ────────────────────────────────────────────


class TestReconcileTradePnl:
    """Verify Check C: trade PnL sum vs state_data.total_pnl."""

    def test_ok_pnl_matches(self, _seed_state):
        """Sum of TradeRecord.pnl_usdt matches state_data.total_pnl → ok."""
        run_id = "paper_test_run"
        _seed_state(
            state="flat",
            state_data={
                "run_id": run_id,
                "usdt_balance": "10150",
                "equity": "10150",
                "total_pnl": "150",
            },
        )
        now = datetime.now(timezone.utc)
        with get_session() as session:
            session.add(
                TradeRecord(
                    run_id=run_id,
                    symbol="BTCUSDT",
                    strategy="smart_hodler",
                    side="long",
                    entry_price=Decimal("50000"),
                    exit_price=Decimal("51500"),
                    size=Decimal("0.1"),
                    entry_time=now,
                    exit_time=now,
                    pnl_usdt=Decimal("150"),
                    pnl_percent=Decimal("3.0"),
                    duration_minutes=60,
                    exit_reason="trailing_stop",
                )
            )

        from core.tasks import _reconcile_pair

        with get_session() as session:
            result = _reconcile_pair(session, "smart_hodler", "BTCUSDT")

        pnl_check = next(c for c in result["checks"] if c["check"] == "trade_pnl")
        assert pnl_check["result"] == "ok"

    def test_warning_pnl_drift(self, _seed_state):
        """Sum of TradeRecord.pnl_usdt doesn't match state_data → warning."""
        run_id = "paper_test_run"
        _seed_state(
            state="flat",
            state_data={
                "run_id": run_id,
                "usdt_balance": "10200",
                "equity": "10200",
                "total_pnl": "200",
            },
        )
        now = datetime.now(timezone.utc)
        with get_session() as session:
            session.add(
                TradeRecord(
                    run_id=run_id,
                    symbol="BTCUSDT",
                    strategy="smart_hodler",
                    side="long",
                    entry_price=Decimal("50000"),
                    exit_price=Decimal("51500"),
                    size=Decimal("0.1"),
                    entry_time=now,
                    exit_time=now,
                    pnl_usdt=Decimal("150"),
                    pnl_percent=Decimal("3.0"),
                    duration_minutes=60,
                    exit_reason="trailing_stop",
                )
            )

        from core.tasks import _reconcile_pair

        with get_session() as session:
            result = _reconcile_pair(session, "smart_hodler", "BTCUSDT")

        pnl_check = next(c for c in result["checks"] if c["check"] == "trade_pnl")
        assert pnl_check["result"] == "warning"
        assert "delta" in pnl_check["detail"]

    def test_ok_no_run_id(self, _seed_state):
        """No run_id in state_data → PnL check is ok (nothing to compare)."""
        _seed_state(
            state="flat",
            state_data={
                "usdt_balance": "10000",
                "equity": "10000",
                "total_pnl": "0",
            },
        )

        from core.tasks import _reconcile_pair

        with get_session() as session:
            result = _reconcile_pair(session, "smart_hodler", "BTCUSDT")

        pnl_check = next(c for c in result["checks"] if c["check"] == "trade_pnl")
        assert pnl_check["result"] == "ok"


# ── Full task integration ────────────────────────────────────────────────────


class TestRunReconciliation:
    """Verify the full run_reconciliation Celery task."""

    def test_returns_summary_dict_clean_db(self):
        """Clean DB → summary with zero mismatches."""
        from core.tasks import run_reconciliation

        result = run_reconciliation.apply()
        assert result.successful()
        summary = result.result
        assert isinstance(summary, dict)
        assert summary["mismatches"] == 0
        assert summary["repairs"] == 0
        assert isinstance(summary["details"], list)

    def test_detects_and_repairs_mismatch(self, _seed_state):
        """Mismatch seeded → task detects and reports it."""
        _seed_state(
            strategy="smart_hodler",
            symbol="BTCUSDT",
            state="position",
            position=False,  # No position record → mismatch
        )

        from core.tasks import run_reconciliation

        result = run_reconciliation.apply()
        assert result.successful()
        summary = result.result
        assert summary["mismatches"] >= 1
        assert summary["repairs"] >= 1

    def test_publishes_ok_event_on_clean_state(self, _seed_state):
        """Clean state → publishes RECONCILIATION_OK event."""
        _seed_state(state="flat", position=False)

        from core.events import EventChannel, EventType

        with patch("core.events.get_publisher") as mock_get_pub:
            mock_pub = MagicMock()
            mock_get_pub.return_value = mock_pub

            from core.tasks import run_reconciliation

            run_reconciliation.apply()

            # Find the summary OK event
            ok_calls = [
                c
                for c in mock_pub.publish.call_args_list
                if c.args[1] == EventType.RECONCILIATION_OK
            ]
            assert len(ok_calls) >= 1

    def test_publishes_mismatch_event_on_inconsistency(self, _seed_state):
        """Mismatch → publishes RECONCILIATION_MISMATCH event."""
        _seed_state(
            strategy="smart_hodler",
            symbol="BTCUSDT",
            state="position",
            position=False,
        )

        from core.events import EventChannel, EventType

        with patch("core.events.get_publisher") as mock_get_pub:
            mock_pub = MagicMock()
            mock_get_pub.return_value = mock_pub

            from core.tasks import run_reconciliation

            run_reconciliation.apply()

            mismatch_calls = [
                c
                for c in mock_pub.publish.call_args_list
                if c.args[1] == EventType.RECONCILIATION_MISMATCH
            ]
            assert len(mismatch_calls) >= 1

    def test_publishes_repaired_event_on_repair(self, _seed_state):
        """Repair performed → publishes RECONCILIATION_REPAIRED event."""
        _seed_state(
            strategy="smart_hodler",
            symbol="BTCUSDT",
            state="position",
            position=False,
        )

        from core.events import EventChannel, EventType

        with patch("core.events.get_publisher") as mock_get_pub:
            mock_pub = MagicMock()
            mock_get_pub.return_value = mock_pub

            from core.tasks import run_reconciliation

            run_reconciliation.apply()

            repaired_calls = [
                c
                for c in mock_pub.publish.call_args_list
                if c.args[1] == EventType.RECONCILIATION_REPAIRED
            ]
            assert len(repaired_calls) >= 1


# ── Startup trigger ──────────────────────────────────────────────────────────


class TestStartupReconciliation:
    """Verify worker_ready signal triggers reconciliation."""

    def test_startup_enqueues_reconciliation(self):
        """worker_ready signal fires → run_reconciliation.delay() called."""
        with patch("core.tasks.run_reconciliation") as mock_task:
            from core.celery_app import _reconcile_on_startup

            _reconcile_on_startup(sender=None)
            mock_task.delay.assert_called_once()

    def test_startup_skips_when_disabled(self):
        """reconcile_on_startup=False → task not enqueued."""
        from core.config import get_settings

        settings = get_settings()
        original = settings.live_trading.reconcile_on_startup
        settings.live_trading.reconcile_on_startup = False

        try:
            with patch("core.tasks.run_reconciliation") as mock_task:
                from core.celery_app import _reconcile_on_startup

                _reconcile_on_startup(sender=None)
                mock_task.delay.assert_not_called()
        finally:
            settings.live_trading.reconcile_on_startup = original
