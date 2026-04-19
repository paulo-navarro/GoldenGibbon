#!/usr/bin/env python3
"""
Task 2.54 – Test WebSocket reconnection after API restart.

Runs on the HOST (not inside Docker) because it needs to stop/start
the API container to simulate a crash.

Usage:
    python3 scripts/test_ws_reconnect.py
    make test-ws-reconnect
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import time

try:
    import websocket
except ImportError:
    print("ERROR: websocket-client required. Install with: pip install websocket-client")
    sys.exit(1)


REDIS_URL = "redis://localhost:6379/0"
WS_URL = "ws://localhost:8000/ws"
API_SERVICE = "api"


class ReconnectTester:
    def __init__(self) -> None:
        self.status: str = "disconnected"
        self.events: list[dict] = []
        self.connect_count: int = 0
        self._ws: websocket.WebSocketApp | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    def _on_message(self, ws, message):
        msg = json.loads(message)
        if msg.get("type") == "welcome":
            self.connect_count += 1
            self.status = "connected"
            return
        if msg.get("type") == "ping":
            return
        if "event_type" in msg:
            self.events.append(msg)

    def _on_error(self, ws, error):
        pass

    def _on_close(self, ws, close_status, close_msg):
        if not self._stop.is_set():
            self.status = "reconnecting"

    def _on_open(self, ws):
        pass

    def start(self):
        self._stop.clear()
        self._ws = websocket.WebSocketApp(
            WS_URL,
            on_message=self._on_message,
            on_error=self._on_error,
            on_close=self._on_close,
            on_open=self._on_open,
        )
        self._thread = threading.Thread(
            target=self._ws.run_forever,
            kwargs={"reconnect": 2},
            daemon=True,
        )
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._ws:
            self._ws.close()

    def wait_for_status(self, target: str, timeout: float = 15.0) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.status == target:
                return True
            time.sleep(0.3)
        return False

    def wait_for_event_count(self, count: int, timeout: float = 10.0) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if len(self.events) >= count:
                return True
            time.sleep(0.3)
        return False


def docker_compose(*args: str) -> int:
    result = subprocess.run(
        ["docker", "compose", *args],
        capture_output=True, text=True, timeout=30,
    )
    return result.returncode


def publish_test_event(tag: str) -> bool:
    result = subprocess.run(
        [
            "docker", "compose", "run", "--rm",
            "-e", "PYTHONPATH=/app",
            "app", "python", "-c",
            f"""
from core.events import EventPublisher, EventChannel, EventType
p = EventPublisher()
if not p.enabled:
    raise SystemExit("Redis not available")
p.publish(EventChannel.SYSTEM, EventType.HEARTBEAT, {{"tag": "{tag}"}})
p.close()
""",
        ],
        capture_output=True, text=True, timeout=30,
    )
    return result.returncode == 0


def main():
    print("=" * 60)
    print("  GoldenGibbon – WebSocket Reconnection Test (Task 2.54)")
    print("=" * 60)

    tester = ReconnectTester()

    # ── Phase 1: Initial connection ──────────────────────────────
    print("\n--- Phase 1: Connect to WebSocket ---\n")
    tester.start()

    if not tester.wait_for_status("connected"):
        print(f"FAIL: Could not connect to {WS_URL}")
        print("Is the API running? (docker compose up api)")
        tester.stop()
        sys.exit(1)

    print(f"  Connected to {WS_URL} (connect #{tester.connect_count})")

    # ── Phase 2: Verify events work before restart ───────────────
    print("\n--- Phase 2: Verify events before restart ---\n")

    if not publish_test_event("before_restart"):
        print("FAIL: Could not publish test event")
        tester.stop()
        sys.exit(1)

    if not tester.wait_for_event_count(1):
        print("FAIL: Did not receive pre-restart event")
        tester.stop()
        sys.exit(1)

    print(f"  Event received (total: {len(tester.events)})")

    # ── Phase 3: Stop the API ────────────────────────────────────
    print("\n--- Phase 3: Stop API container ---\n")

    docker_compose("stop", API_SERVICE)
    time.sleep(2)
    print("  API stopped")

    if not tester.wait_for_status("reconnecting", timeout=10):
        print(f"  Status: {tester.status} (expected 'reconnecting')")

    print(f"  Client status: {tester.status}")

    # ── Phase 4: Restart the API ─────────────────────────────────
    print("\n--- Phase 4: Restart API container ---\n")

    docker_compose("start", API_SERVICE)

    # Wait for API to be healthy
    for _ in range(20):
        r = subprocess.run(
            ["docker", "compose", "ps", API_SERVICE, "--format", "{{.Status}}"],
            capture_output=True, text=True, timeout=10,
        )
        if "healthy" in r.stdout.lower():
            break
        time.sleep(1)

    print("  API restarted and healthy")

    # ── Phase 5: Verify reconnection ─────────────────────────────
    print("\n--- Phase 5: Verify auto-reconnection ---\n")

    if not tester.wait_for_status("connected", timeout=20):
        print(f"FAIL: Client did not reconnect (status: {tester.status})")
        tester.stop()
        sys.exit(1)

    print(f"  Reconnected! (connect #{tester.connect_count})")

    # ── Phase 6: Verify events work after reconnection ───────────
    print("\n--- Phase 6: Verify events after reconnection ---\n")

    events_before = len(tester.events)

    if not publish_test_event("after_restart"):
        print("FAIL: Could not publish post-restart event")
        tester.stop()
        sys.exit(1)

    if not tester.wait_for_event_count(events_before + 1):
        print(f"FAIL: Did not receive post-restart event (got {len(tester.events) - events_before})")
        tester.stop()
        sys.exit(1)

    print(f"  Event received after reconnection (total: {len(tester.events)})")

    # ── Result ───────────────────────────────────────────────────
    tester.stop()

    print("\n" + "=" * 60)
    print(f"  PASS: WebSocket reconnected automatically")
    print(f"  Connections: {tester.connect_count} | Events received: {len(tester.events)}")
    print("=" * 60)


if __name__ == "__main__":
    main()
