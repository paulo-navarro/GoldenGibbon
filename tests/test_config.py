"""
Tests for configuration loading and validation.
"""

import pytest
from pydantic import ValidationError

from core.config import (
    BearGuardConfig,
    SessionDeadZone,
    Settings,
    ShortConfig,
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


# ── Short Config Tests ────────────────────────────────────────────────────────

class TestShortConfig:
    """Tests for ShortConfig (global kill switch)."""

    def test_default_disabled(self):
        """Shorts are disabled by default."""
        config = ShortConfig()
        assert config.enabled is False

    def test_explicit_enabled(self):
        config = ShortConfig(enabled=True)
        assert config.enabled is True


# ── BearGuard Config Tests ────────────────────────────────────────────────────

class TestBearGuardConfig:
    """Tests for BearGuardConfig validation."""

    def test_default_values(self):
        """Test that all defaults match strategy spec § 10."""
        config = BearGuardConfig()
        assert config.enabled is False
        assert config.timeframe_primary == "15m"
        assert config.timeframe_confirmation == "1h"
        assert config.ema_fast == 50
        assert config.ema_slow == 200
        assert config.adx_threshold == 25
        assert config.hourly_rsi_bear_threshold == 55
        assert config.hourly_ema_lookback == 4
        assert config.volume_filter_pct == 0.70
        assert config.rsi_overbought_threshold == 70
        assert config.adx_falling_lookback == 3
        assert config.exit_confirmation_candles == 3
        assert config.position_size_pct == 0.50
        assert config.hard_stop_pct == 0.05
        assert config.trailing_stop_atr_multiplier == 2.5
        assert config.trailing_stop_enabled is True
        assert config.breakeven_trigger_pct == 0.02
        assert config.lockin_trigger_pct == 0.04
        assert config.lockin_stop_pct == 0.01
        assert config.cooldown_candles == 16
        assert config.margin_type == "cross"
        assert config.max_borrow_rate_pct == 0.003

    def test_custom_overrides(self):
        config = BearGuardConfig(
            adx_threshold=30,
            hard_stop_pct=0.08,
            position_size_pct=0.40,
            margin_type="isolated",
        )
        assert config.adx_threshold == 30
        assert config.hard_stop_pct == 0.08
        assert config.position_size_pct == 0.40
        assert config.margin_type == "isolated"

    def test_percentage_bounds(self):
        """Test that bounded floats reject invalid values."""
        with pytest.raises(ValidationError):
            BearGuardConfig(hard_stop_pct=1.5)

        with pytest.raises(ValidationError):
            BearGuardConfig(position_size_pct=0)

        with pytest.raises(ValidationError):
            BearGuardConfig(position_size_pct=-0.1)

    def test_ema_period_too_large(self):
        with pytest.raises(ValidationError, match="EMA period too large"):
            BearGuardConfig(ema_fast=1000)

    def test_invalid_margin_type(self):
        with pytest.raises(ValidationError, match="Invalid margin_type"):
            BearGuardConfig(margin_type="leverage")

    def test_settings_has_shorts_and_bear_guard(self):
        """Settings exposes shorts kill switch and bear_guard config."""
        settings = Settings(symbols=[])
        assert settings.shorts.enabled is False
        assert settings.strategies.bear_guard.hard_stop_pct == 0.05
        assert settings.strategies.bear_guard.margin_type == "cross"


# ── Reconciliation Config Tests ───────────────────────────────────────────────

class TestReconciliationConfig:
    """Tests for ReconciliationConfig (runtime reconciliation settings)."""

    def test_default_values(self):
        from decimal import Decimal

        from core.config import ReconciliationConfig

        config = ReconciliationConfig()
        assert config.enabled is True
        assert config.interval_minutes == 2
        assert config.auto_repair is True
        assert config.force_close_orphans is True
        assert config.recover_untracked is True
        assert config.balance_drift_threshold == Decimal("0.01")
        assert config.size_mismatch_threshold == Decimal("0.00000100")
        assert config.recovery_age_seconds == 120
        assert config.lost_age_seconds == 300

    def test_custom_overrides(self):
        from decimal import Decimal

        from core.config import ReconciliationConfig

        config = ReconciliationConfig(
            enabled=False,
            interval_minutes=5,
            auto_repair=False,
            balance_drift_threshold=Decimal("1.0"),
            recovery_age_seconds=60,
        )
        assert config.enabled is False
        assert config.interval_minutes == 5
        assert config.auto_repair is False
        assert config.balance_drift_threshold == Decimal("1.0")
        assert config.recovery_age_seconds == 60

    def test_validation_bounds(self):
        from core.config import ReconciliationConfig

        with pytest.raises(ValidationError):
            ReconciliationConfig(interval_minutes=0)

        with pytest.raises(ValidationError):
            ReconciliationConfig(recovery_age_seconds=10)

        with pytest.raises(ValidationError):
            ReconciliationConfig(lost_age_seconds=5)

    def test_settings_exposes_reconciliation(self):
        settings = Settings(symbols=[])
        assert settings.reconciliation.enabled is True
        assert settings.reconciliation.interval_minutes == 2

    def test_in_namespace_models(self):
        from core.config import NAMESPACE_MODELS, ReconciliationConfig

        assert "reconciliation" in NAMESPACE_MODELS
        assert NAMESPACE_MODELS["reconciliation"] is ReconciliationConfig
