// ── Order Types ──────────────────────────────────────────────────────────────
// Matches: core/models.py → Order

import type { OrderSide, OrderStatus, OrderType } from './enums';

/** Order record (GET /api/orders/, GET /api/orders/{order_id}). */
export interface Order {
  run_id: string | null;
  symbol: string;
  side: OrderSide;
  order_type: OrderType;
  amount: string; // Decimal
  price: string | null; // null for market orders
  status: OrderStatus;
  filled_amount: string; // Decimal
  avg_fill_price: string | null;
  slippage_percent: string | null;
  fee_usdt: string | null;
  exchange_order_id: string | null;
  created_at: string; // ISO 8601
  updated_at: string | null;
  filled_at: string | null;
}
