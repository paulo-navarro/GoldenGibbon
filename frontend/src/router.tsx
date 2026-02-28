import { createBrowserRouter } from 'react-router-dom';
import AppLayout from './layouts/AppLayout';
import DashboardPage from './pages/DashboardPage';
import StrategyPage from './pages/StrategyPage';
import PortfolioPage from './pages/PortfolioPage';
import TradesPage from './pages/TradesPage';
import OrdersPage from './pages/OrdersPage';
import MetricsPage from './pages/MetricsPage';
import LogsPage from './pages/LogsPage';

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
      { path: 'logs', element: <LogsPage /> },
    ],
  },
]);

export default router;
