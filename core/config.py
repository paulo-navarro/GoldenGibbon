"""
Configuration loading and validation using Pydantic.

Loads configuration with priority: DB > ENV > YAML.
"""

import os
from pathlib import Path
from typing import Any, Dict, List, Optional

import structlog
import yaml
from pydantic import BaseModel, Field, field_validator

logger = structlog.get_logger(__name__)


# ── Path Resolution ──────────────────────────────────────────────────────────

def get_config_dir() -> Path:
    """Get the config directory path."""
    # Assume config/ is adjacent to core/ in the project root
    return Path(__file__).parent.parent / "config"


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

    smart_hodler: SmartHodlerConfig
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
    # Per-trade absolute notional cap in USDT (None = no cap)
    max_trade_size_usdt: Optional[float] = Field(default=None, gt=0)
    # Per-symbol absolute exposure cap in USDT (None = no cap)
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
    # Binance API credentials — env var names (keys themselves stay in .env)
    api_key_env: str = Field(default="BINANCE_API_KEY")
    api_secret_env: str = Field(default="BINANCE_API_SECRET")
    # Use Binance testnet (testnet.binance.vision) — default True for safety
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
    """
    Telegram alerting settings.

    Sends notifications for important trading events (fills, stops,
    kill-switch).  Credentials are read from environment variables
    so they never appear in config files.
    """

    enabled: bool = Field(default=False)
    # Env var names for Telegram Bot API credentials
    bot_token_env: str = Field(default="TELEGRAM_BOT_TOKEN")
    chat_id_env: str = Field(default="TELEGRAM_CHAT_ID")
    # Which events trigger alerts
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

class Settings(BaseModel):
    """
    Root configuration object.
    
    Loads all configuration from YAML files in the config/ directory.
    Validates all settings at load time.
    """
    
    symbols: List[SymbolConfig]
    strategies: StrategiesConfig
    risk: RiskConfig
    execution: ExecutionConfig
    data: DataConfig
    logging: LoggingConfig
    backtest: BacktestConfig
    paper_trading: PaperTradingConfig
    live_trading: LiveTradingConfig
    ws_feed: WebSocketFeedConfig = Field(default_factory=WebSocketFeedConfig)
    alerting: AlertingConfig = Field(default_factory=AlertingConfig)
    regime: RegimeConfig = Field(default_factory=RegimeConfig)
    system: SystemConfig
    
    @classmethod
    def load_yaml(cls, filepath: Path) -> Dict[str, Any]:
        """Load a YAML file and return its contents."""
        if not filepath.exists():
            raise FileNotFoundError(f"Config file not found: {filepath}")
        
        with open(filepath, "r") as f:
            data = yaml.safe_load(f)
        
        if data is None:
            raise ValueError(f"Config file is empty: {filepath}")
        
        return data
    
    @classmethod
    def from_yaml_files(cls) -> "Settings":
        """
        Load configuration from YAML files in the config/ directory.
        
        Loads:
        - config/symbols.yaml
        - config/strategies.yaml
        - config/settings.yaml
        
        Returns:
            Settings: Validated configuration object
        
        Raises:
            FileNotFoundError: If any config file is missing
            ValidationError: If any config value is invalid
        """
        config_dir = get_config_dir()
        
        # Load individual config files
        symbols_data = cls.load_yaml(config_dir / "symbols.yaml")
        strategies_data = cls.load_yaml(config_dir / "strategies.yaml")
        settings_data = cls.load_yaml(config_dir / "settings.yaml")
        
        # Merge into a single dict for validation
        config_dict = {
            "symbols": symbols_data.get("symbols", []),
            "strategies": strategies_data.get("strategies", {}),
            **settings_data,
        }
        
        # Validate and return
        return cls(**config_dict)
    
    @property
    def enabled_symbols(self) -> List[SymbolConfig]:
        """Get only enabled symbols."""
        return [s for s in self.symbols if s.enabled]


# ── DB Config Persistence (task 5.7b) ─────────────────────────────────────────


def _load_db_config(strategy_name: str) -> Optional[Dict[str, Any]]:
    """Load config overrides from the strategy_configs table, or None."""
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


def save_db_config(strategy_name: str, config: Dict[str, Any]) -> None:
    """Persist strategy config overrides to the database."""
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
    """Delete DB overrides for a strategy. Returns True if a row was deleted."""
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


def _load_env_overrides(strategy_name: str) -> Dict[str, Any]:
    """Load config overrides from environment variables.

    Convention: GG_STRATEGY_{UPPER_NAME}_{UPPER_FIELD}=value
    e.g. GG_STRATEGY_SMART_HODLER_EMA_FAST=30
    """
    prefix = f"GG_STRATEGY_{strategy_name.upper()}_"
    overrides: Dict[str, Any] = {}

    for key, value in os.environ.items():
        if key.startswith(prefix):
            field_name = key[len(prefix):].lower()
            if value.lower() in ("true", "false"):
                overrides[field_name] = value.lower() == "true"
            else:
                try:
                    overrides[field_name] = int(value)
                except ValueError:
                    try:
                        overrides[field_name] = float(value)
                    except ValueError:
                        overrides[field_name] = value

    return overrides


def get_config_source(strategy_name: str, base_config: Dict[str, Any]) -> Dict[str, str]:
    """Return the source of each field: 'yaml', 'env', or 'db'."""
    sources: Dict[str, str] = {k: "yaml" for k in base_config}

    env_overrides = _load_env_overrides(strategy_name)
    for k in env_overrides:
        if k in sources:
            sources[k] = "env"

    db_overrides = _load_db_config(strategy_name)
    if db_overrides:
        for k in db_overrides:
            if k in sources:
                sources[k] = "db"

    return sources


def _apply_overrides(strategy_name: str, base_config: Dict[str, Any]) -> Dict[str, Any]:
    """Apply ENV then DB overrides on top of YAML base config."""
    merged = dict(base_config)

    env_overrides = _load_env_overrides(strategy_name)
    if env_overrides:
        merged.update(env_overrides)
        logger.debug("config.env_applied", strategy=strategy_name, fields=list(env_overrides.keys()))

    db_overrides = _load_db_config(strategy_name)
    if db_overrides:
        merged.update(db_overrides)
        logger.debug("config.db_applied", strategy=strategy_name, fields=list(db_overrides.keys()))

    return merged


# ── Singleton Instance ────────────────────────────────────────────────────────

_settings_instance: Optional[Settings] = None


def get_settings(reload: bool = False) -> Settings:
    """
    Get the global settings instance (singleton pattern).

    Loads from YAML, then applies ENV and DB overrides to strategy configs.
    """
    global _settings_instance

    if _settings_instance is None or reload:
        settings = Settings.from_yaml_files()

        for strategy_name in list(settings.strategies.__class__.model_fields.keys()):
            cfg = getattr(settings.strategies, strategy_name, None)
            if cfg is None:
                continue
            base = cfg.model_dump() if hasattr(cfg, "model_dump") else dict(cfg)
            merged = _apply_overrides(strategy_name, base)
            if merged != base:
                try:
                    validated = cfg.__class__(**merged)
                    setattr(settings.strategies, strategy_name, validated)
                except Exception as exc:
                    logger.warning("config.override_failed", strategy=strategy_name, error=str(exc))

        _settings_instance = settings

    return _settings_instance


# ── Module-level convenience ──────────────────────────────────────────────────

def reload_settings() -> Settings:
    """Reload settings from YAML files + ENV + DB overrides."""
    return get_settings(reload=True)
