export { fetchApi, postApi, patchApi, deleteApi, ApiError } from './client';

export {
  // Market
  useCandles,
  usePrice,
  // Portfolio
  usePortfolio,
  useExitProximity,
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
  SymbolItem,
  SymbolListResponse,
  AddSymbolRequest,
  AddSymbolResponse,
  PatchSymbolRequest,
  NamespaceFieldMeta,
  NamespaceConfigResponse,
  NamespaceListResponse,
} from './queries';
