import { useMemo, useState } from 'react';
import {
  createColumnHelper,
  flexRender,
  getCoreRowModel,
  getSortedRowModel,
  useReactTable,
  type SortingState,
} from '@tanstack/react-table';
import Alert from '@mui/material/Alert';
import Box from '@mui/material/Box';
import Card from '@mui/material/Card';
import CardContent from '@mui/material/CardContent';
import Chip from '@mui/material/Chip';
import Divider from '@mui/material/Divider';
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
import ArrowUpwardIcon from '@mui/icons-material/ArrowUpward';
import ArrowDownwardIcon from '@mui/icons-material/ArrowDownward';

import { useExchangeOrders, useTrades, useTradeStats } from '../api';
import { useTradesStore } from '../stores/tradesStore';
import type { ExchangeOrder } from '../types/orders';
import type { ExitReason } from '../types/enums';
import type { Trade } from '../types/trades';

// ── Shared helpers ──────────────────────────────────────────────────────────

function fmt(value: string | number | null | undefined, decimals = 2): string {
  if (value == null) return '—';
  const n = typeof value === 'number' ? value : parseFloat(value as string);
  return isNaN(n) ? '—' : n.toLocaleString(undefined, { minimumFractionDigits: decimals, maximumFractionDigits: decimals });
}

function pnlColor(value: string | null | undefined): string {
  if (value == null) return 'text.secondary';
  return parseFloat(value) >= 0 ? 'success.main' : 'error.main';
}

// ── Exchange Orders helpers ─────────────────────────────────────────────────

function sourceColor(source: ExchangeOrder['source']): 'primary' | 'warning' {
  return source === 'gg' ? 'primary' : 'warning';
}

function sideColor(side: string): 'success' | 'error' {
  return side === 'buy' ? 'success' : 'error';
}

interface ExchangeOrderFiltersState {
  source: string;
  symbol: string;
  status: string;
}

function ExchangeOrderFilters({ filters, onChange, orders }: {
  filters: ExchangeOrderFiltersState;
  onChange: (f: ExchangeOrderFiltersState) => void;
  orders: ExchangeOrder[];
}) {
  const symbols = useMemo(() => [...new Set(orders.map((o) => o.symbol))].sort(), [orders]);

  return (
    <Card sx={{ p: 2 }}>
      <Box sx={{ display: 'flex', gap: 2, flexWrap: 'wrap', alignItems: 'center' }}>
        <TextField
          select size="small" label="Source" value={filters.source}
          onChange={(e) => onChange({ ...filters, source: e.target.value })}
          sx={{ minWidth: 120 }}
        >
          <MenuItem value="">All</MenuItem>
          <MenuItem value="gg">GG</MenuItem>
          <MenuItem value="orphan">Orphan</MenuItem>
        </TextField>
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
          select size="small" label="Status" value={filters.status}
          onChange={(e) => onChange({ ...filters, status: e.target.value })}
          sx={{ minWidth: 140 }}
        >
          <MenuItem value="">All</MenuItem>
          <MenuItem value="open">Open</MenuItem>
          <MenuItem value="filled">Filled</MenuItem>
          <MenuItem value="cancelled">Cancelled</MenuItem>
        </TextField>
      </Box>
    </Card>
  );
}

function ExchangeOrderTable({ orders }: { orders: ExchangeOrder[] }) {
  if (orders.length === 0) {
    return (
      <Card sx={{ p: 2 }}>
        <Typography color="text.secondary" sx={{ py: 2, textAlign: 'center' }}>No orders</Typography>
      </Card>
    );
  }

  return (
    <Card>
      <CardContent sx={{ pb: '16px !important' }}>
        <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', mb: 1 }}>
          <Typography variant="body2" color="text.secondary">Exchange Orders</Typography>
          <Chip label={`${orders.length}`} size="small" variant="outlined" />
        </Box>
        <TableContainer>
          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell>Source</TableCell>
                <TableCell>Symbol</TableCell>
                <TableCell>Side</TableCell>
                <TableCell>Type</TableCell>
                <TableCell align="right">Qty</TableCell>
                <TableCell align="right">Filled</TableCell>
                <TableCell align="right">Price</TableCell>
                <TableCell>Status</TableCell>
                <TableCell>Intent</TableCell>
                <TableCell>Time</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {orders.map((o, i) => (
                <TableRow
                  key={i}
                  hover
                  sx={o.source === 'orphan' ? { bgcolor: 'rgba(255, 152, 0, 0.06)' } : undefined}
                >
                  <TableCell>
                    <Chip label={o.source.toUpperCase()} size="small" color={sourceColor(o.source)} variant="filled" />
                  </TableCell>
                  <TableCell>{o.symbol}</TableCell>
                  <TableCell>
                    <Chip label={o.side} size="small" color={sideColor(o.side)} variant="outlined" />
                  </TableCell>
                  <TableCell>{o.type}</TableCell>
                  <TableCell align="right" sx={{ fontVariantNumeric: 'tabular-nums' }}>{fmt(o.orig_qty, 4)}</TableCell>
                  <TableCell align="right" sx={{ fontVariantNumeric: 'tabular-nums' }}>{fmt(o.executed_qty, 4)}</TableCell>
                  <TableCell align="right" sx={{ fontVariantNumeric: 'tabular-nums' }}>
                    {o.price ? `$${fmt(o.price)}` : 'Market'}
                  </TableCell>
                  <TableCell>{o.status}</TableCell>
                  <TableCell>
                    {o.intent ? (
                      <Chip label={o.intent.replace(/_/g, ' ')} size="small" variant="outlined" />
                    ) : '—'}
                  </TableCell>
                  <TableCell sx={{ fontSize: '0.75rem', whiteSpace: 'nowrap' }}>
                    {o.time ? new Date(o.time).toLocaleString() : '—'}
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

// ── Orders section ──────────────────────────────────────────────────────────

function OrdersSection() {
  const [filters, setFilters] = useState<ExchangeOrderFiltersState>({ source: '', symbol: '', status: 'open' });

  const { data: orders = [], isLoading, error } = useExchangeOrders({
    source: filters.source || undefined,
    symbol: filters.symbol || undefined,
    status: filters.status || undefined,
    limit: 500,
  });

  if (isLoading) {
    return (
      <Grid container spacing={2}>
        {[0, 1].map((i) => (
          <Grid size={{ xs: 12 }} key={i}><Skeleton variant="rounded" height={180} /></Grid>
        ))}
      </Grid>
    );
  }

  if (error) {
    return <Alert severity="error" variant="outlined">Failed to load exchange orders</Alert>;
  }

  return (
    <Grid container spacing={2}>
      <Grid size={{ xs: 12 }}>
        <ExchangeOrderFilters filters={filters} onChange={setFilters} orders={orders} />
      </Grid>
      <Grid size={{ xs: 12 }}>
        <ExchangeOrderTable orders={orders} />
      </Grid>
    </Grid>
  );
}

// ── Trades helpers ──────────────────────────────────────────────────────────

function fmtDuration(minutes: number): string {
  if (minutes < 60) return `${minutes}m`;
  const h = Math.floor(minutes / 60);
  const m = minutes % 60;
  return m === 0 ? `${h}h` : `${h}h ${m}m`;
}

function exitReasonColor(reason: ExitReason): 'default' | 'success' | 'error' | 'warning' {
  if (reason === 'trailing_stop' || reason === 'hard_stop') return 'error';
  if (reason === 'ema_cross' || reason === 'middle_bb') return 'success';
  if (reason === 'time_stop') return 'warning';
  return 'default';
}

const EXIT_REASONS: ExitReason[] = [
  'ema_cross', 'close_below_ema200', 'momentum_fade',
  'trailing_stop', 'hard_stop', 'manual', 'time_stop', 'middle_bb',
];

const col = createColumnHelper<Trade>();

const COLUMNS = [
  col.accessor('symbol', { header: 'Symbol' }),
  col.accessor('strategy', {
    header: 'Strategy',
    cell: (info) => info.getValue().replace(/_/g, ' '),
  }),
  col.accessor('entry_price', {
    header: 'Entry $',
    cell: (info) => `$${fmt(info.getValue())}`,
  }),
  col.accessor('exit_price', {
    header: 'Exit $',
    cell: (info) => `$${fmt(info.getValue())}`,
  }),
  col.accessor('size', {
    header: 'Size',
    cell: (info) => fmt(info.getValue(), 4),
  }),
  col.accessor('pnl_usdt', {
    header: 'PnL ($)',
    cell: (info) => info.getValue(),
  }),
  col.accessor('pnl_percent', {
    header: 'PnL (%)',
    cell: (info) => info.getValue(),
  }),
  col.accessor('duration_minutes', {
    header: 'Duration',
    cell: (info) => fmtDuration(info.getValue()),
  }),
  col.accessor('exit_reason', {
    header: 'Exit Reason',
    cell: (info) => info.getValue(),
  }),
  col.accessor('exit_time', {
    header: 'Exit Time',
    cell: (info) => {
      const d = new Date(info.getValue());
      return d.toLocaleString(undefined, { dateStyle: 'short', timeStyle: 'short' });
    },
  }),
];

interface TradeFiltersState {
  symbol: string;
  strategy: string;
  exit_reason: string;
}

function TradeStatsCards({ filters }: { filters: TradeFiltersState }) {
  const params = {
    symbol: filters.symbol || undefined,
    strategy: filters.strategy || undefined,
    exit_reason: (filters.exit_reason || undefined) as ExitReason | undefined,
    limit: 10000,
  };
  const { isLoading, error } = useTradeStats(params);
  const stats = useTradesStore((s) => s.stats);

  if (isLoading) {
    return (
      <>
        {[0, 1, 2, 3].map((i) => (
          <Grid size={{ xs: 12, sm: 6, md: 3 }} key={i}>
            <Skeleton variant="rounded" height={110} />
          </Grid>
        ))}
      </>
    );
  }
  if (error) {
    return (
      <Grid size={{ xs: 12 }}>
        <Alert severity="error" variant="outlined">Failed to load trade stats</Alert>
      </Grid>
    );
  }
  if (!stats) return null;

  const cards = [
    { label: 'Total Trades', value: String(stats.total_trades), sub: `${stats.winning_trades}W / ${stats.losing_trades}L` },
    { label: 'Win Rate', value: `${fmt(stats.win_rate)}%`, color: parseFloat(stats.win_rate) >= 50 ? 'success.main' : 'error.main' },
    { label: 'Total PnL', value: `$${fmt(stats.total_pnl)}`, color: pnlColor(stats.total_pnl) },
    { label: 'Profit Factor', value: stats.profit_factor ? fmt(stats.profit_factor) : '—' },
  ];

  return (
    <>
      {cards.map((c) => (
        <Grid size={{ xs: 12, sm: 6, md: 3 }} key={c.label}>
          <Card>
            <CardContent>
              <Typography variant="body2" color="text.secondary" gutterBottom>{c.label}</Typography>
              <Typography variant="h5" color={c.color ?? 'text.primary'} sx={{ fontVariantNumeric: 'tabular-nums' }}>
                {c.value}
              </Typography>
              {c.sub && <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>{c.sub}</Typography>}
            </CardContent>
          </Card>
        </Grid>
      ))}
    </>
  );
}

function TradeFilters({ filters, onChange }: { filters: TradeFiltersState; onChange: (f: TradeFiltersState) => void }) {
  const trades = useTradesStore((s) => s.trades);

  const symbols = useMemo(() => [...new Set(trades.map((t) => t.symbol))].sort(), [trades]);
  const strategies = useMemo(() => [...new Set(trades.map((t) => t.strategy))].sort(), [trades]);

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
          select size="small" label="Strategy" value={filters.strategy}
          onChange={(e) => onChange({ ...filters, strategy: e.target.value })}
          sx={{ minWidth: 160 }}
        >
          <MenuItem value="">All</MenuItem>
          {strategies.map((s) => (
            <MenuItem key={s} value={s}>{s.replace(/_/g, ' ')}</MenuItem>
          ))}
        </TextField>
        <TextField
          select size="small" label="Exit Reason" value={filters.exit_reason}
          onChange={(e) => onChange({ ...filters, exit_reason: e.target.value })}
          sx={{ minWidth: 160 }}
        >
          <MenuItem value="">All</MenuItem>
          {EXIT_REASONS.map((r) => (
            <MenuItem key={r} value={r}>{r.replace(/_/g, ' ')}</MenuItem>
          ))}
        </TextField>
      </Box>
    </Card>
  );
}

function TradeHistoryTable({ filters }: { filters: TradeFiltersState }) {
  const [sorting, setSorting] = useState<SortingState>([{ id: 'exit_time', desc: true }]);

  const params = {
    symbol: filters.symbol || undefined,
    strategy: filters.strategy || undefined,
    exit_reason: (filters.exit_reason || undefined) as ExitReason | undefined,
    limit: 500,
  };
  const { isLoading, error } = useTrades(params);
  const trades = useTradesStore((s) => s.trades);

  const filteredData = useMemo(() => {
    return trades.filter((t) => {
      if (filters.symbol && t.symbol !== filters.symbol) return false;
      if (filters.strategy && t.strategy !== filters.strategy) return false;
      if (filters.exit_reason && t.exit_reason !== filters.exit_reason) return false;
      return true;
    });
  }, [trades, filters]);

  const table = useReactTable({
    data: filteredData,
    columns: COLUMNS,
    state: { sorting },
    onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
  });

  if (isLoading) return <Skeleton variant="rounded" height={300} />;
  if (error) return <Alert severity="error" variant="outlined">Failed to load trades</Alert>;

  if (filteredData.length === 0) {
    return (
      <Card sx={{ p: 2 }}>
        <Typography variant="body2" color="text.secondary" gutterBottom>Trade History</Typography>
        <Typography color="text.secondary" sx={{ py: 3, textAlign: 'center' }}>No trades match current filters</Typography>
      </Card>
    );
  }

  return (
    <Card>
      <CardContent sx={{ pb: '16px !important' }}>
        <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', mb: 1 }}>
          <Typography variant="body2" color="text.secondary">Trade History</Typography>
          <Chip label={`${filteredData.length} trades`} size="small" variant="outlined" />
        </Box>
        <TableContainer>
          <Table size="small">
            <TableHead>
              {table.getHeaderGroups().map((hg) => (
                <TableRow key={hg.id}>
                  {hg.headers.map((header) => (
                    <TableCell
                      key={header.id}
                      onClick={header.column.getToggleSortingHandler()}
                      sx={{ cursor: header.column.getCanSort() ? 'pointer' : 'default', userSelect: 'none', whiteSpace: 'nowrap' }}
                    >
                      <Box sx={{ display: 'inline-flex', alignItems: 'center', gap: 0.5 }}>
                        {flexRender(header.column.columnDef.header, header.getContext())}
                        {header.column.getIsSorted() === 'asc' && <ArrowUpwardIcon sx={{ fontSize: 12 }} />}
                        {header.column.getIsSorted() === 'desc' && <ArrowDownwardIcon sx={{ fontSize: 12 }} />}
                      </Box>
                    </TableCell>
                  ))}
                </TableRow>
              ))}
            </TableHead>
            <TableBody>
              {table.getRowModel().rows.map((row) => {
                const trade = row.original;
                return (
                  <TableRow key={row.id} hover>
                    {row.getVisibleCells().map((cell) => {
                      const id = cell.column.id;
                      const isPnlUsdt = id === 'pnl_usdt';
                      const isPnlPct = id === 'pnl_percent';
                      const isExitReason = id === 'exit_reason';
                      if (isPnlUsdt) {
                        return (
                          <TableCell key={cell.id} sx={{ color: pnlColor(trade.pnl_usdt), fontVariantNumeric: 'tabular-nums' }}>
                            ${fmt(trade.pnl_usdt)}
                          </TableCell>
                        );
                      }
                      if (isPnlPct) {
                        return (
                          <TableCell key={cell.id} sx={{ color: pnlColor(trade.pnl_percent), fontVariantNumeric: 'tabular-nums' }}>
                            {fmt(trade.pnl_percent)}%
                          </TableCell>
                        );
                      }
                      if (isExitReason) {
                        return (
                          <TableCell key={cell.id}>
                            <Chip
                              label={trade.exit_reason.replace(/_/g, ' ')}
                              size="small"
                              color={exitReasonColor(trade.exit_reason)}
                              variant="outlined"
                            />
                          </TableCell>
                        );
                      }
                      return (
                        <TableCell key={cell.id} sx={{ fontVariantNumeric: 'tabular-nums' }}>
                          {flexRender(cell.column.columnDef.cell, cell.getContext())}
                        </TableCell>
                      );
                    })}
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        </TableContainer>
      </CardContent>
    </Card>
  );
}

function TradeStatsDetail() {
  const stats = useTradesStore((s) => s.stats);
  if (!stats) return null;

  const rows = [
    { label: 'Avg Win', value: `${fmt(stats.avg_win_percent)}%`, color: 'success.main' },
    { label: 'Avg Loss', value: `${fmt(stats.avg_loss_percent)}%`, color: 'error.main' },
    { label: 'Avg Duration', value: stats.avg_duration_minutes != null ? fmtDuration(Math.round(stats.avg_duration_minutes)) : '—' },
    { label: 'Max Consecutive Wins', value: String(stats.max_consecutive_wins) },
    { label: 'Max Consecutive Losses', value: String(stats.max_consecutive_losses) },
  ];

  return (
    <Card>
      <CardContent>
        <Typography variant="body2" color="text.secondary" gutterBottom>Performance Detail</Typography>
        <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1 }}>
          {rows.map((r) => (
            <Box key={r.label} sx={{ display: 'flex', justifyContent: 'space-between' }}>
              <Typography variant="body2" color="text.secondary">{r.label}</Typography>
              <Typography variant="body2" color={r.color ?? 'text.primary'} sx={{ fontVariantNumeric: 'tabular-nums' }}>
                {r.value}
              </Typography>
            </Box>
          ))}
        </Box>
      </CardContent>
    </Card>
  );
}

// ── Trades section ──────────────────────────────────────────────────────────

function TradesSection() {
  const [filters, setFilters] = useState<TradeFiltersState>({ symbol: '', strategy: '', exit_reason: '' });

  return (
    <Grid container spacing={2}>
      <TradeStatsCards filters={filters} />
      <Grid size={{ xs: 12 }}>
        <TradeFilters filters={filters} onChange={setFilters} />
      </Grid>
      <Grid size={{ xs: 12 }}>
        <TradeHistoryTable filters={filters} />
      </Grid>
      <Grid size={{ xs: 12, md: 6 }}>
        <TradeStatsDetail />
      </Grid>
    </Grid>
  );
}

// ── Page ────────────────────────────────────────────────────────────────────

export default function ActivityPage() {
  return (
    <Box>
      <Typography variant="h5" sx={{ mb: 3 }}>Activity</Typography>

      <Typography variant="h6" color="text.secondary" sx={{ mb: 2 }}>Orders</Typography>
      <OrdersSection />

      <Divider sx={{ my: 4 }} />

      <Typography variant="h6" color="text.secondary" sx={{ mb: 2 }}>Trades</Typography>
      <TradesSection />
    </Box>
  );
}
