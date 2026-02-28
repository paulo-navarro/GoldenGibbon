# 🦧 GoldenGibbon

<p align="center">
  <img src="GoldenGibbon.png" alt="GoldenGibbon Logo" width="300"/>
</p>

<p align="center">
  <strong>A modular, extensible crypto quantitative trading platform</strong>
</p>

<p align="center">
  <em>Designed for backtesting, paper trading, and live execution with real-time visibility</em>
</p>

---

> ## ⚠️ Disclaimer
>
> *This project was built for a developer's amusement,*
> *to risk our own money — code is the instrument.*
> *We make no guarantee of profit, not a single cent,*
> *nor bear responsibility for where your money went.*
> *If you choose to use it, the risk is yours to own —*
> *you trade at your own peril, you reap what you have sown.* 🎲

---

## 🎯 Vision

GoldenGibbon is not a simple trading bot — it's a **crypto quantitative trading platform** built for long-term research and execution. The system prioritizes:

- **Modularity** — Clean separation of concerns across all components
- **Extensibility** — Plugin architecture for strategies and indicators
- **Safety** — Multiple layers of risk management and capital protection
- **Visibility** — Real-time web dashboard with WebSocket event streaming
- **Reproducibility** — Full backtesting engine with historical data caching

---

## 🏗️ Architecture

GoldenGibbon follows a strict unidirectional data flow through isolated layers:

```
┌─────────────┐
│ Binance API │
└──────┬──────┘
       │
       ▼
┌─────────────────┐      ┌──────────────┐
│  Data Loader    │─────▶│  PostgreSQL  │
│  (REST/WS)      │      │  (Cache)     │
└────────┬────────┘      └──────────────┘
         │
         ▼
┌──────────────────┐
│ Indicator Engine │ ◀─── EMA, SMA, RSI, ADX, ATR
└────────┬─────────┘
         │
         ▼
┌──────────────────┐
│ Strategy Engine  │ ◀─── Smart Hodler, Mean Reversion, [Future Strategies]
└────────┬─────────┘
         │
         ▼
┌──────────────────┐
│   Risk Engine    │ ◀─── Position sizing, stop-loss, drawdown limits
└────────┬─────────┘
         │
         ▼
┌──────────────────┐
│ Execution Layer  │ ◀─── Paper / Live trading
└────────┬─────────┘
         │
         ▼
┌──────────────────┐
│  Portfolio Model │
└────────┬─────────┘
         │
         ▼
┌──────────────────┐
│ Event Publisher  │ ─────▶ Redis Pub/Sub
└──────────────────┘             │
                                 ▼
                        ┌─────────────────┐
                        │  FastAPI Server │
                        │   + WebSocket   │
                        └────────┬────────┘
                                 │
                                 ▼
                        ┌─────────────────┐
                        │ React Dashboard │
                        │  (Live Updates) │
                        └─────────────────┘
```

**Key Principle:** Each layer has a single responsibility and never depends on layers below it.

---

## ✨ Features

### Current (Phase 1 - Foundation & Backtest Engine ✅)

- ✅ **Data Loader** — Fetch historical candles from Binance REST API with PostgreSQL caching
- ✅ **Indicator Engine** — Pure functional indicators: EMA, SMA, RSI, ADX, ATR, Bollinger Bands
- ✅ **Config Layer** — YAML-based configuration for symbols, strategies, and settings
- ✅ **Docker Environment** — Multi-stage builds with PostgreSQL and Redis
- ✅ **Strategy Engine** — Abstract interface supporting Smart Hodler (Trend) and Mean Reversion (Counter-trend)
- ✅ **Risk Engine** — Sizing logic, hard stops, trailing stops (ATR-based), and time-based exits
- ✅ **Portfolio Manager** — Full accounting of balance, positions, trade history, and equity curve
- ✅ **Backtest Runner** — Fast candle-by-candle simulation loop with orderbook replay
- ✅ **Reporting** — Rich console output tables and PostgreSQL persistence of backtest results
- ✅ **Logging** — Structured JSON logs (structlog) for full observability of every decision
- ✅ **Test Suite** — 841 unit tests covering the entire pipeline

### Current (Phase 2 - Backend Complete ✅ · Frontend In Progress 🚧)

- ✅ **FastAPI + Uvicorn** — Application factory with lifespan, CORS, health check
- ✅ **Event System** — Redis pub/sub publisher with 6 channels and 23 event types
- ✅ **Event Integration** — Strategy, execution, and portfolio layers publish events in real-time
- ✅ **REST API** — 6 route modules: market, portfolio, trades, orders, strategy, system
- ✅ **WebSocket Streaming** — Real-time event broadcasting with channel filtering and connection manager
- ✅ **Database** — ORM models, Alembic migrations, seed script
- ✅ **Infrastructure** — Docker services, Makefile targets, environment config
- 🚧 **React Dashboard Foundation** — Vite + React + TS setup, MUI dark theme, App Shell layout with routing
- 📋 **React Dashboard Features** — Real-time charts, portfolio tracking, trade history, event streaming (Pending)

### Future (Phase 3+ - Production 🔮)

- 🔮 **Celery Workers** — Distributed task queue for live trading (docker-compose services ready)
- 🔮 **Paper Trading Mode** — Test strategies with live data
- 🔮 **Live Trading** — Binance integration with order lifecycle
- 🔮 **Multi-Strategy Support** — Run multiple strategies in parallel
- 🔮 **Regime-Based Allocation** — Automatic capital routing between Smart Hodler and Mean Reversion
- 🔮 **Parameter Optimization** — Grid search and walk-forward analysis

---

## 🦍 Smart Hodler Strategy

The flagship strategy is **Smart Hodler** — a trend-following system designed to capture intraday and multi-hour trends on the 15-minute timeframe.

### Signal Logic

**BUY** when all conditions are met:
- `EMA 50 > EMA 200` (bullish bias)
- `ADX(14) > 25` (strong trend)
- `Close > EMA 50` (price respects trend)
- `Volume > SMA(20)` (participation confirmed)
- **Hourly confirmation:** `EMA 21` rising + `RSI(14) > 45`

**SELL** with tiered exits:
1. **Hard exit (100%):** `EMA 50 < EMA 200` — trend structure broken
2. **Confirmed break (100%):** `Close < EMA 200` for 2 consecutive candles
3. **Momentum fade (50%):** `Close < EMA 50` AND `ADX falling`

**Session Filter:** No new entries during dead zones (weekends + overnight UTC sessions)

### Risk Management

- **Scaled entries:** 50% → 75% → 100% as trend strengthens
- **Trailing stop:** 2× ATR below highest close since entry
- **Hard stop:** 3% max drawdown per trade triggers cooldown
- **Cooldown period:** 48 hours after stop-loss to avoid revenge trading

See [`strategy_smart_hodler.md`](strategy_smart_hodler.md) for full specification.

---

## � Mean Reversion Strategy

The second strategy is **Mean Reversion** — a contrarian system designed to profit in range-bound markets where Smart Hodler sits idle.

### Signal Logic

**BUY** when all conditions are met:
- `Close ≤ Lower Bollinger Band (20, 2σ)` (price overextended)
- `RSI(14) < 30` (oversold confirmation)
- `ADX(14) < 25` (range-bound regime — **inverse** of Smart Hodler)
- `Volume > 1.5 × SMA(20)` (capitulation spike)
- **Hourly confirmation:** `RSI(14) > 35` + `Close > EMA(50)`

**SELL** with tiered exits:
1. **Middle reversion (50%):** `Close ≥ SMA(20)` — price reached the mean
2. **Full reversion (100%):** `Close ≥ Upper Band` OR `RSI > 70`
3. **Regime shift (100%):** `ADX rises > 30` — market started trending

### Risk Management

- **Position sizing:** 75% of capital (no scale-in — shorter trades)
- **Hard stop:** 2% max drawdown per trade
- **Time stop:** Exit after 16 candles (~4h) if middle band not reached
- **No trailing stop** — targets a fixed level (the mean)
- **Cooldown:** 8 candles (~2h) after stop-loss only; no cooldown after profit exits

### Regime Complementarity

| Market Regime | Smart Hodler | Mean Reversion |
|---|---|---|
| Trending (ADX > 25) | **Active** | Inactive |
| Range-bound (ADX < 25) | Inactive | **Active** |

See [`strategy_mean_reversion.md`](strategy_mean_reversion.md) for full specification.

---

## �🚀 Quick Start

### Prerequisites

- Docker & Docker Compose
- Make (optional, for convenience commands)

### Setup

1. **Clone the repository:**
   ```bash
   git clone https://github.com/yourusername/GoldenGibbon.git
   cd GoldenGibbon
   ```

2. **Configure environment:**
   ```bash
   cp .env.example .env
   # Edit .env with your settings (optional for development)
   ```

3. **Start services:**
   ```bash
   make dev    # Full stack (app + API + workers + postgres + redis)
   # Or just the API:
   make api    # API server + postgres + redis only
   ```

4. **Run database migrations:**
   ```bash
   make migrate
   ```

5. **Test the setup:**
   ```bash
   make test
   ```

6. **Access the API:**
   - Health: http://localhost:8000/health
   - Swagger docs: http://localhost:8000/docs
   - Candles: http://localhost:8000/api/market/candles/BTCUSDT
   - Portfolio: http://localhost:8000/api/portfolio/

### Running a Backtest

```bash
# Test data loader
docker compose run --rm -e PYTHONPATH=/app app python scripts/test_data_loader.py

# Test indicators
docker compose run --rm -e PYTHONPATH=/app app python scripts/test_indicators.py
```

---

## 📦 Project Structure

```
GoldenGibbon/
├── core/               # Core trading engine
│   ├── config.py       # YAML config loaders
│   ├── models.py       # Pydantic models (MarketData, Candle)
│   ├── events.py       # Event publisher (Redis pub/sub)
│   ├── data/           # Data fetching and caching
│   │   ├── binance_client.py
│   │   └── loader.py
│   ├── indicators/     # Technical indicators
│   │   ├── technical.py   # Pure functions (EMA, RSI, etc.)
│   │   └── engine.py      # Orchestration layer
│   ├── strategies/     # Strategy implementations
│   ├── risk/           # Risk management
│   ├── execution/      # Order execution (paper/live)
│   └── portfolio/      # Portfolio tracking
├── api/                # FastAPI REST API
│   ├── main.py         # App factory, lifespan, health endpoint
│   └── routes/         # Route modules
│       ├── market.py   # Candle & price endpoints
│       └── portfolio.py # Portfolio & equity-curve endpoints
├── db/                 # Database layer
│   ├── models.py       # SQLAlchemy ORM models
│   ├── utils.py        # Bidirectional ORM ↔ Pydantic converters
│   └── seeds.py        # Test data generation
├── config/             # Configuration files
│   ├── symbols.yaml    # Trading pairs
│   ├── strategies.yaml # Strategy parameters
│   └── settings.yaml   # System settings
├── tests/              # Test suite (841 tests)
│   ├── conftest.py     # Shared fixtures
│   ├── test_indicators.py
│   ├── test_market_routes.py
│   ├── test_portfolio_routes.py
│   └── ...             # 20+ test modules
├── scripts/            # Utility scripts
├── alembic/            # Database migrations
├── docker-compose.yml  # Services orchestration
├── Dockerfile          # Multi-stage build
├── Makefile            # Convenience commands
└── pyproject.toml      # Python dependencies
```

---

## 🧪 Testing

Run the full test suite:

```bash
make test
# Or directly:
docker compose run --rm -e PYTHONPATH=/app app pytest -v
```

Test specific modules:

```bash
# Indicators
docker compose run --rm -e PYTHONPATH=/app app pytest tests/test_indicators.py -v

# Database
docker compose run --rm -e PYTHONPATH=/app app pytest tests/test_database.py -v
```

Current test coverage: **841 passing tests** across indicators, strategies, events, API routes, and execution engine.

---

## 📊 Development Roadmap

Progress is tracked in [`kanban.md`](kanban.md). Current status:

### Phase 1: Foundation & Backtest Engine (100% Complete)
- ✅ **Tasks 1.1–1.28** — Fully implemented (Data, Indicators, Strategy engine, Risk manager, Portfolio tracking, Execution simulation, Backtesting loop, Logging)

### Phase 2: Real-Time Interface (61% Complete)
- ✅ **Tasks 2.1–2.21** — Backend complete: FastAPI, Pydantic models, ORM, Alembic migrations, event publisher, event channels, REST routes (market, portfolio, trades, orders, strategy, system), WebSocket endpoint & manager, event integration (strategy + execution + portfolio), Docker service, env config
- ✅ **Tasks 2.22–2.29, 2.49–2.50** — Frontend foundation: Vite setup, dependencies, TS interfaces, MUI theme, App Shell layout, React Router, Sidebar, Dockerfile, docker-compose service
- ✅ **Tasks 2.51–2.52, 2.55** — Integration: seed script, Makefile targets, REST endpoint tests
- 📋 Tasks 2.30–2.48 — React frontend stores, pages, components, main entrypoint
- 📋 Tasks 2.53–2.54, 2.56 — Architecture integration tests (event flow, WS reconnection, end-to-end)

### Phase 3: Infrastructure & Paper Trading (10% Complete)
- ✅ **Task 3.1** — Redis + Celery worker + Celery Beat services in docker-compose
- 📋 Tasks 3.2–3.10 (Celery app, live data feeds, state persistence)

### Phase 4: Real Trading (0% Complete)
- 📋 Tasks 4.1–4.10 (Binance executor, capital limits, alerting)

### Phase 5: Research Platform (0% Complete)
- 📋 Tasks 5.1–5.5 (Multi-strategy, optimization, parameter tuning)

---

## 🛠️ Technology Stack

**Backend:**
- Python 3.12
- FastAPI + Uvicorn
- SQLAlchemy 2.x + Alembic
- PostgreSQL 16
- Redis 7 (event pub/sub)
- pandas + numpy

**Data Sources:**
- Binance REST API
- Binance WebSocket (planned)

**Infrastructure:**
- Docker + Docker Compose
- Celery + Celery Beat (planned)

**Frontend (planned):**
- React + TypeScript
- Vite
- Zustand (state management)
- Recharts (charting)
- TanStack Query

**Testing:**
- pytest
- pytest-asyncio
- ta library (cross-validation)

---

## 🤝 Contributing

This is a personal trading platform under active development. Contributions, suggestions, and feedback are welcome!

### Development Workflow

1. Create a feature branch
2. Write tests for new functionality
3. Ensure all tests pass: `make test`
4. Submit a pull request

### Code Style

- Follow PEP 8
- Use type hints
- Write docstrings for public functions
- Keep functions pure when possible (no side effects)

---

## 📄 License

[MIT License](LICENSE) (or specify your preferred license)

---

## 🔗 Resources

- [Blueprint](blueprint.md) — Detailed technical specification
- [Kanban](kanban.md) — Development roadmap and task tracking
- [Smart Hodler Strategy](strategy_smart_hodler.md) — Trend-following strategy specification
- [Mean Reversion Strategy](strategy_mean_reversion.md) — Mean-reversion strategy specification

---

## 📈 MVP Progress

**Phase 1** (Foundation & Backtest Engine) and **Phase 2** (Real-Time Interface) make up the MVP.

```text
Phase 1  ██████████████████████████████  30/30  100%
Phase 2  ██████████████████░░░░░░░░░░░░  34/56   61%
Phase 3  ███░░░░░░░░░░░░░░░░░░░░░░░░░░░   1/10   10%
Phase 4  ░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░   0/10    0%
Phase 5  ░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░   0/5     0%
─────────────────────────────────────────────────────
Overall  ██████████████████░░░░░░░░░░░░  65/111  59%
```

> *Last updated: 28 Feb 2026 · 841 tests passing*

---

<p align="center">
  <strong>Built with 🦧 by the GoldenGibbon team</strong>
</p>
