# Crypto Trading Platform – Technical Blueprint

## 1. Vision

This project is a **crypto quantitative trading platform**, not a simple bot.

### Purpose

- Execute trading strategies automatically
- Support backtesting and paper trading
- Allow real trading via exchanges
- Serve as a long-term research and execution engine
- **Provide real-time visibility through web interface**

> **The system must be modular, extensible and safe by design.**

---

## 2. High-Level Architecture

The system is composed of strictly separated layers:

```
Market API → Data Loader → Indicators → Strategy → Risk → Execution → Portfolio
                                                                          ↓
                                                                    Event Publisher
                                                                          ↓
                                                                    Redis Pub/Sub
                                                                          ↓
                                                                   FastAPI WebSocket
                                                                          ↓
                                                                    React Dashboard
```

**Each layer has a single responsibility and must never depend on layers below it.**

---

## 3. Core Components

### 3.1 MarketData

Represents normalized market information passed through the system.

**Fields:**
- `symbol`
- `timeframe`
- `candles`
- `indicators`

### 3.2 Strategy Engine

Strategies implement the following interface:

```python
Strategy.decide(market_data, portfolio) → BUY | SELL | HOLD
```

**Strategies:**
- Never call APIs
- Never place orders
- Only analyze data and return decisions

### 3.3 Indicator Engine

Responsible for computing technical indicators.

**Responsibilities:**
- Receives raw candles
- Outputs calculated indicators (EMA, RSI, MACD, etc.)
- Does not fetch data
- Does not know about strategies or execution

### 3.4 Risk Engine

Controls exposure and capital allocation.

**Responsibilities:**
- Position sizing
- Stop-loss rules
- Max drawdown limits
- Global kill-switch logic

### 3.5 Execution Engine

Abstract interface for order execution.

**Implementations:**
- `PaperExecutor` (simulation)
- `BinanceExecutor` (real trading)

**Executors expose:**
- `buy(symbol, amount)`
- `sell(symbol, amount)`

### 3.6 Portfolio

Global state of the system.

**Tracks:**
- USDT balance
- Open positions
- Equity curve
- Trade history

### 3.7 Event System

**Real-time event publishing for visibility and monitoring.**

**Components:**
- `EventPublisher` – publishes events to Redis pub/sub
- Event channels: `market_data`, `strategy`, `risk`, `execution`, `portfolio`, `system`
- Event types: `CANDLE_RECEIVED`, `SIGNAL_GENERATED`, `ORDER_FILLED`, `POSITION_OPENED`, etc.

**Integration:**
- Core components publish events after state changes
- Events flow through Redis to web clients
- No blocking – async fire-and-forget pattern

### 3.8 Web Interface

**Real-time React dashboard for monitoring and control.**

**Stack:**
- Frontend: React + Vite + TypeScript
- Backend: FastAPI (REST + WebSocket)
- Communication: WebSocket for real-time, REST for historical data
- State: Zustand stores, React Query for data fetching

**Displays:**
- Live market data (candles, indicators, price ticker)
- Strategy state and signals (conditions checklist, state machine)
- Portfolio (balance, positions, PnL, equity curve)
- Orders and execution (active orders, fills, slippage)
- Trade history and metrics (win rate, drawdown, Sharpe)
- System health (connections, workers, logs)

---

## 4. Repository Structure

Suggested structure:

```
/core
  /data
    loader.py
  /indicators
    ta_engine.py
  /strategies
    base.py
    smart_hodler.py
  /risk
    fixed_risk.py
  /execution
    paper.py
    binance.py
  /backtest
    runner.py
  /portfolio
    state.py
  models.py          # Pydantic data models
  events.py          # Event publisher

/api
  main.py            # FastAPI app
  websocket.py       # WebSocket endpoint
  /routes
    market.py        # Market data endpoints
    portfolio.py     # Portfolio endpoints
    trades.py        # Trade history endpoints
    orders.py        # Order endpoints
    strategy.py      # Strategy state endpoints
    system.py        # Health and logs endpoints
  Dockerfile

/frontend
  /src
    /components      # React dashboard components
    /stores          # Zustand state management
    /hooks           # WebSocket and API hooks
    /api             # React Query hooks
    App.tsx
    main.tsx
  package.json
  vite.config.ts
  Dockerfile

/db
  models.py          # SQLAlchemy ORM
  /migrations        # Alembic migrations

/config
  symbols.yaml
  strategies.yaml

main.py
docker-compose.yml
```

---

## 5. Smart Hodler (MVP Strategy)

> **Full specification:** [strategy_smart_hodler.md](strategy_smart_hodler.md)

Multi-signal trend-following strategy with scaled entries and ATR-based stops.

- **Timeframe:** 15m (primary) + 1H (confirmation)
- **Entry:** `EMA 50 > EMA 200` + `ADX > 25` + `Close > EMA 50` + hourly confirmation
- **Exit:** `EMA 50 < EMA 200` OR `Close < EMA 200` OR trailing stop hit
- **Position sizing:** Scaled entries (50% → 75% → 100% over ~4 hours)
- **Risk:** ATR-based trailing stop (2×) + 3% hard stop per trade + 4h cooldown after exit

**Purpose:**
- Capture intraday trends while cutting losses fast
- Filter out sideways/ranging markets via ADX (threshold 25 for noisy 15m)
- Avoid whipsaws via cooldown and hourly confirmation

---

## 6. Backtesting

**Backtest is mandatory.**

### Flow

1. Load historical data
2. Iterate candle by candle
3. Compute indicators
4. Strategy decides action
5. Risk engine validates
6. Executor simulates order
7. Portfolio updates state

### Metrics to Record

- Total return
- Drawdown
- Win rate
- Sharpe ratio
- Comparison vs Buy & Hold

---

## 7. Paper Trading

Real-time simulation using live data.

Same engine as real trading, only executor changes.

**Purpose:**
- Validate logic
- Detect bugs
- Build confidence before real capital

---

## 8. Safety Rules

**Mandatory from day one:**

- ✅ Max drawdown kill-switch
- ✅ Trade size limits
- ✅ Logging of every decision
- ✅ Real-time visibility of all operations
- ❌ No strategy allowed to bypass risk engine
- ❌ No execution without explicit decision

---

## 9. Design Rules

**Absolute rules:**

- Strategies must be **plug-and-play**
- No component may access Binance directly except executors
- No strategy may know about execution details
- Indicators must be **pure functions**
- Backtest, paper and real trading must **share the same core logic**
- **UI must be read-only observer** – never control execution directly (future: allow manual overrides with confirmation)

---

## 10. Roadmap

### Phase 1 – Platform Foundation
- Backtest engine
- Smart Hodler
- Metrics and logging

### Phase 2 – Real-Time Interface
- **FastAPI backend (REST + WebSocket)**
- **Event publishing system**
- **React dashboard**
- **Live monitoring and visualization**

### Phase 3 – Live Simulation
- Live data feed
- Paper trading
- Celery infrastructure

### Phase 4 – Real Trading
- Binance integration
- Capital limits
- Emergency stop

### Phase 5 – Research Platform
- Multiple strategies
- Strategy comparison
- Portfolio allocation engine

---

## 11. Golden Rule

> **If a new strategy cannot be added without modifying the core system, the architecture is wrong.**

The system must evolve by adding new modules, not rewriting existing ones.

## 12. Technical Decisions

### Containerization
- **100% Dockerized** – every component runs inside Docker containers
- `Dockerfile` with multi-stage build (dev dependencies + slim prod image)
- `docker-compose.yml` orchestrates all services: app, API, frontend, Postgres, Redis, Celery worker, Celery Beat
- No local installs required — `docker compose up` spins up the entire stack
- Environment variables managed via `.env` file (not committed)
- Named volumes for Postgres data persistence

### Data Storage
- Postgres for local development and backtest results (containerized)
- Candle cache to avoid redundant API calls
- SQLAlchemy ORM with Alembic migrations

### Communication & Task Scheduling
- **Stack:** Redis + Celery workers + Celery Beat
- **Redis** serves as the message broker and result backend
- **Celery workers** execute tasks (candle fetching, strategy ticks, reconciliation)
- **Celery Beat** handles periodic scheduling (15m candle ticks, 4h reconciliation, etc.)
- Backtest runs remain synchronous in-process (no Celery overhead) — the same core logic is invoked directly by the backtest runner
- Paper and real trading run as Celery periodic tasks
- Future-proof: supports multi-strategy parallelism, independent worker scaling, and task retries out of the box

### Real-Time Communication
- **Event Publishing:** Redis pub/sub channels (market_data, strategy, risk, execution, portfolio, system)
- **WebSocket:** FastAPI WebSocket endpoint for streaming events to web clients
- **REST API:** FastAPI endpoints for historical data and current state queries
- **Architecture:** Core → Event Publisher → Redis → WebSocket → React
- **Decoupling:** Trading engine never waits for UI – events are fire-and-forget

### Frontend Stack
- **React + Vite + TypeScript** – modern, fast dev experience
- **WebSocket** – bidirectional real-time communication (`socket.io-client` or native WebSocket API)
- **Zustand** – lightweight state management for real-time data
- **React Query** – data fetching, caching, and synchronization for REST endpoints
- **Recharts** – charting library for candlestick charts and equity curves
- **TanStack Table** – powerful table component for trades/orders

### Error Handling
- All executor calls wrapped in retry logic with exponential backoff
- Crash recovery: persist state after every trade
- Reconciliation job: compare local state vs exchange state on startup

### Order Model
- Support MARKET and LIMIT order types
- Track order lifecycle: PENDING → FILLED | PARTIAL | REJECTED | CANCELLED

### Backtest Realism
- Configurable slippage (default: 0.1%)
- Fee modeling (default: Binance taker fee 0.1%)
- No lookahead bias enforcement

### Data Models
- **Pydantic v2** for all data validation and serialization
- Shared models between core logic and API layer
- Type-safe throughout (Python type hints + TypeScript interfaces)