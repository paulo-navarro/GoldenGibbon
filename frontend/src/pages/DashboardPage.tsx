// ── DashboardPage ────────────────────────────────────────────────────────────
// Task 2.38 – Overview page: price tickers, portfolio summary, mini equity
// curve, open positions, recent signals, and system status.

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
import {
  Area,
  AreaChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';

import {
  usePrice,
  usePortfolio,
  useEquityCurve,
  useStrategySignals,
  useHealth,
} from '../api';
import { useMarketStore } from '../stores/marketStore';
import { useStrategyStore } from '../stores/strategyStore';
import type { PriceResponse } from '../types/market';
import type { Signal, StrategyState } from '../types/enums';

// ── Helpers ──────────────────────────────────────────────────────────────────

const FALLBACK_SYMBOLS = ['BTCUSDT', 'ETHUSDT'];

function fmt(value: string | null | undefined, decimals = 2): string {
  if (value == null) return '—';
  const n = parseFloat(value);
  return isNaN(n) ? '—' : n.toLocaleString(undefined, { minimumFractionDigits: decimals, maximumFractionDigits: decimals });
}

function pnlColor(value: string | null | undefined): string {
  if (value == null) return 'text.secondary';
  return parseFloat(value) >= 0 ? 'success.main' : 'error.main';
}

function signalColor(signal: Signal): 'success' | 'error' | 'default' {
  if (signal === 'buy') return 'success';
  if (signal === 'sell_full' || signal === 'sell_half') return 'error';
  return 'default';
}

function stateColor(state: StrategyState): 'primary' | 'success' | 'warning' | 'default' {
  if (state === 'position') return 'success';
  if (state === 'reduced') return 'warning';
  if (state === 'cooldown') return 'primary';
  return 'default';
}

// ── Sub-components ───────────────────────────────────────────────────────────

function PriceTickerCard({ symbol }: { symbol: string }) {
  const { isLoading, error } = usePrice(symbol);
  // Subscribe to store for real-time WS updates; falls back to REST data
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

function PortfolioCards() {
  const { data, isLoading, error } = usePortfolio();

  if (isLoading) {
    return (
      <>
        {[0, 1, 2].map((i) => (
          <Grid size={{ xs: 12, md: 4 }} key={i}>
            <Skeleton variant="rounded" height={120} />
          </Grid>
        ))}
      </>
    );
  }
  if (error) {
    return (
      <Grid size={{ xs: 12 }}>
        <Alert severity="error" variant="outlined">Failed to load portfolio</Alert>
      </Grid>
    );
  }
  if (!data) return null;

  return (
    <>
      {/* Balance */}
      <Grid size={{ xs: 12, md: 4 }}>
        <Card>
          <CardContent>
            <Typography variant="body2" color="text.secondary" gutterBottom>USDT Balance</Typography>
            <Typography variant="h5" sx={{ fontVariantNumeric: 'tabular-nums' }}>${fmt(data.usdt_balance)}</Typography>
            <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>
              Equity: ${fmt(data.equity)}
            </Typography>
          </CardContent>
        </Card>
      </Grid>

      {/* Positions Value */}
      <Grid size={{ xs: 12, md: 4 }}>
        <Card>
          <CardContent>
            <Typography variant="body2" color="text.secondary" gutterBottom>Positions Value</Typography>
            <Typography variant="h5" sx={{ fontVariantNumeric: 'tabular-nums' }}>${fmt(data.positions_value)}</Typography>
            <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>
              {data.open_positions_count} open position{data.open_positions_count !== 1 ? 's' : ''}
            </Typography>
          </CardContent>
        </Card>
      </Grid>

      {/* Total PnL */}
      <Grid size={{ xs: 12, md: 4 }}>
        <Card>
          <CardContent>
            <Typography variant="body2" color="text.secondary" gutterBottom>Total PnL</Typography>
            <Typography variant="h5" color={pnlColor(data.total_pnl)} sx={{ fontVariantNumeric: 'tabular-nums' }}>
              ${fmt(data.total_pnl)}
            </Typography>
            {data.last_updated && (
              <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>
                Updated {new Date(data.last_updated).toLocaleTimeString()}
              </Typography>
            )}
          </CardContent>
        </Card>
      </Grid>
    </>
  );
}

function MiniEquityCurve() {
  const { data, isLoading, error } = useEquityCurve({ limit: 100 });

  if (isLoading) return <Skeleton variant="rounded" height={200} />;
  if (error) return <Alert severity="error" variant="outlined">Failed to load equity curve</Alert>;
  if (!data || data.length === 0) {
    return (
      <Card sx={{ height: 200, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
        <Typography color="text.secondary">No equity data yet</Typography>
      </Card>
    );
  }

  const chartData = data.map((s) => ({
    time: new Date(s.timestamp).toLocaleDateString(undefined, { month: 'short', day: 'numeric' }),
    equity: parseFloat(s.total_equity),
  }));

  return (
    <Card sx={{ p: 2 }}>
      <Typography variant="body2" color="text.secondary" gutterBottom>
        Equity Curve
      </Typography>
      <ResponsiveContainer width="100%" height={160}>
        <AreaChart data={chartData} margin={{ top: 4, right: 4, bottom: 0, left: 0 }}>
          <defs>
            <linearGradient id="equityGrad" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="#00bcd4" stopOpacity={0.3} />
              <stop offset="100%" stopColor="#00bcd4" stopOpacity={0} />
            </linearGradient>
          </defs>
          <XAxis dataKey="time" tick={{ fontSize: 10 }} axisLine={false} tickLine={false} />
          <YAxis hide domain={['auto', 'auto']} />
          <Tooltip
            contentStyle={{ background: '#111720', border: '1px solid rgba(255,255,255,0.08)', borderRadius: 8, fontSize: 12 }}
            labelStyle={{ color: '#9e9e9e' }}
            formatter={(value) => [`$${Number(value).toLocaleString(undefined, { minimumFractionDigits: 2 })}`, 'Equity']}
          />
          <Area
            type="monotone"
            dataKey="equity"
            stroke="#00bcd4"
            strokeWidth={2}
            fill="url(#equityGrad)"
          />
        </AreaChart>
      </ResponsiveContainer>
    </Card>
  );
}

function OpenPositionsTable() {
  const { data } = usePortfolio();
  const positions = data?.positions ?? [];

  if (positions.length === 0) {
    return (
      <Card sx={{ p: 2 }}>
        <Typography variant="body2" color="text.secondary" gutterBottom>
          Open Positions
        </Typography>
        <Typography color="text.secondary" sx={{ py: 2, textAlign: 'center' }}>
          No open positions
        </Typography>
      </Card>
    );
  }

  return (
    <Card>
      <CardContent sx={{ pb: '16px !important' }}>
        <Typography variant="body2" color="text.secondary" gutterBottom>
          Open Positions
        </Typography>
        <TableContainer>
          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell>Symbol</TableCell>
                <TableCell align="right">Size</TableCell>
                <TableCell align="right">Entry</TableCell>
                <TableCell align="right">PnL %</TableCell>
                <TableCell align="right">Trail Stop</TableCell>
                <TableCell align="right">Hard Stop</TableCell>
                <TableCell align="center">Scale-ins</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {positions.map((p) => (
                <TableRow key={p.symbol} hover>
                  <TableCell>{p.symbol}</TableCell>
                  <TableCell align="right" sx={{ fontVariantNumeric: 'tabular-nums' }}>{fmt(p.size, 4)}</TableCell>
                  <TableCell align="right" sx={{ fontVariantNumeric: 'tabular-nums' }}>${fmt(p.entry_price)}</TableCell>
                  <TableCell align="right" sx={{ color: pnlColor(p.unrealized_pnl_percent), fontVariantNumeric: 'tabular-nums' }}>
                    {p.unrealized_pnl_percent != null ? `${fmt(p.unrealized_pnl_percent)}%` : '—'}
                  </TableCell>
                  <TableCell align="right" sx={{ fontVariantNumeric: 'tabular-nums' }}>${fmt(p.trailing_stop_price)}</TableCell>
                  <TableCell align="right" sx={{ fontVariantNumeric: 'tabular-nums' }}>${fmt(p.hard_stop_price)}</TableCell>
                  <TableCell align="center">{p.scale_in_count}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </TableContainer>
      </CardContent>
    </Card>
  );
}

function RecentSignals({ activeStrategy }: { activeStrategy: string }) {
  const { data, isLoading, error } = useStrategySignals();

  const filtered = useMemo(() => {
    if (!data) return [];
    if (activeStrategy === 'all') return data;
    return data.filter((s) => s.strategy === activeStrategy);
  }, [data, activeStrategy]);

  if (isLoading) return <Skeleton variant="rounded" height={180} />;
  if (error) return <Alert severity="error" variant="outlined">Failed to load signals</Alert>;
  if (filtered.length === 0) {
    return (
      <Card sx={{ p: 2, height: '100%' }}>
        <Typography variant="body2" color="text.secondary" gutterBottom>Recent Signals</Typography>
        <Typography color="text.secondary" sx={{ py: 2, textAlign: 'center' }}>No signals yet</Typography>
      </Card>
    );
  }

  return (
    <Card>
      <CardContent sx={{ pb: '16px !important' }}>
        <Typography variant="body2" color="text.secondary" gutterBottom>
          Recent Signals
        </Typography>
        <TableContainer>
          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell>Symbol</TableCell>
                <TableCell>Strategy</TableCell>
                <TableCell>State</TableCell>
                <TableCell>Signal</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {filtered.map((s) => (
                <TableRow key={`${s.symbol}:${s.strategy}`} hover>
                  <TableCell>{s.symbol}</TableCell>
                  <TableCell>{s.strategy}</TableCell>
                  <TableCell>
                    <Chip label={s.state} size="small" color={stateColor(s.state)} variant="outlined" />
                  </TableCell>
                  <TableCell>
                    <Chip label={s.signal} size="small" color={signalColor(s.signal)} variant="filled" />
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

function SystemStatus() {
  const { data, isLoading, error } = useHealth();

  if (isLoading) return <Skeleton variant="rounded" height={180} />;
  if (error) return <Alert severity="error" variant="outlined">Failed to load health</Alert>;
  if (!data) return null;

  const items: { label: string; ok: boolean }[] = [
    { label: 'API', ok: data.status === 'ok' },
    { label: 'Database', ok: data.db },
    { label: 'Redis', ok: data.redis },
  ];

  return (
    <Card sx={{ height: '100%' }}>
      <CardContent>
        <Typography variant="body2" color="text.secondary" gutterBottom>
          System Status
        </Typography>
        <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1.5, mt: 1 }}>
          {items.map((item) => (
            <Box key={item.label} sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
              <Box
                sx={{
                  width: 8,
                  height: 8,
                  borderRadius: '50%',
                  bgcolor: item.ok ? 'success.main' : 'error.main',
                  flexShrink: 0,
                }}
              />
              <Typography variant="body2">{item.label}</Typography>
              <Typography variant="body2" color="text.secondary" sx={{ ml: 'auto' }}>
                {item.ok ? 'Healthy' : 'Down'}
              </Typography>
            </Box>
          ))}
        </Box>
        {data.timestamp && (
          <Typography variant="body2" color="text.secondary" sx={{ mt: 2, fontSize: '0.75rem' }}>
            Last check: {new Date(data.timestamp).toLocaleTimeString()}
          </Typography>
        )}
      </CardContent>
    </Card>
  );
}

// ── Page ─────────────────────────────────────────────────────────────────────

export default function DashboardPage() {
  const [activeStrategy, setActiveStrategy] = useState('all');

  const storeSignals = useStrategyStore((s) => s.signals);

  const strategies = useMemo(
    () => [...new Set(Object.values(storeSignals).map((s) => s.strategy))].sort(),
    [storeSignals],
  );

  const symbols = useMemo(() => {
    const fromStore = Object.values(storeSignals)
      .filter((s) => activeStrategy === 'all' || s.strategy === activeStrategy)
      .map((s) => s.symbol);
    const unique = [...new Set(fromStore)].sort();
    return unique.length > 0 ? unique : FALLBACK_SYMBOLS;
  }, [storeSignals, activeStrategy]);

  return (
    <Box>
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 2, mb: 3 }}>
        <Typography variant="h5">Dashboard</Typography>
        <TextField
          select
          size="small"
          label="Strategy"
          value={activeStrategy}
          onChange={(e) => setActiveStrategy(e.target.value)}
          sx={{ width: 180 }}
        >
          <MenuItem value="all">All Strategies</MenuItem>
          {strategies.map((s) => (
            <MenuItem key={s} value={s}>{s.replace(/_/g, ' ')}</MenuItem>
          ))}
        </TextField>
      </Box>

      <Grid container spacing={2}>
        {/* ── Price Tickers ──────────────────────────────────────── */}
        {symbols.map((symbol) => (
          <Grid size={{ xs: 12, sm: 6 }} key={symbol}>
            <PriceTickerCard symbol={symbol} />
          </Grid>
        ))}

        {/* ── Portfolio Summary ───────────────────────────────────── */}
        <PortfolioCards />

        {/* ── Mini Equity Curve ───────────────────────────────────── */}
        <Grid size={{ xs: 12 }}>
          <MiniEquityCurve />
        </Grid>

        {/* ── Open Positions ─────────────────────────────────────── */}
        <Grid size={{ xs: 12 }}>
          <OpenPositionsTable />
        </Grid>

        {/* ── Recent Signals + System Status ──────────────────────── */}
        <Grid size={{ xs: 12, md: 6 }}>
          <RecentSignals activeStrategy={activeStrategy} />
        </Grid>
        <Grid size={{ xs: 12, md: 6 }}>
          <SystemStatus />
        </Grid>
      </Grid>
    </Box>
  );
}
