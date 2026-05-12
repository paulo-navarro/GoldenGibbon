export { fetchApi, postApi, patchApi, deleteApi, ApiError } from './client';

export {
  // Market
  useCandles,
  usePrice,
  useTicker24h,
  // Portfolio
  usePortfolio,
  useExitProximity,
  useEquityCurve,
  // Trades
  useTrades,
  useTradeStats,
  // Orders
  useOrders,
  useExchangeOrders,
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
  useStrategyOverview,
  useToggleStrategy,
  useStrategyConfig,
  useUpdateStrategyConfig,
  useResetStrategyConfig,
  useResetKillSwitch,
  // Optimization
  useOptimize,
  useWalkForward,
  // Symbols
  useSymbols,
  useAddSymbol,
  useDeleteSymbol,
  usePatchSymbol,
  // App Config (Settings)
  useNamespaceList,
  useNamespaceConfig,
  useUpdateNamespaceConfig,
  useResetNamespaceConfig,
} from './queries';

export type {
  UseCandlesParams,
  UsePriceParams,
  UseEquityCurveParams,
  UseTradesParams,
  UseOrdersParams,
  UseExchangeOrdersParams,
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
  StrategySummary,
  StrategyOverviewResponse,
  GridSearchRow,
  OptimizationRequest,
  OptimizationResponse,
  WalkForwardFold,
  WalkForwardRequest,
  WalkForwardResponse,
  SymbolItem,
  SymbolListResponse,
  AddSymbolRequest,
  AddSymbolResponse,
  PatchSymbolRequest,
  NamespaceFieldMeta,
  NamespaceConfigResponse,
  NamespaceListResponse,
} from './queries';
