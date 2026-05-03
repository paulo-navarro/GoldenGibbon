"""
Risk engine – position sizing, stop-loss checks, and signal validation.

Sits between strategy ``decide()`` and the portfolio / executor layer.
Translates raw ``Signal`` values into concrete ``RiskDecision`` objects
that carry the authorised action, computed size, and initial stop levels.

The engine is assembled from focused mixins:
  - StopCheckMixin  → hard/trailing/time stop logic
  - EvaluationMixin → signal dispatch and open/scale-in/short evaluation
  - SizingMixin     → position sizing, daily limits, exposure guards
"""

from datetime import date
from decimal import Decimal
from typing import Any, Dict, Optional

import structlog

from core.risk._evaluation import EvaluationMixin
from core.risk._sizing import SizingMixin
from core.risk._stops import StopCheckMixin

logger = structlog.get_logger(__name__)


class RiskEngine(StopCheckMixin, EvaluationMixin, SizingMixin):
    """
    Evaluates strategy signals and produces sized, validated risk decisions.

    Responsibilities:
      - Position sizing (strategy-specific: Smart Hodler scaled entries,
        Mean Reversion flat 75 %, Bear Guard flat 50 %).
      - Scale-in eligibility & consecutive BUY-candle tracking.
      - Stop-check framework (hard, trailing, time, break-even ratchet).
      - Exposure caps (``max_position_size_pct``).
    """

    # ── Construction ─────────────────────────────────────────────────

    def __init__(
        self,
        strategy_name: str,
        strategy_config: Dict[str, Any],
        risk_config: Optional[Dict[str, Any]] = None,
        *,
        shorts_enabled: bool = False,
    ) -> None:
        """
        Args:
            strategy_name: ``"smart_hodler"``, ``"mean_reversion"``, or ``"bear_guard"``.
            strategy_config: Strategy-specific section from strategies.yaml.
            risk_config: Global risk section from settings.yaml.
                         Falls back to sensible defaults when None.
            shorts_enabled: Master kill switch for short positions.
        """
        self._strategy_name = strategy_name
        self._strategy_config = strategy_config
        self._risk_config = risk_config or {}
        self._shorts_enabled = shorts_enabled

        # ── Resolved sizing params ───────────────────────────────────
        if strategy_name == "smart_hodler":
            self._initial_pct = Decimal(
                str(strategy_config.get("entry_initial_pct", 0.50))
            )
            self._scale_1_pct = Decimal(
                str(strategy_config.get("entry_scale_1_pct", 0.25))
            )
            self._scale_2_pct = Decimal(
                str(strategy_config.get("entry_scale_2_pct", 0.25))
            )
            self._scale_1_candles = int(
                strategy_config.get("entry_scale_1_candles", 8)
            )
            self._scale_2_candles = int(
                strategy_config.get("entry_scale_2_candles", 16)
            )
            self._sell_half_fraction = Decimal(
                str(strategy_config.get("exit_momentum_fade_pct", 0.50))
            )
        elif strategy_name == "mean_reversion":
            self._initial_pct = Decimal(
                str(strategy_config.get("entry_pct", 0.75))
            )
            self._sell_half_fraction = Decimal(
                str(strategy_config.get("exit_partial_pct", 0.50))
            )
        elif strategy_name == "bear_guard":
            self._initial_pct = Decimal(
                str(strategy_config.get("position_size_pct", 0.50))
            )
            self._sell_half_fraction = Decimal(
                str(strategy_config.get("exit_partial_pct", 0.50))
            )
        else:
            raise ValueError(f"Unknown strategy: {strategy_name}")

        # Hard-stop percentage
        self._hard_stop_pct = Decimal(
            str(strategy_config.get("hard_stop_pct", 0.03))
        )

        # Trailing stop toggle
        self._trailing_enabled = bool(
            strategy_config.get("trailing_stop_enabled", True)
        )

        # Trailing-stop ATR multiplier
        self._trailing_atr_mult = Decimal(
            str(
                strategy_config.get(
                    "trailing_stop_atr_multiplier",
                    self._risk_config.get("trailing_stop_atr_multiplier", 2.0),
                )
            )
        )

        # Max position size as fraction of equity (global cap)
        self._max_position_pct = Decimal(
            str(self._risk_config.get("max_position_size_pct", 1.0))
        )

        # Max daily new entries; None = unlimited
        _max_daily = self._risk_config.get("max_trades_per_day")
        self._max_daily_trades: Optional[int] = (
            int(_max_daily) if _max_daily is not None else None
        )

        # Per-trade absolute notional cap in USDT; None = no cap
        _max_trade = self._risk_config.get("max_trade_size_usdt")
        self._max_trade_size_usdt: Optional[Decimal] = (
            Decimal(str(_max_trade)) if _max_trade is not None else None
        )

        # Per-symbol absolute exposure cap in USDT; None = no cap
        _max_sym = self._risk_config.get("max_symbol_exposure_usdt")
        self._max_symbol_exposure_usdt: Optional[Decimal] = (
            Decimal(str(_max_sym)) if _max_sym is not None else None
        )

        # Daily open counter: {UTC date: count}
        self._daily_opens: Dict[date, int] = {}

        # Cooldown candles after a hard-stop exit
        self._cooldown_candles = int(
            strategy_config.get("cooldown_candles", 16)
        )

        # Time stop (Mean Reversion only)
        self._time_stop_candles = int(
            strategy_config.get("time_stop_candles", 16)
        )
        self._time_stop_cooldown = int(
            strategy_config.get("time_stop_cooldown_candles", 4)
        )
        self._time_stop_skip_profitable = bool(
            strategy_config.get("time_stop_skip_profitable", True)
        )

        # Break-even ratchet
        self._ratchet_enabled = bool(
            strategy_config.get("breakeven_ratchet_enabled", True)
        )
        self._breakeven_trigger = Decimal(
            str(strategy_config.get("breakeven_trigger_pct", 0.02))
        )
        self._lockin_trigger = Decimal(
            str(strategy_config.get("lockin_trigger_pct", 0.04))
        )
        self._lockin_stop_pct = Decimal(
            str(strategy_config.get("lockin_stop_pct", 0.01))
        )

        # Primary timeframe in minutes (for candle counting)
        tf_str = strategy_config.get("timeframe_primary", "15m")
        self._timeframe_minutes = self._parse_timeframe_minutes(tf_str)

        # Consecutive BUY candles for scale-in timing
        self._buy_signal_candles: Dict[str, int] = {}

    # ── Properties ───────────────────────────────────────────────────

    @property
    def strategy_name(self) -> str:
        return self._strategy_name

    def get_daily_opens(self, day: Optional[date] = None) -> int:
        """Return the number of entries opened on *day* (defaults to today UTC)."""
        key = day if day is not None else self._today_utc()
        return self._daily_opens.get(key, 0)

    def get_buy_signal_candles(self, symbol: str) -> int:
        """Return the consecutive BUY-candle count for *symbol*."""
        return self._buy_signal_candles.get(symbol, 0)
