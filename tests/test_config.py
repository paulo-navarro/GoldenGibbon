"""
Tests for configuration loading and validation.
"""

import pytest
from pydantic import ValidationError

from core.config import (
    SessionDeadZone,
    Settings,
    SmartHodlerConfig,
    SymbolConfig,
    get_settings,
    reload_settings,
)


# ── Symbol Configuration Tests ───────────────────────────────────────────────

class TestSymbolConfig:
    """Tests for SymbolConfig validation."""
    
    def test_valid_symbol_config(self):
        """Test that a valid symbol config loads correctly."""
        config = SymbolConfig(
            symbol="BTCUSDT",
            exchange="binance",
            timeframes=["15m", "1h"],
            enabled=True,
            description="Bitcoin",
        )
        assert config.symbol == "BTCUSDT"
        assert config.exchange == "binance"
        assert config.timeframes == ["15m", "1h"]
        assert config.enabled is True
    
    def test_symbol_must_be_uppercase(self):
        """Test that lowercase symbols are rejected."""
        with pytest.raises(ValidationError, match="must be uppercase"):
            SymbolConfig(
                symbol="btcusdt",
                timeframes=["15m"],
            )
    
    def test_symbol_must_end_with_usdt(self):
        """Test that symbols not ending in USDT are rejected."""
        with pytest.raises(ValidationError, match="must end with USDT"):
            SymbolConfig(
                symbol="BTCETH",
                timeframes=["15m"],
            )
    
    def test_invalid_timeframe(self):
        """Test that invalid timeframes are rejected."""
        with pytest.raises(ValidationError, match="Invalid timeframe"):
            SymbolConfig(
                symbol="BTCUSDT",
                timeframes=["15m", "2h"],  # 2h is invalid
            )
    
    def test_valid_timeframes(self):
        """Test that all valid timeframes are accepted."""
        valid_timeframes = ["1m", "5m", "15m", "30m", "1h", "4h", "1d", "1w"]
        config = SymbolConfig(
            symbol="BTCUSDT",
            timeframes=valid_timeframes,
        )
        assert config.timeframes == valid_timeframes


# ── Strategy Configuration Tests ──────────────────────────────────────────────

class TestSmartHodlerConfig:
    """Tests for SmartHodlerConfig validation."""
    
    def test_default_values(self):
        """Test that default values are set correctly."""
        config = SmartHodlerConfig()
        assert config.enabled is True
        assert config.timeframe_primary == "15m"
        assert config.ema_fast == 50
        assert config.ema_slow == 200
        assert config.adx_threshold == 25
    
    def test_custom_values(self):
        """Test that custom values override defaults."""
        config = SmartHodlerConfig(
            ema_fast=100,
            ema_slow=300,
            adx_threshold=30,
        )
        assert config.ema_fast == 100
        assert config.ema_slow == 300
        assert config.adx_threshold == 30
    
    def test_percentage_bounds(self):
        """Test that percentage values are bounded between 0 and 1."""
        # Valid values
        config = SmartHodlerConfig(
            entry_initial_pct=0.5,
            hard_stop_pct=0.03,
        )
        assert config.entry_initial_pct == 0.5
        assert config.hard_stop_pct == 0.03
        
        # Invalid: > 1
        with pytest.raises(ValidationError):
            SmartHodlerConfig(entry_initial_pct=1.5)
        
        # Invalid: <= 0
        with pytest.raises(ValidationError):
            SmartHodlerConfig(entry_initial_pct=0)
    
    def test_positive_integers(self):
        """Test that integer periods must be positive."""
        # Valid
        config = SmartHodlerConfig(ema_fast=50)
        assert config.ema_fast == 50
        
        # Invalid: zero
        with pytest.raises(ValidationError):
            SmartHodlerConfig(ema_fast=0)
        
        # Invalid: negative
        with pytest.raises(ValidationError):
            SmartHodlerConfig(ema_fast=-10)
    
    def test_ema_period_too_large(self):
        """Test that EMA periods have reasonable upper bounds."""
        with pytest.raises(ValidationError, match="EMA period too large"):
            SmartHodlerConfig(ema_fast=1000)
    
    def test_session_dead_zones(self):
        """Test session dead zone configuration."""
        config = SmartHodlerConfig(
            session_filter_enabled=True,
            session_dead_zones=[
                SessionDeadZone(
                    name="Weekend",
                    start_utc="Saturday 21:00",
                    end_utc="Sunday 20:00",
                ),
                SessionDeadZone(
                    name="Overnight",
                    start_utc="21:00",
                    end_utc="01:00",
                ),
            ],
        )
        assert len(config.session_dead_zones) == 2
        assert config.session_dead_zones[0].name == "Weekend"
        assert config.session_dead_zones[1].name == "Overnight"


# ── Risk Configuration Tests ──────────────────────────────────────────────────

class TestRiskConfig:
    """Tests for risk configuration validation."""
    
    def test_drawdown_bounds(self):
        """Test that drawdown percentages are bounded."""
        from core.config import RiskConfig
        
        # Valid
        config = RiskConfig(max_drawdown_per_trade=0.03)
        assert config.max_drawdown_per_trade == 0.03
        
        # Invalid: > 1
        with pytest.raises(ValidationError):
            RiskConfig(max_drawdown_per_trade=1.5)
        
        # Invalid: <= 0
        with pytest.raises(ValidationError):
            RiskConfig(max_drawdown_per_trade=0)


# ── Execution Configuration Tests ─────────────────────────────────────────────

class TestExecutionConfig:
    """Tests for execution configuration validation."""
    
    def test_order_type_validation(self):
        """Test that only valid order types are accepted."""
        from core.config import ExecutionConfig
        
        # Valid
        config = ExecutionConfig(default_order_type="market")
        assert config.default_order_type == "market"
        
        config = ExecutionConfig(default_order_type="limit")
        assert config.default_order_type == "limit"
        
        # Invalid
        with pytest.raises(ValidationError, match="Invalid order type"):
            ExecutionConfig(default_order_type="stop_loss")


# ── Logging Configuration Tests ───────────────────────────────────────────────

class TestLoggingConfig:
    """Tests for logging configuration validation."""
    
    def test_log_level_validation(self):
        """Test that only valid log levels are accepted."""
        from core.config import LoggingConfig
        
        # Valid
        for level in ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]:
            config = LoggingConfig(level=level)
            assert config.level == level
        
        # Valid (case insensitive)
        config = LoggingConfig(level="info")
        assert config.level == "INFO"
        
        # Invalid
        with pytest.raises(ValidationError, match="Invalid log level"):
            LoggingConfig(level="TRACE")
    
    def test_log_format_validation(self):
        """Test that only valid log formats are accepted."""
        from core.config import LoggingConfig
        
        # Valid
        config = LoggingConfig(format="json")
        assert config.format == "json"
        
        config = LoggingConfig(format="text")
        assert config.format == "text"
        
        # Invalid
        with pytest.raises(ValidationError, match="Invalid log format"):
            LoggingConfig(format="xml")


# ── System Configuration Tests ────────────────────────────────────────────────

class TestSystemConfig:
    """Tests for system configuration validation."""
    
    def test_environment_validation(self):
        """Test that only valid environments are accepted."""
        from core.config import SystemConfig
        
        # Valid
        for env in ["development", "staging", "production"]:
            config = SystemConfig(environment=env)
            assert config.environment == env
        
        # Invalid
        with pytest.raises(ValidationError, match="Invalid environment"):
            SystemConfig(environment="testing")


# ── Full Settings Integration Tests ───────────────────────────────────────────

class TestSettings:
    """Tests for full settings loading."""

    def test_load_settings(self):
        """Test that settings load with correct defaults."""
        settings = get_settings(reload=True)

        assert len(settings.symbols) > 0
        assert any(s.symbol == "BTCUSDT" for s in settings.symbols)

        assert settings.risk.max_drawdown_per_trade == 0.03
        assert settings.execution.slippage == 0.001
        assert settings.execution.taker_fee == 0.001
        assert settings.logging.level == "INFO"
        assert settings.backtest.initial_capital == 10000
        assert settings.system.timezone == "UTC"

    def test_enabled_symbols_property(self):
        """Test that enabled_symbols returns only enabled symbols."""
        settings = get_settings(reload=True)
        enabled = settings.enabled_symbols

        assert all(s.enabled for s in enabled)
        assert len(enabled) == len([s for s in settings.symbols if s.enabled])
    
    def test_singleton_pattern(self):
        """Test that get_settings returns the same instance."""
        settings1 = get_settings()
        settings2 = get_settings()
        
        # Should be the same instance
        assert settings1 is settings2
    
    def test_reload_settings(self):
        """Test that reload_settings forces a fresh load."""
        settings1 = get_settings()
        settings2 = reload_settings()
        
        # Should have the same values but be different instances
        assert settings1.strategies.smart_hodler.ema_fast == settings2.strategies.smart_hodler.ema_fast
        # Note: Due to module-level caching, we can't easily test that they're different instances
        # without modifying config between calls


# ── Edge Cases ────────────────────────────────────────────────────────────────

class TestEdgeCases:
    """Tests for edge cases and error handling."""
    
    def test_empty_symbol_list(self):
        """Test handling of empty symbol list."""
        settings = Settings(
            symbols=[],
            strategies={"smart_hodler": SmartHodlerConfig()},
            risk={"max_drawdown_per_trade": 0.03},
            execution={},
            data={},
            logging={},
            backtest={},
            paper_trading={},
            live_trading={},
            system={},
        )
        assert settings.symbols == []
        assert settings.enabled_symbols == []
    
    def test_all_symbols_disabled(self):
        """Test that enabled_symbols works when all symbols are disabled."""
        settings = Settings(
            symbols=[
                SymbolConfig(symbol="BTCUSDT", timeframes=["15m"], enabled=False),
                SymbolConfig(symbol="ETHUSDT", timeframes=["15m"], enabled=False),
            ],
            strategies={"smart_hodler": SmartHodlerConfig()},
            risk={"max_drawdown_per_trade": 0.03},
            execution={},
            data={},
            logging={},
            backtest={},
            paper_trading={},
            live_trading={},
            system={},
        )
        assert len(settings.symbols) == 2
        assert len(settings.enabled_symbols) == 0
