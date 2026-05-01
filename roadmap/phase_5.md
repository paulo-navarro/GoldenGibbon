# Phase 5 — Bear Trend Short (BearGuard Strategy)

> **Goal:** Extend the platform to profit from bear trends via spot margin shorts (Binance Cross Margin, 1x — no leverage multiplier).
> **Strategy spec:** [strategy_bear_guard.md](strategy_bear_guard.md)
> **Status:** Planning

---

## Overview

The system is currently 100% long-only. Adding short support requires a full-stack extension across six layers, delivered in dependency order so every task can be implemented, tested, and merged without breaking existing functionality.

```
Phase 5 dependency order:

  5.1 (models) ──► 5.2 (config)
               ──► 5.3 (risk engine)
               ──► 5.4 (portfolio manager)
               ──► 5.5 (paper executor)   ◄── 5.4
               ──► 5.6 (binance executor) ◄── 5.4
               ──► 5.7 (strategy)
  5.1 + 5.7   ──► 5.8 (tests — strategy)
  5.1 + 5.3   ──► 5.9 (tests — risk)
  5.1 + 5.4 + 5.5 ──► 5.10 (tests — executors)
```

All long-only behaviour is preserved by:
- Default `side = LONG` on every new field
- New signals (`SHORT`) only route to new code paths; existing signals (`BUY`, `SELL_FULL`, `SELL_HALF`, `HOLD`) are untouched
- Global kill switch (`shorts_enabled`) gates every short code path before it can run

---

## Global Short Kill Switch

A single config flag disables the entire short subsystem without touching the long pipeline:

**`core/config.py`** — add to the top-level `Settings` or a new `ShortConfig` section:

```yaml
shorts:
  enabled: false          # master switch — set true to enable BearGuard entries
```

**Behaviour when `shorts.enabled = false`:**
- `Signal.SHORT` is treated as `Signal.HOLD` in the risk engine (no position opened)
- No margin orders are placed by the Binance executor
- Existing open short positions (if any exist from before the flag was disabled) continue to be managed normally — stops and covers still execute
- No UI change needed; the BearGuard strategy appears in the registry but simply never generates actionable signals

---

## 5.1 — Model Extensions (`core/models.py`)

> **Prerequisite for:** everything else.
> All changes are additive — no existing field is removed or renamed.

- [ ] **5.1.1** Add `PositionSide` enum with values `LONG = "long"` and `SHORT = "short"`
- [ ] **5.1.2** Add `Signal.SHORT = "short"` to the `Signal` enum
- [ ] **5.1.3** Add `side: PositionSide = PositionSide.LONG` field to the `Position` model (default preserves all existing long positions)
- [ ] **5.1.4** Add `lowest_close: Optional[Decimal] = None` to the `Position` model (short trailing stop tracks the lowest close since entry — mirrors `highest_close` for longs)
- [ ] **5.1.5** Update `Position.calculate_unrealized_pnl()`: when `side == SHORT`, flip sign — `pnl_per_unit = entry_price - current_price` (instead of `current_price - entry_price`)
- [ ] **5.1.6** Add `side: PositionSide = PositionSide.LONG` to `RiskDecision` model (executor reads this to know what kind of position to open)
- [ ] **5.1.7** Add `BearGuardConditions` Pydantic model (fields: `death_cross`, `close_below_ema_fast`, `adx_above_threshold`, `volume_above_average`, `hourly_ema_falling`, `hourly_rsi_bearish`, `session_filter_pass`; property `all_short_conditions_met`) — follow `StrategyConditions` and `MeanReversionConditions` as pattern
- [ ] **5.1.8** Export `PositionSide` and `BearGuardConditions` in `__all__`

**No migration needed** — `Position` is a Pydantic model, not an ORM model. The DB ORM `PositionRecord` (if it exists in `db/models.py`) may need a separate migration; evaluate in 5.1.x follow-up.

---

## 5.2 — Config Extensions (`core/config.py`)

> **Prerequisite for:** risk engine (5.3), tick loop gating.
> All fields have safe defaults so existing configs need no changes.

- [ ] **5.2.1** Add `ShortConfig` Pydantic settings class with field `enabled: bool = Field(default=False)` — master kill switch
- [ ] **5.2.2** Add `shorts: ShortConfig` field to the top-level `Settings` class
- [ ] **5.2.3** Add `BearGuardConfig` strategy config class with all parameters from [strategy_bear_guard.md § 10](strategy_bear_guard.md#10-risk-parameters-default-config):
  - Entry: `adx_threshold`, `hourly_rsi_bear_threshold`, `hourly_ema_lookback`
  - Exit: `rsi_overbought_threshold`, `adx_falling_lookback`, `exit_confirmation_candles`
  - Sizing: `position_size_pct`
  - Stops: `hard_stop_pct`, `trailing_stop_atr_multiplier`, `trailing_stop_enabled`, `breakeven_trigger_pct`, `lockin_trigger_pct`, `lockin_stop_pct`
  - Cooldown: `cooldown_candles`
  - Margin: `margin_type`
- [ ] **5.2.4** Add `bear_guard: BearGuardConfig` to the `StrategiesConfig` class
- [ ] **5.2.5** Add `bear_guard` section with defaults to `strategies.yaml`
- [ ] **5.2.6** Write unit test in `tests/test_config.py`: load config with `bear_guard` section, assert all fields parse correctly and defaults are sane

---

## 5.3 — Risk Engine (`core/risk/__init__.py`)

> **Prerequisite for:** tick loop integration (5.11).
> Depends on: 5.1, 5.2.

- [ ] **5.3.1** In `evaluate()`, add a branch: if `signal == Signal.SHORT` and `shorts.enabled == False` → return `RiskDecision(action=HOLD)` immediately (kill switch gate)
- [ ] **5.3.2** In `evaluate()`, add a branch: if `signal == Signal.SHORT` and no existing short position → call new `_evaluate_short_open()`
- [ ] **5.3.3** In `evaluate()`, add a branch: if `signal == Signal.SHORT` and short position already exists → return `RiskDecision(action=HOLD)` (no scale-in for shorts)
- [ ] **5.3.4** Implement `_evaluate_short_open()`:
  - Size: `position_size_pct` × `available_capital` / `current_price` (same formula as longs)
  - Hard stop (inverted): `entry_price × (1 + hard_stop_pct)`
  - Trailing stop initial value (inverted): `entry_price + (ATR × trailing_stop_atr_multiplier)`
  - Apply same daily trade cap and per-symbol exposure checks as `_evaluate_open()`
  - Return `RiskDecision(action=OPEN, side=SHORT, size=..., hard_stop_price=..., trailing_stop_price=...)`
- [ ] **5.3.5** In `check_stops()`, detect `position.side == SHORT` and apply inverted logic:
  - Hard stop fires when `close > hard_stop_price` (instead of `close < hard_stop_price`)
  - Trailing: ratchet `lowest_close = min(lowest_close, current_close)` (instead of `max`); recalculate `trailing_stop = lowest_close + (ATR × multiplier)`; fires when `close > trailing_stop_price`
  - Break-even ratchet (inverted): profit_pct for shorts = `(entry_price - close) / entry_price`; ratchet hard stop **downward** (not upward) — `new_stop = entry_price × (1 - lockin_stop_pct)` at `+4%`
  - The ratcheted hard stop for shorts only moves **down**, never up: `min(position.hard_stop_price, new_stop)`
- [ ] **5.3.6** `SELL_FULL` and `SELL_HALF` signals with an open short → route to `RiskAction.CLOSE` / `RiskAction.REDUCE` as already done for longs (no change needed — `decision.side` is read from the position, not the signal)

---

## 5.4 — Portfolio Manager (`core/portfolio/__init__.py`)

> **Prerequisite for:** executors (5.5, 5.6).
> Depends on: 5.1.

- [ ] **5.4.1** Update `open_position()` to accept `side: PositionSide = PositionSide.LONG` parameter; pass it when constructing the `Position` object
- [ ] **5.4.2** For `side=SHORT`, initialise `lowest_close = entry_price` (and `highest_close = entry_price` stays for completeness)
- [ ] **5.4.3** Update `close_position()` PnL calculation: if `position.side == SHORT`, use `pnl_per_unit = entry_price - exit_price`; update `usdt_balance` accordingly
- [ ] **5.4.4** Update `reduce_position()` PnL calculation: same inversion for partial covers

---

## 5.5 — Paper Executor (`core/execution/paper.py`)

> Depends on: 5.1, 5.4.

- [ ] **5.5.1** In `_execute_open()`: if `decision.side == SHORT`, apply sell slippage (`price × (1 - slippage_pct)` — shorting at slightly lower price, adverse); call `pm.open_position(..., side=SHORT)`
- [ ] **5.5.2** In `_execute_close()`: if `position.side == SHORT`, apply buy slippage (`price × (1 + slippage_pct)` — buying back at slightly higher price, adverse); the rest of the close flow is unchanged
- [ ] **5.5.3** In `_execute_reduce()`: same buy-slippage inversion for partial covers on short positions
- [ ] **5.5.4** Ensure `OrderSide` in the resulting `Order` record is set correctly: `SELL` for short open, `BUY` for short close/reduce

---

## 5.6 — Binance Executor (`core/execution/binance.py`)

> Depends on: 5.1, 5.4.
> **This task touches real money — implement and test in paper mode first (5.5), then port to Binance.**

- [ ] **5.6.1** In `_execute_open()`: if `decision.side == SHORT`, route to Binance Margin endpoint `POST /sapi/v1/margin/order` with `side=SELL`, `sideEffectType=MARGIN_BUY`; read `margin_type` from config (`"cross"` → `isIsolated=FALSE`)
- [ ] **5.6.2** In `_execute_close()` / `_execute_reduce()`: if `position.side == SHORT`, use `POST /sapi/v1/margin/order` with `side=BUY`, `sideEffectType=AUTO_REPAY`
- [ ] **5.6.3** For exchange-side stop orders on shorts: use `POST /sapi/v1/margin/order` with `side=BUY`, `type=STOP_LOSS_LIMIT`, `stopPrice=hard_stop_price`
- [ ] **5.6.4** Handle `shorts.enabled == False` gate: if disabled, raise `RuntimeError` before placing any margin order (defence-in-depth, should never reach here but belt-and-suspenders)
- [ ] **5.6.5** Error handling: map Binance margin-specific error codes (`-3045`, `-3021`, insufficient margin, etc.) to actionable log messages

---

## 5.7 — BearGuard Strategy (`core/strategies/bear_guard.py`)

> New file — does not touch any existing strategy.
> Depends on: 5.1 (for `Signal.SHORT`, `BearGuardConditions`, `StrategyState`).
> Reference implementation: [strategy_bear_guard.md](strategy_bear_guard.md), [core/strategies/smart_hodler.py](../core/strategies/smart_hodler.py)

- [ ] **5.7.1** Create `core/strategies/bear_guard.py` with `BearGuard(Strategy)` class; property `name` returns `"bear_guard"`
- [ ] **5.7.2** Implement `decide()` with full entry logic (all 7 conditions from [§ 1](strategy_bear_guard.md#11-bear-trend-detection-primary--15m)); only emit `Signal.SHORT` from `FLAT` state
- [ ] **5.7.3** Implement full-cover exit logic: golden cross (EMA50 > EMA200) OR N consecutive closes above EMA200; emit `Signal.SELL_FULL` → enter `COOLDOWN`
- [ ] **5.7.4** Implement partial-cover exit logic: 1H RSI > 70 AND ADX falling; emit `Signal.SELL_HALF` → transition to `REDUCED`; only from `POSITION` (not `REDUCED`)
- [ ] **5.7.5** Implement cooldown countdown (reuse pattern from `SmartHodler._enter_cooldown()`)
- [ ] **5.7.6** Implement session filter (reuse `is_in_dead_zone` from `core/strategies/session_filter.py`)
- [ ] **5.7.7** Populate `self._conditions` (type `BearGuardConditions`) on every call for UI/debug visibility
- [ ] **5.7.8** Implement NaN guard (return `Signal.HOLD` if any required indicator value is NaN)
- [ ] **5.7.9** Implement missing-secondary-data guard (return `Signal.HOLD` if `market_data.has_secondary == False`)
- [ ] **5.7.10** Verify auto-discovery: run `get_registry()` and confirm `"bear_guard"` appears — no manual edit to `registry.py` needed

---

## 5.8 — Tests: BearGuard Strategy (`tests/test_bear_guard.py`)

> Depends on: 5.1, 5.7.
> Follow pattern: [tests/test_smart_hodler.py](../tests/test_smart_hodler.py)

- [ ] **5.8.1** Helper `_make_candles(close_val, volume_val, length)` — constant OHLCV DataFrame
- [ ] **5.8.2** Helper `_make_series(value, length)` — constant pd.Series
- [ ] **5.8.3** Helper `_make_falling_series(start, end, length)` and `_make_rising_series(start, end, length)`
- [ ] **5.8.4** `DEFAULT_CONFIG` dict with all BearGuard params
- [ ] **5.8.5** Test: `Signal.SHORT` emitted when all 7 conditions met (from `FLAT`)
- [ ] **5.8.6** Test: `Signal.HOLD` when each individual condition is false — one test per condition, use `pytest.mark.parametrize`
- [ ] **5.8.7** Test: `Signal.HOLD` from `POSITION` even when all SHORT conditions met (no re-entry while in position)
- [ ] **5.8.8** Test: `Signal.SELL_FULL` emitted on golden cross (EMA50 > EMA200) while in `POSITION`
- [ ] **5.8.9** Test: `Signal.SELL_FULL` emitted after N consecutive closes above EMA200 while in `POSITION`
- [ ] **5.8.10** Test: `Signal.SELL_HALF` emitted when 1H RSI > 70 AND ADX falling, from `POSITION` → state becomes `REDUCED`
- [ ] **5.8.11** Test: `Signal.SELL_HALF` NOT emitted from `REDUCED` (only full cover from that state)
- [ ] **5.8.12** Test: signal priority — `SELL_FULL` takes precedence over `SELL_HALF` over `SHORT` over `HOLD`
- [ ] **5.8.13** Test: state transitions — `FLAT → POSITION → REDUCED → FLAT` and `FLAT → POSITION → COOLDOWN → FLAT`
- [ ] **5.8.14** Test: cooldown countdown — strategy stays in `HOLD` for `cooldown_candles` ticks, then transitions back to `FLAT`
- [ ] **5.8.15** Test: NaN guard — return `HOLD` if any indicator is NaN
- [ ] **5.8.16** Test: missing secondary data — return `HOLD` if `has_secondary == False`
- [ ] **5.8.17** Test: session filter — no `SHORT` during dead zone, but `SELL_FULL` still fires

---

## 5.9 — Tests: Risk Engine Extensions (`tests/test_risk_engine.py`)

> Depends on: 5.1, 5.3.

- [ ] **5.9.1** Test: `Signal.SHORT` with `shorts.enabled=False` → `RiskDecision(action=HOLD)` (kill switch)
- [ ] **5.9.2** Test: `Signal.SHORT` with `shorts.enabled=True`, no existing position → `RiskDecision(action=OPEN, side=SHORT)` with correct size
- [ ] **5.9.3** Test: `Signal.SHORT` with existing short position → `RiskDecision(action=HOLD)` (no scale-in)
- [ ] **5.9.4** Test: `_evaluate_short_open()` hard stop is ABOVE entry (`entry × (1 + hard_stop_pct)`)
- [ ] **5.9.5** Test: `_evaluate_short_open()` trailing stop initial value is ABOVE entry (`entry + ATR × mult`)
- [ ] **5.9.6** Test: `check_stops()` for short — hard stop fires when `close > hard_stop_price`
- [ ] **5.9.7** Test: `check_stops()` for short — hard stop does NOT fire when `close < entry` (moving in our favour)
- [ ] **5.9.8** Test: `check_stops()` for short — trailing stop ratchets downward; `lowest_close` tracks minimum
- [ ] **5.9.9** Test: `check_stops()` for short — trailing stop fires when price bounces above the trailing level
- [ ] **5.9.10** Test: break-even ratchet for short — hard stop ratchets DOWN (to entry, then below) as profit grows
- [ ] **5.9.11** Test: ratcheted hard stop for short never moves back UP

---

## 5.10 — Tests: Paper Executor Extensions (`tests/test_paper_executor.py`)

> Depends on: 5.1, 5.4, 5.5.

- [ ] **5.10.1** Test: `OPEN` with `side=SHORT` — creates short position, applies sell slippage (fill price < reference price)
- [ ] **5.10.2** Test: `CLOSE` on short position — applies buy slippage (fill price > reference price), PnL correct when price fell
- [ ] **5.10.3** Test: `CLOSE` on short position — PnL correct when price rose (loss scenario)
- [ ] **5.10.4** Test: `REDUCE` on short position — partial cover, correct remaining size and PnL
- [ ] **5.10.5** Test: `Order.side` is `SELL` for short open, `BUY` for short close/reduce

---

## 5.11 — Tick Loop Integration (`core/tasks/__init__.py`)

> Wire everything together in the live tick loop.
> Depends on: 5.1, 5.2, 5.3, 5.4, 5.5, 5.6, 5.7.

- [ ] **5.11.1** Add `bear_guard` as a supported strategy in `_build_components()`: instantiate `BearGuard(config)` when strategy name is `"bear_guard"`
- [ ] **5.11.2** Pass `shorts_enabled` from settings to `RiskEngine` at construction time (or read from `get_settings()` inside evaluate — keep consistent with existing pattern)
- [ ] **5.11.3** In the tick loop, after `check_stops()`, handle `position.side == SHORT` when calling `pm.update_stops()` — pass `lowest_close` from `StopCheckResult` (new field) alongside existing fields
- [ ] **5.11.4** Add `"bear_guard"` to the `strategy_regime_map` in regime config (Phase 3 gating): `"bear_guard": "trending"` — BearGuard only operates in trending (downward) markets, so regime gate should block it when ranging (same ADX logic as Smart Hodler, but the trend direction is confirmed by the death cross itself)
- [ ] **5.11.5** Add `bear_guard` section to `strategies.yaml` with all default parameters from [§ 10](strategy_bear_guard.md#10-risk-parameters-default-config)
- [ ] **5.11.6** Manual smoke test: run backtest with `bear_guard` on a period with a known bear market (e.g., BTCUSDT 2022-01 to 2022-06) and confirm: signals appear, stops fire correctly, PnL is positive

---

## 5.12 — DB Migration (if applicable)

> Only needed if the ORM `PositionRecord` in `db/models.py` stores position data.

- [ ] **5.12.1** Inspect `db/models.py` — check if `PositionRecord` (or equivalent) exists and stores `side`, `lowest_close`
- [ ] **5.12.2** If yes: add `side VARCHAR(5) NOT NULL DEFAULT 'long'` and `lowest_close NUMERIC` columns
- [ ] **5.12.3** Generate migration: `alembic revision --autogenerate -m "add position side and lowest_close"`
- [ ] **5.12.4** Review generated migration file — confirm only the expected columns are added, no destructive changes
- [ ] **5.12.5** Apply: `alembic upgrade head`

---

## 5.13 — Frontend (informational, scope TBD)

> The frontend can be updated in a follow-up. Short positions do not break existing UI — they simply won't appear until frontend support is added.

- [ ] **5.13.1** In Open Positions table: show position `side` badge (`LONG` / `SHORT`) — colour coded (green / red)
- [ ] **5.13.2** In Signals card: show `SHORT` signal alongside existing signals; badge colour red
- [ ] **5.13.3** In Settings page: expose `shorts.enabled` toggle with a clear warning label ("enables Binance Margin borrowing")
- [ ] **5.13.4** In Portfolio page: PnL for short positions renders correctly (negative when price rises)

---

## Completion Checklist

- [ ] All tasks in 5.1–5.10 done
- [ ] Full test suite passes: `.venv-test/bin/python -m pytest tests/ -v`
- [ ] No regressions in existing strategies: `tests/test_smart_hodler.py`, `tests/test_mean_reversion.py`, `tests/test_risk_engine.py`, `tests/test_paper_executor.py`
- [ ] Smoke test: BearGuard backtest on BTCUSDT 2022-Q1 produces valid results
- [ ] `shorts.enabled = false` in config → full test suite still passes, BearGuard generates no orders
- [ ] 5.11 wired and manually verified
- [ ] Strategy spec reviewed: [strategy_bear_guard.md](strategy_bear_guard.md)
