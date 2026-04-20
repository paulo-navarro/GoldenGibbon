"""
Capital allocation model — fixed weight distribution per strategy.

Distributes a total capital pool across enabled strategies based on
configured ``allocation_pct`` weights.  Each strategy's share is then
split equally across its symbols.

Usage::

    from core.allocation import compute_allocations

    allocs = compute_allocations(
        total_capital=Decimal("10000"),
        enabled_strategies=["smart_hodler", "mean_reversion"],
        strategy_configs={"smart_hodler": {...}, "mean_reversion": {...}},
        symbols=["BTCUSDT", "ETHUSDT"],
    )
    # allocs[("smart_hodler", "BTCUSDT")] → Decimal("3000")
"""

from __future__ import annotations

from decimal import Decimal, ROUND_DOWN
from typing import Dict, List, Tuple

import structlog

logger = structlog.get_logger(__name__)

_WEIGHT_SUM_TOLERANCE = Decimal("0.05")


def compute_allocations(
    total_capital: Decimal,
    enabled_strategies: List[str],
    strategy_configs: Dict[str, dict],
    symbols: List[str],
    regime: str | None = None,
    regime_confidence: float = 0.0,
    regime_shift_pct: float = 0.0,
    strategy_regime_map: Dict[str, str] | None = None,
) -> Dict[Tuple[str, str], Decimal]:
    """
    Distribute ``total_capital`` across (strategy, symbol) pairs.

    Each strategy reads ``allocation_pct`` from its config (0.0–1.0).
    If missing, strategies get equal shares.  Weights are normalised
    so they sum to 1.0 even if the raw values don't.

    When ``regime`` is provided, weights are adjusted to favor
    strategies whose preferred regime matches the current market
    conditions (task 5.4c).

    Within a strategy, capital is split equally across symbols.

    Args:
        total_capital: Total USDT pool to distribute.
        enabled_strategies: List of strategy names to include.
        strategy_configs: Mapping of strategy name → config dict.
        symbols: Trading symbols each strategy trades.
        regime: Current market regime (``"trending"``/``"ranging"``/``"uncertain"``).
        regime_confidence: Confidence of the regime classification (0–1).
        regime_shift_pct: Max weight shift for regime rebalancing.
        strategy_regime_map: Maps strategy name → preferred regime.

    Returns:
        Mapping of ``(strategy_name, symbol)`` → allocated capital.
    """
    if not enabled_strategies or not symbols:
        return {}

    raw_weights = _resolve_weights(enabled_strategies, strategy_configs)

    if regime and regime != "uncertain" and regime_shift_pct > 0 and strategy_regime_map:
        raw_weights = regime_adjusted_weights(
            base_weights=raw_weights,
            regime=regime,
            confidence=regime_confidence,
            regime_shift_pct=regime_shift_pct,
            strategy_regime_map=strategy_regime_map,
        )

    normalised = _normalise(raw_weights)

    allocations: Dict[Tuple[str, str], Decimal] = {}
    n_symbols = len(symbols)

    for strategy_name in enabled_strategies:
        strategy_capital = (total_capital * normalised[strategy_name]).quantize(
            Decimal("0.01"), rounding=ROUND_DOWN,
        )
        per_symbol = (strategy_capital / n_symbols).quantize(
            Decimal("0.01"), rounding=ROUND_DOWN,
        )

        for symbol in symbols:
            allocations[(strategy_name, symbol)] = per_symbol

    logger.info(
        "allocation: computed",
        total_capital=str(total_capital),
        weights={k: str(v) for k, v in normalised.items()},
        pairs=len(allocations),
    )

    return allocations


def regime_adjusted_weights(
    base_weights: Dict[str, Decimal],
    regime: str,
    confidence: float,
    regime_shift_pct: float,
    strategy_regime_map: Dict[str, str],
) -> Dict[str, Decimal]:
    """
    Adjust allocation weights based on the detected market regime.

    Strategies whose preferred regime matches the current regime get
    a boost; others get reduced.  The shift is modulated by confidence
    so that weak signals produce small adjustments.

    Args:
        base_weights: Normalised weights from ``_resolve_weights``.
        regime: Current regime (``"trending"``, ``"ranging"``, ``"uncertain"``).
        confidence: Regime confidence (0.0–1.0).
        regime_shift_pct: Maximum weight shift (e.g. 0.2 = ±20%).
        strategy_regime_map: Maps strategy name → preferred regime.

    Returns:
        Adjusted (but not yet normalised) weights dict.
    """
    if regime == "uncertain" or confidence == 0.0 or regime_shift_pct == 0.0:
        return dict(base_weights)

    actual_shift = Decimal(str(regime_shift_pct * confidence))
    adjusted: Dict[str, Decimal] = {}

    favored = [s for s, r in strategy_regime_map.items() if r == regime and s in base_weights]
    unfavored = [s for s in base_weights if s not in favored]

    if not favored or not unfavored:
        return dict(base_weights)

    boost_per = actual_shift / len(favored)
    reduction_per = actual_shift / len(unfavored)

    for name, weight in base_weights.items():
        if name in favored:
            adjusted[name] = weight + boost_per
        else:
            adjusted[name] = max(Decimal("0.01"), weight - reduction_per)

    return adjusted


def _resolve_weights(
    strategies: List[str],
    configs: Dict[str, dict],
) -> Dict[str, Decimal]:
    """
    Read ``allocation_pct`` from each strategy config.

    Strategies without the field get a default equal share.
    """
    weights: Dict[str, Decimal] = {}
    missing: List[str] = []

    for name in strategies:
        cfg = configs.get(name, {})
        raw = cfg.get("allocation_pct")
        if raw is not None:
            weights[name] = Decimal(str(raw))
        else:
            missing.append(name)

    if missing:
        if weights:
            remaining = max(Decimal("0"), Decimal("1") - sum(weights.values()))
            equal_share = (
                remaining / len(missing) if len(missing) > 0 else Decimal("0")
            )
        else:
            equal_share = Decimal("1") / len(strategies)

        for name in missing:
            weights[name] = equal_share

    return weights


def _normalise(weights: Dict[str, Decimal]) -> Dict[str, Decimal]:
    """Normalise weights so they sum to exactly 1.0."""
    total = sum(weights.values())

    if total == Decimal("0"):
        n = len(weights)
        return {k: Decimal("1") / n for k in weights}

    return {k: v / total for k, v in weights.items()}
