// ── Portfolio Types ──────────────────────────────────────────────────────────
// Matches: core/models.py → Position, PortfolioSnapshot
//          api/routes/portfolio.py → PortfolioResponse

/** Open position with stops and scaling info. */
export interface Position {
  symbol: string;
  side: 'long' | 'short';
  size: string; // Decimal
  entry_price: string; // Decimal
  entry_time: string; // ISO 8601
  highest_close: string; // Decimal
  trailing_stop_price: string; // Decimal
  hard_stop_price: string; // Decimal
  scale_in_count: number; // 0, 1, or 2
  buy_signal_candles: number;
  unrealized_pnl: string | null;
  unrealized_pnl_percent: string | null;
}

/** Current portfolio state (GET /api/portfolio/). */
export interface PortfolioResponse {
  usdt_balance: string; // Decimal
  equity: string; // Decimal
  positions_value: string; // Decimal
  total_pnl: string; // Decimal
  open_positions_count: number;
  positions: Position[];
  last_updated: string | null; // ISO 8601
}

/** Status of a single exit condition. */
export interface ExitConditionStatus {
  name: string;
  met: boolean;
  current_value: string | null;
  threshold: string | null;
}

/** Exit proximity for an open position (GET /api/portfolio/exit-proximity). */
export interface ExitProximityResponse {
  symbol: string;
  strategy: string;
  hard_stop_pct: number;
  trailing_stop_pct: number;
  time_stop_pct: number | null;
  exit_conditions: ExitConditionStatus[];
}

/** Point-in-time snapshot for equity curve (GET /api/portfolio/equity-curve). */
export interface PortfolioSnapshot {
  timestamp: string; // ISO 8601
  usdt_balance: string; // Decimal
  positions_value: string; // Decimal
  total_equity: string; // Decimal
  daily_pnl: string | null;
  total_pnl: string; // Decimal
  open_positions_count: number;
}
