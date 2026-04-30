---
description: "Analyze backtest results for GoldenGibbon strategies. Compare performance metrics, identify weaknesses, and suggest parameter improvements."
agent: agent
---

Use the @BacktestAnalyst agent to analyze backtest results.

## Task
1. Read recent backtest results — query from the database or read from provided data
2. Identify the weakest performing strategy and metric
3. Compare strategies against each other and against buy-and-hold
4. Produce a prioritized list of suggested improvements with specific parameter changes

## Reference Files
- [core/backtest/metrics.py](../core/backtest/metrics.py)
- [core/backtest/compare.py](../core/backtest/compare.py)
- [core/backtest/reporting.py](../core/backtest/reporting.py)
