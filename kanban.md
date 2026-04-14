# Kanban – Crypto Trading Platform

> Updated: 2026-03-03

---

## Phase 1 – Foundation & Backtest Engine
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
- [x] **1.11** Smart Hodler strategy – full signal logic (BUY / SELL 100% / SELL 50% / HOLD)
- [x] **1.12** Smart Hodler state machine – FLAT → POSITION → REDUCED → COOLDOWN
- [x] **1.13** Smart Hodler session filter – dead zone logic (weekends + overnight UTC)
- [x] **1.14** Risk Engine – position sizing (scaled entries 50/75/100%), stop-loss rules
- [x] **1.15** Trailing stop – ATR-based (2×ATR below highest close since entry)
- [x] **1.16** Hard stop – 3% max drawdown per trade + cooldown trigger
- [x] **1.17** Portfolio model – USDT balance, open positions, equity curve, trade history
- [x] **1.18** PaperExecutor – simulated `buy()` / `sell()` with slippage + fees
- [x] **1.19** Backtest runner – candle-by-candle loop: load → indicators → strategy → risk → execute → update
- [x] **1.20** Backtest metrics – total return, drawdown, win rate, Sharpe ratio, vs Buy & Hold
- [x] **1.21** Backtest reporting – output results to console + persist to Postgres
- [x] **1.22** Logging – structured logs for every decision (signal, order, fill, stop)
- [x] **1.23** MarketData multi-timeframe – extend model to support 15m + 1H candles/indicators
- [x] **1.24** Mean Reversion strategy – full signal logic (BUY / SELL 50% / SELL 100% / HOLD)
- [x] **1.25** Mean Reversion state machine – FLAT → POSITION → REDUCED → COOLDOWN (no cooldown on profit exits)
- [x] **1.26** Mean Reversion config – add `mean_reversion` section to `strategies.yaml`
- [x] **1.27** Mean Reversion time stop – exit after 16 candles if middle BB not reached
- [x] **1.28** Mean Reversion unit tests – signal logic, state transitions, regime filter

---

## Phase 2 – Real-Time Interface
### Tasks – Backend (FastAPI + Event System)

- [x] **2.1** Add FastAPI to dependencies (`pyproject.toml`)
- [x] **2.2** Create Pydantic models in `core/models.py` (MarketData, Portfolio, Signal, Order, Trade, StrategyState, StrategyConditions)
- [x] **2.3** Database ORM models in `db/models.py` (Candle, Position, TradeRecord, OrderRecord, PortfolioSnapshot)
- [x] **2.4** Alembic migration – initial schema (tables, indexes for symbol+timestamp)
- [x] **2.5** Event publisher in `core/events.py` (Redis pub/sub client, publish_event method)
- [x] **2.6** Event channels – market_data, strategy, risk, execution, portfolio, system
- [x] **2.7** FastAPI app setup in `api/main.py` (CORS, DB connection pool, Redis client, startup/shutdown hooks)
- [x] **2.8** REST endpoints – `api/routes/market.py` (GET /candles/{symbol}, GET /price/{symbol})
- [x] **2.9** REST endpoints – `api/routes/portfolio.py` (GET /portfolio, GET /equity-curve)
- [x] **2.10** REST endpoints – `api/routes/trades.py` (GET /trades with filters, GET /trades/stats)
- [x] **2.11** REST endpoints – `api/routes/orders.py` (GET /orders, GET /orders/{id})
- [x] **2.12** REST endpoints – `api/routes/strategy.py` (GET /strategy/state, GET /strategy/signals)
- [x] **2.13** REST endpoints – `api/routes/system.py` (GET /health, GET /logs)
- [x] **2.14** WebSocket endpoint in `api/websocket.py` (WS /ws, subscribe to Redis pub/sub, forward events)
- [x] **2.15** WebSocket connection manager (multiple clients, heartbeat, reconnection handling)
- [x] **2.16** Integrate event publishing into strategy (publish SIGNAL_GENERATED, STATE_CHANGED events)
- [x] **2.17** Integrate event publishing into execution (publish ORDER_CREATED, ORDER_FILLED events)
- [x] **2.18** Integrate event publishing into portfolio (publish POSITION_UPDATED, TRADE_CLOSED events)
- [x] **2.19** API Dockerfile – folded into existing Dockerfile (`COPY api/ api/` added to prod stage)
- [x] **2.20** Add `api` service to docker-compose.yml (expose port 8000, connect to Postgres + Redis)
- [x] **2.21** Environment config (`api/.env.example` with DATABASE_URL, REDIS_URL, CORS_ORIGINS)

### Tasks – Frontend (React + WebSocket)

- [x] **2.22** Initialize Vite + React + TypeScript project in `frontend/` directory
- [x] **2.23** Install dependencies (`@mui/material`, `@mui/icons-material`, `@emotion/react`, `@emotion/styled`, `react-router-dom`, `zustand`, `@tanstack/react-query`, `recharts`, `@tanstack/react-table`)
- [x] **2.24** Configure Vite proxy to FastAPI (port 8000) in `vite.config.ts`
- [x] **2.25** Create TypeScript interfaces matching backend Pydantic models (`frontend/src/types/`)
- [x] **2.26** MUI theme setup – dark mode `ThemeProvider` in `frontend/src/theme.ts` (palette, typography, component overrides)
- [x] **2.27** App shell layout – `AppLayout.tsx` with MUI `Drawer` (left sidebar nav), `AppBar` (connection status, clock), and `<Outlet />` for routed content
- [x] **2.28** React Router setup – `router.tsx` with routes: `/` (Dashboard), `/trades` (Trades), `/orders` (Orders), `/strategy` (Strategy), `/logs` (Logs), `/settings` (Settings)
- [x] **2.29** Sidebar nav items – icons + labels: Dashboard, Strategy, Portfolio, Trades, Orders, Metrics, Logs, System
- [x] **2.30** WebSocket hook – `frontend/src/hooks/useWebSocket.ts` (connect, parse events, auto-reconnect, connection status)
- [x] **2.31** Zustand store – `stores/marketStore.ts` (candles, indicators, price updates)
- [x] **2.32** Zustand store – `stores/strategyStore.ts` (state, signal, conditions checklist)
- [x] **2.33** Zustand store – `stores/portfolioStore.ts` (balance, positions, equity curve)
- [x] **2.34** Zustand store – `stores/tradesStore.ts` (trade history array)
- [x] **2.35** Zustand store – `stores/ordersStore.ts` (active and recent orders)
- [x] **2.36** Zustand store – `stores/systemStore.ts` (health status, logs buffer)
- [x] **2.37** React Query hooks in `frontend/src/api/` (useCandles, usePortfolio, useTrades, useEquityCurve, etc.)
- [x] **2.38** Page – `pages/DashboardPage.tsx` (price ticker cards, mini equity curve, open positions summary, recent signals, system status)
- [x] **2.39** Page – `pages/StrategyPage.tsx` (state badge, signal, conditions checklist, scaled entry progress, cooldown timer)
- [x] **2.40** Page – `pages/PortfolioPage.tsx` (balance card, open positions table with trailing stop levels, equity curve chart)
- [x] **2.41** Page – `pages/TradesPage.tsx` (sortable/filterable trade history with MUI DataGrid or @tanstack/react-table)
- [x] **2.42** Page – `pages/OrdersPage.tsx` (active orders, recent fills, execution stats)
- [x] **2.43** Page – `pages/MetricsPage.tsx` (equity curve chart, win rate, total return, drawdown, Sharpe ratio)
- [x] **2.44** Page – `pages/LogsPage.tsx` (real-time log stream with filtering and severity chips)
- [x] **2.45** Component – `components/ConnectionStatus.tsx` (WebSocket + API health indicator in AppBar)
- [x] **2.46** Component – `components/PriceTicker.tsx` (live price with delta, used in Dashboard + AppBar)
- [x] **2.47** Initialize WebSocket, stores, QueryClient, Router, and ThemeProvider in `main.tsx`
- [x] **2.48** Environment config (`frontend/.env.example` with VITE_API_URL, VITE_WS_URL)
- [x] **2.49** Frontend Dockerfile (`frontend/Dockerfile` with Vite build + nginx serve)
- [x] **2.50** Add `frontend` service to docker-compose.yml (expose port 5173 for dev, 80 for prod)

### Tasks – Integration & Testing

- [x] **2.51** Database seed script (`db/seeds.py` with sample candles, trades, orders)
- [x] **2.52** Makefile targets (make api, make frontend, make dev)
- [ ] **2.53** Test event flow – publish test event from Python shell, verify UI update (backend event tests done; UI verification blocked on frontend)
- [ ] **2.54** Test WebSocket reconnection – stop/start API service, verify auto-reconnect (blocked on frontend)
- [x] **2.55** Verify all REST endpoints return data (use seed data)
- [ ] **2.56** End-to-end test – run full stack (docker-compose up), open dashboard, verify live updates (docker-compose infra ready; blocked on frontend)

---

## Phase 3 – Infrastructure & Paper Trading
### Tasks

- [x] **3.1** Add Redis + Celery worker + Celery Beat services to docker-compose
- [x] **3.2** Celery setup – app config, worker entrypoint, result backend (Redis)
- [x] **3.3** Celery Beat – periodic task schedule (15m candle tick, 4h reconciliation)
- [x] **3.4** Candle fetch task – Celery task to pull latest candles + update cache + publish CANDLE_RECEIVED event
- [x] **3.5** Strategy tick task – Celery task to run indicator → strategy → risk → execute pipeline
- [x] **3.6** Live data feed – real-time candle ingestion from Binance (REST or WebSocket)
- [x] **3.7** Paper trading mode – PaperExecutor driven by live data via Celery tasks
- [x] **3.8** State persistence – save/load strategy state + portfolio between ticks (also: enrich `/api/strategy/signals` with live conditions from `state_data`)
- [x] **3.9** Reconciliation task – compare local state vs expected state on startup
- [x] **3.10** Monitoring – health checks for workers, beat, Redis connectivity

## Phase 3b – Historical Data Pipeline
### Tasks

- [x] **3b.1** Add inter-request rate-limit delay (250ms) in `_fetch_in_chunks()` to avoid Binance 429s during bulk downloads
- [x] **3b.2** Create `scripts/pull_historical_data.py` — CLI script that wires up DB + client + config, downloads all symbol/timeframe pairs, shows progress + summary table
- [x] **3b.3** `--days` CLI argument to override the 730-day default (e.g. `--days 30` for quick paper-trading warm-up)
- [x] **3b.4** `make pull-historical-data` Makefile target with optional `DAYS=` variable
- [x] **3b.5** Unit tests for the script

---

## Phase 4 – Real Trading
### Tasks

- [ ] **4.1** BinanceExecutor – real `buy()` / `sell()` with order lifecycle (PENDING → FILLED / REJECTED / CANCELLED)
- [ ] **4.2** Order model – MARKET + LIMIT support, status tracking
- [x] **4.3** Retry logic – exponential backoff on executor failures
- [x] **4.4** Capital limits – max position size, max daily trades
- [ ] **4.5** Max drawdown kill-switch – global emergency stop
- [x] **4.6** Trade size limits – per-trade and per-symbol caps
- [ ] **4.7** Crash recovery – restore state from Postgres after unexpected restart
- [ ] **4.8** Reconciliation job – compare local portfolio vs Binance account on startup
- [ ] **4.9** Alerting – notifications on trades, stops hit, kill-switch activation
- [ ] **4.10** Deployment – docker-compose production profile, env-based config, restart policies

---

## Phase 5 – Research Platform
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

| `core` | Part of the core pipeline (data → indicators → strategy → risk → execution → portfolio) |
| `infra` | Infrastructure / DevOps (DB, Redis, Celery, deployment) |
| `strategy` | Strategy-specific logic (Smart Hodler, future strategies) |
| `risk` | Risk management and safety features |
| `test` | Tests and validation |
| `api` | Backend API (FastAPI, WebSocket, REST endpoints) |
| `ui` | Frontend interface (React components, WebSocket client) |
| `events` | Event publishing and real-time communication |
