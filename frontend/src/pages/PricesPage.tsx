import { useMemo } from 'react';
import Alert from '@mui/material/Alert';
import Box from '@mui/material/Box';
import Card from '@mui/material/Card';
import CardContent from '@mui/material/CardContent';
import Chip from '@mui/material/Chip';
import Grid from '@mui/material/Grid';
import Skeleton from '@mui/material/Skeleton';
import Typography from '@mui/material/Typography';

import { usePrice, useSymbols } from '../api';
import { useMarketStore } from '../stores/marketStore';
import type { PriceResponse } from '../types/market';

// ── Helpers ──────────────────────────────────────────────────────────────────

function fmt(value: string | null | undefined, decimals = 2): string {
  if (value == null) return '—';
  const n = parseFloat(value);
  return isNaN(n) ? '—' : n.toLocaleString(undefined, { minimumFractionDigits: decimals, maximumFractionDigits: decimals });
}

// ── PriceTickerCard ─────────────────────────────────────────────────────────

function PriceTickerCard({ symbol }: { symbol: string }) {
  const { isLoading, error } = usePrice(symbol);
  const storePrice = useMarketStore((s) => s.latestPrice[symbol]) as PriceResponse | undefined;

  if (isLoading) return <Skeleton variant="rounded" height={100} />;
  if (error) return <Alert severity="error" variant="outlined">Failed to load {symbol}</Alert>;

  const price = storePrice?.price;

  return (
    <Card>
      <CardContent>
        <Typography variant="body2" color="text.secondary" gutterBottom>
          {symbol}
        </Typography>
        <Typography variant="h5" sx={{ fontVariantNumeric: 'tabular-nums' }}>
          ${fmt(price, symbol.startsWith('BTC') ? 2 : 2)}
        </Typography>
        {storePrice?.timeframe && (
          <Chip label={storePrice.timeframe} size="small" sx={{ mt: 0.5 }} />
        )}
      </CardContent>
    </Card>
  );
}

// ── Page ─────────────────────────────────────────────────────────────────────

export default function PricesPage() {
  const { data: symbolsConfig, isLoading, error } = useSymbols();

  const symbols = useMemo(
    () => (symbolsConfig?.symbols ?? []).filter((s) => s.enabled).map((s) => s.symbol),
    [symbolsConfig],
  );

  if (isLoading) {
    return (
      <Box>
        <Typography variant="h5" sx={{ mb: 3 }}>Prices</Typography>
        <Grid container spacing={2}>
          {[0, 1, 2].map((i) => (
            <Grid size={{ xs: 12, sm: 6, md: 4 }} key={i}>
              <Skeleton variant="rounded" height={100} />
            </Grid>
          ))}
        </Grid>
      </Box>
    );
  }

  if (error) {
    return (
      <Box>
        <Typography variant="h5" sx={{ mb: 3 }}>Prices</Typography>
        <Alert severity="error" variant="outlined">Failed to load symbols</Alert>
      </Box>
    );
  }

  return (
    <Box>
      <Typography variant="h5" sx={{ mb: 3 }}>Prices</Typography>
      <Grid container spacing={2}>
        {symbols.map((symbol) => (
          <Grid size={{ xs: 12, sm: 6, md: 4 }} key={symbol}>
            <PriceTickerCard symbol={symbol} />
          </Grid>
        ))}
      </Grid>
    </Box>
  );
}
