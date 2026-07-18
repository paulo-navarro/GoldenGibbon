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

---

## BUG-010: Stop order cancelled before sell confirmed in `_execute_close`

**Status:** Fixed
**Reported:** 2026-05-01

### Symptom

`_execute_close` cancels the exchange stop order first, then places the sell order. If the sell fails, the position is left unprotected (no stop order, no exit).

### Affected code

- `core/execution/binance.py` — `_execute_close` method

### Fix

- [x] Moved stop order cancellation to after the sell order is confirmed filled

---

## BUG-011: Timezone mismatch in `_count_candles_held()`

**Status:** Fixed
**Reported:** 2026-05-01

### Symptom

`current_time` from `market_data.candles.index[-1]` may be timezone-naive, while `position.entry_time` is UTC-aware. Subtracting mismatched datetimes raises `TypeError` in live trading.

### Affected code

- `core/risk/__init__.py` — `_count_candles_held()` method

### Fix

- [x] Normalise both timestamps to naive UTC before subtraction

---

## BUG-012: `_execute_reduce` sends full position qty instead of fraction

**Status:** Fixed
**Reported:** 2026-05-01

### Symptom

On partial exits (SELL_HALF), `_execute_reduce` sent `decision.size` (full position) to the exchange instead of `size × sell_fraction`. This sold the entire position on-exchange while the portfolio manager only removed 50%, causing a desync.

### Affected code

- `core/execution/binance.py` — `_execute_reduce` method

### Fix

- [x] Compute `sell_qty = decision.size * sell_fraction` and override `decision.size` before calling `_place_and_fill`

---

## BUG-013: Zero close price not rejected by Candle validator

**Status:** Fixed
**Reported:** 2026-05-01

### Symptom

`ensure_positive` validator allows `value == 0`. A zero close price causes division-by-zero in PnL calculations and position sizing.

### Affected code

- `core/models.py` — `Candle.ensure_positive` field validator

### Fix

- [x] Split validator: price fields (`open`, `high`, `low`, `close`) now require strictly positive (`> 0`); volume allows zero (valid for low-liquidity candles)

---

## BUG-014: Non-atomic buy — exchange order fills but PM rejects position, stranding assets

**Status:** Fixed
**Reported:** 2026-05-08
**Severity:** Critical (real money loss)
**Affected symbols:** CHZUSDT, PUMPUSDT (smart_hodler) — 435 CHZ + 9674 PUMP stranded on exchange

### Symptom

The bot bought assets on Binance but never created a local position. On every subsequent tick the system sees no position, the strategy generates a new BUY signal, and buys again — compounding the problem. Eventually the kill switch triggers with a false drawdown (PM equity is tiny because it never tracked the positions), blocking all trading across all pairs.

**Production incident 2026-05-08 04:44 UTC:**
- 4 filled buy orders (2× CHZ, 2× PUMP) totalling ~$38.70 of assets on the exchange
- 0 positions in the DB
- Kill switch falsely triggered at 41% drawdown across all 102 strategy pairs
- Assets sat unmanaged on the exchange with no stop-loss protection

### Root cause

`_execute_open` in `core/execution/_actions.py` is **non-atomic**: it places a real market order on Binance first, then calls `pm.open_position()` to track it locally. `pm.open_position()` raises `ValueError("Insufficient funds")` when the PM's internal `usdt_balance` is lower than the position cost — even though the exchange already accepted and filled the order.

The PM balance diverges from reality because:

1. **Position sizing uses exchange capital:** The risk engine computes order size from `initial_capital` (= real exchange USDT ÷ `max_concurrent_positions`), e.g. ~$12.88 per slot.
2. **PM balance tracks historical P&L:** The portfolio manager's `usdt_balance` is recovered from the DB at ~$0.40 — accumulated losses from paper-like P&L tracking that drifted far from the real exchange balance.
3. **No pre-flight check:** Nothing validates that the PM can afford the position before the exchange order is placed.

When `pm.open_position()` raises, the exception propagates through `execute()` → `run_single_strategy_tick` where it's caught by the outer `try/except`. The tick returns an error, **persistence never runs** (step 7 is skipped), and the position exists only on the exchange.

### Sequence of events

```
1. risk.evaluate → OPEN, size=290 CHZ (~$12.88)
2. _place_and_fill → BUY 290 CHZ on Binance → FILLED ✓
3. pm.open_position() → ValueError("Insufficient funds: need 12.88, have 0.41") ✗
4. Exception propagates → single_tick: failed
5. Persistence skipped → position NOT in DB
6. Next tick → PM has no position → BUY again → repeat
7. After 2 cycles → kill switch triggers on false 41% drawdown → blocks ALL pairs
```

### Three sub-bugs

1. **Non-atomic execution** (`_actions.py:91-155`): Exchange order placed before PM balance validation. If PM rejects, the asset is stranded.

2. **PM balance ≠ exchange balance** (`_state.py:243-305`): `_sync_balance_from_exchange` adjusts proportionally across all strategy pairs, but the per-pair tracked balance drifts far from the per-slot allocated capital. The risk engine sizes orders against allocated capital while the PM validates against tracked balance.

3. **Kill switch false drawdown** (`kill_switch.py:84-142`): Peak equity is based on the PM's historical high-water mark. When PM equity is $0.40 and peak was $0.80, the kill switch computes 50% drawdown even though real exchange equity hasn't changed.

### Affected code

- **`core/execution/_actions.py:91-155`** — `_execute_open`: places exchange order then calls `pm.open_position()` which can raise
- **`core/execution/_actions.py:157-204`** — `_execute_scale_in`: same pattern
- **`core/execution/binance.py:256-304`** — `execute()`: no try/except around action handlers, exceptions propagate
- **`core/portfolio/__init__.py:112-124`** — `open_position`: raises `ValueError` on insufficient funds
- **`core/tasks/_tick.py:586-618`** — tick pipeline: execution at step 5, persistence at step 7 — if step 5 crashes, step 7 is skipped
- **`core/tasks/_state.py:243-305`** — `_sync_balance_from_exchange`: proportional correction doesn't fix the root divergence
- **`core/risk/kill_switch.py:84-142`** — `check()`: drawdown computed from PM equity which is disconnected from reality

### Fix plan

- [x] **1. Pre-flight balance sync in live mode**
  - [x] Added `_ensure_balance_for_cost()` helper in `_actions.py` that tops up PM balance when an exchange fill exceeds it
  - [x] Called in `_execute_open` after fill, before `pm.open_position()` — PM balance is forced to cover the trade
  - [x] Applied same fix to `_execute_scale_in`
- [x] **2. Force-track on fill (safety net)**
  - [x] `_ensure_balance_for_cost()` guarantees PM will accept the position after any exchange fill — logs warning with shortfall details
  - [x] Covers both `open_position` and `scale_in` paths
- [x] **3. Kill switch equity fix**
  - [x] On state recovery in live mode, reset kill switch `_peak_equity` to 0 so first `check()` re-initialises from current equity
  - [x] Prevents false drawdown triggers caused by stale peak from PM balance drift

---

## BUG-015: Reconciliation force-close fabricates exit price (PnL falso no histórico)

**Status:** Fixed
**Reported:** 2026-07-08 (revisão geral)
**Related:** task 9.3 (phase_9.md)

### Problem

`core/tasks/_reconciliation.py:390` — ao force-closar uma posição órfã:

```python
exit_price = current_price if current_price else rec.entry_price
```

Se o fetch de tickers falhou (engolido silenciosamente pelo `except Exception: ticker_prices = {}` em `_reconciliation.py:330`), o trade é gravado com **exit = entry**, ou seja, PnL ≈ 0 **fabricado** — o preço real de venda na exchange foi outro.

### Evidence (prod)

Os 79 trades live com `exit_reason='reconciliation_force_close'` têm PnL médio de **-0.085%** — suspeito de perto de zero, consistente com exit_price = entry_price em massa. O histórico de trades (base de qualquer análise de estratégia) está parcialmente fabricado.

### Fix

- [x] Novo helper `_resolve_exit_from_exchange()` (`core/tasks/_reconciliation.py`): busca os fills SELL reais via `myTrades` desde `entry_time`, calcula o exit price por VWAP e a comissão em USDT. É a primeira fonte de verdade do force-close.
- [x] Fallback em cascata no force-close: fill real da exchange → ticker → `entry_price`. Quando não há fill real, o trade é gravado com `exit_price_estimated=true` e um log ERROR é emitido (nunca mais um PnL fabricado passa como real).
- [x] Nova coluna `TradeRecord.exit_price_estimated` (`db/models.py` + migration `l7m8n9o0p123`) para marcar registros com preço estimado, permitindo excluí-los de análises de estratégia.
- [x] PnL do force-close agora desconta a fee real (`pnl = (exit - entry) * size - fee`), e o `usdt_balance` do estado credita o proceeds líquido.
- [x] O `except` do fetch de tickers passou a logar ERROR (antes engolia silenciosamente).
- [x] `BinanceExecutor._parse_trade_response` deriva `side` de `isBuyer` (o `myTrades` da Binance não retorna `side`), corrigindo também o caminho de reconstrução de posição.
- [x] Exposto na API: `Trade.exit_price_estimated` (`core/models.py`), mapeado em `orm_to_trade` (`db/utils.py`), e novo query param `exit_price_estimated` (true/false) em `GET /api/trades/` e `/stats`.
- [x] Front: tipo `Trade.exit_price_estimated` (`types/trades.ts`), filtro tri-estado "Exit Price" (All / Real fill only / Estimated only) na TradesPage e ícone ⚠️ (tooltip) na coluna Exit $ para trades estimados.
- [x] Testes: `test_force_close_uses_real_exchange_fill`, `test_force_close_flags_estimated_when_no_fill` (reconciliação) e `test_side_derived_from_isbuyer` (executor).
- [ ] **Pendente (manual):** rodar `alembic upgrade head` no DB de produção.

---

## BUG-016: Force-close sem período de carência (race window)

**Status:** Open
**Reported:** 2026-07-08 (revisão geral)
**Related:** task 9.3 (phase_9.md), BUG-015

### Problem

`reconcile_exchange` (`core/tasks/_reconciliation.py:359+`) força o fechamento local quando `exchange_size ≈ 0` com posição aberta no DB, **sem verificar há quanto tempo a posição existe nem se há ordem em voo**. Janelas de corrida com o sync de 2 min e o tick de 15 min podem fechar posições legítimas recém-abertas. Causa externa plausível também: Binance auto-Earn/conversão de dust zerando o saldo spot enquanto o ativo ainda pertence à conta.

### Fix direction

- Carência mínima (ex: posição tocada nos últimos N minutos → skip + warning).
- Exigir 2 ciclos consecutivos de mismatch antes do force-close (mesma filosofia de histerese do RegimeDetector).
- Verificar ordens abertas do símbolo antes de concluir "órfã".

---

## BUG-017: Cooldown hardcoded e bypass da state machine no stop da exchange

**Status:** Open
**Reported:** 2026-07-08 (revisão geral)

### Problem

`core/tasks/_tick.py:391-392` — quando um stop da exchange fillou entre ticks:

```python
comp.strategy._cooldown_remaining = getattr(comp.strategy, "_cooldown_candles", 16)
comp.strategy._state = StrategyState.COOLDOWN
```

Acessa atributos privados com fallback de **16 candles (4h)**, enquanto a spec do smart_hodler define cooldown de **48h** pós-stop. Se o nome do atributo divergir, o fallback silenciosamente aplica cooldown errado. Setar `_state` direto também pula qualquer lógica da state machine.

### Fix direction

Expor método público na Strategy (ex: `enter_cooldown(reason)`) que usa a config real da estratégia; remover o default mágico.
