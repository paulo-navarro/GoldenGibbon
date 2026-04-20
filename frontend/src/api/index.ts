export { fetchApi, postApi, patchApi, ApiError } from './client';

export {
  // Market
  useCandles,
  usePrice,
  // Portfolio
  usePortfolio,
  useEquityCurve,
  // Trades
  useTrades,
  useTradeStats,
  // Orders
  useOrders,
  // Strategy
  useStrategyState,
  useStrategySignals,
  // System
  useHealth,
  useLogs,
  // Backtest
  useCompare,
  useMultiStrategy,
  // Config
  useStrategyList,
  useStrategyConfig,
  useUpdateStrategyConfig,
  useResetStrategyConfig,
  // Optimization
  useOptimize,
  useWalkForward,
} from './queries';

export type {
  UseCandlesParams,
  UsePriceParams,
  UseEquityCurveParams,
  UseTradesParams,
  UseOrdersParams,
  UseStrategyParams,
  UseLogsParams,
  UseCompareParams,
  ComparisonMetricsRow,
  ComparisonResponse,
  UseMultiStrategyParams,
  RegimeEvent,
  StrategyBreakdown,
  MultiStrategyResponse,
  FieldMeta,
  StrategyConfigResponse,
  StrategyListResponse,
  GridSearchRow,
  OptimizationRequest,
  OptimizationResponse,
  WalkForwardFold,
  WalkForwardRequest,
  WalkForwardResponse,
} from './queries';
