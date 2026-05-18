import { useEffect, useMemo, useState } from 'react';
import { useParams } from 'react-router-dom';
import Accordion from '@mui/material/Accordion';
import AccordionDetails from '@mui/material/AccordionDetails';
import AccordionSummary from '@mui/material/AccordionSummary';
import Alert from '@mui/material/Alert';
import Box from '@mui/material/Box';
import Button from '@mui/material/Button';
import Chip from '@mui/material/Chip';
import Divider from '@mui/material/Divider';
import Grid from '@mui/material/Grid';
import LinearProgress from '@mui/material/LinearProgress';
import List from '@mui/material/List';
import ListItem from '@mui/material/ListItem';
import ListItemIcon from '@mui/material/ListItemIcon';
import ListItemText from '@mui/material/ListItemText';
import Skeleton from '@mui/material/Skeleton';
import Tooltip from '@mui/material/Tooltip';
import Typography from '@mui/material/Typography';
import CheckCircleIcon from '@mui/icons-material/CheckCircle';
import CancelIcon from '@mui/icons-material/Cancel';
import ExpandMoreIcon from '@mui/icons-material/ExpandMore';
import TimerIcon from '@mui/icons-material/Timer';

import { useStrategyState, useStrategySignals, usePortfolio, useExitProximity, useResetKillSwitch, useStrategyOverview } from '../api';
import { useStrategyStore } from '../stores/strategyStore';
import type { MacroFilterInfo, RegimeInfo } from '../stores/strategyStore';
import type { Signal, StrategyState } from '../types/enums';
import type { ExitConditionStatus } from '../types/portfolio';

// ── Helpers ──────────────────────────────────────────────────────────────────

function signalColor(signal: Signal): 'success' | 'error' | 'warning' | 'default' {
  if (signal === 'buy') return 'success';
  if (signal === 'sell_full' || signal === 'sell_half') return 'error';
  if (signal === 'short') return 'warning';
  return 'default';
}

function stateColor(state: StrategyState): 'primary' | 'success' | 'warning' | 'default' {
  if (state === 'position') return 'success';
  if (state === 'reduced') return 'warning';
  if (state === 'cooldown') return 'primary';
  return 'default';
}

function regimeColor(regime: RegimeInfo['regime']): 'success' | 'warning' | 'default' {
  if (regime === 'trending') return 'success';
  if (regime === 'ranging') return 'warning';
  return 'default';
}

function labelFromKey(key: string): string {
  return key
    .replace(/_/g, ' ')
    .replace(/\b\w/g, (c) => c.toUpperCase())
    .replace(/\bEma\b/g, 'EMA')
    .replace(/\bAdx\b/g, 'ADX')
    .replace(/\bRsi\b/g, 'RSI')
    .replace(/\bBb\b/g, 'BB');
}

function formatCountdown(totalSeconds: number): string {
  if (totalSeconds <= 0) return '00:00';
  const m = Math.floor(totalSeconds / 60);
  const s = totalSeconds % 60;
  return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
}

type CondEntry = [string, unknown];

function getCondEntries(
  condStore: unknown,
  sig: { conditions?: unknown } | undefined,
): CondEntry[] {
  const cond: Record<string, unknown> | null =
    (condStore as Record<string, unknown> | undefined) ??
    ((sig?.conditions as Record<string, unknown> | undefined) ?? null);
  if (!cond) return [];
  return Object.entries(cond).filter(([k]) => k !== 'symbol' && k !== 'strategy');
}

// ── Conditions progress bar ─────────────────────────────────────────────────

function ConditionsBar({ entries }: { entries: CondEntry[] }) {
  if (entries.length === 0) return null;

  return (
    <Tooltip
      title={entries.map(([name, v]) => `${v === true ? '✓' : '✗'} ${labelFromKey(name)}`).join('\n')}
      slotProps={{ tooltip: { sx: { whiteSpace: 'pre-line', fontSize: '0.75rem' } } }}
    >
      <Box sx={{ display: 'flex', gap: '2px', alignItems: 'center', minWidth: 80 }}>
        {entries.map(([name, value]) => (
          <Box
            key={name}
            sx={{
              flex: 1,
              height: 6,
              borderRadius: 0.5,
              bgcolor: value === true ? 'success.main' : 'action.disabled',
              transition: 'background-color 0.3s',
            }}
          />
        ))}
      </Box>
    </Tooltip>
  );
}

// ── Exit proximity sub-components ───────────────────────────────────────────

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

function ExitConditionRow({ cond }: { cond: ExitConditionStatus }) {
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

// ── Strategy card (collapsible) ──────────────────────────────────────────────

const SCALE_STEPS = ['50%', '75%', '100%'];

function proximityColor(pct: number): 'success' | 'warning' | 'error' {
  if (pct > 0.15) return 'success';
  if (pct > 0.05) return 'warning';
  return 'error';
}

function pctLabel(pct: number): string {
  return `${(pct * 100).toFixed(1)}%`;
}

function StrategyCard({ stratKey }: { stratKey: string }) {
  const st = useStrategyStore((s) => s.states[stratKey]);
  const sig = useStrategyStore((s) => s.signals[stratKey]);
  const condStore = useStrategyStore((s) => s.conditions[stratKey]);
  const regime = useStrategyStore((s) => s.regimes[stratKey]);
  const { data: portfolio } = usePortfolio();
  const { data: exitProximityData } = useExitProximity();

  const condEntries = useMemo(() => getCondEntries(condStore, sig), [condStore, sig]);
  const metCount = condEntries.filter(([, v]) => v === true).length;

  const [now, setNow] = useState(Date.now());
  const inCooldown = st?.state === 'cooldown' && !!st?.cooldown_until;
  useEffect(() => {
    if (!inCooldown) return;
    const interval = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(interval);
  }, [inCooldown]);

  const killSwitchReset = useResetKillSwitch(st?.symbol ?? '', st?.strategy ?? '');

  if (!st) return null;

  const killSwitchTriggered = !!(st.state_data as Record<string, unknown> | null)?.kill_switch_triggered;
  const exitProx = exitProximityData?.find((d) => d.symbol === st.symbol && d.strategy === st.strategy);

  const pos = portfolio?.positions?.find((p) => p.symbol === st.symbol);
  const scaleInCount = pos?.scale_in_count ?? 0;
  const consecutiveBuys = st.consecutive_buy_candles ?? 0;
  const progressPercent = Math.min(((scaleInCount + 1) / 3) * 100, 100);

  const cooldownMs = st.cooldown_until ? new Date(st.cooldown_until).getTime() : 0;
  const remainingSec = Math.max(0, Math.floor((cooldownMs - now) / 1000));

  return (
    <Accordion
      defaultExpanded={false}
      disableGutters
      sx={{
        '&:before': { display: 'none' },
        ...(killSwitchTriggered && {
          border: '2px solid',
          borderColor: 'error.main',
        }),
      }}
    >
      <AccordionSummary
        expandIcon={<ExpandMoreIcon />}
        sx={{ px: 2, '& .MuiAccordionSummary-content': { alignItems: 'center', gap: 1, flexWrap: 'wrap', my: 1 } }}
      >
        <Typography variant="subtitle1" sx={{ fontWeight: 600 }}>{st.symbol}</Typography>
        <Chip label={st.strategy.replace(/_/g, ' ')} size="small" variant="outlined" />
        <Chip label={st.state} size="small" color={stateColor(st.state)} variant="outlined" />
        <Chip
          label={sig?.signal ?? 'hold'}
          size="small"
          color={signalColor(sig?.signal ?? 'hold')}
          variant="filled"
        />
        {regime && (
          <Chip label={regime.regime} size="small" color={regimeColor(regime.regime)} variant="outlined" />
        )}
        {killSwitchTriggered && (
          <Chip label="KILL SWITCH" size="small" color="error" variant="filled" />
        )}

        {condEntries.length > 0 && (
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, ml: 1 }}>
            <ConditionsBar entries={condEntries} />
            <Typography variant="caption" color="text.secondary" sx={{ fontVariantNumeric: 'tabular-nums' }}>
              {metCount}/{condEntries.length}
            </Typography>
          </Box>
        )}

        {st.updated_at && (
          <Typography variant="caption" color="text.secondary" sx={{ ml: 'auto' }}>
            {new Date(st.updated_at).toLocaleString()}
          </Typography>
        )}
      </AccordionSummary>

      <AccordionDetails sx={{ px: 2, pt: 0 }}>
        <Divider sx={{ mb: 1.5 }} />

        <Grid container spacing={2}>
          {/* Conditions */}
          <Grid size={{ xs: 12, md: 4 }}>
            <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>Conditions</Typography>
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
                    <ListItemText primary={labelFromKey(name)} primaryTypographyProps={{ variant: 'body2' }} />
                  </ListItem>
                ))}
              </List>
            )}
          </Grid>

          {/* Scaled Entry */}
          <Grid size={{ xs: 12, md: 4 }}>
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
                value={st.state === 'position' ? progressPercent : 0}
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

          {/* Exit Proximity */}
          <Grid size={{ xs: 12, md: 4 }}>
            <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>Exit Proximity</Typography>
            {exitProx ? (
              <>
                <StopBar label="Hard stop" pct={exitProx.hard_stop_pct} />
                <StopBar label="Trailing stop" pct={exitProx.trailing_stop_pct} />
                {exitProx.time_stop_pct !== null && <TimeStopBar pct={exitProx.time_stop_pct} />}
                {exitProx.exit_conditions.length > 0 && (
                  <Box sx={{ mt: 1.5 }}>
                    <Typography variant="caption" color="text.secondary" sx={{ mb: 0.5, display: 'block' }}>
                      Exit conditions
                    </Typography>
                    {exitProx.exit_conditions.map((c) => (
                      <ExitConditionRow key={c.name} cond={c} />
                    ))}
                  </Box>
                )}
              </>
            ) : (
              <Typography variant="body2" color="text.secondary" sx={{ textAlign: 'center', py: 1 }}>
                No exit data
              </Typography>
            )}
          </Grid>
        </Grid>

        {/* Cooldown */}
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

        {/* Kill-switch reset */}
        {killSwitchTriggered && (
          <>
            <Divider sx={{ my: 1.5 }} />
            <Alert
              severity="error"
              variant="outlined"
              action={
                <Button
                  color="error"
                  size="small"
                  variant="contained"
                  disabled={killSwitchReset.isPending}
                  onClick={() => killSwitchReset.mutate()}
                >
                  {killSwitchReset.isPending ? 'Resetting...' : 'Reset Kill Switch'}
                </Button>
              }
            >
              Kill switch triggered
              {(st.state_data as Record<string, unknown> | null)?.kill_switch_reason
                ? ` — ${(st.state_data as Record<string, unknown>).kill_switch_reason}`
                : ''}
            </Alert>
            {killSwitchReset.isSuccess && (
              <Alert severity="success" variant="outlined" sx={{ mt: 1 }}>
                Kill switch reset — trading resumes on next tick
              </Alert>
            )}
            {killSwitchReset.isError && (
              <Alert severity="error" variant="outlined" sx={{ mt: 1 }}>
                Reset failed — try again
              </Alert>
            )}
          </>
        )}

      </AccordionDetails>
    </Accordion>
  );
}

// ── Page ─────────────────────────────────────────────────────────────────────

export default function StrategyPage() {
  const { strategyName } = useParams<{ strategyName: string }>();

  const { isLoading: statesLoading, error: statesError } = useStrategyState();
  const { isLoading: signalsLoading, error: signalsError } = useStrategySignals();
  const { data: overviewData } = useStrategyOverview();
  const isDisabled = strategyName
    ? overviewData?.strategies?.some((s) => s.name === strategyName && !s.enabled) ?? false
    : false;

  const storeStates = useStrategyStore((s) => s.states);
  const storeSignals = useStrategyStore((s) => s.signals);
  const storeConditions = useStrategyStore((s) => s.conditions);
  const macroFilter = useStrategyStore((s) => s.macroFilter);

  const isLoading = statesLoading || signalsLoading;
  const error = statesError || signalsError;

  const stratKeys = useMemo(() => {
    const keys = Array.from(
      new Set([
        ...Object.keys(storeStates),
        ...Object.keys(storeSignals),
        ...Object.keys(storeConditions),
      ]),
    );

    const filtered = strategyName
      ? keys.filter((k) => k.endsWith(`:${strategyName}`))
      : keys;

    return filtered.sort((a, b) => {
      const aEntries = getCondEntries(storeConditions[a], storeSignals[a]);
      const bEntries = getCondEntries(storeConditions[b], storeSignals[b]);
      const aMet = aEntries.filter(([, v]) => v === true).length;
      const bMet = bEntries.filter(([, v]) => v === true).length;
      if (bMet !== aMet) return bMet - aMet;
      return a.localeCompare(b);
    });
  }, [storeStates, storeSignals, storeConditions, strategyName]);

  const title = strategyName
    ? strategyName.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())
    : 'Strategy';

  if (isLoading) {
    return (
      <Box sx={{ p: 2 }}>
        <Typography variant="h5" sx={{ mb: 3 }}>{title}</Typography>
        <Skeleton variant="rounded" height={300} />
      </Box>
    );
  }

  if (error) {
    return (
      <Box sx={{ p: 2 }}>
        <Typography variant="h5" sx={{ mb: 3 }}>{title}</Typography>
        <Alert severity="error" variant="outlined">Failed to load strategy state</Alert>
      </Box>
    );
  }

  if (stratKeys.length === 0) {
    return (
      <Box sx={{ p: 2 }}>
        <Typography variant="h5" sx={{ mb: 3 }}>{title}</Typography>
        <Typography color="text.secondary" sx={{ textAlign: 'center', py: 4 }}>
          No active pairs for this strategy.
        </Typography>
      </Box>
    );
  }

  return (
    <Box sx={{ p: 2 }}>
      <Typography variant="h5" sx={{ mb: 2 }}>{title}</Typography>
      {isDisabled && (
        <Alert severity="warning" variant="outlined" sx={{ mb: 2 }}>
          {title} is disabled. Enable it in Settings to resume trading.
        </Alert>
      )}
      {macroFilter?.longs_blocked && (
        <Alert severity="warning" variant="outlined" sx={{ mb: 2 }}>
          New LONGs paused — {macroFilter.reason}
        </Alert>
      )}
      <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1 }}>
        {stratKeys.map((k) => (
          <StrategyCard key={k} stratKey={k} />
        ))}
      </Box>
    </Box>
  );
}
