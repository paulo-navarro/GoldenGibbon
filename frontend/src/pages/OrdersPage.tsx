// ── OrdersPage ────────────────────────────────────────────────────────────────
// Task 2.42 – Active orders, recent fills, and execution stats.

import { useMemo, useState } from 'react';
import Alert from '@mui/material/Alert';
import Box from '@mui/material/Box';
import Card from '@mui/material/Card';
import CardContent from '@mui/material/CardContent';
import Chip from '@mui/material/Chip';
import Grid from '@mui/material/Grid';
import MenuItem from '@mui/material/MenuItem';
import Skeleton from '@mui/material/Skeleton';
import Table from '@mui/material/Table';
import TableBody from '@mui/material/TableBody';
import TableCell from '@mui/material/TableCell';
import TableContainer from '@mui/material/TableContainer';
import TableHead from '@mui/material/TableHead';
import TableRow from '@mui/material/TableRow';
import TextField from '@mui/material/TextField';
import Typography from '@mui/material/Typography';

import { useOrders } from '../api';
import { useOrdersStore } from '../stores/ordersStore';
import type { Order } from '../types/orders';
import type { OrderSide, OrderStatus } from '../types/enums';

// ── Helpers ──────────────────────────────────────────────────────────────────

function fmt(value: string | null | undefined, decimals = 2): string {
  if (value == null) return '—';
  const n = parseFloat(value);
  return isNaN(n) ? '—' : n.toLocaleString(undefined, { minimumFractionDigits: decimals, maximumFractionDigits: decimals });
}

function statusColor(status: OrderStatus): 'default' | 'success' | 'warning' | 'error' | 'primary' {
  if (status === 'filled') return 'success';
  if (status === 'partial') return 'warning';
  if (status === 'pending') return 'primary';
  if (status === 'rejected' || status === 'cancelled') return 'error';
  return 'default';
}

function sideColor(side: OrderSide): 'success' | 'error' {
  return side === 'buy' ? 'success' : 'error';
}

const ACTIVE_STATUSES: OrderStatus[] = ['pending', 'partial'];
const CLOSED_STATUSES: OrderStatus[] = ['filled', 'rejected', 'cancelled'];

// ── Sub-components ────────────────────────────────────────────────────────────

interface Filters {
  symbol: string;
  side: string;
  status: string;
}

function OrderFilters({ filters, onChange }: { filters: Filters; onChange: (f: Filters) => void }) {
  const orders = useOrdersStore((s) => s.orders);
  const symbols = useMemo(() => [...new Set(orders.map((o) => o.symbol))].sort(), [orders]);

  return (
    <Card sx={{ p: 2 }}>
      <Box sx={{ display: 'flex', gap: 2, flexWrap: 'wrap', alignItems: 'center' }}>
        <TextField
          select size="small" label="Symbol" value={filters.symbol}
          onChange={(e) => onChange({ ...filters, symbol: e.target.value })}
          sx={{ minWidth: 140 }}
        >
          <MenuItem value="">All</MenuItem>
          {symbols.map((s) => (
            <MenuItem key={s} value={s}>{s}</MenuItem>
          ))}
        </TextField>
        <TextField
          select size="small" label="Side" value={filters.side}
          onChange={(e) => onChange({ ...filters, side: e.target.value })}
          sx={{ minWidth: 120 }}
        >
          <MenuItem value="">All</MenuItem>
          <MenuItem value="buy">Buy</MenuItem>
          <MenuItem value="sell">Sell</MenuItem>
        </TextField>
        <TextField
          select size="small" label="Status" value={filters.status}
          onChange={(e) => onChange({ ...filters, status: e.target.value })}
          sx={{ minWidth: 140 }}
        >
          <MenuItem value="">All</MenuItem>
          {(['pending', 'partial', 'filled', 'rejected', 'cancelled'] as OrderStatus[]).map((s) => (
            <MenuItem key={s} value={s}>{s}</MenuItem>
          ))}
        </TextField>
      </Box>
    </Card>
  );
}

function applyFilters(orders: Order[], filters: Filters): Order[] {
  return orders
    .filter((o) => {
      if (filters.symbol && o.symbol !== filters.symbol) return false;
      if (filters.side && o.side !== filters.side) return false;
      if (filters.status && o.status !== filters.status) return false;
      return true;
    })
    .sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime());
}

function OrderTable({ title, orders, showFillDetail }: { title: string; orders: Order[]; showFillDetail?: boolean }) {
  if (orders.length === 0) {
    return (
      <Card sx={{ p: 2 }}>
        <Typography variant="body2" color="text.secondary" gutterBottom>{title}</Typography>
        <Typography color="text.secondary" sx={{ py: 2, textAlign: 'center' }}>No orders</Typography>
      </Card>
    );
  }

  return (
    <Card>
      <CardContent sx={{ pb: '16px !important' }}>
        <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', mb: 1 }}>
          <Typography variant="body2" color="text.secondary">{title}</Typography>
          <Chip label={`${orders.length}`} size="small" variant="outlined" />
        </Box>
        <TableContainer>
          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell>Symbol</TableCell>
                <TableCell>Side</TableCell>
                <TableCell>Type</TableCell>
                <TableCell align="right">Amount</TableCell>
                <TableCell align="right">Price</TableCell>
                <TableCell align="right">Filled</TableCell>
                <TableCell>Status</TableCell>
                {showFillDetail && <TableCell align="right">Fill Price</TableCell>}
                {showFillDetail && <TableCell align="right">Slippage %</TableCell>}
                {showFillDetail && <TableCell align="right">Fee (USDT)</TableCell>}
                <TableCell>Time</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {orders.map((o, i) => (
                <TableRow key={i} hover>
                  <TableCell>{o.symbol}</TableCell>
                  <TableCell>
                    <Chip label={o.side} size="small" color={sideColor(o.side)} variant="outlined" />
                  </TableCell>
                  <TableCell>
                    <Chip label={o.order_type} size="small" variant="outlined" />
                  </TableCell>
                  <TableCell align="right" sx={{ fontVariantNumeric: 'tabular-nums' }}>{fmt(o.amount, 4)}</TableCell>
                  <TableCell align="right" sx={{ fontVariantNumeric: 'tabular-nums' }}>
                    {o.price ? `$${fmt(o.price)}` : 'Market'}
                  </TableCell>
                  <TableCell align="right" sx={{ fontVariantNumeric: 'tabular-nums' }}>{fmt(o.filled_amount, 4)}</TableCell>
                  <TableCell>
                    <Chip label={o.status} size="small" color={statusColor(o.status)} variant="filled" />
                  </TableCell>
                  {showFillDetail && (
                    <TableCell align="right" sx={{ fontVariantNumeric: 'tabular-nums' }}>
                      {o.avg_fill_price ? `$${fmt(o.avg_fill_price)}` : '—'}
                    </TableCell>
                  )}
                  {showFillDetail && (
                    <TableCell align="right" sx={{ fontVariantNumeric: 'tabular-nums' }}>
                      {o.slippage_percent ? `${fmt(o.slippage_percent, 4)}%` : '—'}
                    </TableCell>
                  )}
                  {showFillDetail && (
                    <TableCell align="right" sx={{ fontVariantNumeric: 'tabular-nums' }}>
                      {o.fee_usdt ? `$${fmt(o.fee_usdt)}` : '—'}
                    </TableCell>
                  )}
                  <TableCell sx={{ fontSize: '0.75rem', whiteSpace: 'nowrap' }}>
                    {new Date(o.created_at).toLocaleString()}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </TableContainer>
      </CardContent>
    </Card>
  );
}

function ExecutionStats({ orders }: { orders: Order[] }) {
  const stats = useMemo(() => {
    const total = orders.length;
    if (total === 0) return null;
    const filled = orders.filter((o) => o.status === 'filled').length;
    const fillRate = total > 0 ? (filled / total) * 100 : 0;
    const withSlippage = orders.filter((o) => o.slippage_percent != null);
    const avgSlippage =
      withSlippage.length > 0
        ? withSlippage.reduce((s, o) => s + parseFloat(o.slippage_percent!), 0) / withSlippage.length
        : null;
    const totalFees = orders.reduce((s, o) => s + (o.fee_usdt ? parseFloat(o.fee_usdt) : 0), 0);
    return { total, filled, fillRate, avgSlippage, totalFees };
  }, [orders]);

  if (!stats) return null;

  const rows = [
    { label: 'Total Orders', value: String(stats.total) },
    { label: 'Filled', value: `${stats.filled} / ${stats.total}` },
    { label: 'Fill Rate', value: `${stats.fillRate.toFixed(1)}%` },
    { label: 'Avg Slippage', value: stats.avgSlippage != null ? `${stats.avgSlippage.toFixed(4)}%` : '—' },
    { label: 'Total Fees', value: `$${stats.totalFees.toFixed(4)}` },
  ];

  return (
    <Card>
      <CardContent>
        <Typography variant="body2" color="text.secondary" gutterBottom>Execution Stats</Typography>
        <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1 }}>
          {rows.map((r) => (
            <Box key={r.label} sx={{ display: 'flex', justifyContent: 'space-between' }}>
              <Typography variant="body2" color="text.secondary">{r.label}</Typography>
              <Typography variant="body2" sx={{ fontVariantNumeric: 'tabular-nums' }}>{r.value}</Typography>
            </Box>
          ))}
        </Box>
      </CardContent>
    </Card>
  );
}

// ── Page ──────────────────────────────────────────────────────────────────────

export default function OrdersPage() {
  const [filters, setFilters] = useState<Filters>({ symbol: '', side: '', status: '' });

  const { isLoading, error } = useOrders({
    symbol: filters.symbol || undefined,
    side: (filters.side || undefined) as OrderSide | undefined,
    status: (filters.status || undefined) as OrderStatus | undefined,
    limit: 500,
  });
  const allOrders = useOrdersStore((s) => s.orders);

  const filtered = useMemo(() => applyFilters(allOrders, filters), [allOrders, filters]);
  const activeOrders = useMemo(() => filtered.filter((o) => ACTIVE_STATUSES.includes(o.status)), [filtered]);
  const closedOrders = useMemo(() => filtered.filter((o) => CLOSED_STATUSES.includes(o.status)), [filtered]);

  if (isLoading) {
    return (
      <Box>
        <Typography variant="h5" sx={{ mb: 3 }}>Orders</Typography>
        <Grid container spacing={2}>
          {[0, 1, 2].map((i) => (
            <Grid size={{ xs: 12 }} key={i}><Skeleton variant="rounded" height={180} /></Grid>
          ))}
        </Grid>
      </Box>
    );
  }

  if (error) {
    return (
      <Box>
        <Typography variant="h5" sx={{ mb: 3 }}>Orders</Typography>
        <Alert severity="error" variant="outlined">Failed to load orders</Alert>
      </Box>
    );
  }

  return (
    <Box>
      <Typography variant="h5" sx={{ mb: 3 }}>Orders</Typography>

      <Grid container spacing={2}>
        {/* ── Filters ────────────────────────────────────────────── */}
        <Grid size={{ xs: 12 }}>
          <OrderFilters filters={filters} onChange={setFilters} />
        </Grid>

        {/* ── Active Orders ───────────────────────────────────────── */}
        <Grid size={{ xs: 12 }}>
          <OrderTable title="Active Orders" orders={activeOrders} />
        </Grid>

        {/* ── Recent Fills ────────────────────────────────────────── */}
        <Grid size={{ xs: 12 }}>
          <OrderTable title="Recent Fills & Closed" orders={closedOrders} showFillDetail />
        </Grid>

        {/* ── Execution Stats ─────────────────────────────────────── */}
        <Grid size={{ xs: 12, md: 5 }}>
          <ExecutionStats orders={filtered} />
        </Grid>
      </Grid>
    </Box>
  );
}
