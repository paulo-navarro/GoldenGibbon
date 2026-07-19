"""
Tests for the exchange balance sync task (phase 6).

Verifies:
  - sync_exchange_balances publishes BALANCE_UPDATED and EQUITY_UPDATED events
  - Auto-repairs USDT balance drift in StrategyStateRecord
  - Skips when live_trading is not enabled
  - Handles Binance API failures gracefully
  - Recovery sync corrects usdt_balance from exchange
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from db import get_session
from db.models import PositionRecord, StrategyStateRecord


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
def _seed(request):
    """Insert strategy state records and optionally positions."""

    def _create(
        *,
        usdt_balance: str = "10000",
        position: bool = False,
        position_size: Decimal = Decimal("0.01"),
        entry_price: Decimal = Decimal("50000"),
        extra_states: list[dict] | None = None,
    ) -> None:
        with get_session() as session:
            session.add(
                StrategyStateRecord(
                    symbol="BTCUSDT",
                    strategy="smart_hodler",
                    state="position" if position else "flat",
                    consecutive_buy_candles=0,
                    state_data={
                        "run_id": "test_run",
                        "usdt_balance": usdt_balance,
                        "equity": usdt_balance,
                        "total_pnl": "0",
                        "cooldown_remaining": 0,
                    },
                )
            )
            if position:
                session.add(
                    PositionRecord(
                        symbol="BTCUSDT",
                        strategy="smart_hodler",
                        side="long",
                        size=position_size,
                        entry_price=entry_price,
                        entry_time=datetime.now(timezone.utc),
                        highest_close=entry_price,
                        trailing_stop_price=entry_price * Decimal("0.95"),
                        hard_stop_price=entry_price * Decimal("0.97"),
                        scale_in_count=0,
                        buy_signal_candles=0,
                    )
                )
            for st in (extra_states or []):
                session.add(
                    StrategyStateRecord(
                        symbol=st["symbol"],
                        strategy=st["strategy"],
                        state="flat",
                        consecutive_buy_candles=0,
                        state_data={
                            "run_id": "test_run",
                            "usdt_balance": st["usdt_balance"],
                            "equity": st["usdt_balance"],
                            "total_pnl": "0",
                            "cooldown_remaining": 0,
                        },
                    )
                )

    return _create


def _mock_executor(usdt_free: Decimal, assets: dict | None = None, prices: dict | None = None):
    """Build a mock BinanceExecutor with given balances and prices."""
    balances = {"USDT": {"free": usdt_free, "locked": Decimal("0")}}
    for asset, qty in (assets or {}).items():
        balances[asset] = {"free": qty, "locked": Decimal("0")}

    executor = MagicMock()
    executor.get_account_info.return_value = {
        "balances": balances,
        "can_trade": True,
    }
    executor.get_ticker_prices.return_value = prices or {}
    return executor


# ── sync_exchange_balances task ──────────────────────────────────────────────


class TestSyncExchangeBalances:
    """Verify the periodic balance sync Celery task."""

    def test_skips_when_not_live(self):
        """Paper mode → skipped."""
        from core.config import get_settings
        from core.tasks import sync_exchange_balances

        settings = get_settings()
        settings.live_trading.enabled = False

        try:
            result = sync_exchange_balances.apply()
            assert result.successful()
            assert result.result["status"] == "skipped"
        finally:
            settings.live_trading.enabled = False

    @patch("core.execution.binance.BinanceExecutor.from_settings")
    def test_publishes_events(self, mock_from_settings, _seed):
        """Live mode → publishes BALANCE_UPDATED and EQUITY_UPDATED."""
        from core.config import get_settings
        from core.events import EventType

        settings = get_settings()
        settings.live_trading.enabled = True

        _seed(usdt_balance="10000")
        mock_from_settings.return_value = _mock_executor(Decimal("10000"))

        try:
            with patch("core.events.get_publisher") as mock_get_pub:
                mock_pub = MagicMock()
                mock_get_pub.return_value = mock_pub

                from core.tasks import sync_exchange_balances

                result = sync_exchange_balances.apply()

            assert result.successful()
            assert result.result["status"] == "ok"
            assert result.result["exchange_usdt"] == "10000"

            event_types = [c.args[1] for c in mock_pub.publish.call_args_list]
            assert EventType.BALANCE_UPDATED in event_types

            publish_model_types = [c.args[1] for c in mock_pub.publish_model.call_args_list]
            assert EventType.EQUITY_UPDATED in publish_model_types
        finally:
            settings.live_trading.enabled = False

    @patch("core.execution.binance.BinanceExecutor.from_settings")
    def test_auto_repairs_drift(self, mock_from_settings, _seed):
        """USDT drift beyond tolerance → auto-repairs StrategyStateRecord."""
        from core.config import get_settings

        settings = get_settings()
        settings.live_trading.enabled = True

        _seed(usdt_balance="10000")
        mock_from_settings.return_value = _mock_executor(Decimal("9500"))

        try:
            from core.tasks import sync_exchange_balances

            result = sync_exchange_balances.apply()

            assert result.result["repaired"] is True
            assert result.result["drift"] == "-500"

            with get_session() as session:
                state = session.query(StrategyStateRecord).filter_by(
                    strategy="smart_hodler", symbol="BTCUSDT",
                ).first()
                new_bal = Decimal(state.state_data["usdt_balance"])
                assert new_bal == Decimal("9500")
        finally:
            settings.live_trading.enabled = False

    @patch("core.execution.binance.BinanceExecutor.from_settings")
    def test_proportional_drift_distribution(self, mock_from_settings, _seed):
        """Drift is distributed proportionally across strategy states."""
        from core.config import get_settings

        settings = get_settings()
        settings.live_trading.enabled = True

        _seed(
            usdt_balance="6000",
            extra_states=[
                {"symbol": "ETHUSDT", "strategy": "smart_hodler", "usdt_balance": "4000"},
            ],
        )
        mock_from_settings.return_value = _mock_executor(Decimal("9000"))

        try:
            from core.tasks import sync_exchange_balances

            result = sync_exchange_balances.apply()

            assert result.result["repaired"] is True

            with get_session() as session:
                btc_state = session.query(StrategyStateRecord).filter_by(
                    strategy="smart_hodler", symbol="BTCUSDT",
                ).first()
                eth_state = session.query(StrategyStateRecord).filter_by(
                    strategy="smart_hodler", symbol="ETHUSDT",
                ).first()
                btc_bal = Decimal(btc_state.state_data["usdt_balance"])
                eth_bal = Decimal(eth_state.state_data["usdt_balance"])
                # 6000/10000 * (-1000) = -600 → 5400
                assert btc_bal == Decimal("5400")
                # 4000/10000 * (-1000) = -400 → 3600
                assert eth_bal == Decimal("3600")
        finally:
            settings.live_trading.enabled = False

    @patch("core.execution.binance.BinanceExecutor.from_settings")
    def test_no_repair_within_tolerance(self, mock_from_settings, _seed):
        """Small drift within tolerance → no repair."""
        from core.config import get_settings

        settings = get_settings()
        settings.live_trading.enabled = True

        _seed(usdt_balance="10000")
        mock_from_settings.return_value = _mock_executor(Decimal("10000.005"))

        try:
            from core.tasks import sync_exchange_balances

            result = sync_exchange_balances.apply()

            assert result.result["repaired"] is False
        finally:
            settings.live_trading.enabled = False

    @patch("core.execution.binance.BinanceExecutor.from_settings")
    def test_includes_position_value_in_equity(self, mock_from_settings, _seed):
        """Equity = USDT balance + positions at live prices."""
        from core.config import get_settings

        settings = get_settings()
        settings.live_trading.enabled = True

        _seed(
            usdt_balance="5000",
            position=True,
            position_size=Decimal("0.1"),
            entry_price=Decimal("50000"),
        )
        mock_from_settings.return_value = _mock_executor(
            usdt_free=Decimal("5000"),
            assets={"BTC": Decimal("0.1")},
            prices={"BTCUSDT": Decimal("55000")},
        )

        try:
            from core.tasks import sync_exchange_balances

            result = sync_exchange_balances.apply()

            assert Decimal(result.result["exchange_usdt"]) == Decimal("5000")
            assert Decimal(result.result["positions_value"]) == Decimal("5500")
            assert Decimal(result.result["equity"]) == Decimal("10500")
        finally:
            settings.live_trading.enabled = False

    @patch("core.execution.binance.BinanceExecutor.from_settings")
    def test_persists_account_level_snapshot(self, mock_from_settings, _seed):
        """Task 9.11: each sync persists one account-level equity snapshot."""
        from core.config import get_settings
        from core.tasks import ACCOUNT_RUN_ID, sync_exchange_balances
        from db.models import PortfolioSnapshot

        settings = get_settings()
        settings.live_trading.enabled = True

        _seed(
            usdt_balance="5000",
            position=True,
            position_size=Decimal("0.1"),
            entry_price=Decimal("50000"),
        )
        mock_from_settings.return_value = _mock_executor(
            usdt_free=Decimal("5000"),
            assets={"BTC": Decimal("0.1")},
            prices={"BTCUSDT": Decimal("55000")},
        )

        try:
            result = sync_exchange_balances.apply()
            assert result.result["status"] == "ok"

            with get_session() as session:
                snaps = (
                    session.query(PortfolioSnapshot)
                    .filter_by(run_id=ACCOUNT_RUN_ID)
                    .all()
                )
                assert len(snaps) == 1
                snap = snaps[0]
                assert snap.trading_mode == "live"
                assert snap.total_equity == Decimal("10500")
                assert snap.usdt_balance == Decimal("5000")
                assert snap.positions_value == Decimal("5500")
                assert snap.open_positions_count == 1
        finally:
            settings.live_trading.enabled = False

    @patch("core.execution.binance.BinanceExecutor.from_settings")
    def test_api_failure_returns_error(self, mock_from_settings):
        """Binance API failure → error status, no crash."""
        from core.config import get_settings

        settings = get_settings()
        settings.live_trading.enabled = True

        executor = MagicMock()
        executor.get_account_info.side_effect = ConnectionError("timeout")
        mock_from_settings.return_value = executor

        try:
            from core.tasks import sync_exchange_balances

            result = sync_exchange_balances.apply()

            assert result.successful()
            assert result.result["status"] == "error"
        finally:
            settings.live_trading.enabled = False


# ── Recovery sync ────────────────────────────────────────────────────────────


class TestRecoverySyncFromExchange:
    """Verify _sync_balance_from_exchange corrects PM balance on recovery."""

    @patch("core.execution.binance.BinanceExecutor.from_settings")
    def test_corrects_balance_from_exchange(self, mock_from_settings, _seed):
        """Drifted DB balance is corrected to exchange proportional share."""
        _seed(usdt_balance="10000")
        mock_from_settings.return_value = _mock_executor(Decimal("9000"))

        from core.portfolio import PortfolioManager
        from core.tasks._state import _sync_balance_from_exchange

        pm = PortfolioManager(initial_capital=Decimal("10000"))
        pm._portfolio.usdt_balance = Decimal("10000")

        _sync_balance_from_exchange("smart_hodler", "BTCUSDT", pm)

        assert pm._portfolio.usdt_balance == Decimal("9000")

    @patch("core.execution.binance.BinanceExecutor.from_settings")
    def test_no_correction_within_tolerance(self, mock_from_settings, _seed):
        """Drift within tolerance → balance unchanged."""
        _seed(usdt_balance="10000")
        mock_from_settings.return_value = _mock_executor(Decimal("10000.005"))

        from core.portfolio import PortfolioManager
        from core.tasks._state import _sync_balance_from_exchange

        pm = PortfolioManager(initial_capital=Decimal("10000"))
        pm._portfolio.usdt_balance = Decimal("10000")

        _sync_balance_from_exchange("smart_hodler", "BTCUSDT", pm)

        assert pm._portfolio.usdt_balance == Decimal("10000")

    @patch("core.execution.binance.BinanceExecutor.from_settings")
    def test_api_failure_no_crash(self, mock_from_settings, _seed):
        """Binance error → no crash, balance unchanged."""
        _seed(usdt_balance="10000")
        executor = MagicMock()
        executor.get_account_info.side_effect = ConnectionError("timeout")
        mock_from_settings.return_value = executor

        from core.portfolio import PortfolioManager
        from core.tasks._state import _sync_balance_from_exchange

        pm = PortfolioManager(initial_capital=Decimal("10000"))
        pm._portfolio.usdt_balance = Decimal("10000")

        # Should not raise — exception is propagated to caller (_recover_state)
        with pytest.raises(ConnectionError):
            _sync_balance_from_exchange("smart_hodler", "BTCUSDT", pm)


# ── Account-level kill switch (task 9.10) ────────────────────────────────────


def _seed_account_peak(equity: str) -> None:
    """Insert an account-level snapshot establishing the equity peak."""
    from core.tasks import ACCOUNT_RUN_ID
    from db.models import PortfolioSnapshot

    with get_session() as session:
        session.add(
            PortfolioSnapshot(
                run_id=ACCOUNT_RUN_ID,
                timestamp=datetime.now(timezone.utc),
                usdt_balance=Decimal(equity),
                positions_value=Decimal("0"),
                total_equity=Decimal(equity),
                total_pnl=Decimal("0"),
                open_positions_count=0,
                trading_mode="live",
            )
        )


class TestAccountKillSwitch:
    """Drawdown vs the persisted account peak halts every strategy."""

    def _sync(self):
        from core.tasks import sync_exchange_balances

        return sync_exchange_balances.apply().result

    @patch("core.execution.binance.BinanceExecutor.from_settings")
    def test_breach_triggers_all_strategies(self, mock_from_settings, _seed):
        from core.config import get_settings

        settings = get_settings()
        settings.live_trading.enabled = True

        _seed(usdt_balance="50")
        _seed_account_peak("100")
        mock_from_settings.return_value = _mock_executor(Decimal("50"))  # 50% dd

        try:
            result = self._sync()
            assert result["account_kill_switch"] is True

            with get_session() as session:
                state = session.query(StrategyStateRecord).filter_by(
                    strategy="smart_hodler", symbol="BTCUSDT",
                ).first()
                assert state.state_data["kill_switch_triggered"] is True
                assert "Account drawdown" in state.state_data["kill_switch_reason"]
                assert state.state_data["kill_switch_trading_mode"] == "live"
        finally:
            settings.live_trading.enabled = False

    @patch("core.execution.binance.BinanceExecutor.from_settings")
    def test_latched_does_not_retrigger(self, mock_from_settings, _seed):
        from core.config import get_settings

        settings = get_settings()
        settings.live_trading.enabled = True

        _seed(usdt_balance="50")
        _seed_account_peak("100")
        mock_from_settings.return_value = _mock_executor(Decimal("50"))

        try:
            assert self._sync()["account_kill_switch"] is True
            # Second run: already latched everywhere → no re-trigger/alert
            assert self._sync()["account_kill_switch"] is False
        finally:
            settings.live_trading.enabled = False

    @patch("core.execution.binance.BinanceExecutor.from_settings")
    def test_small_drawdown_does_not_trigger(self, mock_from_settings, _seed):
        from core.config import get_settings

        settings = get_settings()
        settings.live_trading.enabled = True

        _seed(usdt_balance="95")
        _seed_account_peak("100")
        mock_from_settings.return_value = _mock_executor(Decimal("95"))  # 5% dd

        try:
            result = self._sync()
            assert result["account_kill_switch"] is False

            with get_session() as session:
                state = session.query(StrategyStateRecord).filter_by(
                    strategy="smart_hodler", symbol="BTCUSDT",
                ).first()
                assert not state.state_data.get("kill_switch_triggered", False)
        finally:
            settings.live_trading.enabled = False

    @patch("core.execution.binance.BinanceExecutor.from_settings")
    def test_zero_threshold_disables(self, mock_from_settings, _seed):
        from core.config import get_settings

        settings = get_settings()
        settings.live_trading.enabled = True
        prev = settings.live_trading.account_kill_switch_drawdown
        settings.live_trading.account_kill_switch_drawdown = 0.0

        _seed(usdt_balance="50")
        _seed_account_peak("100")
        mock_from_settings.return_value = _mock_executor(Decimal("50"))

        try:
            result = self._sync()
            assert result["account_kill_switch"] is False
        finally:
            settings.live_trading.account_kill_switch_drawdown = prev
            settings.live_trading.enabled = False
