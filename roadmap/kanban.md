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
- [x] **2.53** Test event flow – publish test event from Python shell, verify UI update
- [x] **2.54** Test WebSocket reconnection – stop/start API service, verify auto-reconnect
- [x] **2.55** Verify all REST endpoints return data (use seed data)
- [x] **2.56** End-to-end test – run full stack (docker-compose up), verify live updates

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

- [x] **4.1** BinanceExecutor – real `buy()` / `sell()` with order lifecycle (PENDING → FILLED / REJECTED / CANCELLED)
- [x] **4.2** Order model – MARKET + LIMIT support, status tracking
- [x] **4.3** Retry logic – exponential backoff on executor failures
- [x] **4.4** Capital limits – max position size, max daily trades
- [x] **4.5** Max drawdown kill-switch – global emergency stop
- [x] **4.6** Trade size limits – per-trade and per-symbol caps
- [x] **4.7** Crash recovery – restore state from Postgres after unexpected restart
- [x] **4.8** Reconciliation job – compare local portfolio vs Binance account on startup
- [x] **4.9** Alerting – notifications on trades, stops hit, kill-switch activation
- [x] **4.10** Deployment – docker-compose production profile, env-based config, restart policies

---

## Phase 5 – Research Platform
### Tasks

- [x] **5.1** Strategy plugin system – register new strategies without modifying core
- [x] **5.2** Multi-strategy support – run multiple strategies in parallel via Celery workers
- [x] **5.3** Strategy comparison – side-by-side backtest metrics across strategies
- [x] **5.4** Portfolio allocation engine – distribute capital across strategies (regime-based: Smart Hodler ↔ Mean Reversion)
  - [x] **5.4a** Allocation model – fixed weight distribution per strategy
  - [x] **5.4b** Regime detector – classify market as trending vs ranging
  - [x] **5.4c** Auto-rebalance – shift allocation based on detected regime
  - [x] **5.4d** Multi-strategy backtest – validate allocation with historical data
- [x] **5.5** Advanced dashboard features – strategy switcher, parameter tuning UI
  - [x] **5.5a** Strategy switcher – dropdown to select active strategy in dashboard
  - [x] **5.5b** Parameter tuning UI – form to edit strategy parameters
  - [x] **5.5c** Side-by-side comparison view – visual diff of strategy performance
- [x] **5.6** Parameter optimization – grid search / walk-forward analysis for strategy tuning
- [x] **5.7** Config migration – Hybrid loader (DB → ENV → YAML priority), strategy_configs table, admin UI CRUD
  - [x] **5.7a** strategy_configs DB table + Alembic migration
  - [x] **5.7b** Hybrid config loader – DB → ENV → YAML priority chain
  - [x] **5.7c** Admin UI CRUD – create/edit/delete strategy configs from dashboard

---

## Phase 6 – VPS Deployment
### Tasks

- [x] **6.1** Create 2 GB swap on VPS (`fallocate`, `mkswap`, `swapon`, persist in `/etc/fstab`) to absorb celery-worker memory spikes and prevent OOM kills `infra`
- [x] **6.2** Off-VPS image builds – build images locally, ship via `docker save | ssh | docker load` (or registry) to avoid consuming 3–5 GB of disk during build on the VPS `infra`
- [x] **6.3** Use `prod` target for the frontend container (nginx serving static build, ~15 MB) instead of Vite dev server (~165 MB) `infra`
- [x] **6.4** Set `mem_limit: 512m` (and matching `mem_reservation`) on `celery-worker-prod` in `docker-compose.yml` to cap pandas/indicator spikes `infra`
- [x] **6.5** Bind postgres and redis published ports to `127.0.0.1` in `docker-compose.yml` so they are not exposed on the public interface `infra`
- [x] **6.6** Reverse-proxy api + frontend behind the existing host nginx on a dedicated subdomain (TLS via certbot, upstreams to `127.0.0.1:${API_PORT}` and `127.0.0.1:${FRONTEND_PORT}`) `infra`
- [x] **6.7** Generate bcrypt htpasswd file on VPS (`htpasswd -B -c`), long random password, stored at `/etc/nginx/auth/gg.htpasswd` with mode `0640 root:www-data` `infra`
- [x] **6.8** Add `auth_basic "GoldenGibbon"` + `auth_basic_user_file` directives to the `gg.*` nginx server block so every route (REST, WebSocket, static) requires credentials `infra`
- [x] **6.9** Install fail2ban and configure `nginx-http-auth` jail (maxretry=5, findtime=10m, bantime=1h) reading the nginx error log, enforced via iptables/ufw `infra`
- [x] **6.10** End-to-end verification: 5 bad attempts → IP banned; correct creds → 200 on REST + WebSocket upgrade; confirm `gg.htpasswd` is unreadable over HTTP `infra`

> Tasks 6.7–6.10 (auth layer) MUST complete before 6.6 serves any public request. Public exposure without Basic Auth + fail2ban is not acceptable.

---

## Phase 7 – Symbol Management (Add / Remove Trading Pairs)
### Tasks

- [x] **7.1** DB table `symbol_configs` – Alembic migration with columns: `id`, `symbol`, `exchange`, `timeframes` (JSONB), `enabled`, `description`, `created_at`, `updated_at`. Unique index on `symbol` `infra`
- [x] **7.2** Hybrid symbol loader – extend `core/config.py` to merge DB symbols on top of YAML (DB wins on conflict, union of both sources). Add `save_symbol()` and `delete_symbol()` helpers `core`
- [x] **7.3** REST endpoint `GET /api/config/symbols` – return all symbols (enabled + disabled) with source label (`yaml` / `db`) `api`
- [x] **7.4** REST endpoint `POST /api/config/symbols` – add a new pair. Validate symbol exists on Binance (`GET /api/v3/exchangeInfo`), reject duplicates `api`
- [x] **7.5** REST endpoint `DELETE /api/config/symbols/{symbol}` – remove a pair. Block if open positions exist for that symbol, return error with position details `api`
- [x] **7.6** REST endpoint `PATCH /api/config/symbols/{symbol}` – toggle `enabled` flag or update `timeframes` / `description` `api`
- [x] **7.7** Frontend – Symbols management page (`pages/SymbolsPage.tsx`) with table listing current pairs (symbol, exchange, timeframes, enabled status, source badge) `ui`
- [x] **7.8** Frontend – "Add Symbol" dialog with symbol input (autocomplete optional), exchange selector, timeframe checkboxes, validation feedback `ui`
- [x] **7.9** Frontend – Delete button per row with confirmation dialog showing open-position warning when applicable `ui`
- [x] **7.10** Frontend – Enable/disable toggle per row (calls PATCH endpoint) `ui`
- [x] **7.11** Config reload – after add/remove/toggle, call `get_settings(reload=True)` so Celery workers pick up changes on next tick `core`
- [x] **7.12** Tests – unit tests for hybrid symbol loader, endpoint validation (duplicate, invalid symbol, open-position block), toggle behavior `test`

---

## Phase 8 – DB-Only Configuration (Remove YAML Config Layer)

> Goal: single source of truth for business config. Defaults live in Pydantic models (code).
> DB overrides defaults. ENV stays for infrastructure only (DB_URL, REDIS_URL, LOG_LEVEL, CORS, ports).
> YAML config files are deleted after migration.

### Tasks – DB Schema & Seed

- [x] **8.1** New DB table `app_configs` – Alembic migration. Columns: `id`, `namespace` (String, e.g. "risk", "execution", "backtest"), `config_json` (JSONB), `updated_at`. Unique index on `namespace` `infra`
- [x] **8.2** Seed migration – insert default rows for: symbols (BTCUSDT + ETHUSDT), strategies (smart_hodler + mean_reversion), risk, execution, data, backtest, paper_trading, live_trading, ws_feed, alerting, regime, system. Values come from current YAML defaults `infra`

### Tasks – Config Loader Rewrite

- [x] **8.3** Rewrite `core/config.py` loader — `get_settings()` reads from `app_configs` + `strategy_configs` + `symbol_configs` tables. Pydantic model defaults are the fallback when a namespace has no DB row. Remove YAML loading, remove `_load_env_overrides` for strategy fields, remove `_merge_symbols` YAML logic `core`
- [x] **8.4** Update `save_db_config()` / `delete_db_config()` to work with `app_configs` table for non-strategy namespaces (risk, execution, etc.) `core`
- [x] **8.5** Graceful fallback — if DB is unreachable on startup, use Pydantic defaults + ENV and log a warning (don't crash) `core`

### Tasks – API Updates

- [x] **8.6** New endpoints `GET /api/config/{namespace}` and `PATCH /api/config/{namespace}` — generic config CRUD for risk, execution, data, backtest, etc. Same pattern as strategy config endpoints `api`
- [x] **8.7** Update existing symbol endpoints — remove YAML source logic, `get_symbol_source()` always returns "db" or "default" `api`
- [x] **8.8** Update strategy config endpoints — remove YAML/ENV source logic, simplify to DB vs Pydantic default `api`

### Tasks – Frontend Updates

- [x][x] **8.9** Settings page (`pages/SettingsPage.tsx`) — editable forms for risk, execution, backtest, paper_trading, live_trading config. Same field-meta pattern as strategy config tuning UI `ui`
- [x][x] **8.10** Add "Settings" nav item in sidebar (below Symbols, above Logs) `ui`
- [x][x] **8.11** Update source badges across UI — replace "yaml"/"env"/"db" with "default"/"custom" (simplified) `ui`

### Tasks – Cleanup & Testing

- [x][x] **8.12** Delete `config/symbols.yaml`, `config/strategies.yaml`, `config/settings.yaml`. Remove `load_yaml()`, `from_yaml_files()`, `get_config_dir()` from `core/config.py` `core`
- [x][x] **8.13** Update Dockerfile — remove `COPY config/ config/` from prod stage `infra`
- [x][x] **8.14** Update tests — replace YAML-dependent fixtures with DB seeds or Pydantic defaults. Update test_config.py, test_symbols.py `test`
- [x][x] **8.15** End-to-end verification — fresh DB + `alembic upgrade head` seeds all defaults, app starts and runs without any config files `test`

---

## Bugs

1 [x] `.venv-test/` was accidentally committed to `main` — remove it from tracking and add to `.gitignore`
2 [x] Deploy pipeline improvements — preflight checks, auto-migration, scripts in prod image, app-prod restart fix
3 [x] **Stale strategy_state for removed symbols** — When a symbol is removed from the enabled list (via Symbols page), its `strategy_state` rows in PostgreSQL are not cleaned up. The heartbeat task only upserts state for currently enabled symbols but never deletes rows for symbols that were disabled/removed. This causes the Strategy page to keep showing cards for old pairs (e.g. LINKUSDT, SOLUSDT) with stale conditions and timestamps. **Fix:** the heartbeat (or a config-reload hook) should delete `strategy_state` rows where the symbol is no longer in `enabled_symbols`. Also consider cleaning up related `portfolio_snapshots` entries for removed symbols. `core`
4 [ ] **Nginx proxy timeout on backtest endpoints** — The `/api/` location in the VPS nginx config (`/etc/nginx/sites-enabled/gg.paulonavarro.com`) has no `proxy_read_timeout`, defaulting to 60s. Long-running endpoints like `/api/backtest/compare` (90-day backtest takes 60–90s on VPS) get silently killed by nginx before the API responds. The browser receives no data and React Query resets to idle state with no error shown. **Fix:** add `proxy_read_timeout 120s;` and `proxy_send_timeout 120s;` to the `/api/` location block. `infra`
5 [ ] **smart_hodler backtest crash with 90-day data on VPS** — Running comparison with 90 days on the VPS produces `"The truth value of a Series is ambiguous. Use a.empty, a.bool(), a.item(), a.any() or a.all()"` for smart_hodler on both BTCUSDT and ETHUSDT. Works fine in dev with 30-day data. Likely a pandas Series being used in a boolean context (e.g. `if series:` instead of `if series.any():`) in the smart_hodler strategy or backtest runner, triggered only with longer data windows. `strategy`
6 [ ] **React error #185 on comparison render (intermittent)** — After the comparison backtest completes, the page occasionally crashes with React error #185 ("Objects are not valid as a React child"). Suspected re-rendering loop — happens when the machine responds fast, does not reproduce on slow machines. Possibly a Recharts Tooltip formatter receiving an unexpected object value, or a race condition in React Query state transitions. Needs investigation with React DevTools profiler in dev mode. `ui`
7 [ ] **Switching to live trading does not sync portfolio with real Binance balances** — When `live_trading.enabled` is turned on via Settings, the dashboard keeps showing stale paper-trading figures (e.g. $10,000 USDT, 2 BTC) because nothing fetches the actual Binance account state. The portfolio endpoint (`GET /portfolio`) returns the latest `portfolio_snapshots` row, which still holds paper data. The worker creates `_TickComponents` with `initial_capital` from config instead of the real exchange balance. **Result:** the operator sees fictional balances and equity, making it impossible to trust the dashboard when trading real money. **Fix:** when live trading is enabled, the system must (1) fetch actual USDT + asset balances from Binance (`BinanceExecutor.get_account_info()`), (2) seed a fresh `PortfolioSnapshot` reflecting the real account state so the dashboard shows correct values immediately, (3) clear stale `_worker_state` so tick components are recreated with the live executor and real balances, (4) ensure the mode gate in `run_single_strategy_tick` uses the real balance as `initial_capital` for the live session. The two independent toggles are a secondary UX issue — the critical problem is data integrity in live mode. `core` `api`

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
