// ── StrategyPage ─────────────────────────────────────────────────────────────
// Task 2.39 – Strategy overview: state badge, signal chip, conditions
// checklist, scaled-entry progress, and cooldown timer.
// Refactored: all per-strategy information grouped into one card each.

import { useCallback, useEffect, useMemo, useState } from 'react';
import Accordion from '@mui/material/Accordion';
import AccordionDetails from '@mui/material/AccordionDetails';
import AccordionSummary from '@mui/material/AccordionSummary';
import Alert from '@mui/material/Alert';
import Box from '@mui/material/Box';
import Button from '@mui/material/Button';
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
import Switch from '@mui/material/Switch';
import TextField from '@mui/material/TextField';
import Typography from '@mui/material/Typography';
import CheckCircleIcon from '@mui/icons-material/CheckCircle';
import CancelIcon from '@mui/icons-material/Cancel';
import ExpandMoreIcon from '@mui/icons-material/ExpandMore';
import TimerIcon from '@mui/icons-material/Timer';
import TuneIcon from '@mui/icons-material/Tune';

import { useStrategyState, useStrategySignals, usePortfolio, useStrategyConfig, useUpdateStrategyConfig, useResetStrategyConfig } from '../api';
import type { FieldMeta } from '../api';
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
        {/* ── Parameter Tuning (task 5.5b) ───────────────────────── */}
        <ParameterTuning strategyName={st.strategy} />
      </CardContent>
    </Card>
  );
}

// ── Parameter Tuning (task 5.5b) ─────────────────────────────────────────────

const SOURCE_COLORS: Record<string, 'info' | 'default'> = {
  db: 'info',
  default: 'default',
};

function ParameterTuning({ strategyName }: { strategyName: string }) {
  const { data, isLoading, error } = useStrategyConfig(strategyName);
  const { update, isPending, isError, isSuccess } = useUpdateStrategyConfig(strategyName);
  const resetMutation = useResetStrategyConfig(strategyName);

  const [edits, setEdits] = useState<Record<string, unknown>>({});
  const [statusMsg, setStatusMsg] = useState<string | null>(null);

  useEffect(() => {
    if (isSuccess) {
      setEdits({});
      setStatusMsg('Saved to database');
      const t = setTimeout(() => setStatusMsg(null), 2000);
      return () => clearTimeout(t);
    }
    if (isError) {
      setStatusMsg('Failed to save');
      const t = setTimeout(() => setStatusMsg(null), 3000);
      return () => clearTimeout(t);
    }
  }, [isSuccess, isError]);

  useEffect(() => {
    if (resetMutation.isSuccess) {
      setEdits({});
      setStatusMsg('Reset to defaults');
      const t = setTimeout(() => setStatusMsg(null), 2000);
      return () => clearTimeout(t);
    }
  }, [resetMutation.isSuccess]);

  const grouped = useMemo(() => {
    if (!data) return {};
    const groups: Record<string, FieldMeta[]> = {};
    for (const field of data.fields) {
      const g = field.group;
      if (!groups[g]) groups[g] = [];
      groups[g].push(field);
    }
    return groups;
  }, [data]);

  const handleChange = useCallback((name: string, value: unknown) => {
    setEdits((prev) => ({ ...prev, [name]: value }));
  }, []);

  const handleApply = useCallback(() => {
    if (Object.keys(edits).length === 0) return;
    update(edits);
  }, [edits, update]);

  const handleReset = useCallback(() => {
    if (!data) return;
    const defaults: Record<string, unknown> = {};
    for (const field of data.fields) {
      if (field.default != null) {
        defaults[field.name] = field.default;
      }
    }
    update(defaults);
  }, [data, update]);

  const hasEdits = Object.keys(edits).length > 0;

  if (isLoading) return <Skeleton variant="rounded" height={60} />;
  if (error) return <Alert severity="error" variant="outlined" sx={{ mt: 1 }}>Failed to load config</Alert>;
  if (!data) return null;

  return (
    <Accordion sx={{ mt: 1 }}>
      <AccordionSummary expandIcon={<ExpandMoreIcon />}>
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
          <TuneIcon fontSize="small" color="primary" />
          <Typography variant="body2">Parameters</Typography>
        </Box>
      </AccordionSummary>
      <AccordionDetails>
        {Object.entries(grouped).map(([group, fields]) => (
          <Box key={group} sx={{ mb: 2 }}>
            <Typography variant="overline" color="text.secondary" sx={{ mb: 1, display: 'block' }}>
              {group}
            </Typography>
            <Grid container spacing={1.5}>
              {fields.map((field) => {
                const currentValue = field.name in edits ? edits[field.name] : field.value;

                if (field.type === 'bool') {
                  return (
                    <Grid size={{ xs: 12, sm: 6, md: 4 }} key={field.name}>
                      <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                        <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}>
                          <Typography variant="body2">{labelFromKey(field.name)}</Typography>
                          {field.source !== 'default' && (
                            <Chip label={field.source} size="small" color={SOURCE_COLORS[field.source]} sx={{ fontSize: 9, height: 16 }} />
                          )}
                        </Box>
                        <Switch
                          size="small"
                          checked={!!currentValue}
                          onChange={(_, checked) => handleChange(field.name, checked)}
                        />
                      </Box>
                    </Grid>
                  );
                }

                if (field.type === 'list') return null;

                return (
                  <Grid size={{ xs: 6, sm: 4, md: 3 }} key={field.name}>
                    <TextField
                      fullWidth
                      size="small"
                      label={`${labelFromKey(field.name)}${field.source !== 'default' ? ` [${field.source}]` : ''}`}
                      type={field.type === 'int' || field.type === 'float' ? 'number' : 'text'}
                      value={currentValue ?? ''}
                      onChange={(e) => {
                        const raw = e.target.value;
                        if (field.type === 'int') handleChange(field.name, raw === '' ? '' : parseInt(raw, 10));
                        else if (field.type === 'float') handleChange(field.name, raw === '' ? '' : parseFloat(raw));
                        else handleChange(field.name, raw);
                      }}
                      slotProps={{
                        htmlInput: {
                          min: field.min,
                          max: field.max,
                          step: field.type === 'float' ? 0.01 : 1,
                        },
                      }}
                      helperText={field.description}
                    />
                  </Grid>
                );
              })}
            </Grid>
          </Box>
        ))}

        <Box sx={{ display: 'flex', gap: 1, mt: 2, alignItems: 'center' }}>
          <Button
            variant="contained"
            size="small"
            onClick={handleApply}
            disabled={!hasEdits || isPending}
          >
            {isPending ? 'Applying...' : 'Apply'}
          </Button>
          <Button
            variant="outlined"
            size="small"
            color="warning"
            onClick={() => resetMutation.mutate()}
            disabled={isPending || resetMutation.isPending}
          >
            Reset to Defaults
          </Button>
          {statusMsg && (
            <Chip
              label={statusMsg}
              size="small"
              color={statusMsg === 'Applied' ? 'success' : 'error'}
              variant="outlined"
            />
          )}
        </Box>
      </AccordionDetails>
    </Accordion>
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
