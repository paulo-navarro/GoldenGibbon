# Phase 10 — Piloto Autônomo: Backtest em Cron + Julgamento por AI (DeepSeek)

> **Goal:** Ciclo 100% autônomo: backtest roda em cron → resultados organizados em relatório estruturado → regras determinísticas propõem ativar/desativar cada estratégia → DeepSeek revisa e pode vetar → decisão final aplicada, justificada e arquivada para auditoria futura.
> **Motivação:** Automatizar o gate da 9.9 em loop contínuo, com trilha de auditoria completa de cada decisão.
> **Status:** Planning
> **Depende de:** 9.1 (backtest via Celery), 9.2 (persistência), 9.4 (custos realistas), 9.9 (critérios do gate). Sem essas, o cron automatiza números não confiáveis.

---

## Decisões de design (avaliação da ideia, 2026-07-08)

| Decisão | Racional |
|---|---|
| **Regras decidem, AI revisa com veto** | LLM como juiz único de métricas é não-determinístico e produz justificativa plausível para qualquer decisão. Regras são auditáveis e reproduzíveis; o LLM pega o que regras não pegam (anomalias, contradições entre runs, bugs de dados). |
| **Autoridade assimétrica** | O DeepSeek pode **vetar ativação** (direção conservadora), nunca forçar uma. Ativar exige regras + AI concordando; desativar basta um dos dois. Pior caso de alucinação = ficar fora do mercado, nunca perder dinheiro. |
| **Histerese anti-flapping** | Ligar/desligar por janela rolante é meta-estratégia de perseguir performance recente. Ativação exige 2 ciclos consecutivos aprovados; desativação é imediata com 1 reprovação. |
| **Modo sombra primeiro** | As primeiras semanas o sistema decide e arquiva mas **não aplica**. Só promove a modo ativo depois de auditar as decisões sombra. |
| **Escopo de atuação limitado** | O piloto só liga/desliga estratégias já existentes na whitelist. Nunca cria símbolo, nunca muda parâmetro, nunca aumenta tamanho de posição. |

Modos de operação (`autonomy.mode` na config): `off` → `shadow` (decide, arquiva, não aplica) → `veto` (regras decidem, AI veta — **recomendado**) → `judge` (AI decide sozinha; existe, mas desaconselhado — documentar o porquê).

---

## Tarefas

### [ ] 10.1 — Cron de backtest noturno

- Entrada no Celery Beat (ex: 03:00 UTC diário; configurável em `autonomy.schedule`).
- Reusa a task da 9.1: walk-forward (critérios da 9.9) para cada estratégia da whitelist sobre o universo de símbolos ativo, custos da 9.4 ligados.
- `run_id` padronizado: `auto:YYYYMMDD:<strategy>`. Persistido via 9.2.
- Lock distribuído (Redis) para nunca haver dois ciclos simultâneos; timeout e alerta se o run passar de N horas.

### [ ] 10.2 — Relatório estruturado (o "dossiê")

Novo módulo `core/autonomy/report.py`:
- JSON com: métricas walk-forward por estratégia (retorno líquido OOS, profit factor, max drawdown, nº trades, flag overfit), comparação com os últimos N ciclos, performance live/paper recente (da `trade_records`), e **flags de qualidade de dados** (valores implausíveis tipo |retorno| > 1000%, folds com < X trades, candles faltando).
- Mesmo dossiê alimenta as regras (10.3), o DeepSeek (10.4) e o arquivo (10.5) — uma única fonte de verdade por ciclo.

### [ ] 10.3 — Gate determinístico com histerese

`core/autonomy/gate.py`:
- Critérios da 9.9 (retorno OOS positivo, profit factor > 1.2, drawdown por fold < 25%, overfit limpo) + flags de qualidade de dados limpos.
- Proposta por estratégia: `activate` / `keep` / `deactivate`, com histerese: ativar exige 2 ciclos consecutivos aprovados; reprovar 1 ciclo desativa.
- Saída: decisão proposta + quais critérios passaram/falharam (vai no dossiê e no arquivo).

### [ ] 10.4 — Revisor DeepSeek

`core/autonomy/reviewer.py`:
- Cliente da API DeepSeek (key em `.env`: `DEEPSEEK_API_KEY`; modelo em `autonomy.model`). Envia dossiê + decisão proposta pelas regras.
- Resposta **estruturada obrigatória** (JSON schema): `verdict` (`approve`/`veto`), `confidence`, `justification` (texto), `anomalies` (lista). Validar com Pydantic; retry com backoff; resposta malformada após retries = tratar como indisponível.
- **Fail-safe se a API estiver fora:** ativações propostas ficam bloqueadas (sem revisão não liga nada novo); desativações propostas pelas regras aplicam mesmo assim. Alerta emitido.
- Prompt versionado em arquivo (`core/autonomy/prompts/`), com a versão registrada no arquivo de decisões — para poder correlacionar mudanças de prompt com qualidade de decisão.
- Só métricas agregadas no prompt — nunca API keys, saldos detalhados ou dados de conta.

### [ ] 10.5 — Arquivo de decisões (auditoria futura)

- Nova tabela `autonomy_decisions`: `cycle_id`, `timestamp`, `backtest_run_id`, `strategy`, `rule_verdict` + critérios (jsonb), `llm_verdict`, `llm_justification`, `llm_confidence`, `llm_model`, `prompt_version`, `final_decision`, `applied` (bool), `mode` (shadow/veto/judge).
- Endpoint `GET /api/autonomy/decisions` (filtros por estratégia/período) + página simples no dashboard listando ciclos e justificativas.
- Nada é sobrescrito: cada ciclo é uma linha nova por estratégia.

### [ ] 10.6 — Atuação + notificação

- Aplicar `final_decision` via flags de estratégia existentes (`strategy_configs` / kill switch por estratégia), publicando evento no canal `system`.
- **Toda mudança de estado (e todo veto) notifica via alerting** (`core/alerting.py` → Telegram/webhook): estratégia, decisão, resumo da justificativa, link do ciclo.
- Kill switch global (9.10) tem precedência absoluta: se ativo, o piloto não liga nada.

### [ ] 10.7 — Modo sombra e promoção

- Primeiras 2-4 semanas em `shadow`: ciclo completo rodando, decisões arquivadas, nada aplicado.
- Checklist de promoção para `veto`: N ciclos sem crash, decisões sombra revisadas manualmente e consideradas razoáveis, fail-safes testados (derrubar a API do DeepSeek de propósito e observar o comportamento).

### [ ] 10.8 — Meta-avaliação: o julgador está acertando?

- Task mensal que cruza `autonomy_decisions` com a performance subsequente (live/paper): estratégias mantidas ligadas performaram melhor que as desligadas teriam performado?
- Métrica simples persistida por ciclo (ex: PnL evitado por desativações, PnL perdido por vetos errados).
- É isso que responde, com dados, se o DeepSeek merece ser promovido de `veto` a algo mais — ou rebaixado.

---

## Ordem de execução

```
(pré-requisitos: 9.1, 9.2, 9.4, 9.9 concluídas)

10.1 (cron) ──► 10.2 (dossiê) ──► 10.3 (gate) ──► 10.5 (arquivo)
                                    └──► 10.4 (DeepSeek) ──┘
10.6 (atuação)  — só depois de 10.5 funcionando em shadow
10.7 (sombra)   — gate de promoção obrigatório
10.8 (meta)     — 1º mês após promoção
```

**Lembrete honesto:** esta fase automatiza "não operar o que não está funcionando" — é gestão de risco, não geração de edge. O motor lucrativo continua tendo que sair da Track B da phase 9; o piloto autônomo só garante que ele voe com evidência fresca e registro de bordo.
