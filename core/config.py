"""
Configuration loading and validation using Pydantic.

Loads YAML configuration files from the config/ directory and provides
type-safe access to settings throughout the application.

TODO Phase 5 (Task 5.7): Add DB config layer with priority: DB > ENV > YAML
Current implementation uses YAML as the sole source of truth.
"""

import os
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from pydantic import BaseModel, Field, field_validator


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


class StrategiesConfig(BaseModel):
    """Root configuration for all strategies."""
    
    smart_hodler: SmartHodlerConfig


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


# ── Singleton Instance ────────────────────────────────────────────────────────

_settings_instance: Optional[Settings] = None


def get_settings(reload: bool = False) -> Settings:
    """
    Get the global settings instance (singleton pattern).
    
    Args:
        reload: If True, reload settings from files even if already loaded
    
    Returns:
        Settings: The global configuration object
    """
    global _settings_instance
    
    if _settings_instance is None or reload:
        _settings_instance = Settings.from_yaml_files()
    
    return _settings_instance


# ── Module-level convenience ──────────────────────────────────────────────────

def reload_settings() -> Settings:
    """Reload settings from YAML files."""
    return get_settings(reload=True)
