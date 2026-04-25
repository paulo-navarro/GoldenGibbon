import { useId } from 'react';
import Alert from '@mui/material/Alert';
import Card from '@mui/material/Card';
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

import { useEquityCurve } from '../api';
import { usePortfolioStore } from '../stores/portfolioStore';

interface EquityCurveChartProps {
  limit?: number;
  height?: number;
  mini?: boolean;
  showPnl?: boolean;
}

const TOOLTIP_STYLE = {
  background: '#111720',
  border: '1px solid rgba(255,255,255,0.08)',
  borderRadius: 8,
  fontSize: 12,
};

export default function EquityCurveChart({
  limit = 500,
  height = 350,
  mini = false,
  showPnl = !mini,
}: EquityCurveChartProps) {
  const uid = useId();
  const equityGradId = `equityGrad-${uid}`;
  const pnlGradId = `pnlGrad-${uid}`;

  const { isLoading, error } = useEquityCurve({ limit });
  const equityCurve = usePortfolioStore((s) => s.equityCurve);

  const placeholderHeight = mini ? height + 40 : height + 50;

  if (isLoading) return <Skeleton variant="rounded" height={placeholderHeight} />;
  if (error) return <Alert severity="error" variant="outlined">Failed to load equity curve</Alert>;
  if (equityCurve.length === 0) {
    return (
      <Card sx={{ height: placeholderHeight, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
        <Typography color="text.secondary">No equity data yet</Typography>
      </Card>
    );
  }

  const chartData = equityCurve.map((s) => ({
    time: new Date(s.timestamp).toLocaleDateString(undefined, { month: 'short', day: 'numeric' }),
    equity: parseFloat(s.total_equity),
    pnl: parseFloat(s.total_pnl),
  }));

  const tickFontSize = mini ? 10 : 11;
  const margin = mini
    ? { top: 4, right: 4, bottom: 0, left: 0 }
    : { top: 8, right: 8, bottom: 0, left: 0 };

  return (
    <Card sx={{ p: 2 }}>
      <Typography variant="body2" color="text.secondary" gutterBottom>
        Equity Curve
      </Typography>
      <ResponsiveContainer width="100%" height={height}>
        <AreaChart data={chartData} margin={margin}>
          <defs>
            <linearGradient id={equityGradId} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="#00bcd4" stopOpacity={0.3} />
              <stop offset="100%" stopColor="#00bcd4" stopOpacity={0} />
            </linearGradient>
            {showPnl && (
              <linearGradient id={pnlGradId} x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor="#ff9800" stopOpacity={0.25} />
                <stop offset="100%" stopColor="#ff9800" stopOpacity={0} />
              </linearGradient>
            )}
          </defs>
          <XAxis dataKey="time" tick={{ fontSize: tickFontSize }} axisLine={false} tickLine={false} />
          {mini ? (
            <YAxis hide domain={['auto', 'auto']} />
          ) : (
            <YAxis
              yAxisId="equity"
              tick={{ fontSize: 11 }}
              axisLine={false}
              tickLine={false}
              domain={['auto', 'auto']}
              tickFormatter={(v: number) => `$${v.toLocaleString()}`}
            />
          )}
          {showPnl && !mini && (
            <YAxis
              yAxisId="pnl"
              orientation="right"
              tick={{ fontSize: 11 }}
              axisLine={false}
              tickLine={false}
              domain={['auto', 'auto']}
              tickFormatter={(v: number) => `$${v.toLocaleString()}`}
            />
          )}
          <Tooltip
            contentStyle={TOOLTIP_STYLE}
            labelStyle={{ color: '#9e9e9e' }}
            formatter={(value, name) => [
              `$${Number(value).toLocaleString(undefined, { minimumFractionDigits: 2 })}`,
              name === 'equity' ? 'Equity' : 'PnL',
            ]}
          />
          <Area
            yAxisId={mini ? undefined : 'equity'}
            type="monotone"
            dataKey="equity"
            stroke="#00bcd4"
            strokeWidth={2}
            fill={`url(#${equityGradId})`}
          />
          {showPnl && (
            <Area
              yAxisId="pnl"
              type="monotone"
              dataKey="pnl"
              stroke="#ff9800"
              strokeWidth={1.5}
              fill={`url(#${pnlGradId})`}
            />
          )}
        </AreaChart>
      </ResponsiveContainer>
    </Card>
  );
}
