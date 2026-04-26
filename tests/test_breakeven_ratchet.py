"""
Tests for break-even ratchet on the hard stop (phase 3, task 3.6).

Verifies:
  - Below breakeven trigger → hard stop unchanged
  - At/above breakeven trigger → hard stop moves to entry price
  - At/above lock-in trigger → hard stop moves to entry × (1 + lockin_stop_pct)
  - Hard stop never decreases (ratchet only goes up)
  - Ratchet disabled → hard stop unchanged
  - Hard stop hit after ratchet fires with correct exit reason / price
  - Works for both Smart Hodler and Mean Reversion
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import pandas as pd
import pytest

from core.models import (
    ExitReason,
    MarketData,
    Portfolio,
    Position,
    RiskAction,
)
from core.risk import RiskEngine


# ── Helpers ──────────────────────────────────────────────────────────────────

N = 50


def _series(value, length=N):
    return pd.Series([value] * length, dtype=float)


def _candles(close=50000.0, length=N):
    dates = pd.date_range("2024-01-01", periods=length, freq="15min")
    return pd.DataFrame(
        {
            "open": [close] * length,
            "high": [close + 10] * length,
            "low": [close - 10] * length,
            "close": [close] * length,
            "volume": [100.0] * length,
        },
        index=dates,
    )


def _market_data(close=50000.0, atr=500.0) -> MarketData:
    return MarketData(
        symbol="BTCUSDT",
        timeframe="15m",
        candles=_candles(close),
        indicators={"atr": _series(atr)},
    )


def _portfolio(
    entry_price=Decimal("50000"),
    hard_stop_price=Decimal("48500"),
    size=Decimal("0.1"),
    highest_close=None,
    trailing_stop_price=Decimal("0"),
) -> Portfolio:
    pos = Position(
        symbol="BTCUSDT",
        size=size,
        entry_price=entry_price,
        entry_time=datetime(2026, 2, 17, 10, 0),
        highest_close=highest_close or entry_price,
        trailing_stop_price=trailing_stop_price,
        hard_stop_price=hard_stop_price,
        scale_in_count=0,
    )
    return Portfolio(
        usdt_balance=Decimal("5000"),
        positions={"BTCUSDT": pos},
        equity=Decimal("5000") + size * entry_price,
        open_trades_count=1,
    )


# ── Engine factories ─────────────────────────────────────────────────────────

SH_CONFIG = {
    "entry_initial_pct": 0.50,
    "entry_scale_1_pct": 0.25,
    "entry_scale_2_pct": 0.25,
    "entry_scale_1_candles": 8,
    "entry_scale_2_candles": 16,
    "exit_momentum_fade_pct": 0.50,
    "hard_stop_pct": 0.03,
    "trailing_stop_atr_multiplier": 2.0,
    "breakeven_ratchet_enabled": True,
    "breakeven_trigger_pct": 0.02,
    "lockin_trigger_pct": 0.04,
    "lockin_stop_pct": 0.01,
}

MR_CONFIG = {
    "entry_pct": 0.75,
    "exit_partial_pct": 0.50,
    "hard_stop_pct": 0.02,
    "breakeven_ratchet_enabled": True,
    "breakeven_trigger_pct": 0.02,
    "lockin_trigger_pct": 0.04,
    "lockin_stop_pct": 0.01,
}

RISK_CONFIG = {
    "max_position_size_pct": 1.0,
    "trailing_stop_atr_multiplier": 2.0,
}


def _sh_engine(**overrides) -> RiskEngine:
    cfg = {**SH_CONFIG, **overrides}
    return RiskEngine("smart_hodler", cfg, RISK_CONFIG)


def _mr_engine(**overrides) -> RiskEngine:
    cfg = {**MR_CONFIG, **overrides}
    return RiskEngine("mean_reversion", cfg, RISK_CONFIG)


# ── Below breakeven trigger ─────────────────────────────────────────────────


class TestBelowTrigger:

    def test_sh_1pct_profit_no_ratchet(self):
        """SH: +1% profit is below 2% trigger → hard stop unchanged."""
        engine = _sh_engine()
        # entry 50000, hard stop at 48500 (3%), close at 50500 (+1%)
        port = _portfolio(entry_price=Decimal("50000"), hard_stop_price=Decimal("48500"))
        result = engine.check_stops(_market_data(close=50500.0), port)
        assert result.stop_hit is False
        assert result.hard_stop_price == Decimal("48500")

    def test_mr_1pct_profit_no_ratchet(self):
        """MR: +1% profit is below 2% trigger → hard stop unchanged."""
        engine = _mr_engine()
        port = _portfolio(entry_price=Decimal("50000"), hard_stop_price=Decimal("49000"))
        result = engine.check_stops(_market_data(close=50500.0), port)
        assert result.stop_hit is False
        assert result.hard_stop_price == Decimal("49000")


# ── Break-even trigger ──────────────────────────────────────────────────────


class TestBreakevenTrigger:

    def test_sh_2pct_profit_moves_to_breakeven(self):
        """SH: +2% profit → hard stop moves to entry price (break-even)."""
        engine = _sh_engine()
        # entry 50000, +2% = 51000
        port = _portfolio(entry_price=Decimal("50000"), hard_stop_price=Decimal("48500"))
        result = engine.check_stops(_market_data(close=51000.0), port)
        assert result.stop_hit is False
        assert result.hard_stop_price == Decimal("50000")

    def test_sh_3pct_profit_still_breakeven(self):
        """SH: +3% profit (between 2% and 4%) → break-even, not lock-in."""
        engine = _sh_engine()
        port = _portfolio(entry_price=Decimal("50000"), hard_stop_price=Decimal("48500"))
        result = engine.check_stops(_market_data(close=51500.0), port)
        assert result.hard_stop_price == Decimal("50000")

    def test_mr_2pct_profit_moves_to_breakeven(self):
        """MR: +2% profit → hard stop moves to entry price."""
        engine = _mr_engine()
        port = _portfolio(entry_price=Decimal("50000"), hard_stop_price=Decimal("49000"))
        result = engine.check_stops(_market_data(close=51000.0), port)
        assert result.hard_stop_price == Decimal("50000")


# ── Lock-in trigger ─────────────────────────────────────────────────────────


class TestLockinTrigger:

    def test_sh_4pct_profit_locks_in(self):
        """SH: +4% profit → hard stop moves to entry × 1.01."""
        engine = _sh_engine()
        # entry 50000, +4% = 52000, lock-in stop = 50000 * 1.01 = 50500
        port = _portfolio(entry_price=Decimal("50000"), hard_stop_price=Decimal("48500"))
        result = engine.check_stops(_market_data(close=52000.0), port)
        assert result.hard_stop_price == Decimal("50500")

    def test_sh_6pct_profit_still_lockin(self):
        """SH: +6% → same lock-in level (no further ratchet)."""
        engine = _sh_engine()
        port = _portfolio(entry_price=Decimal("50000"), hard_stop_price=Decimal("48500"))
        result = engine.check_stops(_market_data(close=53000.0), port)
        assert result.hard_stop_price == Decimal("50500")

    def test_mr_4pct_profit_locks_in(self):
        """MR: +4% profit → hard stop at entry × 1.01."""
        engine = _mr_engine()
        port = _portfolio(entry_price=Decimal("50000"), hard_stop_price=Decimal("49000"))
        result = engine.check_stops(_market_data(close=52000.0), port)
        assert result.hard_stop_price == Decimal("50500")


# ── Ratchet never lowers ────────────────────────────────────────────────────


class TestRatchetNeverLowers:

    def test_breakeven_then_profit_recedes(self):
        """Once at break-even, profit dropping to +1.5% keeps break-even."""
        engine = _sh_engine()
        # Simulate: hard stop already at break-even (50000), profit recedes
        port = _portfolio(
            entry_price=Decimal("50000"),
            hard_stop_price=Decimal("50000"),  # already ratcheted
        )
        result = engine.check_stops(_market_data(close=50750.0), port)
        assert result.hard_stop_price == Decimal("50000")

    def test_lockin_then_profit_recedes_to_3pct(self):
        """Once at lock-in (50500), profit dropping to +3% keeps lock-in."""
        engine = _sh_engine()
        port = _portfolio(
            entry_price=Decimal("50000"),
            hard_stop_price=Decimal("50500"),  # already ratcheted to lock-in
        )
        # +3% = 51500, breakeven trigger would give 50000 which is < 50500
        result = engine.check_stops(_market_data(close=51500.0), port)
        assert result.hard_stop_price == Decimal("50500")


# ── Ratchet disabled ────────────────────────────────────────────────────────


class TestRatchetDisabled:

    def test_sh_ratchet_disabled(self):
        """SH: breakeven_ratchet_enabled=False → hard stop unchanged."""
        engine = _sh_engine(breakeven_ratchet_enabled=False)
        port = _portfolio(entry_price=Decimal("50000"), hard_stop_price=Decimal("48500"))
        result = engine.check_stops(_market_data(close=52000.0), port)
        assert result.hard_stop_price == Decimal("48500")

    def test_mr_ratchet_disabled(self):
        """MR: breakeven_ratchet_enabled=False → hard stop unchanged."""
        engine = _mr_engine(breakeven_ratchet_enabled=False)
        port = _portfolio(entry_price=Decimal("50000"), hard_stop_price=Decimal("49000"))
        result = engine.check_stops(_market_data(close=52000.0), port)
        assert result.hard_stop_price == Decimal("49000")


# ── Hard stop hit after ratchet ─────────────────────────────────────────────


class TestHardStopAfterRatchet:

    def test_hit_at_breakeven_level(self):
        """Position ratcheted to break-even, then price drops below → CLOSE."""
        engine = _sh_engine()
        # Hard stop already at break-even (50000), close drops to 49900
        port = _portfolio(
            entry_price=Decimal("50000"),
            hard_stop_price=Decimal("50000"),
        )
        result = engine.check_stops(_market_data(close=49900.0), port)
        assert result.stop_hit is True
        assert result.decision.action == RiskAction.CLOSE
        assert result.decision.exit_reason == ExitReason.HARD_STOP

    def test_hit_at_lockin_level(self):
        """Position ratcheted to lock-in (50500), close drops below → CLOSE."""
        engine = _sh_engine()
        port = _portfolio(
            entry_price=Decimal("50000"),
            hard_stop_price=Decimal("50500"),
        )
        result = engine.check_stops(_market_data(close=50400.0), port)
        assert result.stop_hit is True
        assert result.decision.exit_reason == ExitReason.HARD_STOP
        assert result.hard_stop_price == Decimal("50500")
