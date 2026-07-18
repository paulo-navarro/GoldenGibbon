---
name: run-tests
description: 'Run the GoldenGibbon test suite on the host (fast path, no Docker rebuild). Use when asked to run tests, verify a change, or check for regressions. Covers: venv + Postgres setup, schema recreation, known environmental failures, and the baseline-diff technique for proving zero regressions.'
argument-hint: '[test file/pattern, or "full" for the whole suite]'
---

# Run Tests (host)

Fast host-based testing. `make test` runs inside Docker (needs the full stack);
this skill runs directly against the dev Postgres container — much faster for
iteration.

## Setup (once per session)

```bash
cd /home/paulo/projects/GoldenGibbon
docker compose up -d postgres          # exposes 127.0.0.1:5444 (host) → 5432
source .venv-test/bin/activate         # the test venv (NOT venv/.venv — they don't exist)
export POSTGRES_HOST=localhost POSTGRES_PORT=5444
export PYTEST_ADDOPTS="-p no:cacheprovider"   # .pytest_cache is root-owned → write errors
```

Credentials come from `.env` (trade / trade_dev / trade); `db/__init__.py`
builds the URL from `POSTGRES_*` env vars.

## Schema

Tests do NOT run alembic — the schema must match `db/models.py`. After any
model change (or when hitting `UndefinedColumn` / `ProgrammingError`), recreate:

```bash
python -c "from db import models; from db import drop_all_tables, init_db; drop_all_tables(); init_db(); print('schema ok')"
```

**Critical:** the `from db import models` import is required FIRST — without it
`Base.metadata` is empty and `drop_all_tables()` silently does nothing.

**Note:** the alembic CLI is broken in `.venv-test` (`alembic` exits 1, no
`__main__`). Never try to migrate the test DB — always recreate via the
snippet above. To find the current migration head, compute it:

```bash
python - <<'PY'
import re, glob
revs, downs = {}, set()
for f in glob.glob('alembic/versions/*.py'):
    t = open(f).read()
    r = re.search(r'^revision[^=]*=\s*["\']([^"\']+)', t, re.M)
    for x in re.findall(r'^down_revision[^=]*=\s*["\']([^"\']+)', t, re.M): downs.add(x)
    if r: revs[r.group(1)] = f
print('HEAD:', [r for r in revs if r not in downs])
PY
```

## Running

```bash
python -m pytest tests/test_reconciliation.py -q        # one file (~1-2s)
python -m pytest tests/ -q                              # full suite (~70s)
```

## Known environmental failures — NOT regressions

The full suite has **~33 pre-existing failures + 2 errors** on this machine.
Do not chase them; do not report them as caused by your change:

- `tests/test_websocket.py` — TestConnectionManager (all)
- `tests/test_regime_gate.py` — SmartHodler/MeanReversion gate tests
- `tests/test_celery_task_tick.py` — several tick tests
- Any test using FastAPI `TestClient(app)` → `PermissionError: logs/trading.log`
  (file is root-owned, written by containers). Same story for root-owned
  `.pytest_cache/` and `frontend/dist/`.

## Proving zero regressions (baseline diff)

When a change might affect many suites, compare failure lists against a clean
baseline instead of eyeballing counts:

```bash
SP=/tmp/gg-testdiff; mkdir -p $SP
python -m pytest tests/ -q 2>&1 | grep '^FAILED' | sort > $SP/mine.txt
git stash push -m tmp && \
python -c "from db import models; from db import drop_all_tables, init_db; drop_all_tables(); init_db()" && \
python -m pytest tests/ -q 2>&1 | grep '^FAILED' | sort > $SP/base.txt ; \
git stash pop
comm -23 $SP/mine.txt $SP/base.txt   # lines here = YOUR regressions (empty = clean)
```

Remember to recreate the schema after each stash/pop if models changed, and
check `git stash list` first — the user may have their own stashes; never drop
those.
