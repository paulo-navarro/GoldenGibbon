# Phase 4 — Visual Adjustments

## 1. Mover preços de moeda para página própria

O `PriceTickerCard` está embutido no topo do Dashboard (`DashboardPage.tsx`, linhas 352-357), ocupando espaço na página principal. Mover para uma página dedicada `/prices` para manter o Dashboard focado em portfolio e sinais.

### Tarefas

- [x] **1.1 — Criar `PricesPage.tsx`**
  - Arquivo: `frontend/src/pages/PricesPage.tsx`
  - Nova página que renderiza os `PriceTickerCard` para todos os símbolos configurados
  - Layout em grid responsivo (xs=12, sm=6, md=4) para acomodar múltiplos símbolos
  - Reutilizar o componente `PriceTickerCard` já existente em `DashboardPage.tsx` (linhas 65-100) — extrair para arquivo próprio se necessário

- [x] **1.2 — Registrar rota `/prices` no router**
  - Arquivo: `frontend/src/router.tsx`
  - Adicionar `{ path: 'prices', element: <PricesPage /> }` ao array de children
  - Importar `PricesPage`

- [x] **1.3 — Adicionar link no menu de navegação**
  - Arquivo: `frontend/src/layouts/AppLayout.tsx`
  - Adicionar item "Prices" na sidebar/nav, posicionar após Dashboard

- [x] **1.4 — Remover `PriceTickerCard` do Dashboard**
  - Arquivo: `frontend/src/pages/DashboardPage.tsx`
  - Remover o bloco de Price Tickers (linhas 352-357)
  - Remover a lógica de `symbols` derivada dos signals/config se não for mais usada em outro lugar do Dashboard
  - Manter o componente `PriceTickerCard` acessível (extraído em 1.1 ou importado do mesmo local)

---

## 2. Recent Signals — grid paginado com filtro por tipo de signal

O widget `RecentSignals` no Dashboard (`DashboardPage.tsx`, linhas 222-319) é uma tabela simples sem paginação nem filtro por tipo de signal (buy/sell_full/sell_half/hold). Com muitos símbolos e estratégias, fica difícil de navegar.

### Tarefas

- [ ] **2.1 — Adicionar filtro por tipo de signal**
  - Arquivo: `frontend/src/pages/DashboardPage.tsx`, componente `RecentSignals`
  - Adicionar um segundo `TextField select` (ou `ToggleButtonGroup`) ao lado do filtro de Strategy existente (linha 270)
  - Opções: `All`, `buy`, `sell_full`, `sell_half`, `hold`
  - Novo state `activeSignalType` com default `'all'`
  - Aplicar filtro no `useMemo` de `filtered` (linha 233): combinar filtro de strategy + filtro de signal type

- [ ] **2.2 — Adicionar paginação ao grid de signals**
  - Arquivo: `frontend/src/pages/DashboardPage.tsx`, componente `RecentSignals`
  - Usar `TablePagination` do MUI abaixo da tabela
  - Default: 10 rows por página, opções [10, 25, 50]
  - States: `page` e `rowsPerPage`
  - Aplicar slice no array `filtered` antes de renderizar: `filtered.slice(page * rowsPerPage, (page + 1) * rowsPerPage)`
  - Resetar `page` para 0 quando filtros mudam (strategy ou signal type)

---

## 3. Strategy Page — ordenar por sinais ativos, depois alfabético

A `StrategyPage` (`StrategyPage.tsx`, linhas 421-427) lista os strategy cards na ordem em que as keys aparecem no store, sem ordenação definida. Ordenar por número de sinais ativos (non-hold) descendente, com desempate alfabético.

### Tarefas

- [ ] **3.1 — Ordenar `stratKeys` por contagem de sinais ativos**
  - Arquivo: `frontend/src/pages/StrategyPage.tsx`, bloco que monta `stratKeys` (linhas 421-427)
  - Para cada strategy key, contar sinais ativos: sinais cujo `signal !== 'hold'` no `storeSignals`
  - Ordenar `stratKeys`:
    1. Descendente por contagem de sinais ativos
    2. Desempate: alfabético ascendente pelo nome da strategy
  - Usar `useMemo` para evitar re-sort desnecessário

---

## 4. Exit Proximity — visualizar proximidade de saída

Mostrar, para cada posição aberta, o quão perto cada trigger de venda está de acionar. Dados calculados no backend (fonte única de verdade) e consumidos pelo frontend.

### Tarefas

- [ ] **4.1 — Criar endpoint `GET /api/portfolio/exit-proximity`**
  - Arquivo: `api/routes/portfolio.py`
  - Para cada posição aberta, retornar:
    - `symbol`, `strategy`
    - `hard_stop_pct`: distância percentual do close ao hard stop `(close - hard_stop) / close`
    - `trailing_stop_pct`: distância percentual do close ao trailing stop `(close - trailing_stop) / close`
    - `time_stop_pct` (MR only): `candles_held / time_stop_candles` (0.0 a 1.0+)
    - `exit_conditions`: lista de condições de sell da strategy com status `met` / `not_met` (ex: RSI overbought, upper BB, momentum fade)
  - Precisa do preço atual — ler do `marketStore`/cache Redis ou receber como query param
  - Response model: `list[ExitProximityResponse]`

- [ ] **4.2 — Criar model `ExitProximityResponse`**
  - Arquivo: `core/models.py`
  - Campos: `symbol: str`, `strategy: str`, `hard_stop_pct: float`, `trailing_stop_pct: float`, `time_stop_pct: Optional[float]`, `exit_conditions: list[ExitConditionStatus]`
  - Sub-model `ExitConditionStatus`: `name: str`, `met: bool`, `current_value: Optional[str]`, `threshold: Optional[str]`

- [ ] **4.3 — Implementar lógica de cálculo no backend**
  - Arquivo: `core/risk/__init__.py` ou novo `core/risk/proximity.py`
  - Função `compute_exit_proximity(position, strategy_name, close, strategy_config, market_data) -> ExitProximityResponse`
  - Reutilizar os mesmos cálculos de `check_stops()` mas sem acionar — apenas reportar distância
  - Para exit conditions da strategy, consultar os indicadores atuais vs thresholds de config

- [ ] **4.4 — Componente `ExitProximityCard` no frontend**
  - Arquivo: `frontend/src/components/ExitProximityCard.tsx`
  - Para cada posição, mostrar barras de progresso (MUI `LinearProgress`) coloridas:
    - Verde (>15% de distância) → Amarelo (5-15%) → Vermelho (<5%)
    - Time stop: barra que enche conforme candles passam
  - Checklist de exit conditions: ícones check/x para cada condição
  - Consumir do endpoint via React Query

- [ ] **4.5 — Integrar `ExitProximityCard` na UI**
  - Arquivo: `frontend/src/pages/DashboardPage.tsx` ou `StrategyPage.tsx`
  - Exibir abaixo da tabela de posições abertas, um card por posição
  - Atualizar via WebSocket event `PRICE_UPDATE` ou polling curto (30s)
