---
name: api-route
description: 'Create a new FastAPI endpoint or route for GoldenGibbon. Use when asked to add an API endpoint, create a GET/POST/DELETE route, add a new REST resource, or expose backend data to the frontend. Covers: Pydantic response model, router file, app registration, and test file.'
argument-hint: '<HTTP method> <path> — <description>'
---

# New API Route

Creates a complete FastAPI endpoint following the GoldenGibbon architecture.

## When to Use
- "Create endpoint GET /api/portfolio/summary"
- "Add a route that returns..."
- "Expose X data via REST"
- "Create a POST endpoint to..."

## Procedure

### 1. Read the reference files first
- Read [`api/routes/market.py`](../../api/routes/market.py) — reference route implementation
- Read [`tests/test_market_routes.py`](../../tests/test_market_routes.py) — reference test pattern
- Read [`api/main.py`](../../api/main.py) — to see how routes are registered

### 2. Create or update the Pydantic response model

**File**: `core/models.py`

Add the response model at the end of the file (or in the appropriate section):
```python
class <Resource>Response(BaseModel):
    """Response model for <endpoint description>."""
    field_one: str
    field_two: float
    field_three: Optional[str] = None
    created_at: datetime
```

**Rules:**
- All response models live in `core/models.py`
- Use `Optional[T] = None` for nullable fields
- Use `str` for Decimal values that need precision (avoids float rounding)
- Never expose ORM models directly — always map to Pydantic

### 3. Create or update the router file

**File**: `api/routes/<domain>.py`

If file doesn't exist, create it:
```python
"""
<Domain> REST endpoints.

Mounted at /api/<domain> by api.main._include_routes.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from core.models import <Resource>Response
from db import get_db

router = APIRouter()


@router.get("/<path>", response_model=<Resource>Response)
def get_<resource>(db: Session = Depends(get_db)) -> <Resource>Response:
    """<Docstring describing what this endpoint does>."""
    # implementation
    ...
```

**Rules:**
- `router = APIRouter()` at module level — no prefix here (prefix is set in `api/main.py`)
- Always use `response_model=` on the decorator
- Raise `HTTPException(status_code=404, detail="X not found")` for missing resources
- Use `Depends(get_db)` for database access
- Use `get_settings()` from `core/config.py` for config access

### 4. Register the router in `api/main.py`

Find the `_include_routes()` function and add:
```python
from api.routes.<domain> import router as <domain>_router
app.include_router(<domain>_router, prefix="/api/<domain>", tags=["<domain>"])
```

### 5. Create the test file

**File**: `tests/test_<domain>_routes.py`

Follow [`tests/test_market_routes.py`](../../tests/test_market_routes.py):
```python
from fastapi.testclient import TestClient
from api.main import create_app

client = TestClient(create_app())


def test_get_<resource>_returns_200():
    response = client.get("/api/<domain>/<path>")
    assert response.status_code == 200
    data = response.json()
    assert "field_one" in data


def test_get_<resource>_not_found_returns_404():
    response = client.get("/api/<domain>/nonexistent")
    assert response.status_code == 404
```

**Rules:**
- Use `TestClient` from `fastapi.testclient` — no real HTTP calls
- Test both happy path and error cases (404, 422 validation errors)
- `conftest.py` auto-cleans the database — use it to seed test data if needed

### 6. Run tests

```bash
docker compose run --rm app python -m pytest tests/test_<domain>_routes.py -v
# Or locally:
.venv-test/bin/python -m pytest tests/test_<domain>_routes.py -v
```

## HTTP Status Codes
- `200` — success (GET, PUT)
- `201` — created (POST)
- `204` — no content (DELETE)
- `404` — resource not found
- `422` — validation error (automatic from Pydantic)
- `400` — bad request (business rule violation)
