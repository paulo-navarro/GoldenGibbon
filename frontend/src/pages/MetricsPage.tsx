// ── MetricsPage ───────────────────────────────────────────────────────────────
// Task 2.43 – Equity curve, drawdown chart, win rate, total return,
// drawdown, and Sharpe ratio from trade stats and equity curve data.

import { useCallback, useMemo, useState } from 'react';
import Alert from '@mui/material/Alert';
import Box from '@mui/material/Box';
import Button from '@mui/material/Button';
import Card from '@mui/material/Card';
import CardContent from '@mui/material/CardContent';
import Chip from '@mui/material/Chip';
import CircularProgress from '@mui/material/CircularProgress';
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

import Tabs from '@mui/material/Tabs';
import Tab from '@mui/material/Tab';
import CompareArrowsIcon from '@mui/icons-material/CompareArrows';
import LayersIcon from '@mui/icons-material/Layers';
import TuneIcon from '@mui/icons-material/Tune';
import WarningAmberIcon from '@mui/icons-material/WarningAmber';
import {
  Area,
  AreaChart,
  Legend,
  Line,
  LineChart,
  ReferenceArea,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';

import { useTradeStats, useCompare, useMultiStrategy, useStrategyList, useOptimize, useWalkForward } from '../api';
import EquityCurveChart from '../components/EquityCurveChart';
import type { ComparisonMetricsRow, GridSearchRow, WalkForwardFold } from '../api';
import { useTradesStore } from '../stores/tradesStore';
import { usePortfolioStore } from '../stores/portfolioStore';
import { finiteNumber, isPlausiblePercent, MAX_PLAUSIBLE_PERCENT } from '../utils/sanitize';
import type { PortfolioSnapshot } from '../types/portfolio';

// ── Helpers ──────────────────────────────────────────────────────────────────

function fmt(value: string | number | null | undefined, decimals = 2): string {
  if (value == null) return '—';
  const n = typeof value === 'number' ? value : parseFloat(value as string);
  return isNaN(n) ? '—' : n.toLocaleString(undefined, { minimumFractionDigits: decimals, maximumFractionDigits: decimals });
}

function pnlColor(value: string | number | null | undefined): string {
  if (value == null) return 'text.secondary';
  const n = typeof value === 'number' ? value : parseFloat(value as string);
  return n >= 0 ? 'success.main' : 'error.main';
}

const CHART_TOOLTIP_STYLE = {
  background: '#111720',
  border: '1px solid rgba(255,255,255,0.08)',
  borderRadius: 8,
  fontSize: 12,
};

/** Compute max drawdown series (%) from equity snapshots. */
function computeDrawdown(snapshots: PortfolioSnapshot[]): { time: string; drawdown: number }[] {
  let peak = 0;
  return snapshots.map((s) => {
    const equity = parseFloat(s.total_equity);
    if (equity > peak) peak = equity;
    const drawdown = peak > 0 ? ((equity - peak) / peak) * 100 : 0;
    return {
      time: new Date(s.timestamp).toLocaleDateString(undefined, { month: 'short', day: 'numeric' }),
      drawdown: parseFloat(drawdown.toFixed(2)),
    };
  });
}

/** Compute annualised Sharpe ratio from daily equity snapshots (risk-free = 0). */
function computeSharpe(snapshots: PortfolioSnapshot[]): number | null {
  if (snapshots.length < 2) return null;
  const returns: number[] = [];
  for (let i = 1; i < snapshots.length; i++) {
    const prev = parseFloat(snapshots[i - 1].total_equity);
    const curr = parseFloat(snapshots[i].total_equity);
    if (prev > 0) returns.push((curr - prev) / prev);
  }
  if (returns.length < 2) return null;
  const mean = returns.reduce((a, b) => a + b, 0) / returns.length;
  const variance = returns.reduce((a, b) => a + (b - mean) ** 2, 0) / returns.length;
  const std = Math.sqrt(variance);
  if (std === 0) return null;
  return parseFloat(((mean / std) * Math.sqrt(252)).toFixed(2));
}

/** Max drawdown percentage from equity snapshots. */
function computeMaxDrawdown(snapshots: PortfolioSnapshot[]): number | null {
  if (snapshots.length < 2) return null;
  let peak = 0;
  let maxDD = 0;
  for (const s of snapshots) {
    const eq = parseFloat(s.total_equity);
    if (eq > peak) peak = eq;
    const dd = peak > 0 ? ((eq - peak) / peak) * 100 : 0;
    if (dd < maxDD) maxDD = dd;
  }
  return parseFloat(maxDD.toFixed(2));
}

// ── Sub-components ────────────────────────────────────────────────────────────

function MetricCards() {
  const { isLoading, error } = useTradeStats({ limit: 10000 });
  const stats = useTradesStore((s) => s.stats);
  const equityCurve = usePortfolioStore((s) => s.equityCurve);

  const sharpe = useMemo(() => computeSharpe(equityCurve), [equityCurve]);
  const maxDD = useMemo(() => computeMaxDrawdown(equityCurve), [equityCurve]);

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
        <Alert severity="error" variant="outlined">Failed to load metrics</Alert>
      </Grid>
    );
  }

  const cards: { label: string; value: string; sub?: string; color?: string }[] = [
    {
      label: 'Win Rate',
      value: stats ? `${fmt(stats.win_rate)}%` : '—',
      sub: stats ? `${stats.winning_trades}W / ${stats.losing_trades}L` : undefined,
      color: stats ? (parseFloat(stats.win_rate) >= 50 ? 'success.main' : 'error.main') : undefined,
    },
    {
      label: 'Total PnL',
      value: stats ? `$${fmt(stats.total_pnl)}` : '—',
      color: stats ? pnlColor(stats.total_pnl) : undefined,
    },
    {
      label: 'Max Drawdown',
      value: maxDD != null ? `${fmt(maxDD)}%` : '—',
      color: maxDD != null && maxDD < 0 ? 'error.main' : 'text.secondary',
    },
    {
      label: 'Sharpe Ratio',
      value: sharpe != null ? fmt(sharpe) : '—',
      color: sharpe != null ? (sharpe >= 1 ? 'success.main' : sharpe >= 0 ? 'warning.main' : 'error.main') : undefined,
      sub: 'annualised',
    },
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


function DrawdownChart() {
  const equityCurve = usePortfolioStore((s) => s.equityCurve);

  const chartData = useMemo(() => computeDrawdown(equityCurve), [equityCurve]);

  if (equityCurve.length < 2) return null;

  return (
    <Card sx={{ p: 2 }}>
      <Typography variant="body2" color="text.secondary" gutterBottom>Drawdown (%)</Typography>
      <ResponsiveContainer width="100%" height={200}>
        <AreaChart data={chartData} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
          <defs>
            <linearGradient id="drawdownGrad" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="#f44336" stopOpacity={0} />
              <stop offset="100%" stopColor="#f44336" stopOpacity={0.3} />
            </linearGradient>
          </defs>
          <XAxis dataKey="time" tick={{ fontSize: 11 }} axisLine={false} tickLine={false} />
          <YAxis
            tick={{ fontSize: 11 }} axisLine={false} tickLine={false}
            domain={['auto', 0]}
            tickFormatter={(v: number) => `${v.toFixed(1)}%`}
          />
          <Tooltip
            contentStyle={CHART_TOOLTIP_STYLE}
            labelStyle={{ color: '#9e9e9e' }}
            formatter={(v) => [`${Number(v).toFixed(2)}%`, 'Drawdown']}
          />
          <Area type="monotone" dataKey="drawdown" stroke="#f44336" strokeWidth={2} fill="url(#drawdownGrad)" isAnimationActive={false} />
        </AreaChart>
      </ResponsiveContainer>
    </Card>
  );
}

function DetailedStatsTable() {
  const stats = useTradesStore((s) => s.stats);
  if (!stats) return null;

  const rows = [
    { label: 'Total Trades', value: String(stats.total_trades) },
    { label: 'Winning Trades', value: String(stats.winning_trades), color: 'success.main' },
    { label: 'Losing Trades', value: String(stats.losing_trades), color: 'error.main' },
    { label: 'Win Rate', value: `${fmt(stats.win_rate)}%` },
    { label: 'Avg Win', value: `${fmt(stats.avg_win_percent)}%`, color: 'success.main' },
    { label: 'Avg Loss', value: `${fmt(stats.avg_loss_percent)}%`, color: 'error.main' },
    { label: 'Profit Factor', value: stats.profit_factor ? fmt(stats.profit_factor) : '—' },
    { label: 'Avg Duration', value: stats.avg_duration_minutes != null ? `${Math.round(stats.avg_duration_minutes)} min` : '—' },
    { label: 'Max Consecutive Wins', value: String(stats.max_consecutive_wins), color: 'success.main' },
    { label: 'Max Consecutive Losses', value: String(stats.max_consecutive_losses), color: 'error.main' },
    { label: 'Total PnL', value: `$${fmt(stats.total_pnl)}`, color: pnlColor(stats.total_pnl) },
  ];

  return (
    <Card>
      <CardContent>
        <Typography variant="body2" color="text.secondary" gutterBottom>All Metrics</Typography>
        <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1 }}>
          {rows.map((r) => (
            <Box key={r.label} sx={{ display: 'flex', justifyContent: 'space-between', borderBottom: '1px solid rgba(255,255,255,0.05)', pb: 0.5 }}>
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

// ── Strategy Comparison (task 5.3) ────────────────────────────────────────────

const DAYS_OPTIONS = [30, 60, 90, 180, 365];

const COMPARISON_COLORS = ['#00bcd4', '#ff9800', '#7c4dff', '#4caf50', '#f44336', '#e91e63', '#009688', '#ffc107'];

function bestInColumn(rows: ComparisonMetricsRow[], key: keyof ComparisonMetricsRow, higher = true): string | null {
  if (rows.length < 2) return null;
  let bestIdx = 0;
  let bestVal = parseFloat(rows[0][key] as string);
  for (let i = 1; i < rows.length; i++) {
    const v = parseFloat(rows[i][key] as string);
    if (isNaN(v)) continue;
    if ((higher && v > bestVal) || (!higher && v < bestVal)) {
      bestVal = v;
      bestIdx = i;
    }
  }
  return `${rows[bestIdx].strategy}:${rows[bestIdx].symbol}`;
}

function StrategyComparison() {
  const [days, setDays] = useState(90);
  const { data, isLoading, isFetching, error, refetch } = useCompare({ days });

  const handleRun = useCallback(() => { refetch(); }, [refetch]);

  const rows = data?.rows ?? [];

  // Task 9.12: |return| beyond the plausibility bound means a degenerate
  // slice (e.g. near-zero initial capital), not real performance.
  const implausibleRows = rows.filter(
    (r) => !isPlausiblePercent(parseFloat(r.total_return)),
  );

  return (
    <Card>
      <CardContent>
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 2, mb: 2, flexWrap: 'wrap' }}>
          <CompareArrowsIcon color="primary" />
          <Typography variant="h6">Strategy Comparison</Typography>
          <TextField
            select
            size="small"
            label="Lookback"
            value={days}
            onChange={(e) => setDays(Number(e.target.value))}
            sx={{ width: 120 }}
          >
            {DAYS_OPTIONS.map((d) => (
              <MenuItem key={d} value={d}>{d} days</MenuItem>
            ))}
          </TextField>
          <Button
            variant="contained"
            size="small"
            onClick={handleRun}
            disabled={isLoading || isFetching}
            startIcon={(isLoading || isFetching) ? <CircularProgress size={16} /> : undefined}
          >
            {(isLoading || isFetching) ? 'Running...' : 'Run Comparison'}
          </Button>
          {data?.date_range && (
            <Typography variant="body2" color="text.secondary">{data.date_range}</Typography>
          )}
        </Box>

        {error && <Alert severity="error" variant="outlined" sx={{ mb: 2 }}>Failed to run comparison</Alert>}

        {data?.errors && data.errors.length > 0 && (
          <Alert severity="warning" variant="outlined" sx={{ mb: 2 }}>
            {data.errors.length} pair(s) failed: {data.errors.map((e) => `${e.strategy ?? ''}:${e.symbol ?? ''}`).join(', ')}
          </Alert>
        )}

        {implausibleRows.length > 0 && (
          <Alert severity="warning" variant="outlined" sx={{ mb: 2 }}>
            {implausibleRows.length} row(s) have returns beyond ±{MAX_PLAUSIBLE_PERCENT}% —
            likely degenerate slice capital, not real performance:{' '}
            {implausibleRows.map((r) => `${r.strategy}:${r.symbol}`).join(', ')}
          </Alert>
        )}

        {rows.length > 0 && (
          <TableContainer>
            <Table size="small">
              <TableHead>
                <TableRow>
                  <TableCell>Strategy</TableCell>
                  <TableCell>Symbol</TableCell>
                  <TableCell align="right">Return %</TableCell>
                  <TableCell align="right">Win Rate %</TableCell>
                  <TableCell align="right">Sharpe</TableCell>
                  <TableCell align="right">Max DD %</TableCell>
                  <TableCell align="right">Profit Factor</TableCell>
                  <TableCell align="right">Trades</TableCell>
                  <TableCell align="right">vs B&H %</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {rows.map((row) => {
                  const ret = parseFloat(row.total_return);
                  const bh = row.buy_hold_vs_strategy ? parseFloat(row.buy_hold_vs_strategy) : null;
                  const bestReturn = bestInColumn(rows, 'total_return');
                  const bestSharpe = bestInColumn(rows, 'sharpe_ratio');
                  const isBestReturn = bestReturn === `${row.strategy}:${row.symbol}`;
                  const isBestSharpe = bestSharpe === `${row.strategy}:${row.symbol}`;

                  return (
                    <TableRow key={`${row.strategy}:${row.symbol}`} hover>
                      <TableCell>
                        <Chip label={row.strategy} size="small" variant="outlined" />
                      </TableCell>
                      <TableCell>{row.symbol}</TableCell>
                      <TableCell align="right" sx={{ color: ret >= 0 ? 'success.main' : 'error.main', fontWeight: isBestReturn ? 700 : 400 }}>
                        {fmt(row.total_return)}%
                      </TableCell>
                      <TableCell align="right">{fmt(row.win_rate)}%</TableCell>
                      <TableCell align="right" sx={{ fontWeight: isBestSharpe ? 700 : 400 }}>
                        {row.sharpe_ratio ? fmt(row.sharpe_ratio) : '—'}
                      </TableCell>
                      <TableCell align="right" sx={{ color: 'error.main' }}>
                        {fmt(row.max_drawdown)}%
                      </TableCell>
                      <TableCell align="right">
                        {row.profit_factor ? fmt(row.profit_factor) : '—'}
                      </TableCell>
                      <TableCell align="right">{row.total_trades}</TableCell>
                      <TableCell align="right" sx={{ color: bh != null ? (bh >= 0 ? 'success.main' : 'error.main') : 'text.secondary' }}>
                        {bh != null ? `${fmt(bh)}%` : '—'}
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          </TableContainer>
        )}

        {/* ── Overlaid Equity Curves (task 5.5c) ─────────────────── */}
        {data?.per_strategy_curves && Object.keys(data.per_strategy_curves).length > 0 && (
          <ComparisonEquityChart curves={data.per_strategy_curves} />
        )}

        {!data && !isLoading && !isFetching && (
          <Typography variant="body2" color="text.secondary" sx={{ textAlign: 'center', py: 4 }}>
            Click "Run Comparison" to backtest all strategies side-by-side
          </Typography>
        )}
      </CardContent>
    </Card>
  );
}

function ComparisonEquityChart({ curves }: { curves: Record<string, Array<{ timestamp: string; equity: string }>> }) {
  const seriesKeys = useMemo(() => Object.keys(curves).sort(), [curves]);

  const chartData = useMemo(() => {
    const tsMap = new Map<string, Record<string, number | string>>();

    for (const key of seriesKeys) {
      for (const point of curves[key]) {
        // Task 9.12: skip non-finite equities — they break Recharts.
        const equity = finiteNumber(point.equity);
        if (equity === null) continue;
        const time = new Date(point.timestamp).toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
        if (!tsMap.has(point.timestamp)) {
          tsMap.set(point.timestamp, { time, _ts: new Date(point.timestamp).getTime() });
        }
        tsMap.get(point.timestamp)![key] = equity;
      }
    }

    return Array.from(tsMap.values()).sort((a, b) => (a._ts as number) - (b._ts as number));
  }, [curves, seriesKeys]);

  if (chartData.length === 0) return null;

  return (
    <Box sx={{ mt: 3 }}>
      <Typography variant="body2" color="text.secondary" gutterBottom>Equity Curves Comparison</Typography>
      <ResponsiveContainer width="100%" height={320}>
        <LineChart data={chartData} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
          <XAxis dataKey="time" tick={{ fontSize: 11 }} axisLine={false} tickLine={false} />
          <YAxis
            tick={{ fontSize: 11 }} axisLine={false} tickLine={false}
            domain={['auto', 'auto']}
            tickFormatter={(v: number) => `$${v.toLocaleString()}`}
          />
          <Tooltip
            contentStyle={CHART_TOOLTIP_STYLE}
            labelStyle={{ color: '#9e9e9e' }}
            formatter={((v: number | undefined, name: string | undefined) => [`$${(v ?? 0).toLocaleString(undefined, { minimumFractionDigits: 2 })}`, name ?? '']) as never}
          />
          <Legend wrapperStyle={{ fontSize: 12 }} />
          {seriesKeys.map((key, i) => (
            <Line
              key={key}
              type="monotone"
              dataKey={key}
              stroke={COMPARISON_COLORS[i % COMPARISON_COLORS.length]}
              strokeWidth={2}
              dot={false}
              isAnimationActive={false}
            />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </Box>
  );
}

// ── Multi-Strategy Backtest (task 5.4d) ──────────────────────────────────────

const REGIME_COLORS: Record<string, string> = {
  trending: 'rgba(0, 188, 212, 0.12)',
  ranging: 'rgba(255, 152, 0, 0.12)',
  uncertain: 'rgba(158, 158, 158, 0.08)',
};

const REGIME_CHIP_COLORS: Record<string, 'info' | 'warning' | 'default'> = {
  trending: 'info',
  ranging: 'warning',
  uncertain: 'default',
};

function MultiStrategyBacktest() {
  const [days, setDays] = useState(90);
  const { data, isLoading, isFetching, error, refetch } = useMultiStrategy({ days });

  const handleRun = useCallback(() => { refetch(); }, [refetch]);

  const equityData = useMemo(() => {
    if (!data?.combined_equity_curve) return [];
    // Task 9.12: drop non-finite equities before they reach Recharts.
    return data.combined_equity_curve.flatMap((p) => {
      const equity = finiteNumber(p.equity);
      if (equity === null) return [];
      return [{
        time: new Date(p.timestamp).toLocaleDateString(undefined, { month: 'short', day: 'numeric' }),
        timestamp: new Date(p.timestamp).getTime(),
        equity,
      }];
    });
  }, [data]);

  const regimeBands = useMemo(() => {
    if (!data?.regime_timeline || !equityData.length) return [];
    const bands: { x1: string; x2: string; regime: string }[] = [];
    const timeline = data.regime_timeline;
    for (let i = 0; i < timeline.length; i++) {
      const startTs = new Date(timeline[i].timestamp).getTime();
      const endTs = i + 1 < timeline.length
        ? new Date(timeline[i + 1].timestamp).getTime()
        : equityData[equityData.length - 1].timestamp;

      const startPoint = equityData.find((p) => p.timestamp >= startTs);
      const endPoint = [...equityData].reverse().find((p) => p.timestamp <= endTs);
      if (startPoint && endPoint) {
        bands.push({ x1: startPoint.time, x2: endPoint.time, regime: timeline[i].regime });
      }
    }
    return bands;
  }, [data, equityData]);

  return (
    <Card>
      <CardContent>
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 2, mb: 2, flexWrap: 'wrap' }}>
          <LayersIcon color="primary" />
          <Typography variant="h6">Multi-Strategy Backtest</Typography>
          <TextField
            select
            size="small"
            label="Lookback"
            value={days}
            onChange={(e) => setDays(Number(e.target.value))}
            sx={{ width: 120 }}
          >
            {DAYS_OPTIONS.map((d) => (
              <MenuItem key={d} value={d}>{d} days</MenuItem>
            ))}
          </TextField>
          <Button
            variant="contained"
            size="small"
            onClick={handleRun}
            disabled={isLoading || isFetching}
            startIcon={(isLoading || isFetching) ? <CircularProgress size={16} /> : undefined}
          >
            {(isLoading || isFetching) ? 'Running...' : 'Run Multi-Strategy'}
          </Button>
          {data?.date_range && (
            <Typography variant="body2" color="text.secondary">{data.date_range}</Typography>
          )}
        </Box>

        {error && <Alert severity="error" variant="outlined" sx={{ mb: 2 }}>Failed to run multi-strategy backtest</Alert>}

        {data?.errors && data.errors.length > 0 && (
          <Alert severity="warning" variant="outlined" sx={{ mb: 2 }}>
            {data.errors.length} pair(s) failed:{' '}
            {data.errors.map((e) => `${e.strategy ?? ''}:${e.symbol ?? ''}`).join(', ')}
          </Alert>
        )}

        {data && (
          <>
            {/* Summary cards */}
            <Box sx={{ display: 'flex', gap: 2, mb: 3, flexWrap: 'wrap' }}>
              <Box sx={{ minWidth: 140 }}>
                <Typography variant="body2" color="text.secondary">Initial Capital</Typography>
                <Typography variant="h6">${fmt(data.total_initial_capital)}</Typography>
              </Box>
              <Box sx={{ minWidth: 140 }}>
                <Typography variant="body2" color="text.secondary">Final Equity</Typography>
                <Typography variant="h6" color={pnlColor(parseFloat(data.total_return_pct))}>
                  ${fmt(data.total_final_equity)}
                </Typography>
              </Box>
              <Box sx={{ minWidth: 100 }}>
                <Typography variant="body2" color="text.secondary">Total Return</Typography>
                <Typography variant="h6" color={pnlColor(parseFloat(data.total_return_pct))}>
                  {fmt(data.total_return_pct)}%
                </Typography>
              </Box>
            </Box>

            {/* Combined equity curve with regime bands */}
            {equityData.length > 0 && (
              <Box sx={{ mb: 3 }}>
                <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 1 }}>
                  <Typography variant="body2" color="text.secondary">Combined Equity Curve</Typography>
                  {data.regime_timeline.length > 0 && (
                    <Box sx={{ display: 'flex', gap: 0.5, ml: 1 }}>
                      {['trending', 'ranging', 'uncertain'].map((r) => (
                        <Chip
                          key={r}
                          label={r}
                          size="small"
                          color={REGIME_CHIP_COLORS[r]}
                          variant="outlined"
                          sx={{ fontSize: 10, height: 20 }}
                        />
                      ))}
                    </Box>
                  )}
                </Box>
                <ResponsiveContainer width="100%" height={320}>
                  <AreaChart data={equityData} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
                    <defs>
                      <linearGradient id="multiEquityGrad" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="0%" stopColor="#7c4dff" stopOpacity={0.35} />
                        <stop offset="100%" stopColor="#7c4dff" stopOpacity={0} />
                      </linearGradient>
                    </defs>
                    {regimeBands.map((band, i) => (
                      <ReferenceArea
                        key={i}
                        x1={band.x1}
                        x2={band.x2}
                        fill={REGIME_COLORS[band.regime] ?? REGIME_COLORS.uncertain}
                        fillOpacity={1}
                      />
                    ))}
                    <XAxis dataKey="time" tick={{ fontSize: 11 }} axisLine={false} tickLine={false} />
                    <YAxis
                      tick={{ fontSize: 11 }} axisLine={false} tickLine={false}
                      domain={['auto', 'auto']}
                      tickFormatter={(v: number) => `$${v.toLocaleString()}`}
                    />
                    <Tooltip
                      contentStyle={CHART_TOOLTIP_STYLE}
                      labelStyle={{ color: '#9e9e9e' }}
                      formatter={(v) => [`$${Number(v).toLocaleString(undefined, { minimumFractionDigits: 2 })}`, 'Equity']}
                    />
                    <Area type="monotone" dataKey="equity" stroke="#7c4dff" strokeWidth={2} fill="url(#multiEquityGrad)" isAnimationActive={false} />
                  </AreaChart>
                </ResponsiveContainer>
              </Box>
            )}

            {/* Per-strategy breakdown table */}
            {data.per_strategy.length > 0 && (
              <TableContainer>
                <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>Per-Strategy Breakdown</Typography>
                <Table size="small">
                  <TableHead>
                    <TableRow>
                      <TableCell>Strategy</TableCell>
                      <TableCell>Symbol</TableCell>
                      <TableCell align="right">Allocated</TableCell>
                      <TableCell align="right">Return %</TableCell>
                      <TableCell align="right">Win Rate %</TableCell>
                      <TableCell align="right">Sharpe</TableCell>
                      <TableCell align="right">Max DD %</TableCell>
                      <TableCell align="right">Trades</TableCell>
                    </TableRow>
                  </TableHead>
                  <TableBody>
                    {data.per_strategy.map((s) => {
                      const ret = parseFloat(s.total_return);
                      return (
                        <TableRow key={`${s.strategy}:${s.symbol}`} hover>
                          <TableCell>
                            <Chip label={s.strategy} size="small" variant="outlined" />
                          </TableCell>
                          <TableCell>{s.symbol}</TableCell>
                          <TableCell align="right">${fmt(s.allocated_capital)}</TableCell>
                          <TableCell align="right" sx={{ color: ret >= 0 ? 'success.main' : 'error.main' }}>
                            {fmt(s.total_return)}%
                          </TableCell>
                          <TableCell align="right">{fmt(s.win_rate)}%</TableCell>
                          <TableCell align="right">{s.sharpe_ratio ? fmt(s.sharpe_ratio) : '—'}</TableCell>
                          <TableCell align="right" sx={{ color: 'error.main' }}>{fmt(s.max_drawdown)}%</TableCell>
                          <TableCell align="right">{s.total_trades}</TableCell>
                        </TableRow>
                      );
                    })}
                  </TableBody>
                </Table>
              </TableContainer>
            )}
          </>
        )}

        {!data && !isLoading && !isFetching && (
          <Typography variant="body2" color="text.secondary" sx={{ textAlign: 'center', py: 4 }}>
            Click "Run Multi-Strategy" to backtest all strategies with regime-based allocation
          </Typography>
        )}
      </CardContent>
    </Card>
  );
}

// ── Parameter Optimization (task 5.6) ────────────────────────────────────────

const METRIC_OPTIONS = [
  { value: 'sharpe_ratio', label: 'Sharpe Ratio' },
  { value: 'total_return', label: 'Total Return' },
  { value: 'max_drawdown', label: 'Max Drawdown' },
  { value: 'win_rate', label: 'Win Rate' },
  { value: 'profit_factor', label: 'Profit Factor' },
];

interface ParamRange {
  min: string;
  max: string;
  step: string;
}

function generateValues(min: number, max: number, step: number): number[] {
  if (step <= 0 || min > max) return [min];
  const values: number[] = [];
  for (let v = min; v <= max + step * 0.001; v += step) {
    values.push(parseFloat(v.toFixed(6)));
  }
  return values;
}

function ParameterOptimization() {
  const { data: strategyList } = useStrategyList();
  const [strategy, setStrategy] = useState('');
  const [symbol, setSymbol] = useState('BTCUSDT');
  const [days, setDays] = useState(90);
  const [metric, setMetric] = useState('sharpe_ratio');
  const [nFolds, setNFolds] = useState(3);
  const [tab, setTab] = useState(0);

  const [paramRanges, setParamRanges] = useState<Record<string, ParamRange>>({});

  const strategies = strategyList?.strategies ?? [];

  const handleAddParam = useCallback((name: string) => {
    setParamRanges((prev) => ({
      ...prev,
      [name]: { min: '', max: '', step: '' },
    }));
  }, []);

  const handleRemoveParam = useCallback((name: string) => {
    setParamRanges((prev) => {
      const next = { ...prev };
      delete next[name];
      return next;
    });
  }, []);

  const handleParamChange = useCallback((name: string, field: keyof ParamRange, value: string) => {
    setParamRanges((prev) => ({
      ...prev,
      [name]: { ...prev[name], [field]: value },
    }));
  }, []);

  const paramGrid = useMemo(() => {
    const grid: Record<string, number[]> = {};
    for (const [name, range] of Object.entries(paramRanges)) {
      const min = parseFloat(range.min);
      const max = parseFloat(range.max);
      const step = parseFloat(range.step);
      if (!isNaN(min) && !isNaN(max) && !isNaN(step) && step > 0) {
        grid[name] = generateValues(min, max, step);
      }
    }
    return grid;
  }, [paramRanges]);

  const totalCombos = useMemo(() => {
    const values = Object.values(paramGrid);
    if (values.length === 0) return 0;
    return values.reduce((acc, v) => acc * v.length, 1);
  }, [paramGrid]);

  const optimizeMutation = useOptimize();
  const walkForwardMutation = useWalkForward();

  const canRun = strategy && totalCombos > 0 && totalCombos <= 10000;

  const handleGridSearch = useCallback(() => {
    if (!canRun) return;
    optimizeMutation.mutate({ strategy, symbol, days, param_grid: paramGrid, metric });
  }, [canRun, strategy, symbol, days, paramGrid, metric, optimizeMutation]);

  const handleWalkForward = useCallback(() => {
    if (!canRun) return;
    walkForwardMutation.mutate({ strategy, symbol, days, param_grid: paramGrid, metric, n_folds: nFolds, train_pct: 0.7 });
  }, [canRun, strategy, symbol, days, paramGrid, metric, nFolds, walkForwardMutation]);

  const isRunning = optimizeMutation.isPending || walkForwardMutation.isPending;

  return (
    <Card>
      <CardContent>
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 2, mb: 2, flexWrap: 'wrap' }}>
          <TuneIcon color="primary" />
          <Typography variant="h6">Parameter Optimization</Typography>
        </Box>

        {/* Controls */}
        <Grid container spacing={1.5} sx={{ mb: 2 }}>
          <Grid size={{ xs: 6, sm: 3 }}>
            <TextField
              select fullWidth size="small" label="Strategy" value={strategy}
              onChange={(e) => setStrategy(e.target.value)}
            >
              {strategies.map((s) => (
                <MenuItem key={s} value={s}>{s.replace(/_/g, ' ')}</MenuItem>
              ))}
            </TextField>
          </Grid>
          <Grid size={{ xs: 6, sm: 2 }}>
            <TextField
              fullWidth size="small" label="Symbol" value={symbol}
              onChange={(e) => setSymbol(e.target.value)}
            />
          </Grid>
          <Grid size={{ xs: 4, sm: 2 }}>
            <TextField
              select fullWidth size="small" label="Days" value={days}
              onChange={(e) => setDays(Number(e.target.value))}
            >
              {DAYS_OPTIONS.map((d) => (
                <MenuItem key={d} value={d}>{d}</MenuItem>
              ))}
            </TextField>
          </Grid>
          <Grid size={{ xs: 4, sm: 2 }}>
            <TextField
              select fullWidth size="small" label="Optimize for" value={metric}
              onChange={(e) => setMetric(e.target.value)}
            >
              {METRIC_OPTIONS.map((m) => (
                <MenuItem key={m.value} value={m.value}>{m.label}</MenuItem>
              ))}
            </TextField>
          </Grid>
          <Grid size={{ xs: 4, sm: 2 }}>
            <TextField
              select fullWidth size="small" label="WF Folds" value={nFolds}
              onChange={(e) => setNFolds(Number(e.target.value))}
            >
              {[2, 3, 4, 5].map((n) => (
                <MenuItem key={n} value={n}>{n}</MenuItem>
              ))}
            </TextField>
          </Grid>
        </Grid>

        {/* Parameter grid builder */}
        <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
          Parameter Ranges
        </Typography>
        {Object.entries(paramRanges).map(([name, range]) => (
          <Box key={name} sx={{ display: 'flex', gap: 1, mb: 1, alignItems: 'center' }}>
            <Chip label={name} size="small" variant="outlined" onDelete={() => handleRemoveParam(name)} />
            <TextField size="small" label="Min" type="number" value={range.min} sx={{ width: 90 }}
              onChange={(e) => handleParamChange(name, 'min', e.target.value)} />
            <TextField size="small" label="Max" type="number" value={range.max} sx={{ width: 90 }}
              onChange={(e) => handleParamChange(name, 'max', e.target.value)} />
            <TextField size="small" label="Step" type="number" value={range.step} sx={{ width: 90 }}
              onChange={(e) => handleParamChange(name, 'step', e.target.value)} />
            {paramGrid[name] && (
              <Typography variant="caption" color="text.secondary">
                {paramGrid[name].length} values
              </Typography>
            )}
          </Box>
        ))}
        <Box sx={{ display: 'flex', gap: 1, mb: 2, alignItems: 'center', flexWrap: 'wrap' }}>
          <TextField
            size="small" label="Add parameter" placeholder="e.g. ema_fast"
            onKeyDown={(e) => {
              if (e.key === 'Enter') {
                const input = e.target as HTMLInputElement;
                const name = input.value.trim();
                if (name && !(name in paramRanges)) {
                  handleAddParam(name);
                  input.value = '';
                }
              }
            }}
            sx={{ width: 180 }}
          />
          {totalCombos > 0 && (
            <Chip
              label={`${totalCombos.toLocaleString()} combinations`}
              size="small"
              color={totalCombos > 10000 ? 'error' : totalCombos > 1000 ? 'warning' : 'default'}
              variant="outlined"
            />
          )}
        </Box>

        {/* Action buttons */}
        <Box sx={{ display: 'flex', gap: 1, mb: 2 }}>
          <Button
            variant="contained" size="small" onClick={handleGridSearch}
            disabled={!canRun || isRunning}
            startIcon={isRunning && tab === 0 ? <CircularProgress size={16} /> : undefined}
          >
            {optimizeMutation.isPending ? 'Searching...' : 'Grid Search'}
          </Button>
          <Button
            variant="outlined" size="small" onClick={handleWalkForward}
            disabled={!canRun || isRunning}
            startIcon={isRunning && tab === 1 ? <CircularProgress size={16} /> : undefined}
          >
            {walkForwardMutation.isPending ? 'Running...' : 'Walk-Forward'}
          </Button>
        </Box>

        {(optimizeMutation.error || walkForwardMutation.error) && (
          <Alert severity="error" variant="outlined" sx={{ mb: 2 }}>
            Optimization failed
          </Alert>
        )}

        {/* Results */}
        {(optimizeMutation.data || walkForwardMutation.data) && (
          <>
            <Tabs value={tab} onChange={(_, v) => setTab(v)} sx={{ mb: 2 }}>
              <Tab label="Grid Search" disabled={!optimizeMutation.data} />
              <Tab label="Walk-Forward" disabled={!walkForwardMutation.data} />
            </Tabs>

            {tab === 0 && optimizeMutation.data && (
              <GridSearchResults data={optimizeMutation.data.results} metric={optimizeMutation.data.metric} errors={optimizeMutation.data.errors} />
            )}

            {tab === 1 && walkForwardMutation.data && (
              <WalkForwardResults data={walkForwardMutation.data} />
            )}
          </>
        )}

        {!optimizeMutation.data && !walkForwardMutation.data && !isRunning && (
          <Typography variant="body2" color="text.secondary" sx={{ textAlign: 'center', py: 3 }}>
            Add parameter ranges and click "Grid Search" or "Walk-Forward" to optimize
          </Typography>
        )}
      </CardContent>
    </Card>
  );
}

function GridSearchResults({ data, metric, errors }: { data: GridSearchRow[]; metric: string; errors: string[] }) {
  if (data.length === 0 && errors.length === 0) {
    return <Typography color="text.secondary">No results</Typography>;
  }

  return (
    <>
      {errors.length > 0 && (
        <Alert severity="warning" variant="outlined" sx={{ mb: 1 }}>
          {errors.join('; ')}
        </Alert>
      )}
      <TableContainer>
        <Table size="small">
          <TableHead>
            <TableRow>
              <TableCell>#</TableCell>
              <TableCell>Parameters</TableCell>
              <TableCell align="right">Sharpe</TableCell>
              <TableCell align="right">Return %</TableCell>
              <TableCell align="right">Max DD %</TableCell>
              <TableCell align="right">Win Rate %</TableCell>
              <TableCell align="right">PF</TableCell>
              <TableCell align="right">Trades</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {data.slice(0, 50).map((row, i) => (
              <TableRow key={i} hover sx={i === 0 ? { bgcolor: 'rgba(0, 188, 212, 0.08)' } : undefined}>
                <TableCell>{i + 1}</TableCell>
                <TableCell>
                  <Box sx={{ display: 'flex', gap: 0.5, flexWrap: 'wrap' }}>
                    {Object.entries(row.params).map(([k, v]) => (
                      <Chip key={k} label={`${k}=${v}`} size="small" variant="outlined" sx={{ fontSize: 11 }} />
                    ))}
                  </Box>
                </TableCell>
                <TableCell align="right" sx={{ fontWeight: i === 0 && metric === 'sharpe_ratio' ? 700 : 400 }}>
                  {row.sharpe_ratio != null ? fmt(row.sharpe_ratio) : '—'}
                </TableCell>
                <TableCell align="right" sx={{ color: row.total_return >= 0 ? 'success.main' : 'error.main', fontWeight: i === 0 && metric === 'total_return' ? 700 : 400 }}>
                  {fmt(row.total_return)}%
                </TableCell>
                <TableCell align="right" sx={{ color: 'error.main' }}>{fmt(row.max_drawdown)}%</TableCell>
                <TableCell align="right">{fmt(row.win_rate)}%</TableCell>
                <TableCell align="right">{row.profit_factor != null ? fmt(row.profit_factor) : '—'}</TableCell>
                <TableCell align="right">{row.total_trades}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </TableContainer>
      {data.length > 50 && (
        <Typography variant="caption" color="text.secondary" sx={{ mt: 1, display: 'block' }}>
          Showing top 50 of {data.length} results
        </Typography>
      )}
    </>
  );
}

function WalkForwardResults({ data }: { data: { folds: WalkForwardFold[]; avg_test_sharpe: number | null; avg_test_return: number; errors: string[] } }) {
  const hasOverfit = data.folds.some((f) => f.overfit);

  return (
    <>
      {data.errors.length > 0 && (
        <Alert severity="warning" variant="outlined" sx={{ mb: 1 }}>
          {data.errors.join('; ')}
        </Alert>
      )}

      {hasOverfit && (
        <Alert severity="warning" variant="outlined" icon={<WarningAmberIcon />} sx={{ mb: 1 }}>
          Overfitting detected in some folds — out-of-sample Sharpe dropped &gt;50% vs in-sample
        </Alert>
      )}

      <Box sx={{ display: 'flex', gap: 3, mb: 2 }}>
        <Box>
          <Typography variant="body2" color="text.secondary">Avg Test Sharpe</Typography>
          <Typography variant="h6">{data.avg_test_sharpe != null ? fmt(data.avg_test_sharpe) : '—'}</Typography>
        </Box>
        <Box>
          <Typography variant="body2" color="text.secondary">Avg Test Return</Typography>
          <Typography variant="h6" color={pnlColor(data.avg_test_return)}>{fmt(data.avg_test_return)}%</Typography>
        </Box>
      </Box>

      <TableContainer>
        <Table size="small">
          <TableHead>
            <TableRow>
              <TableCell>Fold</TableCell>
              <TableCell>Best Params</TableCell>
              <TableCell align="right">Train Sharpe</TableCell>
              <TableCell align="right">Train Return %</TableCell>
              <TableCell align="right">Test Sharpe</TableCell>
              <TableCell align="right">Test Return %</TableCell>
              <TableCell align="center">Overfit</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {data.folds.map((fold) => (
              <TableRow key={fold.fold} hover sx={fold.overfit ? { bgcolor: 'rgba(244, 67, 54, 0.06)' } : undefined}>
                <TableCell>{fold.fold}</TableCell>
                <TableCell>
                  <Box sx={{ display: 'flex', gap: 0.5, flexWrap: 'wrap' }}>
                    {Object.entries(fold.best_params).map(([k, v]) => (
                      <Chip key={k} label={`${k}=${v}`} size="small" variant="outlined" sx={{ fontSize: 11 }} />
                    ))}
                  </Box>
                </TableCell>
                <TableCell align="right">{fold.train_sharpe != null ? fmt(fold.train_sharpe) : '—'}</TableCell>
                <TableCell align="right" sx={{ color: fold.train_return >= 0 ? 'success.main' : 'error.main' }}>
                  {fmt(fold.train_return)}%
                </TableCell>
                <TableCell align="right">{fold.test_sharpe != null ? fmt(fold.test_sharpe) : '—'}</TableCell>
                <TableCell align="right" sx={{ color: fold.test_return >= 0 ? 'success.main' : 'error.main' }}>
                  {fmt(fold.test_return)}%
                </TableCell>
                <TableCell align="center">
                  {fold.overfit ? <WarningAmberIcon fontSize="small" color="warning" /> : '—'}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </TableContainer>
    </>
  );
}

// ── Page ──────────────────────────────────────────────────────────────────────

export default function MetricsPage() {
  return (
    <Box>
      <Typography variant="h5" sx={{ mb: 3 }}>Metrics</Typography>

      <Grid container spacing={2}>
        {/* ── Top metric cards ────────────────────────────────────── */}
        <MetricCards />

        {/* ── Strategy Comparison (task 5.3) ──────────────────────── */}
        <Grid size={{ xs: 12 }}>
          <StrategyComparison />
        </Grid>

        {/* ── Multi-Strategy Backtest (task 5.4d) ─────────────────── */}
        <Grid size={{ xs: 12 }}>
          <MultiStrategyBacktest />
        </Grid>

        {/* ── Parameter Optimization (task 5.6) ──────────────────── */}
        <Grid size={{ xs: 12 }}>
          <ParameterOptimization />
        </Grid>

        {/* ── Equity Curve ────────────────────────────────────────── */}
        <Grid size={{ xs: 12 }}>
          <EquityCurveChart limit={1000} height={200} percent />
        </Grid>

        {/* ── Drawdown Chart ──────────────────────────────────────── */}
        <Grid size={{ xs: 12 }}>
          <DrawdownChart />
        </Grid>

        {/* ── Detailed Stats ──────────────────────────────────────── */}
        <Grid size={{ xs: 12, md: 5 }}>
          <DetailedStatsTable />
        </Grid>
      </Grid>
    </Box>
  );
}
