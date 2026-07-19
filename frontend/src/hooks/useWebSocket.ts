// ── WebSocket Hook ───────────────────────────────────────────────────────────
// Task 2.30 – Connect to the GoldenGibbon event stream, parse messages,
// auto-reconnect with exponential backoff, and expose connection status.
//
// The hook is callback-based: callers pass `onEvent` to receive parsed
// `Event` envelopes.  Zustand stores (tasks 2.31–2.36) will wire into
// this callback to dispatch updates.

import { useCallback, useEffect, useRef, useState } from 'react';
import type { EventChannel } from '../types/enums';
import type { Event, WebSocketMessage } from '../types/events';

// ── Public Types ─────────────────────────────────────────────────────────────

/** Connection lifecycle status. */
export type ConnectionStatus =
  | 'connecting'
  | 'connected'
  | 'disconnected'
  | 'reconnecting';

/** Configuration accepted by `useWebSocket`. */
export interface UseWebSocketOptions {
  /**
   * Full WebSocket URL.  Defaults to `ws(s)://<current host>/ws` so that
   * both the Vite dev proxy and nginx production proxy work transparently.
   */
  url?: string;

  /** Subscribe to a subset of event channels (server-side filter). */
  channels?: EventChannel[];

  /** Called for every `Event` envelope received from the server. */
  onEvent?: (event: Event) => void;

  /** Called whenever the connection status changes. */
  onStatusChange?: (status: ConnectionStatus) => void;

  /** Maximum reconnect attempts.  `0` = unlimited (default). */
  maxRetries?: number;

  /** Whether the hook should connect at all.  Defaults to `true`. */
  enabled?: boolean;
}

/** Values returned by `useWebSocket`. */
export interface UseWebSocketReturn {
  /** Current connection status. */
  status: ConnectionStatus;

  /** Timestamp (ms) of the last heartbeat ping from the server. */
  lastPingAt: number | null;

  /**
   * `true` when the connection is open AND a ping was received within
   * the last 45 seconds (1.5× the server's 30 s heartbeat interval).
   */
  isHealthy: boolean;
}

// ── Constants ────────────────────────────────────────────────────────────────

/** Initial reconnect delay in milliseconds. */
const BASE_DELAY_MS = 1_000;

/** Maximum reconnect delay in milliseconds. */
const MAX_DELAY_MS = 30_000;

/** Pings older than this (ms) mark the connection as unhealthy. */
const PING_STALE_MS = 45_000;

// ── Helpers ──────────────────────────────────────────────────────────────────

/** Build the default WS URL from the current page location. */
function defaultUrl(): string {
  const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  return `${proto}//${window.location.host}/ws`;
}

/** Compute reconnect delay with exponential backoff + jitter. */
function reconnectDelay(attempt: number): number {
  const exp = Math.min(BASE_DELAY_MS * 2 ** attempt, MAX_DELAY_MS);
  // Add ±25 % jitter to prevent thundering-herd reconnects
  const jitter = exp * (0.75 + Math.random() * 0.5);
  return Math.round(jitter);
}

/**
 * Type-guard: distinguish an `Event` envelope (has `event_type` + `channel`)
 * from control messages (have `type`).
 */
function isEventEnvelope(msg: WebSocketMessage): msg is Event {
  return 'event_type' in msg && 'channel' in msg;
}

// ── Hook ─────────────────────────────────────────────────────────────────────

export function useWebSocket(options: UseWebSocketOptions = {}): UseWebSocketReturn {
  const {
    url,
    channels,
    onEvent,
    onStatusChange,
    maxRetries = 0,
    enabled = true,
  } = options;

  // ── State ────────────────────────────────────────────────────────
  const [status, setStatus] = useState<ConnectionStatus>('disconnected');
  const [lastPingAt, setLastPingAt] = useState<number | null>(null);

  // ── Refs (stable across renders, no re-render on mutation) ──────
  const wsRef = useRef<WebSocket | null>(null);
  const retriesRef = useRef(0);
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const unmountedRef = useRef(false);
  const statusRef = useRef<ConnectionStatus>('disconnected');

  // Keep callbacks in refs so the effect doesn't re-run when they change.
  const onEventRef = useRef(onEvent);
  onEventRef.current = onEvent;

  const onStatusChangeRef = useRef(onStatusChange);
  onStatusChangeRef.current = onStatusChange;

  // ── Status setter (also fires the external callback) ────────────
  // Task 9.12: no-op when the status is unchanged — repeated
  // 'reconnecting' updates during a reconnect storm must not feed
  // extra renders into the tree.
  const updateStatus = useCallback((next: ConnectionStatus) => {
    if (statusRef.current === next) return;
    statusRef.current = next;
    setStatus(next);
    onStatusChangeRef.current?.(next);
  }, []);

  // ── Derived ─────────────────────────────────────────────────────
  const isHealthy =
    status === 'connected' &&
    lastPingAt !== null &&
    Date.now() - lastPingAt < PING_STALE_MS;

  // ── Build the target URL once per logical config change ─────────
  // Task 9.12: depend on the joined string, not the array identity — an
  // inline `channels: [...]` literal would otherwise change every render,
  // re-running the connect effect (close → status change → re-render →
  // reconnect...) in an infinite loop (React #185).
  const channelsKey = channels && channels.length > 0 ? channels.join(',') : '';
  const targetUrl = useCallback(() => {
    const base = url ?? defaultUrl();
    if (channelsKey) {
      const sep = base.includes('?') ? '&' : '?';
      return `${base}${sep}channels=${channelsKey}`;
    }
    return base;
  }, [url, channelsKey]);

  // ── Core connect / reconnect logic ──────────────────────────────
  useEffect(() => {
    if (!enabled) {
      // Clean up any existing connection when disabled.
      wsRef.current?.close(1000, 'hook disabled');
      wsRef.current = null;
      updateStatus('disconnected');
      return;
    }

    unmountedRef.current = false;

    function connect() {
      if (unmountedRef.current) return;

      const wsUrl = targetUrl();
      updateStatus(retriesRef.current === 0 ? 'connecting' : 'reconnecting');

      const ws = new WebSocket(wsUrl);
      wsRef.current = ws;

      // ── onopen ───────────────────────────────────────────────
      ws.onopen = () => {
        // Status will move to 'connected' once we receive the welcome
        // message, but we can optimistically set it here in case the
        // server doesn't send one.
      };

      // ── onmessage ────────────────────────────────────────────
      ws.onmessage = (frame: MessageEvent) => {
        let msg: WebSocketMessage;
        try {
          msg = JSON.parse(frame.data as string) as WebSocketMessage;
        } catch {
          return; // ignore malformed frames
        }

        if (isEventEnvelope(msg)) {
          // Standard event envelope → forward to caller
          onEventRef.current?.(msg);
          return;
        }

        // Control messages (discriminated by `type`)
        switch (msg.type) {
          case 'welcome':
            retriesRef.current = 0; // reset backoff on successful connect
            updateStatus('connected');
            break;

          case 'ping':
            setLastPingAt(Date.now());
            break;

          case 'error':
            // Server-side error — log but stay connected
            console.warn('[WS] server error:', msg.message);
            break;
        }
      };

      // ── onclose ──────────────────────────────────────────────
      ws.onclose = () => {
        if (unmountedRef.current) return;
        wsRef.current = null;
        scheduleReconnect();
      };

      // ── onerror ──────────────────────────────────────────────
      ws.onerror = () => {
        // `onerror` is always followed by `onclose`, so reconnect
        // will be handled there.  Just log for visibility.
        console.warn('[WS] connection error');
      };
    }

    function scheduleReconnect() {
      if (unmountedRef.current) return;

      if (maxRetries > 0 && retriesRef.current >= maxRetries) {
        updateStatus('disconnected');
        return;
      }

      const delay = reconnectDelay(retriesRef.current);
      retriesRef.current += 1;
      updateStatus('reconnecting');

      reconnectTimerRef.current = setTimeout(connect, delay);
    }

    // Kick off initial connection
    connect();

    // ── Cleanup on unmount or deps change ──────────────────────
    return () => {
      unmountedRef.current = true;

      if (reconnectTimerRef.current !== null) {
        clearTimeout(reconnectTimerRef.current);
        reconnectTimerRef.current = null;
      }

      if (wsRef.current) {
        wsRef.current.onclose = null; // prevent reconnect on intentional close
        wsRef.current.close(1000, 'hook cleanup');
        wsRef.current = null;
      }

      updateStatus('disconnected');
    };
    // Re-run whenever the target URL or enabled flag changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [targetUrl, enabled, maxRetries]);

  return { status, lastPingAt, isHealthy };
}
