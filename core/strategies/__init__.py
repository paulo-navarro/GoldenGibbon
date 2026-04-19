"""
Strategy base, implementations, and plugin registry.
"""

from core.strategies.base import Strategy
from core.strategies.mean_reversion import MeanReversion
from core.strategies.registry import get_registry
from core.strategies.smart_hodler import SmartHodler

__all__ = ["Strategy", "MeanReversion", "SmartHodler", "get_registry"]
