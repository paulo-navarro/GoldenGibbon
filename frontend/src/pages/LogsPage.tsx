// ── LogsPage ──────────────────────────────────────────────────────────────────
// Task 2.44 – Real-time log stream with level filtering and severity chips.

import { useMemo, useRef, useState } from 'react';
import Alert from '@mui/material/Alert';
import Box from '@mui/material/Box';
import Card from '@mui/material/Card';
import CardContent from '@mui/material/CardContent';
import Chip from '@mui/material/Chip';
import Grid from '@mui/material/Grid';
import MenuItem from '@mui/material/MenuItem';
import Skeleton from '@mui/material/Skeleton';
import TextField from '@mui/material/TextField';
import Typography from '@mui/material/Typography';

import { useLogs } from '../api';
import { useSystemStore } from '../stores/systemStore';

// ── Types ─────────────────────────────────────────────────────────────────────

type LogLevel = 'INFO' | 'WARNING' | 'ERROR' | 'DEBUG' | string;

interface ParsedLog {
  raw: string;
  level: LogLevel;
  timestamp: string | null;
  message: string;
}

// ── Helpers ──────────────────────────────────────────────────────────────────

function parseLine(line: string): ParsedLog {
  try {
    const obj = JSON.parse(line);
    return {
      raw: line,
      level: (obj.level ?? obj.levelname ?? 'INFO').toUpperCase() as LogLevel,
      timestamp: obj.timestamp ?? obj.time ?? obj.asctime ?? null,
      message: obj.message ?? obj.msg ?? obj.event ?? line,
    };
  } catch {
    // Not JSON — return raw
    return { raw: line, level: 'INFO', timestamp: null, message: line };
  }
}

function levelColor(level: LogLevel): 'default' | 'warning' | 'error' | 'primary' | 'success' {
  const l = level.toUpperCase();
  if (l === 'ERROR' || l === 'CRITICAL') return 'error';
  if (l === 'WARNING' || l === 'WARN') return 'warning';
  if (l === 'DEBUG') return 'primary';
  return 'default';
}

function levelTextColor(level: LogLevel): string {
  const l = level.toUpperCase();
  if (l === 'ERROR' || l === 'CRITICAL') return 'error.main';
  if (l === 'WARNING' || l === 'WARN') return 'warning.main';
  if (l === 'DEBUG') return 'primary.main';
  return 'text.secondary';
}

// ── Sub-components ────────────────────────────────────────────────────────────

interface Filters {
  level: string;
  search: string;
}

function LogFilters({ filters, onChange }: { filters: Filters; onChange: (f: Filters) => void }) {
  return (
    <Card sx={{ p: 2 }}>
      <Box sx={{ display: 'flex', gap: 2, flexWrap: 'wrap', alignItems: 'center' }}>
        <TextField
          select size="small" label="Level" value={filters.level}
          onChange={(e) => onChange({ ...filters, level: e.target.value })}
          sx={{ minWidth: 130 }}
        >
          <MenuItem value="">All Levels</MenuItem>
          <MenuItem value="info">INFO</MenuItem>
          <MenuItem value="warning">WARNING</MenuItem>
          <MenuItem value="error">ERROR</MenuItem>
        </TextField>
        <TextField
          size="small" label="Search logs…" value={filters.search}
          onChange={(e) => onChange({ ...filters, search: e.target.value })}
          sx={{ minWidth: 240, flexGrow: 1 }}
        />
      </Box>
    </Card>
  );
}

function LogStats({ lines }: { lines: ParsedLog[] }) {
  const counts = useMemo(() => {
    const c: Record<string, number> = {};
    for (const l of lines) {
      c[l.level] = (c[l.level] ?? 0) + 1;
    }
    return c;
  }, [lines]);

  return (
    <Box sx={{ display: 'flex', gap: 1, flexWrap: 'wrap', alignItems: 'center' }}>
      <Typography variant="body2" color="text.secondary">
        {lines.length} lines
      </Typography>
      {Object.entries(counts).map(([level, count]) => (
        <Chip
          key={level}
          label={`${level}: ${count}`}
          size="small"
          color={levelColor(level)}
          variant="outlined"
        />
      ))}
    </Box>
  );
}

function LogStream({ lines }: { lines: ParsedLog[] }) {
  const containerRef = useRef<HTMLDivElement>(null);

  if (lines.length === 0) {
    return (
      <Box sx={{ py: 4, textAlign: 'center' }}>
        <Typography color="text.secondary">No log lines match current filters</Typography>
      </Box>
    );
  }

  return (
    <Box
      ref={containerRef}
      sx={{
        maxHeight: 580,
        overflowY: 'auto',
        fontFamily: 'monospace',
        fontSize: '0.78rem',
        lineHeight: 1.7,
      }}
    >
      {lines.map((l, i) => (
        <Box
          key={i}
          sx={{
            display: 'flex',
            gap: 1.5,
            alignItems: 'flex-start',
            borderBottom: '1px solid rgba(255,255,255,0.04)',
            py: 0.25,
            '&:hover': { bgcolor: 'rgba(255,255,255,0.025)' },
          }}
        >
          <Chip
            label={l.level}
            size="small"
            color={levelColor(l.level)}
            variant="outlined"
            sx={{ fontSize: '0.65rem', height: 18, minWidth: 60, flexShrink: 0, mt: 0.2 }}
          />
          {l.timestamp && (
            <Typography
              component="span"
              sx={{ color: 'text.secondary', fontSize: '0.72rem', flexShrink: 0, mt: 0.25 }}
            >
              {new Date(l.timestamp).toLocaleTimeString()}
            </Typography>
          )}
          <Typography
            component="span"
            sx={{ color: levelTextColor(l.level), wordBreak: 'break-word', flexGrow: 1 }}
          >
            {l.message}
          </Typography>
        </Box>
      ))}
    </Box>
  );
}

// ── Page ──────────────────────────────────────────────────────────────────────

export default function LogsPage() {
  const [filters, setFilters] = useState<Filters>({ level: '', search: '' });
  // REST fetch — re-fetches when level filter changes
  const { isLoading, error } = useLogs({
    lines: 500,
    level: (filters.level || undefined) as ('info' | 'warning' | 'error') | undefined,
  });

  // Zustand store holds all log lines (REST + WS appendLog)
  const storeLogs = useSystemStore((s) => s.logs);

  const parsedLines = useMemo(() => storeLogs.map(parseLine), [storeLogs]);

  const visibleLines = useMemo(() => {
    let filtered = parsedLines;
    if (filters.level) {
      const lvl = filters.level.toUpperCase();
      filtered = filtered.filter((l) => l.level === lvl);
    }
    if (filters.search.trim()) {
      const q = filters.search.toLowerCase();
      filtered = filtered.filter((l) => l.message.toLowerCase().includes(q) || l.raw.toLowerCase().includes(q));
    }
    return filtered;
  }, [parsedLines, filters]);

  return (
    <Box>
      <Typography variant="h5" sx={{ mb: 3 }}>Logs</Typography>

      <Grid container spacing={2}>
        {/* ── Filters ──────────────────────────────────────────────── */}
        <Grid size={{ xs: 12 }}>
          <LogFilters filters={filters} onChange={setFilters} />
        </Grid>

        {/* ── Log Stream ───────────────────────────────────────────── */}
        <Grid size={{ xs: 12 }}>
          {isLoading ? (
            <Skeleton variant="rounded" height={400} />
          ) : error ? (
            <Alert severity="error" variant="outlined">Failed to load logs</Alert>
          ) : (
            <Card>
              <CardContent sx={{ pb: '16px !important' }}>
                <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', mb: 1 }}>
                  <LogStats lines={visibleLines} />
                </Box>
                <LogStream lines={visibleLines} />
              </CardContent>
            </Card>
          )}
        </Grid>
      </Grid>
    </Box>
  );
}
