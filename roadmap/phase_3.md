# Phase 3 — Profitability Upgrades

## 1. Regime-based strategy gating

Usar o `RegimeDetector` (já implementado em `core/regime.py`) para impedir que estratégias operem no regime errado. Mean Reversion não deve abrir posições em mercado de tendência; Smart Hodler não deve abrir em mercado lateral.

**Regra:** O gate aplica-se apenas a **novas entradas** (BUY quando state=FLAT). Posições já abertas continuam sendo geridas normalmente (exits, stops, scale-ins).

### Tarefas

- [x] **1.1 — Adicionar `RegimeDetector` ao `_TickComponents`**
  - Arquivo: `core/tasks/__init__.py`
  - No `__slots__` da `_TickComponents` (linha 112), adicionar `regime_detector`
  - No `__init__` (linha 114), aceitar e guardar o novo campo
  - Na função `_build_components()` (linha ~289), instanciar `RegimeDetector` a partir de `settings.regime` (`adx_trending_threshold`, `adx_ranging_threshold`, `smoothing_window`) e passar ao `_TickComponents`

- [x] **1.2 — Gate no tick loop: bloquear entrada em regime incompatível**
  - Arquivo: `core/tasks/__init__.py`, bloco "3. Strategy decision" (linha ~1065)
  - Após `signal = comp.strategy.evaluate(...)`, se `signal == Signal.BUY` e `comp.strategy.state` acabou de transicionar para `POSITION` (era `FLAT`):
    1. Chamar `comp.regime_detector.detect(market_data.indicators)`
    2. Consultar `settings.regime.strategy_regime_map[strategy_name]` para obter o regime favorável (`"trending"` ou `"ranging"`)
    3. Se o regime detectado for o **oposto** do favorável (trending vs ranging ou vice-versa), override o signal para `HOLD` e reverter o state da strategy para `FLAT`
    4. Se regime for `UNCERTAIN`, permitir a entrada (não bloquear)
  - Logar `regime.gate_blocked` com `strategy`, `symbol`, `detected_regime`, `expected_regime`

- [x] **1.3 — Adicionar campo `regime_gating_enabled` ao `RegimeConfig`**
  - Arquivo: `core/config.py`, classe `RegimeConfig` (linha ~381)
  - Novo campo `regime_gating_enabled: bool = Field(default=True)`
  - O gate no tick loop (1.2) só ativa se esse campo for `True`
  - Aparece automaticamente na UI de Settings (namespace `regime`)

- [x] **1.4 — Publicar evento de regime no WebSocket**
  - Arquivo: `core/tasks/__init__.py`, após o regime detect (1.2)
  - Publicar evento `REGIME_DETECTED` no canal `STRATEGY` com `symbol`, `strategy`, `regime`, `confidence`, `adx_value`
  - Adicionar `REGIME_DETECTED = "regime_detected"` ao `EventType` em `core/events.py`

- [x] **1.5 — Exibir regime atual no card da Strategy Page**
  - Arquivo: `frontend/src/pages/StrategyPage.tsx`
  - No header do `StrategyCard` (linha ~117), adicionar um `Chip` com o regime (`trending` / `ranging` / `uncertain`)
  - Cores: trending=success, ranging=warning, uncertain=default
  - Consumir do Zustand store (novo campo `regimes` no `strategyStore`)

- [x] **1.6 — Testes unitários do regime gate**
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

- [x] **3.1 — Implementar ratchet no `check_stops()`**
  - Arquivo: `core/risk/__init__.py`, método `check_stops()`, antes da verificação de hard stop (linha ~315)
  - Calcular profit percent: `profit_pct = (close - position.entry_price) / position.entry_price`
  - Aplicar ratchet em 2 níveis:
    - **Nível 1 (break-even):** Se `profit_pct >= breakeven_trigger_pct` (default 2%), mover hard stop para `entry_price` (break-even)
    - **Nível 2 (lock-in):** Se `profit_pct >= lockin_trigger_pct` (default 4%), mover hard stop para `entry_price × (1 + lockin_stop_pct)` (default 1%)
  - O novo hard stop nunca desce — usar `max(position.hard_stop_price, new_stop)`
  - Retornar o hard stop atualizado no `StopCheckResult` para que o caller persista via `pm.update_stops()`

- [x] **3.2 — Propagar hard stop atualizado no tick loop**
  - Arquivo: `core/tasks/__init__.py`, bloco onde `update_stops` é chamado (linha ~1058-1063)
  - Atualmente chama `comp.pm.update_stops(symbol, highest_close=..., trailing_stop_price=...)`
  - Adicionar `hard_stop_price=stop_result.hard_stop_price` ao call
  - Adicionar campo `hard_stop_price: Optional[Decimal] = None` ao `StopCheckResult` em `core/models.py`

- [x] **3.3 — Adicionar campos de ratchet ao `StopCheckResult`**
  - Arquivo: `core/models.py`, classe `StopCheckResult`
  - Adicionar: `hard_stop_price: Optional[Decimal] = None`
  - O `check_stops()` preenche esse campo quando o ratchet move o hard stop

- [x] **3.4 — Configuração de ratchet por estratégia**
  - Arquivo: `core/config.py`, classes `SmartHodlerConfig` e `MeanReversionStrategyConfig`
  - Novos campos em ambas:
    ```
    breakeven_ratchet_enabled: bool = Field(default=True)
    breakeven_trigger_pct: float = Field(default=0.02, description="Move stop to break-even at this profit %")
    lockin_trigger_pct: float = Field(default=0.04, description="Lock in profit at this profit %")
    lockin_stop_pct: float = Field(default=0.01, description="Hard stop moves to entry + this %")
    ```
  - Ler no `RiskEngine.__init__` e guardar como atributos privados

- [x] **3.5 — Logar ratchet events**
  - Quando o hard stop é movido, logar `risk.ratchet_breakeven` ou `risk.ratchet_lockin` com:
    `symbol`, `old_stop`, `new_stop`, `profit_pct`, `entry_price`, `close`

- [x] **3.6 — Testes unitários**
  - Arquivo: `tests/test_breakeven_ratchet.py`
  - Test: Posição com +1% profit → hard stop inalterado (abaixo do trigger)
  - Test: Posição com +2% profit → hard stop movido para entry_price (break-even)
  - Test: Posição com +4% profit → hard stop movido para entry_price × 1.01 (lock-in)
  - Test: Hard stop nunca desce — se já está em break-even e profit recua para +1.5%, mantém break-even
  - Test: Ratchet desabilitado (`breakeven_ratchet_enabled=False`) → hard stop nunca muda
  - Test: Hard stop hit após ratchet → ExitReason.HARD_STOP com preço correto
  - Test: Funciona para ambas as estratégias (SH e MR)

---

## 4. Exchange-side stop orders (STOP_LOSS on Binance)

Hoje os stops (hard stop, trailing stop) são verificados apenas no tick de 15min, no close do candle. Se o preço despencar entre ticks, o bot fica cego por até 15 minutos. Essa feature coloca uma ordem `STOP_LOSS_LIMIT` diretamente no livro de ordens da Binance no momento da compra, e a atualiza sempre que o ratchet/trailing move o stop. A exchange monitora o preço em tempo real e executa instantaneamente se o trigger for atingido.

**Escopo:** Apenas live mode (`BinanceExecutor`). O `PaperExecutor` continua com a lógica atual (stops verificados no tick), pois não existe exchange real para hospedar a ordem.

**Tipo de ordem:** `STOP_LOSS_LIMIT` (Binance spot não suporta `STOP_MARKET`). Usa `stopPrice` como trigger e `price` com margem de slippage para garantir fill.

**Sync de estado:** A detecção de fill de uma stop que disparou entre ticks é feita no início de cada tick via REST poll (`GET /api/v3/order`). Se a stop já foi preenchida, o portfólio é sincronizado antes de qualquer outra lógica.

### Tarefas

- [x] **4.1 — Adicionar `STOP_LOSS_LIMIT` ao `OrderType` enum**
  - Arquivo: `core/models.py`, classe `OrderType` (linha 42)
  - Adicionar: `STOP_LOSS_LIMIT = "stop_loss_limit"`
  - Esse tipo é usado somente para ordens de proteção colocadas na exchange, nunca para buy/sell direto

- [x] **4.2 — Adicionar campo `exchange_stop_order_id` ao `Position`**
  - Arquivo: `core/models.py`, classe `Position` (linha 280)
  - Novo campo: `exchange_stop_order_id: Optional[str] = None`
  - Armazena o `orderId` da Binance da stop order ativa para esta posição
  - Precisa ser persistido no `PositionRecord` do banco (verificar `core/db/` models)

- [x] **4.3 — Adicionar `exchange_stop_order_id` ao `PositionRecord` do DB**
  - Arquivo: modelo de persistência de posição (SQLAlchemy / SQLite)
  - Adicionar coluna `exchange_stop_order_id TEXT NULL`
  - Criar migration se o projeto usar migrations, senão adicionar ao schema
  - Garantir que `open_position()`, `update_stops()`, e `close_position()` no `PortfolioManager` leem/escrevem esse campo

- [x] **4.4 — Implementar `place_stop_order()` no `BinanceExecutor`**
  - Arquivo: `core/execution/binance.py`
  - Novo método privado:
    ```python
    def _place_stop_order(
        self, symbol: str, quantity: Decimal,
        stop_price: Decimal, limit_price: Decimal,
    ) -> Optional[str]:
    ```
  - Monta params: `type=STOP_LOSS_LIMIT`, `side=SELL`, `stopPrice`, `price`, `quantity`, `timeInForce=GTC`
  - Usa `self._signed_request("POST", "/api/v3/order", params)` (mesmo padrão do `_place_and_fill`)
  - Aplica `_format_quantity()` e `_format_price()` para respeitar LOT_SIZE e PRICE_FILTER
  - `limit_price` = `stop_price × (1 - stop_limit_slippage)` onde slippage default = 0.5% (margem para garantir fill em queda rápida)
  - Retorna `exchange_order_id` (string) em caso de sucesso, `None` em caso de falha
  - Logar `binance.stop_order_placed` com `symbol`, `stop_price`, `limit_price`, `quantity`, `order_id`

- [x] **4.5 — Implementar `cancel_stop_order()` no `BinanceExecutor`**
  - Arquivo: `core/execution/binance.py`
  - Novo método privado:
    ```python
    def _cancel_stop_order(self, symbol: str, order_id: str) -> bool:
    ```
  - Usa `self._signed_request("DELETE", "/api/v3/order", {"symbol": symbol, "orderId": order_id})`
  - Retorna `True` se cancelou com sucesso ou se a ordem já não existe (código -2011 `UNKNOWN_ORDER`)
  - Retorna `False` em erros inesperados
  - Logar `binance.stop_order_cancelled` com `symbol`, `order_id`

- [x] **4.6 — Implementar `check_stop_order_status()` no `BinanceExecutor`**
  - Arquivo: `core/execution/binance.py`
  - Novo método privado:
    ```python
    def _check_stop_order_status(self, symbol: str, order_id: str) -> Optional[dict]:
    ```
  - Usa `self._signed_request("GET", "/api/v3/order", {"symbol": symbol, "orderId": order_id})`
  - Retorna dict com `status` (`NEW`, `FILLED`, `CANCELED`, `EXPIRED`, etc.) e `fills` se preenchida
  - Usado no início do tick para detectar se a stop disparou entre ticks

- [x] **4.7 — Colocar stop order após compra (`_execute_open` e `_execute_scale_in`)**
  - Arquivo: `core/execution/binance.py`
  - Em `_execute_open()` (linha 253), após `self._pm.open_position()`:
    1. Calcular `stop_price` = `hard_stop_price` da decision
    2. Calcular `limit_price` = `stop_price × (1 - 0.005)` (0.5% slippage)
    3. Chamar `self._place_stop_order(symbol, filled_size, stop_price, limit_price)`
    4. Salvar `exchange_stop_order_id` na posição via `self._pm.update_stop_order_id(symbol, order_id)`
    5. Se falhar, logar warning mas **não** abortar — posição continua protegida pelos stops no tick (fallback)
  - Em `_execute_scale_in()` (linha 293), após `self._pm.scale_in()`:
    1. Cancelar stop order antiga (quantidade mudou)
    2. Colocar nova stop order com a quantidade total atualizada e stop price atual

- [x] **4.8 — Cancelar stop order antes de venda (`_execute_close` e `_execute_reduce`)**
  - Arquivo: `core/execution/binance.py`
  - Em `_execute_close()` (linha 331), **antes** de `self._place_and_fill()`:
    1. Buscar `exchange_stop_order_id` da posição
    2. Se existir, chamar `self._cancel_stop_order(symbol, order_id)`
    3. Se cancelamento falhar (stop pode ter disparado), verificar status:
       - Se `FILLED` → stop já vendeu, abortar o close e fazer sync (4.9 cuida)
       - Se outro erro → logar warning e prosseguir com o close (a stop NEW será rejeitada por saldo)
  - Em `_execute_reduce()` (linha 378), **antes** de `self._place_and_fill()`:
    1. Cancelar stop order antiga
    2. Após o reduce executar com sucesso, colocar nova stop order com a quantidade restante

- [x] **4.9 — Sync no início do tick: detectar stop preenchida entre ticks**
  - Arquivo: `core/tasks/__init__.py`, em `run_single_strategy_tick()`, **antes** do bloco "2. Check stops" (linha 1050)
  - Novo bloco "1b. Sync exchange stop orders":
    1. Se `comp.executor` é `BinanceExecutor` e posição tem `exchange_stop_order_id`:
       - Chamar `comp.executor._check_stop_order_status(symbol, order_id)`
       - Se status == `FILLED`:
         a. Extrair fill price dos dados do response
         b. Chamar `comp.pm.close_position()` com `exit_reason=ExitReason.HARD_STOP` e o fill price real
         c. Registrar trade no DB
         d. Limpar `exchange_stop_order_id`
         e. Setar `comp.strategy._state = StrategyState.COOLDOWN`
         f. Logar `tick.exchange_stop_filled` com detalhes
         g. Skip o resto do tick (posição já foi fechada)
       - Se status == `CANCELED` ou `EXPIRED`:
         a. Limpar `exchange_stop_order_id`
         b. Logar warning — o tick vai recriar a stop se necessário (4.10)
  - Precisa de um método público no executor para expor a checagem: `check_exchange_stop(symbol) -> Optional[StopFillResult]`

- [x] **4.10 — Atualizar stop order no ratchet (breakeven + trailing)**
  - Arquivo: `core/tasks/__init__.py`, no bloco `update_stops` (linha 1086-1092)
  - Após `comp.pm.update_stops()`, se estamos em live mode:
    1. Comparar o novo `hard_stop_price` ou `trailing_stop_price` (o maior dos dois) com o `stopPrice` da ordem atual
    2. Se o stop subiu (ratchet ou trailing ratchetou):
       a. Cancelar stop order antiga
       b. Colocar nova stop order com o novo `stop_price` = `max(hard_stop_price, trailing_stop_price)`
       c. Atualizar `exchange_stop_order_id` na posição
    3. Se o stop não mudou → noop (não cancelar/recriar desnecessariamente)
  - O `stop_price` efetivo enviado à exchange é sempre `max(hard_stop, trailing_stop)` — a exchange precisa de uma só ordem

- [x] **4.11 — Adicionar config `exchange_stop_orders_enabled`**
  - Arquivo: `core/config.py`
  - Novo campo no `ExecutionConfig` (ou equivalente): `exchange_stop_orders_enabled: bool = Field(default=False)`
  - Default `False` para rollout seguro — precisa ser habilitado explicitamente
  - Toda a lógica de 4.7–4.10 só executa se esse campo for `True`
  - Quando `False`, comportamento idêntico ao atual (stops só no tick)

- [x] **4.12 — Adicionar config `stop_limit_slippage_pct`**
  - Arquivo: `core/config.py`
  - Novo campo no `ExecutionConfig`: `stop_limit_slippage_pct: float = Field(default=0.005, description="Slippage margin between stopPrice and limit price")`
  - Usado em `_place_stop_order()` para calcular `limit_price = stop_price × (1 - slippage)`
  - Valores razoáveis: 0.3%-1.0% — muito apertado e a ordem pode não preencher; muito largo e o fill é pior

- [x] **4.13 — Método `update_stop_order_id()` no `PortfolioManager`**
  - Arquivo: `core/portfolio/__init__.py`
  - Novo método:
    ```python
    def update_stop_order_id(self, symbol: str, order_id: Optional[str]) -> None:
    ```
  - Atualiza `position.exchange_stop_order_id` no portfólio in-memory
  - Persiste no `PositionRecord` do DB

- [x] **4.14 — Tratar edge cases na interação com a exchange**
  - Arquivo: `core/execution/binance.py`
  - **Race condition cancel/fill:** `_cancel_stop_order()` retorna `-2011 UNKNOWN_ORDER` se a stop já disparou → tratar como "filled", não como erro
  - **Saldo insuficiente:** Se o bot faz REDUCE (vende parcial) e a stop order tinha a quantidade total, a stop velha precisa ser cancelada e recriada com quantidade menor (já coberto em 4.8)
  - **Exchange offline / timeout:** Se `_place_stop_order()` falha, a posição fica sem proteção na exchange mas ainda tem stops no tick. Logar como warning, não como fatal
  - **Restart do bot:** Na inicialização, carregar `exchange_stop_order_id` do DB e verificar status. Se `FILLED` durante downtime → sync. Se `NEW` → manter. Se `CANCELED/EXPIRED` → limpar e recriar no próximo tick

- [x] **4.15 — Cleanup de stop order no shutdown / kill switch**
  - Arquivo: `core/tasks/__init__.py`, bloco do kill switch (se existir)
  - Se o kill switch for acionado e posições forem fechadas, cancelar as stop orders ativas antes
  - Também considerar: se o bot for parado (graceful shutdown), as stop orders **ficam** na exchange — isso é desejável, pois protegem mesmo com o bot offline

- [x] **4.16 — Testes unitários**
  - Arquivo: `tests/test_exchange_stop_orders.py`
  - Test: Após `_execute_open()`, stop order é colocada na exchange com `stopPrice` = `hard_stop_price`
  - Test: Após `_execute_scale_in()`, stop order antiga é cancelada e nova é criada com quantidade total
  - Test: Antes de `_execute_close()`, stop order é cancelada
  - Test: Antes de `_execute_reduce()`, stop order é cancelada e recriada com quantidade restante
  - Test: Sync no tick detecta stop `FILLED` → portfólio sincronizado, posição fechada
  - Test: Sync no tick detecta stop `NEW` → noop
  - Test: Ratchet move stop para cima → stop order cancelada e recriada com novo preço
  - Test: Ratchet não move stop → nenhuma chamada à exchange
  - Test: `exchange_stop_orders_enabled=False` → nenhuma stop order colocada
  - Test: Falha ao colocar stop order → warning logado, posição continua (fallback para tick)
  - Test: Cancel retorna -2011 (already filled) → tratado como fill, sync executado
  - Test: `limit_price` calculado corretamente com slippage configurado

- [ ] **4.17 — Testes de integração (testnet)**
  - Arquivo: `tests/integration/test_exchange_stops_testnet.py`
  - Requer testnet credentials configuradas
  - Test: Fluxo completo: buy → stop order aparece em `GET /api/v3/openOrders` → cancel → confirmado
  - Test: Fluxo ratchet: buy → stop order → cancel → nova stop com preço mais alto
  - Test: Verificar que `STOP_LOSS_LIMIT` com params corretos é aceita pela Binance testnet
