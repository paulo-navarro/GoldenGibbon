// ── Barrel Export ────────────────────────────────────────────────────────────
// All TypeScript types for the GoldenGibbon frontend.
// Usage: import { Candle, Order, Signal } from '@/types';

export type {
  Signal,
  StrategyState,
  OrderSide,
  OrderType,
  OrderStatus,
  ExitReason,
  RiskAction,
  EventChannel,
  EventType,
} from './enums';

export type { Candle, PriceResponse } from './market';

export type {
  Position,
  PortfolioResponse,
  PortfolioSnapshot,
} from './portfolio';

export type { Trade, TradeStatsResponse } from './trades';

export type { Order } from './orders';

export type {
  StrategyStateResponse,
  StrategySignalSnapshot,
  StrategyConditions,
  MeanReversionConditions,
} from './strategy';

export type { HealthResponse, LogsResponse } from './system';

export type {
  Event,
  WelcomeMessage,
  PingMessage,
  ErrorMessage,
  WebSocketMessage,
} from './events';
