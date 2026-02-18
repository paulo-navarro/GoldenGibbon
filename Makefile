.PHONY: dev prod down logs ps build test test-cov migrate migrate-create seed db-shell

# ── Development (hot-reload via volume mount) ──
dev:
	docker compose --profile dev up --build -d

# ── Production (slim image, restart policies) ──
prod:
	docker compose --profile prod up --build -d

# ── Helpers ────────────────────────────────────
down:
	docker compose --profile dev --profile prod down

logs:
	docker compose --profile dev --profile prod logs -f

ps:
	docker compose --profile dev --profile prod ps

build:
	docker compose --profile dev build
	docker compose --profile prod build

# ── Testing ────────────────────────────────────
test:
	docker compose run --rm app python -m pytest tests/ -v

test-cov:
	docker compose run --rm app python -m pytest tests/ -v --cov=core --cov-report=term-missing

# ── Database ───────────────────────────────────
migrate:
	docker compose run --rm app alembic upgrade head

migrate-create:
	docker compose run --rm app alembic revision --autogenerate -m "$(message)"

seed:
	docker compose run --rm app python db/seeds.py

db-shell:
	docker compose exec postgres psql -U trade -d trade
