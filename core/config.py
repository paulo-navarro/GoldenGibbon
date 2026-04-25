"""
Configuration loading and validation using Pydantic.

Loads configuration with priority: DB > Pydantic defaults.
ENV variables are used only for infrastructure (DATABASE_URL, REDIS_URL, etc.).
YAML files are no longer used.
"""

import os
from typing import Any, Dict, List, Optional

import structlog
from pydantic import BaseModel, Field, field_validator

logger = structlog.get_logger(__name__)


# ── Symbol Configuration ──────────────────────────────────────────────────────

class SymbolConfig(BaseModel):
    """Configuration for a single trading symbol."""

    symbol: str = Field(..., description="Trading pair (e.g., BTCUSDT)")
    exchange: str = Field(default="binance", description="Exchange name")
    timeframes: List[str] = Field(..., description="List of timeframes to monitor")
    enabled: bool = Field(default=True, description="Whether trading is enabled for this symbol")
    description: Optional[str] = Field(None, description="Human-readable description")

    @field_validator("symbol")
    @classmethod
    def validate_symbol_format(cls, v: str) -> str:
        """Validate symbol format (must end with USDT for now)."""
        if not v:
            raise ValueError("Symbol cannot be empty")
        if not v.isupper():
            raise ValueError(f"Symbol must be uppercase: {v}")
        if not v.endswith("USDT"):
            raise ValueError(f"Symbol must end with USDT: {v}")
        return v

    @field_validator("timeframes")
    @classmethod
    def validate_timeframes(cls, v: List[str]) -> List[str]:
        """Validate timeframe formats."""
        valid_timeframes = {"1m", "5m", "15m", "30m", "1h", "4h", "1d", "1w"}
        for tf in v:
            if tf not in valid_timeframes:
                raise ValueError(f"Invalid timeframe: {tf}. Must be one of {valid_timeframes}")
        return v


class SymbolsConfig(BaseModel):
    """Root configuration for all symbols."""

    symbols: List[SymbolConfig]

    @property
    def enabled_symbols(self) -> List[SymbolConfig]:
        """Get only enabled symbols."""
        return [s for s in self.symbols if s.enabled]


# ── Strategy Configuration ────────────────────────────────────────────────────

class SessionDeadZone(BaseModel):
    """Configuration for a session dead zone (no new entries)."""

    name: str
    start_utc: str = Field(..., description="Start time in UTC (e.g., 'Saturday 21:00' or '21:00')")
    end_utc: str = Field(..., description="End time in UTC")


class SmartHodlerConfig(BaseModel):
    """Configuration for the Smart Hodler strategy."""

    enabled: bool = Field(default=True)
    description: Optional[str] = None
    allocation_pct: Optional[float] = Field(default=None, ge=0, le=1, description="Capital allocation weight (0–1). None = equal split.")

    # Timeframes
    timeframe_primary: str = Field(default="15m")
    timeframe_confirmation: str = Field(default="1h")

    # Trend Detection (15m)
    ema_fast: int = Field(default=50, ge=1)
    ema_slow: int = Field(default=200, ge=1)
    adx_period: int = Field(default=14, ge=1)
    adx_threshold: float = Field(default=25, ge=0, le=100)
    volume_sma_period: int = Field(default=20, ge=1)

    # Hourly Confirmation (1H)
    ema_hourly: int = Field(default=21, ge=1)
    rsi_period: int = Field(default=14, ge=1)
    rsi_threshold: float = Field(default=45, ge=0, le=100)

    # Position Sizing (scaled entries)
    entry_initial_pct: float = Field(default=0.50, gt=0, le=1)
    entry_scale_1_pct: float = Field(default=0.25, gt=0, le=1)
    entry_scale_2_pct: float = Field(default=0.25, gt=0, le=1)
    entry_scale_1_candles: int = Field(default=8, ge=1)
    entry_scale_2_candles: int = Field(default=16, ge=1)

    # Exit Strategy
    exit_momentum_fade_pct: float = Field(default=0.50, gt=0, le=1)
    exit_confirmation_candles: int = Field(default=2, ge=1)

    # Stop-Loss
    atr_period: int = Field(default=14, ge=1)
    trailing_stop_atr_multiplier: float = Field(default=2.0, gt=0)
    hard_stop_pct: float = Field(default=0.03, gt=0, le=1)

    # Re-Entry / Cooldown
    cooldown_candles: int = Field(default=16, ge=0)

    # Session Filter
    session_filter_enabled: bool = Field(default=True)
    session_dead_zones: List[SessionDeadZone] = Field(default_factory=list)

    # ADX Momentum Detection
    adx_falling_lookback: int = Field(default=3, ge=1)

    @field_validator("ema_fast", "ema_slow")
    @classmethod
    def validate_ema_periods(cls, v: int) -> int:
        """Ensure EMA periods are reasonable."""
        if v > 500:
            raise ValueError(f"EMA period too large: {v}")
        return v


class MeanReversionStrategyConfig(BaseModel):
    """Configuration for the Mean Reversion strategy."""

    enabled: bool = Field(default=True)
    description: Optional[str] = None
    allocation_pct: Optional[float] = Field(default=None, ge=0, le=1, description="Capital allocation weight (0–1). None = equal split.")

    # Timeframes
    timeframe_primary: str = Field(default="15m")
    timeframe_confirmation: str = Field(default="1h")

    # Bollinger Bands (15m)
    bb_period: int = Field(default=20, ge=1)
    bb_std_dev: float = Field(default=2.0, gt=0)

    # RSI (15m)
    rsi_period: int = Field(default=14, ge=1)
    rsi_oversold: float = Field(default=30, ge=0, le=100)
    rsi_overbought: float = Field(default=70, ge=0, le=100)

    # ADX regime filter (15m)
    adx_period: int = Field(default=14, ge=1)
    adx_threshold: float = Field(default=25, ge=0, le=100)

    # Volume confirmation
    volume_sma_period: int = Field(default=20, ge=1)
    volume_spike_multiplier: float = Field(default=1.5, gt=0)

    # Hourly confirmation (1H)
    ema_hourly: int = Field(default=21, ge=1)
    rsi_hourly_floor: float = Field(default=35, ge=0, le=100)

    # Position Sizing (single entry, no scale-in)
    entry_pct: float = Field(default=0.75, gt=0, le=1)

    # Exit Strategy
    exit_partial_pct: float = Field(default=0.50, gt=0, le=1)

    # Stop-Loss
    hard_stop_pct: float = Field(default=0.02, gt=0, le=1)
    atr_period: int = Field(default=14, ge=1)

    # Time Stop
    time_stop_candles: int = Field(default=16, ge=1)
    time_stop_cooldown_candles: int = Field(default=4, ge=0)

    # Cooldown
    cooldown_candles: int = Field(default=8, ge=0)

    # Session Filter
    session_filter_enabled: bool = Field(default=True)
    session_dead_zones: List[SessionDeadZone] = Field(default_factory=list)


class StrategiesConfig(BaseModel):
    """Root configuration for all strategies."""

    model_config = {"extra": "allow"}

    smart_hodler: SmartHodlerConfig = Field(default_factory=SmartHodlerConfig)
    mean_reversion: MeanReversionStrategyConfig = Field(
        default_factory=MeanReversionStrategyConfig
    )

    def get_strategy_config(self, name: str) -> BaseModel | Dict | None:
        """Return the config for a strategy by name, or None."""
        val = getattr(self, name, None)
        if val is not None:
            return val
        extra = self.model_extra or {}
        return extra.get(name)


# ── Risk Configuration ────────────────────────────────────────────────────────

class RiskConfig(BaseModel):
    """Risk management settings."""

    max_drawdown_per_trade: float = Field(default=0.03, gt=0, le=1)
    trailing_stop_atr_multiplier: float = Field(default=2.0, gt=0)
    cooldown_candles: int = Field(default=16, ge=0)
    max_position_size_pct: float = Field(default=1.0, gt=0, le=1)
    max_total_exposure_pct: float = Field(default=1.0, gt=0, le=1)
    max_trades_per_day: int = Field(default=20, ge=1)
    max_daily_loss_pct: float = Field(default=0.10, gt=0, le=1)
    max_trade_size_usdt: Optional[float] = Field(default=None, gt=0)
    max_symbol_exposure_usdt: Optional[float] = Field(default=None, gt=0)


# ── Execution Configuration ───────────────────────────────────────────────────

class ExecutionConfig(BaseModel):
    """Execution settings (slippage, fees, order types)."""

    slippage: float = Field(default=0.001, ge=0, le=0.1)
    taker_fee: float = Field(default=0.001, ge=0, le=0.1)
    maker_fee: float = Field(default=0.001, ge=0, le=0.1)
    default_order_type: str = Field(default="market")
    order_timeout: int = Field(default=30, ge=1)
    max_retries: int = Field(default=3, ge=0)
    retry_delay: int = Field(default=1, ge=0)

    @field_validator("default_order_type")
    @classmethod
    def validate_order_type(cls, v: str) -> str:
        """Validate order type."""
        if v not in {"market", "limit"}:
            raise ValueError(f"Invalid order type: {v}. Must be 'market' or 'limit'")
        return v


# ── Data Configuration ────────────────────────────────────────────────────────

class DataConfig(BaseModel):
    """Data fetching and caching settings."""

    binance_api_base: str = Field(default="https://api.binance.com")
    max_candles_per_request: int = Field(default=1000, ge=1)
    historical_lookback_days: int = Field(default=730, ge=1)
    cache_ttl: int = Field(default=300, ge=0)


# ── Logging Configuration ─────────────────────────────────────────────────────

class LoggingConfig(BaseModel):
    """Logging settings."""

    level: str = Field(default="INFO")
    format: str = Field(default="json")
    include_timestamp: bool = Field(default=True)
    include_context: bool = Field(default=True)
    console: bool = Field(default=True)
    file: bool = Field(default=True)
    file_path: str = Field(default="logs/trading.log")
    max_file_size_mb: int = Field(default=100, ge=1)
    backup_count: int = Field(default=10, ge=0)

    @field_validator("level")
    @classmethod
    def validate_log_level(cls, v: str) -> str:
        """Validate log level."""
        valid_levels = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        v_upper = v.upper()
        if v_upper not in valid_levels:
            raise ValueError(f"Invalid log level: {v}. Must be one of {valid_levels}")
        return v_upper

    @field_validator("format")
    @classmethod
    def validate_log_format(cls, v: str) -> str:
        """Validate log format."""
        if v not in {"json", "text"}:
            raise ValueError(f"Invalid log format: {v}. Must be 'json' or 'text'")
        return v


# ── Backtest Configuration ────────────────────────────────────────────────────

class BacktestConfig(BaseModel):
    """Backtest-specific settings."""

    initial_capital: float = Field(default=10000, gt=0)
    benchmark_symbol: str = Field(default="BTCUSDT")
    save_trades: bool = Field(default=True)
    save_equity_curve: bool = Field(default=True)
    save_metrics: bool = Field(default=True)
    output_format: str = Field(default="both")

    @field_validator("output_format")
    @classmethod
    def validate_output_format(cls, v: str) -> str:
        """Validate output format."""
        if v not in {"console", "database", "both"}:
            raise ValueError(f"Invalid output format: {v}")
        return v


# ── Paper Trading Configuration ───────────────────────────────────────────────

class PaperTradingConfig(BaseModel):
    """Paper trading settings."""

    enabled: bool = Field(default=False)
    initial_capital: float = Field(default=10000, gt=0, description="Starting USDT balance for paper trading")
    simulate_slippage: bool = Field(default=True)
    simulate_latency: bool = Field(default=True)
    latency_ms: int = Field(default=100, ge=0)


# ── Live Trading Configuration ────────────────────────────────────────────────

class LiveTradingConfig(BaseModel):
    """Live trading settings."""

    enabled: bool = Field(default=False)
    require_manual_approval: bool = Field(default=True)
    max_order_size_usdt: float = Field(default=1000, gt=0)
    kill_switch_max_drawdown: float = Field(default=0.15, gt=0, le=1)
    reconcile_on_startup: bool = Field(default=True)
    reconciliation_interval_hours: int = Field(default=4, ge=1)
    api_key: str = Field(default="")
    api_secret: str = Field(default="")
    use_testnet: bool = Field(default=True)


# ── WebSocket Feed Configuration ──────────────────────────────────────────────

class WebSocketFeedConfig(BaseModel):
    """Binance WebSocket live-feed settings (task 3.6)."""

    enabled: bool = Field(default=False)
    base_url: str = Field(default="wss://stream.binance.com:9443")
    reconnect_delay: float = Field(default=1.0, gt=0, description="Initial reconnect delay in seconds")
    max_reconnect_delay: float = Field(default=60.0, gt=0, description="Maximum reconnect delay in seconds")
    ping_interval: int = Field(default=20, ge=5, description="WebSocket ping interval in seconds")
    ping_timeout: int = Field(default=10, ge=1, description="WebSocket ping timeout in seconds")


# ── Alerting Configuration (task 4.9) ─────────────────────────────────────────

class AlertingConfig(BaseModel):
    """Telegram alerting settings."""

    enabled: bool = Field(default=False)
    alert_on_fill: bool = Field(default=True, description="Alert on order fills (trade open/close)")
    alert_on_stop: bool = Field(default=True, description="Alert on stop-loss triggers")
    alert_on_kill_switch: bool = Field(default=True, description="Alert on kill-switch activation")
    alert_on_reconciliation: bool = Field(default=True, description="Alert on reconciliation mismatches")
    alert_on_error: bool = Field(default=False, description="Alert on task errors")


# ── System Configuration ──────────────────────────────────────────────────────

class SystemConfig(BaseModel):
    """System-wide settings."""

    timezone: str = Field(default="UTC")
    environment: str = Field(default="development")

    @field_validator("environment")
    @classmethod
    def validate_environment(cls, v: str) -> str:
        """Validate environment."""
        if v not in {"development", "staging", "production"}:
            raise ValueError(f"Invalid environment: {v}")
        return v


# ── Regime Detection Configuration (task 5.4b) ──────────────────────────────

class RegimeConfig(BaseModel):
    """Market regime detection and rebalancing settings."""

    adx_trending_threshold: float = Field(default=25.0, gt=0, le=100)
    adx_ranging_threshold: float = Field(default=20.0, ge=0, le=100)
    smoothing_window: int = Field(default=3, ge=1, le=50)
    rebalance_enabled: bool = Field(default=False, description="Enable regime-based allocation rebalancing")
    regime_shift_pct: float = Field(default=0.2, ge=0, le=0.5, description="Max weight shift on regime change")
    strategy_regime_map: Dict[str, str] = Field(
        default_factory=lambda: {"smart_hodler": "trending", "mean_reversion": "ranging"},
        description="Which regime each strategy favors",
    )


# ── Root Settings ─────────────────────────────────────────────────────────────

# Maps namespace → Pydantic model class
NAMESPACE_MODELS: Dict[str, type] = {
    "risk": RiskConfig,
    "execution": ExecutionConfig,
    "data": DataConfig,
    "logging": LoggingConfig,
    "backtest": BacktestConfig,
    "paper_trading": PaperTradingConfig,
    "live_trading": LiveTradingConfig,
    "ws_feed": WebSocketFeedConfig,
    "alerting": AlertingConfig,
    "regime": RegimeConfig,
    "system": SystemConfig,
}


class Settings(BaseModel):
    """
    Root configuration object.

    Built from DB tables with Pydantic model defaults as fallback.
    """

    symbols: List[SymbolConfig]
    strategies: StrategiesConfig = Field(default_factory=StrategiesConfig)
    risk: RiskConfig = Field(default_factory=RiskConfig)
    execution: ExecutionConfig = Field(default_factory=ExecutionConfig)
    data: DataConfig = Field(default_factory=DataConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    backtest: BacktestConfig = Field(default_factory=BacktestConfig)
    paper_trading: PaperTradingConfig = Field(default_factory=PaperTradingConfig)
    live_trading: LiveTradingConfig = Field(default_factory=LiveTradingConfig)
    ws_feed: WebSocketFeedConfig = Field(default_factory=WebSocketFeedConfig)
    alerting: AlertingConfig = Field(default_factory=AlertingConfig)
    regime: RegimeConfig = Field(default_factory=RegimeConfig)
    system: SystemConfig = Field(default_factory=SystemConfig)


    @classmethod
    def from_db(cls) -> "Settings":
        """Load all configuration from DB tables. Pydantic defaults as fallback."""
        # Load symbols
        symbols = _load_db_symbols()
        if not symbols:
            symbols = [
                {"symbol": "BTCUSDT", "exchange": "binance", "timeframes": ["15m", "1h"], "enabled": True, "description": "Bitcoin / Tether"},
                {"symbol": "ETHUSDT", "exchange": "binance", "timeframes": ["15m", "1h"], "enabled": True, "description": "Ethereum / Tether"},
            ]

        # Load app config namespaces
        config_dict: Dict[str, Any] = {"symbols": symbols}

        for namespace, model_cls in NAMESPACE_MODELS.items():
            db_data = _load_app_config(namespace)
            if db_data:
                try:
                    config_dict[namespace] = model_cls(**db_data).model_dump()
                except Exception as exc:
                    logger.warning("config.namespace_validation_failed", namespace=namespace, error=str(exc))
                    config_dict[namespace] = model_cls().model_dump()
            else:
                config_dict[namespace] = model_cls().model_dump()

        # Load strategies
        strategies_dict: Dict[str, Any] = {}
        for strategy_name in ["smart_hodler", "mean_reversion"]:
            db_data = _load_db_config(strategy_name)
            if db_data:
                strategies_dict[strategy_name] = db_data
        config_dict["strategies"] = strategies_dict

        return cls(**config_dict)

    @property
    def enabled_symbols(self) -> List[SymbolConfig]:
        """Get only enabled symbols."""
        return [s for s in self.symbols if s.enabled]


# ── DB Loaders ───────────────────────────────────────────────────────────────


def _load_app_config(namespace: str) -> Optional[Dict[str, Any]]:
    """Load config from the app_configs table by namespace."""
    try:
        from db import get_session
        from db.models import AppConfigRecord
        from sqlalchemy import select

        with get_session() as session:
            stmt = select(AppConfigRecord).where(AppConfigRecord.namespace == namespace)
            record = session.execute(stmt).scalar_one_or_none()
            if record and record.config_json:
                return dict(record.config_json)
    except Exception as exc:
        logger.debug("config.app_config_load_failed", namespace=namespace, error=str(exc))
    return None


def _load_db_config(strategy_name: str) -> Optional[Dict[str, Any]]:
    """Load config from the strategy_configs table."""
    try:
        from db import get_session
        from db.models import StrategyConfigRecord
        from sqlalchemy import select

        with get_session() as session:
            stmt = select(StrategyConfigRecord).where(
                StrategyConfigRecord.strategy_name == strategy_name
            )
            record = session.execute(stmt).scalar_one_or_none()
            if record and record.config_json:
                return dict(record.config_json)
    except Exception as exc:
        logger.debug("config.db_load_failed", strategy=strategy_name, error=str(exc))
    return None


def _load_db_symbols() -> List[Dict[str, Any]]:
    """Load all symbol configs from the symbol_configs table."""
    try:
        from db import get_session
        from db.models import SymbolConfigRecord
        from sqlalchemy import select

        with get_session() as session:
            stmt = select(SymbolConfigRecord)
            records = session.execute(stmt).scalars().all()
            return [
                {
                    "symbol": r.symbol,
                    "exchange": r.exchange,
                    "timeframes": list(r.timeframes) if r.timeframes else ["15m", "1h"],
                    "enabled": r.enabled,
                    "description": r.description,
                }
                for r in records
            ]
    except Exception as exc:
        logger.debug("config.db_symbols_load_failed", error=str(exc))
        return []


# ── DB Persistence ───────────────────────────────────────────────────────────


def save_db_config(strategy_name: str, config: Dict[str, Any]) -> None:
    """Persist strategy config to the database."""
    from db import get_session
    from db.models import StrategyConfigRecord
    from sqlalchemy import select

    with get_session() as session:
        stmt = select(StrategyConfigRecord).where(
            StrategyConfigRecord.strategy_name == strategy_name
        )
        record = session.execute(stmt).scalar_one_or_none()
        if record:
            record.config_json = config
        else:
            record = StrategyConfigRecord(
                strategy_name=strategy_name,
                config_json=config,
            )
            session.add(record)
        session.commit()


def delete_db_config(strategy_name: str) -> bool:
    """Delete DB config for a strategy. Returns True if a row was deleted."""
    from db import get_session
    from db.models import StrategyConfigRecord
    from sqlalchemy import delete

    with get_session() as session:
        stmt = delete(StrategyConfigRecord).where(
            StrategyConfigRecord.strategy_name == strategy_name
        )
        result = session.execute(stmt)
        session.commit()
        return result.rowcount > 0


def save_app_config(namespace: str, config: Dict[str, Any]) -> None:
    """Persist app config to the database by namespace."""
    from db import get_session
    from db.models import AppConfigRecord
    from sqlalchemy import select

    with get_session() as session:
        stmt = select(AppConfigRecord).where(AppConfigRecord.namespace == namespace)
        record = session.execute(stmt).scalar_one_or_none()
        if record:
            record.config_json = config
        else:
            record = AppConfigRecord(namespace=namespace, config_json=config)
            session.add(record)
        session.commit()


def delete_app_config(namespace: str) -> bool:
    """Delete app config by namespace. Returns True if deleted."""
    from db import get_session
    from db.models import AppConfigRecord
    from sqlalchemy import delete as sa_delete

    with get_session() as session:
        stmt = sa_delete(AppConfigRecord).where(AppConfigRecord.namespace == namespace)
        result = session.execute(stmt)
        session.commit()
        return result.rowcount > 0


# ── Symbol Persistence ───────────────────────────────────────────────────────


def get_symbol_source(symbol_name: str) -> str:
    """Return 'db' or 'default' for a given symbol."""
    db_symbols = _load_db_symbols()
    db_names = {s["symbol"] for s in db_symbols}
    return "db" if symbol_name in db_names else "default"


def save_symbol(
    symbol: str,
    exchange: str = "binance",
    timeframes: Optional[List[str]] = None,
    enabled: bool = True,
    description: Optional[str] = None,
) -> None:
    """Upsert a symbol config to the database."""
    from db import get_session
    from db.models import SymbolConfigRecord
    from sqlalchemy import select

    if timeframes is None:
        timeframes = ["15m", "1h"]

    with get_session() as session:
        stmt = select(SymbolConfigRecord).where(SymbolConfigRecord.symbol == symbol)
        record = session.execute(stmt).scalar_one_or_none()
        if record:
            record.exchange = exchange
            record.timeframes = timeframes
            record.enabled = enabled
            record.description = description
        else:
            record = SymbolConfigRecord(
                symbol=symbol,
                exchange=exchange,
                timeframes=timeframes,
                enabled=enabled,
                description=description,
            )
            session.add(record)
        session.commit()


def delete_symbol(symbol: str) -> bool:
    """Delete a symbol from the DB. Returns True if a row was deleted."""
    from db import get_session
    from db.models import SymbolConfigRecord
    from sqlalchemy import delete as sa_delete

    with get_session() as session:
        stmt = sa_delete(SymbolConfigRecord).where(SymbolConfigRecord.symbol == symbol)
        result = session.execute(stmt)
        session.commit()
        return result.rowcount > 0


# ── Config Source Helpers ────────────────────────────────────────────────────


def get_config_source(strategy_name: str, base_config: Dict[str, Any]) -> Dict[str, str]:
    """Return the source of each field: 'default' or 'db'."""
    sources: Dict[str, str] = {k: "default" for k in base_config}

    db_data = _load_db_config(strategy_name)
    if db_data:
        for k in db_data:
            if k in sources:
                sources[k] = "db"

    return sources


# ── Singleton Instance ────────────────────────────────────────────────────────

_settings_instance: Optional[Settings] = None


def get_settings(reload: bool = False) -> Settings:
    """
    Get the global settings instance (singleton pattern).

    Tries DB first. Falls back to YAML if DB is unreachable (graceful fallback).
    """
    global _settings_instance

    if _settings_instance is None or reload:
        try:
            settings = Settings.from_db()
            logger.debug("config.loaded_from_db")
        except Exception as exc:
            logger.warning("config.db_unavailable_fallback_defaults", error=str(exc))
            settings = Settings(
                symbols=[
                    SymbolConfig(symbol="BTCUSDT", timeframes=["15m", "1h"]),
                    SymbolConfig(symbol="ETHUSDT", timeframes=["15m", "1h"]),
                ],
            )
            logger.warning("config.loaded_from_pydantic_defaults")

        _settings_instance = settings

    return _settings_instance


# ── Module-level convenience ──────────────────────────────────────────────────

def reload_settings() -> Settings:
    """Reload settings from DB (or fallback)."""
    return get_settings(reload=True)
