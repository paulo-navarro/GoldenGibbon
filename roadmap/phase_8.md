# Phase 8 — UI/UX Refinements

> **Goal:** Melhorar a organização e usabilidade do dashboard e das páginas de estratégia.
> **Status:** Planning

---

## Tarefas

### [ ] 8.1 — Mover Exit Proximity para dentro dos Strategy pair cards

**Problema:** A seção "Exit Proximity" no Dashboard é redundante — mostra dados por par/estratégia que já pertencem ao card de cada par na Strategy page. No header do accordion já aparecem Hard/Trail %, mas o detalhe completo (stop bars, time stop, exit conditions) fica isolado no Dashboard sem contexto.

**Solução:**
- Adicionar uma terceira coluna "Exit Proximity" no `AccordionDetails` do `StrategyCard`, ao lado de "Conditions" e "Scaled Entry"
- Conteúdo: hard stop bar, trailing stop bar, time stop bar, exit conditions checklist (reutilizar lógica do `ExitProximityCard.tsx`)
- Manter os indicadores compactos (Hard/Trail %) no `AccordionSummary` (header)

**Remover:**
- `ExitProximitySection` do `DashboardPage.tsx` (Grid item linhas 366-369)
- Import de `ExitProximitySection` no `DashboardPage.tsx` (linha 30)
- O componente `ExitProximityCard.tsx` em si (ou refatorar para exportar sub-componentes reutilizáveis)
- Remover `exitProx` do header do `StrategyCard` se decidirmos que a coluna no body é suficiente

**Arquivos afetados:**
- `frontend/src/pages/DashboardPage.tsx` — remover seção Exit Proximity
- `frontend/src/pages/StrategyPage.tsx` — adicionar coluna Exit Proximity no `AccordionDetails`
- `frontend/src/components/ExitProximityCard.tsx` — deletar ou refatorar

**Layout do AccordionDetails (3 colunas):**
```
┌─────────────────┬─────────────────┬─────────────────┐
│   Conditions    │  Scaled Entry   │ Exit Proximity  │
│                 │                 │                 │
│ ✓ EMA cross     │ [████░░] 66%    │ Hard   ██░ 12% │
│ ✗ ADX filter    │ Scale-ins: 1/2  │ Trail  ███ 18% │
│ ✓ RSI confirm   │ Buy candles: 3  │ Time   ██░ 40% │
│                 │                 │                 │
│                 │                 │ Exit conditions │
│                 │                 │ ✗ RSI overbought│
│                 │                 │ ✓ Volume fade   │
└─────────────────┴─────────────────┴─────────────────┘
```

**Notas:**
- A query `useExitProximity()` já é chamada no `StrategyCard` — não precisa de novo fetch
- Remover os indicadores compactos do header (Hard/Trail %) — o chip de state (`position`/`idle`) já indica se está em trade

---

### [ ] 8.2 — Mover Strategy Parameters para Settings page

**Problema:** O accordion "Parameters" dentro de cada pair card (`StrategyCard`) mostra a config **da estratégia inteira** (EMA periods, ADX threshold, etc.), não do par. Todos os cards da mesma estratégia mostram exatamente os mesmos campos, e alterar em qualquer um afeta todos. Isso é confuso — parece configuração per-pair mas é per-strategy.

Além disso, o toggle "Enabled" dentro do Parameters dá a impressão de desabilitar só aquele par, mas desabilita a estratégia inteira.

**Solução:**
- Adicionar cada estratégia registrada como item no sidebar da Settings page (ao lado de risk, execution, regime, etc.)
- Ao selecionar uma estratégia, renderizar o editor de parâmetros completo (reusando a lógica do `ParameterTuning` ou do `NamespaceEditor`)
- O toggle Enabled fica proeminente no topo da seção da estratégia

**Remover:**
- `ParameterTuning` component do `StrategyCard` (`StrategyPage.tsx` linha 341)
- `StrategyToggles` component do `SettingsPage.tsx` (linhas 218-267) — redundante quando cada estratégia tem sua própria seção com toggle

**Arquivos afetados:**
- `frontend/src/pages/SettingsPage.tsx` — adicionar estratégias no sidebar + editor
- `frontend/src/pages/StrategyPage.tsx` — remover `ParameterTuning` dos cards
- `frontend/src/api/queries.ts` — verificar se `useStrategyConfig` já serve ou precisa adaptar para o namespace pattern

**Layout Settings (sidebar expandido):**
```
┌──────────────────┬────────────────────────────────────┐
│ Settings         │                                    │
│                  │  Smart Hodler                      │
│ ▸ Trading Mode   │  ┌────────────────────────────┐    │
│ ▸ Risk           │  │ Enabled  [===ON===]        │    │
│ ▸ Execution      │  └────────────────────────────┘    │
│ ▸ Regime         │                                    │
│ ▸ Shorts         │  TIMEFRAMES                        │
│ ───────────────  │  Primary: 15m  Confirmation: 1h    │
│ ▸ Smart Hodler ◄ │                                    │
│ ▸ Mean Reversion │  INDICATORS                        │
│ ▸ Bear Guard     │  EMA Fast: 50  EMA Slow: 200 ...  │
│                  │                                    │
│                  │  POSITION SIZING                   │
│                  │  Entry Pct: ...  Scale 1 Pct: ...  │
│                  │                                    │
│                  │  [Apply]  [Reset to Defaults]      │
└──────────────────┴────────────────────────────────────┘
```

**Notas:**
- As strategy configs usam a API `GET/PUT /api/strategy/config/{name}` — diferente do namespace pattern (`/api/config/{namespace}`). Pode ser necessário unificar ou o frontend trata os dois patterns
- Separador visual no sidebar entre system configs e strategy configs

---

### [ ] 8.3 — Warning banner quando estratégia está disabled

**Problema:** Quando uma estratégia está disabled, a Strategy page mostra os pair cards normalmente (state persiste no DB/store). Não há nenhuma indicação de que a estratégia não está rodando.

**Solução:**
- Quando `strategy.enabled = false`, mostrar um `Alert` (warning) no topo da Strategy page, abaixo do título:
  - Ex: "⚠ Smart Hodler is disabled. Enable it in Settings to resume trading."
  - Link direto para a seção da estratégia no Settings (depende de 8.2)
- Pair cards continuam visíveis normalmente — o warning no topo da página é suficiente

**Arquivos afetados:**
- `frontend/src/pages/StrategyPage.tsx` — adicionar Alert condicional
- Usar `useStrategyOverview()` que já retorna `enabled` por estratégia

---

### [ ] 8.4 — Logs: paginação + categorias de log

**Problema:** A Logs page faz fetch de 500 linhas de uma vez, sem paginação. O conteúdo é na maioria ruído de infraestrutura (lock acquired, config loaded, heartbeat) — os eventos úteis (sinais, trades, rejeições de risco, transições de estado) ficam enterrados. Filtro só por level, sem como filtrar por módulo/contexto.

**Solução — Backend:**
- Adicionar `offset` param ao endpoint `GET /api/logs` para paginação server-side (retornar `total_count` na response)
- Auditar os ~212 `logger.*` calls em `core/` e adicionar campo `category` aos ~40 que importam:
  - `trade` — ordens colocadas, fills, closes
  - `signal` — sinais emitidos por estratégias
  - `risk` — rejeições do risk engine, stop triggers
  - `state` — transições de estado (idle→position, position→cooldown)
  - `system` — startup, shutdown, config changes, exchange errors
- Adicionar filtro `category` ao endpoint

**Solução — Frontend:**
- `TablePagination` com server-side offset/limit
- Dropdown de category filter (ao lado do level filter)
- Default: mostrar apenas `trade`, `signal`, `risk`, `state` (esconder ruído de infra)
- Search text filter (já existe, manter)

**Arquivos afetados:**
- `api/routes/system.py` — `get_logs()` endpoint: add `offset`, `category` params
- `core/tasks/_tick.py` — adicionar `category=` nos logs de signal/trade/state
- `core/risk/engine.py` — adicionar `category="risk"` nos logs de rejeição
- `core/execution/` — adicionar `category="trade"` nos logs de ordens
- `frontend/src/pages/LogsPage.tsx` — pagination + category filter
- `frontend/src/api/queries.ts` — atualizar `useLogs` com novos params

**Estimativa:** ~5-6 horas

---

### [ ] 8.5 — Prices: remover chip de timeframe + deduplicar price updates

**Problema:** O card de preço mostra um chip com o timeframe do candle (ex: "15m", "1h") que fica alternando entre timeframes a cada ~2s. O preço é o mesmo independente do timeframe — é só o close do candle atual. A alternância é porque o Binance WS publica `PRICE_UPDATE` para cada combinação symbol × timeframe, e o frontend armazena a última que chegou.

**Solução:**
- Backend (`core/data/stream_runner.py`): emitir `PRICE_UPDATE` apenas para o menor timeframe de cada symbol (ex: só 15m), ou deduplicar por symbol ignorando timeframe
- Frontend (`PricesPage.tsx`): remover o chip de timeframe (linhas 43-45)
- Frontend (`types/market.ts`): campo `timeframe` em `PriceResponse` pode virar opcional/removido

**Estimativa:** ~30 min

---

### [ ] 8.6 — Prices: variação 24h com cores verde/vermelho

**Problema:** Os cards de preço mostram apenas o preço atual, sem contexto de variação diária. Não dá pra saber de relance se o ativo subiu ou caiu.

**Solução:**
- Backend: novo endpoint `GET /api/market/ticker24h` que retorna variação 24h de todos os symbols enabled (proxy do Binance `GET /api/v3/ticker/24hr` ou batch com `GET /api/v3/ticker/24hr?symbols=[...]`)
  - Response: `[{ symbol, price_change_pct, open_24h, high_24h, low_24h }]`
- Frontend: fetch uma vez no mount da `PricesPage`, guardar `open_24h` por symbol
  - Variação = `(current_price - open_24h) / open_24h * 100`
  - Mostrar como `+2.34%` em verde ou `-1.56%` em vermelho no card
  - Cor do preço também muda: `success.main` / `error.main`

**Nota:** O `PRICE_UPDATE` via WS já envia `open`, mas é o open do candle 15m/1h, não do dia. Precisa do 24h ticker separado.

**Arquivos afetados:**
- `api/routes/market.py` — novo endpoint `ticker24h`
- `frontend/src/pages/PricesPage.tsx` — adicionar variação + cores ao card
- `frontend/src/api/queries.ts` — nova query `useTicker24h`

**Estimativa:** ~1-2 horas

---

<!-- Adicionar mais tarefas abaixo conforme a discussão avança -->
