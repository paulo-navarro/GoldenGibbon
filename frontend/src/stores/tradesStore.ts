// ── Trades Store ─────────────────────────────────────────────────────────────
// Task 2.34 – Zustand store for closed trade history and aggregate stats.
// Populated via REST (initial load) and WebSocket events (live `trade_closed`
// events from the `gg:portfolio` channel).

import { create } from 'zustand';
import type { Event } from '../types/events';
import type { ExitReason } from '../types/enums';
import type { Trade, TradeStatsResponse } from '../types/trades';

// ── Types ────────────────────────────────────────────────────────────────────

interface TradesState {
  /** Closed trade history (newest first). */
  trades: Trade[];

  /** Aggregate statistics. `null` until first REST fetch. */
  stats: TradeStatsResponse | null;
}

interface TradesActions {
  // ── REST seed actions ───────────────────────────────────────────
  /** Bulk-set trades from GET /api/trades/. */
  setTrades: (data: Trade[]) => void;

  /** Set aggregate stats from GET /api/trades/stats. */
  setStats: (data: TradeStatsResponse) => void;

  // ── WebSocket event dispatcher ──────────────────────────────────
  /** Process a WebSocket event on the `gg:portfolio` channel. */
  handleEvent: (event: Event) => void;
}

type TradesStore = TradesState & TradesActions;

// ── Constants ────────────────────────────────────────────────────────────────

/** Maximum trades kept in the client-side buffer. */
const MAX_TRADES = 500;

// ── Store ────────────────────────────────────────────────────────────────────

export const useTradesStore = create<TradesStore>()((set) => ({
  // ── Initial state ───────────────────────────────────────────────
  trades: [],
  stats: null,

  // ── REST seed actions ───────────────────────────────────────────

  setTrades: (data) => {
    set({ trades: data.slice(0, MAX_TRADES) });
  },

  setStats: (data) => {
    set({ stats: data });
  },

  // ── WebSocket event dispatcher ──────────────────────────────────

  handleEvent: (event) => {
    if (event.channel !== 'gg:portfolio') return;
    if (event.event_type !== 'trade_closed') return;

    const { data } = event;

    const trade: Trade = {
      symbol: data.symbol as string,
      strategy: data.strategy as string,
      entry_price: data.entry_price as string,
      exit_price: data.exit_price as string,
      size: data.size as string,
      entry_time: data.entry_time as string,
      exit_time: data.exit_time as string,
      pnl_usdt: data.pnl_usdt as string,
      pnl_percent: data.pnl_percent as string,
      duration_minutes: data.duration_minutes as number,
      exit_reason: data.exit_reason as ExitReason,
      max_favorable_excursion: (data.max_favorable_excursion as string) ?? null,
      max_adverse_excursion: (data.max_adverse_excursion as string) ?? null,
    };

    // Prepend (newest first) and cap the buffer.
    set((s) => {
      const updated = [trade, ...s.trades];
      return {
        trades:
          updated.length > MAX_TRADES
            ? updated.slice(0, MAX_TRADES)
            : updated,
      };
    });
  },
}));
