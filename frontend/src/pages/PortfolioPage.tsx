// ── PortfolioPage ────────────────────────────────────────────────────────────
// Task 2.40 – Portfolio overview: balance cards, open positions table with
// trailing-stop / hard-stop levels, and full-size equity curve chart.

import Alert from '@mui/material/Alert';
import Box from '@mui/material/Box';
import Card from '@mui/material/Card';
import CardContent from '@mui/material/CardContent';
import Chip from '@mui/material/Chip';
import Grid from '@mui/material/Grid';
import Skeleton from '@mui/material/Skeleton';
import Table from '@mui/material/Table';
import TableBody from '@mui/material/TableBody';
import TableCell from '@mui/material/TableCell';
import TableContainer from '@mui/material/TableContainer';
import TableHead from '@mui/material/TableHead';
import TableRow from '@mui/material/TableRow';
import Typography from '@mui/material/Typography';

import { usePortfolio } from '../api';
import EquityCurveChart from '../components/EquityCurveChart';
import { usePortfolioStore } from '../stores/portfolioStore';

// ── Helpers ──────────────────────────────────────────────────────────────────

function fmt(value: string | null | undefined, decimals = 2): string {
  if (value == null) return '—';
  const n = parseFloat(value);
  return isNaN(n) ? '—' : n.toLocaleString(undefined, { minimumFractionDigits: decimals, maximumFractionDigits: decimals });
}

function pnlColor(value: string | null | undefined): string {
  if (value == null) return 'text.secondary';
  return parseFloat(value) >= 0 ? 'success.main' : 'error.main';
}

// ── Sub-components ───────────────────────────────────────────────────────────

function BalanceCards() {
  const { isLoading, error } = usePortfolio();
  const portfolio = usePortfolioStore((s) => s.balance);

  if (isLoading) {
    return (
      <>
        {[0, 1, 2, 3].map((i) => (
          <Grid size={{ xs: 12, sm: 6, md: 3 }} key={i}>
            <Skeleton variant="rounded" height={120} />
          </Grid>
        ))}
      </>
    );
  }
  if (error) {
    return (
      <Grid size={{ xs: 12 }}>
        <Alert severity="error" variant="outlined">Failed to load portfolio</Alert>
      </Grid>
    );
  }
  if (!portfolio) return null;

  const cards: { label: string; value: string; color?: string }[] = [
    { label: 'Total PnL', value: `$${fmt(portfolio.total_pnl)}`, color: pnlColor(portfolio.total_pnl) },
    { label: 'Equity', value: `$${fmt(portfolio.equity)}` },
    { label: 'USDT Balance', value: `$${fmt(portfolio.usdt_balance)}` },
    { label: 'Positions Value', value: `$${fmt(portfolio.positions_value)}` },
  ];

  return (
    <>
      {cards.map((c) => (
        <Grid size={{ xs: 12, sm: 6, md: 3 }} key={c.label}>
          <Card>
            <CardContent>
              <Typography variant="body2" color="text.secondary" gutterBottom>{c.label}</Typography>
              <Typography
                variant="h5"
                sx={{ fontVariantNumeric: 'tabular-nums' }}
                color={c.color ?? 'text.primary'}
              >
                {c.value}
              </Typography>
            </CardContent>
          </Card>
        </Grid>
      ))}
    </>
  );
}

function OpenPositionsTable() {
  const portfolio = usePortfolioStore((s) => s.balance);
  const positions = portfolio?.positions ?? [];

  if (positions.length === 0) {
    return (
      <Card sx={{ p: 2 }}>
        <Typography variant="body2" color="text.secondary" gutterBottom>
          Open Positions
        </Typography>
        <Typography color="text.secondary" sx={{ py: 3, textAlign: 'center' }}>
          No open positions
        </Typography>
      </Card>
    );
  }

  return (
    <Card>
      <CardContent sx={{ pb: '16px !important' }}>
        <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', mb: 1 }}>
          <Typography variant="body2" color="text.secondary">
            Open Positions
          </Typography>
          <Chip label={`${positions.length} open`} size="small" variant="outlined" />
        </Box>
        <TableContainer>
          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell>Symbol</TableCell>
                <TableCell align="right">Size</TableCell>
                <TableCell align="right">Entry Price</TableCell>
                <TableCell align="right">Entry Time</TableCell>
                <TableCell align="right">Highest Close</TableCell>
                <TableCell align="right" sx={{ color: 'warning.main' }}>Trail Stop</TableCell>
                <TableCell align="right" sx={{ color: 'warning.main' }}>Hard Stop</TableCell>
                <TableCell align="center">Scale-ins</TableCell>
                <TableCell align="right">Unreal. PnL</TableCell>
                <TableCell align="right">PnL %</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {positions.map((p) => (
                <TableRow key={p.symbol} hover>
                  <TableCell>
                    <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                      <Typography variant="body2" fontWeight={600}>{p.symbol}</Typography>
                      <Chip
                        label={p.side === 'short' ? 'SHORT' : 'LONG'}
                        size="small"
                        color={p.side === 'short' ? 'error' : 'success'}
                        variant="outlined"
                        sx={{ fontSize: '0.65rem', height: 18 }}
                      />
                    </Box>
                  </TableCell>
                  <TableCell align="right" sx={{ fontVariantNumeric: 'tabular-nums' }}>
                    {fmt(p.size, 4)}
                  </TableCell>
                  <TableCell align="right" sx={{ fontVariantNumeric: 'tabular-nums' }}>
                    ${fmt(p.entry_price)}
                  </TableCell>
                  <TableCell align="right">
                    <Typography variant="body2" sx={{ fontSize: '0.75rem' }}>
                      {new Date(p.entry_time).toLocaleString()}
                    </Typography>
                  </TableCell>
                  <TableCell align="right" sx={{ fontVariantNumeric: 'tabular-nums' }}>
                    ${fmt(p.highest_close)}
                  </TableCell>
                  <TableCell align="right" sx={{ fontVariantNumeric: 'tabular-nums', color: 'warning.main' }}>
                    ${fmt(p.trailing_stop_price)}
                  </TableCell>
                  <TableCell align="right" sx={{ fontVariantNumeric: 'tabular-nums', color: 'warning.main' }}>
                    ${fmt(p.hard_stop_price)}
                  </TableCell>
                  <TableCell align="center">{p.scale_in_count}/2</TableCell>
                  <TableCell
                    align="right"
                    sx={{ fontVariantNumeric: 'tabular-nums', color: pnlColor(p.unrealized_pnl) }}
                  >
                    {p.unrealized_pnl != null ? `$${fmt(p.unrealized_pnl)}` : '—'}
                  </TableCell>
                  <TableCell
                    align="right"
                    sx={{ fontVariantNumeric: 'tabular-nums', color: pnlColor(p.unrealized_pnl_percent) }}
                  >
                    {p.unrealized_pnl_percent != null ? `${fmt(p.unrealized_pnl_percent)}%` : '—'}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </TableContainer>
      </CardContent>
    </Card>
  );
}


// ── Page ─────────────────────────────────────────────────────────────────────

export default function PortfolioPage() {
  return (
    <Box>
      <Typography variant="h5" sx={{ mb: 3 }}>
        Portfolio
      </Typography>

      <Grid container spacing={2}>
        {/* ── Balance Cards ───────────────────────────────────────── */}
        <BalanceCards />

        {/* ── Equity Curve Chart ──────────────────────────────────── */}
        <Grid size={{ xs: 12 }}>
          <EquityCurveChart />
        </Grid>

        {/* ── Open Positions Table ────────────────────────────────── */}
        <Grid size={{ xs: 12 }}>
          <OpenPositionsTable />
        </Grid>
      </Grid>
    </Box>
  );
}
