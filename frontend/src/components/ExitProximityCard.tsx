import Alert from '@mui/material/Alert';
import Box from '@mui/material/Box';
import Card from '@mui/material/Card';
import CardContent from '@mui/material/CardContent';
import Chip from '@mui/material/Chip';
import LinearProgress from '@mui/material/LinearProgress';
import Skeleton from '@mui/material/Skeleton';
import Typography from '@mui/material/Typography';
import CheckCircleIcon from '@mui/icons-material/CheckCircle';
import CancelIcon from '@mui/icons-material/Cancel';

import { useExitProximity } from '../api';
import type { ExitConditionStatus, ExitProximityResponse } from '../types/portfolio';

function proximityColor(pct: number): 'success' | 'warning' | 'error' {
  if (pct > 0.15) return 'success';
  if (pct > 0.05) return 'warning';
  return 'error';
}

function pctLabel(pct: number): string {
  return `${(pct * 100).toFixed(1)}%`;
}

function StopBar({ label, pct }: { label: string; pct: number }) {
  const danger = Math.max(0, Math.min(100, (1 - pct / 0.20) * 100));
  const color = proximityColor(pct);

  return (
    <Box sx={{ mb: 1.5 }}>
      <Box sx={{ display: 'flex', justifyContent: 'space-between', mb: 0.25 }}>
        <Typography variant="caption" color="text.secondary">{label}</Typography>
        <Typography variant="caption" fontWeight={600}>{pctLabel(pct)}</Typography>
      </Box>
      <LinearProgress
        variant="determinate"
        value={danger}
        color={color}
        sx={{ height: 8, borderRadius: 1 }}
      />
    </Box>
  );
}

function TimeStopBar({ pct }: { pct: number }) {
  const progress = Math.max(0, Math.min(100, pct * 100));
  const color = pct >= 0.85 ? 'error' : pct >= 0.60 ? 'warning' : 'success';

  return (
    <Box sx={{ mb: 1.5 }}>
      <Box sx={{ display: 'flex', justifyContent: 'space-between', mb: 0.25 }}>
        <Typography variant="caption" color="text.secondary">Time stop</Typography>
        <Typography variant="caption" fontWeight={600}>{`${Math.round(progress)}%`}</Typography>
      </Box>
      <LinearProgress
        variant="determinate"
        value={progress}
        color={color}
        sx={{ height: 8, borderRadius: 1 }}
      />
    </Box>
  );
}

function ConditionRow({ cond }: { cond: ExitConditionStatus }) {
  return (
    <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5, mb: 0.5 }}>
      {cond.met
        ? <CheckCircleIcon sx={{ fontSize: 16, color: 'error.main' }} />
        : <CancelIcon sx={{ fontSize: 16, color: 'text.disabled' }} />
      }
      <Typography variant="caption" sx={{ flex: 1 }}>{cond.name}</Typography>
      {cond.current_value && (
        <Typography variant="caption" color="text.secondary" sx={{ fontVariantNumeric: 'tabular-nums' }}>
          {cond.current_value}
        </Typography>
      )}
    </Box>
  );
}

function ProximityCard({ data }: { data: ExitProximityResponse }) {
  return (
    <Card>
      <CardContent sx={{ pb: '12px !important' }}>
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 1.5 }}>
          <Typography variant="body2" fontWeight={600}>{data.symbol}</Typography>
          <Chip label={data.strategy.replace(/_/g, ' ')} size="small" variant="outlined" />
        </Box>

        <StopBar label="Hard stop" pct={data.hard_stop_pct} />
        <StopBar label="Trailing stop" pct={data.trailing_stop_pct} />
        {data.time_stop_pct !== null && <TimeStopBar pct={data.time_stop_pct} />}

        {data.exit_conditions.length > 0 && (
          <Box sx={{ mt: 1.5 }}>
            <Typography variant="caption" color="text.secondary" sx={{ mb: 0.5, display: 'block' }}>
              Exit conditions
            </Typography>
            {data.exit_conditions.map((c) => (
              <ConditionRow key={c.name} cond={c} />
            ))}
          </Box>
        )}
      </CardContent>
    </Card>
  );
}

export default function ExitProximitySection() {
  const { data, isLoading, error } = useExitProximity();

  if (isLoading) return <Skeleton variant="rounded" height={120} />;
  if (error) return <Alert severity="error" variant="outlined">Failed to load exit proximity</Alert>;
  if (!data || data.length === 0) return null;

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
      <Typography variant="body2" color="text.secondary">Exit Proximity</Typography>
      {data.map((d) => (
        <ProximityCard key={`${d.symbol}:${d.strategy}`} data={d} />
      ))}
    </Box>
  );
}
