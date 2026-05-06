import { useCallback, useEffect, useState } from 'react';
import { NavLink, Outlet, useLocation } from 'react-router-dom';
import ConnectionStatus from '../components/ConnectionStatus';
import { useWebSocket } from '../hooks';
import type { Event } from '../types/events';
import { useMarketStore } from '../stores/marketStore';
import { useStrategyStore } from '../stores/strategyStore';
import { usePortfolioStore } from '../stores/portfolioStore';
import { useTradesStore } from '../stores/tradesStore';
import { useOrdersStore } from '../stores/ordersStore';
import { useSystemStore } from '../stores/systemStore';
import AppBar from '@mui/material/AppBar';
import Box from '@mui/material/Box';
import Divider from '@mui/material/Divider';
import Drawer from '@mui/material/Drawer';
import List from '@mui/material/List';
import ListItemButton from '@mui/material/ListItemButton';
import ListItemIcon from '@mui/material/ListItemIcon';
import ListItemText from '@mui/material/ListItemText';
import Toolbar from '@mui/material/Toolbar';
import Typography from '@mui/material/Typography';
import AccountBalanceWalletIcon from '@mui/icons-material/AccountBalanceWallet';
import BarChartIcon from '@mui/icons-material/BarChart';
import DashboardIcon from '@mui/icons-material/Dashboard';
import ShowChartIcon from '@mui/icons-material/ShowChart';
import SwapHorizIcon from '@mui/icons-material/SwapHoriz';
import CurrencyExchangeIcon from '@mui/icons-material/CurrencyExchange';
import SettingsIcon from '@mui/icons-material/Settings';
import PaidIcon from '@mui/icons-material/Paid';
import TerminalIcon from '@mui/icons-material/Terminal';

// ── Constants ───────────────────────────────────────────────────────────────

/** Width of the permanent sidebar drawer (px). */
export const DRAWER_WIDTH = 240;

const NAV_ITEMS = [
  { label: 'Dashboard', path: '/', icon: <DashboardIcon /> },
  { label: 'Prices', path: '/prices', icon: <PaidIcon /> },
  { label: 'Smart Hodler', path: '/strategy/smart_hodler', icon: <ShowChartIcon /> },
  { label: 'Mean Reversion', path: '/strategy/mean_reversion', icon: <ShowChartIcon /> },
  { label: 'Bear Guard', path: '/strategy/bear_guard', icon: <ShowChartIcon /> },
  { label: 'Portfolio', path: '/portfolio', icon: <AccountBalanceWalletIcon /> },
  { label: 'Activity', path: '/activity', icon: <SwapHorizIcon /> },
  { label: 'Metrics', path: '/metrics', icon: <BarChartIcon /> },
  { label: 'Symbols', path: '/symbols', icon: <CurrencyExchangeIcon /> },
  { label: 'Settings', path: '/settings', icon: <SettingsIcon /> },
  { label: 'Logs', path: '/logs', icon: <TerminalIcon /> },
];

// ── Helpers ─────────────────────────────────────────────────────────────────

function localClock(): string {
  return new Date().toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

// ── Layout ──────────────────────────────────────────────────────────────────

export default function AppLayout() {
  const { pathname } = useLocation();
  const [clock, setClock] = useState(localClock);

  const marketHandleEvent = useMarketStore((s) => s.handleEvent);
  const strategyHandleEvent = useStrategyStore((s) => s.handleEvent);
  const portfolioHandleEvent = usePortfolioStore((s) => s.handleEvent);
  const tradesHandleEvent = useTradesStore((s) => s.handleEvent);
  const ordersHandleEvent = useOrdersStore((s) => s.handleEvent);
  const systemHandleEvent = useSystemStore((s) => s.handleEvent);

  const onEvent = useCallback(
    (event: Event) => {
      marketHandleEvent(event);
      strategyHandleEvent(event);
      portfolioHandleEvent(event);
      tradesHandleEvent(event);
      ordersHandleEvent(event);
      systemHandleEvent(event);
    },
    [marketHandleEvent, strategyHandleEvent, portfolioHandleEvent, tradesHandleEvent, ordersHandleEvent, systemHandleEvent],
  );

  const { status: wsStatus, isHealthy: wsHealthy } = useWebSocket({ onEvent });

  useEffect(() => {
    const id = setInterval(() => setClock(localClock()), 1_000);
    return () => clearInterval(id);
  }, []);

  return (
    <Box sx={{ display: 'flex' }}>
      {/* ── AppBar ──────────────────────────────────────────────────── */}
      <AppBar
        position="fixed"
        sx={{
          width: `calc(100% - ${DRAWER_WIDTH}px)`,
          ml: `${DRAWER_WIDTH}px`,
        }}
      >
        <Toolbar variant="dense">
          {/* Left – page context (placeholder) */}
          <Typography variant="body2" color="text.secondary" noWrap>
            Dashboard
          </Typography>

          <Box sx={{ flexGrow: 1 }} />

          {/* Right – connection status + price tickers + clock */}
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 2 }}>
            <ConnectionStatus wsStatus={wsStatus} wsHealthy={wsHealthy} />
            <Typography
              variant="body2"
              color="text.secondary"
              sx={{ fontFamily: 'monospace', letterSpacing: 1 }}
            >
              {clock}
            </Typography>
          </Box>
        </Toolbar>
      </AppBar>

      {/* ── Sidebar Drawer ──────────────────────────────────────────── */}
      <Drawer
        variant="permanent"
        anchor="left"
        sx={{
          width: DRAWER_WIDTH,
          flexShrink: 0,
          '& .MuiDrawer-paper': {
            width: DRAWER_WIDTH,
            boxSizing: 'border-box',
          },
        }}
      >
        {/* Logo / app title */}
        <Toolbar variant="dense" sx={{ px: 2 }}>
          <Typography variant="h6" noWrap sx={{ color: 'primary.main' }}>
            GoldenGibbon
          </Typography>
        </Toolbar>
        <Divider />

        <List sx={{ pt: 1 }}>
          {NAV_ITEMS.map(({ label, path, icon }) => {
            const active =
              path === '/' ? pathname === '/' : pathname.startsWith(path);
            return (
              <ListItemButton
                key={path}
                component={NavLink}
                to={path}
                selected={active}
              >
                <ListItemIcon
                  sx={{
                    minWidth: 36,
                    color: active ? 'primary.main' : 'text.secondary',
                  }}
                >
                  {icon}
                </ListItemIcon>
                <ListItemText
                  primary={label}
                  primaryTypographyProps={{
                    variant: 'body2',
                    fontWeight: active ? 600 : 400,
                  }}
                />
              </ListItemButton>
            );
          })}
        </List>
      </Drawer>

      {/* ── Main Content ────────────────────────────────────────────── */}
      <Box
        component="main"
        sx={{
          flexGrow: 1,
          p: 3,
          width: `calc(100% - ${DRAWER_WIDTH}px)`,
        }}
      >
        {/* Spacer to push content below the AppBar */}
        <Toolbar variant="dense" />

        <Outlet />
      </Box>
    </Box>
  );
}
