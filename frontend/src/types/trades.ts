// ── Trade Types ──────────────────────────────────────────────────────────────
// Matches: core/models.py → Trade
//          api/routes/trades.py → TradeStatsResponse

import type { ExitReason } from './enums';

/** Closed trade record (GET /api/trades/). */
export interface Trade {
  symbol: string;
  strategy: string;
  entry_price: string; // Decimal
  exit_price: string; // Decimal
  size: string; // Decimal
  entry_time: string; // ISO 8601
  exit_time: string; // ISO 8601
  pnl_usdt: string; // Decimal
  pnl_percent: string; // Decimal
  duration_minutes: number;
  exit_reason: ExitReason;
  max_favorable_excursion: string | null;
  max_adverse_excursion: string | null;
  /** True when exit_price was estimated (not a real exchange fill) — PnL untrustworthy. */
  exit_price_estimated: boolean;
}

/** Aggregate trade statistics (GET /api/trades/stats). */
export interface TradeStatsResponse {
  total_trades: number;
  winning_trades: number;
  losing_trades: number;
  win_rate: string; // percentage
  total_pnl: string; // USDT
  avg_win_percent: string;
  avg_loss_percent: string;
  profit_factor: string | null;
  avg_duration_minutes: number | null;
  max_consecutive_wins: number;
  max_consecutive_losses: number;
}
