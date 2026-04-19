"""
Market regime detector — classify current conditions as trending or ranging.

Uses ADX (Average Directional Index) with a hysteresis band to avoid
flapping between regimes on noisy data.

Usage::

    from core.regime import RegimeDetector, MarketRegime

    detector = RegimeDetector()
    result = detector.detect(indicators)
    if result.regime == MarketRegime.TRENDING:
        ...
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Dict, Optional

import pandas as pd


class MarketRegime(str, Enum):
    TRENDING = "trending"
    RANGING = "ranging"
    UNCERTAIN = "uncertain"


@dataclass(frozen=True)
class RegimeClassification:
    regime: MarketRegime
    confidence: float
    adx_value: float
    smoothed_adx: float
    timestamp: Optional[datetime] = None


class RegimeDetector:
    """
    Classify market regime from pre-computed ADX indicator values.

    Uses two thresholds with a hysteresis band:

    * ADX > ``trending_threshold`` → TRENDING
    * ADX < ``ranging_threshold`` → RANGING
    * Between the two → UNCERTAIN

    An optional smoothing window averages the last N ADX values to
    reduce noise from single-candle spikes.
    """

    def __init__(
        self,
        trending_threshold: float = 25.0,
        ranging_threshold: float = 20.0,
        smoothing_window: int = 3,
    ) -> None:
        if ranging_threshold >= trending_threshold:
            raise ValueError(
                f"ranging_threshold ({ranging_threshold}) must be < "
                f"trending_threshold ({trending_threshold})"
            )
        self._trending = trending_threshold
        self._ranging = ranging_threshold
        self._window = max(1, smoothing_window)

    def detect(
        self,
        indicators: Dict[str, pd.Series],
        adx_key: str = "adx",
    ) -> RegimeClassification:
        """
        Classify the current market regime.

        Args:
            indicators: Dict of indicator name → pd.Series (from MarketData).
            adx_key: Key to look up ADX in the indicators dict.

        Returns:
            ``RegimeClassification`` with regime, confidence, and ADX values.
        """
        adx_series = indicators.get(adx_key)

        if adx_series is None or adx_series.empty:
            return RegimeClassification(
                regime=MarketRegime.UNCERTAIN,
                confidence=0.0,
                adx_value=0.0,
                smoothed_adx=0.0,
            )

        adx_clean = adx_series.dropna()
        if adx_clean.empty:
            return RegimeClassification(
                regime=MarketRegime.UNCERTAIN,
                confidence=0.0,
                adx_value=0.0,
                smoothed_adx=0.0,
            )

        raw_adx = float(adx_clean.iloc[-1])
        tail = adx_clean.iloc[-self._window:]
        smoothed = float(tail.mean())

        regime = self._classify(smoothed)
        confidence = self._compute_confidence(smoothed, regime)

        timestamp = None
        if hasattr(adx_clean.index[-1], "to_pydatetime"):
            timestamp = adx_clean.index[-1].to_pydatetime()
        elif isinstance(adx_clean.index[-1], datetime):
            timestamp = adx_clean.index[-1]

        return RegimeClassification(
            regime=regime,
            confidence=confidence,
            adx_value=raw_adx,
            smoothed_adx=round(smoothed, 4),
            timestamp=timestamp,
        )

    def _classify(self, adx: float) -> MarketRegime:
        if adx >= self._trending:
            return MarketRegime.TRENDING
        if adx <= self._ranging:
            return MarketRegime.RANGING
        return MarketRegime.UNCERTAIN

    def _compute_confidence(self, adx: float, regime: MarketRegime) -> float:
        """
        Confidence scales with distance from the hysteresis band.

        At the threshold boundary → 0.5.  Far away → approaches 1.0.
        """
        band_width = self._trending - self._ranging
        if band_width == 0:
            return 1.0

        if regime == MarketRegime.TRENDING:
            distance = adx - self._trending
            return min(1.0, 0.5 + distance / band_width)
        elif regime == MarketRegime.RANGING:
            distance = self._ranging - adx
            return min(1.0, 0.5 + distance / band_width)
        else:
            mid = (self._trending + self._ranging) / 2
            distance = abs(adx - mid)
            return max(0.0, 0.5 - distance / band_width)
