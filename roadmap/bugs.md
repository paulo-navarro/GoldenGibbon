# Bugs

## BUG-001: Numeric overflow on candle insert for high-volume tokens

**Status:** Open
**Reported:** 2026-04-29
**Affected symbols:** PEPEUSDT (and potentially other meme coins with extreme volume)

### Error

```
❌ ERROR in tick:mean_reversion:PEPEUSDT
(psycopg2.errors.NumericValueOutOfRange) numeric field overflow
DETAIL: A field with precision 20, scale 8 must round to an absolute value less than 10^12.

[SQL: INSERT INTO candles (symbol, timeframe, open_time, open, high, low, close, volume, quote_volume, trades_count) VALUES (%(symbol_m0)s, ...
```

### Root cause

The `candles` table defines `volume` and `quote_volume` as `Numeric(20, 8)`, which allows at most 12 integer digits (max ~999,999,999,999). Meme coins like PEPE have token volumes that routinely exceed 10^12, causing the insert to fail.

### Affected code

- **Schema definition:** `db/models.py:48-49` — `CandleRecord.volume` and `CandleRecord.quote_volume` are `Numeric(20, 8)`
- **Initial migration:** `alembic/versions/2d84a25288a5_initial_schema.py:63-64`
- **Insert path:** `core/data/loader.py` → `DataLoader` inserts candles fetched from Binance into the DB

### Fix

- [x] Create an alembic migration to `ALTER COLUMN` `volume` and `quote_volume` from `Numeric(20, 8)` to `Numeric(30, 8)` (supports up to 10^22 integer digits)
- [x] Update `db/models.py` `CandleRecord` to match
- [x] Run migration on production DB

---

## BUG-002: Tick errors not appearing in application logs

**Status:** Fixed
**Reported:** 2026-04-29

### Symptom

Tick errors (e.g. BUG-001) are delivered to Telegram via the alerter but do not appear in the application logs, making debugging harder.

### Root cause

Two issues combined:

1. **Celery hijacks the root logger** — `worker_hijack_root_logger` defaults to `True`, so Celery reconfigures the root logger during its own setup phase, potentially overriding our structlog handlers installed via `worker_process_init`.
2. **Production containers had no logs volume** — `celery-worker-prod`, `celery-beat-prod`, and `api-prod` had no `./logs:/app/logs` volume mount, so `logs/trading.log` written inside the container was invisible from the host and lost on restart.

### Fix

- [x] Set `worker_hijack_root_logger=False` in Celery config (`core/celery_app.py`) so structlog has full control
- [x] Prod containers write to `/app/logs` inside the container (created by Dockerfile with correct permissions) and to stderr (accessible via `docker logs`). Volume mounts reverted — they caused permission failures since Docker creates host dirs as root but the container runs as `appuser`.

---

## BUG-003: Stale position after Binance sell — repeated SELL alerts and UI ghost trade

**Status:** Fixed (code) — needs manual cleanup for BIOUSDT
**Reported:** 2026-04-29
**Affected symbols:** BIOUSDT (smart_hodler)

### Symptom

Binance executed a SELL for BIOUSDT long ago, but the system keeps sending Telegram alerts and the UI keeps showing the position as open:

```
🔴 SELL BIOUSDT
Size: 436.20000000 @ 0.0329
Strategy: smart_hodler
PnL: -0.4216745400000000 USDT
```

### Root cause (likely)

The sell was executed on Binance, but the local DB state was never properly updated. This means `PositionRecord` and `StrategyStateRecord` still reflect an open position. On every subsequent tick:

1. `_get_or_create_components` rebuilds from DB state and sees an open position (`core/tasks/__init__.py:292`)
2. The strategy evaluates and generates a SELL signal
3. The executor attempts the sell (paper mode succeeds unconditionally; live mode may silently fail since Binance has no balance)
4. `_send_tick_alerts` fires the Telegram fill alert (`core/tasks/__init__.py:619-631`)
5. `_persist_tick_results` writes the same stale state back to DB (`core/tasks/__init__.py:488-513`)

The most likely reason the DB state was never cleaned up: the persist step (`_persist_tick_results` + `session.commit()`) failed or was interrupted after the original sell executed on Binance but before the local position was deleted from the DB.

### Why reconciliation doesn't fix it

- **DB reconciliation** (`_reconcile_pair`, Check A): only checks whether `StrategyStateRecord.state` and `PositionRecord` are consistent _with each other_. If both say "open position", it sees no mismatch — even though Binance already sold.
- **Exchange reconciliation** (`_reconcile_with_exchange`, Check E): detects the position size mismatch but is **advisory only** — it logs a warning and publishes an event, but performs **no auto-repair** (`core/tasks/__init__.py:1599`).

### Affected code

- **Alert loop:** `core/tasks/__init__.py:619-631` — `_send_tick_alerts` fires on every tick that produces an `execution_result`
- **Persist loop:** `core/tasks/__init__.py:488-513` — `_persist_tick_results` writes stale position back
- **Exchange reconciliation (advisory only):** `core/tasks/__init__.py:1580-1716` — detects mismatch but does not repair
- **UI reads from DB:** `api/routes/portfolio.py:78-80` — queries `PositionRecord` table, which still has the stale row
- **State rebuild:** `core/tasks/__init__.py:292` — `_get_or_create_components` restores from DB including stale position

### Fix

- [x] Immediate fix: redeploy and let reconciliation auto-repair BIOUSDT (or manually delete stale `PositionRecord` and set `StrategyStateRecord.state` to `flat`)
- [x] Code fix: make exchange reconciliation auto-repair when local position exists but Binance has zero balance for that asset (upgrade Check E from advisory to corrective)
- [x] Auto-repair evicts in-memory `_worker_state` cache so next tick rebuilds from corrected DB
- [x] Exchange reconciliation now returns `repairs` list and publishes `RECONCILIATION_REPAIRED` events

---

## BUG-004: `Portfolio.positions_value` uses entry price instead of mark-to-market

**Status:** Fixed
**Reported:** 2026-05-01

### Symptom

`DeprecationWarning: datetime.datetime.utcnow() is deprecated` appears in test output. Will raise an error in Python 3.14.

### Affected code

- `core/models.py` — `Order.created_at` field default: `Field(default_factory=datetime.utcnow)`
- `core/data/loader.py` (×2) — `end_date = datetime.utcnow()`

### Fix

- [x] Replace all `datetime.utcnow()` with `datetime.now(timezone.utc)` in the affected files
- [x] Normalise naive datetimes passed to `_fetch_in_chunks` (including `last_candle_time` from mocked candles) to UTC-aware to avoid offset-naive comparison errors

### Symptom

`Portfolio.positions_value` (a `@property` on the Pydantic model) calculated position value using `entry_price` instead of the current market price. The `KillSwitch` reads `portfolio.equity` to decide whether to halt trading — but `equity` is only correct after `update_equity(current_prices)` is called. Any codepath that skips that call and falls back to `positions_value` would see stale cost-basis values, meaning a position deep in the red would not trigger the global drawdown stop.

Additionally, `backtest/metrics.py` used `positions_value` as a fallback for `initial_capital` when the equity curve was empty — incorrectly inflating the initial capital figure.

### Affected code

- `core/models.py` — `Portfolio.positions_value` property
- `core/backtest/metrics.py` — fallback `usdt_balance + positions_value`

### Fix

- [x] Rename `Portfolio.positions_value` → `Portfolio.positions_cost_basis` with a clear docstring marking it as cost basis (not MTM)
- [x] Fix `backtest/metrics.py` fallback: use `usdt_balance` only (when equity curve is empty, no positions were open)
- [x] Update `tests/test_models.py` and `frontend/src/pages/PortfolioPage.tsx` to use the renamed property

---

## BUG-005: `datetime.utcnow()` is deprecated and will break on Python 3.14

**Status:** Fixed
**Reported:** 2026-05-01

---

## BUG-006: Sharpe ratio uses population variance instead of sample variance

**Status:** Fixed
**Reported:** 2026-05-01

### Symptom

Sharpe ratio is overestimated, especially for short backtests (< 200 candles). The formula divides by `len(returns)` (population variance) instead of `len(returns) - 1` (sample variance / Bessel's correction), which is the standard for financial time series.

### Affected code

- `core/backtest/metrics.py:192` — `variance = sum(...) / len(returns)`

### Fix

- [x] Change divisor to `len(returns) - 1` and raise the minimum snapshot guard from `< 2` to `< 3` to prevent division-by-zero with Bessel's correction

---

## BUG-007: `_closed_candles.pop()` evicts a random entry, risking duplicate tick triggers

**Status:** Fixed
**Reported:** 2026-05-01

### Symptom

After a WebSocket reconnect, a candle that was recently processed could be re-triggered if its key was the one randomly evicted from the dedup set during the trim. `set.pop()` is unordered in CPython. Additionally, the set is in-memory only — a worker restart clears it entirely, allowing any candle to be re-triggered once.

### Affected code

- `core/data/stream_runner.py:83-88` — trim loop using `set.pop()`

### Fix

- [x] Replace in-memory `set` with a size-bounded `collections.OrderedDict` so the oldest entry is always evicted first (FIFO / `popitem(last=False)`), eliminating the random eviction risk

---

## BUG-008: `DataLoader._fetch_in_chunks` silently skips failed chunks

**Status:** Fixed
**Reported:** 2026-05-01

### Symptom

When a Binance API call fails mid-fetch, the loader logs an error but continues with the next chunk. The caller receives a partial candle list with no indication of how many candles are missing. Backtests running on this data produce results that silently cover a shorter window than requested.

### Affected code

- `core/data/loader.py` — `_fetch_in_chunks` `except` block advances `current_start = current_end` and continues

### Fix

- [x] Track failed chunk date ranges in `failed_chunks` list
- [x] After the loop, emit a `logger.warning` listing all failed ranges so the caller sees a clear indication that data may have gaps

---

## BUG-009: `Trade.strategy` default is hardcoded to `"smart_hodler"`

**Status:** Fixed
**Reported:** 2026-05-01

### Symptom

`Trade` Pydantic model has `strategy: str = "smart_hodler"`. If the `strategy` field is not explicitly passed when constructing a `Trade`, trades from Mean Reversion (or any future strategy) are recorded in the DB and displayed in the UI as `smart_hodler`. Metrics and filtering by strategy name are silently wrong.

### Affected code

- `core/models.py` — `Trade` model field definition

### Fix

- [x] Remove default — `strategy` is now a required field, forcing all callers to supply it explicitly
- [x] Updated `tests/test_models.py` and `tests/test_database.py` to pass `strategy` explicitly
