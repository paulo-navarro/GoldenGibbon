// ── React Query Hooks ────────────────────────────────────────────────────────
// Task 2.37 – One hook per REST endpoint.  Each hook fetches data, seeds the
// corresponding Zustand store on success, and exposes standard React Query
// loading / error / data states.
//
// Pages can read directly from `data` (React Query cache) or from the Zustand
// store (kept in sync via seed + WebSocket events).

import { useEffect } from 'react';
import { useQuery } from '@tanstack/react-query';
import { fetchApi } from './client';

import type { Candle, PriceResponse } from '../types/market';
import type { PortfolioResponse, PortfolioSnapshot } from '../types/portfolio';
import type { Trade, TradeStatsResponse } from '../types/trades';
import type { Order } from '../types/orders';
import type {
  StrategyStateResponse,
  StrategySignalSnapshot,
} from '../types/strategy';
import type { HealthResponse, LogsResponse } from '../types/system';

import { useMarketStore } from '../stores/marketStore';
import { usePortfolioStore } from '../stores/portfolioStore';
import { useTradesStore } from '../stores/tradesStore';
import { useOrdersStore } from '../stores/ordersStore';
import { useStrategyStore } from '../stores/strategyStore';
import { useSystemStore } from '../stores/systemStore';

// ── Shared param types ───────────────────────────────────────────────────────

interface TimeRangeParams {
  start?: string;
  end?: string;
}

interface RunFilterParams extends TimeRangeParams {
  run_id?: string;
}

// ── Market ───────────────────────────────────────────────────────────────────

export interface UseCandlesParams extends TimeRangeParams {
  timeframe?: string;
  limit?: number;
}

export function useCandles(symbol: string, params: UseCandlesParams = {}) {
  const { timeframe = '15m', limit, start, end } = params;

  const query = useQuery({
    queryKey: ['candles', symbol, timeframe, limit, start, end],
    queryFn: () =>
      fetchApi<Candle[]>(`/api/market/candles/${symbol}`, {
        timeframe,
        limit,
        start,
        end,
      }),
    enabled: !!symbol,
  });

  useEffect(() => {
    if (query.data) {
      useMarketStore.getState().setCandles(symbol, timeframe, query.data);
    }
  }, [query.data, symbol, timeframe]);

  return query;
}

export interface UsePriceParams {
  timeframe?: string;
}

export function usePrice(symbol: string, params: UsePriceParams = {}) {
  const { timeframe = '15m' } = params;

  const query = useQuery({
    queryKey: ['price', symbol, timeframe],
    queryFn: () =>
      fetchApi<PriceResponse>(`/api/market/price/${symbol}`, { timeframe }),
    enabled: !!symbol,
  });

  useEffect(() => {
    if (query.data) {
      useMarketStore.getState().setPrice(symbol, query.data);
    }
  }, [query.data, symbol]);

  return query;
}

// ── Portfolio ────────────────────────────────────────────────────────────────

export function usePortfolio() {
  const query = useQuery({
    queryKey: ['portfolio'],
    queryFn: () => fetchApi<PortfolioResponse>('/api/portfolio/'),
  });

  useEffect(() => {
    if (query.data) {
      usePortfolioStore.getState().setPortfolio(query.data);
    }
  }, [query.data]);

  return query;
}

export interface UseEquityCurveParams extends RunFilterParams {
  limit?: number;
}

export function useEquityCurve(params: UseEquityCurveParams = {}) {
  const { run_id, limit, start, end } = params;

  const query = useQuery({
    queryKey: ['equity-curve', run_id, limit, start, end],
    queryFn: () =>
      fetchApi<PortfolioSnapshot[]>('/api/portfolio/equity-curve', {
        run_id,
        limit,
        start,
        end,
      }),
  });

  useEffect(() => {
    if (query.data) {
      usePortfolioStore.getState().setEquityCurve(query.data);
    }
  }, [query.data]);

  return query;
}

// ── Trades ───────────────────────────────────────────────────────────────────

export interface UseTradesParams extends RunFilterParams {
  symbol?: string;
  strategy?: string;
  exit_reason?: string;
  limit?: number;
}

export function useTrades(params: UseTradesParams = {}) {
  const { run_id, symbol, strategy, exit_reason, limit, start, end } = params;

  const query = useQuery({
    queryKey: ['trades', run_id, symbol, strategy, exit_reason, limit, start, end],
    queryFn: () =>
      fetchApi<Trade[]>('/api/trades/', {
        run_id,
        symbol,
        strategy,
        exit_reason,
        limit,
        start,
        end,
      }),
  });

  useEffect(() => {
    if (query.data) {
      useTradesStore.getState().setTrades(query.data);
    }
  }, [query.data]);

  return query;
}

export function useTradeStats(params: UseTradesParams = {}) {
  const { run_id, symbol, strategy, exit_reason, limit, start, end } = params;

  const query = useQuery({
    queryKey: ['trade-stats', run_id, symbol, strategy, exit_reason, limit, start, end],
    queryFn: () =>
      fetchApi<TradeStatsResponse>('/api/trades/stats', {
        run_id,
        symbol,
        strategy,
        exit_reason,
        limit,
        start,
        end,
      }),
  });

  useEffect(() => {
    if (query.data) {
      useTradesStore.getState().setStats(query.data);
    }
  }, [query.data]);

  return query;
}

// ── Orders ───────────────────────────────────────────────────────────────────

export interface UseOrdersParams extends RunFilterParams {
  symbol?: string;
  side?: string;
  status?: string;
  limit?: number;
}

export function useOrders(params: UseOrdersParams = {}) {
  const { run_id, symbol, side, status, limit, start, end } = params;

  const query = useQuery({
    queryKey: ['orders', run_id, symbol, side, status, limit, start, end],
    queryFn: () =>
      fetchApi<Order[]>('/api/orders/', {
        run_id,
        symbol,
        side,
        status,
        limit,
        start,
        end,
      }),
  });

  useEffect(() => {
    if (query.data) {
      useOrdersStore.getState().setOrders(query.data);
    }
  }, [query.data]);

  return query;
}

// ── Strategy ─────────────────────────────────────────────────────────────────

export interface UseStrategyParams {
  symbol?: string;
  strategy?: string;
}

export function useStrategyState(params: UseStrategyParams = {}) {
  const { symbol, strategy } = params;

  const query = useQuery({
    queryKey: ['strategy-state', symbol, strategy],
    queryFn: () =>
      fetchApi<StrategyStateResponse[]>('/api/strategy/state', {
        symbol,
        strategy,
      }),
  });

  useEffect(() => {
    if (query.data) {
      useStrategyStore.getState().setStates(query.data);
    }
  }, [query.data]);

  return query;
}

export function useStrategySignals(params: UseStrategyParams = {}) {
  const { symbol, strategy } = params;

  const query = useQuery({
    queryKey: ['strategy-signals', symbol, strategy],
    queryFn: () =>
      fetchApi<StrategySignalSnapshot[]>('/api/strategy/signals', {
        symbol,
        strategy,
      }),
  });

  useEffect(() => {
    if (query.data) {
      useStrategyStore.getState().setSignals(query.data);
    }
  }, [query.data]);

  return query;
}

// ── System ───────────────────────────────────────────────────────────────────

export function useHealth() {
  const query = useQuery({
    queryKey: ['health'],
    queryFn: () => fetchApi<HealthResponse>('/api/system/health'),
    refetchInterval: 30_000, // Poll every 30s as a liveness fallback
  });

  useEffect(() => {
    if (query.data) {
      useSystemStore.getState().setHealth(query.data);
    }
  }, [query.data]);

  return query;
}

export interface UseLogsParams {
  lines?: number;
  level?: string;
}

export function useLogs(params: UseLogsParams = {}) {
  const { lines, level } = params;

  const query = useQuery({
    queryKey: ['logs', lines, level],
    queryFn: () =>
      fetchApi<LogsResponse>('/api/system/logs', { lines, level }),
  });

  useEffect(() => {
    if (query.data) {
      useSystemStore.getState().setLogs(query.data);
    }
  }, [query.data]);

  return query;
}
