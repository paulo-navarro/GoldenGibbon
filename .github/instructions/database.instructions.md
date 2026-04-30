---
description: "Use when writing database models, migrations, or queries in db/ or alembic/. Covers GoldenGibbon ORM conventions: Base inheritance, column types, indexes, JSONB, migrations via Alembic, and the separation from Pydantic models."
applyTo: "db/**/*.py, alembic/versions/**"
---

# GoldenGibbon Database Conventions

## ORM Model Pattern
All ORM models inherit from `db.Base`:
```python
from db import Base
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import String, Numeric, DateTime, BigInteger, Boolean
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.sql import func

class MyRecord(Base):
    __tablename__ = "my_table"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    value: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    metadata: Mapped[Optional[Dict]] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
```

## Column Type Conventions
| Data | SQLAlchemy type |
|---|---|
| IDs | `BigInteger` (primary key) |
| Prices / amounts | `Numeric(20, 8)` |
| Percentages / ratios | `Numeric(10, 4)` |
| Symbol names | `String(20)` |
| Strategy names | `String(50)` |
| Short strings | `String(100)` |
| Long text | `Text` |
| Flexible data | `JSONB` (PostgreSQL) |
| Timestamps | `DateTime(timezone=True)` |
| Flags | `Boolean` |

## Index Conventions
```python
# Single-column index on a Mapped field
symbol: Mapped[str] = mapped_column(String(20), nullable=False, index=True)

# Composite index via __table_args__
__table_args__ = (
    Index("ix_my_table_symbol_created", "symbol", "created_at"),
    UniqueConstraint("symbol", "timeframe", "open_time", name="uq_candles_symbol_tf_time"),
)
```

## Migrations — Always via Alembic
Every change to `db/models.py` requires a migration:
```bash
# Generate (auto-detects changes)
docker compose run --rm app alembic revision --autogenerate -m "<description>"

# Apply
docker compose run --rm app alembic upgrade head
```

**Rules:**
- Never modify the database schema directly (no `CREATE TABLE` by hand)
- Always review the auto-generated migration before applying — Alembic can miss renames
- Migrations are numbered by Alembic revision IDs — do not rename files

## Separation from Business Logic
- ORM models (`db/models.py`) are for **persistence only**
- Business logic uses **Pydantic models** from `core/models.py`
- Map ORM → Pydantic using utility functions in `db/utils.py`
- Never import `db.models` into `core/` modules

## Session Usage
```python
# In business logic / tasks
from db import get_session
with get_session() as session:
    record = session.get(MyRecord, record_id)
    session.add(new_record)
    # auto-commit on exit, auto-rollback on exception

# In FastAPI endpoints
from db import get_db
from sqlalchemy.orm import Session
@router.get("/path")
def endpoint(db: Session = Depends(get_db)):
    result = db.query(MyRecord).filter_by(symbol="BTCUSDT").first()
```

## JSONB Usage
JSONB columns store flexible indicator data and config snapshots:
```python
# Store
record.indicators = {"ema_50": 50000.12, "rsi": 65.4, "adx": 28.5}

# Query by JSONB key (PostgreSQL)
from sqlalchemy import cast, String
session.query(CandleRecord).filter(
    CandleRecord.indicators["ema_50"].astext.cast(Float) > 45000
)
```
