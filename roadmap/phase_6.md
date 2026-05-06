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

- [x] **6.1.1** `get_open_orders(symbol: str | None = None) -> list[dict]` — retorna todas as ordens abertas na conta spot. Sem symbol → todas. Campos mínimos: `orderId`, `symbol`, `side`, `type`, `status`, `origQty`, `executedQty`, `price`, `stopPrice`, `time`, `updateTime`
- [x] **6.1.2** `get_all_orders(symbol: str, start_time: int | None = None, limit: int = 500) -> list[dict]` — histórico de ordens para um symbol (spot). Usado para fill recovery
- [x] **6.1.3** `get_my_trades(symbol: str, start_time: int | None = None, limit: int = 500) -> list[dict]` — trades executados na conta. Campos: `id`, `orderId`, `symbol`, `side`, `price`, `qty`, `commission`, `commissionAsset`, `time`
- [x] **6.1.4** `get_margin_open_orders(symbol: str | None = None) -> list[dict]` — mesma interface do 6.1.1 para margin
- [x] **6.1.5** `get_order_status(symbol: str, order_id: int) -> dict` — consulta status de uma ordem específica (`GET /api/v3/order`)
- [x] **6.1.6** `cancel_order(symbol: str, order_id: int) -> dict` — cancela ordem aberta (`DELETE /api/v3/order`); retorna status final. Se a ordem já foi executada ou cancelada, trata graciosamente (não lança erro)

---

## 6.2 — Order Ledger & Migration (`db/models.py`)

> Estende o `OrderRecord` existente para funcionar como ledger de reconciliação.
> Depende de: nada (schema only).

O `order_records` atual já tem os campos essenciais, mas faltam:
- `intent` — o que o GG pretendia fazer (OPEN_LONG, CLOSE_LONG, OPEN_SHORT, CLOSE_SHORT, STOP_LOSS, etc.)
- `reconciled_at` — timestamp da última vez que a ordem foi verificada contra a exchange
- `reconciliation_status` — PENDING_SYNC, SYNCED, ORPHAN, RECOVERED

- [x] **6.2.1** Adicionar enum `OrderIntent` no `core/models.py`: `OPEN_LONG`, `CLOSE_LONG`, `OPEN_SHORT`, `CLOSE_SHORT`, `STOP_LOSS`, `SCALE_IN`, `REDUCE`
- [x] **6.2.2** Adicionar coluna `intent VARCHAR(20)` ao `OrderRecord` — nullable para ordens históricas
- [x] **6.2.3** Adicionar coluna `reconciled_at TIMESTAMP` ao `OrderRecord` — última verificação com exchange
- [x] **6.2.4** Adicionar coluna `reconciliation_status VARCHAR(20) DEFAULT 'pending_sync'` ao `OrderRecord`
- [x] **6.2.5** Adicionar índice `ix_order_records_reconciliation` em `(reconciliation_status, created_at)` para queries de sync
- [x] **6.2.6** Adicionar índice `ix_order_records_exchange_order_id` em `(exchange_order_id)` para lookups rápidos
- [x] **6.2.7** Gerar migration Alembic: `alembic revision --autogenerate -m "add order reconciliation columns"`
- [x] **6.2.8** Aplicar e verificar: `alembic upgrade head`

---

## 6.3 — Crash-Safe Order Flow (`core/execution/binance.py`)

> **Problema:** se o processo morre entre enviar a ordem e registrar o fill, a posição some.
> **Solução:** write-ahead — grava a ordem no DB *antes* de enviá-la à exchange.
> Depende de: 6.2.

- [x] **6.3.1** Refactor `_place_and_fill()`: antes de chamar a API da Binance, criar `OrderRecord` com `status=PENDING`, `intent=<intent>`, `reconciliation_status=pending_sync` e fazer `session.commit()`
- [x] **6.3.2** Após o fill, atualizar o `OrderRecord` existente (não criar um novo): `status=FILLED`, `exchange_order_id`, `filled_amount`, `avg_fill_price`, `fee_usdt`, `reconciliation_status=synced`
- [x] **6.3.3** Em caso de erro/rejeição: atualizar `status=REJECTED` com `exchange_status` contendo a mensagem de erro
- [x] **6.3.4** Refactor `_place_margin_order()`: mesma lógica write-ahead para ordens margin
- [x] **6.3.5** Refactor `_place_stop_order()` e `_place_margin_stop_order()`: gravar stop order no DB com `intent=STOP_LOSS` antes de colocá-la na exchange
- [x] **6.3.6** Garantir que o `exchange_order_id` é sempre persistido — é a chave de reconciliação

---

## 6.4 — Open Order Sync (`core/tasks/_reconciliation.py`)

> Sincroniza ordens abertas na exchange com o DB local.
> Detecta: stop orders executadas que o GG não viu, ordens órfãs, ordens canceladas pela exchange.
> Depende de: 6.1, 6.2.

- [x] **6.4.1** Nova função `_sync_open_orders(session, executor, symbols)`:
  - Busca todas as open orders na exchange (`get_open_orders()`)
  - Para cada order, verifica se existe `OrderRecord` com mesmo `exchange_order_id`
  - **Ordem conhecida:** atualiza status se mudou (ex: parcialmente filled)
  - **Ordem órfã (externa):** ordem na exchange que o GG não colocou (manual, outro bot, API direta) ou perdeu tracking. Cria `OrderRecord` com `reconciliation_status=orphan`, `intent=NULL`. Destacada no frontend (ver 6.8.6)
- [x] **6.4.2** Detectar stop orders executadas: buscar `OrderRecord` onde `intent=STOP_LOSS` e `status=PENDING`, consultar `get_order_status()` na exchange
  - Se `status=FILLED` na exchange → disparar fill recovery (6.5)
  - Se `status=CANCELED` → atualizar para CANCELLED localmente
  - Se não existe na exchange → marcar como `EXPIRED`
- [x] **6.4.3** Detectar stop orders órfãs na exchange: open orders na exchange com tipo `STOP_LOSS_LIMIT` que não correspondem a nenhuma posição aberta no GG → cancelar via `cancel_order()` (com safety check)
- [x] **6.4.4** Atualizar `reconciled_at` e `reconciliation_status` em cada `OrderRecord` verificado

---

## 6.5 — Fill Recovery (`core/tasks/_reconciliation.py`)

> Recupera fills que o GG perdeu (crash, timeout, stop executada sem callback).
> **Este é o componente mais crítico** — resolve o problema de "ativo some do GG" e "ativo vendido mas continua no GG".
> Depende de: 6.1, 6.2, 6.3.

- [x] **6.5.1** Nova função `_recover_pending_orders(session, executor)`:
  - Query: `OrderRecord` onde `status=PENDING` e `exchange_order_id IS NOT NULL` e `created_at < now() - 2 minutes`
  - Para cada uma, consultar `get_order_status()` na exchange
  - Se `FILLED` → executar recovery (6.5.2)
  - Se `CANCELED` / `EXPIRED` → atualizar status localmente
  - Se `NEW` (ainda aberta) → noop, será processada no próximo ciclo
- [x] **6.5.2** `_apply_fill_recovery(session, order_record, exchange_data)`:
  - Determinar a ação baseada no `intent`:
    - `OPEN_LONG` / `OPEN_SHORT`: chamar `pm.open_position()` com os dados do fill
    - `CLOSE_LONG` / `CLOSE_SHORT`: chamar `pm.close_position()` + deletar `PositionRecord`
    - `STOP_LOSS`: mesma lógica de close
    - `REDUCE` / `SCALE_IN`: chamar `pm.reduce_position()` / `pm.open_position()` (parcial)
  - Atualizar `OrderRecord`: `status=FILLED`, fills, fees
  - **Idempotência**: verificar se a posição/trade já existe antes de criar (checar por `exchange_order_id` em trades)
- [x] **6.5.3** `_recover_pending_orders_without_id(session, executor)`:
  - Query: `OrderRecord` onde `status=PENDING` e `exchange_order_id IS NULL` e `created_at < now() - 5 minutes`
  - O GG crashou *antes* de receber o response da exchange
  - Buscar `get_all_orders(symbol, start_time=order.created_at)` e tentar match por (symbol, side, qty, timestamp ±30s)
  - Se match encontrado → associar `exchange_order_id` e processar como 6.5.1
  - Se nenhum match → marcar como `LOST` (a ordem provavelmente nunca chegou à exchange)
- [x] **6.5.4** Guard de duplicação: antes de aplicar qualquer recovery, verificar se já existe `TradeRecord` referenciando o mesmo `exchange_order_id`

---

## 6.6 — Position Reconciliation v2 (`core/tasks/_reconciliation.py`)

> Substitui os Checks D e E atuais por uma reconciliação completa com auto-repair.
> Depende de: 6.1.

- [x] **6.6.1** Refactor `_reconcile_with_exchange()` → `_reconcile_positions_with_exchange()`:
  - **Asset no GG, zero na exchange** (já existe): auto-repair — deletar position, reset state, **registrar TradeRecord de emergência** com `exit_reason=reconciliation_force_close` para não perder o tracking de PnL
  - **Asset na exchange, zero no GG** (já existe como alert): tentar recovery automático — buscar `get_my_trades(symbol)` recentes, identificar a ordem de compra, reconstruir `PositionRecord` com preço de entrada real
  - **Size mismatch** (novo): position no GG com size diferente da exchange — ajustar `PositionRecord.size` para match da exchange, logar a diferença
- [x] **6.6.2** USDT balance reconciliation melhorada:
  - Calcular USDT esperado: `sum(strategy_state.usdt_balance)` + `sum(positions * current_price)` ≈ `exchange_total_equity`
  - Se drift > threshold configurável → ajustar `usdt_balance` na strategy state para match
  - **Implementado:** `sync_exchange_balances` task (Celery Beat a cada 2 min) + auto-repair proporcional + recovery sync on startup
- [x] **6.6.3** **Margin debt check** (novo para shorts): consultar `GET /sapi/v1/margin/account` e verificar que `borrowed` amounts correspondem a short positions abertas no GG
- [x] **6.6.4** Toda reparação gera um `ReconciliationEvent` com: timestamp, tipo de reparo, valores antes/depois, exchange_data de referência

---

## 6.7 — Reconciliation Orchestrator (`core/tasks/_reconciliation.py`)

> Unifica todos os checks em um fluxo ordenado e configurável.
> Depende de: 6.4, 6.5, 6.6.

- [x] **6.7.1** Novo fluxo de `run_reconciliation()`:
  ```
  1. DB consistency checks (existente — Checks A, B, C)
  2. Recover pending orders (6.5) — resolve ordens perdidas ANTES de comparar posições
  3. Sync open orders (6.4) — detecta stops executadas e ordens órfãs
  4. Position reconciliation v2 (6.6) — compara estado final com exchange
  5. Publish summary + alerts
  ```
- [x] **6.7.2** `ReconciliationConfig` Pydantic model em `core/config.py`, persistido via `app_configs` (namespace `"reconciliation"`) — mesmo padrão de `RiskConfig`, `ExecutionConfig`, etc.:
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
- [x] **6.7.3** Cada step do orchestrator retorna um resultado estruturado; o summary final agrega todos
- [x] **6.7.4** Startup recovery: na inicialização do worker, rodar fill recovery (6.5) antes de qualquer tick — garante que ordens perdidas no último crash são recuperadas imediatamente

---

## BUGS — Code Review (Phase 6 audit)

> Bugs, inconsistências e fraquezas detectados na revisão do código implementado.
> Devem ser resolvidos antes de prosseguir para 6.8.

### 🔴 Bugs (crash ou dados incorretos em produção)

- [x] **BUG-1** — `get_ticker_prices()` chamado sem argumento obrigatório
  - **Arquivo:** `core/tasks/_reconciliation.py` linha ~326
  - **Problema:** `executor.get_ticker_prices()` é chamado sem `symbols`, mas a assinatura é `get_ticker_prices(self, symbols: list[str])`. Causa `TypeError` em toda execução de `_reconcile_with_exchange` quando tenta buscar preços para calcular PnL no force-close.
  - **Impacto:** Position reconciliation v2 (6.6) falha silenciosamente — posições fantasma não são force-closed corretamente.
  - **Fix:** Passar `symbols` como argumento: `executor.get_ticker_prices(symbols)`

- [x] **BUG-2** — Typo `cummulativeQuoteQty` impede cálculo correto de avg_price no fill recovery
  - **Arquivo:** `core/tasks/_reconciliation.py` linha ~1087
  - **Problema:** `_apply_fill_recovery` usa `exchange_data.get("cummulativeQuoteQty")` (duplo 'm'), mas `_parse_order_response` normaliza o campo como `"cumulativeQuoteQty"` (um 'm'). O campo nunca é encontrado no dict.
  - **Impacto:** O fallback usa `exchange_data.get("price", "0")` — que para MARKET orders é `"0.00000000"`. Resultado: `avg_price = 0`, posições reconstruídas com entry_price zero, PnL completamente errado.
  - **Fix:** Corrigir para `exchange_data.get("cumulativeQuoteQty")`

- [x] **BUG-3** — `filled_amount` e `avg_fill_price` atribuídos como `float` em vez de `Decimal`
  - **Arquivo:** `core/tasks/_reconciliation.py` linhas ~1092-1093
  - **Problema:** `order_record.filled_amount = float(filled_qty)` e `order_record.avg_fill_price = float(avg_price)` — a coluna DB é `Numeric(20,8)` (Decimal). Conversão para float introduz imprecisão de ponto flutuante em dados financeiros (e.g. `0.1` → `0.09999999...`).
  - **Impacto:** Perda de precisão em registros de fill — pode causar divergências acumulativas em PnL e balance checks.
  - **Fix:** Manter como `Decimal`: `order_record.filled_amount = filled_qty` e `order_record.avg_fill_price = avg_price`

### 🟡 Inconsistências (comportamento diverge da especificação/config)

- [x] **BUG-4** — Config flags `auto_repair` / `force_close_orphans` / `recover_untracked` nunca são verificados
  - **Arquivo:** `core/tasks/_reconciliation.py` (todo o módulo)
  - **Problema:** `ReconciliationConfig` define `auto_repair`, `force_close_orphans` e `recover_untracked` como flags configuráveis, mas o código de reconciliação **nunca consulta esses valores** — repara incondicionalmente.
  - **Impacto:** O operador não consegue desligar auto-repair em emergência sem desligar toda a reconciliação (`enabled=False`). Comportamento misleading.
  - **Fix:** Guardar cada bloco de repair com `if recon_cfg.auto_repair:`, `if recon_cfg.force_close_orphans:`, `if recon_cfg.recover_untracked:`

- [x] **BUG-5** — `order_type` truncado a 10 caracteres corrompe dados de ordens órfãs
  - **Arquivo:** `core/tasks/_reconciliation.py` linha ~869
  - **Problema:** `order_type=order["type"].lower()[:10]` trunca tipos como `"stop_loss_limit"` (15 chars) para `"stop_loss_"`. A coluna `OrderRecord.order_type` é `String(10)`.
  - **Impacto:** Dados de ordens órfãs ficam incorretos; queries futuras por `order_type='stop_loss_limit'` nunca encontram estes registros.
  - **Fix:** Expandir a coluna para `String(20)` (migration) ou mapear para um valor curto (e.g. `"sll"` / `"sl"`)

- [x] **BUG-6** — `OrderRecord` não tem coluna `strategy` — fill recovery pode reconstruir na strategy errada
  - **Arquivo:** `core/tasks/_reconciliation.py` (múltiplas funções de recovery)
  - **Problema:** Quando fill recovery precisa determinar a strategy de uma ordem, faz `session.query(StrategyStateRecord).filter_by(symbol=symbol).first()`. Se múltiplas strategies operam o mesmo símbolo (e.g. SmartHodler + BearGuard em BTCUSDT), retorna uma arbitrária.
  - **Impacto:** Recovery pode abrir/fechar posição na strategy errada — cascata de inconsistências.
  - **Fix:** Adicionar coluna `strategy VARCHAR(50)` ao `OrderRecord` (nullable, preencher no write-ahead via `self._strategy`). Migration necessária.

- [x] **BUG-7** — `ReconciliationConfig.interval_minutes` é dead code — Beat interval hardcoded
  - **Arquivo:** `core/celery_app.py` linha ~79
  - **Problema:** O `beat_schedule` usa `"schedule": 120.0` (hardcoded 2 min). O campo `interval_minutes` do `ReconciliationConfig` nunca é lido pelo Beat — alterar o valor no DB/config não tem efeito.
  - **Impacto:** Operador pensa que pode ajustar o intervalo em runtime, mas não pode.
  - **Fix:** Usar `schedule=settings.reconciliation.interval_minutes * 60` (requer rework de como Beat lê config), ou remover o campo do config e documentar que requer restart.

### 🟠 Fraquezas (funcionam mas com riscos)

- [x] **BUG-8** — Race condition entre `sync_exchange_balances` e `run_reconciliation`
  - **Arquivo:** `core/tasks/_reconciliation.py` (ambos tasks)
  - **Problema:** Ambos rodam a cada 2 minutos, podem sobrepor-se no tempo, e ambos lêem/escrevem em `StrategyStateRecord.state_data` (balance, equity). Sem lock ou serialização, um pode fazer stale read e sobrescrever o repair do outro.
  - **Impacto:** Reparos de balance podem ser revertidos silenciosamente; em edge cases, loop infinito de "drift detectado → reparado → sobrescrito → drift detectado de novo".
  - **Fix:** Usar `task_acks_late=True` + `solo` pool, ou adicionar `advisory lock` no DB antes de escrever state_data, ou consolidar ambos em um único task.

- [x] **BUG-9** — Position reconstruction assume `side="long"` incondicionalmente
  - **Arquivo:** `core/tasks/_reconciliation.py` linha ~535
  - **Problema:** Na reconstrução de posição (asset na exchange sem PositionRecord local), `side` é hardcoded como `"long"`. Se o asset é resultado de um short parcialmente fechado (BearGuard), a posição será reconstruída incorretamente como long.
  - **Impacto:** BearGuard shorts reconstrídos como longs terão PnL invertido e decisões de close erradas.
  - **Fix:** Inferir `side` a partir do `StrategyStateRecord` (se strategy é bear_guard → short) ou do `intent` de ordens recentes no símbolo.

- [x] **BUG-10** — Idempotency check de close usa janela de 60s — insuficiente
  - **Arquivo:** `core/tasks/_reconciliation.py` linha ~1157
  - **Problema:** O check de idempotência para `close_long`/`stop_loss` verifica `TradeRecord` com `exit_time >= now - timedelta(seconds=60)`. Mas o recovery age padrão é 120s (ordem precisa ter >2 min para entrar no recovery). Ou seja, o trade original pode ter sido criado há >60s quando o recovery roda → check não encontra → duplica.
  - **Impacto:** Trades duplicados em cenário de crash + restart lento (>60s entre fill real e recovery).
  - **Fix:** Verificar por `exchange_order_id` no `TradeRecord` (precisa adicionar campo) ou expandir janela para `recovery_age_seconds + margem`.

- [x] **BUG-11** — Startup recovery sem retry dedicado nem alerta de falha
  - **Arquivo:** `core/celery_app.py` linhas ~142-175
  - **Problema:** Se a API da Binance estiver down no momento do startup, o recovery síncrono falha (catch genérico logga warning) e depende do ciclo assíncrono de 2 min. Não há retry imediato nem alerta de que pending orders ficaram sem resolver.
  - **Impacto:** Worker inicia com estado potencialmente inconsistente. Se o primeiro tick executar antes do ciclo de 2 min resolver, pode duplicar ordens ou operar com balance errado.
  - **Fix:** Adicionar retry com backoff (max 3 tentativas, 5s/15s/30s) no bloco síncrono, e emitir alerta CRITICAL se todas falharem.

---

## 6.8 — Alerting & Dashboard

> Visibilidade completa do estado de reconciliação.
> Depende de: 6.7.

- [x] **6.8.1** Novos `EventType`s: `FILL_RECOVERED`, `ORDER_ORPHAN_DETECTED`, `POSITION_FORCE_CLOSED`, `POSITION_RECONSTRUCTED`, `STOP_ORDER_SYNCED`
- [x] **6.8.2** Alert escalation por severidade:
  - **INFO**: sync ok, stop order status atualizado
  - **WARNING**: size mismatch corrigido, balance drift ajustado
  - **CRITICAL**: posição force-closed, asset não rastreado detectado, fill recovery executado
- [x] **6.8.3** Telegram alert para eventos CRITICAL (integração com alerter existente)
- [x] **6.8.4** API endpoint `GET /api/reconciliation/status` — último resultado de reconciliação, ordens pending, divergências ativas
- [x] **6.8.5** API endpoint `GET /api/reconciliation/history` — histórico de reparos com filtro por tipo e severidade
- [x] **6.8.6** API endpoint `GET /api/exchange/orders` — lista **todas** as ordens da conta na exchange (abertas + recentes do DB), cada uma com campo `source`:
  - `source: "gg"` — ordem criada pelo GoldenGibbon (tem `intent` preenchido)
  - `source: "orphan"` — ordem detectada na exchange sem correspondência no GG
  - Response inclui: `exchange_order_id`, `symbol`, `side`, `type`, `status`, `origQty`, `executedQty`, `price`, `stopPrice`, `time`, `source`, `intent`
- [x] **6.8.7** Frontend — at Activities page **Orders**, must reflect only exchange orders:
  - Tabela com todas as ordens (GG + órfãs) ordenadas por timestamp
  - Ordens órfãs destacadas com badge visual (ex: tag "ORPHAN" em amarelo/laranja) para identificação imediata
  - Filtros: por source (GG / Orphan / All), por symbol, por status (open / filled / cancelled)
  - Ordens GG mostram o `intent` (OPEN_LONG, STOP_LOSS, etc.); órfãs mostram "—"
  - Break "strategies" page in 3, one for each strategy, left menu must show the estrategy name


---

## 6.9 — Tests

> Depende de: tudo.

### Exchange Query Layer (6.1)
- [x] **6.9.1** Test: `get_open_orders()` retorna lista formatada corretamente (mock da API)
- [x] **6.9.2** Test: `get_open_orders()` sem symbol retorna todas
- [x] **6.9.3** Test: `get_order_status()` para ordem FILLED, NEW, CANCELED
- [x] **6.9.4** Test: `cancel_order()` quando ordem já foi executada → retorna graciosamente

### Crash-Safe Order Flow (6.3)
- [x] **6.9.5** Test: `_place_and_fill()` grava OrderRecord PENDING antes de chamar API
- [x] **6.9.6** Test: se API falha, OrderRecord fica REJECTED (não PENDING infinito)
- [x] **6.9.7** Test: `exchange_order_id` é persistido após fill

### Open Order Sync (6.4)
- [x] **6.9.8** Test: stop order executada na exchange → detectada e marcada para recovery
- [x] **6.9.9** Test: ordem órfã na exchange → criada como ORPHAN no DB
- [x] **6.9.10** Test: stop order sem posição correspondente → cancelada

### Fill Recovery (6.5)
- [x] **6.9.11** Test: ordem PENDING com fill na exchange → posição criada no GG
- [x] **6.9.12** Test: recovery idempotente — rodar duas vezes não duplica posição
- [x] **6.9.13** Test: ordem PENDING sem exchange_order_id → match por heurística (symbol, side, qty, time)
- [x] **6.9.14** Test: ordem PENDING sem match na exchange → marcada como LOST

### Position Reconciliation v2 (6.6)
- [x] **6.9.15** Test: asset na exchange sem posição no GG → posição reconstruída
- [x] **6.9.16** Test: posição no GG sem asset na exchange → force close com TradeRecord
- [x] **6.9.17** Test: size mismatch → PositionRecord ajustado
- [x] **6.9.18** Test: margin debt check para shorts

### Orchestrator (6.7)
- [x] **6.9.19** Test: fluxo completo — pending recovery → open order sync → position reconciliation
- [x] **6.9.20** Test: startup recovery roda antes do primeiro tick

### Exchange Orders API & Frontend (6.8)
- [x] **6.9.21** Test: `GET /api/exchange/orders` retorna ordens GG com `source="gg"` e intent preenchido
- [x] **6.9.22** Test: `GET /api/exchange/orders` retorna ordens órfãs com `source="orphan"` e intent nulo
- [x] **6.9.23** Test: filtro por source funciona (`?source=orphan` retorna só órfãs)

---

## BUGS v2 — Second Code Review (Post-fix audit)

> Bugs, inconsistências e fraquezas detectados na segunda revisão após os fixes de BUG-1 a BUG-11.
> Numerados a partir de BUG-12 para continuidade.

### 🔴 Bugs (crash ou dados incorretos em produção)

- [x] **BUG-12** — `stop_price` field mapping errado no endpoint de exchange orders
  - **Arquivo:** `api/routes/exchange_orders.py` linha ~47
  - **Problema:** `_to_exchange_order()` mapeia `stop_price=rec.limit_price`. Para ordens STOP_LOSS_LIMIT, `limit_price` é o preço-limite de execução *após* o stop trigger — não é o preço de trigger (stopPrice). O `OrderRecord` não tem coluna `stop_price`.
  - **Impacto:** Dashboard mostra preços de stop incorretos; operador não consegue verificar seus níveis de proteção.
  - **Fix:** Adicionar coluna `stop_price Numeric(20,8)` ao `OrderRecord`, popular no write-ahead (de `order["stopPrice"]`) e na criação de órfãs. Migration necessária.

- [x] **BUG-13** — Multi-strategy symbol resolution ainda não-determinística em `_apply_fill_recovery`
  - **Arquivo:** `core/tasks/_reconciliation.py` linha ~1261
  - **Problema:** Quando `order_record.strategy` é NULL (órfã ou ordem legada), fallback usa `.filter_by(symbol=symbol).first()` no `StrategyStateRecord`. Se SmartHodler + BearGuard operam BTCUSDT, retorna resultado arbitrário (DB sort order).
  - **Impacto:** Recovery pode reconstruir na strategy errada → PnL invertido, decisões de close erradas.
  - **Fix:** Usar a mesma lógica de inferência do 6.6 (checar intents recentes, checar strategy name), ou rejeitar recovery quando ambiguidade existe e alertar.

- [x] **BUG-14** — `OrderIntent` enum nunca é validado no write-ahead
  - **Arquivo:** `core/execution/binance.py` linha ~312, `db/models.py` linha ~199
  - **Problema:** `OrderIntent` enum existe em `core/models.py`, mas `OrderRecord.intent` é `String(20)` sem validação. O write-ahead passa `intent=intent` como string raw — typos passam silenciosamente.
  - **Impacto:** Fill recovery roteia por intent string; um typo resulta em no-op silencioso (ordem nunca recuperada).
  - **Fix:** Validar intent contra `OrderIntent` enum antes de persistir, ou usar um CHECK constraint na coluna.

- [x] **BUG-15** — Sem unique constraint em `exchange_order_id` — idempotência frágil
  - **Arquivo:** `db/models.py` linha ~224
  - **Problema:** Índice `ix_order_records_exchange_order_id` existe mas **não é UNIQUE**. Paper mode pode ter mesmo `exchange_order_id` que live mode. Idempotency check (`filter_by(exchange_order_id=eid).first()`) pode retornar record errado.
  - **Impacto:** Fill recovery pode duplicar trades ou associar fill ao record errado se IDs colidirem entre modos.
  - **Fix:** `UniqueConstraint("exchange_order_id", "trading_mode", name="uq_order_exchange_id_mode")`. Migration necessária.

### 🟡 Inconsistências (comportamento diverge da especificação)

- [ ] **BUG-16** — Orphan orders criadas sem `filled_at` mesmo quando exchange reporta FILLED
  - **Arquivo:** `core/tasks/_reconciliation.py` linha ~989
  - **Problema:** Criação de `OrderRecord` órfã nunca seta `filled_at` — mesmo se `order.get("status") == "FILLED"`.
  - **Impacto:** Audit trail incompleto; dashboard não pode ordenar/filtrar órfãs por fill time.
  - **Fix:** `filled_at=datetime.now(timezone.utc) if order.get("status") == "FILLED" else None`

- [ ] **BUG-17** — Position reconstruction usa stop percentages hardcoded (5%/3%)
  - **Arquivo:** `core/tasks/_reconciliation.py` linhas ~573-574
  - **Problema:** `trailing_stop_price=avg_entry_price * Decimal("0.95")`, `hard_stop_price=avg_entry_price * Decimal("0.97")`. Valores hardcoded em vez de ler do `RiskConfig` da strategy correspondente.
  - **Impacto:** Posição reconstruída pode ter stops incompatíveis com a strategy real — stopped out cedo ou tarde demais.
  - **Fix:** Ler `trailing_stop_pct` e `hard_stop_pct` do `RiskConfig` associado à strategy, com fallback para os valores atuais.

- [ ] **BUG-18** — `reconciliation_status="recovered"` usado no código mas ausente do enum
  - **Arquivo:** `core/tasks/_reconciliation.py` linha ~1253, `core/models.py` linha ~96
  - **Problema:** `ReconciliationStatus` enum define `PENDING_SYNC`, `SYNCED`, `ORPHAN` — mas o código seta `"recovered"` como quarto estado sem declará-lo.
  - **Impacto:** Queries que filtram por enum values não encontram records recovered; inconsistência de schema.
  - **Fix:** Adicionar `RECOVERED = "recovered"` ao `ReconciliationStatus` enum.

- [ ] **BUG-19** — Beat interval de `sync_exchange_balances` hardcoded — não respeita config
  - **Arquivo:** `core/celery_app.py` linha ~87
  - **Problema:** `sync-exchange-balances-2m` usa `"schedule": 120.0` hardcoded. Se operador ajusta `reconciliation.interval_minutes` via config/ENV, este task não muda junto. Ambos tasks (reconciliation + balance sync) rodam no mesmo intervalo, competem pelo advisory lock.
  - **Impacto:** Worker slot desperdiçado bloqueando no lock; configuração confusa.
  - **Fix:** Usar offset (ex: balance sync = reconciliation_interval / 2), ou consolidar ambos em um único task.

### 🟠 Fraquezas (funcionam mas com riscos)

- [ ] **BUG-20** — Tolerância de timestamp matching (30s) pode causar match errado
  - **Arquivo:** `core/tasks/_reconciliation.py` linha ~1215
  - **Problema:** `_TIME_MATCH_TOLERANCE_MS = 30_000`. No recovery de ordens sem `exchange_order_id`, se duas ordens para mesmo symbol/side/qty existirem dentro de 30s, pode associar à errada.
  - **Impacto:** Baixa probabilidade, mas em cenários de high-frequency ou testes automatizados, fill associado à ordem errada.
  - **Fix:** Reduzir para 10s, adicionar score de confiança (qty match + price proximity), ou exigir confirmação quando múltiplos matches existem.

- [ ] **BUG-21** — Sem rate-limit awareness nas queries de reconciliação
  - **Arquivo:** `core/execution/binance.py` (múltiplos métodos), `core/tasks/_reconciliation.py`
  - **Problema:** Reconciliação chama `get_open_orders()`, `get_all_orders(symbol)` por símbolo, `get_order_status()`, `get_my_trades()`, `get_ticker_prices()`, `get_account_info()` — potencialmente dezenas de chamadas por ciclo. Sem tracking de weight usado ou backoff em 429.
  - **Impacto:** Se Binance rate-limita, reconciliação falha no meio — estado parcial persistido como se fosse completo.
  - **Fix:** Implementar request coalescing, batch por símbolo, ou tracking de peso com pausa preventiva.

- [ ] **BUG-22** — Margin debt mismatch detectado mas trading não é pausado
  - **Arquivo:** `core/tasks/_reconciliation.py` linhas ~743-760
  - **Problema:** Detecta que `borrowed` na exchange não bate com position size do GG, loga warning, mas strategy continua operando normalmente.
  - **Impacto:** Se borrowed < position size real, próxima operação pode triggerar liquidação.
  - **Fix:** Marcar posição como `reconciliation_suspect`, bloquear scale-in enquanto debt mismatch persiste.

- [ ] **BUG-23** — Position reconstruction subtrai custo do balance sem verificar suficiência
  - **Arquivo:** `core/tasks/_reconciliation.py` linha ~583
  - **Problema:** `state_data["usdt_balance"] = str(balance - cost)` — se `balance < cost`, fica negativo.
  - **Impacto:** Balance negativo pode causar comportamento inesperado em allocation e risk checks subsequentes.
  - **Fix:** Se balance insuficiente, setar para zero e logar warning (a posição já está financiada na exchange).

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
