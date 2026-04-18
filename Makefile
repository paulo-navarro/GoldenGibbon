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

deploy-build: ## Build all prod images locally
	docker compose --profile prod build

deploy-ship: deploy-build ## Build + stream images to VPS
	@echo "Streaming $$(echo $(PROD_IMAGES) | wc -w) images to $(VPS)..."
	docker save $(PROD_IMAGES) | gzip | ssh $(VPS) 'gunzip | docker load'

deploy-vps: deploy-ship ## Build, ship, restart prod stack on VPS
	@echo "Pulling main + restarting prod stack on $(VPS)..."
	ssh $(VPS) 'cd $(VPS_PATH) && git pull origin main && docker compose --profile prod up -d'
	@echo "Deploy complete. Check: ssh $(VPS) 'docker ps --filter name=trade-'"
