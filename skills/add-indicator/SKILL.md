---
name: add-indicator
description: 'Add a new technical indicator to GoldenGibbon. Use when asked to implement, add, or create an indicator like MACD, Stochastic, CCI, VWAP, OBV, or any custom calculation. Covers: pure function in technical.py, export in __init__.py, engine integration, and test.'
argument-hint: '<indicator name> — <formula or description>'
---

# Add Technical Indicator

Adds a new technical indicator following the GoldenGibbon indicator architecture.

## When to Use
- "Add MACD indicator"
- "Implement Stochastic oscillator"
- "Create a volume-weighted indicator X"
- Any new calculation that consumes OHLCV data

## Architecture

```
core/indicators/
  technical.py   ← pure calculation functions (add here)
  engine.py      ← orchestration / config-driven batch calc (wire here)
  __init__.py    ← public exports (add here)
```

## Procedure

### 1. Implement the pure function in `core/indicators/technical.py`

Add to the end of the file. Follow the existing pattern exactly:

```python
def calculate_<name>(series_or_df, period: int, ...) -> pd.Series:
    """
    Calculate <Indicator Name>.

    <Brief description of what it measures and when it's useful.>

    Args:
        series: <description of input> (typically close/high/low prices)
        period: <description of period parameter>

    Returns:
        Series with <indicator name> values (same length as input,
        leading NaNs for warmup period)

    Raises:
        ValueError: If period < 1 or series is empty

    Example:
        >>> close = pd.Series([10, 11, 12, 13, 14, 15, 16])
        >>> result = calculate_<name>(close, period=3)
    """
    if period < 1:
        raise ValueError(f"Period must be >= 1, got {period}")
    if len(series) == 0:
        return pd.Series(dtype=float)

    # ... implementation using numpy / pandas
    return result
```

**Rules for indicator functions:**
- Pure function — no side effects, no I/O
- Accept `pd.Series` (or `pd.DataFrame` for multi-input indicators)
- Return `pd.Series` of same length as input (leading `NaN` for warmup)
- Validate inputs: `period >= 1`, non-empty series
- Use `numpy` / `pandas` — do not use the `ta` library unless wrapping it explicitly

### 2. Export in `core/indicators/__init__.py`

Add to the imports and `__all__`:
```python
from core.indicators.technical import (
    ...,
    calculate_<name>,      # ← add here
)

__all__ = [
    ...,
    "calculate_<name>",    # ← add here
]
```

### 3. Wire into `IndicatorEngine` in `core/indicators/engine.py`

Add the config-driven calculation inside `IndicatorEngine.calculate_all()`:
```python
# In the relevant section of calculate_all():
if '<name>_period' in config:
    indicators['<name>'] = calculate_<name>(
        df['close'],  # or df[['high', 'low', 'close']] for multi-input
        config['<name>_period']
    )
```

If the indicator needs a named preset (like `calculate_smart_hodler_indicators`), add a dedicated function.

### 4. Add unit tests in `tests/test_indicators.py`

Follow the pattern of existing tests in the file:
```python
def test_calculate_<name>_basic():
    """<name> returns correct values for known input."""
    close = pd.Series([...])  # known values
    result = calculate_<name>(close, period=N)
    assert len(result) == len(close)
    assert pd.isna(result.iloc[0])          # warmup NaN
    assert abs(float(result.iloc[-1]) - EXPECTED) < 0.01

def test_calculate_<name>_invalid_period():
    with pytest.raises(ValueError):
        calculate_<name>(pd.Series([1, 2, 3]), period=0)

def test_calculate_<name>_empty_series():
    result = calculate_<name>(pd.Series(dtype=float), period=5)
    assert len(result) == 0
```

### 5. Run tests

```bash
docker compose run --rm app python -m pytest tests/test_indicators.py -v
# Or locally:
.venv-test/bin/python -m pytest tests/test_indicators.py -v
```
