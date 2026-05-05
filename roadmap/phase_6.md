# Phase 6 — Order & Asset Reconciliation (Full Account Control)

> **Goal:** Garantir que o GG nunca perca o controle de ordens ou ativos — toda ordem enviada à Binance é rastreada até resolução, e o estado local é periodicamente sincronizado com a exchange.
> **Motivação:** Ordens estão escapando do controle — ativos somem do GG mas continuam na exchange, ou são vendidos na exchange e continuam aparecendo no GG. A reconciliação atual é insuficiente.
> **Status:** Planning

---

## Diagnóstico: Gaps da reconciliação atual

| Problema | Causa raiz |
|----------|-----------|
| Ativo vendido na exchange mas continua no GG | Stop order executada na Binance sem callback; GG nunca registrou o fill |
| Ativo some do GG mas continua na exchange | Crash entre `place_order()` e `pm.open_position()`; ordem executou mas DB não registrou |
| Posição fantasma após restart | `order_records` grava status PENDING mas nunca atualiza para FILLED se o worker morre |
| Stop orders órfãs na exchange | GG coloca stop, fecha posição por trailing, mas não cancela o stop na exchange |
| Balance drift silencioso | Reconciliação de USDT é advisory — loga warning mas não repara — **parcialmente resolvido: sync_exchange_balances (2 min) + auto-repair de drift** |

---

## Overview

```
Phase 6 dependency order:

  6.1 (exchange query layer) ──► 6.2 (order ledger migration)
                              ──► 6.3 (crash-safe order flow)
  6.1 + 6.2                  ──► 6.4 (open order sync)
  6.1 + 6.2                  ──► 6.5 (fill recovery)
  6.1                        ──► 6.6 (position reconciliation v2)
  6.4 + 6.5 + 6.6            ──► 6.7 (reconciliation orchestrator)
  6.7                        ──► 6.8 (alerting & dashboard)
  *                          ──► 6.9 (tests)
```

Princípios:
- **Exchange é a fonte de verdade** — em caso de divergência, o estado da exchange vence
- **Write-ahead** — toda ordem é registrada no DB *antes* de ser enviada à exchange
- **Idempotência** — fill recovery pode rodar N vezes sem duplicar posições ou trades
- **Auto-repair com audit trail** — reparos são aplicados e logados, não apenas alertados

---

## 6.1 — Exchange Query Layer (`core/execution/binance.py`)

> **Prerequisite for:** tudo que consulta a exchange.
> Novos métodos no `BinanceExecutor` para consultar ordens e trades.

**Referência API:**
- Spot: `GET /api/v3/openOrders`, `GET /api/v3/allOrders`, `GET /api/v3/myTrades`
- Margin: `GET /sapi/v1/margin/openOrders`, `GET /sapi/v1/margin/allOrders`, `GET /sapi/v1/margin/myTrades`
- Futures (referência): `GET /fapi/v1/openOrders`

- [ ] **6.1.1** `get_open_orders(symbol: str | None = None) -> list[dict]` — retorna todas as ordens abertas na conta spot. Sem symbol → todas. Campos mínimos: `orderId`, `symbol`, `side`, `type`, `status`, `origQty`, `executedQty`, `price`, `stopPrice`, `time`, `updateTime`
- [ ] **6.1.2** `get_all_orders(symbol: str, start_time: int | None = None, limit: int = 500) -> list[dict]` — histórico de ordens para um symbol (spot). Usado para fill recovery
- [ ] **6.1.3** `get_my_trades(symbol: str, start_time: int | None = None, limit: int = 500) -> list[dict]` — trades executados na conta. Campos: `id`, `orderId`, `symbol`, `side`, `price`, `qty`, `commission`, `commissionAsset`, `time`
- [ ] **6.1.4** `get_margin_open_orders(symbol: str | None = None) -> list[dict]` — mesma interface do 6.1.1 para margin
- [ ] **6.1.5** `get_order_status(symbol: str, order_id: int) -> dict` — consulta status de uma ordem específica (`GET /api/v3/order`)
- [ ] **6.1.6** `cancel_order(symbol: str, order_id: int) -> dict` — cancela ordem aberta (`DELETE /api/v3/order`); retorna status final. Se a ordem já foi executada ou cancelada, trata graciosamente (não lança erro)

---

## 6.2 — Order Ledger & Migration (`db/models.py`)

> Estende o `OrderRecord` existente para funcionar como ledger de reconciliação.
> Depende de: nada (schema only).

O `order_records` atual já tem os campos essenciais, mas faltam:
- `intent` — o que o GG pretendia fazer (OPEN_LONG, CLOSE_LONG, OPEN_SHORT, CLOSE_SHORT, STOP_LOSS, etc.)
- `reconciled_at` — timestamp da última vez que a ordem foi verificada contra a exchange
- `reconciliation_status` — PENDING_SYNC, SYNCED, ORPHAN, RECOVERED

- [ ] **6.2.1** Adicionar enum `OrderIntent` no `core/models.py`: `OPEN_LONG`, `CLOSE_LONG`, `OPEN_SHORT`, `CLOSE_SHORT`, `STOP_LOSS`, `SCALE_IN`, `REDUCE`
- [ ] **6.2.2** Adicionar coluna `intent VARCHAR(20)` ao `OrderRecord` — nullable para ordens históricas
- [ ] **6.2.3** Adicionar coluna `reconciled_at TIMESTAMP` ao `OrderRecord` — última verificação com exchange
- [ ] **6.2.4** Adicionar coluna `reconciliation_status VARCHAR(20) DEFAULT 'pending_sync'` ao `OrderRecord`
- [ ] **6.2.5** Adicionar índice `ix_order_records_reconciliation` em `(reconciliation_status, created_at)` para queries de sync
- [ ] **6.2.6** Adicionar índice `ix_order_records_exchange_order_id` em `(exchange_order_id)` para lookups rápidos
- [ ] **6.2.7** Gerar migration Alembic: `alembic revision --autogenerate -m "add order reconciliation columns"`
- [ ] **6.2.8** Aplicar e verificar: `alembic upgrade head`

---

## 6.3 — Crash-Safe Order Flow (`core/execution/binance.py`)

> **Problema:** se o processo morre entre enviar a ordem e registrar o fill, a posição some.
> **Solução:** write-ahead — grava a ordem no DB *antes* de enviá-la à exchange.
> Depende de: 6.2.

- [ ] **6.3.1** Refactor `_place_and_fill()`: antes de chamar a API da Binance, criar `OrderRecord` com `status=PENDING`, `intent=<intent>`, `reconciliation_status=pending_sync` e fazer `session.commit()`
- [ ] **6.3.2** Após o fill, atualizar o `OrderRecord` existente (não criar um novo): `status=FILLED`, `exchange_order_id`, `filled_amount`, `avg_fill_price`, `fee_usdt`, `reconciliation_status=synced`
- [ ] **6.3.3** Em caso de erro/rejeição: atualizar `status=REJECTED` com `exchange_status` contendo a mensagem de erro
- [ ] **6.3.4** Refactor `_place_margin_order()`: mesma lógica write-ahead para ordens margin
- [ ] **6.3.5** Refactor `_place_stop_order()` e `_place_margin_stop_order()`: gravar stop order no DB com `intent=STOP_LOSS` antes de colocá-la na exchange
- [ ] **6.3.6** Garantir que o `exchange_order_id` é sempre persistido — é a chave de reconciliação

---

## 6.4 — Open Order Sync (`core/tasks/_reconciliation.py`)

> Sincroniza ordens abertas na exchange com o DB local.
> Detecta: stop orders executadas que o GG não viu, ordens órfãs, ordens canceladas pela exchange.
> Depende de: 6.1, 6.2.

- [ ] **6.4.1** Nova função `_sync_open_orders(session, executor, symbols)`:
  - Busca todas as open orders na exchange (`get_open_orders()`)
  - Para cada order, verifica se existe `OrderRecord` com mesmo `exchange_order_id`
  - **Ordem conhecida:** atualiza status se mudou (ex: parcialmente filled)
  - **Ordem órfã (externa):** ordem na exchange que o GG não colocou (manual, outro bot, API direta) ou perdeu tracking. Cria `OrderRecord` com `reconciliation_status=orphan`, `intent=NULL`. Destacada no frontend (ver 6.8.6)
- [ ] **6.4.2** Detectar stop orders executadas: buscar `OrderRecord` onde `intent=STOP_LOSS` e `status=PENDING`, consultar `get_order_status()` na exchange
  - Se `status=FILLED` na exchange → disparar fill recovery (6.5)
  - Se `status=CANCELED` → atualizar para CANCELLED localmente
  - Se não existe na exchange → marcar como `EXPIRED`
- [ ] **6.4.3** Detectar stop orders órfãs na exchange: open orders na exchange com tipo `STOP_LOSS_LIMIT` que não correspondem a nenhuma posição aberta no GG → cancelar via `cancel_order()` (com safety check)
- [ ] **6.4.4** Atualizar `reconciled_at` e `reconciliation_status` em cada `OrderRecord` verificado

---

## 6.5 — Fill Recovery (`core/tasks/_reconciliation.py`)

> Recupera fills que o GG perdeu (crash, timeout, stop executada sem callback).
> **Este é o componente mais crítico** — resolve o problema de "ativo some do GG" e "ativo vendido mas continua no GG".
> Depende de: 6.1, 6.2, 6.3.

- [ ] **6.5.1** Nova função `_recover_pending_orders(session, executor)`:
  - Query: `OrderRecord` onde `status=PENDING` e `exchange_order_id IS NOT NULL` e `created_at < now() - 2 minutes`
  - Para cada uma, consultar `get_order_status()` na exchange
  - Se `FILLED` → executar recovery (6.5.2)
  - Se `CANCELED` / `EXPIRED` → atualizar status localmente
  - Se `NEW` (ainda aberta) → noop, será processada no próximo ciclo
- [ ] **6.5.2** `_apply_fill_recovery(session, order_record, exchange_data)`:
  - Determinar a ação baseada no `intent`:
    - `OPEN_LONG` / `OPEN_SHORT`: chamar `pm.open_position()` com os dados do fill
    - `CLOSE_LONG` / `CLOSE_SHORT`: chamar `pm.close_position()` + deletar `PositionRecord`
    - `STOP_LOSS`: mesma lógica de close
    - `REDUCE` / `SCALE_IN`: chamar `pm.reduce_position()` / `pm.open_position()` (parcial)
  - Atualizar `OrderRecord`: `status=FILLED`, fills, fees
  - **Idempotência**: verificar se a posição/trade já existe antes de criar (checar por `exchange_order_id` em trades)
- [ ] **6.5.3** `_recover_pending_orders_without_id(session, executor)`:
  - Query: `OrderRecord` onde `status=PENDING` e `exchange_order_id IS NULL` e `created_at < now() - 5 minutes`
  - O GG crashou *antes* de receber o response da exchange
  - Buscar `get_all_orders(symbol, start_time=order.created_at)` e tentar match por (symbol, side, qty, timestamp ±30s)
  - Se match encontrado → associar `exchange_order_id` e processar como 6.5.1
  - Se nenhum match → marcar como `LOST` (a ordem provavelmente nunca chegou à exchange)
- [ ] **6.5.4** Guard de duplicação: antes de aplicar qualquer recovery, verificar se já existe `TradeRecord` referenciando o mesmo `exchange_order_id`

---

## 6.6 — Position Reconciliation v2 (`core/tasks/_reconciliation.py`)

> Substitui os Checks D e E atuais por uma reconciliação completa com auto-repair.
> Depende de: 6.1.

- [ ] **6.6.1** Refactor `_reconcile_with_exchange()` → `_reconcile_positions_with_exchange()`:
  - **Asset no GG, zero na exchange** (já existe): auto-repair — deletar position, reset state, **registrar TradeRecord de emergência** com `exit_reason=reconciliation_force_close` para não perder o tracking de PnL
  - **Asset na exchange, zero no GG** (já existe como alert): tentar recovery automático — buscar `get_my_trades(symbol)` recentes, identificar a ordem de compra, reconstruir `PositionRecord` com preço de entrada real
  - **Size mismatch** (novo): position no GG com size diferente da exchange — ajustar `PositionRecord.size` para match da exchange, logar a diferença
- [x] **6.6.2** USDT balance reconciliation melhorada:
  - Calcular USDT esperado: `sum(strategy_state.usdt_balance)` + `sum(positions * current_price)` ≈ `exchange_total_equity`
  - Se drift > threshold configurável → ajustar `usdt_balance` na strategy state para match
  - **Implementado:** `sync_exchange_balances` task (Celery Beat a cada 2 min) + auto-repair proporcional + recovery sync on startup
- [ ] **6.6.3** **Margin debt check** (novo para shorts): consultar `GET /sapi/v1/margin/account` e verificar que `borrowed` amounts correspondem a short positions abertas no GG
- [ ] **6.6.4** Toda reparação gera um `ReconciliationEvent` com: timestamp, tipo de reparo, valores antes/depois, exchange_data de referência

---

## 6.7 — Reconciliation Orchestrator (`core/tasks/_reconciliation.py`)

> Unifica todos os checks em um fluxo ordenado e configurável.
> Depende de: 6.4, 6.5, 6.6.

- [ ] **6.7.1** Novo fluxo de `run_reconciliation()`:
  ```
  1. DB consistency checks (existente — Checks A, B, C)
  2. Recover pending orders (6.5) — resolve ordens perdidas ANTES de comparar posições
  3. Sync open orders (6.4) — detecta stops executadas e ordens órfãs
  4. Position reconciliation v2 (6.6) — compara estado final com exchange
  5. Publish summary + alerts
  ```
- [ ] **6.7.2** `ReconciliationConfig` Pydantic model em `core/config.py`, persistido via `app_configs` (namespace `"reconciliation"`) — mesmo padrão de `RiskConfig`, `ExecutionConfig`, etc.:
  ```python
  class ReconciliationConfig(BaseModel):
      enabled: bool = True
      interval_minutes: int = 2
      auto_repair: bool = True
      force_close_orphans: bool = True
      recover_untracked: bool = True
      size_mismatch_threshold: Decimal = Decimal("0.001")
      balance_drift_threshold: Decimal = Decimal("1.0")
  ```
  - Adicionar `reconciliation: ReconciliationConfig` ao `Settings`
  - Carregar via `_load_app_config("reconciliation")` (padrão existente)
  - Seed defaults com `save_app_config("reconciliation", ...)` se namespace não existir
  - Celery Beat interval lê `interval_minutes` do DB (não mais hardcoded)
  - Expor no settings panel do frontend para ajuste em runtime
- [ ] **6.7.3** Cada step do orchestrator retorna um resultado estruturado; o summary final agrega todos
- [ ] **6.7.4** Startup recovery: na inicialização do worker, rodar fill recovery (6.5) antes de qualquer tick — garante que ordens perdidas no último crash são recuperadas imediatamente

---

## 6.8 — Alerting & Dashboard

> Visibilidade completa do estado de reconciliação.
> Depende de: 6.7.

- [ ] **6.8.1** Novos `EventType`s: `FILL_RECOVERED`, `ORDER_ORPHAN_DETECTED`, `POSITION_FORCE_CLOSED`, `POSITION_RECONSTRUCTED`, `STOP_ORDER_SYNCED`
- [ ] **6.8.2** Alert escalation por severidade:
  - **INFO**: sync ok, stop order status atualizado
  - **WARNING**: size mismatch corrigido, balance drift ajustado
  - **CRITICAL**: posição force-closed, asset não rastreado detectado, fill recovery executado
- [ ] **6.8.3** Telegram alert para eventos CRITICAL (integração com alerter existente)
- [ ] **6.8.4** API endpoint `GET /api/reconciliation/status` — último resultado de reconciliação, ordens pending, divergências ativas
- [ ] **6.8.5** API endpoint `GET /api/reconciliation/history` — histórico de reparos com filtro por tipo e severidade
- [ ] **6.8.6** API endpoint `GET /api/exchange/orders` — lista **todas** as ordens da conta na exchange (abertas + recentes do DB), cada uma com campo `source`:
  - `source: "gg"` — ordem criada pelo GoldenGibbon (tem `intent` preenchido)
  - `source: "orphan"` — ordem detectada na exchange sem correspondência no GG
  - Response inclui: `exchange_order_id`, `symbol`, `side`, `type`, `status`, `origQty`, `executedQty`, `price`, `stopPrice`, `time`, `source`, `intent`
- [ ] **6.8.7** Frontend — página/tab **Exchange Orders**:
  - Tabela com todas as ordens (GG + órfãs) ordenadas por timestamp
  - Ordens órfãs destacadas com badge visual (ex: tag "ORPHAN" em amarelo/laranja) para identificação imediata
  - Filtros: por source (GG / Orphan / All), por symbol, por status (open / filled / cancelled)
  - Ordens GG mostram o `intent` (OPEN_LONG, STOP_LOSS, etc.); órfãs mostram "—"
- [ ] **6.8.8** Frontend — no painel de posições existente, indicador de ordens abertas associadas (ex: "1 stop order ativa") com link para a tab de Exchange Orders

---

## 6.9 — Tests

> Depende de: tudo.

### Exchange Query Layer (6.1)
- [ ] **6.9.1** Test: `get_open_orders()` retorna lista formatada corretamente (mock da API)
- [ ] **6.9.2** Test: `get_open_orders()` sem symbol retorna todas
- [ ] **6.9.3** Test: `get_order_status()` para ordem FILLED, NEW, CANCELED
- [ ] **6.9.4** Test: `cancel_order()` quando ordem já foi executada → retorna graciosamente

### Crash-Safe Order Flow (6.3)
- [ ] **6.9.5** Test: `_place_and_fill()` grava OrderRecord PENDING antes de chamar API
- [ ] **6.9.6** Test: se API falha, OrderRecord fica REJECTED (não PENDING infinito)
- [ ] **6.9.7** Test: `exchange_order_id` é persistido após fill

### Open Order Sync (6.4)
- [ ] **6.9.8** Test: stop order executada na exchange → detectada e marcada para recovery
- [ ] **6.9.9** Test: ordem órfã na exchange → criada como ORPHAN no DB
- [ ] **6.9.10** Test: stop order sem posição correspondente → cancelada

### Fill Recovery (6.5)
- [ ] **6.9.11** Test: ordem PENDING com fill na exchange → posição criada no GG
- [ ] **6.9.12** Test: recovery idempotente — rodar duas vezes não duplica posição
- [ ] **6.9.13** Test: ordem PENDING sem exchange_order_id → match por heurística (symbol, side, qty, time)
- [ ] **6.9.14** Test: ordem PENDING sem match na exchange → marcada como LOST

### Position Reconciliation v2 (6.6)
- [ ] **6.9.15** Test: asset na exchange sem posição no GG → posição reconstruída
- [ ] **6.9.16** Test: posição no GG sem asset na exchange → force close com TradeRecord
- [ ] **6.9.17** Test: size mismatch → PositionRecord ajustado
- [ ] **6.9.18** Test: margin debt check para shorts

### Orchestrator (6.7)
- [ ] **6.9.19** Test: fluxo completo — pending recovery → open order sync → position reconciliation
- [ ] **6.9.20** Test: startup recovery roda antes do primeiro tick

### Exchange Orders API & Frontend (6.8)
- [ ] **6.9.21** Test: `GET /api/exchange/orders` retorna ordens GG com `source="gg"` e intent preenchido
- [ ] **6.9.22** Test: `GET /api/exchange/orders` retorna ordens órfãs com `source="orphan"` e intent nulo
- [ ] **6.9.23** Test: filtro por source funciona (`?source=orphan` retorna só órfãs)

---

## Completion Checklist

- [ ] 6.1–6.8 implementados
- [ ] Full test suite passa: `.venv-test/bin/python -m pytest tests/ -v`
- [ ] Sem regressões nos testes existentes
- [ ] Reconciliação roda a cada 2 minutos sem erros
- [ ] Cenários validados manualmente:
  - [ ] Simular crash após place_order → fill recovery reconstrói posição
  - [ ] Simular stop executada na exchange → GG detecta e fecha posição
  - [ ] Simular asset na exchange sem tracking → GG reconstrói posição
  - [ ] Simular posição fantasma → GG force-close e registra trade
- [ ] Alerts de Telegram disparam para eventos CRITICAL
- [ ] API de reconciliation status funcional
