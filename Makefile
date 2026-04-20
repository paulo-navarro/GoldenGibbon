.DEFAULT_GOAL := menu

include ./make_cmd/colors.mk
include ./make_cmd/cli.mk

# ── Development ───────────────────────────────────

dev: ## Start dev environment (all services)
	docker compose --profile dev up --build -d

api: ## Start API + infra only
	docker compose --profile dev up --build -d api postgres redis

frontend: ## Start frontend + API + infra
	docker compose --profile dev up --build -d frontend api postgres redis

celery: ## Start Celery workers + infra
	docker compose --profile dev up --build -d celery-worker celery-beat postgres redis

ws-feed: ## Start WebSocket feed + infra
	docker compose --profile dev up --build -d ws-feed postgres redis

# ── Production ────────────────────────────────────

prod: ## Start production stack
	docker compose --profile prod up --build -d

# ── Containers ────────────────────────────────────

down: ## Stop all containers
	docker compose --profile dev --profile prod down

logs: ## Tail all container logs
	docker compose --profile dev --profile prod logs -f

ps: ## Show container status
	docker compose --profile dev --profile prod ps

build: ## Build all images (dev + prod)
	docker compose --profile dev build
	docker compose --profile prod build

celery-logs: ## Tail Celery worker/beat logs
	docker compose --profile dev logs -f celery-worker celery-beat

ws-feed-logs: ## Tail WebSocket feed logs
	docker compose --profile dev logs -f ws-feed

# ── Testing ───────────────────────────────────────

test: ## Run test suite
	docker compose run --rm app python -m pytest tests/ -v $(TEST_ARGS)

test-cov: ## Run tests with coverage report
	docker compose run --rm app python -m pytest tests/ -v --cov=core --cov-report=term-missing

# ── Database ──────────────────────────────────────

migrate: ## Run Alembic migrations
	docker compose run --rm app alembic upgrade head

migrate-create: ## Create new migration (message=...)
	docker compose run --rm app alembic revision --autogenerate -m "$(message)"

seed: ## Seed database
	docker compose run --rm -e PYTHONPATH=/app app python db/seeds.py

db-shell: ## Open psql shell
	docker compose exec postgres psql -U trade -d trade

# ── Scripts ───────────────────────────────────────

test-indicators: ## Run indicator smoke test script
	docker compose run --rm -e PYTHONPATH=/app app python scripts/test_indicators.py

test-loader: ## Run data loader smoke test script
	docker compose run --rm -e PYTHONPATH=/app app python scripts/test_data_loader.py

pull-historical-data: ## Pull historical candles (DAYS=730)
	docker compose run --rm -e PYTHONPATH=/app app \
		python scripts/pull_historical_data.py --days $(or $(DAYS),730)

test-event-flow: ## Test event flow end-to-end: Python → Redis → WebSocket
	docker compose run --rm -e PYTHONPATH=/app app \
		python scripts/test_event_flow.py --ws-url ws://api:8000/ws

test-ws-reconnect: ## Test WebSocket auto-reconnection after API restart (runs on host)
	@test -d .venv-test || python3 -m venv .venv-test && .venv-test/bin/pip install -q websocket-client
	.venv-test/bin/python3 scripts/test_ws_reconnect.py

test-e2e: ## Full stack end-to-end test (runs on host, expects 'make dev' already up)
	@test -d .venv-test || python3 -m venv .venv-test && .venv-test/bin/pip install -q websocket-client
	.venv-test/bin/python3 scripts/test_e2e_stack.py

test-all: ## Run all Docker tests (unit + smoke — no stack required)
	@echo "═══ pytest (unit/integration) ═══"
	docker compose run --rm app python -m pytest tests/ -v $(TEST_ARGS)
	@echo ""
	@echo "═══ smoke: indicators ═══"
	docker compose run --rm -e PYTHONPATH=/app app python scripts/test_indicators.py
	@echo ""
	@echo "═══ smoke: data loader ═══"
	docker compose run --rm -e PYTHONPATH=/app app python scripts/test_data_loader.py
	@echo ""
	@echo "═══ smoke: event flow ═══"
	docker compose run --rm -e PYTHONPATH=/app app python scripts/test_event_flow.py --ws-url ws://api:8000/ws

test-full: test-all ## Run ALL tests including e2e (requires 'make dev' running)
	@echo ""
	@echo "═══ e2e: WebSocket reconnection ═══"
	@test -d .venv-test || python3 -m venv .venv-test && .venv-test/bin/pip install -q websocket-client
	.venv-test/bin/python3 scripts/test_ws_reconnect.py
	@echo ""
	@echo "═══ e2e: full stack ═══"
	.venv-test/bin/python3 scripts/test_e2e_stack.py

# ── Deploy to VPS ─────────────────────────────────

VPS      ?= root@76.13.172.71
VPS_PATH ?= /root/GoldenGibbon
PROD_IMAGES = \
	goldengibbon-app-prod \
	goldengibbon-api-prod \
	goldengibbon-celery-worker-prod \
	goldengibbon-celery-beat-prod \
	goldengibbon-ws-feed-prod \
	goldengibbon-frontend-prod

deploy-preflight: ## Pre-deploy checks (TS type check + pytest)
	@echo "═══ TypeScript type check ═══"
	cd frontend && npx tsc --noEmit
	@echo ""
	@echo "═══ pytest ═══"
	docker compose run --rm app python -m pytest tests/ -q
	@echo ""
	@echo "✓ Preflight passed"

deploy-build: deploy-preflight ## Preflight + build all prod images locally
	docker compose --profile prod build

deploy-ship: deploy-build ## Preflight + build + stream images to VPS
	@echo "Streaming $$(echo $(PROD_IMAGES) | wc -w) images to $(VPS)..."
	docker save $(PROD_IMAGES) | gzip | ssh $(VPS) 'gunzip | docker load'

deploy-vps: deploy-ship ## Full deploy: preflight, build, ship, migrate, restart
	@echo "Pulling main on $(VPS)..."
	ssh $(VPS) 'cd $(VPS_PATH) && git pull origin main'
	@echo "Running migrations..."
	ssh $(VPS) 'cd $(VPS_PATH) && docker compose run --rm app-prod alembic upgrade head'
	@echo "Restarting prod stack..."
	ssh $(VPS) 'cd $(VPS_PATH) && docker compose --profile prod up -d'
	@echo "Deploy complete. Check: ssh $(VPS) 'docker ps --filter name=trade-'"
