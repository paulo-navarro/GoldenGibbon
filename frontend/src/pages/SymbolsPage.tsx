import { useCallback, useMemo, useState } from 'react';
import Alert from '@mui/material/Alert';
import Box from '@mui/material/Box';
import Button from '@mui/material/Button';
import Chip from '@mui/material/Chip';
import Dialog from '@mui/material/Dialog';
import DialogActions from '@mui/material/DialogActions';
import DialogContent from '@mui/material/DialogContent';
import DialogContentText from '@mui/material/DialogContentText';
import DialogTitle from '@mui/material/DialogTitle';
import Checkbox from '@mui/material/Checkbox';
import FormControlLabel from '@mui/material/FormControlLabel';
import FormGroup from '@mui/material/FormGroup';
import IconButton from '@mui/material/IconButton';
import Paper from '@mui/material/Paper';
import Skeleton from '@mui/material/Skeleton';
import Switch from '@mui/material/Switch';
import Table from '@mui/material/Table';
import TableBody from '@mui/material/TableBody';
import TableCell from '@mui/material/TableCell';
import TableContainer from '@mui/material/TableContainer';
import TableHead from '@mui/material/TableHead';
import TableRow from '@mui/material/TableRow';
import TextField from '@mui/material/TextField';
import Typography from '@mui/material/Typography';
import AddIcon from '@mui/icons-material/Add';
import DeleteIcon from '@mui/icons-material/Delete';
import {
  useSymbols,
  useAddSymbol,
  useDeleteSymbol,
  usePatchSymbol,
} from '../api';
import type { SymbolItem } from '../api';

const VALID_TIMEFRAMES = ['1m', '5m', '15m', '30m', '1h', '4h', '1d', '1w'];
const DEFAULT_TIMEFRAMES = ['15m', '1h'];

// ── Add Symbol Dialog ─────────────────────────────────────────────────────────

function AddSymbolDialog({
  open,
  onClose,
}: {
  open: boolean;
  onClose: () => void;
}) {
  const [symbol, setSymbol] = useState('');
  const [description, setDescription] = useState('');
  const [timeframes, setTimeframes] = useState<string[]>(DEFAULT_TIMEFRAMES);
  const addMutation = useAddSymbol();

  const handleToggleTf = (tf: string) => {
    setTimeframes((prev) =>
      prev.includes(tf) ? prev.filter((t) => t !== tf) : [...prev, tf],
    );
  };

  const handleSubmit = () => {
    addMutation.mutate(
      {
        symbol: symbol.toUpperCase().trim(),
        timeframes,
        description: description || undefined,
      },
      {
        onSuccess: () => {
          setSymbol('');
          setDescription('');
          setTimeframes(DEFAULT_TIMEFRAMES);
          onClose();
        },
      },
    );
  };

  const errorDetail = useMemo(() => {
    if (!addMutation.error) return null;
    const err = addMutation.error as { body?: { detail?: string } };
    return err.body?.detail ?? addMutation.error.message;
  }, [addMutation.error]);

  return (
    <Dialog open={open} onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle>Add Trading Pair</DialogTitle>
      <DialogContent>
        <TextField
          autoFocus
          label="Symbol"
          placeholder="e.g. DOTUSDT"
          fullWidth
          margin="normal"
          value={symbol}
          onChange={(e) => setSymbol(e.target.value)}
          helperText="Must end with USDT. Validated against Binance."
        />
        <TextField
          label="Description"
          placeholder="e.g. Polkadot / Tether"
          fullWidth
          margin="normal"
          value={description}
          onChange={(e) => setDescription(e.target.value)}
        />
        <Typography variant="body2" sx={{ mt: 2, mb: 1 }}>
          Timeframes
        </Typography>
        <FormGroup row>
          {VALID_TIMEFRAMES.map((tf) => (
            <FormControlLabel
              key={tf}
              control={
                <Checkbox
                  checked={timeframes.includes(tf)}
                  onChange={() => handleToggleTf(tf)}
                  size="small"
                />
              }
              label={tf}
            />
          ))}
        </FormGroup>
        {errorDetail && (
          <Alert severity="error" sx={{ mt: 2 }}>
            {errorDetail}
          </Alert>
        )}
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button
          onClick={handleSubmit}
          variant="contained"
          disabled={!symbol.trim() || timeframes.length === 0 || addMutation.isPending}
        >
          {addMutation.isPending ? 'Validating...' : 'Add'}
        </Button>
      </DialogActions>
    </Dialog>
  );
}

// ── Delete Confirmation Dialog ────────────────────────────────────────────────

function DeleteDialog({
  symbol,
  onClose,
}: {
  symbol: string | null;
  onClose: () => void;
}) {
  const deleteMutation = useDeleteSymbol();

  const errorDetail = useMemo(() => {
    if (!deleteMutation.error) return null;
    const err = deleteMutation.error as { body?: { detail?: string } };
    return err.body?.detail ?? deleteMutation.error.message;
  }, [deleteMutation.error]);

  const handleConfirm = () => {
    if (!symbol) return;
    deleteMutation.mutate(symbol, {
      onSuccess: () => onClose(),
    });
  };

  return (
    <Dialog open={!!symbol} onClose={onClose} maxWidth="xs" fullWidth>
      <DialogTitle>Remove {symbol}?</DialogTitle>
      <DialogContent>
        <DialogContentText>
          This will remove {symbol} from the active trading pairs.
          If it's defined in YAML, it will be disabled instead.
        </DialogContentText>
        {errorDetail && (
          <Alert severity="error" sx={{ mt: 2 }}>
            {errorDetail}
          </Alert>
        )}
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button
          onClick={handleConfirm}
          color="error"
          variant="contained"
          disabled={deleteMutation.isPending}
        >
          {deleteMutation.isPending ? 'Removing...' : 'Remove'}
        </Button>
      </DialogActions>
    </Dialog>
  );
}

// ── Main Page ─────────────────────────────────────────────────────────────────

export default function SymbolsPage() {
  const { data, isLoading, error } = useSymbols();
  const patchMutation = usePatchSymbol();
  const [addOpen, setAddOpen] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<string | null>(null);

  const handleToggleEnabled = useCallback(
    (item: SymbolItem) => {
      patchMutation.mutate({ symbol: item.symbol, enabled: !item.enabled });
    },
    [patchMutation],
  );

  if (isLoading) {
    return (
      <Box>
        <Skeleton variant="rectangular" height={300} />
      </Box>
    );
  }

  if (error) {
    return <Alert severity="error">Failed to load symbols: {error.message}</Alert>;
  }

  const symbols = data?.symbols ?? [];

  return (
    <Box>
      <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', mb: 2 }}>
        <Typography variant="h5">Trading Pairs</Typography>
        <Button
          variant="contained"
          startIcon={<AddIcon />}
          onClick={() => setAddOpen(true)}
        >
          Add Symbol
        </Button>
      </Box>

      <TableContainer component={Paper}>
        <Table size="small">
          <TableHead>
            <TableRow>
              <TableCell>Symbol</TableCell>
              <TableCell>Exchange</TableCell>
              <TableCell>Timeframes</TableCell>
              <TableCell>Enabled</TableCell>
              <TableCell>Description</TableCell>
              <TableCell align="right">Actions</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {symbols.map((s) => (
              <TableRow key={s.symbol} sx={{ opacity: s.enabled ? 1 : 0.5 }}>
                <TableCell>
                  <Typography variant="body2" fontWeight={600}>
                    {s.symbol}
                  </Typography>
                </TableCell>
                <TableCell>{s.exchange}</TableCell>
                <TableCell>
                  <Box sx={{ display: 'flex', gap: 0.5, flexWrap: 'wrap' }}>
                    {s.timeframes.map((tf) => (
                      <Chip key={tf} label={tf} size="small" variant="outlined" />
                    ))}
                  </Box>
                </TableCell>
                <TableCell>
                  <Switch
                    checked={s.enabled}
                    onChange={() => handleToggleEnabled(s)}
                    size="small"
                  />
                </TableCell>
                <TableCell>
                  <Typography variant="body2" color="text.secondary">
                    {s.description ?? '—'}
                  </Typography>
                </TableCell>
                <TableCell align="right">
                  <IconButton
                    size="small"
                    color="error"
                    onClick={() => setDeleteTarget(s.symbol)}
                  >
                    <DeleteIcon fontSize="small" />
                  </IconButton>
                </TableCell>
              </TableRow>
            ))}
            {symbols.length === 0 && (
              <TableRow>
                <TableCell colSpan={7} align="center">
                  <Typography variant="body2" color="text.secondary" sx={{ py: 4 }}>
                    No trading pairs configured. Click "Add Symbol" to get started.
                  </Typography>
                </TableCell>
              </TableRow>
            )}
          </TableBody>
        </Table>
      </TableContainer>

      <AddSymbolDialog open={addOpen} onClose={() => setAddOpen(false)} />
      <DeleteDialog symbol={deleteTarget} onClose={() => setDeleteTarget(null)} />
    </Box>
  );
}
