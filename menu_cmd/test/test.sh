# Testing commands — submenu "test". Extra arguments are passed to pytest where noted.

_venv_test() {
    [[ -d .venv-test ]] || python3 -m venv .venv-test
    .venv-test/bin/pip install -q websocket-client
}

## Run test suite (extra args go to pytest)
test_unit() { docker compose run --rm app python -m pytest tests/ -v "$@"; }

## Run tests with coverage report
test_cov() { docker compose run --rm app python -m pytest tests/ -v --cov=core --cov-report=term-missing; }

## Run indicator smoke test script
test_indicators() { docker compose run --rm -e PYTHONPATH=/app app python scripts/test_indicators.py; }

## Run data loader smoke test script
test_loader() { docker compose run --rm -e PYTHONPATH=/app app python scripts/test_data_loader.py; }

## Test event flow end-to-end: Python → Redis → WebSocket
test_event_flow() {
    docker compose run --rm -e PYTHONPATH=/app app \
        python scripts/test_event_flow.py --ws-url ws://api:8000/ws
}

## Test WebSocket auto-reconnection after API restart (runs on host)
test_ws_reconnect() {
    _venv_test
    .venv-test/bin/python3 scripts/test_ws_reconnect.py
}

## Full stack end-to-end test (runs on host, expects './menu dev' already up)
test_e2e() {
    _venv_test
    .venv-test/bin/python3 scripts/test_e2e_stack.py
}

## Run all Docker tests (unit + smoke — no stack required)
test_all() {
    echo "═══ pytest (unit/integration) ═══"
    docker compose run --rm app python -m pytest tests/ -v "$@"
    echo ""
    echo "═══ smoke: indicators ═══"
    docker compose run --rm -e PYTHONPATH=/app app python scripts/test_indicators.py
    echo ""
    echo "═══ smoke: data loader ═══"
    docker compose run --rm -e PYTHONPATH=/app app python scripts/test_data_loader.py
    echo ""
    echo "═══ smoke: event flow ═══"
    docker compose run --rm -e PYTHONPATH=/app app python scripts/test_event_flow.py --ws-url ws://api:8000/ws
}

## Run ALL tests including e2e (requires './menu dev' running)
test_full() {
    test_all "$@"
    echo ""
    echo "═══ e2e: WebSocket reconnection ═══"
    _venv_test
    .venv-test/bin/python3 scripts/test_ws_reconnect.py
    echo ""
    echo "═══ e2e: full stack ═══"
    .venv-test/bin/python3 scripts/test_e2e_stack.py
}
