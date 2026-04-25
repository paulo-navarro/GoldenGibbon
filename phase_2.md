# Phase 2

## Interface Improvements

- [x] Criar um único componente compartilhado para "Equity Curve" — hoje duplicado em 3 lugares: `DashboardPage` (`MiniEquityCurve`, limit 100, height 160), `PortfolioPage` (`EquityCurveChart`, limit 500, height 350) e `MetricsPage` (`EquityCurveChart`, limit 1000, height 420). Unificar em um componente com props (`limit`, `height`, `mini?`) e adicionar uma área separada para o PnL (já está mapeado nos dados de Portfolio e Metrics, mas não renderizado)
- [x] O cabeçalho deve exibir todos os indicadores de status: **WS**, **API**, **DB** (database) e **Redis** (atualmente mostra apenas WS e API)
- [x] Remover o valor das cryptos do cabeçalho
- [x] No Dashboard, remover o card **System Status**
- [x] Mover o select **Strategy** para dentro do card **Recent Signals**
- [x] Os Symbols listados no Dashboard devem vir da configuração de symbols (sem hardcode)
- [x] Criar componente autônomo **CycleStatus** para o Dashboard mostrando último ciclo e countdown para o próximo:
  - **Último ciclo**: lê `updated_at` do strategy signal (já disponível no Zustand store via WebSocket)
  - **Próximo ciclo**: schedule fixo — `:02, :17, :32, :47` de cada hora. O countdown deve ser calculado a partir de `Date.now()` a cada atualização — **nunca incrementar um contador** (ex: `setInterval` decrementando 1s). Usar `setInterval` que recalcula `nextCycle - Date.now()` a cada tick
  - Componente deve ser independente, sem props obrigatórias, consumindo o store diretamente
- [x] A página de Portfolio deve exibir os itens na seguinte ordem:
  - 1ª linha de cards: **Total PnL** | **Equity** (campo que hoje está dentro de USDT Balance) | **USDT Balance** | **Positions Value**
  - 2ª linha: **Equity Curve**
  - 3ª linha: **Open Positions**

## Separação Paper / Live

- [ ] Adicionar coluna `trading_mode` (`VARCHAR(10)`, valores `'paper'` ou `'live'`) com índice nas tabelas:
  - `order_records`
  - `trade_records`
  - `portfolio_snapshots`
- [ ] Migração dos dados existentes: `UPDATE` baseado no prefixo do `run_id` (`paper_` → `'paper'`, `live_` → `'live'`). Default `'paper'` para `run_id` NULL
- [ ] Worker: em `_persist_tick_results`, gravar `comp.trading_mode` na coluna ao inserir order, trade e snapshot
- [ ] API: todos os endpoints que retornam orders, trades, portfolio e equity-curve devem aceitar (e filtrar por) `trading_mode`. Default determinado pela config `live_trading.enabled`:
  - Se live trading ligado → default `'live'`
  - Se desligado → default `'paper'`
- [ ] Frontend: **não** expor seletor de modo ao usuário. O front lê o estado de `live_trading.enabled` (já disponível via `GET /api/config/settings`) e passa `trading_mode` automaticamente em todas as chamadas de API. Se live → mostra só dados live. Se paper → mostra só dados paper. Transparente para o usuário.

## Bugs

### ~~BUG 6 — Kill-switch path chama método inexistente, matando o tick silenciosamente~~ DONE

**Arquivo:** `core/tasks/__init__.py:1009`

**Problema:** Quando o kill-switch está ativo para um par (strategy+symbol), o código chama `comp.pm.update_equity(candle_time, {symbol: close})`. O método `update_equity` existe no model `Portfolio` (Pydantic), mas **não** no `PortfolioManager`. Resultado: `AttributeError` capturado pelo `except` geral → task retorna com `{"error": "..."}` sem gravar snapshot, sem atualizar `strategy_state`, sem nenhum registro. Todos os pares com kill-switch ativo ficam completamente inoperantes a cada ciclo.

**Impacto em produção:** BTC, ETH, LINK (ambas estratégias) e HBAR/mean_reversion estão com kill-switch disparado. Todos falhando silenciosamente a cada tick desde ~21/04. Apenas HBAR/smart_hodler está operacional.

**Fix:** Trocar `comp.pm.update_equity(...)` por `comp.pm.take_snapshot(...)`.

---

### ~~BUG 4 — Horários exibidos em UTC em vez da timezone do browser~~ DONE

**Problema:** Datas e horários no cabeçalho (e futuramente no CycleStatus) estão sendo exibidos em UTC+0, ignorando a timezone do usuário.

**Fix:** Usar `toLocaleString()` / `toLocaleTimeString()` sem forçar timezone — o browser usa o local automaticamente. Evitar qualquer formatação que assuma UTC explicitamente (ex: `.toISOString()` diretamente no display, ou `timeZone: 'UTC'` no `Intl.DateTimeFormat`).

---

### ~~BUG 1 — `total_pnl` não é restaurado após restart do worker~~ DONE

**Arquivo:** `core/tasks/__init__.py` → função `recover_state_from_db`

**Problema:** O `usdt_balance` é restaurado do `state_data` no restart do worker Celery, mas o `total_pnl` não. O `state_data` contém o valor correto (`total_pnl` é salvo a cada tick), mas não é lido na recuperação. No primeiro tick após o restart, o snapshot gravado no DB já tem `total_pnl=0`, sobrescrevendo o valor histórico correto. Trades que fechem depois acumulam apenas a partir de 0.

**Fix:** Adicionar logo após o restore do `usdt_balance` (linha ~209):
```python
saved_total_pnl = data.get("total_pnl")
if saved_total_pnl is not None:
    pm._portfolio.total_pnl = Decimal(str(saved_total_pnl))
```

---

### ~~BUG 3 — Label `[db]` em campos de formulário~~ DONE

**Arquivos:**
- `frontend/src/pages/StrategyPage.tsx:345` — TextField recebe `label={...` [db]`}` quando `field.source === 'db'`
- `frontend/src/pages/SettingsPage.tsx:88` — Chip com `label={data.source}` exibe "db" no título da seção
- `frontend/src/pages/SymbolsPage.tsx:281` — Chip com `label={s.source}` exibe "db" na tabela de símbolos

**Problema:** O `source` é um detalhe de implementação interno (indica se o valor veio do DB ou do default/env). Exibir `[db]` no label do campo ou como chip não comunica nada útil ao usuário — todo valor salvo vem do banco. Polui a UI desnecessariamente.

**Fix:** Remover o `source` de todos os labels e chips voltados ao usuário final.

---

### ~~BUG 5 — Auto-scroll na página de Logs arrasta para o fim da lista~~ DONE

**Arquivo:** `frontend/src/pages/LogsPage.tsx` (ou componente de lista de logs)

**Problema:** Existe uma funcionalidade de "auto scroll" que rola até o último elemento da lista. Como os logs são exibidos com o mais recente no topo, esse comportamento arrasta o usuário para os logs mais antigos — o oposto do útil.

**Fix:** Remover completamente a funcionalidade de auto-scroll da página de Logs.

---

### ~~BUG 2 — `total_pnl` no Portfolio summary não é agregado entre símbolos~~ DONE

**Arquivo:** `api/routes/portfolio.py` → endpoint `GET /`

**Problema:** Cada par strategy+symbol tem seu próprio `PortfolioManager` com seu próprio `total_pnl`. A API lê o snapshot com o `timestamp` mais recente da tabela `portfolio_snapshots` — que pode ser de qualquer símbolo (ex: ETHUSDT com pnl=0) mesmo que outro símbolo (ex: BTCUSDT) tenha trades fechados. O `total_pnl` retornado nunca reflete o total real de todos os símbolos.

**Fix:** O endpoint `GET /portfolio` deve calcular o `total_pnl` somando `pnl_usdt` diretamente da tabela `trade_records` (para o `run_id` ativo), em vez de depender do snapshot mais recente:
```python
total_pnl = session.query(func.sum(TradeRecord.pnl_usdt)).filter_by(run_id=active_run_id).scalar() or Decimal("0")
```
