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
- [ ] Run migration on production DB

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

- [ ] Immediate fix: redeploy and let reconciliation auto-repair BIOUSDT (or manually delete stale `PositionRecord` and set `StrategyStateRecord.state` to `flat`)
- [x] Code fix: make exchange reconciliation auto-repair when local position exists but Binance has zero balance for that asset (upgrade Check E from advisory to corrective)
- [x] Auto-repair evicts in-memory `_worker_state` cache so next tick rebuilds from corrected DB
- [x] Exchange reconciliation now returns `repairs` list and publishes `RECONCILIATION_REPAIRED` events
