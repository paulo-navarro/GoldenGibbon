---
name: frontend-check
description: 'Validate GoldenGibbon frontend changes (typecheck, lint, build) and keep TS types in sync with the backend. Use after editing anything under frontend/, or when adding a field to a Pydantic model / API endpoint that the UI consumes. Covers the root-owned dist/ build caveat and the full backend→frontend field propagation checklist.'
---

# Frontend Check

Stack: React 19 + TS + Vite, MUI v7 (`Grid size={{...}}`), @tanstack/react-query
v5 + react-table v8, zustand stores. `node_modules` is already installed.

## Validation commands

```bash
cd /home/paulo/projects/GoldenGibbon/frontend
npx tsc -b            # typecheck — must produce NO output
npx eslint src/path/to/changed-files.tsx
npm run build         # optional; see caveat below
```

**Build caveat:** `npm run build` will FAIL at the end with
`EACCES ... dist/assets` — `dist/` is root-owned (written by containers). This
is environmental. The build is considered OK if the output shows
`✓ N modules transformed` before the EACCES; `tsc -b` clean is the real gate.

**Known lint warning (pre-existing, ignore):** `react-hooks/incompatible-library`
on `useReactTable` in TradesPage.

## Backend → frontend field propagation checklist

When a field is added to a Pydantic response model (`core/models.py`) or an
API route (`api/routes/*.py`), it must ripple through ALL of:

1. **`src/types/*.ts`** — the interface mirrors the Pydantic model 1:1
   (Decimals arrive as `string`, datetimes as ISO `string`). Header comments
   say which backend model each type mirrors.
2. **Literal constructors** — grep the stores for object literals of that type:
   `src/stores/*.ts` build typed objects from WebSocket events (e.g.
   `tradesStore.handleEvent` constructs a `Trade`). A new required field breaks
   `tsc -b` there — that error is your checklist, don't suppress it.
3. **Query hooks (`src/api/queries.ts`)** — for a new query param: add it to
   the `UseXParams` interface, destructure it, add it to **both** the
   `queryKey` array (cache correctness!) and the `fetchApi` params object.
   `fetchApi` drops `null`/`''` params automatically, so `'' → undefined`
   mapping in pages is the convention for "filter off".
4. **Pages** — filter UIs follow the TradesPage pattern: a `Filters` interface,
   `TextField select` with `<MenuItem value="">All</MenuItem>`, params built
   with `filters.x || undefined`, plus client-side re-filter in the table's
   `useMemo`.

Backend counterpart of the same checklist: SQLAlchemy model (`db/models.py`) +
alembic migration, Pydantic model (`core/models.py`), ORM→Pydantic converter
(`db/utils.py` `orm_to_*`), route Query param + `_base_query` filter
(`api/routes/*.py`), and any API-shape test asserting exact keys
(`tests/test_trade_routes.py::test_trade_shape`-style tests fail on new
fields — update the expected set).
