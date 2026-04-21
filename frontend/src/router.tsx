import { createBrowserRouter } from 'react-router-dom';
import AppLayout from './layouts/AppLayout';
import DashboardPage from './pages/DashboardPage';
import StrategyPage from './pages/StrategyPage';
import PortfolioPage from './pages/PortfolioPage';
import TradesPage from './pages/TradesPage';
import OrdersPage from './pages/OrdersPage';
import MetricsPage from './pages/MetricsPage';
import LogsPage from './pages/LogsPage';
import SymbolsPage from './pages/SymbolsPage';
import SettingsPage from './pages/SettingsPage';

const router = createBrowserRouter([
  {
    path: '/',
    element: <AppLayout />,
    children: [
      { index: true, element: <DashboardPage /> },
      { path: 'strategy', element: <StrategyPage /> },
      { path: 'portfolio', element: <PortfolioPage /> },
      { path: 'trades', element: <TradesPage /> },
      { path: 'orders', element: <OrdersPage /> },
      { path: 'metrics', element: <MetricsPage /> },
      { path: 'symbols', element: <SymbolsPage /> },
      { path: 'settings', element: <SettingsPage /> },
      { path: 'logs', element: <LogsPage /> },
    ],
  },
]);

export default router;
