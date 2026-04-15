// ── MetricsPage ───────────────────────────────────────────────────────────────
// Task 2.43 – Equity curve, drawdown chart, win rate, total return,
// drawdown, and Sharpe ratio from trade stats and equity curve data.

import { useMemo } from 'react';
import Alert from '@mui/material/Alert';
import Box from '@mui/material/Box';
import Card from '@mui/material/Card';
import CardContent from '@mui/material/CardContent';
import Grid from '@mui/material/Grid';
import Skeleton from '@mui/material/Skeleton';
import Typography from '@mui/material/Typography';
import {
  Area,
  AreaChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';

import { useTradeStats, useEquityCurve } from '../api';
import { useTradesStore } from '../stores/tradesStore';
import { usePortfolioStore } from '../stores/portfolioStore';
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

function EquityCurveChart() {
  const { isLoading, error } = useEquityCurve({ limit: 1000 });
  const equityCurve = usePortfolioStore((s) => s.equityCurve);

  if (isLoading) return <Skeleton variant="rounded" height={420} />;
  if (error) return <Alert severity="error" variant="outlined">Failed to load equity curve</Alert>;
  if (equityCurve.length === 0) {
    return (
      <Card sx={{ height: 420, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
        <Typography color="text.secondary">No equity data yet</Typography>
      </Card>
    );
  }

  const chartData = equityCurve.map((s) => ({
    time: new Date(s.timestamp).toLocaleDateString(undefined, { month: 'short', day: 'numeric' }),
    equity: parseFloat(s.total_equity),
    pnl: parseFloat(s.total_pnl),
  }));

  return (
    <Card sx={{ p: 2 }}>
      <Typography variant="body2" color="text.secondary" gutterBottom>Equity Curve</Typography>
      <ResponsiveContainer width="100%" height={360}>
        <AreaChart data={chartData} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
          <defs>
            <linearGradient id="metricsEquityGrad" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="#00bcd4" stopOpacity={0.35} />
              <stop offset="100%" stopColor="#00bcd4" stopOpacity={0} />
            </linearGradient>
          </defs>
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
          <Area type="monotone" dataKey="equity" stroke="#00bcd4" strokeWidth={2} fill="url(#metricsEquityGrad)" />
        </AreaChart>
      </ResponsiveContainer>
    </Card>
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
          <Area type="monotone" dataKey="drawdown" stroke="#f44336" strokeWidth={2} fill="url(#drawdownGrad)" />
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

// ── Page ──────────────────────────────────────────────────────────────────────

export default function MetricsPage() {
  return (
    <Box>
      <Typography variant="h5" sx={{ mb: 3 }}>Metrics</Typography>

      <Grid container spacing={2}>
        {/* ── Top metric cards ────────────────────────────────────── */}
        <MetricCards />

        {/* ── Equity Curve ────────────────────────────────────────── */}
        <Grid size={{ xs: 12 }}>
          <EquityCurveChart />
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
