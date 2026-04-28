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
import TablePagination from '@mui/material/TablePagination';
import TableRow from '@mui/material/TableRow';
import TextField from '@mui/material/TextField';
import Typography from '@mui/material/Typography';

import {
  usePortfolio,
  useStrategySignals,
} from '../api';
import CycleStatus from '../components/CycleStatus';
import EquityCurveChart from '../components/EquityCurveChart';
import { useStrategyStore } from '../stores/strategyStore';
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

const SIGNAL_TYPES: Array<Signal | 'all'> = ['all', 'buy', 'sell_full', 'sell_half', 'hold'];

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
  const [activeSignalType, setActiveSignalType] = useState<Signal | 'all'>('all');
  const [page, setPage] = useState(0);
  const [rowsPerPage, setRowsPerPage] = useState(10);

  const filtered = useMemo(() => {
    if (!data) return [];
    let result = data;
    if (activeStrategy !== 'all') result = result.filter((s) => s.strategy === activeStrategy);
    if (activeSignalType !== 'all') result = result.filter((s) => s.signal === activeSignalType);
    return result;
  }, [data, activeStrategy, activeSignalType]);

  const paged = useMemo(
    () => filtered.slice(page * rowsPerPage, (page + 1) * rowsPerPage),
    [filtered, page, rowsPerPage],
  );

  const handleStrategyChange = (v: string) => { setActiveStrategy(v); setPage(0); };
  const handleSignalTypeChange = (v: Signal | 'all') => { setActiveSignalType(v); setPage(0); };

  if (isLoading) return <Skeleton variant="rounded" height={180} />;
  if (error) return <Alert severity="error" variant="outlined">Failed to load signals</Alert>;

  const filters = (
    <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', mb: 1 }}>
      <Typography variant="body2" color="text.secondary">Recent Signals</Typography>
      <Box sx={{ display: 'flex', gap: 1 }}>
        <TextField
          select
          size="small"
          label="Strategy"
          value={activeStrategy}
          onChange={(e) => handleStrategyChange(e.target.value)}
          sx={{ width: 160 }}
        >
          <MenuItem value="all">All</MenuItem>
          {strategies.map((s) => (
            <MenuItem key={s} value={s}>{s.replace(/_/g, ' ')}</MenuItem>
          ))}
        </TextField>
        <TextField
          select
          size="small"
          label="Signal"
          value={activeSignalType}
          onChange={(e) => handleSignalTypeChange(e.target.value as Signal | 'all')}
          sx={{ width: 140 }}
        >
          {SIGNAL_TYPES.map((t) => (
            <MenuItem key={t} value={t}>{t === 'all' ? 'All' : t.replace(/_/g, ' ')}</MenuItem>
          ))}
        </TextField>
      </Box>
    </Box>
  );

  if (filtered.length === 0) {
    return (
      <Card sx={{ p: 2, height: '100%' }}>
        {filters}
        <Typography color="text.secondary" sx={{ py: 2, textAlign: 'center' }}>No signals yet</Typography>
      </Card>
    );
  }

  return (
    <Card>
      <CardContent sx={{ pb: '0 !important' }}>
        {filters}
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
              {paged.map((s) => (
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
        <TablePagination
          component="div"
          count={filtered.length}
          page={page}
          onPageChange={(_, p) => setPage(p)}
          rowsPerPage={rowsPerPage}
          onRowsPerPageChange={(e) => { setRowsPerPage(parseInt(e.target.value, 10)); setPage(0); }}
          rowsPerPageOptions={[10, 25, 50]}
        />
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

  return (
    <Box>
      <Typography variant="h5" sx={{ mb: 3 }}>Dashboard</Typography>

      <Grid container spacing={2}>
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
