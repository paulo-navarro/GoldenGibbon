// ── ConnectionStatus ─────────────────────────────────────────────────────────
// Task 2.45 – AppBar indicator showing WebSocket + API/DB/Redis health.

import Tooltip from '@mui/material/Tooltip';
import Box from '@mui/material/Box';
import Typography from '@mui/material/Typography';

import { useHealth } from '../api';
import { useSystemStore } from '../stores';
import type { ConnectionStatus as WsStatus } from '../hooks';

// ── Types ────────────────────────────────────────────────────────────────────

export interface ConnectionStatusProps {
  wsStatus: WsStatus;
  wsHealthy: boolean;
}

// ── Helpers ──────────────────────────────────────────────────────────────────

type DotColor = 'success.main' | 'warning.main' | 'error.main';

function wsDotColor(status: WsStatus, healthy: boolean): DotColor {
  if (status === 'connected' && healthy) return 'success.main';
  if (status === 'connecting' || status === 'reconnecting') return 'warning.main';
  return 'error.main';
}

function wsLabel(status: WsStatus, healthy: boolean): string {
  if (status === 'connected' && healthy) return 'WS';
  if (status === 'reconnecting') return 'WS↻';
  if (status === 'connecting') return 'WS…';
  return 'WS';
}

function Dot({ color }: { color: DotColor }) {
  return (
    <Box
      component="span"
      sx={{
        display: 'inline-block',
        width: 8,
        height: 8,
        borderRadius: '50%',
        bgcolor: color,
        flexShrink: 0,
      }}
    />
  );
}

function StatusItem({ color, label }: { color: DotColor; label: string }) {
  return (
    <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}>
      <Dot color={color} />
      <Typography variant="caption" sx={{ lineHeight: 1 }}>
        {label}
      </Typography>
    </Box>
  );
}

// ── Component ────────────────────────────────────────────────────────────────

export default function ConnectionStatus({ wsStatus, wsHealthy }: ConnectionStatusProps) {
  // Seed store via REST polling (every 30 s)
  useHealth();

  const health = useSystemStore((s) => s.health);

  const apiOk: boolean = health?.status === 'ok';
  const dbOk: boolean = health?.db ?? false;
  const redisOk: boolean = health?.redis ?? false;

  const dotWs = wsDotColor(wsStatus, wsHealthy);
  const dotApi: DotColor = apiOk ? 'success.main' : 'error.main';
  const dotDb: DotColor = dbOk ? 'success.main' : 'error.main';
  const dotRedis: DotColor = redisOk ? 'success.main' : 'error.main';

  const tooltipContent = (
    <Box sx={{ display: 'flex', flexDirection: 'column', gap: 0.5, p: 0.5 }}>
      <StatusItem color={dotWs} label={`WebSocket: ${wsStatus}`} />
      <StatusItem color={dotApi} label={`API: ${apiOk ? 'ok' : 'down'}`} />
      <StatusItem color={dotDb} label={`DB: ${dbOk ? 'ok' : 'down'}`} />
      <StatusItem color={dotRedis} label={`Redis: ${redisOk ? 'ok' : 'down'}`} />
    </Box>
  );

  return (
    <Tooltip title={tooltipContent} arrow placement="bottom-end">
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, cursor: 'default' }}>
        <StatusItem color={dotWs} label={wsLabel(wsStatus, wsHealthy)} />
        <StatusItem color={dotApi} label="API" />
        <StatusItem color={dotDb} label="DB" />
        <StatusItem color={dotRedis} label="Redis" />
      </Box>
    </Tooltip>
  );
}
