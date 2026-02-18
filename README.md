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
│ Strategy Engine  │ ◀─── Smart Hodler, [Future Strategies]
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

### Current (Phase 1 - Foundation ✅)

- ✅ **Data Loader** — Fetch historical candles from Binance REST API with PostgreSQL caching
- ✅ **Indicator Engine** — Pure functional indicators: EMA, SMA, ATR, RSI, ADX
- ✅ **Config Layer** — YAML-based configuration for symbols, strategies, and settings
- ✅ **Docker Environment** — Multi-stage builds with PostgreSQL and Redis
- ✅ **Test Suite** — Comprehensive unit tests with 32 passing tests

### In Progress (Phase 1 - Backtest Engine 🚧)

- 🚧 **Strategy Base Class** — Abstract interface for strategy implementations
- 🚧 **Smart Hodler Strategy** — Trend-following 15m/1H strategy with tiered exits
- 🚧 **Risk Engine** — Position sizing, trailing stops, hard stops
- 🚧 **Portfolio Model** — Track balance, positions, equity curve
- 🚧 **Backtest Runner** — Candle-by-candle simulation with metrics

### Planned (Phase 2 - Real-Time Interface 📋)

- 📋 **FastAPI Backend** — REST endpoints + WebSocket event streaming
- 📋 **React Dashboard** — Real-time charts, portfolio tracking, trade history
- 📋 **Event System** — Redis pub/sub for live updates
- 📋 **WebSocket Client** — Auto-reconnecting client with state management

### Future (Phase 3+ - Production 🔮)

- 🔮 **Celery Workers** — Distributed task queue for live trading
- 🔮 **Paper Trading Mode** — Test strategies with live data
- 🔮 **Live Trading** — Binance integration with order lifecycle
- 🔮 **Multi-Strategy Support** — Run multiple strategies in parallel
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

## 🚀 Quick Start

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
   docker compose up -d postgres redis
   ```

4. **Run database migrations:**
   ```bash
   docker compose run --rm app alembic upgrade head
   ```

5. **Test the setup:**
   ```bash
   make test
   ```

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
├── db/                 # Database layer
│   ├── models.py       # SQLAlchemy ORM models
│   ├── utils.py        # DB helpers
│   └── seeds.py        # Test data generation
├── config/             # Configuration files
│   ├── symbols.yaml    # Trading pairs
│   ├── strategies.yaml # Strategy parameters
│   └── settings.yaml   # System settings
├── tests/              # Test suite
│   ├── conftest.py     # Shared fixtures
│   ├── test_indicators.py
│   └── test_database.py
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

Current test coverage: **32 passing tests** across indicators and data layer.

---

## 📊 Development Roadmap

Progress is tracked in [`kanban.md`](kanban.md). Current status:

### Phase 1: Foundation & Backtest Engine (40% Complete)
- ✅ Tasks 1.1–1.9 (Project setup, data layer, indicators)
- 🚧 Tasks 1.10–1.22 (Strategy, risk, backtest runner)

### Phase 2: Real-Time Interface (0% Complete)
- 📋 Tasks 2.1–2.52 (FastAPI backend, React frontend, WebSocket)

### Phase 3: Infrastructure & Paper Trading (0% Complete)
- 📋 Tasks 3.1–3.10 (Celery, live data feeds, state persistence)

### Phase 4: Real Trading (0% Complete)
- 📋 Tasks 4.1–4.10 (Binance executor, capital limits, alerting)

### Phase 5: Research Platform (0% Complete)
- 📋 Tasks 5.1–5.7 (Multi-strategy, optimization, parameter tuning)

---

## 🛠️ Technology Stack

**Backend:**
- Python 3.12
- FastAPI (planned)
- SQLAlchemy 2.x + Alembic
- PostgreSQL 16
- Redis 7
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

## ⚠️ Disclaimer

**This software is for educational and research purposes.**

- Cryptocurrency trading involves substantial risk of loss
- Past performance does not guarantee future results
- Never trade with money you cannot afford to lose
- The authors are not responsible for any financial losses
- Always test strategies thoroughly in paper trading before using real capital

---

## 📄 License

[MIT License](LICENSE) (or specify your preferred license)

---

## 🔗 Resources

- [Blueprint](blueprint.md) — Detailed technical specification
- [Kanban](kanban.md) — Development roadmap and task tracking
- [Smart Hodler Strategy](strategy_smart_hodler.md) — Full strategy specification

---

<p align="center">
  <strong>Built with 🦧 by the GoldenGibbon team</strong>
</p>
