#!/usr/bin/env python3
"""
Task 2.56 – End-to-end full stack test.

Verifies that all services are healthy and the complete event pipeline
works: Python → Redis → WebSocket API → client.

Runs on the HOST (needs Docker access + websocket-client).

Usage:
    python3 scripts/test_e2e_stack.py
    make test-e2e
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
import urllib.request
import urllib.error

try:
    import websocket
except ImportError:
    print("ERROR: websocket-client required. Install with: pip install websocket-client")
    sys.exit(1)

API_PORT = 8000
FRONTEND_PORT = 5858
WS_URL = f"ws://localhost:{API_PORT}/ws"

EXPECTED_SERVICES = [
    "trade-postgres",
    "trade-redis",
    "trade-api",
    "trade-frontend",
    "trade-celery-worker",
    "trade-celery-beat",
    "trade-ws-feed",
]

results: list[tuple[str, bool, str]] = []


def record(phase: str, passed: bool, detail: str = ""):
    status = "PASS" if passed else "FAIL"
    results.append((phase, passed, detail))
    print(f"  [{status}] {phase}" + (f" — {detail}" if detail else ""))


def run_cmd(*args: str, timeout: int = 30) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout)


# ── Phase 1: Service health ─────────────────────────────────────────────────


def check_services():
    print("\n--- Phase 1: Service health checks ---\n")

    r = run_cmd("docker", "compose", "--profile", "dev", "ps", "--format", "{{.Name}}\t{{.Status}}")
    if r.returncode != 0:
        record("docker-compose", False, r.stderr.strip())
        return

    running = {}
    for line in r.stdout.strip().splitlines():
        parts = line.split("\t")
        if len(parts) == 2:
            running[parts[0]] = parts[1]

    for svc in EXPECTED_SERVICES:
        status = running.get(svc, "NOT FOUND")
        healthy = "healthy" in status.lower() or ("up" in status.lower() and "unhealthy" not in status.lower())
        record(f"service:{svc}", healthy, status)


# ── Phase 2: API REST ───────────────────────────────────────────────────────


def check_api_rest():
    print("\n--- Phase 2: API REST endpoints ---\n")

    endpoints = [
        ("/api/system/health", "health"),
    ]

    for path, name in endpoints:
        url = f"http://localhost:{API_PORT}{path}"
        try:
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=5) as resp:
                record(f"api:{name}", resp.status == 200, f"HTTP {resp.status}")
        except Exception as exc:
            record(f"api:{name}", False, str(exc))


# ── Phase 3: WebSocket ──────────────────────────────────────────────────────


def check_websocket():
    print("\n--- Phase 3: WebSocket connection ---\n")

    welcome_received = threading.Event()
    events_received: list[dict] = []
    event_ready = threading.Event()

    def on_message(ws, message):
        msg = json.loads(message)
        if msg.get("type") == "welcome":
            welcome_received.set()
        elif msg.get("type") == "ping":
            pass
        elif "event_type" in msg and msg.get("data", {}).get("source") == "test_e2e":
            events_received.append(msg)
            event_ready.set()

    def on_error(ws, error):
        pass

    ws = websocket.WebSocketApp(WS_URL, on_message=on_message, on_error=on_error)
    t = threading.Thread(target=ws.run_forever, daemon=True)
    t.start()

    connected = welcome_received.wait(timeout=5)
    record("ws:connect", connected, "welcome received" if connected else "timeout")

    if not connected:
        ws.close()
        return

    # Publish a test event via Docker
    r = run_cmd(
        "docker", "compose", "run", "--rm", "-e", "PYTHONPATH=/app", "app",
        "python", "-c",
        'from core.events import EventPublisher, EventChannel, EventType; '
        'p = EventPublisher(); '
        'p.publish(EventChannel.SYSTEM, EventType.HEARTBEAT, {"source": "test_e2e"}); '
        'p.close()',
    )

    if r.returncode != 0:
        record("ws:publish", False, "could not publish test event")
        ws.close()
        return

    got_event = event_ready.wait(timeout=10)
    record("ws:event_flow", got_event, f"received {len(events_received)} event(s)")
    ws.close()


# ── Phase 4: Frontend ───────────────────────────────────────────────────────


def check_frontend():
    print("\n--- Phase 4: Frontend ---\n")

    url = f"http://localhost:{FRONTEND_PORT}/"
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=5) as resp:
            body = resp.read().decode()
            has_root = "root" in body or "app" in body.lower()
            record("frontend:http", resp.status == 200, f"HTTP {resp.status}")
            record("frontend:html", has_root, "app root found" if has_root else "unexpected content")
    except Exception as exc:
        record("frontend:http", False, str(exc))


# ── Phase 5: Celery ─────────────────────────────────────────────────────────


def check_celery():
    print("\n--- Phase 5: Celery worker ---\n")

    r = run_cmd(
        "docker", "compose", "--profile", "dev", "exec", "celery-worker",
        "celery", "-A", "core.celery_app", "inspect", "ping", "--timeout", "5",
        timeout=15,
    )
    alive = r.returncode == 0 and "pong" in r.stdout.lower()
    record("celery:ping", alive, "worker responded" if alive else (r.stderr.strip() or r.stdout.strip())[:100])


# ── Main ─────────────────────────────────────────────────────────────────────


def main():
    print("=" * 60)
    print("  GoldenGibbon – Full Stack E2E Test (Task 2.56)")
    print("=" * 60)

    check_services()
    check_api_rest()
    check_websocket()
    check_frontend()
    check_celery()

    # Summary
    total = len(results)
    passed = sum(1 for _, ok, _ in results if ok)
    failed = total - passed

    print("\n" + "=" * 60)
    print(f"  Results: {passed}/{total} passed", end="")
    if failed:
        print(f" ({failed} failed)")
        print("\n  Failed checks:")
        for phase, ok, detail in results:
            if not ok:
                print(f"    - {phase}: {detail}")
    else:
        print()

    print("=" * 60)
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
