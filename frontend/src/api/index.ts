export { fetchApi, ApiError } from './client';

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
} from './queries';

export type {
  UseCandlesParams,
  UsePriceParams,
  UseEquityCurveParams,
  UseTradesParams,
  UseOrdersParams,
  UseStrategyParams,
  UseLogsParams,
} from './queries';
