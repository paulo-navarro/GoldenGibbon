.PHONY: dev prod down logs ps build test test-cov migrate migrate-create seed db-shell test-indicators test-loader pull-historical-data api frontend celery celery-logs ws-feed ws-feed-logs

# ── Development (hot-reload via volume mount) ──
dev:
	docker compose --profile dev up --build -d

# ── Production (slim image, restart policies) ──
prod:
	docker compose --profile prod up --build -d

# ── API (just the FastAPI server + infra) ──────
api:
	docker compose --profile dev up --build -d api postgres redis

# ── Frontend (dev server + API + infra) ────────
frontend:
	docker compose --profile dev up --build -d frontend api postgres redis

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

# ── Celery ─────────────────────────────────────
celery:
	docker compose --profile dev up --build -d celery-worker celery-beat postgres redis

celery-logs:
	docker compose --profile dev logs -f celery-worker celery-beat

# ── WebSocket Feed ─────────────────────────────
ws-feed:
	docker compose --profile dev up --build -d ws-feed postgres redis

ws-feed-logs:
	docker compose --profile dev logs -f ws-feed

# ── Testing ────────────────────────────────────
test:
	docker compose run --rm app python -m pytest tests/ -v

test-cov:
	docker compose run --rm app python -m pytest tests/ -v --cov=core --cov-report=term-missing

# ── Scripts ────────────────────────────────────
test-indicators:
	docker compose run --rm -e PYTHONPATH=/app app python scripts/test_indicators.py

test-loader:
	docker compose run --rm -e PYTHONPATH=/app app python scripts/test_data_loader.py

pull-historical-data:
	docker compose run --rm -e PYTHONPATH=/app app \
		python scripts/pull_historical_data.py --days $(or $(DAYS),730)

# ── Database ───────────────────────────────────
migrate:
	docker compose run --rm app alembic upgrade head

migrate-create:
	docker compose run --rm app alembic revision --autogenerate -m "$(message)"

seed:
	docker compose run --rm -e PYTHONPATH=/app app python db/seeds.py

db-shell:
	docker compose exec postgres psql -U trade -d trade

# ── Deploy to VPS (off-host build + ship) ──────
#
# Builds prod images locally, streams them over SSH to the VPS and
# restarts the stack there. Off-host builds avoid eating 3–5 GB of
# disk on the VPS during `pip install` / `npm ci`.
#
# Override VPS / VPS_PATH from the command line if needed:
#   make deploy-vps VPS=root@host VPS_PATH=/root/GoldenGibbon
VPS      ?= root@76.13.172.71
VPS_PATH ?= /root/GoldenGibbon
PROD_IMAGES = \
	goldengibbon-app-prod \
	goldengibbon-api-prod \
	goldengibbon-celery-worker-prod \
	goldengibbon-celery-beat-prod \
	goldengibbon-ws-feed-prod \
	goldengibbon-frontend-prod

deploy-build: ## Build all prod images locally
	docker compose --profile prod build

deploy-ship: deploy-build ## Stream built images to the VPS over SSH
	@echo "Streaming $$(echo $(PROD_IMAGES) | wc -w) images to $(VPS)..."
	docker save $(PROD_IMAGES) | gzip | ssh $(VPS) 'gunzip | docker load'

deploy-vps: deploy-ship ## Build locally, ship images, restart stack on VPS
	@echo "Pulling main + restarting prod stack on $(VPS)..."
	ssh $(VPS) 'cd $(VPS_PATH) && git pull origin main && docker compose --profile prod up -d'
	@echo "Deploy complete. Check: ssh $(VPS) 'docker ps --filter name=trade-'"
