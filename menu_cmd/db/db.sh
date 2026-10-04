# Database commands — submenu "db".

## Run Alembic migrations
db_migrate() { docker compose run --rm app alembic upgrade head; }

## Create new migration: ./menu db migrate-create "message"
db_migrate_create() {
    local message="$*"
    [[ -n "$message" ]] || { error "Usage: ./menu db migrate-create \"message\""; return 1; }
    docker compose run --rm app alembic revision --autogenerate -m "$message"
}

## Seed database
db_seed() { docker compose run --rm -e PYTHONPATH=/app app python db/seeds.py; }

## Open psql shell
db_shell() { docker compose exec postgres psql -U trade -d trade; }

## Pull historical candles (DAYS=730)
db_pull_historical_data() {
    docker compose run --rm -e PYTHONPATH=/app app \
        python scripts/pull_historical_data.py --days "${DAYS:-730}"
}
