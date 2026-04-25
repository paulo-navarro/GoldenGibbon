# Phase 3 — Profitability Upgrades

## 1. Regime-based strategy gating

Usar o `RegimeDetector` (já implementado em `core/regime.py`) para impedir que estratégias operem no regime errado. Mean Reversion não deve abrir posições em mercado de tendência; Smart Hodler não deve abrir em mercado lateral.

**Regra:** O gate aplica-se apenas a **novas entradas** (BUY quando state=FLAT). Posições já abertas continuam sendo geridas normalmente (exits, stops, scale-ins).

### Tarefas

- [ ] **1.1 — Adicionar `RegimeDetector` ao `_TickComponents`**
  - Arquivo: `core/tasks/__init__.py`
  - No `__slots__` da `_TickComponents` (linha 112), adicionar `regime_detector`
  - No `__init__` (linha 114), aceitar e guardar o novo campo
  - Na função `_build_components()` (linha ~289), instanciar `RegimeDetector` a partir de `settings.regime` (`adx_trending_threshold`, `adx_ranging_threshold`, `smoothing_window`) e passar ao `_TickComponents`

- [ ] **1.2 — Gate no tick loop: bloquear entrada em regime incompatível**
  - Arquivo: `core/tasks/__init__.py`, bloco "3. Strategy decision" (linha ~1065)
  - Após `signal = comp.strategy.evaluate(...)`, se `signal == Signal.BUY` e `comp.strategy.state` acabou de transicionar para `POSITION` (era `FLAT`):
    1. Chamar `comp.regime_detector.detect(market_data.indicators)`
    2. Consultar `settings.regime.strategy_regime_map[strategy_name]` para obter o regime favorável (`"trending"` ou `"ranging"`)
    3. Se o regime detectado for o **oposto** do favorável (trending vs ranging ou vice-versa), override o signal para `HOLD` e reverter o state da strategy para `FLAT`
    4. Se regime for `UNCERTAIN`, permitir a entrada (não bloquear)
  - Logar `regime.gate_blocked` com `strategy`, `symbol`, `detected_regime`, `expected_regime`

- [ ] **1.3 — Adicionar campo `regime_gating_enabled` ao `RegimeConfig`**
  - Arquivo: `core/config.py`, classe `RegimeConfig` (linha ~381)
  - Novo campo `regime_gating_enabled: bool = Field(default=True)`
  - O gate no tick loop (1.2) só ativa se esse campo for `True`
  - Aparece automaticamente na UI de Settings (namespace `regime`)

- [ ] **1.4 — Publicar evento de regime no WebSocket**
  - Arquivo: `core/tasks/__init__.py`, após o regime detect (1.2)
  - Publicar evento `REGIME_DETECTED` no canal `STRATEGY` com `symbol`, `strategy`, `regime`, `confidence`, `adx_value`
  - Adicionar `REGIME_DETECTED = "regime_detected"` ao `EventType` em `core/events.py`

- [ ] **1.5 — Exibir regime atual no card da Strategy Page**
  - Arquivo: `frontend/src/pages/StrategyPage.tsx`
  - No header do `StrategyCard` (linha ~117), adicionar um `Chip` com o regime (`trending` / `ranging` / `uncertain`)
  - Cores: trending=success, ranging=warning, uncertain=default
  - Consumir do Zustand store (novo campo `regimes` no `strategyStore`)

- [ ] **1.6 — Testes unitários do regime gate**
  - Arquivo: `tests/test_regime_gate.py`
  - Test: Smart Hodler BUY bloqueado quando regime=RANGING
  - Test: Mean Reversion BUY bloqueado quando regime=TRENDING
  - Test: Ambos permitidos quando regime=UNCERTAIN
  - Test: Gate desabilitado quando `regime_gating_enabled=False`
  - Test: Posições existentes (SELL_FULL, SELL_HALF, scale-in) nunca bloqueadas

---

## 2. Trailing stop + smart time stop para Mean Reversion

Hoje o Mean Reversion não tem trailing stop — depende exclusivamente dos sinais de SELL_FULL (upper BB / RSI overbought / ADX regime shift) e SELL_HALF (middle BB). O time stop (16 candles) fecha posições cegas ao lucro/prejuízo.

### Tarefas

- [ ] **2.1 — Habilitar trailing stop para Mean Reversion no `check_stops()`**
  - Arquivo: `core/risk/__init__.py`, método `check_stops()` (linha ~338)
  - Atualmente, o bloco Mean Reversion (linha 339-363) retorna `StopCheckResult()` sem trailing stop
  - Mover o bloco de trailing stop (linhas 365-401) para **fora** do branch `if strategy == "mean_reversion"`, tornando-o comum a ambas as estratégias
  - Manter o time stop como check separado antes do trailing
  - O trailing stop ATR multiplier do MR já é lido do config (linha 100-107), basta usar

- [ ] **2.2 — Adicionar config `trailing_stop_enabled` por estratégia**
  - Arquivo: `core/config.py`, classes `SmartHodlerConfig` e `MeanReversionStrategyConfig`
  - Novo campo: `trailing_stop_enabled: bool = Field(default=True)`
  - Default `True` para ambas (Smart Hodler já usa, Mean Reversion passa a usar)
  - Em `check_stops()`, verificar `self._trailing_enabled` antes de rodar trailing stop
  - Ler o campo no `__init__` do `RiskEngine` e guardar como `self._trailing_enabled`

- [ ] **2.3 — Configurar ATR multiplier específico para Mean Reversion**
  - Arquivo: `core/config.py`, classe `MeanReversionStrategyConfig`
  - Adicionar `trailing_stop_atr_multiplier: float = Field(default=2.5)` (mais largo que SH default de 2.0, porque MR espera swings maiores de volta à média)
  - O `RiskEngine.__init__` já lê esse campo do strategy_config (linha 100-107)

- [ ] **2.4 — Smart time stop: não fechar posições em lucro**
  - Arquivo: `core/risk/__init__.py`, bloco time stop (linha ~338-361)
  - Antes de fechar por time stop, calcular PnL: `unrealized = (close - position.entry_price) / position.entry_price`
  - Se `unrealized > 0` (posição em lucro), **não acionar** o time stop — deixar trailing stop proteger
  - Se `unrealized <= 0` (posição flat ou em prejuízo), acionar time stop normalmente (tese invalidada)
  - Logar `risk.time_stop_skipped_profitable` quando pular

- [ ] **2.5 — Adicionar config `time_stop_skip_profitable` ao `MeanReversionStrategyConfig`**
  - Arquivo: `core/config.py`, classe `MeanReversionStrategyConfig`
  - Novo campo: `time_stop_skip_profitable: bool = Field(default=True)`
  - Ler no `RiskEngine.__init__` e guardar como `self._time_stop_skip_profitable`
  - A lógica de 2.4 só ativa se esse campo for `True`

- [ ] **2.6 — Testes unitários**
  - Arquivo: `tests/test_mr_trailing_stop.py`
  - Test: MR posição com trailing stop ativo, close cai abaixo → CLOSE com ExitReason.TRAILING_STOP
  - Test: MR trailing stop ratchet: highest_close sobe, trailing acompanha, nunca desce
  - Test: MR time stop NÃO fecha posição quando PnL > 0 e `time_stop_skip_profitable=True`
  - Test: MR time stop FECHA posição quando PnL <= 0
  - Test: MR time stop FECHA posição quando `time_stop_skip_profitable=False` independente de PnL
  - Test: `trailing_stop_enabled=False` desabilita trailing para a estratégia

---

## 3. Break-even ratchet no hard stop

O hard stop atual é fixo em `entry_price × (1 - hard_stop_pct)`. Uma posição que sobe +5% e depois cai pode devolver todo o ganho e ainda fechar com -3% de loss. O ratchet move o hard stop para break-even (e além) conforme o preço avança.

### Tarefas

- [ ] **3.1 — Implementar ratchet no `check_stops()`**
  - Arquivo: `core/risk/__init__.py`, método `check_stops()`, antes da verificação de hard stop (linha ~315)
  - Calcular profit percent: `profit_pct = (close - position.entry_price) / position.entry_price`
  - Aplicar ratchet em 2 níveis:
    - **Nível 1 (break-even):** Se `profit_pct >= breakeven_trigger_pct` (default 2%), mover hard stop para `entry_price` (break-even)
    - **Nível 2 (lock-in):** Se `profit_pct >= lockin_trigger_pct` (default 4%), mover hard stop para `entry_price × (1 + lockin_stop_pct)` (default 1%)
  - O novo hard stop nunca desce — usar `max(position.hard_stop_price, new_stop)`
  - Retornar o hard stop atualizado no `StopCheckResult` para que o caller persista via `pm.update_stops()`

- [ ] **3.2 — Propagar hard stop atualizado no tick loop**
  - Arquivo: `core/tasks/__init__.py`, bloco onde `update_stops` é chamado (linha ~1058-1063)
  - Atualmente chama `comp.pm.update_stops(symbol, highest_close=..., trailing_stop_price=...)`
  - Adicionar `hard_stop_price=stop_result.hard_stop_price` ao call
  - Adicionar campo `hard_stop_price: Optional[Decimal] = None` ao `StopCheckResult` em `core/models.py`

- [ ] **3.3 — Adicionar campos de ratchet ao `StopCheckResult`**
  - Arquivo: `core/models.py`, classe `StopCheckResult`
  - Adicionar: `hard_stop_price: Optional[Decimal] = None`
  - O `check_stops()` preenche esse campo quando o ratchet move o hard stop

- [ ] **3.4 — Configuração de ratchet por estratégia**
  - Arquivo: `core/config.py`, classes `SmartHodlerConfig` e `MeanReversionStrategyConfig`
  - Novos campos em ambas:
    ```
    breakeven_ratchet_enabled: bool = Field(default=True)
    breakeven_trigger_pct: float = Field(default=0.02, description="Move stop to break-even at this profit %")
    lockin_trigger_pct: float = Field(default=0.04, description="Lock in profit at this profit %")
    lockin_stop_pct: float = Field(default=0.01, description="Hard stop moves to entry + this %")
    ```
  - Ler no `RiskEngine.__init__` e guardar como atributos privados

- [ ] **3.5 — Logar ratchet events**
  - Quando o hard stop é movido, logar `risk.ratchet_breakeven` ou `risk.ratchet_lockin` com:
    `symbol`, `old_stop`, `new_stop`, `profit_pct`, `entry_price`, `close`

- [ ] **3.6 — Alertar ratchet via Telegram (opcional)**
  - Arquivo: `core/tasks/__init__.py`, `_send_tick_alerts()`
  - Se o hard stop foi ratchetado neste tick (comparar old vs new), enviar alerta informativo
  - Usar `alerter.send()` com mensagem tipo `🔒 BTCUSDT: stop moved to break-even @ 65000`
  - Controlado por novo campo `alert_on_ratchet: bool = Field(default=False)` no `AlertingConfig`

- [ ] **3.7 — Testes unitários**
  - Arquivo: `tests/test_breakeven_ratchet.py`
  - Test: Posição com +1% profit → hard stop inalterado (abaixo do trigger)
  - Test: Posição com +2% profit → hard stop movido para entry_price (break-even)
  - Test: Posição com +4% profit → hard stop movido para entry_price × 1.01 (lock-in)
  - Test: Hard stop nunca desce — se já está em break-even e profit recua para +1.5%, mantém break-even
  - Test: Ratchet desabilitado (`breakeven_ratchet_enabled=False`) → hard stop nunca muda
  - Test: Hard stop hit após ratchet → ExitReason.HARD_STOP com preço correto
  - Test: Funciona para ambas as estratégias (SH e MR)
