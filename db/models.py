"""
SQLAlchemy ORM models for database persistence.

These models define the database schema and provide the ORM interface
for querying and persisting data.
"""

from datetime import datetime
from decimal import Decimal
from typing import Dict, Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from db import Base


# ── Candle Records ───────────────────────────────────────────────────────────

class CandleRecord(Base):
    """
    OHLCV candle data with indicators.
    
    Stores historical market data for backtesting and real-time trading.
    """
    __tablename__ = "candles"
    
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    timeframe: Mapped[str] = mapped_column(String(10), nullable=False, index=True)
    open_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    open: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    high: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    low: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    close: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    volume: Mapped[Decimal] = mapped_column(Numeric(30, 8), nullable=False)
    quote_volume: Mapped[Optional[Decimal]] = mapped_column(Numeric(30, 8), nullable=True)
    trades_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    
    # Store indicators as JSONB for flexibility
    # Example: {"ema_50": 50000.12, "ema_200": 49000.00, "adx": 28.5}
    indicators: Mapped[Optional[Dict]] = mapped_column(JSONB, nullable=True)
    
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now()
    )
    
    __table_args__ = (
        # Unique constraint: one candle per symbol/timeframe/open_time
        UniqueConstraint("symbol", "timeframe", "open_time", name="uq_candle"),
        # Composite index for fast queries
        Index("ix_candles_symbol_timeframe_time", "symbol", "timeframe", "open_time"),
    )
    
    def __repr__(self) -> str:
        return f"<CandleRecord(symbol={self.symbol}, timeframe={self.timeframe}, open_time={self.open_time}, close={self.close})>"


# ── Position Records ─────────────────────────────────────────────────────────

class PositionRecord(Base):
    """
    Open position tracking.
    
    Stores active long positions with entry details and stop prices.
    """
    __tablename__ = "positions"
    
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    side: Mapped[str] = mapped_column(String(10), nullable=False, default="long")
    size: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    entry_price: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    entry_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    highest_close: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    lowest_close: Mapped[Optional[Decimal]] = mapped_column(Numeric(20, 8), nullable=True)
    trailing_stop_price: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    hard_stop_price: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    exchange_stop_order_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    strategy: Mapped[str] = mapped_column(String(50), nullable=False, default="smart_hodler")
    scale_in_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    buy_signal_candles: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now()
    )
    
    __table_args__ = (
        Index("ix_positions_symbol_strategy", "symbol", "strategy"),
    )
    
    def __repr__(self) -> str:
        return f"<PositionRecord(symbol={self.symbol}, size={self.size}, entry_price={self.entry_price})>"


# ── Trade Records ────────────────────────────────────────────────────────────

class TradeRecord(Base):
    """
    Historical trade records.
    
    Stores completed trades with entry/exit details and PnL.
    """
    __tablename__ = "trade_records"
    
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    run_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, index=True)
    symbol: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    strategy: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    side: Mapped[str] = mapped_column(String(10), nullable=False, default="long")
    entry_price: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    exit_price: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    size: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    entry_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    exit_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    pnl_usdt: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    pnl_percent: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    duration_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    exit_reason: Mapped[str] = mapped_column(String(50), nullable=False)
    max_favorable_excursion: Mapped[Optional[Decimal]] = mapped_column(Numeric(20, 8), nullable=True)
    max_adverse_excursion: Mapped[Optional[Decimal]] = mapped_column(Numeric(20, 8), nullable=True)
    
    trading_mode: Mapped[str] = mapped_column(String(10), nullable=False, default="paper", server_default="paper")

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now()
    )

    __table_args__ = (
        Index("ix_trade_records_symbol_strategy_entry", "symbol", "strategy", "entry_time"),
        Index("ix_trade_records_strategy_exit", "strategy", "exit_time"),
        Index("ix_trade_records_trading_mode", "trading_mode"),
    )
    
    def __repr__(self) -> str:
        return f"<TradeRecord(symbol={self.symbol}, entry={self.entry_price}, exit={self.exit_price}, pnl={self.pnl_usdt})>"


# ── Order Records ────────────────────────────────────────────────────────────

class OrderRecord(Base):
    """
    Order execution history.
    
    Tracks buy and sell orders with fill details.
    """
    __tablename__ = "order_records"
    
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    run_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, index=True)
    symbol: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    side: Mapped[str] = mapped_column(String(10), nullable=False)  # buy, sell
    order_type: Mapped[str] = mapped_column(String(10), nullable=False)  # market, limit
    amount: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    price: Mapped[Optional[Decimal]] = mapped_column(Numeric(20, 8), nullable=True)  # null for market
    status: Mapped[str] = mapped_column(String(20), nullable=False, index=True)  # pending, filled, etc.
    filled_amount: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False, default=0)
    avg_fill_price: Mapped[Optional[Decimal]] = mapped_column(Numeric(20, 8), nullable=True)
    slippage_percent: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 4), nullable=True)
    fee_usdt: Mapped[Optional[Decimal]] = mapped_column(Numeric(20, 8), nullable=True)
    exchange_order_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    time_in_force: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    limit_price: Mapped[Optional[Decimal]] = mapped_column(Numeric(20, 8), nullable=True)
    reject_reason: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    exchange_status: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)

    trading_mode: Mapped[str] = mapped_column(String(10), nullable=False, default="paper", server_default="paper")

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        index=True
    )
    updated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        onupdate=func.now()
    )
    filled_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True
    )

    __table_args__ = (
        Index("ix_order_records_symbol_created", "symbol", "created_at"),
        Index("ix_order_records_status_created", "status", "created_at"),
        Index("ix_order_records_trading_mode", "trading_mode"),
    )
    
    def __repr__(self) -> str:
        return f"<OrderRecord(symbol={self.symbol}, side={self.side}, amount={self.amount}, status={self.status})>"


# ── Portfolio Snapshots ──────────────────────────────────────────────────────

class PortfolioSnapshot(Base):
    """
    Portfolio equity curve snapshots.
    
    Stores point-in-time portfolio values for performance tracking.
    """
    __tablename__ = "portfolio_snapshots"
    
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    run_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, index=True)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        index=True
    )
    usdt_balance: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    positions_value: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False, default=0)
    total_equity: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    daily_pnl: Mapped[Optional[Decimal]] = mapped_column(Numeric(20, 8), nullable=True)
    total_pnl: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False, default=0)
    open_positions_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    trading_mode: Mapped[str] = mapped_column(String(10), nullable=False, default="paper", server_default="paper")

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now()
    )

    __table_args__ = (
        Index("ix_portfolio_snapshots_trading_mode", "trading_mode"),
    )

    def __repr__(self) -> str:
        return f"<PortfolioSnapshot(timestamp={self.timestamp}, equity={self.total_equity})>"


# ── Backtest Results ─────────────────────────────────────────────────────────

class BacktestResult(Base):
    """
    Backtest run results and metrics.
    
    Stores comprehensive performance statistics for each backtest run.
    """
    __tablename__ = "backtest_results"
    
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    run_id: Mapped[str] = mapped_column(String(100), nullable=False, unique=True, index=True)
    strategy: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    symbol: Mapped[str] = mapped_column(String(20), nullable=False)
    start_date: Mapped[datetime] = mapped_column(DateTime(timezone=False), nullable=False)
    end_date: Mapped[datetime] = mapped_column(DateTime(timezone=False), nullable=False)
    initial_capital: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    final_capital: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    total_return: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)  # Percentage
    total_trades: Mapped[int] = mapped_column(Integer, nullable=False)
    winning_trades: Mapped[int] = mapped_column(Integer, nullable=False)
    losing_trades: Mapped[int] = mapped_column(Integer, nullable=False)
    win_rate: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)  # Percentage
    avg_win: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    avg_loss: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    max_drawdown: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)  # Percentage
    sharpe_ratio: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 4), nullable=True)
    profit_factor: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 4), nullable=True)
    avg_trade_duration_minutes: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    max_consecutive_wins: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    max_consecutive_losses: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    buy_hold_return: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 4), nullable=True)
    buy_hold_vs_strategy: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 4), nullable=True)
    
    # Store full config as JSONB for reproducibility
    config_snapshot: Mapped[Optional[Dict]] = mapped_column(JSONB, nullable=True)
    
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        index=True
    )
    
    __table_args__ = (
        Index("ix_backtest_results_strategy_created", "strategy", "created_at"),
    )
    
    def __repr__(self) -> str:
        return f"<BacktestResult(run_id={self.run_id}, strategy={self.strategy}, return={self.total_return}%)>"


# ── Strategy State ───────────────────────────────────────────────────────────

class StrategyStateRecord(Base):
    """
    Strategy state for crash recovery.
    
    Stores the current state machine status and metadata for each symbol/strategy pair.
    """
    __tablename__ = "strategy_state"
    
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    strategy: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    state: Mapped[str] = mapped_column(String(20), nullable=False)  # FLAT, POSITION, REDUCED, COOLDOWN
    cooldown_until: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    consecutive_buy_candles: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_exit_time: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    
    # Flexible storage for strategy-specific state
    state_data: Mapped[Optional[Dict]] = mapped_column(JSONB, nullable=True)
    
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now()
    )
    
    __table_args__ = (
        UniqueConstraint("symbol", "strategy", name="uq_strategy_state"),
    )
    
    def __repr__(self) -> str:
        return f"<StrategyStateRecord(symbol={self.symbol}, strategy={self.strategy}, state={self.state})>"


# ── Strategy Config Persistence (task 5.7a) ───────────────────────────────────

class StrategyConfigRecord(Base):
    """
    Persisted strategy configuration overrides.

    Stores per-strategy config as JSONB. These override YAML/ENV defaults.
    """
    __tablename__ = "strategy_configs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    strategy_name: Mapped[str] = mapped_column(String(50), nullable=False, unique=True, index=True)
    config_json: Mapped[Dict] = mapped_column(JSONB, nullable=False, default=dict)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    def __repr__(self) -> str:
        return f"<StrategyConfigRecord(strategy_name={self.strategy_name})>"


# ── App Config Persistence (task 8.1) ────────────────────────────────────────

class AppConfigRecord(Base):
    """Persisted application configuration by namespace (risk, execution, etc.)."""
    __tablename__ = "app_configs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    namespace: Mapped[str] = mapped_column(String(50), nullable=False, unique=True, index=True)
    config_json: Mapped[Dict] = mapped_column(JSONB, nullable=False, default=dict)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    def __repr__(self) -> str:
        return f"<AppConfigRecord(namespace={self.namespace})>"


# ── Symbol Config Persistence (task 7.1) ─────────────────────────────────────

class SymbolConfigRecord(Base):
    """Persisted symbol (trading pair) configuration."""
    __tablename__ = "symbol_configs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(20), nullable=False, unique=True, index=True)
    exchange: Mapped[str] = mapped_column(String(20), nullable=False, default="binance")
    timeframes: Mapped[Dict] = mapped_column(JSONB, nullable=False, default=lambda: ["15m", "1h"])
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    description: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    def __repr__(self) -> str:
        return f"<SymbolConfigRecord(symbol={self.symbol}, enabled={self.enabled})>"


# ── Exports ──────────────────────────────────────────────────────────────────

__all__ = [
    "CandleRecord",
    "PositionRecord",
    "TradeRecord",
    "OrderRecord",
    "PortfolioSnapshot",
    "BacktestResult",
    "StrategyStateRecord",
    "StrategyConfigRecord",
    "SymbolConfigRecord",
    "AppConfigRecord",
]
