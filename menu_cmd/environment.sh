# Project commands (top-level). Each function with a `## description` comment directly above it
# becomes a menu command; functions without it are helpers and are ignored.
# (info/warn/error and the color variables come from ./menu.)

## Start dev environment (all services)
dev() { docker compose --profile dev up --build -d; }

## Start API + infra only
api() { docker compose --profile dev up --build -d api postgres redis; }

## Start frontend + API + infra
frontend() { docker compose --profile dev up --build -d frontend api postgres redis; }

## Start Celery workers + infra
celery() { docker compose --profile dev up --build -d celery-worker celery-beat postgres redis; }

## Start WebSocket feed + infra
ws_feed() { docker compose --profile dev up --build -d ws-feed postgres redis; }

## Start production stack
prod() { docker compose --profile prod up --build -d; }

## Stop all containers
down() { docker compose --profile dev --profile prod down; }

## Tail all container logs
logs() { docker compose --profile dev --profile prod logs -f; }

## Show container status
ps() { docker compose --profile dev --profile prod ps; }

## Build all images (dev + prod)
build() {
    docker compose --profile dev build
    docker compose --profile prod build
}

## Tail Celery worker/beat logs
celery_logs() { docker compose --profile dev logs -f celery-worker celery-beat; }

## Tail WebSocket feed logs
ws_feed_logs() { docker compose --profile dev logs -f ws-feed; }
