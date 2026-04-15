// ── StrategyPage ─────────────────────────────────────────────────────────────
// Task 2.39 – Strategy overview: state badge, signal chip, conditions
// checklist, scaled-entry progress, and cooldown timer.
// Refactored: all per-strategy information grouped into one card each.

import { useEffect, useState } from 'react';
import Alert from '@mui/material/Alert';
import Box from '@mui/material/Box';
import Card from '@mui/material/Card';
import CardContent from '@mui/material/CardContent';
import Chip from '@mui/material/Chip';
import Divider from '@mui/material/Divider';
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

// ── Unified per-strategy card ─────────────────────────────────────────────────

const SCALE_STEPS = ['50%', '75%', '100%'];

function StrategyCard({ stratKey }: { stratKey: string }) {
  const st = useStrategyStore((s) => s.states[stratKey]);
  const sig = useStrategyStore((s) => s.signals[stratKey]);
  const condStore = useStrategyStore((s) => s.conditions[stratKey]);
  const { data: portfolio } = usePortfolio();

  const cond: Record<string, unknown> | null =
    (condStore as unknown as Record<string, unknown> | undefined) ??
    ((sig?.conditions as unknown as Record<string, unknown> | undefined) ?? null);

  // Live countdown ticker (only while in cooldown)
  const [now, setNow] = useState(Date.now());
  const inCooldown = st?.state === 'cooldown' && !!st?.cooldown_until;
  useEffect(() => {
    if (!inCooldown) return;
    const interval = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(interval);
  }, [inCooldown]);

  if (!st) return null;

  // Scaled entry
  const pos = portfolio?.positions?.find((p) => p.symbol === st.symbol);
  const scaleInCount = pos?.scale_in_count ?? 0;
  const consecutiveBuys = st.consecutive_buy_candles ?? 0;
  const progressPercent = Math.min(((scaleInCount + 1) / 3) * 100, 100);

  // Conditions
  const condEntries = cond
    ? Object.entries(cond).filter(([k]) => k !== 'symbol' && k !== 'strategy')
    : [];
  const metCount = condEntries.filter(([, v]) => v === true).length;

  // Cooldown
  const cooldownMs = st.cooldown_until ? new Date(st.cooldown_until).getTime() : 0;
  const remainingSec = Math.max(0, Math.floor((cooldownMs - now) / 1000));

  return (
    <Card>
      <CardContent>
        {/* ── Header ──────────────────────────────────────────────── */}
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 1, flexWrap: 'wrap' }}>
          <Typography variant="h6">{st.symbol}</Typography>
          <Chip label={st.strategy.replace(/_/g, ' ')} size="small" variant="outlined" />
          <Chip label={st.state} size="small" color={stateColor(st.state)} variant="outlined" />
          <Chip
            label={sig?.signal ?? 'hold'}
            size="small"
            color={signalColor(sig?.signal ?? 'hold')}
            variant="filled"
          />
          {st.updated_at && (
            <Typography variant="caption" color="text.secondary" sx={{ ml: 'auto' }}>
              Updated {new Date(st.updated_at).toLocaleString()}
            </Typography>
          )}
        </Box>

        <Divider sx={{ my: 1.5 }} />

        {/* ── Conditions + Scaled Entry ────────────────────────────── */}
        <Grid container spacing={2}>
          {/* Conditions */}
          <Grid size={{ xs: 12, md: 6 }}>
            <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', mb: 1 }}>
              <Typography variant="body2" color="text.secondary">Conditions</Typography>
              {condEntries.length > 0 && (
                <Chip
                  label={`${metCount}/${condEntries.length}`}
                  size="small"
                  color={metCount === condEntries.length ? 'success' : 'default'}
                  variant="outlined"
                />
              )}
            </Box>
            {condEntries.length === 0 ? (
              <Typography variant="body2" color="text.secondary" sx={{ textAlign: 'center', py: 1 }}>
                No conditions data yet
              </Typography>
            ) : (
              <List dense disablePadding>
                {condEntries.map(([name, value]) => (
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
            )}
          </Grid>

          {/* Scaled Entry */}
          <Grid size={{ xs: 12, md: 6 }}>
            <Typography variant="body2" color="text.secondary" gutterBottom>Scaled Entry</Typography>
            <Box sx={{ mb: 2 }}>
              <Box sx={{ display: 'flex', justifyContent: 'space-between', mb: 0.5 }}>
                {SCALE_STEPS.map((label, i) => (
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
                Buy candles: <strong>{consecutiveBuys}</strong>
              </Typography>
            </Box>
          </Grid>
        </Grid>

        {/* ── Cooldown ─────────────────────────────────────────────── */}
        {inCooldown && (
          <>
            <Divider sx={{ my: 1.5 }} />
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, flexWrap: 'wrap' }}>
              <TimerIcon fontSize="small" color={remainingSec <= 0 ? 'disabled' : 'primary'} />
              <Typography variant="body2" color="text.secondary">Cooldown</Typography>
              {remainingSec <= 0 ? (
                <Chip label="Expired" size="small" color="default" variant="outlined" />
              ) : (
                <Typography variant="h5" color="primary.main" sx={{ fontVariantNumeric: 'tabular-nums' }}>
                  {formatCountdown(remainingSec)}
                </Typography>
              )}
              <Typography variant="body2" color="text.secondary" sx={{ fontSize: '0.75rem' }}>
                until {new Date(st.cooldown_until!).toLocaleString()}
              </Typography>
            </Box>
          </>
        )}
      </CardContent>
    </Card>
  );
}

// ── Page ─────────────────────────────────────────────────────────────────────

export default function StrategyPage() {
  const { isLoading: statesLoading, error: statesError } = useStrategyState();
  const { isLoading: signalsLoading, error: signalsError } = useStrategySignals();

  const storeStates = useStrategyStore((s) => s.states);
  const storeSignals = useStrategyStore((s) => s.signals);
  const storeConditions = useStrategyStore((s) => s.conditions);

  const isLoading = statesLoading || signalsLoading;
  const error = statesError || signalsError;

  const stratKeys = Array.from(
    new Set([
      ...Object.keys(storeStates),
      ...Object.keys(storeSignals),
      ...Object.keys(storeConditions),
    ]),
  );

  if (isLoading) {
    return (
      <Box>
        <Typography variant="h5" sx={{ mb: 3 }}>Strategy</Typography>
        <Skeleton variant="rounded" height={300} />
      </Box>
    );
  }

  if (error) {
    return (
      <Box>
        <Typography variant="h5" sx={{ mb: 3 }}>Strategy</Typography>
        <Alert severity="error" variant="outlined">Failed to load strategy state</Alert>
      </Box>
    );
  }

  if (stratKeys.length === 0) {
    return (
      <Box>
        <Typography variant="h5" sx={{ mb: 3 }}>Strategy</Typography>
        <Card sx={{ p: 2 }}>
          <Typography color="text.secondary" sx={{ textAlign: 'center', py: 2 }}>
            No active strategies — start a backtest or paper-trade session to see data here.
          </Typography>
        </Card>
      </Box>
    );
  }

  return (
    <Box>
      <Typography variant="h5" sx={{ mb: 3 }}>Strategy</Typography>
      <Grid container spacing={2}>
        {stratKeys.map((k) => (
          <Grid key={k} size={{ xs: 12 }}>
            <StrategyCard stratKey={k} />
          </Grid>
        ))}
      </Grid>
    </Box>
  );
}
