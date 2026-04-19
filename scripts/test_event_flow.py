#!/usr/bin/env python3
"""
Task 2.53 – Test event flow: publish events from Python, verify via WebSocket.

Usage:
    python scripts/test_event_flow.py [--redis-url REDIS_URL] [--ws-url WS_URL]

Publishes one test event per channel (market_data, strategy, execution,
portfolio, system) and optionally connects a WebSocket client to verify
they arrive on the other side.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.events import EventChannel, EventPublisher, EventType


def _test_events() -> list[tuple[EventChannel, EventType, dict]]:
    return [
        (
            EventChannel.MARKET_DATA,
            EventType.PRICE_UPDATE,
            {"symbol": "BTCUSDT", "price": "65432.10", "source": "test_event_flow"},
        ),
        (
            EventChannel.STRATEGY,
            EventType.SIGNAL_GENERATED,
            {
                "symbol": "BTCUSDT",
                "strategy": "smart_hodler",
                "signal": "BUY",
                "source": "test_event_flow",
            },
        ),
        (
            EventChannel.EXECUTION,
            EventType.ORDER_FILLED,
            {
                "symbol": "BTCUSDT",
                "side": "BUY",
                "size": "0.01",
                "price": "65432.10",
                "order_id": "TEST-001",
                "source": "test_event_flow",
            },
        ),
        (
            EventChannel.PORTFOLIO,
            EventType.BALANCE_UPDATED,
            {
                "balance": "10150.00",
                "currency": "USDT",
                "source": "test_event_flow",
            },
        ),
        (
            EventChannel.SYSTEM,
            EventType.HEARTBEAT,
            {"status": "healthy", "source": "test_event_flow"},
        ),
    ]


def publish_events(redis_url: str) -> bool:
    publisher = EventPublisher(redis_url=redis_url)

    if not publisher.enabled:
        print("FAIL: Could not connect to Redis at", redis_url)
        return False

    print(f"Connected to Redis: {redis_url}\n")

    events = _test_events()
    for channel, event_type, data in events:
        publisher.publish(channel, event_type, data)
        print(f"  Published: {channel.value} / {event_type.value}")
        time.sleep(0.2)

    publisher.close()
    return True


def verify_websocket(ws_url: str, timeout: float = 10.0) -> bool:
    try:
        import websocket
    except ImportError:
        print("\nSkipping WebSocket verification (pip install websocket-client)")
        return True

    received: list[dict] = []
    connected = threading.Event()
    done = threading.Event()

    def on_message(ws, message):
        msg = json.loads(message)
        if msg.get("type") == "welcome":
            connected.set()
            return
        if msg.get("type") == "ping":
            return
        if "event_type" in msg and msg.get("data", {}).get("source") == "test_event_flow":
            received.append(msg)
            print(f"  Received: {msg['channel']} / {msg['event_type']}")
            if len(received) >= 5:
                done.set()

    def on_error(ws, error):
        print(f"  WS error: {error}")

    def on_open(ws):
        pass

    ws = websocket.WebSocketApp(
        ws_url,
        on_message=on_message,
        on_error=on_error,
        on_open=on_open,
    )

    ws_thread = threading.Thread(target=ws.run_forever, daemon=True)
    ws_thread.start()

    if not connected.wait(timeout=5):
        print("FAIL: WebSocket did not connect")
        ws.close()
        return False

    print(f"\nWebSocket connected to {ws_url}")
    print("Waiting for events...\n")

    done.wait(timeout=timeout)
    ws.close()

    if len(received) >= 5:
        print(f"\nPASS: Received all {len(received)} test events via WebSocket")
        return True
    else:
        print(f"\nPARTIAL: Received {len(received)}/5 events (some may have arrived before WS connected)")
        return len(received) > 0


def main():
    parser = argparse.ArgumentParser(description="Test event flow: Python → Redis → WebSocket")
    parser.add_argument("--redis-url", default=os.getenv("REDIS_URL", "redis://localhost:6379/0"))
    parser.add_argument("--ws-url", default=os.getenv("WS_URL", "ws://localhost:8000/ws"))
    parser.add_argument("--no-ws", action="store_true", help="Skip WebSocket verification")
    args = parser.parse_args()

    print("=" * 60)
    print("  GoldenGibbon – Event Flow Test (Task 2.53)")
    print("=" * 60)

    if args.no_ws:
        print("\n--- Publishing events to Redis ---\n")
        ok = publish_events(args.redis_url)
    else:
        print("\n--- Phase 1: Connect WebSocket listener ---\n")

        try:
            import websocket  # noqa: F401
        except ImportError:
            print("websocket-client not installed, running publish-only mode")
            print("\n--- Publishing events to Redis ---\n")
            ok = publish_events(args.redis_url)
            print("\nTo verify end-to-end: open the dashboard in a browser,")
            print("then re-run this script. Events should appear in real-time.")
            sys.exit(0 if ok else 1)

        received: list[dict] = []
        connected = threading.Event()
        done = threading.Event()

        def on_message(ws_app, message):
            msg = json.loads(message)
            if msg.get("type") == "welcome":
                connected.set()
                return
            if msg.get("type") == "ping":
                return
            if "event_type" in msg and msg.get("data", {}).get("source") == "test_event_flow":
                received.append(msg)
                print(f"  ← Received: {msg['channel']} / {msg['event_type']}")
                if len(received) >= 5:
                    done.set()

        def on_error(ws_app, error):
            print(f"  WS error: {error}")

        ws = websocket.WebSocketApp(args.ws_url, on_message=on_message, on_error=on_error)
        ws_thread = threading.Thread(target=ws.run_forever, daemon=True)
        ws_thread.start()

        if not connected.wait(timeout=5):
            print(f"FAIL: Could not connect WebSocket to {args.ws_url}")
            print("Is the API server running? (docker-compose up api)")
            sys.exit(1)

        print(f"WebSocket connected to {args.ws_url}")

        print("\n--- Phase 2: Publish events to Redis ---\n")
        ok = publish_events(args.redis_url)
        if not ok:
            ws.close()
            sys.exit(1)

        print("\n--- Phase 3: Verify events received via WebSocket ---\n")
        done.wait(timeout=5)
        ws.close()

        if len(received) >= 5:
            print(f"PASS: All {len(received)}/5 events received end-to-end")
        elif len(received) > 0:
            print(f"PARTIAL: {len(received)}/5 events received")
            ok = False
        else:
            print("FAIL: No events received via WebSocket")
            ok = False

    print("\n" + "=" * 60)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
