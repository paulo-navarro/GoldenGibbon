// ── React Query Hooks ────────────────────────────────────────────────────────
// Task 2.37 – One hook per REST endpoint.  Each hook fetches data, seeds the
// corresponding Zustand store on success, and exposes standard React Query
// loading / error / data states.
//
// Pages can read directly from `data` (React Query cache) or from the Zustand
// store (kept in sync via seed + WebSocket events).

import { useCallback, useEffect, useState } from 'react';
import { useQuery, useQueryClient, useMutation } from '@tanstack/react-query';
import { fetchApi, postApi, patchApi, deleteApi } from './client';

import type { Candle, PriceResponse, Ticker24hResponse } from '../types/market';
import type { ExitProximityResponse, PortfolioResponse, PortfolioSnapshot } from '../types/portfolio';
import type { Trade, TradeStatsResponse } from '../types/trades';
import type { Order, ExchangeOrder } from '../types/orders';
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

// ── Trading Mode ────────────────────────────────────────────────────────────

export function useTradingMode(): string {
  const { data } = useNamespaceConfig('live_trading');
  if (!data) return 'paper';
  const enabled = data.fields.find(f => f.name === 'enabled');
  return enabled?.value === true ? 'live' : 'paper';
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

export function useTicker24h() {
  return useQuery<Ticker24hResponse>({
    queryKey: ['ticker24h'],
    queryFn: () => fetchApi<Ticker24hResponse>('/api/market/ticker24h'),
    refetchInterval: 60_000,
  });
}

// ── Portfolio ────────────────────────────────────────────────────────────────

export function usePortfolio() {
  const tradingMode = useTradingMode();

  const query = useQuery({
    queryKey: ['portfolio', tradingMode],
    queryFn: () => fetchApi<PortfolioResponse>('/api/portfolio/', { trading_mode: tradingMode }),
    refetchInterval: 15_000,
  });

  useEffect(() => {
    if (query.data) {
      usePortfolioStore.getState().setPortfolio(query.data);
    }
  }, [query.data]);

  return query;
}

export function useExitProximity() {
  return useQuery({
    queryKey: ['exit-proximity'],
    queryFn: () => fetchApi<ExitProximityResponse[]>('/api/portfolio/exit-proximity'),
    refetchInterval: 30_000,
  });
}

export interface UseEquityCurveParams extends RunFilterParams {
  limit?: number;
}

export function useEquityCurve(params: UseEquityCurveParams = {}) {
  const { run_id, limit, start, end } = params;
  const tradingMode = useTradingMode();

  const query = useQuery({
    queryKey: ['equity-curve', tradingMode, run_id, limit, start, end],
    queryFn: () =>
      fetchApi<PortfolioSnapshot[]>('/api/portfolio/equity-curve', {
        run_id,
        trading_mode: run_id ? undefined : tradingMode,
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
  /** 'true' | 'false' | undefined — filter by estimated exit price (BUG-015). */
  exit_price_estimated?: string;
  limit?: number;
}

export function useTrades(params: UseTradesParams = {}) {
  const { run_id, symbol, strategy, exit_reason, exit_price_estimated, limit, start, end } = params;
  const tradingMode = useTradingMode();

  const query = useQuery({
    queryKey: ['trades', tradingMode, run_id, symbol, strategy, exit_reason, exit_price_estimated, limit, start, end],
    queryFn: () =>
      fetchApi<Trade[]>('/api/trades/', {
        run_id,
        trading_mode: run_id ? undefined : tradingMode,
        symbol,
        strategy,
        exit_reason,
        exit_price_estimated,
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
  const { run_id, symbol, strategy, exit_reason, exit_price_estimated, limit, start, end } = params;
  const tradingMode = useTradingMode();

  const query = useQuery({
    queryKey: ['trade-stats', tradingMode, run_id, symbol, strategy, exit_reason, exit_price_estimated, limit, start, end],
    queryFn: () =>
      fetchApi<TradeStatsResponse>('/api/trades/stats', {
        run_id,
        trading_mode: run_id ? undefined : tradingMode,
        symbol,
        strategy,
        exit_reason,
        exit_price_estimated,
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
  const tradingMode = useTradingMode();

  const query = useQuery({
    queryKey: ['orders', tradingMode, run_id, symbol, side, status, limit, start, end],
    queryFn: () =>
      fetchApi<Order[]>('/api/orders/', {
        run_id,
        trading_mode: run_id ? undefined : tradingMode,
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

// ── Exchange Orders ─────────────────────────────────────────────────────────

export interface UseExchangeOrdersParams {
  source?: string;
  symbol?: string;
  status?: string;
  limit?: number;
}

export function useExchangeOrders(params: UseExchangeOrdersParams = {}) {
  const { source, symbol, status, limit } = params;

  return useQuery({
    queryKey: ['exchange-orders', source, symbol, status, limit],
    queryFn: () =>
      fetchApi<ExchangeOrder[]>('/api/exchange/orders/', {
        source,
        symbol,
        status,
        limit,
      }),
  });
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

// ── Backtest jobs (task 9.1) ────────────────────────────────────────────────
// Backtests run in Celery workers. POST enqueues and returns a job_id (202);
// the UI then polls GET /api/backtest/jobs/{job_id} until done/failed.

export interface JobEnqueuedResponse {
  job_id: string;
  job_type: string;
  status: string;
}

export interface BacktestJobStatus<T> {
  job_id: string;
  status: 'pending' | 'running' | 'done' | 'failed';
  result: T | null;
  error: string | null;
}

const JOB_POLL_INTERVAL_MS = 2000;

function useBacktestJob<TResult>(endpoint: string) {
  const [jobId, setJobId] = useState<string | null>(null);

  const enqueue = useMutation({
    mutationFn: (body: Record<string, unknown>) =>
      postApi<JobEnqueuedResponse>(endpoint, body),
    onSuccess: (res) => setJobId(res.job_id),
  });

  const poll = useQuery({
    queryKey: ['backtest-job', endpoint, jobId],
    queryFn: () =>
      fetchApi<BacktestJobStatus<TResult>>(`/api/backtest/jobs/${jobId}`),
    enabled: !!jobId,
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return status === 'done' || status === 'failed' ? false : JOB_POLL_INTERVAL_MS;
    },
  });

  const status = jobId ? poll.data?.status : undefined;
  const isRunning =
    enqueue.isPending || (!!jobId && status !== 'done' && status !== 'failed');
  const data = status === 'done' ? (poll.data?.result ?? undefined) : undefined;
  const error: Error | null =
    (enqueue.error as Error | null) ??
    (poll.error as Error | null) ??
    (status === 'failed'
      ? new Error(poll.data?.error ?? 'Backtest job failed')
      : null);

  const { mutate: enqueueMutate } = enqueue;
  const start = useCallback(
    (body: Record<string, unknown>) => {
      enqueueMutate(body);
    },
    [enqueueMutate],
  );

  return { start, data, error, isRunning };
}

/** "BTCUSDT,ETHUSDT" → ["BTCUSDT", "ETHUSDT"]; empty/undefined → undefined. */
function splitCsv(value?: string): string[] | undefined {
  if (!value) return undefined;
  const parts = value.split(',').map((s) => s.trim()).filter(Boolean);
  return parts.length > 0 ? parts : undefined;
}

// ── Backtest Comparison ─────────────────────────────────────────────────────

export interface UseCompareParams {
  symbols?: string;
  days?: number;
  strategies?: string;
}

export interface ComparisonMetricsRow {
  strategy: string;
  symbol: string;
  total_return: string;
  total_trades: number;
  winning_trades: number;
  losing_trades: number;
  win_rate: string;
  max_drawdown: string;
  sharpe_ratio: string | null;
  profit_factor: string | null;
  avg_trade_duration_minutes: number | null;
  max_consecutive_wins: number | null;
  max_consecutive_losses: number | null;
  buy_hold_return: string | null;
  buy_hold_vs_strategy: string | null;
  initial_capital: string;
  final_capital: string;
}

export interface EquityPoint {
  timestamp: string;
  equity: string;
}

export interface ComparisonResponse {
  rows: ComparisonMetricsRow[];
  days: number;
  date_range: string | null;
  errors: Array<Record<string, string>>;
  per_strategy_curves: Record<string, EquityPoint[]>;
}

export function useCompare(params: UseCompareParams = {}) {
  const { symbols, days = 90, strategies } = params;
  const { start, data, error, isRunning } =
    useBacktestJob<ComparisonResponse>('/api/backtest/compare');

  const refetch = useCallback(() => {
    start({ symbols: splitCsv(symbols), days, strategies: splitCsv(strategies) });
  }, [start, symbols, days, strategies]);

  return { data, isLoading: isRunning, isFetching: isRunning, error, refetch };
}

// ── Multi-Strategy Backtest ─────────────────────────────────────────────────

export interface UseMultiStrategyParams {
  symbols?: string;
  days?: number;
  strategies?: string;
  regime_shift_pct?: number;
}

export interface RegimeEvent {
  timestamp: string;
  regime: string;
  confidence: number;
  adx: number;
}

export interface StrategyBreakdown {
  strategy: string;
  symbol: string;
  allocated_capital: string;
  total_return: string;
  total_trades: number;
  win_rate: string;
  max_drawdown: string;
  sharpe_ratio: string | null;
}

export interface MultiStrategyResponse {
  combined_equity_curve: Array<{ timestamp: string; equity: string }>;
  per_strategy: StrategyBreakdown[];
  regime_timeline: RegimeEvent[];
  total_initial_capital: string;
  total_final_equity: string;
  total_return_pct: string;
  date_range: string | null;
  days: number;
  errors: Array<Record<string, string>>;
}

export function useMultiStrategy(params: UseMultiStrategyParams = {}) {
  const { symbols, days = 90, strategies } = params;
  const { start, data, error, isRunning } =
    useBacktestJob<MultiStrategyResponse>('/api/backtest/multi-strategy');

  const refetch = useCallback(() => {
    start({ symbols: splitCsv(symbols), days, strategies: splitCsv(strategies) });
  }, [start, symbols, days, strategies]);

  return { data, isLoading: isRunning, isFetching: isRunning, error, refetch };
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
  limit?: number;
  offset?: number;
  level?: string;
  category?: string;
}

export function useLogs(params: UseLogsParams = {}) {
  const { limit, offset, level, category } = params;

  const query = useQuery({
    queryKey: ['logs', limit, offset, level, category],
    queryFn: () =>
      fetchApi<LogsResponse>('/api/system/logs', { limit, offset, level, category }),
  });

  useEffect(() => {
    if (query.data) {
      useSystemStore.getState().setLogs(query.data);
    }
  }, [query.data]);

  return query;
}

// ── Strategy Config (task 5.5b) ─────────────────────────────────────────────

export interface FieldMeta {
  name: string;
  value: unknown;
  default: unknown;
  type: string;
  description?: string;
  min?: number;
  max?: number;
  group: string;
  source: string;
}

export interface StrategyConfigResponse {
  strategy: string;
  fields: FieldMeta[];
}

export interface StrategyListResponse {
  strategies: string[];
}

export interface StrategySummary {
  name: string;
  enabled: boolean;
}

export interface StrategyOverviewResponse {
  strategies: StrategySummary[];
}

export function useStrategyList() {
  return useQuery({
    queryKey: ['strategy-list'],
    queryFn: () => fetchApi<StrategyListResponse>('/api/strategy/config/strategies'),
  });
}

export function useStrategyOverview() {
  return useQuery({
    queryKey: ['strategy-overview'],
    queryFn: () => fetchApi<StrategyOverviewResponse>('/api/strategy/config/overview'),
  });
}

export function useToggleStrategy() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({ name, enabled }: { name: string; enabled: boolean }) =>
      patchApi<StrategyConfigResponse>(`/api/strategy/config/${name}`, { enabled }),
    onSuccess: (_data, { name }) => {
      queryClient.invalidateQueries({ queryKey: ['strategy-overview'] });
      queryClient.invalidateQueries({ queryKey: ['strategy-config', name] });
    },
  });
}

export function useStrategyConfig(name: string) {
  return useQuery({
    queryKey: ['strategy-config', name],
    queryFn: () => fetchApi<StrategyConfigResponse>(`/api/strategy/config/${name}`),
    enabled: !!name,
  });
}

export function useUpdateStrategyConfig(name: string) {
  const queryClient = useQueryClient();

  const mutation = useMutation({
    mutationFn: (updates: Record<string, unknown>) =>
      patchApi<StrategyConfigResponse>(`/api/strategy/config/${name}`, updates),
    onSuccess: (data) => {
      queryClient.setQueryData(['strategy-config', name], data);
    },
  });

  const update = useCallback(
    (updates: Record<string, unknown>) => mutation.mutate(updates),
    [mutation],
  );

  return { ...mutation, update };
}

export function useResetStrategyConfig(name: string) {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: async () => {
      const res = await fetch(`/api/strategy/config/${name}/reset`, { method: 'DELETE' });
      if (!res.ok) throw new Error('Reset failed');
      return (await res.json()) as StrategyConfigResponse;
    },
    onSuccess: (data) => {
      queryClient.setQueryData(['strategy-config', name], data);
    },
  });
}

// ── Kill-switch reset ───────────────────────────────────────────────────────

export function useResetKillSwitch(symbol: string, strategy: string) {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: () =>
      postApi<{ status: string }>(`/api/strategy/kill-switch/${symbol}/${strategy}/reset`, {}),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['strategy-state'] });
      queryClient.invalidateQueries({ queryKey: ['strategy-signals'] });
    },
  });
}

// ── Parameter Optimization (task 5.6) ───────────────────────────────────────

export interface GridSearchRow {
  params: Record<string, unknown>;
  sharpe_ratio: number | null;
  total_return: number;
  max_drawdown: number;
  win_rate: number;
  profit_factor: number | null;
  total_trades: number;
}

export interface OptimizationRequest {
  strategy: string;
  symbol: string;
  days: number;
  param_grid: Record<string, unknown[]>;
  metric: string;
}

export interface OptimizationResponse {
  strategy: string;
  symbol: string;
  metric: string;
  days: number;
  total_combos: number;
  results: GridSearchRow[];
  best_params: Record<string, unknown>;
  errors: string[];
}

export interface WalkForwardFold {
  fold: number;
  train_range: string;
  test_range: string;
  best_params: Record<string, unknown>;
  train_sharpe: number | null;
  train_return: number;
  test_sharpe: number | null;
  test_return: number;
  overfit: boolean;
}

export interface WalkForwardRequest {
  strategy: string;
  symbol: string;
  days: number;
  param_grid: Record<string, unknown[]>;
  metric: string;
  n_folds: number;
  train_pct: number;
}

export interface WalkForwardResponse {
  strategy: string;
  symbol: string;
  metric: string;
  days: number;
  n_folds: number;
  folds: WalkForwardFold[];
  avg_test_sharpe: number | null;
  avg_test_return: number;
  errors: string[];
}

export function useOptimize() {
  const { start, data, error, isRunning } =
    useBacktestJob<OptimizationResponse>('/api/backtest/optimize');

  const mutate = useCallback(
    (req: OptimizationRequest) => start(req as unknown as Record<string, unknown>),
    [start],
  );

  return { mutate, isPending: isRunning, data, error };
}

export function useWalkForward() {
  const { start, data, error, isRunning } =
    useBacktestJob<WalkForwardResponse>('/api/backtest/walk-forward');

  const mutate = useCallback(
    (req: WalkForwardRequest) => start(req as unknown as Record<string, unknown>),
    [start],
  );

  return { mutate, isPending: isRunning, data, error };
}

// ── Symbol Management (task 7.7–7.10) ────────────────────────────────────────

export interface SymbolItem {
  symbol: string;
  exchange: string;
  timeframes: string[];
  enabled: boolean;
  description: string | null;
  source: string;
}

export interface SymbolListResponse {
  symbols: SymbolItem[];
}

export interface AddSymbolRequest {
  symbol: string;
  exchange?: string;
  timeframes?: string[];
  enabled?: boolean;
  description?: string;
}

export interface AddSymbolResponse {
  symbol: SymbolItem;
  message: string;
}

export interface PatchSymbolRequest {
  enabled?: boolean;
  timeframes?: string[];
  description?: string;
}

export function useSymbols() {
  return useQuery<SymbolListResponse>({
    queryKey: ['symbols'],
    queryFn: () => fetchApi<SymbolListResponse>('/api/config/symbols'),
  });
}

export function useAddSymbol() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (req: AddSymbolRequest) =>
      postApi<AddSymbolResponse>('/api/config/symbols', req as unknown as Record<string, unknown>),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['symbols'] });
    },
  });
}

export function useDeleteSymbol() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (symbol: string) =>
      deleteApi<{ message: string }>(`/api/config/symbols/${symbol}`),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['symbols'] });
    },
  });
}

export function usePatchSymbol() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({ symbol, ...body }: PatchSymbolRequest & { symbol: string }) =>
      patchApi<SymbolItem>(`/api/config/symbols/${symbol}`, body as Record<string, unknown>),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['symbols'] });
    },
  });
}

// ── App Config (Namespace-based settings) ─────────────────────────────────────

export interface NamespaceFieldMeta {
  name: string;
  value: unknown;
  default: unknown;
  type: string;
  description?: string;
  min?: number;
  max?: number;
}

export interface NamespaceConfigResponse {
  namespace: string;
  source: string;
  fields: NamespaceFieldMeta[];
}

export interface NamespaceListResponse {
  namespaces: string[];
}

export function useNamespaceList() {
  return useQuery({
    queryKey: ['namespace-list'],
    queryFn: () => fetchApi<NamespaceListResponse>('/api/config/namespaces'),
  });
}

export function useNamespaceConfig(namespace: string) {
  return useQuery({
    queryKey: ['namespace-config', namespace],
    queryFn: () => fetchApi<NamespaceConfigResponse>(`/api/config/${namespace}`),
    enabled: !!namespace,
  });
}

export function useUpdateNamespaceConfig(namespace: string) {
  const queryClient = useQueryClient();

  const mutation = useMutation({
    mutationFn: (updates: Record<string, unknown>) =>
      patchApi<NamespaceConfigResponse>(`/api/config/${namespace}`, updates),
    onSuccess: (data) => {
      queryClient.setQueryData(['namespace-config', namespace], data);
    },
  });

  const update = useCallback(
    (updates: Record<string, unknown>) => mutation.mutate(updates),
    [mutation],
  );

  return { ...mutation, update };
}

export function useResetNamespaceConfig(namespace: string) {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: async () => {
      const res = await fetch(`/api/config/${namespace}/reset`, { method: 'DELETE' });
      if (!res.ok) throw new Error('Reset failed');
      return (await res.json()) as NamespaceConfigResponse;
    },
    onSuccess: (data) => {
      queryClient.setQueryData(['namespace-config', namespace], data);
    },
  });
}
