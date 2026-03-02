// ── Portfolio Store ──────────────────────────────────────────────────────────
// Task 2.33 – Zustand store for real-time portfolio data: balance, open
// positions, and equity curve.  Populated via REST (initial load) and
// WebSocket events (live updates from the `gg:portfolio` channel).

import { create } from 'zustand';
import type { Event } from '../types/events';
import type {
  PortfolioResponse,
  PortfolioSnapshot,
  Position,
} from '../types/portfolio';

// ── Types ────────────────────────────────────────────────────────────────────

interface PortfolioState {
  /** Current portfolio (balance + open positions). `null` until first REST fetch. */
  balance: PortfolioResponse | null;

  /** Historical equity snapshots for charting. */
  equityCurve: PortfolioSnapshot[];
}

interface PortfolioActions {
  // ── REST seed actions ───────────────────────────────────────────
  /** Set full portfolio from GET /api/portfolio/. */
  setPortfolio: (data: PortfolioResponse) => void;

  /** Set equity curve from GET /api/portfolio/equity-curve. */
  setEquityCurve: (data: PortfolioSnapshot[]) => void;

  // ── WebSocket event dispatcher ──────────────────────────────────
  /** Process a WebSocket event on the `gg:portfolio` channel. */
  handleEvent: (event: Event) => void;
}

type PortfolioStore = PortfolioState & PortfolioActions;

// ── Constants ────────────────────────────────────────────────────────────────

/** Maximum equity curve points kept in memory. */
const MAX_EQUITY_POINTS = 1_000;

// ── Helpers ──────────────────────────────────────────────────────────────────

/** Upsert a position by symbol in the positions array. */
function upsertPosition(positions: Position[], incoming: Position): Position[] {
  const idx = positions.findIndex((p) => p.symbol === incoming.symbol);
  if (idx === -1) return [...positions, incoming];
  const updated = [...positions];
  updated[idx] = incoming;
  return updated;
}

/** Remove a position by symbol from the positions array. */
function removePosition(positions: Position[], symbol: string): Position[] {
  return positions.filter((p) => p.symbol !== symbol);
}

// ── Store ────────────────────────────────────────────────────────────────────

export const usePortfolioStore = create<PortfolioStore>()((set) => ({
  // ── Initial state ───────────────────────────────────────────────
  balance: null,
  equityCurve: [],

  // ── REST seed actions ───────────────────────────────────────────

  setPortfolio: (data) => {
    set({ balance: data });
  },

  setEquityCurve: (data) => {
    set({ equityCurve: data.slice(-MAX_EQUITY_POINTS) });
  },

  // ── WebSocket event dispatcher ──────────────────────────────────

  handleEvent: (event) => {
    if (event.channel !== 'gg:portfolio') return;

    const { event_type, data } = event;

    switch (event_type) {
      case 'position_opened': {
        const position: Position = {
          symbol: data.symbol as string,
          size: data.size as string,
          entry_price: data.entry_price as string,
          entry_time: data.entry_time as string,
          highest_close: data.highest_close as string,
          trailing_stop_price: data.trailing_stop_price as string,
          hard_stop_price: data.hard_stop_price as string,
          scale_in_count: data.scale_in_count as number,
          buy_signal_candles: data.buy_signal_candles as number,
          unrealized_pnl: (data.unrealized_pnl as string) ?? null,
          unrealized_pnl_percent: (data.unrealized_pnl_percent as string) ?? null,
        };
        set((s) => {
          if (!s.balance) return s;
          const positions = upsertPosition(s.balance.positions, position);
          return {
            balance: {
              ...s.balance,
              positions,
              open_positions_count: positions.length,
            },
          };
        });
        break;
      }

      case 'position_updated': {
        const position: Position = {
          symbol: data.symbol as string,
          size: data.size as string,
          entry_price: data.entry_price as string,
          entry_time: data.entry_time as string,
          highest_close: data.highest_close as string,
          trailing_stop_price: data.trailing_stop_price as string,
          hard_stop_price: data.hard_stop_price as string,
          scale_in_count: data.scale_in_count as number,
          buy_signal_candles: data.buy_signal_candles as number,
          unrealized_pnl: (data.unrealized_pnl as string) ?? null,
          unrealized_pnl_percent: (data.unrealized_pnl_percent as string) ?? null,
        };
        set((s) => {
          if (!s.balance) return s;
          return {
            balance: {
              ...s.balance,
              positions: upsertPosition(s.balance.positions, position),
            },
          };
        });
        break;
      }

      case 'trade_closed': {
        // Remove the closed position from the portfolio.
        // The trade itself is archived by tradesStore.
        const symbol = data.symbol as string;
        set((s) => {
          if (!s.balance) return s;
          const positions = removePosition(s.balance.positions, symbol);
          return {
            balance: {
              ...s.balance,
              positions,
              open_positions_count: positions.length,
            },
          };
        });
        break;
      }

      case 'balance_updated': {
        set((s) => {
          if (!s.balance) return s;
          return {
            balance: {
              ...s.balance,
              usdt_balance: data.usdt_balance as string,
              total_pnl: data.total_pnl as string,
              open_positions_count:
                (data.open_trades_count as number) ?? s.balance.open_positions_count,
            },
          };
        });
        break;
      }

      case 'equity_updated': {
        const snapshot: PortfolioSnapshot = {
          timestamp: data.timestamp as string,
          usdt_balance: data.usdt_balance as string,
          positions_value: data.positions_value as string,
          total_equity: data.total_equity as string,
          daily_pnl: (data.daily_pnl as string) ?? null,
          total_pnl: data.total_pnl as string,
          open_positions_count: data.open_positions_count as number,
        };

        set((s) => {
          if (!s.balance) return s;
          const curve = [...s.equityCurve, snapshot];
          return {
            balance: {
              ...s.balance,
              equity: snapshot.total_equity,
              positions_value: snapshot.positions_value,
            },
            equityCurve:
              curve.length > MAX_EQUITY_POINTS
                ? curve.slice(curve.length - MAX_EQUITY_POINTS)
                : curve,
          };
        });
        break;
      }

      default:
        break;
    }
  },
}));
