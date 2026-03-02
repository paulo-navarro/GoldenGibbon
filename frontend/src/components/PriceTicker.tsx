// ── PriceTicker ───────────────────────────────────────────────────────────────
// Task 2.46 – AppBar price ticker: shows latest price for a symbol with
// a directional arrow (up / down / flat) based on the previous value.

import { useState } from 'react';
import Box from '@mui/material/Box';
import Typography from '@mui/material/Typography';
import ArrowDropUpIcon from '@mui/icons-material/ArrowDropUp';
import ArrowDropDownIcon from '@mui/icons-material/ArrowDropDown';
import RemoveIcon from '@mui/icons-material/Remove';

import { usePrice } from '../api';
import { useMarketStore } from '../stores';

// ── Types ────────────────────────────────────────────────────────────────────

export interface PriceTickerProps {
  /** Full symbol, e.g. "BTCUSDT". */
  symbol: string;
}

type Direction = 'up' | 'down' | 'flat';

// ── Helpers ──────────────────────────────────────────────────────────────────

/** Shorten "BTCUSDT" → "BTC/USDT". */
function formatSymbol(sym: string): string {
  if (sym.endsWith('USDT')) return `${sym.slice(0, -4)}/USDT`;
  if (sym.endsWith('BTC')) return `${sym.slice(0, -3)}/BTC`;
  return sym;
}

/** Format price string to 2 dp for fiat-quoted pairs, more for others. */
function formatPrice(price: string): string {
  const n = parseFloat(price);
  if (isNaN(n)) return price;
  return n >= 1 ? n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : n.toFixed(6);
}

// ── Component ────────────────────────────────────────────────────────────────

export default function PriceTicker({ symbol }: PriceTickerProps) {
  // Seed store via REST on mount (refetch every 30 s)
  usePrice(symbol, {});

  // Live value from Zustand (updated by WS events via marketStore)
  const priceData = useMarketStore((s) => s.latestPrice[symbol]);
  const price = priceData?.price ?? null;

  // Track previous price to compute direction.
  // React allows calling setState during render when guarded by a changed-value
  // condition — this is the recommended "store derived-from-prev" pattern.
  const [prevPrice, setPrevPrice] = useState<number | null>(null);
  const [direction, setDirection] = useState<Direction>('flat');

  const priceNum = price !== null ? parseFloat(price) : null;

  if (priceNum !== null && priceNum !== prevPrice) {
    setPrevPrice(priceNum);
    if (prevPrice !== null) {
      setDirection(priceNum > prevPrice ? 'up' : priceNum < prevPrice ? 'down' : 'flat');
    }
  }

  const color =
    direction === 'up'
      ? 'success.main'
      : direction === 'down'
      ? 'error.main'
      : 'text.secondary';

  const Arrow =
    direction === 'up'
      ? ArrowDropUpIcon
      : direction === 'down'
      ? ArrowDropDownIcon
      : RemoveIcon;

  return (
    <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.25 }}>
      <Typography variant="caption" color="text.disabled" sx={{ lineHeight: 1 }}>
        {formatSymbol(symbol)}
      </Typography>
      <Typography variant="body2" color={color} sx={{ fontVariantNumeric: 'tabular-nums', lineHeight: 1 }}>
        {price !== null ? formatPrice(price) : '—'}
      </Typography>
      <Arrow sx={{ fontSize: 16, color }} />
    </Box>
  );
}
