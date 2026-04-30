---
name: strategy
description: 'Create a new trading strategy for GoldenGibbon. Use when asked to implement a new strategy, add a strategy, or create a strategy named X. Covers the full workflow: base class, decide() logic, auto-registry, and test file.'
argument-hint: '<strategy name and description>'
---

# New Strategy

Creates a complete trading strategy following the GoldenGibbon architecture.

## When to Use
- "Create a new strategy called X"
- "Implement a mean-reversion / momentum / breakout strategy"
- "Add a strategy that does..."

## Procedure

### 1. Read the interface and reference implementation
Before writing anything:
- Read [`core/strategies/base.py`](../../core/strategies/base.py) — understand the `Strategy` ABC
- Read [`core/strategies/smart_hodler.py`](../../core/strategies/smart_hodler.py) — reference implementation
- Read [`core/strategies/mean_reversion.py`](../../core/strategies/mean_reversion.py) — second reference

### 2. Create the strategy file

**File**: `core/strategies/<snake_case_name>.py`

Required structure:
```python
"""
<Human-readable description of what the strategy does.>

This docstring becomes the `description` property shown in the UI.
"""
from typing import Any, Dict
from core.models import MarketData, Portfolio, Signal, StrategyState
from core.strategies.base import Strategy


class <CamelCaseName>Strategy(Strategy):
    """<Same description as module docstring>"""

    @property
    def name(self) -> str:
        return "<snake_case_name>"   # must be unique across all strategies

    def decide(self, market_data: MarketData, portfolio: Portfolio) -> Signal:
        """
        Analyze market data and return a trading signal.
        Pure logic — no side effects, no API calls, no order placement.
        """
        # ... implementation
        return Signal.HOLD
```

**Rules:**
- `name` must be a unique lowercase string matching the filename (without `.py`)
- `decide()` must be pure — no side effects, no API calls, no order placement
- Use `market_data.indicators` for computed indicator values
- Use `portfolio.positions` and `portfolio.balance` for current state
- Signal options: `Signal.BUY`, `Signal.SELL_FULL`, `Signal.SELL_HALF`, `Signal.HOLD`
- Do NOT edit `core/strategies/registry.py` — auto-discovery handles registration

### 3. Registry is automatic

The registry scans `core.strategies` on startup and finds all concrete `Strategy` subclasses.
No manual registration needed. Verify by checking `core/strategies/registry.py` to understand the pattern.

### 4. Create the test file

**File**: `tests/test_<snake_case_name>.py`

Follow the pattern in [`tests/test_smart_hodler.py`](../../tests/test_smart_hodler.py):
- Build `MarketData` with `pd.DataFrame` of OHLCV + indicator columns
- Build `Portfolio` with a fixed balance and empty/populated positions
- Test: BUY signal when conditions are met
- Test: HOLD signal when conditions are not met
- Test: SELL signal when exit conditions trigger
- Use `pytest.mark.parametrize` for edge cases
- No real API calls — all data is constructed in the test

### 5. Run tests

```bash
docker compose run --rm app python -m pytest tests/test_<snake_case_name>.py -v
# Or locally:
.venv-test/bin/python -m pytest tests/test_<snake_case_name>.py -v
```

## Key Types

```python
# Signal
Signal.BUY | Signal.SELL_FULL | Signal.SELL_HALF | Signal.HOLD

# StrategyState (optional state machine)
StrategyState.FLAT | StrategyState.POSITION | StrategyState.REDUCED | StrategyState.COOLDOWN

# Access indicators
ema_50 = market_data.indicators.get("ema_50")
rsi = market_data.indicators.get("rsi")
close = float(market_data.candles["close"].iloc[-1])

# Access portfolio
has_position = any(p.symbol == market_data.symbol for p in portfolio.positions)
balance = portfolio.balance
```
