# Phase 9 — Stop the Bleed: Bugs + Cirurgia de Estratégia (AI-executável)

> **Goal:** Corrigir os problemas que os dados de produção provaram estar custando dinheiro, e submeter toda mudança de estratégia a um gate de backtest antes de voltar ao live.
> **Motivação:** Diagnóstico (2026-07-08): trading live perdeu ~$7.5 (~13% da conta de ~$50) em 10 semanas com churn de 6.4 trades/dia e custo de ~0.2-0.3% por ida-e-volta; 17% dos exits forçados por bug de reconciliação; mean_reversion nunca disparou; backtest quebra a UI e nunca persiste resultados; e o histórico de equity (`portfolio_snapshots`) está corrompido — fatias por (strategy, symbol) do modelo pré-Phase 7, snapshots paper marcados como live e escritas duplicadas, fazendo o dashboard mostrar $2.09 numa conta de $50.
> **Status:** In progress — Etapa 1 (9.1 + 9.2 + 9.3) concluída em 2026-07-18; Etapas 2-5 (9.11, 9.12, 9.4, 9.9, 9.10) em 2026-07-19 — Track A completo
> **Regra de ouro da fase:** nenhuma mudança de estratégia vai ao live sem passar pelo gate da task 9.9.

---

## Diagnóstico que motivou a fase (dados live, 2026-04-21 → 2026-07-08)

| Evidência | Número | Implicação |
|---|---|---|
| PnL médio por trade | -0.29% | ≈ custo de transação; estratégia pré-custos é ~neutra, custos matam |
| Win rate (smart_hodler live) | 21.7% | Baixo demais até para trend-following |
| Frequência | ~6.4 trades/dia | Churn incompatível com 15m + taxa spot |
| Exits `reconciliation_force_close` | 79 de 469 (17%) | Infra fechando posições no lugar da estratégia |
| Exit `momentum_fade` | -0.68% médio (pior exit) | Candidato a remoção |
| `hard_stop` disparos | 180 (38% dos exits) | 3% no 15m é ruído, não proteção |
| Trades live da mean_reversion | 0 | Condições de entrada superconstrangidas |
| Tabela `backtest_results` | vazia | Sistema foi ao live sem evidência persistida |
| Símbolos ativos | 34 com ~$50 de capital | Fricção de min-notional multiplicada |
| `portfolio_snapshots` no mesmo timestamp | 13-26 linhas, somas divergentes ($44 vs $88) | Histórico de equity inutilizável; equity real ($50.10) nunca persistida |

---

## Track A — Bugs e infraestrutura

### [x] 9.1 — Backtest como Celery task (corrige a UI quebrando) ✅ 2026-07-18

**Implementado:**
- [x] Task Celery `run_backtest_job(job_type, params)` em `core/tasks/_backtest.py` — executa compare / multi_strategy / optimize / walk_forward no worker; payload idêntico ao dos endpoints síncronos antigos.
- [x] `POST /compare|/multi-strategy|/optimize|/walk-forward` agora enfileiram e retornam HTTP 202 com `{job_id, job_type, status}`.
- [x] `GET /api/backtest/jobs/{job_id}` — mapeia estado Celery → `pending/running/done/failed`; resultado quando done, erro quando failed (resultado expira do Redis em 1h via `result_expires`).
- [x] Eventos `BACKTEST_JOB_STARTED/COMPLETED/FAILED` publicados no canal SYSTEM.
- [x] Frontend: `useCompare`/`useMultiStrategy`/`useOptimize`/`useWalkForward` reescritos sobre um runner genérico (enqueue POST + polling de 2s no job) preservando as interfaces que a MetricsPage consome — zero mudança na página.

**Problema:** Os 4 endpoints de `api/routes/backtest.py` (`/compare`, `/multi-strategy`, `/optimize`, `/walk-forward`) executam o backtest inteiro **dentro do request HTTP**, no processo da API. O loop candle-a-candle em pandas é CPU-bound; segura o GIL, o event loop engasga, heartbeats de WebSocket estouram, o healthcheck falha e o dashboard inteiro cai junto. Requests de minutos também estouram timeout no frontend.

**Solução:**
- Criar task Celery `run_backtest_job` em `core/tasks/` que executa compare/multi-strategy/optimize/walk-forward.
- Endpoints passam a enfileirar e retornar `job_id` imediatamente (HTTP 202).
- Novo endpoint `GET /api/backtest/jobs/{job_id}` — status (`pending/running/done/failed`) + resultado quando pronto.
- Frontend: página Metrics passa a fazer polling do job (ou escutar evento `BACKTEST_COMPLETED` via WebSocket, canal já existe).
- Progresso opcional: publicar evento a cada símbolo concluído.

**Arquivos:** `api/routes/backtest.py`, `core/tasks/`, `core/celery_app.py`, `frontend/src/pages/Metrics*`

### [x] 9.2 — Persistir todo backtest em `backtest_results` ✅ 2026-07-18

**Implementado:**
- [x] Runs de `compare` e `multi_strategy` persistem 1 linha por (strategy, symbol) com `run_id = "job:{job_id}"` e `config_snapshot = {job_type, params, strategy_config}`.
- [x] Migração `m8n9o0p1q234`: índice de `run_id` deixa de ser único (linhas agrupam por job). **Pendente manual: `alembic upgrade head` em prod.**
- [x] `GET /api/backtest/history` — filtros strategy/symbol/run_id, paginação, mais recentes primeiro.
- [x] Optimize/walk-forward não persistem aqui (combos do grid não têm métricas completas) — o gate da 9.9 persistirá seus runs finais com prefixo `gate:`.
- [x] Testes: `tests/test_backtest_job.py` (persistência agrupada por run_id + config_snapshot + validação de job_type).

**Problema:** A tabela `backtest_results` existe, tem schema completo (incl. `config_snapshot` jsonb) e está **vazia**. Backtests rodados pela API são descartados — impossível comparar experimentos ao longo do tempo.

**Solução:**
- Todo run da task 9.1 grava uma linha por (strategy, symbol) em `backtest_results`, com `config_snapshot` preenchido com os parâmetros usados.
- `run_id` único por job para agrupar.
- Endpoint `GET /api/backtest/history` para listar runs persistidos (a página Metrics consome).

### [x] 9.3 — Investigar e conter `reconciliation_force_close` ✅ 2026-07-18 (código; auditoria dos 79 casos históricos fica como análise opcional)

**Problema:** 79 exits live (17%) foram fechamentos forçados pela reconciliação (Phase 6), com PnL médio -0.085%. A reconciliação está decidindo trades no lugar da estratégia — ou há dessincronia real recorrente (pior ainda), ou o repair é agressivo demais.

**Solução:**
- Auditar logs/`reconciliation_runs` para classificar os 79 casos: dessincronia real vs falso positivo.
- Se falso positivo: adicionar janela de tolerância/confirmação dupla antes do force close.
- Se dessincronia real: encontrar a causa upstream (fill não registrado? stop órfão?) e corrigir.
- Adicionar teste de regressão para o cenário identificado.

**Causas raiz já identificadas na revisão de 2026-07-08** (detalhes em `bugs.md`):
- **BUG-015:** `exit_price` cai para `entry_price` quando o fetch de ticker falha (engolido por `except Exception` silencioso) → os 79 trades têm PnL fabricado (~0), corrompendo o histórico.
- **BUG-016:** força fechamento sem carência nem checagem de ordens em voo — janela de corrida com o sync de 2 min; auto-Earn/dust da Binance também zera o saldo spot e dispara falso positivo.

**Resolvido via BUG-015 + BUG-016 (ambos Fixed, ver `bugs.md`):** exit_price real via VWAP de `myTrades` com fallback em cascata e flag `exit_price_estimated`; carência + histerese (min_cycles) + checagem de ordens abertas antes do force close. Testes de regressão em `tests/test_reconciliation.py`.

### [x] 9.4 — Modelar min-notional e lot-size no backtest ✅ 2026-07-19

**Implementado:**
- [x] `ExecutionConfig`: `min_notional` (default $5, filtro NOTIONAL) e `qty_step` (default 0.00001, filtro LOT_SIZE). `0` desabilita.
- [x] `PaperExecutor._normalize_entry_size`: arredonda entradas para baixo ao step e rejeita notional < mínimo (loga + publica `ORDER_REJECTED`). Exits são isentos — um close sempre consegue zerar a posição.
- [x] Aplicado em OPEN e SCALE_IN, tanto no backtest quanto no paper trading do tick (mesmos filtros que o live enfrenta).
- [x] `core/backtest/capital.py::resolve_total_capital`: em modo live o capital total do backtest vem da conta real (cache de 5 min), não do fictício `backtest.initial_capital` — usado por compare, multi-strategy e optimize/walk-forward (per-slot).
- [x] Testes: rejeição abaixo do mínimo, arredondamento ao step, size zero pós-step, scale-in rejeitado, close isento, defaults zero = comportamento legado, evento ORDER_REJECTED.

**Problema:** O backtest simula taxa (0.1%) e slippage (0.1%), mas não os limites reais da Binance (notional mínimo ~$5, step de quantidade). Com capital pequeno, esses limites mudam o resultado — o backtest é otimista demais exatamente no cenário atual.

**Solução:**
- `ExecutionConfig`: adicionar `min_notional` e `qty_step` (defaults da Binance spot).
- `PaperExecutor`: rejeitar ordens abaixo do mínimo e arredondar quantidade ao step, como a exchange faz.
- Backtest com `initial_capital` igual ao capital real da conta, não valor fictício.

### [x] 9.11 — Consertar a coleta do histórico de equity (`portfolio_snapshots`) ✅ 2026-07-19

**Implementado:**
- [x] `sync_exchange_balances` persiste 1 snapshot account-level por execução (`run_id='account'`, `trading_mode='live'`) — a equity real que antes era calculada e jogada fora.
- [x] `GET /equity-curve` no modo live retorna **apenas** linhas `run_id='account'`; fatias continuam acessíveis via `run_id` explícito.
- [x] Fatias por (strategy, symbol) continuam sendo gravadas (state recovery precisa delas), mas não entram mais na curva.
- [x] Dupla escrita: `_persist_tick_results` agora faz upsert por (run_id, timestamp) — re-execução do mesmo tick (redelivery com acks_late / corrida de lock) não duplica mais.
- [x] Migração `n9o0p1q2r345`: corrige `trading_mode` das fatias paper marcadas como live, deduplica (run_id, timestamp) mantendo a linha mais antiga, e cria índice único `uq_portfolio_snapshots_run_ts`. **Pendente manual: `alembic upgrade head` em prod.**
- [x] Testes: sync persiste account snapshot; curva live só retorna account-level; duplicata (run_id, timestamp) rejeitada pelo banco.

**Problema (confirmado em prod, 2026-07-08):** a conta real tem $50.10 e o endpoint `GET /api/portfolio/` mostra isso corretamente (consulta a Binance ao vivo), mas o **histórico** está corrompido:

1. Snapshots são gravados **por fatia (strategy, symbol)** com `run_id` do modelo de capital isolado (pré-Phase 7) — 10 fatias live somando $35.35, nenhuma linha com a equity real da conta.
2. **3 fatias paper** (`paper_smart_hodler_BTC/ETH/LINK` de 2026-04-16) estão gravadas com `trading_mode='live'`, contaminando somas com dinheiro fictício ($8.86).
3. Nos timestamps de hora cheia há **escrita duplicada** (26 linhas = 2× 13 fatias; soma dobra de $44.21 para $88.42).
4. O `sync_exchange_balances` calcula a equity real ($50.10) a cada 2 min e publica no WebSocket, mas **nunca persiste** — o número certo existe e é jogado fora, enquanto o errado é salvo.

Consequência: equity curve do dashboard mostra valores sem sentido (ex: $2.09) e qualquer análise histórica de performance é impossível.

**Solução:**
- `sync_exchange_balances` passa a **persistir** um snapshot account-level por execução (`run_id='account'`, `trading_mode='live'`).
- `GET /equity-curve` (e a página Dashboard) passam a ler apenas snapshots account-level no modo live.
- Parar de gravar snapshots por fatia no live (ou movê-los para `run_id` claramente separado, sem entrar na curva).
- Encontrar e corrigir a dupla escrita nos timestamps de hora cheia (dois schedules do Beat gravando o mesmo?).
- Migração de limpeza: corrigir `trading_mode` das fatias paper marcadas como live; opcionalmente arquivar as fatias legadas.
- Teste de regressão: 1 timestamp → 1 snapshot live account-level.

**Arquivos:** `core/tasks/_reconciliation.py` (sync), task de snapshot legada em `core/tasks/`, `api/routes/portfolio.py` (equity-curve), migração Alembic de limpeza.

### [x] 9.12 — Backtest utilizável na UI de prod (crash React #185) ✅ 2026-07-19

**Implementado:**
- [x] `errorElement` (`RouteError`) em todas as rotas — crash de uma página renderiza um card de erro dentro do layout; o resto do app sobrevive.
- [x] `useWebSocket` endurecido: (a) dependência do effect passa a ser a string `channels.join(',')` em vez da identidade do array — um literal inline causaria reconexão infinita (close → status → re-render → reconnect = React #185); (b) `updateStatus` vira no-op quando o status não muda, cortando re-renders durante tempestade de reconexão.
- [x] Sanitização antes do Recharts (`utils/sanitize.ts`): pontos não-finitos (NaN/Infinity) são descartados no `EquityCurveChart`, nas curvas do compare e na curva combinada do multi-strategy.
- [x] Flag de implausibilidade: linhas do compare com |retorno| > 1000% geram warning na UI ("degenerate slice capital") em vez de passar despercebidas.
- [x] Gatilho principal já removido pela 9.1 (backtest não bloqueia mais a API/WebSocket).
- [x] Causa do 10500% investigada: `compare.py` usa `slot_capital = backtest.initial_capital / max_concurrent_positions` — com capital pequeno a fatia degenera. Modelagem correta (min-notional/lot-size) é a task 9.4.

**Problema (reportado 2026-07-08):** rodar backtest em prod derruba o frontend inteiro com `Minified React error #185` ("Maximum update depth exceeded" — loop infinito de setState). O span de medição do Recharts mostra `10500.0%` no momento do crash. Três fatores se combinam:

1. **Sem ErrorBoundary por rota** — qualquer exceção em uma página (Metrics) desmonta o app inteiro; o usuário vê a tela de erro padrão do React Router em vez de só a página quebrada.
2. **Loop de re-render (#185)** — candidato mais provável: enquanto o request de backtest bloqueia a API (ver 9.1), o WebSocket cai e o ciclo reconexão → update de store → re-render entra em loop; e/ou um `useEffect` na MetricsPage sincronizando estado a partir da resposta dispara setState em cascata.
3. **Dados absurdos alimentando os gráficos** — retorno de `10500.0%` sugere fatia de backtest com capital inicial degenerado (mesma família de bug da 9.11); valores extremos vão direto pro Recharts sem sanitização.

**Solução:**
- Adicionar `errorElement`/ErrorBoundary nas rotas (`frontend/src/router.tsx`) — crash de uma página não derruba o app.
- Reproduzir com build dev (erro não-minificado) e corrigir o loop de setState — inspecionar `useWebSocket` (reconexão) e os `useEffect` da `MetricsPage`.
- Sanitizar a resposta do backtest antes de plotar (clamp/flag de valores implausíveis, ex: |retorno| > 1000%) e investigar o capital inicial degenerado que gera o 10500% no backend.
- **Depende da 9.1** (backtest via Celery): sem ela a API continua travando o WebSocket durante o run, que é o gatilho principal.
- Critério de aceite: rodar compare/multi-strategy/optimize em prod com o dashboard aberto, sem crash e com as demais páginas responsivas durante o run.

**Arquivos:** `frontend/src/router.tsx`, `frontend/src/pages/MetricsPage.tsx`, `frontend/src/hooks/useWebSocket.ts`, `core/backtest/compare.py` (capital inicial)

---

## Track B — Cirurgia de estratégia (toda task = mudança + backtest A/B persistido)

### [ ] 9.5 — Experimento: smart_hodler em 1h/4h como timeframe primário

A alavanca mais promissora: reduzir frequência ~10x dilui o custo fixo por trade. Criar variante do smart_hodler decidindo no 1h (confirmação no 4h), rodar A/B vs 15m em ≥ 365 dias, custos ligados. Comparar retorno líquido, drawdown, nº de trades.

### [ ] 9.6 — Experimento: remover exit `momentum_fade` e afrouxar hard stop

`momentum_fade` é o pior exit (-0.68% médio) e o hard stop de 3% dispara em ruído (180x). A/B: (a) baseline, (b) sem momentum_fade, (c) sem momentum_fade + hard stop 5-6%, (d) trailing mais largo 3-4× ATR (combina com 9.5). Escolher pela combinação retorno líquido / drawdown.

### [ ] 9.7 — Cortar universo de símbolos: 34 → 4-6 majors líquidos

Config (`symbols.yaml` / `symbol_configs`): manter BTC, ETH, SOL, BNB (+ 1-2 a critério do backtest). Menos fricção de mínimos, menos churn, sinais de melhor qualidade. Desabilitar o resto, não deletar.

### [ ] 9.8 — Destravar a mean_reversion (0 trades em 10 semanas)

**Problema:** Entrada exige 5 condições simultâneas no 15m + confirmação horária `close > EMA50` que **contradiz** a premissa de capitulação (preço na banda inferior com RSI < 30 raramente coexiste com 1h acima da EMA50).

**Solução:**
- Instrumentar: logar por tick quais condições passaram/falharam (contador por condição).
- Backtest de contribuição marginal: remover uma condição por vez, medir impacto.
- Provável correção: remover ou substituir a confirmação horária contraditória; se após ajuste a estratégia não mostrar edge líquido no backtest, **desativar** em vez de manter código morto.

### [x] 9.9 — Gate de validação: walk-forward obrigatório antes do live ✅ 2026-07-19

**Implementado:**
- [x] `core/backtest/gate.py::validate_strategy(strategy, params)` — walk-forward de ≥365 dias / ≥3 folds com custos + filtros da 9.4 ligados; também disponível como task Celery `run_validation_gate` e CLI (`python -m core.backtest.gate`, exit 0=aprovado / 2=reprovado).
- [x] Critérios (todos obrigatórios): retorno líquido positivo em todo fold de teste; profit factor > 1.2 em todo fold que produziu um; nenhum fold com drawdown > 25%; nenhum fold com flag `overfit`; ≥365 dias e ≥3 folds avaliados.
- [x] Evidência persistida: 1 linha em `backtest_results` por fold de teste com `run_id` prefixado `gate:` e `config_snapshot` com params, veredito e checks.
- [x] `WalkForwardFold` estendido com `test_profit_factor`/`test_max_drawdown`/`test_metrics` (não serializados na API).
- [x] Documentado no README (seção "Validation Gate").
- [x] Testes: `tests/test_validation_gate.py` (aprovação, cada critério de rejeição, persistência).

Os endpoints `/optimize` e `/walk-forward` já existem (`core/backtest/optimize.py`). Formalizar o gate:

- Script/task `validate_strategy(strategy, params)` que roda walk-forward (≥ 365 dias, ≥ 3 folds, custos + 9.4 ligados).
- **Critério de aprovação:** retorno líquido positivo nos folds de teste (out-of-sample), profit factor > 1.2, sem fold com drawdown > 25%, flag `overfit` limpa.
- Resultado persistido em `backtest_results` com `run_id` prefixado `gate:`.
- Documentar no README: mudança de estratégia sem gate aprovado não sobe para o live.

### [x] 9.10 — Kill switch automático por drawdown de conta ✅ 2026-07-19

Hoje o kill switch é manual (`core/risk/kill_switch.py`). Adicionar trigger automático: se equity da conta cair X% (default 20%) do pico, ativar kill switch e alertar. Depende da 9.11 (equity account-level confiável para medir o drawdown).

**Implementado:**
- [x] Config `live_trading.account_kill_switch_drawdown` (default 0.20; 0 desabilita).
- [x] `sync_exchange_balances` (a cada ~1 min): compara a equity real com o pico dos snapshots account-level (dados da 9.11); no breach, marca `kill_switch_triggered` em **todos** os `StrategyStateRecord`, publica `KILL_SWITCH_TRIGGERED`, alerta via Telegram (`alert_kill_switch`) e trava (latch — não re-alerta enquanto ativo).
- [x] Tick: a detecção de mudança externa do kill switch virou bidirecional — além do reset externo, agora detecta trigger externo no DB e trava a memória via `restore_from_dict` antes de avaliar a estratégia.
- [x] `KillSwitch.trigger_external(reason, timestamp)` — trigger público idempotente.
- [x] Testes: breach ativa todas as estratégias; latch não re-dispara; drawdown pequeno não ativa; threshold 0 desabilita; trigger_external latcha e bloqueia.

---

## Ordem de execução sugerida

```
9.1 (backtest via Celery) ──► 9.2 (persistência) ──► 9.5/9.6/9.8 (experimentos A/B)
9.3 (reconciliação)       — independente, alta prioridade (custo direto)
9.4 (min-notional)        ──► pré-requisito para 9.9
9.7 (símbolos)            — config, imediato
9.9 (gate)                ──► bloqueia qualquer retorno ao live
9.11 (equity histórico)   — alta prioridade: sem ele não há visibilidade nem 9.10
9.10 (kill switch)        — depende de 9.11; antes de religar o live
9.12 (UI crash #185)      — depende de 9.1; ErrorBoundary pode ser feito já
```
