"""
Order execution layer – abstract interface + PaperExecutor.

Sits between the Risk Engine and PortfolioManager.  Takes a
``RiskDecision``, applies execution-level concerns (slippage),
delegates accounting to PortfolioManager, and returns a filled
``Order`` wrapped in an ``ExecutionResult``.
"""

from core.execution.paper import PaperExecutor

__all__ = ["PaperExecutor"]
