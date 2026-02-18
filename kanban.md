# Kanban – Crypto Trading Platform

> Updated: 2026-02-18

---

## Phase 1 – Foundation & Backtest Engine

| TODO | IN PROGRESS | DONE |
|------|-------------|------|
| | | |

### Tasks

- [x] **1.1** Project scaffolding (folder structure, `pyproject.toml`, virtual env)
- [x] **1.2** Dockerfile – multi-stage build (dev + prod), Python image, deps install
- [x] **1.3** docker-compose.yml – app + Postgres services, volumes, env vars
- [x] **1.4** Database setup – Postgres schema (candles, trades, backtest results)
- [x] **1.5** Config layer – `symbols.yaml`, `strategies.yaml`, settings loader
- [x] **1.6** MarketData model – dataclass with `symbol`, `timeframe`, `candles`, `indicators`
- [x] **1.7** Data Loader – fetch historical candles from Binance API + cache to Postgres
- [x] **1.8** Indicator Engine – pure functions: EMA, ADX, ATR, RSI, Volume SMA
- [x] **1.8a** Bollinger Bands indicator – `calculate_bollinger_bands(close, period, std_dev)` → upper, middle, lower
- [x] **1.8b** Bollinger Bands unit tests – validate against known values
- [x] **1.9** Indicator unit tests – validate outputs against known values
- [x] **1.10** Strategy base class – `Strategy.decide(market_data, portfolio) → Signal`
- [ ] **1.11** Smart Hodler strategy – full signal logic (BUY / SELL 100% / SELL 50% / HOLD)
- [ ] **1.12** Smart Hodler state machine – FLAT → POSITION → REDUCED → COOLDOWN
- [ ] **1.13** Smart Hodler session filter – dead zone logic (weekends + overnight UTC)
- [ ] **1.14** Risk Engine – position sizing (scaled entries 50/75/100%), stop-loss rules
- [ ] **1.15** Trailing stop – ATR-based (2×ATR below highest close since entry)
- [ ] **1.16** Hard stop – 3% max drawdown per trade + cooldown trigger
- [ ] **1.17** Portfolio model – USDT balance, open positions, equity curve, trade history
- [ ] **1.18** PaperExecutor – simulated `buy()` / `sell()` with slippage + fees
- [ ] **1.19** Backtest runner – candle-by-candle loop: load → indicators → strategy → risk → execute → update
- [ ] **1.20** Backtest metrics – total return, drawdown, win rate, Sharpe ratio, vs Buy & Hold
- [ ] **1.21** Backtest reporting – output results to console + persist to Postgres
- [ ] **1.22** Logging – structured logs for every decision (signal, order, fill, stop)
- [x] **1.23** MarketData multi-timeframe – extend model to support 15m + 1H candles/indicators
- [x] **1.24** Mean Reversion strategy – full signal logic (BUY / SELL 50% / SELL 100% / HOLD)
- [ ] **1.25** Mean Reversion state machine – FLAT → POSITION → REDUCED → COOLDOWN (no cooldown on profit exits)
- [ ] **1.26** Mean Reversion config – add `mean_reversion` section to `strategies.yaml`
- [ ] **1.27** Mean Reversion time stop – exit after 16 candles if middle BB not reached
- [x] **1.28** Mean Reversion unit tests – signal logic, state transitions, regime filter

---

## Phase 2 – Real-Time Interface

| TODO | IN PROGRESS | DONE |
|------|-------------|------|
| | | |

### Tasks – Backend (FastAPI + Event System)

- [ ] **2.1** Add FastAPI to dependencies (`pyproject.toml`)
- [ ] **2.2** Create Pydantic models in `core/models.py` (MarketData, Portfolio, Signal, Order, Trade, StrategyState, StrategyConditions)
- [ ] **2.3** Database ORM models in `db/models.py` (Candle, Position, TradeRecord, OrderRecord, PortfolioSnapshot)
- [ ] **2.4** Alembic migration – initial schema (tables, indexes for symbol+timestamp)
- [ ] **2.5** Event publisher in `core/events.py` (Redis pub/sub client, publish_event method)
- [ ] **2.6** Event channels – market_data, strategy, risk, execution, portfolio, system
- [ ] **2.7** FastAPI app setup in `api/main.py` (CORS, DB connection pool, Redis client, startup/shutdown hooks)
- [ ] **2.8** REST endpoints – `api/routes/market.py` (GET /candles/{symbol}, GET /price/{symbol})
- [ ] **2.9** REST endpoints – `api/routes/portfolio.py` (GET /portfolio, GET /equity-curve)
- [ ] **2.10** REST endpoints – `api/routes/trades.py` (GET /trades with filters, GET /trades/stats)
- [ ] **2.11** REST endpoints – `api/routes/orders.py` (GET /orders, GET /orders/{id})
- [ ] **2.12** REST endpoints – `api/routes/strategy.py` (GET /strategy/state, GET /strategy/signals)
- [ ] **2.13** REST endpoints – `api/routes/system.py` (GET /health, GET /logs)
- [ ] **2.14** WebSocket endpoint in `api/websocket.py` (WS /ws, subscribe to Redis pub/sub, forward events)
- [ ] **2.15** WebSocket connection manager (multiple clients, heartbeat, reconnection handling)
- [ ] **2.16** Integrate event publishing into strategy (publish SIGNAL_GENERATED, STATE_CHANGED events)
- [ ] **2.17** Integrate event publishing into execution (publish ORDER_CREATED, ORDER_FILLED events)
- [ ] **2.18** Integrate event publishing into portfolio (publish POSITION_UPDATED, TRADE_CLOSED events)
- [ ] **2.19** API Dockerfile (`api/Dockerfile` with FastAPI + uvicorn)
- [ ] **2.20** Add `api` service to docker-compose.yml (expose port 8000, connect to Postgres + Redis)
- [ ] **2.21** Environment config (`api/.env.example` with DATABASE_URL, REDIS_URL, CORS_ORIGINS)

### Tasks – Frontend (React + WebSocket)

- [ ] **2.22** Initialize Vite + React + TypeScript project in `frontend/` directory
- [ ] **2.23** Install dependencies (socket.io-client or native WebSocket, zustand, @tanstack/react-query, recharts, @tanstack/react-table)
- [ ] **2.24** Configure Vite proxy to FastAPI (port 8000) in `vite.config.ts`
- [ ] **2.25** Create TypeScript interfaces matching backend Pydantic models
- [ ] **2.26** WebSocket hook in `frontend/src/hooks/useWebSocket.ts` (connect, parse events, auto-reconnect)
- [ ] **2.27** Zustand store – `stores/marketStore.ts` (candles, indicators, price updates)
- [ ] **2.28** Zustand store – `stores/strategyStore.ts` (state, signal, conditions checklist)
- [ ] **2.29** Zustand store – `stores/portfolioStore.ts` (balance, positions, equity curve)
- [ ] **2.30** Zustand store – `stores/tradesStore.ts` (trade history array)
- [ ] **2.31** Zustand store – `stores/ordersStore.ts` (active and recent orders)
- [ ] **2.32** Zustand store – `stores/systemStore.ts` (health status, logs buffer)
- [ ] **2.33** React Query hooks in `frontend/src/api/` (useCandles, usePortfolio, useTrades, useEquityCurve, etc.)
- [ ] **2.34** Component – `MarketDataPanel.tsx` (price ticker, candlestick charts, indicator overlays)
- [ ] **2.35** Component – `StrategyPanel.tsx` (state badge, signal, conditions checklist, scaled entry progress, cooldown timer)
- [ ] **2.36** Component – `PortfolioPanel.tsx` (balance, open positions table, trailing stop level)
- [ ] **2.37** Component – `TradesTable.tsx` (sortable/filterable trade history)
- [ ] **2.38** Component – `OrdersPanel.tsx` (active orders, recent fills, execution stats)
- [ ] **2.39** Component – `MetricsPanel.tsx` (equity curve chart, win rate, total return, drawdown, Sharpe)
- [ ] **2.40** Component – `SystemStatus.tsx` (connection indicators, last update timestamp, session status)
- [ ] **2.41** Component – `LogsViewer.tsx` (real-time log stream with filtering)
- [ ] **2.42** Main layout in `App.tsx` (grid layout, connection status header, theme toggle)
- [ ] **2.43** Initialize WebSocket and stores in `main.tsx` and `App.tsx`
- [ ] **2.44** Environment config (`frontend/.env.example` with VITE_API_URL, VITE_WS_URL)
- [ ] **2.45** Frontend Dockerfile (`frontend/Dockerfile` with Vite build + nginx serve)
- [ ] **2.46** Add `frontend` service to docker-compose.yml (expose port 5173 for dev, 80 for prod)

### Tasks – Integration & Testing

- [ ] **2.47** Database seed script (`db/seeds.py` with sample candles, trades, orders)
- [ ] **2.48** Makefile targets (make api, make frontend, make dev)
- [ ] **2.49** Test event flow – publish test event from Python shell, verify UI update
- [ ] **2.50** Test WebSocket reconnection – stop/start API service, verify auto-reconnect
- [ ] **2.51** Verify all REST endpoints return data (use seed data)
- [ ] **2.52** End-to-end test – run full stack (docker-compose up), open dashboard, verify live updates

---

## Phase 3 – Infrastructure & Paper Trading

| TODO | IN PROGRESS | DONE |
|------|-------------|------|
| | | |

### Tasks

- [ ] **3.1** Add Redis + Celery worker + Celery Beat services to docker-compose
- [ ] **3.2** Celery setup – app config, worker entrypoint, result backend (Redis)
- [ ] **3.3** Celery Beat – periodic task schedule (15m candle tick, 4h reconciliation)
- [ ] **3.4** Candle fetch task – Celery task to pull latest candles + update cache + publish CANDLE_RECEIVED event
- [ ] **3.5** Strategy tick task – Celery task to run indicator → strategy → risk → execute pipeline
- [ ] **3.6** Live data feed – real-time candle ingestion from Binance (REST or WebSocket)
- [ ] **3.7** Paper trading mode – PaperExecutor driven by live data via Celery tasks
- [ ] **3.8** State persistence – save/load strategy state + portfolio between ticks
- [ ] **3.9** Reconciliation task – compare local state vs expected state on startup
- [ ] **3.10** Monitoring – health checks for workers, beat, Redis connectivity

---

## Phase 4 – Real Trading

| TODO | IN PROGRESS | DONE |
|------|-------------|------|
| | | |

### Tasks

- [ ] **3.1** BinanceExecutor – real `buy()` / `sell()` with order lifecycle (PENDING → FILLED / REJECTED / CANCELLED)
- [ ] **3.2** Order model – MARKET + LIMIT support, status tracking
- [ ] **3.3** Retry logic – exponential backoff on executor failures
- [ ] **3.4** Capital limits – max position size, max daily trades
- [ ] **3.5** Max drawdown kill-switch – global emergency stop
- [ ] **3.6** Trade size limits – per-trade and per-symbol caps
- [ ] **3.7** Crash recovery – restore state from Postgres after unexpected restart
- [ ] **3.8** Reconciliation job – compare local portfolio vs Binance account on startup
- [ ] **3.9** Alerting – notifications on trades, stops hit, kill-switch activation
- [ ] **3.10** Deployment – docker-compose production profile, env-based config, restart policies

---

## Phase 5 – Research Platform

| TODO | IN PROGRESS | DONE |
|------|-------------|------|
| | | |

### Tasks

- [ ] **5.1** Strategy plugin system – register new strategies without modifying core
- [ ] **5.2** Multi-strategy support – run multiple strategies in parallel via Celery workers
- [ ] **5.3** Strategy comparison – side-by-side backtest metrics across strategies
- [ ] **5.4** Portfolio allocation engine – distribute capital across strategies (regime-based: Smart Hodler ↔ Mean Reversion)
- [ ] **5.5** Advanced dashboard features – strategy switcher, parameter tuning UI
- [ ] **5.6** Parameter optimization – grid search / walk-forward analysis for strategy tuning
- [ ] **5.7** Config migration – Hybrid loader (DB → ENV → YAML priority), strategy_configs table, admin UI CRUD

---

## Labels

| Label | Meaning |
|-------|---------|
| `core` | Part of the core pipeline (data → indicators → strategy → risk → execution → portfolio) |
| `infra` | Infrastructure / DevOps (DB, Redis, Celery, deployment) |
| `strategy` | Strategy-specific logic (Smart Hodler, future strategies) |
| `risk` | Risk management and safety features |
| `test` | Tests and validation |
| `api` | Backend API (FastAPI, WebSocket, REST endpoints) |
| `ui` | Frontend interface (React components, WebSocket client) |
| `events` | Event publishing and real-time communication |
