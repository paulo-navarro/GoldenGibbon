// ── Market Data Types ────────────────────────────────────────────────────────
// Matches: core/models.py → Candle
//          api/routes/market.py → PriceResponse

/** Single OHLCV candle. */
export interface Candle {
  open_time: string; // ISO 8601
  open: string; // Decimal
  high: string; // Decimal
  low: string; // Decimal
  close: string; // Decimal
  volume: string; // Decimal
  quote_volume: string | null;
  trades_count: number | null;
}

/** Latest price for a symbol (GET /api/market/price/{symbol}). */
export interface PriceResponse {
  symbol: string;
  price: string; // Decimal as string
  open_time: string; // ISO 8601
  timeframe: string;
}
