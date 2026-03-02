// ── Orders Store ─────────────────────────────────────────────────────────────
// Task 2.35 – Zustand store for order records.  Populated via REST (initial
// load) and WebSocket events (live updates from the `gg:execution` channel).

import { create } from 'zustand';
import type { Event } from '../types/events';
import type { OrderStatus } from '../types/enums';
import type { Order } from '../types/orders';

// ── Types ────────────────────────────────────────────────────────────────────

interface OrdersState {
  /** Order history (newest first). */
  orders: Order[];
}

interface OrdersActions {
  // ── REST seed actions ───────────────────────────────────────────
  /** Bulk-set orders from GET /api/orders/. */
  setOrders: (data: Order[]) => void;

  // ── WebSocket event dispatcher ──────────────────────────────────
  /** Process a WebSocket event on the `gg:execution` channel. */
  handleEvent: (event: Event) => void;
}

type OrdersStore = OrdersState & OrdersActions;

// ── Constants ────────────────────────────────────────────────────────────────

/** Maximum orders kept in the client-side buffer. */
const MAX_ORDERS = 500;

// ── Helpers ──────────────────────────────────────────────────────────────────

/**
 * Find the index of a matching order by composite key.
 * Uses `symbol` + `created_at` as primary match.  Falls back to
 * `exchange_order_id` if both sides have it.
 */
function findOrderIndex(orders: Order[], incoming: Record<string, unknown>): number {
  const symbol = incoming.symbol as string;
  const createdAt = incoming.created_at as string;
  const exchangeId = incoming.exchange_order_id as string | null;

  // Prefer exchange_order_id when available on both sides
  if (exchangeId) {
    const idx = orders.findIndex(
      (o) => o.exchange_order_id === exchangeId && o.symbol === symbol,
    );
    if (idx !== -1) return idx;
  }

  // Fall back to symbol + created_at
  return orders.findIndex(
    (o) => o.symbol === symbol && o.created_at === createdAt,
  );
}

/** Build an Order object from an event data payload. */
function orderFromData(data: Record<string, unknown>): Order {
  return {
    run_id: (data.run_id as string) ?? null,
    symbol: data.symbol as string,
    side: data.side as Order['side'],
    order_type: data.order_type as Order['order_type'],
    amount: data.amount as string,
    price: (data.price as string) ?? null,
    status: data.status as Order['status'],
    filled_amount: (data.filled_amount as string) ?? '0',
    avg_fill_price: (data.avg_fill_price as string) ?? null,
    slippage_percent: (data.slippage_percent as string) ?? null,
    fee_usdt: (data.fee_usdt as string) ?? null,
    exchange_order_id: (data.exchange_order_id as string) ?? null,
    created_at: data.created_at as string,
    updated_at: (data.updated_at as string) ?? null,
    filled_at: (data.filled_at as string) ?? null,
  };
}

// ── Store ────────────────────────────────────────────────────────────────────

export const useOrdersStore = create<OrdersStore>()((set) => ({
  // ── Initial state ───────────────────────────────────────────────
  orders: [],

  // ── REST seed actions ───────────────────────────────────────────

  setOrders: (data) => {
    set({ orders: data.slice(0, MAX_ORDERS) });
  },

  // ── WebSocket event dispatcher ──────────────────────────────────

  handleEvent: (event) => {
    if (event.channel !== 'gg:execution') return;

    const { event_type, data } = event;

    switch (event_type) {
      case 'order_created': {
        const order = orderFromData(data);
        set((s) => {
          const updated = [order, ...s.orders];
          return {
            orders:
              updated.length > MAX_ORDERS
                ? updated.slice(0, MAX_ORDERS)
                : updated,
          };
        });
        break;
      }

      case 'order_filled':
      case 'order_rejected':
      case 'order_cancelled': {
        // Patch the matching order in-place.
        set((s) => {
          const idx = findOrderIndex(s.orders, data);
          if (idx === -1) {
            // Order not in buffer — prepend the full record.
            const order = orderFromData(data);
            const updated = [order, ...s.orders];
            return {
              orders:
                updated.length > MAX_ORDERS
                  ? updated.slice(0, MAX_ORDERS)
                  : updated,
            };
          }

          const orders = [...s.orders];
          orders[idx] = {
            ...orders[idx],
            status: (data.status as OrderStatus) ?? orders[idx].status,
            filled_amount: (data.filled_amount as string) ?? orders[idx].filled_amount,
            avg_fill_price: (data.avg_fill_price as string) ?? orders[idx].avg_fill_price,
            slippage_percent: (data.slippage_percent as string) ?? orders[idx].slippage_percent,
            fee_usdt: (data.fee_usdt as string) ?? orders[idx].fee_usdt,
            filled_at: (data.filled_at as string) ?? orders[idx].filled_at,
            updated_at: (data.updated_at as string) ?? event.timestamp,
          };
          return { orders };
        });
        break;
      }

      default:
        break;
    }
  },
}));
