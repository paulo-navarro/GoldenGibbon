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
  usePrice,
  usePortfolio,
  useStrategySignals,
  useSymbols,
} from '../api';
import CycleStatus from '../components/CycleStatus';
import EquityCurveChart from '../components/EquityCurveChart';
import { useMarketStore } from '../stores/marketStore';
import { useStrategyStore } from '../stores/strategyStore';
import type { PriceResponse } from '../types/market';
import type { Signal, StrategyState } from '../types/enums';

// ── Helpers ──────────────────────────────────────────────────────────────────


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

function RecentSignals({
  strategies,
  activeStrategy,
  setActiveStrategy,
}: {
  strategies: string[];
  activeStrategy: string;
  setActiveStrategy: (v: string) => void;
}) {
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
        <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', mb: 1 }}>
          <Typography variant="body2" color="text.secondary">Recent Signals</Typography>
          <TextField
            select
            size="small"
            label="Strategy"
            value={activeStrategy}
            onChange={(e) => setActiveStrategy(e.target.value)}
            sx={{ width: 160 }}
          >
            <MenuItem value="all">All</MenuItem>
            {strategies.map((s) => (
              <MenuItem key={s} value={s}>{s.replace(/_/g, ' ')}</MenuItem>
            ))}
          </TextField>
        </Box>
        <Typography color="text.secondary" sx={{ py: 2, textAlign: 'center' }}>No signals yet</Typography>
      </Card>
    );
  }

  return (
    <Card>
      <CardContent sx={{ pb: '16px !important' }}>
        <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', mb: 1 }}>
          <Typography variant="body2" color="text.secondary">Recent Signals</Typography>
          <TextField
            select
            size="small"
            label="Strategy"
            value={activeStrategy}
            onChange={(e) => setActiveStrategy(e.target.value)}
            sx={{ width: 160 }}
          >
            <MenuItem value="all">All</MenuItem>
            {strategies.map((s) => (
              <MenuItem key={s} value={s}>{s.replace(/_/g, ' ')}</MenuItem>
            ))}
          </TextField>
        </Box>
        <TableContainer>
          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell>Symbol</TableCell>
                <TableCell>Strategy</TableCell>
                <TableCell>State</TableCell>
                <TableCell>Signal</TableCell>
                <TableCell>Updated</TableCell>
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
                  <TableCell>
                    <Typography variant="body2" sx={{ fontSize: '0.75rem', whiteSpace: 'nowrap' }}>
                      {new Date(s.updated_at).toLocaleString()}
                    </Typography>
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

// ── Page ─────────────────────────────────────────────────────────────────────

export default function DashboardPage() {
  const [activeStrategy, setActiveStrategy] = useState('all');

  const storeSignals = useStrategyStore((s) => s.signals);
  const { data: symbolsConfig } = useSymbols();

  const strategies = useMemo(
    () => [...new Set(Object.values(storeSignals).map((s) => s.strategy))].sort(),
    [storeSignals],
  );

  const configSymbols = useMemo(
    () => (symbolsConfig?.symbols ?? []).filter((s) => s.enabled).map((s) => s.symbol),
    [symbolsConfig],
  );

  const symbols = useMemo(() => {
    const fromStore = Object.values(storeSignals)
      .filter((s) => activeStrategy === 'all' || s.strategy === activeStrategy)
      .map((s) => s.symbol);
    const unique = [...new Set(fromStore)].sort();
    return unique.length > 0 ? unique : configSymbols;
  }, [storeSignals, activeStrategy, configSymbols]);

  return (
    <Box>
      <Typography variant="h5" sx={{ mb: 3 }}>Dashboard</Typography>

      <Grid container spacing={2}>
        {/* ── Price Tickers ──────────────────────────────────────── */}
        {symbols.map((symbol) => (
          <Grid size={{ xs: 12, sm: 6 }} key={symbol}>
            <PriceTickerCard symbol={symbol} />
          </Grid>
        ))}

        {/* ── Portfolio Summary ───────────────────────────────────── */}
        <PortfolioCards />

        {/* ── Cycle Status ────────────────────────────────────────── */}
        <Grid size={{ xs: 12 }}>
          <CycleStatus />
        </Grid>

        {/* ── Mini Equity Curve ───────────────────────────────────── */}
        <Grid size={{ xs: 12 }}>
          <EquityCurveChart limit={100} height={160} mini />
        </Grid>

        {/* ── Open Positions ─────────────────────────────────────── */}
        <Grid size={{ xs: 12 }}>
          <OpenPositionsTable />
        </Grid>

        {/* ── Recent Signals ──────────────────────────────────────── */}
        <Grid size={{ xs: 12 }}>
          <RecentSignals strategies={strategies} activeStrategy={activeStrategy} setActiveStrategy={setActiveStrategy} />
        </Grid>
      </Grid>
    </Box>
  );
}
