import { createBrowserRouter, Navigate } from 'react-router-dom';
import AppLayout from './layouts/AppLayout';
import DashboardPage from './pages/DashboardPage';
import StrategyPage from './pages/StrategyPage';
import PortfolioPage from './pages/PortfolioPage';
import ActivityPage from './pages/ActivityPage';
import MetricsPage from './pages/MetricsPage';
import LogsPage from './pages/LogsPage';
import SymbolsPage from './pages/SymbolsPage';
import PricesPage from './pages/PricesPage';
import SettingsPage from './pages/SettingsPage';

const router = createBrowserRouter([
  {
    path: '/',
    element: <AppLayout />,
    children: [
      { index: true, element: <DashboardPage /> },
      { path: 'prices', element: <PricesPage /> },
      { path: 'strategy', element: <Navigate to="/strategy/smart_hodler" replace /> },
      { path: 'strategy/:strategyName', element: <StrategyPage /> },
      { path: 'portfolio', element: <PortfolioPage /> },
      { path: 'activity', element: <ActivityPage /> },
      { path: 'metrics', element: <MetricsPage /> },
      { path: 'symbols', element: <SymbolsPage /> },
      { path: 'settings', element: <SettingsPage /> },
      { path: 'logs', element: <LogsPage /> },
    ],
  },
]);

export default router;
