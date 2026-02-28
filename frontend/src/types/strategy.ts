// ── Strategy Types ───────────────────────────────────────────────────────────
// Matches: core/models.py → StrategyStateResponse, StrategySignalSnapshot,
//          StrategyConditions, MeanReversionConditions

import type { Signal, StrategyState } from './enums';

/** Strategy state record (GET /api/strategy/state). */
export interface StrategyStateResponse {
  symbol: string;
  strategy: string;
  state: StrategyState;
  cooldown_until: string | null; // ISO 8601
  consecutive_buy_candles: number;
  last_exit_time: string | null; // ISO 8601
  state_data: Record<string, unknown> | null;
  created_at: string; // ISO 8601
  updated_at: string; // ISO 8601
}

/** Derived signal snapshot (GET /api/strategy/signals). */
export interface StrategySignalSnapshot {
  symbol: string;
  strategy: string;
  state: StrategyState;
  signal: Signal;
  conditions: Record<string, unknown> | null;
  updated_at: string; // ISO 8601
}

/** Conditions checklist for Smart Hodler strategy. */
export interface StrategyConditions {
  ema_cross_bullish: boolean;
  adx_above_threshold: boolean;
  close_above_ema_fast: boolean;
  volume_above_average: boolean;
  hourly_ema_rising: boolean;
  hourly_rsi_above_threshold: boolean;
  session_filter_pass: boolean;
}

/** Conditions checklist for Mean Reversion strategy. */
export interface MeanReversionConditions {
  close_below_lower_bb: boolean;
  rsi_oversold: boolean;
  adx_below_threshold: boolean;
  volume_spike: boolean;
  hourly_rsi_ok: boolean;
  hourly_close_above_ema: boolean;
  session_filter_pass: boolean;
}
