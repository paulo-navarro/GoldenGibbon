// ── StrategyPage ─────────────────────────────────────────────────────────────
// Task 2.39 – Strategy overview: state badge, signal chip, conditions
// checklist, scaled-entry progress, and cooldown timer.

import { useEffect, useState } from 'react';
import Alert from '@mui/material/Alert';
import Box from '@mui/material/Box';
import Card from '@mui/material/Card';
import CardContent from '@mui/material/CardContent';
import Chip from '@mui/material/Chip';
import Grid from '@mui/material/Grid';
import LinearProgress from '@mui/material/LinearProgress';
import List from '@mui/material/List';
import ListItem from '@mui/material/ListItem';
import ListItemIcon from '@mui/material/ListItemIcon';
import ListItemText from '@mui/material/ListItemText';
import Skeleton from '@mui/material/Skeleton';
import Typography from '@mui/material/Typography';
import CheckCircleIcon from '@mui/icons-material/CheckCircle';
import CancelIcon from '@mui/icons-material/Cancel';
import TimerIcon from '@mui/icons-material/Timer';

import { useStrategyState, useStrategySignals, usePortfolio } from '../api';
import { useStrategyStore } from '../stores/strategyStore';
import type { Signal, StrategyState } from '../types/enums';
import type {
  StrategyConditions,
  MeanReversionConditions,
} from '../types/strategy';

// ── Helpers ──────────────────────────────────────────────────────────────────

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

/** Convert snake_case key to Title Case label. */
function labelFromKey(key: string): string {
  return key
    .replace(/_/g, ' ')
    .replace(/\b\w/g, (c) => c.toUpperCase())
    .replace(/\bEma\b/g, 'EMA')
    .replace(/\bAdx\b/g, 'ADX')
    .replace(/\bRsi\b/g, 'RSI')
    .replace(/\bBb\b/g, 'BB');
}

/** Format a remaining-seconds value as MM:SS. */
function formatCountdown(totalSeconds: number): string {
  if (totalSeconds <= 0) return '00:00';
  const m = Math.floor(totalSeconds / 60);
  const s = totalSeconds % 60;
  return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
}

// ── Sub-components ───────────────────────────────────────────────────────────

function StrategyOverviewCard() {
  const { isLoading: statesLoading, error: statesError } = useStrategyState();
  const { isLoading: signalsLoading, error: signalsError } = useStrategySignals();

  const storeStates = useStrategyStore((s) => s.states);
  const storeSignals = useStrategyStore((s) => s.signals);
  const entries = Object.values(storeStates);

  const isLoading = statesLoading || signalsLoading;
  const error = statesError || signalsError;

  if (isLoading) return <Skeleton variant="rounded" height={120} />;
  if (error) return <Alert severity="error" variant="outlined">Failed to load strategy state</Alert>;
  if (entries.length === 0) {
    return (
      <Card sx={{ p: 2 }}>
        <Typography color="text.secondary" sx={{ textAlign: 'center', py: 2 }}>
          No active strategies — start a backtest or paper-trade session to see data here.
        </Typography>
      </Card>
    );
  }

  return (
    <>
      {entries.map((st) => {
        const key = `${st.symbol}:${st.strategy}`;
        const sig = storeSignals[key];
        return (
          <Card key={key}>
            <CardContent>
              <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 1 }}>
                <Typography variant="h6">{st.symbol}</Typography>
                <Chip
                  label={st.strategy.replace(/_/g, ' ')}
                  size="small"
                  variant="outlined"
                />
              </Box>
              <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5 }}>
                <Typography variant="body2" color="text.secondary">State:</Typography>
                <Chip label={st.state} size="small" color={stateColor(st.state)} variant="outlined" />
                <Typography variant="body2" color="text.secondary" sx={{ ml: 1 }}>Signal:</Typography>
                <Chip
                  label={sig?.signal ?? 'hold'}
                  size="small"
                  color={signalColor(sig?.signal ?? 'hold')}
                  variant="filled"
                />
              </Box>
              {st.updated_at && (
                <Typography variant="body2" color="text.secondary" sx={{ mt: 1, fontSize: '0.75rem' }}>
                  Updated {new Date(st.updated_at).toLocaleString()}
                </Typography>
              )}
            </CardContent>
          </Card>
        );
      })}
    </>
  );
}

function ConditionsChecklist() {
  const storeConditions = useStrategyStore((s) => s.conditions);
  const storeSignals = useStrategyStore((s) => s.signals);

  // Gather conditions: prefer store.conditions (from WS), fallback to signal.conditions
  const allConditions: { key: string; conditions: Record<string, unknown> }[] = [];

  // Collect from all known strategy keys
  const keys = new Set([...Object.keys(storeConditions), ...Object.keys(storeSignals)]);
  for (const k of keys) {
    const cond =
      storeConditions[k] ??
      (storeSignals[k]?.conditions as Record<string, unknown> | null);
    if (cond) {
      allConditions.push({ key: k, conditions: cond });
    }
  }

  if (allConditions.length === 0) {
    return (
      <Card sx={{ p: 2, height: '100%' }}>
        <Typography variant="body2" color="text.secondary" gutterBottom>Conditions</Typography>
        <Typography color="text.secondary" sx={{ py: 2, textAlign: 'center' }}>
          No conditions data yet
        </Typography>
      </Card>
    );
  }

  return (
    <>
      {allConditions.map(({ key, conditions }) => {
        const entries = Object.entries(conditions).filter(
          ([k]) => k !== 'symbol' && k !== 'strategy',
        );
        const metCount = entries.filter(([, v]) => v === true).length;

        return (
          <Card key={key} sx={{ height: '100%' }}>
            <CardContent sx={{ pb: '16px !important' }}>
              <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', mb: 1 }}>
                <Typography variant="body2" color="text.secondary">
                  Conditions — {key.replace(':', ' / ')}
                </Typography>
                <Chip
                  label={`${metCount}/${entries.length}`}
                  size="small"
                  color={metCount === entries.length ? 'success' : 'default'}
                  variant="outlined"
                />
              </Box>
              <List dense disablePadding>
                {entries.map(([name, value]) => (
                  <ListItem key={name} disableGutters sx={{ py: 0.25 }}>
                    <ListItemIcon sx={{ minWidth: 32 }}>
                      {value === true ? (
                        <CheckCircleIcon fontSize="small" color="success" />
                      ) : (
                        <CancelIcon fontSize="small" color="error" />
                      )}
                    </ListItemIcon>
                    <ListItemText
                      primary={labelFromKey(name)}
                      primaryTypographyProps={{ variant: 'body2' }}
                    />
                  </ListItem>
                ))}
              </List>
            </CardContent>
          </Card>
        );
      })}
    </>
  );
}

function ScaledEntryProgress() {
  const storeStates = useStrategyStore((s) => s.states);
  const { data: portfolio } = usePortfolio();

  const entries = Object.entries(storeStates);

  if (entries.length === 0) {
    return (
      <Card sx={{ p: 2, height: '100%' }}>
        <Typography variant="body2" color="text.secondary" gutterBottom>Scaled Entry</Typography>
        <Typography color="text.secondary" sx={{ py: 2, textAlign: 'center' }}>
          No active strategies
        </Typography>
      </Card>
    );
  }

  // Scale-in steps: 0 → 50%, 1 → 75%, 2 → 100%
  const STEPS = ['50%', '75%', '100%'];

  return (
    <>
      {entries.map(([key, st]) => {
        const pos = portfolio?.positions?.find((p) => p.symbol === st.symbol);
        const scaleInCount = pos?.scale_in_count ?? 0;
        const consecutiveBuys = st.consecutive_buy_candles ?? 0;
        const progressPercent = Math.min(((scaleInCount + 1) / 3) * 100, 100);

        return (
          <Card key={key} sx={{ height: '100%' }}>
            <CardContent>
              <Typography variant="body2" color="text.secondary" gutterBottom>
                Scaled Entry — {st.symbol}
              </Typography>

              {/* Scale-in steps */}
              <Box sx={{ mb: 2 }}>
                <Box sx={{ display: 'flex', justifyContent: 'space-between', mb: 0.5 }}>
                  {STEPS.map((label, i) => (
                    <Typography
                      key={label}
                      variant="caption"
                      color={i <= scaleInCount ? 'primary.main' : 'text.secondary'}
                      sx={{ fontWeight: i <= scaleInCount ? 700 : 400 }}
                    >
                      {label}
                    </Typography>
                  ))}
                </Box>
                <LinearProgress
                  variant="determinate"
                  value={st.state === 'flat' ? 0 : progressPercent}
                  sx={{ height: 8, borderRadius: 1 }}
                />
              </Box>

              <Box sx={{ display: 'flex', justifyContent: 'space-between' }}>
                <Typography variant="body2" color="text.secondary">
                  Scale-ins: <strong>{scaleInCount}/2</strong>
                </Typography>
                <Typography variant="body2" color="text.secondary">
                  Consecutive buy candles: <strong>{consecutiveBuys}</strong>
                </Typography>
              </Box>
            </CardContent>
          </Card>
        );
      })}
    </>
  );
}

function CooldownTimer() {
  const storeStates = useStrategyStore((s) => s.states);
  const cooldownEntries = Object.entries(storeStates).filter(
    ([, st]) => st.state === 'cooldown' && st.cooldown_until,
  );

  // Live countdown ticker
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    if (cooldownEntries.length === 0) return;
    const interval = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(interval);
  }, [cooldownEntries.length]);

  if (cooldownEntries.length === 0) {
    return (
      <Card sx={{ p: 2 }}>
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
          <TimerIcon fontSize="small" color="disabled" />
          <Typography variant="body2" color="text.secondary">No active cooldowns</Typography>
        </Box>
      </Card>
    );
  }

  return (
    <>
      {cooldownEntries.map(([key, st]) => {
        const cooldownMs = new Date(st.cooldown_until!).getTime();
        const remainingSec = Math.max(0, Math.floor((cooldownMs - now) / 1000));
        const expired = remainingSec <= 0;

        return (
          <Card key={key}>
            <CardContent>
              <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 1 }}>
                <TimerIcon fontSize="small" color={expired ? 'disabled' : 'primary'} />
                <Typography variant="body2" color="text.secondary">
                  Cooldown — {key.replace(':', ' / ')}
                </Typography>
              </Box>
              {expired ? (
                <Chip label="Expired" size="small" color="default" variant="outlined" />
              ) : (
                <Typography variant="h4" color="primary.main" sx={{ fontVariantNumeric: 'tabular-nums' }}>
                  {formatCountdown(remainingSec)}
                </Typography>
              )}
              <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5, fontSize: '0.75rem' }}>
                Until {new Date(st.cooldown_until!).toLocaleString()}
              </Typography>
            </CardContent>
          </Card>
        );
      })}
    </>
  );
}

// ── Page ─────────────────────────────────────────────────────────────────────

export default function StrategyPage() {
  return (
    <Box>
      <Typography variant="h5" sx={{ mb: 3 }}>
        Strategy
      </Typography>

      <Grid container spacing={2}>
        {/* ── Overview: state + signal ────────────────────────────── */}
        <Grid size={{ xs: 12 }}>
          <StrategyOverviewCard />
        </Grid>

        {/* ── Conditions Checklist ────────────────────────────────── */}
        <Grid size={{ xs: 12, md: 6 }}>
          <ConditionsChecklist />
        </Grid>

        {/* ── Scaled Entry Progress ──────────────────────────────── */}
        <Grid size={{ xs: 12, md: 6 }}>
          <ScaledEntryProgress />
        </Grid>

        {/* ── Cooldown Timer ─────────────────────────────────────── */}
        <Grid size={{ xs: 12 }}>
          <CooldownTimer />
        </Grid>
      </Grid>
    </Box>
  );
}
