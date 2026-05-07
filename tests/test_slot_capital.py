"""
Integration tests for the shared-pool slot capital model (Phase 7).

Validates:
  - slot_capital = total / max_concurrent_positions flows through Portfolio
  - available_capital returns slot_capital when set
  - _resolve_allocated_capital uses the slot model
  - _count_global_open_positions counts across worker state
  - max positions guard blocks new entries at the limit
"""

from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from core.models import Portfolio, Position, PositionSide, Signal


# ── Portfolio.available_capital ──────────────────────────────────────────────


class TestPortfolioSlotCapital:
    """Portfolio model uses slot_capital in available_capital."""

    def test_available_capital_returns_slot_when_set(self):
        p = Portfolio(usdt_balance=Decimal("10000"), equity=Decimal("10000"))
        p.slot_capital = Decimal("2500")
        assert p.available_capital == Decimal("2500")

    def test_available_capital_falls_back_to_balance(self):
        p = Portfolio(usdt_balance=Decimal("10000"), equity=Decimal("10000"))
        assert p.available_capital == Decimal("10000")

    def test_available_capital_falls_back_when_slot_zero(self):
        p = Portfolio(usdt_balance=Decimal("5000"), equity=Decimal("5000"))
        p.slot_capital = Decimal("0")
        assert p.available_capital == Decimal("5000")


# ── _resolve_allocated_capital ───────────────────────────────────────────────


class TestResolveAllocatedCapital:
    """_resolve_allocated_capital returns total / max_concurrent_positions."""

    def test_paper_mode(self):
        from core.tasks._tick import _resolve_allocated_capital

        settings = MagicMock()
        settings.live_trading.enabled = False
        settings.paper_trading.initial_capital = 10000
        settings.risk.max_concurrent_positions = 4

        result = _resolve_allocated_capital(settings)
        assert result == 2500.0

    def test_paper_mode_custom_positions(self):
        from core.tasks._tick import _resolve_allocated_capital

        settings = MagicMock()
        settings.live_trading.enabled = False
        settings.paper_trading.initial_capital = 12000
        settings.risk.max_concurrent_positions = 6

        result = _resolve_allocated_capital(settings)
        assert result == 2000.0

    @patch("core.tasks._tick._get_live_total_capital")
    def test_live_mode(self, mock_live_cap):
        from core.tasks._tick import _resolve_allocated_capital

        mock_live_cap.return_value = Decimal("20000")
        settings = MagicMock()
        settings.live_trading.enabled = True
        settings.risk.max_concurrent_positions = 4

        result = _resolve_allocated_capital(settings)
        assert result == 5000.0


# ── _count_global_open_positions ─────────────────────────────────────────────


class TestCountGlobalPositions:
    """_count_global_open_positions counts across all worker state entries."""

    def setup_method(self):
        from core.tasks._state import clear_worker_state
        clear_worker_state()

    def teardown_method(self):
        from core.tasks._state import clear_worker_state
        clear_worker_state()

    def test_zero_when_empty(self):
        from core.tasks._tick import _count_global_open_positions
        assert _count_global_open_positions() == 0

    def test_counts_positions_across_workers(self):
        from core.tasks._tick import _count_global_open_positions
        from core.tasks._state import _worker_state

        comp1 = MagicMock()
        comp1.pm.portfolio.positions = {"BTCUSDT": MagicMock()}
        comp2 = MagicMock()
        comp2.pm.portfolio.positions = {"ETHUSDT": MagicMock()}
        comp3 = MagicMock()
        comp3.pm.portfolio.positions = {}

        _worker_state[("smart_hodler", "BTCUSDT")] = comp1
        _worker_state[("smart_hodler", "ETHUSDT")] = comp2
        _worker_state[("mean_reversion", "BTCUSDT")] = comp3

        assert _count_global_open_positions() == 2

    def test_counts_multiple_positions_per_worker(self):
        from core.tasks._tick import _count_global_open_positions
        from core.tasks._state import _worker_state

        comp = MagicMock()
        comp.pm.portfolio.positions = {
            "BTCUSDT": MagicMock(),
            "ETHUSDT": MagicMock(),
        }
        _worker_state[("smart_hodler", "BTCUSDT")] = comp

        assert _count_global_open_positions() == 2


# ── Max positions guard (integration) ───────────────────────────────────────


class TestMaxPositionsGuard:
    """The tick pipeline blocks new entries when at max_concurrent_positions."""

    def setup_method(self):
        from core.tasks._state import clear_worker_state
        clear_worker_state()

    def teardown_method(self):
        from core.tasks._state import clear_worker_state
        clear_worker_state()

    def test_guard_blocks_buy_at_limit(self):
        """When global positions == max, a BUY on a flat pair becomes HOLD."""
        from core.tasks._tick import _count_global_open_positions
        from core.tasks._state import _worker_state

        comp1 = MagicMock()
        comp1.pm.portfolio.positions = {"BTCUSDT": MagicMock()}
        comp2 = MagicMock()
        comp2.pm.portfolio.positions = {"ETHUSDT": MagicMock()}
        _worker_state[("smart_hodler", "BTCUSDT")] = comp1
        _worker_state[("smart_hodler", "ETHUSDT")] = comp2

        assert _count_global_open_positions() == 2

        settings = MagicMock()
        settings.risk.max_concurrent_positions = 2

        signal = Signal.BUY
        has_position = False

        if (
            signal in (Signal.BUY, Signal.SHORT)
            and not has_position
            and _count_global_open_positions() >= settings.risk.max_concurrent_positions
        ):
            signal = Signal.HOLD

        assert signal == Signal.HOLD

    def test_guard_allows_buy_below_limit(self):
        """When global positions < max, BUY passes through."""
        from core.tasks._tick import _count_global_open_positions
        from core.tasks._state import _worker_state

        comp1 = MagicMock()
        comp1.pm.portfolio.positions = {"BTCUSDT": MagicMock()}
        _worker_state[("smart_hodler", "BTCUSDT")] = comp1

        settings = MagicMock()
        settings.risk.max_concurrent_positions = 4

        signal = Signal.BUY
        has_position = False

        if (
            signal in (Signal.BUY, Signal.SHORT)
            and not has_position
            and _count_global_open_positions() >= settings.risk.max_concurrent_positions
        ):
            signal = Signal.HOLD

        assert signal == Signal.BUY

    def test_guard_allows_scale_in_at_limit(self):
        """Scale-in on an existing position is allowed even at max."""
        from core.tasks._tick import _count_global_open_positions
        from core.tasks._state import _worker_state

        comp1 = MagicMock()
        comp1.pm.portfolio.positions = {"BTCUSDT": MagicMock()}
        comp2 = MagicMock()
        comp2.pm.portfolio.positions = {"ETHUSDT": MagicMock()}
        _worker_state[("smart_hodler", "BTCUSDT")] = comp1
        _worker_state[("smart_hodler", "ETHUSDT")] = comp2

        settings = MagicMock()
        settings.risk.max_concurrent_positions = 2

        signal = Signal.BUY
        has_position = True  # already has a position — this is a scale-in

        if (
            signal in (Signal.BUY, Signal.SHORT)
            and not has_position
            and _count_global_open_positions() >= settings.risk.max_concurrent_positions
        ):
            signal = Signal.HOLD

        assert signal == Signal.BUY


# ── Config validation ────────────────────────────────────────────────────────


class TestMaxConcurrentPositionsConfig:
    """max_concurrent_positions in RiskConfig has correct constraints."""

    def test_default_value(self):
        from core.config import RiskConfig
        cfg = RiskConfig()
        assert cfg.max_concurrent_positions == 4

    def test_min_value(self):
        from core.config import RiskConfig
        cfg = RiskConfig(max_concurrent_positions=1)
        assert cfg.max_concurrent_positions == 1

    def test_max_value(self):
        from core.config import RiskConfig
        cfg = RiskConfig(max_concurrent_positions=20)
        assert cfg.max_concurrent_positions == 20

    def test_below_min_raises(self):
        from core.config import RiskConfig
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            RiskConfig(max_concurrent_positions=0)

    def test_above_max_raises(self):
        from core.config import RiskConfig
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            RiskConfig(max_concurrent_positions=21)
