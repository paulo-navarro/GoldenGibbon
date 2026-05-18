"""
Macro market filter — block new LONGs when BTC and ETH are both bearish.

Bearish definition: close < EMA 50 AND ADX > 25 (confirmed downtrend on 15m).

The result is cached per candle open_time so that multiple (strategy, symbol)
ticks in the same cycle share a single computation.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import structlog

from core.indicators.technical import calculate_adx, calculate_ema

logger = structlog.get_logger(__name__)

SENTINEL_SYMBOLS = ("BTCUSDT", "ETHUSDT")
_EMA_PERIOD = 50
_ADX_PERIOD = 14
_ADX_THRESHOLD = 25.0


@dataclass(frozen=True)
class SentinelStatus:
    symbol: str
    bearish: bool
    close: float
    ema_50: float
    adx: float


@dataclass(frozen=True)
class MacroFilterResult:
    longs_blocked: bool
    sentinels: tuple[SentinelStatus, ...]
    reason: str


_cache: dict[str, MacroFilterResult] = {}
_CACHE_MAX = 5


def _evict_cache() -> None:
    while len(_cache) > _CACHE_MAX:
        _cache.pop(next(iter(_cache)))


def evaluate(candle_open_time_iso: str | None) -> MacroFilterResult:
    """
    Check whether BTC and ETH are both in a confirmed downtrend.

    Returns a cached result if already computed for this candle window.
    """
    cache_key = candle_open_time_iso or "_no_ts"
    if cache_key in _cache:
        return _cache[cache_key]

    result = _compute()
    _cache[cache_key] = result
    _evict_cache()
    return result


def _compute() -> MacroFilterResult:
    from db import get_session
    from db.models import CandleRecord

    sentinels: list[SentinelStatus] = []

    for sym in SENTINEL_SYMBOLS:
        status = _evaluate_symbol(sym)
        if status is not None:
            sentinels.append(status)

    if len(sentinels) < len(SENTINEL_SYMBOLS):
        return MacroFilterResult(
            longs_blocked=False,
            sentinels=tuple(sentinels),
            reason="insufficient sentinel data",
        )

    all_bearish = all(s.bearish for s in sentinels)

    if all_bearish:
        parts = [
            f"{s.symbol} below EMA 50 (ADX {s.adx:.1f})" for s in sentinels if s.bearish
        ]
        reason = " & ".join(parts)
    else:
        reason = ""

    return MacroFilterResult(
        longs_blocked=all_bearish,
        sentinels=tuple(sentinels),
        reason=reason,
    )


def _evaluate_symbol(symbol: str) -> Optional[SentinelStatus]:
    """Load 15m candles for *symbol* from DB and check bearish condition."""
    import pandas as pd
    from datetime import datetime, timedelta, timezone

    from db import get_session
    from db.models import CandleRecord

    try:
        with get_session() as session:
            cutoff = datetime.now(timezone.utc) - timedelta(days=7)
            rows = (
                session.query(
                    CandleRecord.open_time,
                    CandleRecord.high,
                    CandleRecord.low,
                    CandleRecord.close,
                )
                .filter(
                    CandleRecord.symbol == symbol,
                    CandleRecord.timeframe == "15m",
                    CandleRecord.open_time >= cutoff,
                )
                .order_by(CandleRecord.open_time)
                .all()
            )

        if len(rows) < _EMA_PERIOD + _ADX_PERIOD:
            logger.debug("macro_filter.insufficient_candles", symbol=symbol, count=len(rows))
            return None

        df = pd.DataFrame(rows, columns=["open_time", "high", "low", "close"])
        df = df.set_index("open_time").sort_index()

        ema_50 = calculate_ema(df["close"], _EMA_PERIOD)
        adx = calculate_adx(df["high"], df["low"], df["close"], _ADX_PERIOD)

        close_val = float(df["close"].iloc[-1])
        ema_val = float(ema_50.iloc[-1])
        adx_val = float(adx.iloc[-1])

        bearish = close_val < ema_val and adx_val > _ADX_THRESHOLD

        return SentinelStatus(
            symbol=symbol,
            bearish=bearish,
            close=close_val,
            ema_50=ema_val,
            adx=adx_val,
        )
    except Exception as exc:
        logger.warning("macro_filter.error", symbol=symbol, error=str(exc))
        return None


def clear_cache() -> None:
    """Clear the macro filter cache (useful for testing)."""
    _cache.clear()
