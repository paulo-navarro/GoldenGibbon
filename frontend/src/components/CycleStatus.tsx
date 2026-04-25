import { useEffect, useMemo, useState } from 'react';
import Box from '@mui/material/Box';
import Card from '@mui/material/Card';
import CardContent from '@mui/material/CardContent';
import Typography from '@mui/material/Typography';

import { useStrategyStore } from '../stores/strategyStore';

const CYCLE_MINUTES = [2, 17, 32, 47];

function getNextCycleTime(): Date {
  const now = new Date();
  const h = now.getHours();
  const m = now.getMinutes();

  for (const cm of CYCLE_MINUTES) {
    if (m < cm) {
      const next = new Date(now);
      next.setMinutes(cm, 0, 0);
      return next;
    }
  }
  const next = new Date(now);
  next.setHours(h + 1, CYCLE_MINUTES[0], 0, 0);
  return next;
}

function formatCountdown(ms: number): string {
  if (ms <= 0) return '00:00';
  const totalSec = Math.floor(ms / 1000);
  const min = Math.floor(totalSec / 60);
  const sec = totalSec % 60;
  return `${String(min).padStart(2, '0')}:${String(sec).padStart(2, '0')}`;
}

export default function CycleStatus() {
  const signals = useStrategyStore((s) => s.signals);

  const lastCycleTime = useMemo(() => {
    const timestamps = Object.values(signals)
      .map((s) => new Date(s.updated_at).getTime())
      .filter((t) => !isNaN(t));
    if (timestamps.length === 0) return null;
    return new Date(Math.max(...timestamps));
  }, [signals]);

  const [countdown, setCountdown] = useState(() =>
    formatCountdown(getNextCycleTime().getTime() - Date.now()),
  );

  useEffect(() => {
    const id = setInterval(() => {
      setCountdown(formatCountdown(getNextCycleTime().getTime() - Date.now()));
    }, 1000);
    return () => clearInterval(id);
  }, []);

  return (
    <Card>
      <CardContent>
        <Typography variant="body2" color="text.secondary" gutterBottom>
          Cycle Status
        </Typography>
        <Box sx={{ display: 'flex', gap: 3, alignItems: 'baseline' }}>
          <Box>
            <Typography variant="caption" color="text.secondary">
              Last cycle
            </Typography>
            <Typography variant="h6" sx={{ fontVariantNumeric: 'tabular-nums' }}>
              {lastCycleTime ? lastCycleTime.toLocaleTimeString() : '—'}
            </Typography>
          </Box>
          <Box>
            <Typography variant="caption" color="text.secondary">
              Next cycle in
            </Typography>
            <Typography variant="h6" sx={{ fontFamily: 'monospace', fontVariantNumeric: 'tabular-nums' }}>
              {countdown}
            </Typography>
          </Box>
        </Box>
      </CardContent>
    </Card>
  );
}
