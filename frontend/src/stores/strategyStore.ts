// ── Strategy Store ───────────────────────────────────────────────────────────
// Task 2.32 – Zustand store for real-time strategy data: state-machine status,
// signal snapshots, and conditions checklists.  Populated via REST (initial
// load) and WebSocket events (live updates from the `gg:strategy` channel).

import { create } from 'zustand';
import type { Event } from '../types/events';
import type { Signal, StrategyState } from '../types/enums';
import type {
  MeanReversionConditions,
  StrategyConditions,
  StrategySignalSnapshot,
  StrategyStateResponse,
} from '../types/strategy';

// ── Types ────────────────────────────────────────────────────────────────────

/** Composite key: "BTCUSDT:smart_hodler" */
function storeKey(symbol: string, strategy: string): string {
  return `${symbol}:${strategy}`;
}

export interface RegimeInfo {
  regime: 'trending' | 'ranging' | 'uncertain';
  confidence: number;
  adx_value: number;
  gate_blocked: boolean;
}

export interface SentinelStatus {
  symbol: string;
  bearish: boolean;
  close: number;
  ema_50: number;
  adx: number;
}

export interface MacroFilterInfo {
  longs_blocked: boolean;
  reason: string;
  sentinels: SentinelStatus[];
}

interface StrategyStoreState {
  /** State-machine records keyed by "symbol:strategy". */
  states: Record<string, StrategyStateResponse>;

  /** Signal snapshots keyed by "symbol:strategy". */
  signals: Record<string, StrategySignalSnapshot>;

  /** Conditions checklists keyed by "symbol:strategy". */
  conditions: Record<string, StrategyConditions | MeanReversionConditions>;

  /** Latest detected regime keyed by "symbol:strategy". */
  regimes: Record<string, RegimeInfo>;

  /** Global macro filter state (BTC + ETH sentiment). */
  macroFilter: MacroFilterInfo | null;
}

interface StrategyStoreActions {
  // ── REST seed actions ───────────────────────────────────────────
  /** Bulk-set state records from GET /api/strategy/state. */
  setStates: (states: StrategyStateResponse[]) => void;

  /** Bulk-set signal snapshots from GET /api/strategy/signals. */
  setSignals: (signals: StrategySignalSnapshot[]) => void;

  // ── Live update actions ─────────────────────────────────────────
  /** Process a WebSocket event on the `gg:strategy` channel. */
  handleEvent: (event: Event) => void;
}

type StrategyStore = StrategyStoreState & StrategyStoreActions;

// ── Store ────────────────────────────────────────────────────────────────────

export const useStrategyStore = create<StrategyStore>()((set) => ({
  // ── Initial state ───────────────────────────────────────────────
  states: {},
  signals: {},
  conditions: {},
  regimes: {},
  macroFilter: null,

  // ── REST seed actions ───────────────────────────────────────────

  setStates: (states) => {
    const map: Record<string, StrategyStateResponse> = {};
    for (const s of states) {
      map[storeKey(s.symbol, s.strategy)] = s;
    }
    set({ states: map });
  },

  setSignals: (signals) => {
    const map: Record<string, StrategySignalSnapshot> = {};
    for (const s of signals) {
      map[storeKey(s.symbol, s.strategy)] = s;
    }
    set({ signals: map });
  },

  // ── WebSocket event dispatcher ──────────────────────────────────

  handleEvent: (event) => {
    if (event.channel !== 'gg:strategy') return;

    const { event_type, data } = event;

    if (event_type === 'macro_filter_updated') {
      set({
        macroFilter: {
          longs_blocked: data.longs_blocked as boolean,
          reason: data.reason as string,
          sentinels: data.sentinels as SentinelStatus[],
        },
      });
      return;
    }

    const symbol = data.symbol as string;
    const strategy = data.strategy as string;
    const key = storeKey(symbol, strategy);

    switch (event_type) {
      case 'signal_generated': {
        set((s) => ({
          signals: {
            ...s.signals,
            [key]: {
              // Preserve existing snapshot fields, overlay with new data
              ...s.signals[key],
              symbol,
              strategy,
              state: (data.state as StrategyState) ?? s.signals[key]?.state ?? 'flat',
              signal: data.signal as Signal,
              conditions: (data.conditions as Record<string, unknown>) ?? s.signals[key]?.conditions ?? null,
              updated_at: event.timestamp,
            },
          },
        }));
        break;
      }

      case 'state_changed': {
        const newState = data.new_state as StrategyState;
        set((s) => {
          const existing = s.states[key];
          if (!existing) {
            // We don't have the full record yet — store a partial one.
            // REST fetch will fill in missing fields.
            return {
              states: {
                ...s.states,
                [key]: {
                  symbol,
                  strategy,
                  state: newState,
                  cooldown_until: null,
                  consecutive_buy_candles: 0,
                  last_exit_time: null,
                  state_data: null,
                  created_at: event.timestamp,
                  updated_at: event.timestamp,
                },
              },
            };
          }
          return {
            states: {
              ...s.states,
              [key]: { ...existing, state: newState, updated_at: event.timestamp },
            },
          };
        });

        // Also update the state field in the signal snapshot if it exists
        set((s) => {
          const sig = s.signals[key];
          if (!sig) return s;
          return {
            signals: {
              ...s.signals,
              [key]: { ...sig, state: newState, updated_at: event.timestamp },
            },
          };
        });
        break;
      }

      case 'conditions_evaluated': {
        // The event data is the flattened conditions object (+ symbol/strategy).
        // eslint-disable-next-line @typescript-eslint/no-unused-vars
        const { symbol: _s, strategy: _st, ...conditionFields } = data;

        set((s) => ({
          conditions: {
            ...s.conditions,
            [key]: conditionFields as unknown as StrategyConditions | MeanReversionConditions,
          },
        }));

        // Also stash in the signal snapshot's conditions field
        set((s) => {
          const sig = s.signals[key];
          if (!sig) return s;
          return {
            signals: {
              ...s.signals,
              [key]: { ...sig, conditions: conditionFields, updated_at: event.timestamp },
            },
          };
        });
        break;
      }

      case 'regime_detected': {
        set((s) => ({
          regimes: {
            ...s.regimes,
            [key]: {
              regime: data.regime as RegimeInfo['regime'],
              confidence: data.confidence as number,
              adx_value: data.adx_value as number,
              gate_blocked: data.gate_blocked as boolean,
            },
          },
        }));
        break;
      }

      default:
        break;
    }
  },
}));
