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

### Data & Market Feed

- **Binance REST Client** — Historical candle fetching with PostgreSQL caching and symbol validation
- **Binance WebSocket Feed** — Real-time candle-close stream driving the live trading pipeline
- **Multi-Timeframe Support** — 15-minute and 1-hour candles computed and passed together into every strategy
- **Reconciliation** — Background jobs ensuring DB/cache coherency between ticks

### Indicator Engine

Pure functional indicators, cross-validated against the `ta` library:

- EMA, SMA, ATR, RSI, ADX, Bollinger Bands

### Strategy Engine

- **Auto-discovery registry** — drop a file in `core/strategies/`, it's available instantly
- **Smart Hodler** — trend-following (EMA cross + ADX filter + volume confirmation + session gate)
- **Mean Reversion** — range-bound counter-trend (Bollinger Bands + RSI oversold + ADX inverse filter)
- **Session Filter** — suppresses entries during dead zones (weekends + overnight UTC)
- **State machine** — `FLAT → POSITION → REDUCED → COOLDOWN` with configurable cooldown periods

### Risk Engine

- **Scaled entries** — 50 % → 75 % → 100 % position sizing as conviction grows
- **ATR trailing stop** — 2× ATR below highest close since entry
- **Hard stop** — per-trade max drawdown threshold triggering cooldown
- **Time stop** — exits Mean Reversion positions after N candles if target not reached
- **Exit proximity tracking** — live distance-to-stop surfaced in the dashboard
- **Global kill switch** — halts all new entries platform-wide instantly

### Execution Layer

- **PaperExecutor** — full simulated execution with configurable slippage and fees
- **BinanceExecutor** — real broker integration (MARKET orders, PENDING → FILLED flow)
- **Exchange stop orders** — native stop-loss placement and management on Binance
- **Retry logic** — exponential backoff for transient network and broker failures
- **Crash recovery** — state restored safely from DB after unexpected restarts

### Backtest Engine

- **Candle-by-candle simulation loop** — identical pipeline to live trading
- **Metrics** — total return, max drawdown, win rate, Sharpe ratio, profit factor, vs. Buy & Hold
- **Rich reporting** — console tables + PostgreSQL persistence of every run
- **Strategy comparison** — side-by-side results across multiple strategies
- **Multi-strategy backtest** — concurrent strategy simulation in a single run
- **Grid search optimizer** — parameter sweep with configurable objective function

### Portfolio & Allocation

- **Portfolio Manager** — USDT balance, open positions, equity curve, full trade history
- **Portfolio Allocation Engine** — dynamic capital distribution across active strategies
- **Regime detection** — market regime classification fed into allocation decisions

### Infrastructure & Operations

- **Celery + Redis** — distributed task queue with Beat scheduler for periodic jobs
- **Strategy tick pipeline** — Celery chain: fetch → indicators → strategy → risk → execute
- **State persistence** — strategy states and portfolio snapshots survive worker restarts
- **Monitoring** — health endpoints + Docker healthchecks for app, workers, and Beat
- **Alerting** — webhook/push notifications on critical events (stop-loss hit, kill switch, errors)
- **Structured logging** — JSON logs via structlog for every decision in the pipeline
- **Docker Compose** — multi-profile stack (`dev` / `prod`) with PostgreSQL 16, Redis 7

### REST API (FastAPI)

10 route modules: `market`, `portfolio`, `trades`, `orders`, `strategy`, `system`, `backtest`, `config`, `symbols`, `app_config`

- Real-time candles, price, equity curve, open positions, trade history, order book
- Strategy state and signal conditions per symbol
- Backtest trigger and results retrieval
- Live config inspection

### WebSocket & Events

- **Event publisher** — Redis pub/sub with 6 channels and 26 event types
- **WebSocket endpoint** — real-time event broadcast with per-client channel filtering
- **Connection manager** — multiple concurrent clients, heartbeat, auto-reconnect

### React Dashboard

11 pages with live WebSocket updates:

| Page | Content |
|------|---------|
| **Dashboard** | Portfolio summary, equity curve, recent signals |
| **Prices** | Live price tickers for all configured symbols |
| **Portfolio** | Positions, balance breakdown, P&L |
| **Strategy** | Per-symbol strategy state, signal conditions checklist |
| **Trades** | Full trade history with filters and stats |
| **Orders** | Open and historical orders |
| **Metrics** | Backtest results and performance charts |
| **Symbols** | Configured trading pairs and their status |
| **Logs** | Streaming structured log viewer |
| **Activity** | Real-time event stream from all channels |
| **Settings** | Platform configuration |

- MUI v7 dark theme, Zustand stores, TanStack Query v5, Recharts
- Exit proximity indicator and cycle status components

### Test Suite

**1 360 passing tests** across every layer: indicators, strategies, risk engine, execution, portfolio, backtest, API routes, WebSocket, events, Celery tasks, and database.

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

Current test coverage: **1 360 passing tests** across indicators, strategies, events, API routes, execution engine, backtest, and Celery tasks.

---

## 🚦 Validation Gate (mandatory before live)

**Phase-9 golden rule: no strategy change goes live without an approved
walk-forward gate run.** The gate (`core/backtest/gate.py`) walk-forwards
the candidate parameters over ≥ 365 days (≥ 3 folds, costs and exchange
filters enabled) and approves only when **all** criteria hold:

- positive net return in every out-of-sample test fold
- profit factor > 1.2 in every fold that produced one
- no fold with max drawdown > 25%
- no fold flagged as overfit

```bash
# CLI (exit code 0 = approved, 2 = rejected):
python -m core.backtest.gate smart_hodler '{"ema_fast": 12}' BTCUSDT

# Or as a Celery task:
from core.tasks import run_validation_gate
run_validation_gate.delay("smart_hodler", {"ema_fast": 12})
```

Every gate run persists its per-fold evidence in `backtest_results` under
a `gate:`-prefixed `run_id` — queryable via
`GET /api/backtest/history?run_id=gate:...`.

---

## 📊 Roadmap

Active development is tracked in [roadmap/kanban.md](roadmap/kanban.md).

**Completed:** Phases 1–4 (foundation, backtest engine, real-time interface, live trading infrastructure, Binance execution, alerting, allocation, regime detection, UI visual improvements)

**Next:** Phase 5 — BearGuard short strategy (spot margin shorts via Binance Cross Margin)

---

## 🛠️ Technology Stack

**Backend:**
- Python 3.12, FastAPI + Uvicorn
- SQLAlchemy 2.x + Alembic, PostgreSQL 16
- Redis 7 (event pub/sub + Celery broker)
- Celery 5 + Celery Beat
- pandas, numpy, ta
- structlog

**Data Sources:**
- Binance REST API
- Binance WebSocket (real-time candles)

**Infrastructure:**
- Docker + Docker Compose (profiles: `dev`, `prod`)
- Multi-stage Dockerfile

**Frontend:**
- React 19 + TypeScript, Vite
- MUI v7 (dark theme)
- Zustand 5 (state management)
- TanStack Query v5
- Recharts

**Testing:**
- pytest + pytest-asyncio
- ta library (cross-validation)
- 1 360 tests

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
- [Roadmap](roadmap/kanban.md) — Development kanban and task tracking
- [Smart Hodler Strategy](roadmap/strategy_smart_hodler.md) — Trend-following strategy specification
- [Mean Reversion Strategy](roadmap/strategy_mean_reversion.md) — Mean-reversion strategy specification
- [BearGuard Strategy](roadmap/strategy_bear_guard.md) — Short strategy specification (Phase 5)

---

<p align="center">
  <strong>Built with 🦧 by the GoldenGibbon team</strong>
</p>
