// ── System Store ─────────────────────────────────────────────────────────────
// Task 2.36 – Zustand store for system health, logs, and system-level events.
// Populated via REST (initial load) and WebSocket events (live updates from
// the `gg:system` channel).

import { create } from 'zustand';
import type { Event } from '../types/events';
import type { HealthResponse, LogsResponse } from '../types/system';

// ── Types ────────────────────────────────────────────────────────────────────

/** Lightweight record for recent system events displayed in the UI. */
export interface SystemEvent {
  event_type: string;
  timestamp: string;
  data: Record<string, unknown>;
}

interface SystemState {
  /** Latest health check. `null` until first REST fetch or heartbeat event. */
  health: HealthResponse | null;

  /** Log lines buffer (newest first). */
  logs: string[];

  /** Recent system-level events (startup, shutdown, error, warning). */
  systemEvents: SystemEvent[];
}

interface SystemActions {
  // ── REST seed actions ───────────────────────────────────────────
  /** Set health from GET /api/system/health. */
  setHealth: (data: HealthResponse) => void;

  /** Set logs from GET /api/system/logs. */
  setLogs: (data: LogsResponse) => void;

  /** Append a single log line (for future real-time streaming). */
  appendLog: (line: string) => void;

  // ── WebSocket event dispatcher ──────────────────────────────────
  /** Process a WebSocket event on the `gg:system` channel. */
  handleEvent: (event: Event) => void;
}

type SystemStore = SystemState & SystemActions;

// ── Constants ────────────────────────────────────────────────────────────────

/** Maximum log lines kept in the client-side buffer. */
const MAX_LOGS = 500;

/** Maximum system events kept in the buffer. */
const MAX_SYSTEM_EVENTS = 100;

// ── Store ────────────────────────────────────────────────────────────────────

export const useSystemStore = create<SystemStore>()((set, get) => ({
  // ── Initial state ───────────────────────────────────────────────
  health: null,
  logs: [],
  systemEvents: [],

  // ── REST seed actions ───────────────────────────────────────────

  setHealth: (data) => {
    set({ health: data });
  },

  setLogs: (data) => {
    // REST returns lines oldest-first; reverse so newest is first.
    set({ logs: [...data.lines].reverse().slice(0, MAX_LOGS) });
  },

  appendLog: (line) => {
    set((s) => {
      const updated = [line, ...s.logs];
      return {
        logs:
          updated.length > MAX_LOGS ? updated.slice(0, MAX_LOGS) : updated,
      };
    });
  },

  // ── WebSocket event dispatcher ──────────────────────────────────

  handleEvent: (event) => {
    if (event.channel !== 'gg:system') return;

    const { event_type, data, timestamp } = event;
    const entry: SystemEvent = { event_type, timestamp, data };

    switch (event_type) {
      case 'heartbeat': {
        // Update the health timestamp to reflect liveness.
        set((s) => ({
          health: s.health
            ? { ...s.health, timestamp }
            : { status: 'ok', db: true, redis: true, celery_worker: true, celery_beat: true, environment: '', timestamp },
        }));
        break;
      }

      case 'startup':
      case 'shutdown': {
        set((s) => {
          const updated = [entry, ...s.systemEvents];
          return {
            systemEvents:
              updated.length > MAX_SYSTEM_EVENTS
                ? updated.slice(0, MAX_SYSTEM_EVENTS)
                : updated,
          };
        });
        break;
      }

      case 'error':
      case 'warning': {
        // Store as a system event and also append a formatted log line.
        const level = event_type.toUpperCase();
        const message = (data.message as string) ?? JSON.stringify(data);
        const logLine = `[${timestamp}] ${level}: ${message}`;

        const actions = get();
        actions.appendLog(logLine);

        set((s) => {
          const updated = [entry, ...s.systemEvents];
          return {
            systemEvents:
              updated.length > MAX_SYSTEM_EVENTS
                ? updated.slice(0, MAX_SYSTEM_EVENTS)
                : updated,
          };
        });
        break;
      }

      default:
        break;
    }
  },
}));
