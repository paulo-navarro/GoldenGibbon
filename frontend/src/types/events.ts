// ── WebSocket Event Types ────────────────────────────────────────────────────
// Matches: core/events.py → Event
//          api/websocket.py → control messages (welcome, ping, error)

import type { EventChannel, EventType } from './enums';

/** Standard event envelope forwarded over the WebSocket. */
export interface Event {
  event_type: EventType;
  channel: EventChannel;
  timestamp: string; // ISO 8601
  data: Record<string, unknown>;
}

/** Sent on initial WebSocket connection. */
export interface WelcomeMessage {
  type: 'welcome';
  channels: string[];
  message: string;
}

/** Server-initiated heartbeat ping. */
export interface PingMessage {
  type: 'ping';
}

/** Server-sent error notification. */
export interface ErrorMessage {
  type: 'error';
  message: string;
}

/** Discriminated union of all possible WebSocket messages. */
export type WebSocketMessage = WelcomeMessage | PingMessage | ErrorMessage | Event;
