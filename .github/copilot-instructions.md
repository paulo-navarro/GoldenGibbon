# GoldenGibbon — AI Agent Instructions

GoldenGibbon is a modular crypto quantitative trading platform. Architecture and vision: see [blueprint.md](../blueprint.md).

## Stack

**Backend**
- Python 3.12, FastAPI >=0.115, SQLAlchemy 2.0, Pydantic 2.6, Alembic
- Celery 5.4 + Redis, python-binance, structlog, pandas, numpy, ta

**Frontend**
- React 19, TypeScript ~5.9, MUI v7, Zustand 5, React Query v5 (@tanstack/react-query)
- Vite, React Router v7, Recharts, @tanstack/react-table

**Infra**: Docker Compose (profiles: `dev`, `prod`), PostgreSQL 16, Redis 7

## Architecture Rules

The system is a strict unidirectional pipeline:

```
Market API → Data Loader → Indicators → Strategy → Risk → Execution → Portfolio
```

1. **Strategies are pure decision engines** — never call APIs, never place orders, never mutate portfolio directly
2. **Risk engine** sizes positions and checks stops — never places orders itself
3. **Pydantic models** (`core/models.py`) = business logic and data validation
4. **ORM models** (`db/models.py`) = database schema only — never use in business logic
5. Each layer depends only on layers above it, never below

## Commands

```bash
# Tests
docker compose run --rm app python -m pytest tests/ -v
docker compose run --rm app python -m pytest tests/test_foo.py -v   # single file
docker compose run --rm app python -m pytest tests/ -v --cov=core --cov-report=term-missing

# Dev local (without Docker)
.venv-test/bin/python -m pytest tests/ -x -q

# Migrations
docker compose run --rm app alembic revision --autogenerate -m "<description>"
docker compose run --rm app alembic upgrade head

# Dev environment
make dev        # all services
make api        # API + infra only
make frontend   # frontend + API + infra
```

## Key Conventions

### Signals
`Signal` enum in `core/models.py`: `buy | sell_full | sell_half | hold`

### Strategy pattern
- Inherit from `core.strategies.base.Strategy`
- Implement `name` (property → str) and `decide(market_data, portfolio) -> Signal`
- Registry is **auto-discovery** — do not edit `core/strategies/registry.py` manually
- Reference implementation: [core/strategies/smart_hodler.py](../core/strategies/smart_hodler.py)

### API routes pattern
- Response models (Pydantic) → `core/models.py`
- Router files → `api/routes/<domain>.py` with `router = APIRouter()`
- Include in app → `api/main.py` via `app.include_router(router, prefix="/api/<domain>", tags=["<domain>"])`
- Reference: [api/routes/market.py](../api/routes/market.py)

### Events pattern
- Publisher: `get_publisher()` from `core/events.py`
- Fire-and-forget, never raises on Redis failure
- Publish dict: `publisher.publish(EventChannel.X, EventType.Y, {...})`
- Publish model: `publisher.publish_model(EventChannel.X, EventType.Y, pydantic_obj)`

### Config
- `get_settings()` from `core/config.py` — singleton, Pydantic-validated
- ENV vars for infra only (`DATABASE_URL`, `REDIS_URL`, `POSTGRES_*`)
- Symbols: `XUSDT` format enforced by validator

### Database
- ORM models inherit from `db.Base` in `db/models.py`
- Session: `get_session()` context manager from `db`
- FastAPI dependency: `get_db` from `db`
- Every schema change requires an Alembic migration

### Testing
- Every new backend module gets `tests/test_<module>.py`
- `conftest.py` auto-cleans the database before each test
- Use `pytest.mark.parametrize` for table-driven tests
- Mock external APIs — no real network calls in tests
- Reference test for strategy: [tests/test_smart_hodler.py](../tests/test_smart_hodler.py)
- Reference test for routes: [tests/test_market_routes.py](../tests/test_market_routes.py)

### Frontend
- MUI: import by component, not barrel (`import Box from '@mui/material/Box'`, not `import { Box } from '@mui/material'`)
- Zustand stores → `frontend/src/stores/`; reference: [frontend/src/stores/marketStore.ts](../frontend/src/stores/marketStore.ts)
- Custom hooks → `frontend/src/hooks/`
- React Query v5: use `useQuery`, `useMutation` from `@tanstack/react-query`
- TypeScript: always type props with `interface`, use `Record<string, T>` for dictionaries
