"""
Tests for the market regime detector (task 5.4b).

Verifies classification logic, hysteresis band, smoothing, confidence
computation, and edge cases.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from core.regime import MarketRegime, RegimeClassification, RegimeDetector


# ── Helpers ──────────────────────────────────────────────────────────────────


def _adx_series(values: list[float]) -> dict[str, pd.Series]:
    idx = pd.date_range("2025-01-01", periods=len(values), freq="15min", tz="UTC")
    return {"adx": pd.Series(values, index=idx)}


# ── Classification ───────────────────────────────────────────────────────────


class TestClassification:
    def test_high_adx_is_trending(self):
        detector = RegimeDetector()
        result = detector.detect(_adx_series([30.0, 32.0, 35.0]))
        assert result.regime == MarketRegime.TRENDING

    def test_low_adx_is_ranging(self):
        detector = RegimeDetector()
        result = detector.detect(_adx_series([15.0, 14.0, 13.0]))
        assert result.regime == MarketRegime.RANGING

    def test_mid_adx_is_uncertain(self):
        detector = RegimeDetector()
        result = detector.detect(_adx_series([22.0, 22.5, 23.0]))
        assert result.regime == MarketRegime.UNCERTAIN

    def test_exact_trending_threshold(self):
        detector = RegimeDetector(trending_threshold=25.0)
        result = detector.detect(_adx_series([25.0, 25.0, 25.0]))
        assert result.regime == MarketRegime.TRENDING

    def test_exact_ranging_threshold(self):
        detector = RegimeDetector(ranging_threshold=20.0)
        result = detector.detect(_adx_series([20.0, 20.0, 20.0]))
        assert result.regime == MarketRegime.RANGING


# ── Hysteresis ───────────────────────────────────────────────────────────────


class TestHysteresis:
    def test_just_above_ranging_stays_uncertain(self):
        detector = RegimeDetector(trending_threshold=25.0, ranging_threshold=20.0)
        result = detector.detect(_adx_series([21.0, 21.0, 21.0]))
        assert result.regime == MarketRegime.UNCERTAIN

    def test_just_below_trending_stays_uncertain(self):
        detector = RegimeDetector(trending_threshold=25.0, ranging_threshold=20.0)
        result = detector.detect(_adx_series([24.0, 24.0, 24.0]))
        assert result.regime == MarketRegime.UNCERTAIN


# ── Smoothing ────────────────────────────────────────────────────────────────


class TestSmoothing:
    def test_spike_smoothed_out(self):
        """A single ADX spike doesn't flip the regime if smoothing window covers it."""
        detector = RegimeDetector(smoothing_window=3)
        # Two low values + one spike → smoothed ≈ (15+15+30)/3 = 20 → RANGING
        result = detector.detect(_adx_series([15.0, 15.0, 30.0]))
        assert result.regime == MarketRegime.RANGING or result.regime == MarketRegime.UNCERTAIN
        assert result.adx_value == 30.0  # raw is the spike
        assert result.smoothed_adx == 20.0

    def test_window_1_uses_raw(self):
        detector = RegimeDetector(smoothing_window=1)
        result = detector.detect(_adx_series([15.0, 15.0, 30.0]))
        assert result.regime == MarketRegime.TRENDING
        assert result.smoothed_adx == 30.0

    def test_smoothing_with_fewer_values_than_window(self):
        detector = RegimeDetector(smoothing_window=5)
        result = detector.detect(_adx_series([30.0, 32.0]))
        assert result.regime == MarketRegime.TRENDING
        assert result.smoothed_adx == 31.0


# ── Confidence ───────────────────────────────────────────────────────────────


class TestConfidence:
    def test_trending_at_threshold_is_half(self):
        detector = RegimeDetector(trending_threshold=25.0, ranging_threshold=20.0)
        result = detector.detect(_adx_series([25.0, 25.0, 25.0]))
        assert result.confidence == pytest.approx(0.5)

    def test_trending_far_above_approaches_one(self):
        detector = RegimeDetector(trending_threshold=25.0, ranging_threshold=20.0)
        result = detector.detect(_adx_series([35.0, 35.0, 35.0]))
        assert result.confidence == 1.0

    def test_ranging_at_threshold_is_half(self):
        detector = RegimeDetector(trending_threshold=25.0, ranging_threshold=20.0)
        result = detector.detect(_adx_series([20.0, 20.0, 20.0]))
        assert result.confidence == pytest.approx(0.5)

    def test_ranging_far_below_approaches_one(self):
        detector = RegimeDetector(trending_threshold=25.0, ranging_threshold=20.0)
        result = detector.detect(_adx_series([10.0, 10.0, 10.0]))
        assert result.confidence == 1.0

    def test_uncertain_confidence_below_half(self):
        detector = RegimeDetector(trending_threshold=25.0, ranging_threshold=20.0)
        result = detector.detect(_adx_series([22.5, 22.5, 22.5]))
        assert result.confidence <= 0.5


# ── Edge cases ───────────────────────────────────────────────────────────────


class TestEdgeCases:
    def test_empty_indicators(self):
        detector = RegimeDetector()
        result = detector.detect({})
        assert result.regime == MarketRegime.UNCERTAIN
        assert result.confidence == 0.0

    def test_missing_adx_key(self):
        detector = RegimeDetector()
        result = detector.detect({"rsi": pd.Series([50.0])})
        assert result.regime == MarketRegime.UNCERTAIN

    def test_all_nan_adx(self):
        detector = RegimeDetector()
        idx = pd.date_range("2025-01-01", periods=5, freq="15min")
        result = detector.detect({"adx": pd.Series([float("nan")] * 5, index=idx)})
        assert result.regime == MarketRegime.UNCERTAIN

    def test_single_value(self):
        detector = RegimeDetector(smoothing_window=3)
        result = detector.detect(_adx_series([30.0]))
        assert result.regime == MarketRegime.TRENDING

    def test_timestamp_populated(self):
        detector = RegimeDetector()
        result = detector.detect(_adx_series([30.0, 30.0, 30.0]))
        assert result.timestamp is not None

    def test_invalid_thresholds_raises(self):
        with pytest.raises(ValueError, match="ranging_threshold"):
            RegimeDetector(trending_threshold=20.0, ranging_threshold=25.0)

    def test_equal_thresholds_raises(self):
        with pytest.raises(ValueError, match="ranging_threshold"):
            RegimeDetector(trending_threshold=25.0, ranging_threshold=25.0)


# ── Config integration ───────────────────────────────────────────────────────


class TestRegimeConfig:
    def test_config_defaults(self):
        from core.config import RegimeConfig

        cfg = RegimeConfig()
        assert cfg.adx_trending_threshold == 25.0
        assert cfg.adx_ranging_threshold == 20.0
        assert cfg.smoothing_window == 3

    def test_config_in_settings(self):
        from core.config import get_settings

        settings = get_settings(reload=True)
        assert hasattr(settings, "regime")
        assert settings.regime.adx_trending_threshold == 25.0

    def test_detector_from_config(self):
        from core.config import RegimeConfig

        cfg = RegimeConfig(adx_trending_threshold=30.0, adx_ranging_threshold=15.0, smoothing_window=5)
        detector = RegimeDetector(
            trending_threshold=cfg.adx_trending_threshold,
            ranging_threshold=cfg.adx_ranging_threshold,
            smoothing_window=cfg.smoothing_window,
        )
        result = detector.detect(_adx_series([28.0, 28.0, 28.0, 28.0, 28.0]))
        assert result.regime == MarketRegime.UNCERTAIN  # below 30
