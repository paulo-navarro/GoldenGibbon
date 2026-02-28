import { createTheme } from '@mui/material/styles';

// ── Palette Tokens ──────────────────────────────────────────────────────────

const BACKGROUND_DEFAULT = '#0a0e14';
const BACKGROUND_PAPER = '#111720';
const PRIMARY = '#00bcd4';
const SECONDARY = '#546e7a';
const SUCCESS = '#4caf50';
const ERROR = '#f44336';
const WARNING = '#ff9800';
const TEXT_PRIMARY = '#e0e0e0';
const TEXT_SECONDARY = '#9e9e9e';
const DIVIDER = 'rgba(255, 255, 255, 0.08)';

// ── Theme ───────────────────────────────────────────────────────────────────

const theme = createTheme({
  palette: {
    mode: 'dark',
    primary: { main: PRIMARY },
    secondary: { main: SECONDARY },
    success: { main: SUCCESS },
    error: { main: ERROR },
    warning: { main: WARNING },
    background: {
      default: BACKGROUND_DEFAULT,
      paper: BACKGROUND_PAPER,
    },
    text: {
      primary: TEXT_PRIMARY,
      secondary: TEXT_SECONDARY,
    },
    divider: DIVIDER,
  },

  typography: {
    fontFamily: "'Inter', 'Roboto', 'Helvetica', 'Arial', sans-serif",
    fontSize: 13,
    h5: { fontWeight: 600 },
    h6: { fontWeight: 600 },
    body2: { fontSize: '0.8125rem' }, // 13px – data-dense default
  },

  shape: {
    borderRadius: 8,
  },

  components: {
    // ── Global baseline ───────────────────────────────────────────────
    MuiCssBaseline: {
      styleOverrides: {
        body: {
          backgroundColor: BACKGROUND_DEFAULT,
          margin: 0,
        },
      },
    },

    // ── AppBar ────────────────────────────────────────────────────────
    MuiAppBar: {
      defaultProps: {
        elevation: 0,
      },
      styleOverrides: {
        root: {
          backgroundColor: BACKGROUND_PAPER,
          backgroundImage: 'none',
          borderBottom: `1px solid ${DIVIDER}`,
        },
      },
    },

    // ── Drawer ────────────────────────────────────────────────────────
    MuiDrawer: {
      styleOverrides: {
        paper: {
          backgroundColor: BACKGROUND_PAPER,
          backgroundImage: 'none',
          borderRight: `1px solid ${DIVIDER}`,
        },
      },
    },

    // ── Cards & Paper ─────────────────────────────────────────────────
    MuiPaper: {
      defaultProps: {
        elevation: 0,
      },
      styleOverrides: {
        root: {
          backgroundImage: 'none',
          border: `1px solid ${DIVIDER}`,
        },
      },
    },
    MuiCard: {
      defaultProps: {
        elevation: 0,
      },
      styleOverrides: {
        root: {
          backgroundImage: 'none',
          border: `1px solid ${DIVIDER}`,
        },
      },
    },

    // ── Navigation ────────────────────────────────────────────────────
    MuiListItemButton: {
      styleOverrides: {
        root: {
          borderRadius: 6,
          margin: '2px 8px',
          '&.Mui-selected': {
            backgroundColor: `${PRIMARY}14`, // ~8% opacity
            '&:hover': {
              backgroundColor: `${PRIMARY}24`, // ~14% opacity
            },
          },
        },
      },
    },

    // ── Chips (log severity, status badges) ───────────────────────────
    MuiChip: {
      defaultProps: {
        size: 'small',
      },
    },

    // ── Tables ────────────────────────────────────────────────────────
    MuiTableCell: {
      styleOverrides: {
        root: {
          borderBottom: `1px solid ${DIVIDER}`,
        },
      },
    },
  },
});

export default theme;
