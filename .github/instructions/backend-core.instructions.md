---
description: "Use when writing Python code in core/ or api/. Covers the GoldenGibbon backend conventions: model placement, imports, events, config, session management, and module structure."
applyTo: "core/**/*.py, api/**/*.py"
---

# GoldenGibbon Backend Conventions

## Model Placement — Critical Rule
- **Business logic / validation** → `core/models.py` (Pydantic `BaseModel`)
- **Database schema** → `db/models.py` (SQLAlchemy, inherits `db.Base`)
- These are completely separate — never import ORM models into business logic or vice versa

## Imports Pattern

```python
# Pydantic models (for business logic)
from core.models import Signal, MarketData, Portfolio, Position

# ORM models (for DB queries only)
from db.models import CandleRecord, PositionRecord

# Database session
from db import get_db, get_session   # get_db = FastAPI dependency, get_session = context manager

# Config
from core.config import get_settings  # singleton

# Events
from core.events import get_publisher, EventChannel, EventType
```

## Config Usage
```python
settings = get_settings()  # Pydantic-validated, singleton
# ENV vars for infra only: DATABASE_URL, REDIS_URL, POSTGRES_*
# Symbols must be uppercase and end with USDT: "BTCUSDT"
```

## Events Usage
```python
publisher = get_publisher()

# Fire-and-forget dict
publisher.publish(EventChannel.STRATEGY, EventType.SIGNAL_GENERATED, {
    "symbol": "BTCUSDT",
    "signal": "buy",
})

# Fire-and-forget Pydantic model
publisher.publish_model(EventChannel.EXECUTION, EventType.ORDER_FILLED, order_model)
# Never raises — Redis failure is silently logged
```

## Database Session
```python
# In business logic / tasks
with get_session() as session:
    records = session.query(CandleRecord).filter_by(symbol="BTCUSDT").all()

# In FastAPI endpoints — use dependency injection
@router.get("/path")
def endpoint(db: Session = Depends(get_db)):
    ...
```

## Module `__init__.py` Pattern
Every module that exposes a public API should have a docstring:
```python
"""
<Module name> — <one-line purpose>.

<Layer position in pipeline>: X → **This module** → Y
"""
```

## Architecture Layer Rules
```
Market API → Data Loader → Indicators → Strategy → Risk → Execution → Portfolio
```
- **Strategies** (`core/strategies/`): pure `decide(market_data, portfolio) -> Signal` — no side effects
- **Risk engine** (`core/risk/`): sizes positions, checks stops — never places orders
- **Execution** (`core/execution/`): places orders — never makes strategy decisions
- Each layer only imports from layers above it
