# Phase 7 — Shared Pool Capital Model

> **Goal:** Substituir o modelo de capital isolado por par/estratégia por uma pool comum de USDT, com limite de posições simultâneas configurável.
> **Motivação:** Com capital pequeno (~$50), dividir por estratégia × símbolo fragmenta demais — ordens ficam abaixo do mínimo da Binance ($5–10). O modelo de pool compartilhada é mais simples e eficiente.
> **Status:** Complete

---

## Modelo atual vs novo

| Aspecto | Atual (isolado) | Novo (pool) |
|---------|-----------------|-------------|
| Capital por par | `total / (estratégias × símbolos)` | `total / max_concurrent_positions` |
| Quem controla | `compute_allocations()` + weights | `Portfolio.available_capital` |
| Scale-in | Limitado ao slice do par | SmartHodler: entrada = slot/3, pode escalar 3x = 1 slot |
| Guarda | Per-symbol exposure cap | `posições_abertas < max_concurrent_positions` |
| Config | `allocation_pct` por estratégia | `max_concurrent_positions` no RiskConfig |

---

## Tarefas

### [x] 7.1 — Config: `max_concurrent_positions`

**Arquivo:** `core/config.py` — `RiskConfig`

Novo campo:
```python
max_concurrent_positions: int = Field(default=4, ge=1, le=20)
```

- Representa o número máximo de posições abertas simultâneas (across all strategies/symbols)
- Cada posição = 1 slot, independente de scale-in
- Tamanho do slot = `saldo_exchange / max_concurrent_positions`

**UI:** Já aparece automaticamente na Settings page (namespace `risk`).

**Testes:**
- Validação min/max do campo

---

### [x] 7.2 — `Portfolio.available_capital` → pool compartilhada

**Arquivo:** `core/models.py` — `Portfolio`

Mudar:
```python
# Antes
@property
def available_capital(self) -> Decimal:
    return self.usdt_balance

# Depois
@property
def available_capital(self) -> Decimal:
    return self._slot_capital

# Novo campo
slot_capital: Decimal = Decimal("0")
```

`slot_capital` é calculado externamente (pelo tick) como:
```
saldo_exchange / max_concurrent_positions
```

E injetado no PortfolioManager antes de cada tick. Assim o risk engine chama `portfolio.available_capital × entry_pct` e tudo downstream funciona sem mudança.

**Testes:**
- `available_capital` retorna `slot_capital`
- Risk engine sizing usa slot_capital corretamente

---

### [x] 7.3 — Guard: limite de posições simultâneas

**Arquivo:** `core/risk/_sizing.py` ou `core/risk/engine.py`

Novo check antes de abrir posição:
```python
def _is_max_positions_reached(self, portfolio: Portfolio) -> bool:
    return len(portfolio.positions) >= self._max_concurrent_positions
```

Se atingiu o limite, `evaluate()` retorna `RiskDecision(action=REJECT, reason="max concurrent positions reached")`.

**Nota:** `portfolio.positions` conta posições de TODOS os pares do worker (não só do par atual). Verificar se o `portfolio` passado ao risk engine tem visão global ou isolada — se isolada, o guard precisa consultar o DB.

**Testes:**
- Com 4 posições abertas, nova entrada é rejeitada
- Com 3 posições abertas, nova entrada é aceita

---

### [x] 7.4 — Tick: simplificar `_resolve_allocated_capital()`

**Arquivo:** `core/tasks/_tick.py`

Substituir toda a lógica de allocation por:
```python
def _resolve_allocated_capital(settings) -> Decimal:
    if settings.live_trading.enabled:
        total = _get_live_total_capital(settings)
    else:
        total = Decimal(str(settings.paper_trading.initial_capital))
    max_pos = settings.risk.max_concurrent_positions
    return (total / max_pos).quantize(Decimal("0.01"), rounding=ROUND_DOWN)
```

O resultado é passado como `initial_capital` pro PortfolioManager (ou atualizado via `slot_capital` no Portfolio).

Remover:
- Import de `compute_allocations`
- Toda a lógica de `enabled_strategies`, `configs`, `regime_kwargs`, `weights`
- A call `compute_allocations(...)`

Manter:
- `_get_live_total_capital()` (fetch + cache do saldo exchange)
- `_get_enabled_strategy_pairs()` (necessário para saber quais pares rodar)

**Testes:**
- `_resolve_allocated_capital` retorna `total / max_concurrent_positions`
- Não depende de allocation weights

---

### [x] 7.5 — Remover código morto

**Deletar:**
- `core/allocation.py` (200 linhas)
- `tests/test_allocation.py`

**Limpar referências:**
- `core/tasks/_tick.py` — remover `from core.allocation import compute_allocations`
- `core/backtest/compare.py` — adaptar para usar slot model
- `core/backtest/multi_strategy.py` — adaptar para usar slot model
- `api/routes/config.py` — remover `allocation_pct` do `StrategySummary`
- `frontend/src/api/queries.ts` — remover `allocation_pct` do `StrategySummary`

**Config cleanup:**
- Remover `allocation_pct` de `SmartHodlerConfig`, `MeanReversionStrategyConfig`, `BearGuardConfig`
- Remover regime rebalancing de allocation (`regime_adjusted_weights`)
- Limpar `RegimeConfig` dos campos `rebalance_enabled`, `regime_shift_pct`, `strategy_regime_map` se não usados em outro lugar

**Testes:**
- Remover/atualizar testes que dependem de `compute_allocations`

---

### [x] 7.6 — Backtest: adaptar para slot model

**Arquivos:** `core/backtest/compare.py`, `core/backtest/multi_strategy.py`

Substituir `compute_allocations()` por:
```python
slot_capital = total_capital / max_concurrent_positions
```

E distribuir `slot_capital` igualmente para cada (strategy, symbol) pair no backtest. O backtest continua rodando cada par independente mas com o mesmo `slot_capital`.

**Testes:**
- Backtest compare e multi-strategy usam slot model

---

### [x] 7.7 — Testes de integração

**Verificar fluxo completo:**
1. Exchange tem $50 USDT, `max_concurrent_positions = 4`
2. SmartHodler sinaliza buy em BTCUSDT → ordem de ~$4.17 (slot/3, entry_pct=0.33)
3. MeanReversion sinaliza buy em ETHUSDT → ordem de ~$12.50 (slot inteiro)
4. Com 4 posições abertas, próximo sinal de buy é rejeitado
5. Uma posição fecha → slot libera → próximo buy é aceito

---

## Ordem de execução

```
7.1 (config) ──► 7.2 (portfolio model)
                 7.3 (guard)
7.1 + 7.2    ──► 7.4 (tick simplification)
7.4          ──► 7.5 (remove dead code)
7.4          ──► 7.6 (backtest adaptation)
*            ──► 7.7 (integration tests)
```

---

## Riscos e edge cases

| Risco | Mitigação |
|-------|-----------|
| Scale-in ultrapassa slot | Risk engine já cap com `max_position_size_pct` — manter |
| Todas posições no mesmo ativo | `max_symbol_exposure_usdt` já existente continua ativo |
| Saldo exchange muda mid-cycle | Cache de 5 min (`_CAPITAL_CACHE_TTL`) já amortece |
| Paper trading sem exchange | Usa `paper_trading.initial_capital` como total |
