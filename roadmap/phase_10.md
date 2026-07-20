# Phase 10 — Piloto Autônomo Determinístico: Backtest em Cron + Gate com Veto de Qualidade

> **Goal:** Ciclo 100% autônomo e 100% determinístico: backtest roda em cron → resultados organizados em dossiê estruturado → regras determinísticas decidem ativar/desativar cada estratégia → flags de qualidade de dados vetam ativações suspeitas → decisão aplicada, justificada e arquivada. Cada decisão é reproduzível a partir do dossiê arquivado.
> **Motivação:** Automatizar o gate da 9.9 em loop contínuo, com trilha de auditoria completa de cada decisão.
> **Status:** Planning
> **Depende de:** 9.1 (backtest via Celery), 9.2 (persistência), 9.4 (custos realistas), 9.9 (critérios do gate) — **todas concluídas em 2026-07-19**.

---

## Decisões de design

| Decisão | Racional |
|---|---|
| **Sem AI no loop** (revisado 2026-07-19) | A versão anterior desta fase incluía um revisor LLM (DeepSeek) com poder de veto. Cortado: benefício marginal e não comprovado vs. custo estrutural alto (dependência externa não-determinística, fail-safes, prompts versionados, pinagem de modelo, ~40% da complexidade da fase). A função defensável do LLM — veto conservador em anomalias — é replicada deterministicamente pelas flags de qualidade de dados (10.2): mesma direção fail-safe, mas auditável e testável. O dossiê JSON autocontido deixa a porta aberta: um revisor AI pode ser plugado depois em modo **log-only** (opina, não decide, não veta) sem redesenhar nada. Modo "AI decide" (`judge`) foi avaliado e **rejeitado em definitivo**: LLM como juiz de métricas é não-determinístico e produz justificativa plausível para qualquer decisão. |
| **Veto de qualidade determinístico** | Qualquer flag de qualidade de dados no dossiê bloqueia ativação da estratégia naquele ciclo e dispara alerta. Na dúvida, não liga — pior caso é ficar fora do mercado, nunca perder dinheiro. |
| **Histerese anti-flapping + cooldown** | Ligar/desligar por janela rolante é meta-estratégia de perseguir performance recente. Ativação exige 2 ciclos consecutivos aprovados; desativação é imediata com 1 reprovação; após desativar, **cooldown de N dias** (default 7, `autonomy.reactivation_cooldown_days`) antes de poder reativar — mata oscilação na fronteira do gate. |
| **Modo sombra primeiro** | As primeiras semanas o sistema decide e arquiva mas **não aplica**. Só promove a modo ativo depois de auditar as decisões sombra. |
| **Escopo de atuação limitado** | O piloto só liga/desliga estratégias já existentes na whitelist. Nunca cria símbolo, nunca muda parâmetro, nunca aumenta tamanho de posição. |
| **Autonomia operacional, supervisão humana** | 100% autônomo significa nenhum humano no loop de decisão — não nenhum humano na supervisão. Toda mudança de estado notifica via Telegram; a meta-avaliação mensal (10.8) é o registro de bordo que justifica a confiança no resto. |

Modos de operação (`autonomy.mode` na config): `off` → `shadow` (decide, arquiva, não aplica) → `active` (decide e aplica).

---

## Tarefas

### [ ] 10.1 — Cron de backtest noturno

- Entrada no Celery Beat (ex: 03:00 UTC diário; configurável em `autonomy.schedule`).
- Reusa a task da 9.1 e o gate da 9.9: walk-forward para cada estratégia da whitelist sobre o universo de símbolos ativo, custos da 9.4 ligados.
- `run_id` padronizado: `auto:YYYYMMDD:<strategy>`. Persistido via 9.2.
- Lock distribuído (Redis) para nunca haver dois ciclos simultâneos; timeout e alerta se o run passar de N horas.

### [ ] 10.2 — Dossiê estruturado + flags de qualidade (o veto)

Novo módulo `core/autonomy/report.py`:
- JSON com: métricas walk-forward por estratégia (retorno líquido OOS, profit factor, max drawdown, nº trades, flag overfit), comparação com os últimos N ciclos, performance live/paper recente (da `trade_records`).
- **Flags de qualidade de dados — o mecanismo de veto da fase:** valores implausíveis (|retorno| > 1000%), folds com < X trades, candles faltando no período, capital de slot degenerado (< min_notional), divergência anômala entre ciclos consecutivos (ex: retorno OOS mudou > Y pontos sem mudança de código/config). Flag levantada ⇒ ativação bloqueada naquele ciclo + alerta.
- Mesmo dossiê alimenta o gate (10.3) e o arquivo (10.5) — uma única fonte de verdade por ciclo, autocontida (permite plugar revisor log-only no futuro sem redesign).

### [ ] 10.3 — Gate determinístico com histerese e cooldown

`core/autonomy/gate.py`:
- Critérios da 9.9 (retorno OOS positivo em todo fold, profit factor > 1.2, drawdown por fold < 25%, overfit limpo) + flags de qualidade limpas.
- Decisão por estratégia: `activate` / `keep` / `deactivate`:
  - ativar: 2 ciclos consecutivos aprovados **e** fora do cooldown de reativação;
  - desativar: 1 ciclo reprovado (imediato);
  - cooldown: `autonomy.reactivation_cooldown_days` (default 7) após desativação.
- Saída: decisão + quais critérios/flags passaram ou falharam (vai no dossiê e no arquivo). 100% reproduzível a partir do dossiê.

### [ ] 10.5 — Arquivo de decisões (auditoria futura)

- Nova tabela `autonomy_decisions`: `cycle_id`, `timestamp`, `backtest_run_id`, `strategy`, `verdict` + critérios e flags (jsonb), `final_decision`, `applied` (bool), `mode` (shadow/active).
- Endpoint `GET /api/autonomy/decisions` (filtros por estratégia/período) + página simples no dashboard listando ciclos e justificativas.
- Nada é sobrescrito: cada ciclo é uma linha nova por estratégia.

### [ ] 10.6 — Atuação + notificação

- Aplicar `final_decision` via flags de estratégia existentes (`strategy_configs` / kill switch por estratégia), publicando evento no canal `system`.
- **Toda mudança de estado (e toda ativação bloqueada por flag) notifica via alerting** (`core/alerting.py` → Telegram/webhook): estratégia, decisão, critérios decisivos, id do ciclo.
- Kill switch global (9.10) tem precedência absoluta: se ativo, o piloto não liga nada.

### [ ] 10.7 — Modo sombra e promoção

- Primeiras 2-4 semanas em `shadow`: ciclo completo rodando, decisões arquivadas, nada aplicado.
- Checklist de promoção para `active`: N ciclos sem crash, decisões sombra revisadas manualmente e consideradas razoáveis, comportamento das flags de qualidade auditado (nenhum falso-bloqueio sistemático).

### [ ] 10.8 — Meta-avaliação: o gate está acertando?

- Task mensal que cruza `autonomy_decisions` com a performance subsequente (live/paper): estratégias mantidas ligadas performaram melhor que as desligadas teriam performado?
- Métrica simples persistida por ciclo (ex: PnL evitado por desativações, PnL perdido por bloqueios errados).
- Sem AI no loop, a atribuição é limpa: toda decisão veio das regras — a meta-avaliação mede diretamente a qualidade dos critérios e das flags, e informa ajustes de threshold.

---

## Ordem de execução

```
(pré-requisitos 9.1, 9.2, 9.4, 9.9: ✅ concluídos)

10.1 (cron) ──► 10.2 (dossiê + flags) ──► 10.3 (gate) ──► 10.5 (arquivo)
10.6 (atuação)  — só depois de 10.5 funcionando em shadow
10.7 (sombra)   — gate de promoção obrigatório
10.8 (meta)     — 1º mês após promoção
```

**Lembrete honesto:** esta fase automatiza "não operar o que não está funcionando" — é gestão de risco, não geração de edge. Com as estratégias atuais (smart_hodler -4.5% / 30d, WR 16%), o comportamento autônomo correto no primeiro ciclo será **manter tudo desligado — e estará certo**. O motor lucrativo continua tendo que sair da Track B da phase 9; o piloto autônomo só garante que ele voe com evidência fresca e registro de bordo.
