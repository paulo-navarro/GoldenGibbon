// ── Route Error Boundary (task 9.12) ────────────────────────────────────────
// Rendered by React Router when a route element throws (render error,
// loader failure, React #185 loops…). Placed per-route so a crash in one
// page keeps the AppLayout chrome and the rest of the app alive.

import { isRouteErrorResponse, Link, useRouteError } from 'react-router-dom';
import Alert from '@mui/material/Alert';
import AlertTitle from '@mui/material/AlertTitle';
import Box from '@mui/material/Box';
import Button from '@mui/material/Button';
import Typography from '@mui/material/Typography';

export default function RouteError() {
  const error = useRouteError();

  let title = 'Something went wrong';
  let detail: string;

  if (isRouteErrorResponse(error)) {
    title = `${error.status} ${error.statusText}`;
    detail = typeof error.data === 'string' ? error.data : '';
  } else if (error instanceof Error) {
    detail = error.message;
  } else {
    detail = String(error);
  }

  return (
    <Box sx={{ maxWidth: 720, mx: 'auto', mt: 4 }}>
      <Alert severity="error" variant="outlined">
        <AlertTitle>{title}</AlertTitle>
        <Typography variant="body2" sx={{ fontFamily: 'monospace', wordBreak: 'break-word', mb: 2 }}>
          {detail}
        </Typography>
        <Box sx={{ display: 'flex', gap: 1 }}>
          <Button size="small" variant="contained" onClick={() => window.location.reload()}>
            Reload page
          </Button>
          <Button size="small" component={Link} to="/">
            Back to Dashboard
          </Button>
        </Box>
      </Alert>
    </Box>
  );
}
