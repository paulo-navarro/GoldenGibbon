---
name: migration
description: 'Create an Alembic database migration for GoldenGibbon. Use when asked to add a column, create a table, rename a field, drop a constraint, or make any schema change to db/models.py. Covers: editing the ORM model, generating the migration, reviewing it, and applying it.'
argument-hint: '<plain-english description of the schema change>'
---

# New Alembic Migration

Creates a complete database migration following the GoldenGibbon conventions.

## When to Use
- "Add a column X to table Y"
- "Create a new table for Z"
- "Rename field X to Y"
- "Add index on column X"
- Any change to `db/models.py`

## Procedure

### 1. Edit `db/models.py`

Apply the schema change to the ORM model. Follow these conventions:

**Column types:**
| Data | SQLAlchemy type |
|---|---|
| IDs | `BigInteger` (primary key) |
| Prices / amounts | `Numeric(20, 8)` |
| Percentages / ratios | `Numeric(10, 4)` |
| Symbol / strategy names | `String(20)` / `String(50)` |
| Timestamps | `DateTime(timezone=True)` |
| Flexible/nested data | `JSONB` (PostgreSQL) |
| Flags | `Boolean` |

**Adding a nullable column** (safest for existing tables):
```python
new_field: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
```

**Adding an index:**
```python
# Inline (single column)
symbol: Mapped[str] = mapped_column(String(20), nullable=False, index=True)

# Composite (via __table_args__)
__table_args__ = (
    Index("ix_tablename_col1_col2", "col1", "col2"),
)
```

### 2. Generate the migration via Docker

```bash
docker compose run --rm app alembic revision --autogenerate -m "<short description>"
```

The generated file will appear in `alembic/versions/`.

### 3. Review the generated file

Open the new file and verify:
- `upgrade()` contains the expected `op.add_column / op.create_table / op.create_index` calls
- `downgrade()` contains the correct reversal
- No unexpected tables or columns were auto-detected

**Common Alembic blind spots to fix manually:**
- Renames are detected as drop + add (fix by using `op.alter_column` with `new_column_name`)
- `server_default` changes may be missed
- JSONB column type might default to generic JSON — change to `postgresql.JSONB`

### 4. Apply the migration

```bash
docker compose run --rm app alembic upgrade head
```

### 5. Run tests to verify

```bash
docker compose run --rm app python -m pytest tests/test_database.py -v
# Or locally:
.venv-test/bin/python -m pytest tests/test_database.py -v
```

## Rules
- Never modify the database schema directly (no raw SQL DDL)
- Always pair a `db/models.py` change with a migration file
- Prefer `nullable=True` for new columns on existing tables to avoid backfill issues
- Index naming convention: `ix_<tablename>_<column(s)>`
- Unique constraint naming convention: `uq_<tablename>_<column(s)>`
