// ── Market Store ─────────────────────────────────────────────────────────────
// Task 2.31 – Zustand store for real-time market data: candles, indicators,
// and latest prices.  Populated via REST (initial load) and WebSocket events
// (live updates from the `gg:market_data` channel).

import { create } from 'zustand';
import type { Event } from '../types/events';
import type { Candle, PriceResponse } from '../types/market';

// ── Types ────────────────────────────────────────────────────────────────────

/** Composite key helpers. */
function priceKey(symbol: string): string {
  return symbol;
}

function candleKey(symbol: string, timeframe: string): string {
  return `${symbol}:${timeframe}`;
}

interface MarketState {
  /** Latest price per symbol. Keyed by symbol (e.g. "BTCUSDT"). */
  latestPrice: Record<string, PriceResponse>;

  /** Candle arrays keyed by "symbol:timeframe" (e.g. "BTCUSDT:15m"). */
  candles: Record<string, Candle[]>;

  /** Indicator snapshots keyed by "symbol:timeframe". */
  indicators: Record<string, Record<string, unknown>>;
}

interface MarketActions {
  // ── REST seed actions ───────────────────────────────────────────
  /** Bulk-set candles from a REST fetch. */
  setCandles: (symbol: string, timeframe: string, candles: Candle[]) => void;

  /** Set the latest price from a REST fetch. */
  setPrice: (symbol: string, price: PriceResponse) => void;

  /** Set indicator data from a REST fetch. */
  setIndicators: (
    symbol: string,
    timeframe: string,
    data: Record<string, unknown>,
  ) => void;

  // ── Live update actions ─────────────────────────────────────────
  /** Append a candle received over WebSocket (capped buffer). */
  appendCandle: (symbol: string, timeframe: string, candle: Candle) => void;

  /** Process a WebSocket event on the `gg:market_data` channel. */
  handleEvent: (event: Event) => void;
}

type MarketStore = MarketState & MarketActions;

// ── Constants ────────────────────────────────────────────────────────────────

/** Maximum candles kept per symbol:timeframe key. */
const MAX_CANDLES = 500;

// ── Store ────────────────────────────────────────────────────────────────────

export const useMarketStore = create<MarketStore>()((set, get) => ({
  // ── Initial state ───────────────────────────────────────────────
  latestPrice: {},
  candles: {},
  indicators: {},

  // ── REST seed actions ───────────────────────────────────────────

  setCandles: (symbol, timeframe, candles) => {
    const key = candleKey(symbol, timeframe);
    set((s) => ({
      candles: { ...s.candles, [key]: candles.slice(-MAX_CANDLES) },
    }));
  },

  setPrice: (symbol, price) => {
    set((s) => ({
      latestPrice: { ...s.latestPrice, [priceKey(symbol)]: price },
    }));
  },

  setIndicators: (symbol, timeframe, data) => {
    const key = candleKey(symbol, timeframe);
    set((s) => ({
      indicators: { ...s.indicators, [key]: data },
    }));
  },

  // ── Live update actions ─────────────────────────────────────────

  appendCandle: (symbol, timeframe, candle) => {
    const key = candleKey(symbol, timeframe);
    set((s) => {
      const existing = s.candles[key] ?? [];
      const updated = [...existing, candle];
      // Cap at MAX_CANDLES to prevent unbounded growth
      return {
        candles: {
          ...s.candles,
          [key]: updated.length > MAX_CANDLES
            ? updated.slice(updated.length - MAX_CANDLES)
            : updated,
        },
      };
    });
  },

  // ── WebSocket event dispatcher ──────────────────────────────────

  handleEvent: (event) => {
    if (event.channel !== 'gg:market_data') return;

    const { event_type, data } = event;
    const actions = get();

    switch (event_type) {
      case 'candle_received': {
        const symbol = data.symbol as string;
        const timeframe = data.timeframe as string;
        const candle: Candle = {
          open_time: data.open_time as string,
          open: data.open as string,
          high: data.high as string,
          low: data.low as string,
          close: data.close as string,
          volume: data.volume as string,
          quote_volume: (data.quote_volume as string) ?? null,
          trades_count: (data.trades_count as number) ?? null,
        };
        actions.appendCandle(symbol, timeframe, candle);
        break;
      }

      case 'price_update': {
        const symbol = data.symbol as string;
        const price: PriceResponse = {
          symbol,
          price: data.price as string,
          open_time: data.open_time as string ?? event.timestamp,
          timeframe: data.timeframe as string ?? '',
        };
        actions.setPrice(symbol, price);
        break;
      }

      case 'indicators_computed': {
        const symbol = data.symbol as string;
        const timeframe = data.timeframe as string;
        // eslint-disable-next-line @typescript-eslint/no-unused-vars
        const { symbol: _s, timeframe: _t, ...indicatorData } = data;
        actions.setIndicators(symbol, timeframe, indicatorData);
        break;
      }

      default:
        break;
    }
  },
}));
