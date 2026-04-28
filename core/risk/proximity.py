"""
Exit proximity calculation.

Computes how close each exit trigger is to firing for open positions,
without actually triggering any stops or state transitions.
"""

from __future__ import annotations

import math
from decimal import Decimal
from typing import Any, Dict, List, Optional

import pandas as pd

from core.indicators.engine import IndicatorEngine
from core.models import ExitConditionStatus, ExitProximityResponse


def _safe_last(series: Optional[pd.Series]) -> Optional[float]:
    if series is None or series.empty:
        return None
    val = series.iloc[-1]
    try:
        f = float(val)
        return None if math.isnan(f) else f
    except (TypeError, ValueError):
        return None


def compute_exit_proximity(
    *,
    symbol: str,
    strategy: str,
    close: Decimal,
    position_hard_stop: Decimal,
    position_trailing_stop: Decimal,
    entry_price: Decimal,
    candles_held: Optional[int],
    time_stop_candles: Optional[int],
    df: pd.DataFrame,
    strategy_config: Dict[str, Any],
) -> ExitProximityResponse:
    close_f = float(close)

    # Stop distances
    hard_stop_pct = 0.0
    if position_hard_stop > 0 and close > 0:
        hard_stop_pct = float((close - position_hard_stop) / close)

    trailing_stop_pct = 0.0
    if position_trailing_stop > 0 and close > 0:
        trailing_stop_pct = float((close - position_trailing_stop) / close)

    time_stop_pct: Optional[float] = None
    if candles_held is not None and time_stop_candles and time_stop_candles > 0:
        time_stop_pct = candles_held / time_stop_candles

    # Exit conditions from indicators
    conditions: List[ExitConditionStatus] = []
    indicators = IndicatorEngine.calculate_all(df, strategy_config)

    if strategy == "smart_hodler":
        conditions = _sh_exit_conditions(close_f, indicators, strategy_config)
    elif strategy == "mean_reversion":
        conditions = _mr_exit_conditions(close_f, indicators, strategy_config)

    return ExitProximityResponse(
        symbol=symbol,
        strategy=strategy,
        hard_stop_pct=round(hard_stop_pct, 6),
        trailing_stop_pct=round(trailing_stop_pct, 6),
        time_stop_pct=round(time_stop_pct, 6) if time_stop_pct is not None else None,
        exit_conditions=conditions,
    )


def _sh_exit_conditions(
    close: float,
    indicators: Dict[str, pd.Series],
    config: Dict[str, Any],
) -> List[ExitConditionStatus]:
    conditions: List[ExitConditionStatus] = []

    ema_fast = _safe_last(indicators.get("ema_fast"))
    ema_slow = _safe_last(indicators.get("ema_slow"))

    # SELL_FULL: EMA death cross (EMA fast < EMA slow)
    if ema_fast is not None and ema_slow is not None:
        conditions.append(ExitConditionStatus(
            name="EMA death cross",
            met=ema_fast < ema_slow,
            current_value=f"{ema_fast:.2f} / {ema_slow:.2f}",
            threshold="fast < slow",
        ))

    # SELL_HALF: close < EMA fast
    if ema_fast is not None:
        conditions.append(ExitConditionStatus(
            name="Close below EMA fast",
            met=close < ema_fast,
            current_value=f"{close:.2f}",
            threshold=f"{ema_fast:.2f}",
        ))

    # SELL_HALF: ADX falling
    adx = indicators.get("adx")
    if adx is not None and len(adx) >= 4:
        lookback = int(config.get("adx_falling_lookback", 3))
        adx_now = _safe_last(adx)
        adx_prev_val = None
        if len(adx) > lookback:
            try:
                adx_prev_val = float(adx.iloc[-(lookback + 1)])
            except (TypeError, ValueError):
                pass
        if adx_now is not None and adx_prev_val is not None:
            conditions.append(ExitConditionStatus(
                name="ADX falling",
                met=adx_now < adx_prev_val,
                current_value=f"{adx_now:.1f}",
                threshold=f"< {adx_prev_val:.1f} ({lookback}b ago)",
            ))

    return conditions


def _mr_exit_conditions(
    close: float,
    indicators: Dict[str, pd.Series],
    config: Dict[str, Any],
) -> List[ExitConditionStatus]:
    conditions: List[ExitConditionStatus] = []

    bb_upper = _safe_last(indicators.get("bb_upper"))
    bb_middle = _safe_last(indicators.get("bb_middle"))
    rsi = _safe_last(indicators.get("rsi"))
    adx = _safe_last(indicators.get("adx"))

    # SELL_FULL: close >= upper BB
    if bb_upper is not None:
        conditions.append(ExitConditionStatus(
            name="Close >= upper BB",
            met=close >= bb_upper,
            current_value=f"{close:.2f}",
            threshold=f"{bb_upper:.2f}",
        ))

    # SELL_FULL: RSI overbought
    rsi_threshold = float(config.get("rsi_overbought", 70))
    if rsi is not None:
        conditions.append(ExitConditionStatus(
            name="RSI overbought",
            met=rsi > rsi_threshold,
            current_value=f"{rsi:.1f}",
            threshold=f"> {rsi_threshold:.0f}",
        ))

    # SELL_FULL: ADX regime shift to trending
    adx_regime = float(config.get("adx_regime_shift", 30))
    if adx is not None:
        conditions.append(ExitConditionStatus(
            name="ADX regime shift",
            met=adx > adx_regime,
            current_value=f"{adx:.1f}",
            threshold=f"> {adx_regime:.0f}",
        ))

    # SELL_HALF: close >= middle BB
    if bb_middle is not None:
        conditions.append(ExitConditionStatus(
            name="Close >= middle BB",
            met=close >= bb_middle,
            current_value=f"{close:.2f}",
            threshold=f"{bb_middle:.2f}",
        ))

    return conditions
