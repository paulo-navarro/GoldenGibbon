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
from db.models import PositionRecord, PortfolioSnapshot, StrategyStateRecord, TradeRecord, OrderRecord


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
        from core.config import get_settings
        from core.tasks import run_reconciliation

        settings = get_settings()
        original = settings.live_trading.enabled
        settings.live_trading.enabled = False

        try:
            result = run_reconciliation.apply()
            assert result.successful()
            summary = result.result
            assert isinstance(summary, dict)
            assert summary["mismatches"] == 0
            assert summary["repairs"] == 0
            assert isinstance(summary["details"], list)
        finally:
            settings.live_trading.enabled = original

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

    def test_disabled_via_config_skips_all_work(self):
        """When reconciliation.enabled=False, task returns immediately."""
        from unittest.mock import patch

        from core.config import ReconciliationConfig

        disabled_cfg = ReconciliationConfig(enabled=False)

        with patch("core.config.get_settings") as mock_settings:
            mock_settings.return_value.reconciliation = disabled_cfg
            from core.tasks import run_reconciliation

            result = run_reconciliation.apply()
            assert result.successful()
            summary = result.result
            assert summary["status"] == "disabled"
            assert summary["pairs_checked"] == 0

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


# ── Exchange reconciliation (task 4.8) ──────────────────────────────────────


class TestReconcileWithExchange:
    """Verify Check D (USDT balance) and Check E (position size) vs Binance."""

    def _make_executor(self, balances: dict, *, trades=None, ticker_prices=None) -> MagicMock:
        """Build a mock executor that returns given balances."""
        executor = MagicMock()
        executor.get_account_info.return_value = {
            "balances": balances,
            "can_trade": True,
        }
        executor.get_ticker_prices.return_value = ticker_prices or {}
        executor.get_my_trades.return_value = trades if trades is not None else []
        return executor

    def test_ok_when_balances_match(self, _seed_state):
        """Local USDT + position matches exchange → ok."""
        _seed_state(
            state="position",
            position=True,
            position_size=Decimal("0.01"),
            state_data={
                "run_id": "test",
                "usdt_balance": "5000",
                "equity": "5500",
                "total_pnl": "0",
                "cooldown_remaining": 0,
            },
        )

        executor = self._make_executor({
            "USDT": {"free": Decimal("5000"), "locked": Decimal("0")},
            "BTC": {"free": Decimal("0.01"), "locked": Decimal("0")},
        })

        from core.tasks import _reconcile_with_exchange

        with get_session() as session:
            result = _reconcile_with_exchange(session, executor, ["BTCUSDT"])

        assert result["status"] == "ok"
        usdt_check = next(c for c in result["checks"] if c["check"] == "exchange_usdt")
        assert usdt_check["result"] == "ok"
        pos_check = next(c for c in result["checks"] if c["check"] == "exchange_position_BTCUSDT")
        assert pos_check["result"] == "ok"

    def test_usdt_mismatch_detected(self, _seed_state):
        """Local USDT differs from exchange → warning."""
        _seed_state(
            state="flat",
            state_data={
                "run_id": "test",
                "usdt_balance": "10000",
                "equity": "10000",
                "total_pnl": "0",
                "cooldown_remaining": 0,
            },
        )

        executor = self._make_executor({
            "USDT": {"free": Decimal("8000"), "locked": Decimal("0")},
        })

        from core.tasks import _reconcile_with_exchange

        with get_session() as session:
            result = _reconcile_with_exchange(session, executor, ["BTCUSDT"])

        assert result["status"] == "mismatch"
        usdt_check = next(c for c in result["checks"] if c["check"] == "exchange_usdt")
        assert usdt_check["result"] == "warning"
        assert "10000" in usdt_check["detail"]
        assert "8000" in usdt_check["detail"]

    def test_position_mismatch_detected(self, _seed_state):
        """Local position size differs from exchange → auto-repaired."""
        _seed_state(
            state="position",
            position=True,
            position_size=Decimal("0.05"),
        )

        executor = self._make_executor({
            "USDT": {"free": Decimal("10000"), "locked": Decimal("0")},
            "BTC": {"free": Decimal("0.02"), "locked": Decimal("0")},
        })

        from core.tasks import _reconcile_with_exchange

        with get_session() as session:
            result = _reconcile_with_exchange(session, executor, ["BTCUSDT"])

        assert result["status"] == "mismatch"
        pos_check = next(c for c in result["checks"] if c["check"] == "exchange_position_BTCUSDT")
        assert pos_check["result"] == "repaired"
        assert "0.02" in pos_check["detail"]

    def test_no_position_no_exchange_balance_ok(self, _seed_state):
        """No local position + no exchange balance → ok."""
        _seed_state(
            state="flat",
            state_data={
                "run_id": "test",
                "usdt_balance": "1000",
                "equity": "1000",
                "total_pnl": "0",
                "cooldown_remaining": 0,
            },
        )

        executor = self._make_executor({
            "USDT": {"free": Decimal("1000"), "locked": Decimal("0")},
        })

        from core.tasks import _reconcile_with_exchange

        with get_session() as session:
            result = _reconcile_with_exchange(session, executor, ["BTCUSDT"])

        assert result["status"] == "ok"

    def test_api_error_returns_error_status(self):
        """Binance API failure → status=error, no crash."""
        executor = MagicMock()
        executor.get_account_info.side_effect = ConnectionError("timeout")

        from core.tasks import _reconcile_with_exchange

        with get_session() as session:
            result = _reconcile_with_exchange(session, executor, ["BTCUSDT"])

        assert result["status"] == "error"
        assert result["checks"][0]["result"] == "error"

    def test_multiple_symbols_checked(self, _seed_state):
        """All tracked symbols get a position check."""
        _seed_state(state="flat")

        executor = self._make_executor({
            "USDT": {"free": Decimal("10000"), "locked": Decimal("0")},
        })

        from core.tasks import _reconcile_with_exchange

        with get_session() as session:
            result = _reconcile_with_exchange(
                session, executor, ["BTCUSDT", "ETHUSDT"],
            )

        check_names = [c["check"] for c in result["checks"]]
        assert "exchange_position_BTCUSDT" in check_names
        assert "exchange_position_ETHUSDT" in check_names

    def test_stale_position_auto_repaired_when_exchange_zero(self, _seed_state):
        """Local position exists but exchange has zero balance → auto-repair with TradeRecord."""
        _seed_state(
            state="position",
            position=True,
            position_size=Decimal("436.2"),
            entry_price=Decimal("0.0329"),
        )

        executor = self._make_executor({
            "USDT": {"free": Decimal("10000"), "locked": Decimal("0")},
        })

        from core.tasks import _reconcile_with_exchange

        with get_session() as session:
            result = _reconcile_with_exchange(session, executor, ["BTCUSDT"])

        assert result["status"] == "mismatch"
        pos_check = next(c for c in result["checks"] if c["check"] == "exchange_position_BTCUSDT")
        assert pos_check["result"] == "repaired"
        assert len(result["repairs"]) >= 1
        assert "force-closed" in result["repairs"][0]

        with get_session() as session:
            from db.models import StrategyStateRecord, TradeRecord as TR
            pos = session.query(PositionRecord).filter_by(symbol="BTCUSDT").first()
            assert pos is None
            state = session.query(StrategyStateRecord).filter_by(
                symbol="BTCUSDT", strategy="smart_hodler",
            ).first()
            assert state.state == "flat"
            # Should have created an emergency TradeRecord
            trade = session.query(TR).filter_by(symbol="BTCUSDT").first()
            assert trade is not None
            assert trade.exit_reason == "reconciliation_force_close"

    def test_stale_position_evicts_worker_state(self, _seed_state):
        """Auto-repair evicts the in-memory worker state cache entry."""
        _seed_state(
            state="position",
            position=True,
            position_size=Decimal("1.0"),
        )

        from core.tasks import _reconcile_with_exchange, _worker_state

        _worker_state[("smart_hodler", "BTCUSDT")] = MagicMock()

        executor = self._make_executor({
            "USDT": {"free": Decimal("10000"), "locked": Decimal("0")},
        })

        with get_session() as session:
            _reconcile_with_exchange(session, executor, ["BTCUSDT"])

        assert ("smart_hodler", "BTCUSDT") not in _worker_state

    def test_exchange_dust_treated_as_zero(self, _seed_state):
        """Exchange balance within tolerance (dust) treated as zero → auto-repair."""
        _seed_state(
            state="position",
            position=True,
            position_size=Decimal("100.0"),
        )

        executor = self._make_executor({
            "USDT": {"free": Decimal("10000"), "locked": Decimal("0")},
            "BTC": {"free": Decimal("0.00000050"), "locked": Decimal("0")},
        })

        from core.tasks import _reconcile_with_exchange

        with get_session() as session:
            result = _reconcile_with_exchange(session, executor, ["BTCUSDT"])

        pos_check = next(c for c in result["checks"] if c["check"] == "exchange_position_BTCUSDT")
        assert pos_check["result"] == "repaired"

    def test_tolerance_within_threshold(self, _seed_state):
        """Small differences within tolerance → ok."""
        _seed_state(
            state="position",
            position=True,
            position_size=Decimal("0.01000000"),
            state_data={
                "run_id": "test",
                "usdt_balance": "5000.005",
                "equity": "5500",
                "total_pnl": "0",
                "cooldown_remaining": 0,
            },
        )

        executor = self._make_executor({
            "USDT": {"free": Decimal("5000"), "locked": Decimal("0")},
            "BTC": {"free": Decimal("0.01000050"), "locked": Decimal("0")},
        })

        from core.tasks import _reconcile_with_exchange

        with get_session() as session:
            result = _reconcile_with_exchange(session, executor, ["BTCUSDT"])

        assert result["status"] == "ok"

    def test_exchange_has_asset_no_local_position_critical(self, _seed_state):
        """Exchange has asset but NO local position + no trades → critical alert."""
        _seed_state(
            state="flat",
            state_data={
                "run_id": "test",
                "usdt_balance": "10000",
                "equity": "10000",
                "total_pnl": "0",
                "cooldown_remaining": 0,
            },
        )

        executor = self._make_executor(
            {
                "USDT": {"free": Decimal("10000"), "locked": Decimal("0")},
                "BTC": {"free": Decimal("847.90"), "locked": Decimal("0")},
            },
            trades=[],  # No trades → cannot reconstruct
        )

        from core.tasks import _reconcile_with_exchange

        with patch("core.events.get_publisher") as mock_get_pub:
            mock_pub = MagicMock()
            mock_get_pub.return_value = mock_pub

            with get_session() as session:
                result = _reconcile_with_exchange(session, executor, ["BTCUSDT"])

        assert result["status"] == "mismatch"
        pos_check = next(c for c in result["checks"] if c["check"] == "exchange_position_BTCUSDT")
        assert pos_check["result"] == "critical"
        assert "Could not reconstruct" in pos_check["detail"]
        assert len(result.get("repairs", [])) == 0

    def test_untracked_asset_reconstructed_from_trades(self, _seed_state):
        """Exchange has asset + trade history → position reconstructed."""
        _seed_state(
            state="flat",
            state_data={
                "run_id": "test",
                "usdt_balance": "10000",
                "equity": "10000",
                "total_pnl": "0",
                "cooldown_remaining": 0,
            },
        )

        # Mock trades that show net buy of 1.0 BTC at avg price 50000
        trades = [
            {
                "id": 1,
                "orderId": 100,
                "symbol": "BTCUSDT",
                "side": "BUY",
                "price": "50000",
                "qty": "1.0",
                "commission": "0.001",
                "commissionAsset": "BTC",
                "time": 1700000000000,
            }
        ]

        executor = self._make_executor(
            {
                "USDT": {"free": Decimal("10000"), "locked": Decimal("0")},
                "BTC": {"free": Decimal("1.0"), "locked": Decimal("0")},
            },
            trades=trades,
        )

        from core.tasks import _reconcile_with_exchange

        with get_session() as session:
            result = _reconcile_with_exchange(session, executor, ["BTCUSDT"])

        assert result["status"] == "mismatch"
        pos_check = next(c for c in result["checks"] if c["check"] == "exchange_position_BTCUSDT")
        assert pos_check["result"] == "repaired"
        assert "Reconstructed" in pos_check["detail"]
        assert len(result["repairs"]) >= 1
        assert "reconstructed" in result["repairs"][0]

        # Verify PositionRecord was created
        with get_session() as session:
            pos = session.query(PositionRecord).filter_by(symbol="BTCUSDT").first()
            assert pos is not None
            assert pos.size == Decimal("1.0")
            assert pos.entry_price == Decimal("50000")
            # Stops should use SmartHodler's hard_stop_pct (0.03), not hardcoded 5%/3%
            expected_hard = Decimal("50000") * (1 - Decimal("0.03"))
            assert pos.hard_stop_price == expected_hard
            assert pos.trailing_stop_price == expected_hard

    def test_size_mismatch_auto_repaired(self, _seed_state):
        """Local size=1.5, exchange=1.2 → PositionRecord.size adjusted to 1.2."""
        _seed_state(
            state="position",
            position=True,
            position_size=Decimal("1.5"),
            entry_price=Decimal("50000"),
        )

        executor = self._make_executor({
            "USDT": {"free": Decimal("5000"), "locked": Decimal("0")},
            "BTC": {"free": Decimal("1.2"), "locked": Decimal("0")},
        })

        from core.tasks import _reconcile_with_exchange

        with get_session() as session:
            result = _reconcile_with_exchange(session, executor, ["BTCUSDT"])

        assert result["status"] == "mismatch"
        pos_check = next(c for c in result["checks"] if c["check"] == "exchange_position_BTCUSDT")
        assert pos_check["result"] == "repaired"
        assert len(result["repairs"]) >= 1
        assert "adjusted" in result["repairs"][0]

        # Verify position size was adjusted
        with get_session() as session:
            pos = session.query(PositionRecord).filter_by(symbol="BTCUSDT").first()
            assert pos is not None
            assert pos.size == Decimal("1.2")

    def test_force_close_pnl_calculated(self, _seed_state):
        """Force-close uses ticker price for PnL calculation."""
        _seed_state(
            state="position",
            position=True,
            position_size=Decimal("1.0"),
            entry_price=Decimal("50000"),
        )

        executor = self._make_executor(
            {
                "USDT": {"free": Decimal("5000"), "locked": Decimal("0")},
            },
            ticker_prices={"BTCUSDT": Decimal("55000")},
        )

        from core.tasks import _reconcile_with_exchange

        with get_session() as session:
            result = _reconcile_with_exchange(session, executor, ["BTCUSDT"])

        # Verify TradeRecord has correct PnL
        with get_session() as session:
            trade = session.query(TradeRecord).filter_by(symbol="BTCUSDT").first()
            assert trade is not None
            assert trade.exit_reason == "reconciliation_force_close"
            assert trade.exit_price == Decimal("55000")
            # PnL = (55000 - 50000) * 1.0 = 5000
            assert trade.pnl_usdt == Decimal("5000")


# ── Phase 6.6.3: Margin debt for shorts (6.9.18) ────────────────────────────


class TestMarginDebtCheck:
    """Verify Check F: margin borrowed amount vs short position size."""

    def _make_executor(self, balances: dict, *, margin_account=None) -> MagicMock:
        executor = MagicMock()
        executor.get_account_info.return_value = {
            "balances": balances,
            "can_trade": True,
        }
        executor.get_ticker_prices.return_value = {}
        executor.get_my_trades.return_value = []
        if margin_account is not None:
            executor.get_margin_account.return_value = margin_account
        return executor

    def _seed_short_position(self, symbol="BTCUSDT", size=Decimal("0.05")):
        with get_session() as session:
            now = datetime.now(timezone.utc)
            session.add(
                StrategyStateRecord(
                    symbol=symbol,
                    strategy="bear_guard",
                    state="position",
                    consecutive_buy_candles=0,
                    state_data={
                        "run_id": "test",
                        "usdt_balance": "10000",
                        "equity": "10000",
                        "total_pnl": "0",
                        "cooldown_remaining": 0,
                    },
                )
            )
            session.add(
                PositionRecord(
                    symbol=symbol,
                    strategy="bear_guard",
                    side="short",
                    size=size,
                    entry_price=Decimal("50000"),
                    entry_time=now,
                    highest_close=Decimal("50000"),
                    trailing_stop_price=Decimal("52500"),
                    hard_stop_price=Decimal("55000"),
                    scale_in_count=0,
                    buy_signal_candles=0,
                )
            )

    def test_margin_debt_matches_short_position(self):
        self._seed_short_position(size=Decimal("0.05"))

        executor = self._make_executor(
            {"USDT": {"free": Decimal("10000"), "locked": Decimal("0")}},
            margin_account={"BTC": {"borrowed": Decimal("0.05"), "free": Decimal("0"), "locked": Decimal("0")}},
        )

        from core.tasks import _reconcile_with_exchange

        with get_session() as session:
            result = _reconcile_with_exchange(session, executor, ["BTCUSDT"])

        debt_check = next(c for c in result["checks"] if c["check"] == "margin_debt_BTCUSDT")
        assert debt_check["result"] == "ok"

    def test_margin_debt_mismatch_warns(self):
        self._seed_short_position(size=Decimal("0.05"))

        executor = self._make_executor(
            {"USDT": {"free": Decimal("10000"), "locked": Decimal("0")}},
            margin_account={"BTC": {"borrowed": Decimal("0.02"), "free": Decimal("0"), "locked": Decimal("0")}},
        )

        from core.tasks import _reconcile_with_exchange

        with get_session() as session:
            result = _reconcile_with_exchange(session, executor, ["BTCUSDT"])

        assert result["status"] == "mismatch"
        debt_check = next(c for c in result["checks"] if c["check"] == "margin_debt_BTCUSDT")
        assert debt_check["result"] == "warning"
        assert "0.05" in debt_check["detail"]
        assert "0.02" in debt_check["detail"]

    def test_margin_api_failure_falls_back_to_warning(self):
        self._seed_short_position(size=Decimal("0.05"))

        executor = self._make_executor(
            {"USDT": {"free": Decimal("10000"), "locked": Decimal("0")}},
        )
        executor.get_margin_account.side_effect = Exception("margin API down")

        from core.tasks import _reconcile_with_exchange

        with get_session() as session:
            result = _reconcile_with_exchange(session, executor, ["BTCUSDT"])

        debt_check = next(c for c in result["checks"] if c["check"] == "margin_debt_BTCUSDT")
        assert debt_check["result"] == "warning"


# ── Phase 6.4: Open Order Sync ───────────────────────────────────────────────


class TestSyncOpenOrders:
    """Verify _sync_open_orders: matches exchange orders to local records."""

    def test_known_order_gets_synced(self):
        """An open order matching a local OrderRecord → reconciled_at set, status=synced."""
        from core.tasks import _sync_open_orders

        # Pre-create a local order record
        with get_session() as session:
            session.add(OrderRecord(
                symbol="BTCUSDT",
                side="buy",
                order_type="limit",
                amount=1.5,
                price=50000,
                status="pending",
                exchange_order_id="12345",
                exchange_status="NEW",
                trading_mode="live",
                reconciliation_status="pending_sync",
            ))

        executor = MagicMock()
        executor.get_open_orders.return_value = [
            {
                "orderId": 12345,
                "symbol": "BTCUSDT",
                "side": "BUY",
                "type": "LIMIT",
                "status": "NEW",
                "origQty": "1.5",
                "price": "50000",
                "executedQty": "0",
            }
        ]

        with get_session() as session:
            result = _sync_open_orders(session, executor)

        assert result["status"] == "ok"
        assert result["orders_synced"] == 1
        assert result["orphans_found"] == 0

        # Verify the record was updated
        with get_session() as session:
            record = session.query(OrderRecord).filter_by(exchange_order_id="12345").first()
            assert record.reconciliation_status == "synced"
            assert record.reconciled_at is not None

    def test_orphan_order_creates_record(self):
        """An open order with no local match → creates orphan OrderRecord."""
        from core.tasks import _sync_open_orders

        executor = MagicMock()
        executor.get_open_orders.return_value = [
            {
                "orderId": 99999,
                "symbol": "ETHUSDT",
                "side": "SELL",
                "type": "LIMIT",
                "status": "NEW",
                "origQty": "10.0",
                "price": "3500",
                "executedQty": "0",
            }
        ]

        with get_session() as session:
            result = _sync_open_orders(session, executor)

        assert result["status"] == "ok"
        assert result["orphans_found"] == 1
        assert result["orders_synced"] == 0

        # Verify orphan record was created
        with get_session() as session:
            record = session.query(OrderRecord).filter_by(exchange_order_id="99999").first()
            assert record is not None
            assert record.reconciliation_status == "orphan"
            assert record.symbol == "ETHUSDT"
            assert record.side == "sell"
            assert record.filled_at is None

    def test_orphan_filled_order_has_filled_at(self):
        """An orphan order with status FILLED gets filled_at set."""
        from core.tasks import _sync_open_orders

        executor = MagicMock()
        executor.get_open_orders.return_value = [
            {
                "orderId": 88888,
                "symbol": "BTCUSDT",
                "side": "BUY",
                "type": "MARKET",
                "status": "FILLED",
                "origQty": "0.5",
                "price": "60000",
                "executedQty": "0.5",
            }
        ]

        with get_session() as session:
            result = _sync_open_orders(session, executor)

        assert result["orphans_found"] == 1

        with get_session() as session:
            record = session.query(OrderRecord).filter_by(exchange_order_id="88888").first()
            assert record is not None
            assert record.reconciliation_status == "orphan"
            assert record.filled_at is not None

    def test_api_error_returns_error_status(self):
        """Exchange API failure → graceful error return."""
        from core.tasks import _sync_open_orders

        executor = MagicMock()
        executor.get_open_orders.side_effect = ConnectionError("timeout")

        with get_session() as session:
            result = _sync_open_orders(session, executor)

        assert result["status"] == "error"

    def test_empty_open_orders(self):
        """No open orders on exchange → 0 synced, 0 orphans."""
        from core.tasks import _sync_open_orders

        executor = MagicMock()
        executor.get_open_orders.return_value = []

        with get_session() as session:
            result = _sync_open_orders(session, executor)

        assert result == {"status": "ok", "orders_synced": 0, "orphans_found": 0}


class TestCheckPendingStopOrders:
    """Verify _check_pending_stop_orders: status checks for pending stops."""

    def test_filled_stop_detected(self):
        """Pending stop that's FILLED on exchange → marked for fill recovery."""
        from core.tasks import _check_pending_stop_orders

        with get_session() as session:
            session.add(OrderRecord(
                symbol="BTCUSDT",
                side="sell",
                order_type="stop_limit",
                amount=0.5,
                status="pending",
                intent="stop_loss",
                exchange_order_id="55555",
                exchange_status="NEW",
                trading_mode="live",
                reconciliation_status="pending_sync",
            ))

        executor = MagicMock()
        executor.get_order_status.return_value = {
            "orderId": 55555,
            "status": "FILLED",
            "executedQty": "0.5",
        }

        with get_session() as session:
            result = _check_pending_stop_orders(session, executor)

        assert result["status"] == "ok"
        assert result["filled_stops"] == 1
        assert result["cancelled_stops"] == 0

        with get_session() as session:
            record = session.query(OrderRecord).filter_by(exchange_order_id="55555").first()
            assert record.exchange_status == "FILLED"
            assert record.reconciliation_status == "pending_sync"

    def test_cancelled_stop_detected(self):
        """Pending stop that's CANCELED on exchange → marked cancelled locally."""
        from core.tasks import _check_pending_stop_orders

        with get_session() as session:
            session.add(OrderRecord(
                symbol="ETHUSDT",
                side="sell",
                order_type="stop_limit",
                amount=5.0,
                status="pending",
                intent="stop_loss",
                exchange_order_id="66666",
                exchange_status="NEW",
                trading_mode="live",
                reconciliation_status="pending_sync",
            ))

        executor = MagicMock()
        executor.get_order_status.return_value = {
            "orderId": 66666,
            "status": "CANCELED",
        }

        with get_session() as session:
            result = _check_pending_stop_orders(session, executor)

        assert result["status"] == "ok"
        assert result["cancelled_stops"] == 1

        with get_session() as session:
            record = session.query(OrderRecord).filter_by(exchange_order_id="66666").first()
            assert record.status == "cancelled"
            assert record.exchange_status == "CANCELED"
            assert record.reconciliation_status == "synced"

    def test_order_not_found_on_exchange(self):
        """Stop order that doesn't exist on exchange → marked expired."""
        from core.execution.binance import BinanceAPIError
        from core.tasks import _check_pending_stop_orders

        with get_session() as session:
            session.add(OrderRecord(
                symbol="BTCUSDT",
                side="sell",
                order_type="stop_limit",
                amount=0.3,
                status="pending",
                intent="stop_loss",
                exchange_order_id="77777",
                exchange_status="NEW",
                trading_mode="live",
                reconciliation_status="pending_sync",
            ))

        executor = MagicMock()
        executor.get_order_status.side_effect = BinanceAPIError(-2013, "Order does not exist.")

        with get_session() as session:
            result = _check_pending_stop_orders(session, executor)

        assert result["status"] == "ok"
        assert result["expired_stops"] == 1

        with get_session() as session:
            record = session.query(OrderRecord).filter_by(exchange_order_id="77777").first()
            assert record.status == "cancelled"
            assert record.exchange_status == "EXPIRED"

    def test_no_pending_stops(self):
        """No pending stop orders → zero counts."""
        from core.tasks import _check_pending_stop_orders

        executor = MagicMock()

        with get_session() as session:
            result = _check_pending_stop_orders(session, executor)

        assert result == {
            "status": "ok",
            "pending_stops_checked": 0,
            "filled_stops": 0,
            "cancelled_stops": 0,
            "expired_stops": 0,
        }


class TestCancelOrphanStopOrders:
    """Verify _cancel_orphan_stop_orders: cleans up stops without positions."""

    def test_stop_with_no_position_gets_cancelled(self):
        """Stop order on exchange for symbol with no position → cancel."""
        from core.tasks import _cancel_orphan_stop_orders

        executor = MagicMock()
        executor.cancel_order.return_value = {"orderId": 88888, "status": "CANCELED"}

        open_orders = [
            {
                "orderId": 88888,
                "symbol": "BTCUSDT",
                "type": "STOP_LOSS_LIMIT",
                "side": "SELL",
            }
        ]

        with get_session() as session:
            result = _cancel_orphan_stop_orders(session, executor, open_orders)

        assert result["status"] == "ok"
        assert result["orphan_stops_cancelled"] == 1
        executor.cancel_order.assert_called_once_with("BTCUSDT", 88888)

    def test_stop_with_position_not_cancelled(self, _seed_state):
        """Stop order for symbol with active position → leave alone."""
        from core.tasks import _cancel_orphan_stop_orders

        _seed_state(state="position", position=True)

        executor = MagicMock()

        open_orders = [
            {
                "orderId": 88888,
                "symbol": "BTCUSDT",
                "type": "STOP_LOSS_LIMIT",
                "side": "SELL",
            }
        ]

        with get_session() as session:
            result = _cancel_orphan_stop_orders(session, executor, open_orders)

        assert result["status"] == "ok"
        assert result["orphan_stops_cancelled"] == 0
        executor.cancel_order.assert_not_called()

    def test_non_stop_orders_ignored(self):
        """Regular limit orders are not checked for orphan stops."""
        from core.tasks import _cancel_orphan_stop_orders

        executor = MagicMock()

        open_orders = [
            {
                "orderId": 11111,
                "symbol": "BTCUSDT",
                "type": "LIMIT",
                "side": "BUY",
            }
        ]

        with get_session() as session:
            result = _cancel_orphan_stop_orders(session, executor, open_orders)

        assert result["stop_orders_checked"] == 0
        assert result["orphan_stops_cancelled"] == 0
        executor.cancel_order.assert_not_called()


# ── Phase 6.5: Fill Recovery ─────────────────────────────────────────────────


class TestRecoverPendingOrders:
    """Verify _recover_pending_orders: recovers fills for stale pending orders."""

    def _make_stale_order(self, *, intent="open_long", exchange_order_id="12345", symbol="BTCUSDT"):
        """Insert a stale pending order (>2 min old)."""
        from datetime import timedelta

        with get_session() as session:
            order = OrderRecord(
                symbol=symbol,
                side="buy",
                order_type="market",
                amount=1.0,
                status="pending",
                intent=intent,
                exchange_order_id=exchange_order_id,
                exchange_status="NEW",
                trading_mode="live",
                reconciliation_status="pending_sync",
            )
            session.add(order)
            session.flush()
            # Backdate created_at to make it stale
            order.created_at = datetime.now(timezone.utc) - timedelta(minutes=5)

    def _seed_strategy_state(self, *, symbol="BTCUSDT", strategy="smart_hodler", state="flat", balance="10000"):
        """Insert a StrategyStateRecord."""
        from db.models import StrategyStateRecord

        with get_session() as session:
            session.add(StrategyStateRecord(
                symbol=symbol,
                strategy=strategy,
                state=state,
                consecutive_buy_candles=0,
                state_data={"usdt_balance": balance, "equity": balance, "total_pnl": "0"},
            ))

    def _seed_position(self, *, symbol="BTCUSDT", strategy="smart_hodler", size="1.0", entry_price="50000"):
        """Insert a PositionRecord."""
        with get_session() as session:
            session.add(PositionRecord(
                symbol=symbol,
                strategy=strategy,
                side="long",
                size=Decimal(size),
                entry_price=Decimal(entry_price),
                entry_time=datetime.now(timezone.utc) - __import__("datetime").timedelta(hours=2),
                highest_close=Decimal(entry_price),
                trailing_stop_price=Decimal(entry_price) * Decimal("0.95"),
                hard_stop_price=Decimal(entry_price) * Decimal("0.97"),
                scale_in_count=0,
                buy_signal_candles=0,
            ))

    def test_filled_open_long_creates_position(self):
        """Pending open_long + FILLED on exchange → PositionRecord created."""
        from core.tasks import _recover_pending_orders

        self._seed_strategy_state(balance="10000")
        self._make_stale_order(intent="open_long", exchange_order_id="11111")

        executor = MagicMock()
        executor.get_order_status.return_value = {
            "orderId": 11111,
            "status": "FILLED",
            "executedQty": "1.0",
            "cumulativeQuoteQty": "50000.0",
            "price": "50000",
        }

        with get_session() as session:
            result = _recover_pending_orders(session, executor)

        assert result["status"] == "ok"
        assert result["recovered"] == 1

        # Verify position was created
        with get_session() as session:
            pos = session.query(PositionRecord).filter_by(symbol="BTCUSDT").first()
            assert pos is not None
            assert pos.size == Decimal("1.0")
            assert pos.entry_price == Decimal("50000")

        # Verify order was updated
        with get_session() as session:
            order = session.query(OrderRecord).filter_by(exchange_order_id="11111").first()
            assert order.status == "filled"
            assert order.reconciliation_status == "recovered"

    def test_filled_close_long_creates_trade(self):
        """Pending close_long + FILLED → PositionRecord deleted, TradeRecord created."""
        from db.models import StrategyStateRecord, TradeRecord as TR
        from core.tasks import _recover_pending_orders

        self._seed_strategy_state(state="position", balance="0")
        self._seed_position(size="1.0", entry_price="50000")
        self._make_stale_order(intent="close_long", exchange_order_id="22222")

        executor = MagicMock()
        executor.get_order_status.return_value = {
            "orderId": 22222,
            "status": "FILLED",
            "executedQty": "1.0",
            "cumulativeQuoteQty": "55000.0",
            "price": "55000",
        }

        with get_session() as session:
            result = _recover_pending_orders(session, executor)

        assert result["recovered"] == 1

        # Position should be deleted
        with get_session() as session:
            pos = session.query(PositionRecord).filter_by(symbol="BTCUSDT").first()
            assert pos is None

        # Trade should be created
        with get_session() as session:
            trade = session.query(TR).filter_by(symbol="BTCUSDT").first()
            assert trade is not None
            assert trade.pnl_usdt > 0  # Profitable close

        # Strategy state should be flat
        with get_session() as session:
            state = session.query(StrategyStateRecord).filter_by(symbol="BTCUSDT").first()
            assert state.state == "flat"

    def test_filled_stop_loss_closes_position(self):
        """Pending stop_loss + FILLED → same as close."""
        from db.models import TradeRecord as TR
        from core.tasks import _recover_pending_orders

        self._seed_strategy_state(state="position", balance="0")
        self._seed_position(size="1.0", entry_price="50000")
        self._make_stale_order(intent="stop_loss", exchange_order_id="33333")

        executor = MagicMock()
        executor.get_order_status.return_value = {
            "orderId": 33333,
            "status": "FILLED",
            "executedQty": "1.0",
            "cumulativeQuoteQty": "48000.0",
            "price": "48000",
        }

        with get_session() as session:
            result = _recover_pending_orders(session, executor)

        assert result["recovered"] == 1

        with get_session() as session:
            pos = session.query(PositionRecord).filter_by(symbol="BTCUSDT").first()
            assert pos is None
            trade = session.query(TR).filter_by(symbol="BTCUSDT").first()
            assert trade is not None
            assert trade.pnl_usdt < 0  # Loss (stop hit)
            assert trade.exit_reason == "trailing_stop"

    def test_cancelled_order_updated(self):
        """CANCELED on exchange → OrderRecord updated, no position change."""
        from core.tasks import _recover_pending_orders

        self._seed_strategy_state()
        self._make_stale_order(intent="open_long", exchange_order_id="44444")

        executor = MagicMock()
        executor.get_order_status.return_value = {
            "orderId": 44444,
            "status": "CANCELED",
        }

        with get_session() as session:
            result = _recover_pending_orders(session, executor)

        assert result["cancelled"] == 1
        assert result["recovered"] == 0

        with get_session() as session:
            order = session.query(OrderRecord).filter_by(exchange_order_id="44444").first()
            assert order.status == "cancelled"
            assert order.reconciliation_status == "synced"

    def test_still_open_order_skipped(self):
        """NEW on exchange → no changes, counted as still_open."""
        from core.tasks import _recover_pending_orders

        self._seed_strategy_state()
        self._make_stale_order(intent="open_long", exchange_order_id="55555")

        executor = MagicMock()
        executor.get_order_status.return_value = {
            "orderId": 55555,
            "status": "NEW",
        }

        with get_session() as session:
            result = _recover_pending_orders(session, executor)

        assert result["still_open"] == 1
        assert result["recovered"] == 0

        # Order should remain pending
        with get_session() as session:
            order = session.query(OrderRecord).filter_by(exchange_order_id="55555").first()
            assert order.status == "pending"

    def test_idempotent_no_duplicate_trade(self):
        """If TradeRecord already exists for a close, skip creating another."""
        from db.models import TradeRecord as TR
        from core.tasks import _recover_pending_orders

        self._seed_strategy_state(state="position", balance="0")
        self._seed_position(size="1.0", entry_price="50000")
        self._make_stale_order(intent="close_long", exchange_order_id="66666")

        # Pre-create a trade that looks like a recent close
        with get_session() as session:
            session.add(TR(
                symbol="BTCUSDT",
                strategy="smart_hodler",
                side="long",
                entry_price=Decimal("50000"),
                exit_price=Decimal("55000"),
                size=Decimal("1.0"),
                entry_time=datetime.now(timezone.utc) - __import__("datetime").timedelta(hours=2),
                exit_time=datetime.now(timezone.utc),
                pnl_usdt=Decimal("5000"),
                pnl_percent=Decimal("10"),
                duration_minutes=120,
                exit_reason="manual",
                exchange_order_id="66666",
                trading_mode="live",
            ))

        executor = MagicMock()
        executor.get_order_status.return_value = {
            "orderId": 66666,
            "status": "FILLED",
            "executedQty": "1.0",
            "cumulativeQuoteQty": "55000.0",
            "price": "55000",
        }

        with get_session() as session:
            result = _recover_pending_orders(session, executor)

        assert result["recovered"] == 1

        # Should NOT create a duplicate trade
        with get_session() as session:
            trades = session.query(TR).filter_by(symbol="BTCUSDT").all()
            assert len(trades) == 1  # Only the pre-existing one


class TestRecoverPendingWithoutId:
    """Verify _recover_pending_orders_without_id: matches orders by criteria."""

    def _make_orphan_order(self, *, intent="open_long", symbol="BTCUSDT", amount=1.0):
        """Insert an orphan pending order (no exchange_order_id, >5 min old)."""
        from datetime import timedelta

        with get_session() as session:
            order = OrderRecord(
                symbol=symbol,
                side="buy",
                order_type="market",
                amount=amount,
                status="pending",
                intent=intent,
                exchange_order_id=None,
                trading_mode="live",
                reconciliation_status="pending_sync",
            )
            session.add(order)
            session.flush()
            order.created_at = datetime.now(timezone.utc) - timedelta(minutes=10)

    def test_matched_by_symbol_side_qty_time(self):
        """Finds matching order on exchange → sets exchange_order_id, recovers."""
        from db.models import StrategyStateRecord
        from core.tasks import _recover_pending_orders_without_id

        self._make_orphan_order(intent="open_long", amount=1.0)

        # Seed strategy state
        with get_session() as session:
            session.add(StrategyStateRecord(
                symbol="BTCUSDT",
                strategy="smart_hodler",
                state="flat",
                consecutive_buy_candles=0,
                state_data={"usdt_balance": "10000", "equity": "10000", "total_pnl": "0"},
            ))

        # Mock exchange returns a matching order
        order_time_ms = int((datetime.now(timezone.utc) - __import__("datetime").timedelta(minutes=10)).timestamp() * 1000)
        executor = MagicMock()
        executor.get_all_orders.return_value = [
            {
                "orderId": 77777,
                "symbol": "BTCUSDT",
                "side": "BUY",
                "origQty": "1.0",
                "status": "FILLED",
                "executedQty": "1.0",
                "cumulativeQuoteQty": "50000.0",
                "price": "50000",
                "time": order_time_ms,
            }
        ]

        with get_session() as session:
            result = _recover_pending_orders_without_id(session, executor)

        assert result["status"] == "ok"
        assert result["matched"] == 1
        assert result["lost"] == 0

        # Should have set exchange_order_id
        with get_session() as session:
            order = session.query(OrderRecord).filter_by(symbol="BTCUSDT").first()
            assert order.exchange_order_id == "77777"

    def test_no_match_marks_lost(self):
        """No matching orders on exchange → marked as lost."""
        from core.tasks import _recover_pending_orders_without_id

        self._make_orphan_order(intent="open_long", amount=1.0)

        executor = MagicMock()
        executor.get_all_orders.return_value = []  # No orders found

        with get_session() as session:
            result = _recover_pending_orders_without_id(session, executor)

        assert result["status"] == "ok"
        assert result["matched"] == 0
        assert result["lost"] == 1

        with get_session() as session:
            order = session.query(OrderRecord).filter_by(symbol="BTCUSDT").first()
            assert order.status == "cancelled"
            assert order.reconciliation_status == "lost"

    def test_skips_recent_orders(self):
        """Orders < 5 min old are not processed."""
        from core.tasks import _recover_pending_orders_without_id

        # Create a recent order (not stale)
        with get_session() as session:
            session.add(OrderRecord(
                symbol="BTCUSDT",
                side="buy",
                order_type="market",
                amount=1.0,
                status="pending",
                intent="open_long",
                exchange_order_id=None,
                trading_mode="live",
                reconciliation_status="pending_sync",
            ))

        executor = MagicMock()

        with get_session() as session:
            result = _recover_pending_orders_without_id(session, executor)

        assert result["orders_checked"] == 0
        executor.get_all_orders.assert_not_called()


# ── Phase 6.8.3: Telegram alerts for reconciliation events ──────────────────


class TestReconciliationTelegramAlerts:
    """Verify Telegram alerts fire for order sync / fill recovery / orphan events (6.8.3)."""

    def _run_with_mocked_results(
        self,
        *,
        recovery_result=None,
        lost_result=None,
        sync_result=None,
        stop_result=None,
        orphan_stop_result=None,
        exchange_result=None,
        alerting_enabled=True,
        alert_min_severity="warning",
    ):
        """
        Run run_reconciliation with live_trading.enabled=True but all
        exchange sub-functions mocked to return preset results.
        """
        from core.config import AlertingConfig, get_settings

        settings = get_settings()
        alert_cfg = AlertingConfig(
            enabled=alerting_enabled,
            alert_on_reconciliation=True,
            alert_min_severity=alert_min_severity,
        )
        orig_alerting = settings.alerting
        orig_live = settings.live_trading.enabled
        settings.alerting = alert_cfg
        settings.live_trading.enabled = True

        defaults = {
            "recovery": recovery_result or {"status": "ok", "recovered": 0, "cancelled": 0, "still_open": 0, "orders_checked": 0},
            "lost": lost_result or {"status": "ok", "lost": 0, "matched": 0, "orders_checked": 0},
            "sync": sync_result or {"status": "ok", "orphans_found": 0, "orders_synced": 0},
            "stop": stop_result or {"status": "ok", "filled_stops": 0, "cancelled_stops": 0, "expired_stops": 0, "pending_stops_checked": 0},
            "orphan_stop": orphan_stop_result or {"status": "ok", "orphan_stops_cancelled": 0, "stop_orders_checked": 0},
            "exchange": exchange_result or {"status": "ok", "checks": [], "repairs": []},
        }

        mock_executor = MagicMock()
        mock_executor.get_open_orders.return_value = []

        try:
            with (
                patch("core.tasks._reconciliation._recover_pending_orders", return_value=defaults["recovery"]),
                patch("core.tasks._reconciliation._recover_pending_orders_without_id", return_value=defaults["lost"]),
                patch("core.tasks._reconciliation._sync_open_orders", return_value=defaults["sync"]),
                patch("core.tasks._reconciliation._check_pending_stop_orders", return_value=defaults["stop"]),
                patch("core.tasks._reconciliation._cancel_orphan_stop_orders", return_value=defaults["orphan_stop"]),
                patch("core.tasks._reconciliation._reconcile_with_exchange", return_value=defaults["exchange"]),
                patch("core.execution.binance.BinanceExecutor.from_settings", return_value=mock_executor),
                patch("core.alerting.requests.post") as mock_post,
                patch("core.alerting._alerter", None),
            ):
                mock_post.return_value = MagicMock(ok=True)

                from core.alerting import init_alerter
                if alerting_enabled:
                    init_alerter("123:ABC", "456")
                else:
                    init_alerter("", "")

                from core.tasks import run_reconciliation
                run_reconciliation.apply()

                return mock_post
        finally:
            settings.alerting = orig_alerting
            settings.live_trading.enabled = orig_live
            from core.alerting import reset_alerter
            reset_alerter()

    def test_orphan_orders_trigger_warning_alert(self):
        """Orphan orders detected → WARNING Telegram alert."""
        mock_post = self._run_with_mocked_results(
            sync_result={"status": "ok", "orphans_found": 2, "orders_synced": 3},
        )
        mock_post.assert_called()
        texts = [c[1]["json"]["text"] for c in mock_post.call_args_list]
        assert any("orphan order" in t for t in texts)

    def test_filled_stops_trigger_critical_alert(self):
        """Stop orders found filled → CRITICAL Telegram alert."""
        mock_post = self._run_with_mocked_results(
            stop_result={"status": "ok", "filled_stops": 1, "cancelled_stops": 0, "expired_stops": 0, "pending_stops_checked": 1},
        )
        mock_post.assert_called()
        texts = [c[1]["json"]["text"] for c in mock_post.call_args_list]
        assert any("stop order" in t.lower() for t in texts)
        assert any("CRITICAL" in t for t in texts)

    def test_orphan_stops_cancelled_trigger_warning(self):
        """Orphan stops cancelled → WARNING Telegram alert.

        The orchestrator only calls _cancel_orphan_stop_orders when
        executor.get_open_orders() returns a non-empty list, so we
        override the mock executor's return value for this test.
        """
        from core.config import AlertingConfig, get_settings

        settings = get_settings()
        alert_cfg = AlertingConfig(
            enabled=True,
            alert_on_reconciliation=True,
            alert_min_severity="warning",
        )
        orig_alerting = settings.alerting
        orig_live = settings.live_trading.enabled
        settings.alerting = alert_cfg
        settings.live_trading.enabled = True

        mock_executor = MagicMock()
        mock_executor.get_open_orders.return_value = [
            {"orderId": 1, "symbol": "BTCUSDT", "type": "STOP_LOSS_LIMIT", "side": "SELL"},
        ]

        orphan_stop_ret = {"status": "ok", "orphan_stops_cancelled": 3, "stop_orders_checked": 5}

        try:
            with (
                patch("core.tasks._reconciliation._recover_pending_orders", return_value={"status": "ok", "recovered": 0, "cancelled": 0, "still_open": 0, "orders_checked": 0}),
                patch("core.tasks._reconciliation._recover_pending_orders_without_id", return_value={"status": "ok", "lost": 0, "matched": 0, "orders_checked": 0}),
                patch("core.tasks._reconciliation._sync_open_orders", return_value={"status": "ok", "orphans_found": 0, "orders_synced": 0}),
                patch("core.tasks._reconciliation._check_pending_stop_orders", return_value={"status": "ok", "filled_stops": 0, "cancelled_stops": 0, "expired_stops": 0, "pending_stops_checked": 0}),
                patch("core.tasks._reconciliation._cancel_orphan_stop_orders", return_value=orphan_stop_ret),
                patch("core.tasks._reconciliation._reconcile_with_exchange", return_value={"status": "ok", "checks": [], "repairs": []}),
                patch("core.execution.binance.BinanceExecutor.from_settings", return_value=mock_executor),
                patch("core.alerting.requests.post") as mock_post,
                patch("core.alerting._alerter", None),
            ):
                mock_post.return_value = MagicMock(ok=True)
                from core.alerting import init_alerter
                init_alerter("123:ABC", "456")

                from core.tasks import run_reconciliation
                run_reconciliation.apply()

                mock_post.assert_called()
                texts = [c[1]["json"]["text"] for c in mock_post.call_args_list]
                assert any("orphan stop" in t.lower() for t in texts)
        finally:
            settings.alerting = orig_alerting
            settings.live_trading.enabled = orig_live
            from core.alerting import reset_alerter
            reset_alerter()

    def test_lost_orders_trigger_warning(self):
        """Lost orders → WARNING Telegram alert."""
        mock_post = self._run_with_mocked_results(
            lost_result={"status": "ok", "lost": 2, "matched": 0, "orders_checked": 2},
        )
        mock_post.assert_called()
        texts = [c[1]["json"]["text"] for c in mock_post.call_args_list]
        assert any("LOST" in t for t in texts)

    def test_fill_recovery_triggers_critical_alert(self):
        """Fill recovery → CRITICAL Telegram alert."""
        mock_post = self._run_with_mocked_results(
            recovery_result={"status": "ok", "recovered": 1, "cancelled": 0, "still_open": 0, "orders_checked": 1},
        )
        mock_post.assert_called()
        texts = [c[1]["json"]["text"] for c in mock_post.call_args_list]
        assert any("CRITICAL" in t and "fill_recovered" in t for t in texts)

    def test_no_events_no_alerts(self):
        """Clean reconciliation → no Telegram alerts."""
        mock_post = self._run_with_mocked_results()
        mock_post.assert_not_called()

    def test_min_severity_critical_filters_warnings(self):
        """With min_severity=critical, WARNING events don't trigger alerts."""
        mock_post = self._run_with_mocked_results(
            sync_result={"status": "ok", "orphans_found": 2, "orders_synced": 0},
            alert_min_severity="critical",
        )
        mock_post.assert_not_called()

    def test_alerting_disabled_no_alerts(self):
        """Alerting disabled → no Telegram calls even with events."""
        mock_post = self._run_with_mocked_results(
            recovery_result={"status": "ok", "recovered": 3, "cancelled": 0, "still_open": 0, "orders_checked": 3},
            alerting_enabled=False,
        )
        mock_post.assert_not_called()
