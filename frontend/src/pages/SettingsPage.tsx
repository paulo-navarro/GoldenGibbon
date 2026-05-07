import { useState, useCallback, useEffect, useMemo } from 'react';
import {
  Box,
  Typography,
  List,
  ListItemButton,
  ListItemText,
  Paper,
  Grid,
  TextField,
  Switch,
  Button,
  Chip,
  Skeleton,
  Alert,
  Card,
  CardContent,
  CardActions,
} from '@mui/material';
import SettingsIcon from '@mui/icons-material/Settings';
import ShowChartIcon from '@mui/icons-material/ShowChart';
import { useQueryClient } from '@tanstack/react-query';
import {
  useNamespaceList,
  useNamespaceConfig,
  useUpdateNamespaceConfig,
  useResetNamespaceConfig,
  useStrategyOverview,
  useToggleStrategy,
} from '../api';
import type { NamespaceFieldMeta } from '../api';

function labelFromKey(key: string): string {
  return key
    .replace(/_/g, ' ')
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

function NamespaceEditor({ namespace, onSaved }: { namespace: string; onSaved?: () => void }) {
  const { data, isLoading, error } = useNamespaceConfig(namespace);
  const { update, isPending, isError, isSuccess } = useUpdateNamespaceConfig(namespace);
  const resetMutation = useResetNamespaceConfig(namespace);

  const [edits, setEdits] = useState<Record<string, unknown>>({});
  const [statusMsg, setStatusMsg] = useState<string | null>(null);

  useEffect(() => {
    setEdits({});
    setStatusMsg(null);
  }, [namespace]);

  useEffect(() => {
    if (isSuccess) {
      setEdits({});
      setStatusMsg('Saved');
      onSaved?.();
      const t = setTimeout(() => setStatusMsg(null), 2000);
      return () => clearTimeout(t);
    }
    if (isError) {
      setStatusMsg('Failed to save');
      const t = setTimeout(() => setStatusMsg(null), 3000);
      return () => clearTimeout(t);
    }
  }, [isSuccess, isError, onSaved]);

  useEffect(() => {
    if (resetMutation.isSuccess) {
      setEdits({});
      setStatusMsg('Reset to defaults');
      onSaved?.();
      const t = setTimeout(() => setStatusMsg(null), 2000);
      return () => clearTimeout(t);
    }
  }, [resetMutation.isSuccess, onSaved]);

  const handleChange = useCallback((name: string, value: unknown) => {
    setEdits((prev) => ({ ...prev, [name]: value }));
  }, []);

  const handleApply = useCallback(() => {
    if (Object.keys(edits).length === 0) return;
    update(edits);
  }, [edits, update]);

  const hasEdits = Object.keys(edits).length > 0;

  if (isLoading) return <Skeleton variant="rounded" height={200} />;
  if (error) return <Alert severity="error">Failed to load {namespace} config</Alert>;
  if (!data) return null;

  return (
    <Box>
      <Typography variant="h6" sx={{ mb: 2 }}>{labelFromKey(namespace)}</Typography>

      <Grid container spacing={2}>
        {data.fields.map((field: NamespaceFieldMeta) => {
          const currentValue = field.name in edits ? edits[field.name] : field.value;

          if (field.type === 'bool') {
            return (
              <Grid size={{ xs: 12, sm: 6, md: 4 }} key={field.name}>
                <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', py: 0.5 }}>
                  <Typography variant="body2">{labelFromKey(field.name)}</Typography>
                  <Switch
                    size="small"
                    checked={!!currentValue}
                    onChange={(_, checked) => handleChange(field.name, checked)}
                  />
                </Box>
                {field.description && (
                  <Typography variant="caption" color="text.secondary">{field.description}</Typography>
                )}
                {namespace === 'shorts' && field.name === 'enabled' && (
                  <Typography variant="caption" color="warning.main" sx={{ display: 'block', mt: 0.5 }}>
                    ⚠️ Enabling shorts activates Binance Margin borrowing. Use with caution.
                  </Typography>
                )}
              </Grid>
            );
          }

          if (field.type === 'dict' || field.type === 'list') return null;

          return (
            <Grid size={{ xs: 12, sm: 6, md: 4 }} key={field.name}>
              <TextField
                fullWidth
                size="small"
                label={labelFromKey(field.name)}
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

      <Box sx={{ display: 'flex', gap: 1, mt: 3, alignItems: 'center' }}>
        <Button
          variant="contained"
          size="small"
          onClick={handleApply}
          disabled={!hasEdits || isPending}
        >
          {isPending ? 'Saving...' : 'Apply'}
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
            color={statusMsg === 'Saved' ? 'success' : statusMsg.includes('Reset') ? 'info' : 'error'}
            variant="outlined"
          />
        )}
      </Box>
    </Box>
  );
}

const TRADING_MODE_KEY = '_trading_mode';
const MERGED_NAMESPACES = new Set(['paper_trading', 'live_trading']);

function TradingModeEditor() {
  const queryClient = useQueryClient();

  const refreshLive = useCallback(
    () => { queryClient.invalidateQueries({ queryKey: ['namespace-config', 'live_trading'] }); },
    [queryClient],
  );
  const refreshPaper = useCallback(
    () => { queryClient.invalidateQueries({ queryKey: ['namespace-config', 'paper_trading'] }); },
    [queryClient],
  );

  return (
    <Box>
      <Typography variant="h6" sx={{ mb: 2 }}>Trading Mode</Typography>
      <Grid container spacing={2}>
        <Grid size={{ xs: 12, md: 6 }}>
          <Paper variant="outlined" sx={{ p: 2 }}>
            <NamespaceEditor namespace="live_trading" onSaved={refreshPaper} />
          </Paper>
        </Grid>
        <Grid size={{ xs: 12, md: 6 }}>
          <Paper variant="outlined" sx={{ p: 2 }}>
            <NamespaceEditor namespace="paper_trading" onSaved={refreshLive} />
          </Paper>
        </Grid>
      </Grid>
    </Box>
  );
}

function StrategyToggles() {
  const { data, isLoading, error } = useStrategyOverview();
  const toggle = useToggleStrategy();

  if (isLoading) return <Skeleton variant="rounded" height={80} />;
  if (error) return <Alert severity="error" variant="outlined">Failed to load strategies</Alert>;
  if (!data) return null;

  return (
    <Box sx={{ mb: 3 }}>
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 2 }}>
        <ShowChartIcon color="primary" />
        <Typography variant="h6">Strategies</Typography>
      </Box>
      <Grid container spacing={2}>
        {data.strategies.map((s) => (
          <Grid size={{ xs: 12, sm: 6, md: 4 }} key={s.name}>
            <Card variant="outlined">
              <CardContent sx={{ pb: 0 }}>
                <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                  <Typography variant="subtitle1" sx={{ fontWeight: 600 }}>
                    {labelFromKey(s.name)}
                  </Typography>
                  <Chip
                    label={s.enabled ? 'Active' : 'Disabled'}
                    size="small"
                    color={s.enabled ? 'success' : 'default'}
                    variant={s.enabled ? 'filled' : 'outlined'}
                  />
                </Box>
              </CardContent>
              <CardActions sx={{ px: 2, pb: 1.5, justifyContent: 'flex-end' }}>
                <Switch
                  checked={s.enabled}
                  disabled={toggle.isPending}
                  onChange={(_, checked) => toggle.mutate({ name: s.name, enabled: checked })}
                />
              </CardActions>
            </Card>
          </Grid>
        ))}
      </Grid>
      {toggle.isError && (
        <Alert severity="error" variant="outlined" sx={{ mt: 1 }}>
          Failed to toggle strategy
        </Alert>
      )}
    </Box>
  );
}

export default function SettingsPage() {
  const { data: listData, isLoading } = useNamespaceList();
  const [selected, setSelected] = useState('');

  const menuItems = useMemo(() => {
    const raw = listData?.namespaces ?? [];
    const items: string[] = [];
    let tradingInserted = false;
    for (const ns of raw) {
      if (MERGED_NAMESPACES.has(ns)) {
        if (!tradingInserted) {
          items.push(TRADING_MODE_KEY);
          tradingInserted = true;
        }
      } else {
        items.push(ns);
      }
    }
    if (!tradingInserted && raw.some((ns) => MERGED_NAMESPACES.has(ns))) {
      items.unshift(TRADING_MODE_KEY);
    }
    return items;
  }, [listData]);

  useEffect(() => {
    if (menuItems.length > 0 && !selected) {
      setSelected(menuItems[0]);
    }
  }, [menuItems, selected]);

  return (
    <Box sx={{ p: 2 }}>
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 2 }}>
        <SettingsIcon color="primary" />
        <Typography variant="h5">Settings</Typography>
      </Box>

      <StrategyToggles />

      <Grid container spacing={2}>
        <Grid size={{ xs: 12, md: 3 }}>
          <Paper variant="outlined" sx={{ p: 1 }}>
            {isLoading ? (
              <Skeleton variant="rounded" height={300} />
            ) : (
              <List dense disablePadding>
                {menuItems.map((key) => (
                  <ListItemButton
                    key={key}
                    selected={key === selected}
                    onClick={() => setSelected(key)}
                    sx={{ borderRadius: 1 }}
                  >
                    <ListItemText
                      primary={key === TRADING_MODE_KEY ? 'Trading Mode' : labelFromKey(key)}
                    />
                  </ListItemButton>
                ))}
              </List>
            )}
          </Paper>
        </Grid>

        <Grid size={{ xs: 12, md: 9 }}>
          <Paper variant="outlined" sx={{ p: 2 }}>
            {selected === TRADING_MODE_KEY ? (
              <TradingModeEditor />
            ) : selected ? (
              <NamespaceEditor namespace={selected} />
            ) : (
              <Typography color="text.secondary">Select a category</Typography>
            )}
          </Paper>
        </Grid>
      </Grid>
    </Box>
  );
}
