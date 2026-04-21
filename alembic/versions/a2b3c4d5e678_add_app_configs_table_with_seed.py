"""Add app_configs table with seed data

Revision ID: a2b3c4d5e678
Revises: f1b2a3c4d567
Create Date: 2026-04-20
"""

import json

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "a2b3c4d5e678"
down_revision = "f1b2a3c4d567"
branch_labels = None
depends_on = None


# ── Seed data (Pydantic model defaults) ──────────────────────────────────────

SEED_APP_CONFIGS = {
    "risk": {
        "max_drawdown_per_trade": 0.03,
        "trailing_stop_atr_multiplier": 2.0,
        "cooldown_candles": 16,
        "max_position_size_pct": 1.0,
        "max_total_exposure_pct": 1.0,
        "max_trades_per_day": 20,
        "max_daily_loss_pct": 0.10,
        "max_trade_size_usdt": None,
        "max_symbol_exposure_usdt": None,
    },
    "execution": {
        "slippage": 0.001,
        "taker_fee": 0.001,
        "maker_fee": 0.001,
        "default_order_type": "market",
        "order_timeout": 30,
        "max_retries": 3,
        "retry_delay": 1,
    },
    "data": {
        "binance_api_base": "https://api.binance.com",
        "max_candles_per_request": 1000,
        "historical_lookback_days": 730,
        "cache_ttl": 300,
    },
    "logging": {
        "level": "INFO",
        "format": "json",
        "include_timestamp": True,
        "include_context": True,
        "console": True,
        "file": True,
        "file_path": "logs/trading.log",
        "max_file_size_mb": 100,
        "backup_count": 10,
    },
    "backtest": {
        "initial_capital": 10000,
        "benchmark_symbol": "BTCUSDT",
        "save_trades": True,
        "save_equity_curve": True,
        "save_metrics": True,
        "output_format": "both",
    },
    "paper_trading": {
        "enabled": False,
        "initial_capital": 10000,
        "simulate_slippage": True,
        "simulate_latency": True,
        "latency_ms": 100,
    },
    "live_trading": {
        "enabled": False,
        "require_manual_approval": True,
        "max_order_size_usdt": 1000,
        "kill_switch_max_drawdown": 0.15,
        "reconcile_on_startup": True,
        "reconciliation_interval_hours": 4,
        "api_key_env": "BINANCE_API_KEY",
        "api_secret_env": "BINANCE_API_SECRET",
        "use_testnet": True,
    },
    "ws_feed": {
        "enabled": False,
        "base_url": "wss://stream.binance.com:9443",
        "reconnect_delay": 1.0,
        "max_reconnect_delay": 60.0,
        "ping_interval": 20,
        "ping_timeout": 10,
    },
    "alerting": {
        "enabled": False,
        "bot_token_env": "TELEGRAM_BOT_TOKEN",
        "chat_id_env": "TELEGRAM_CHAT_ID",
        "alert_on_fill": True,
        "alert_on_stop": True,
        "alert_on_kill_switch": True,
        "alert_on_reconciliation": True,
        "alert_on_error": False,
    },
    "regime": {
        "adx_trending_threshold": 25.0,
        "adx_ranging_threshold": 20.0,
        "smoothing_window": 3,
        "rebalance_enabled": False,
        "regime_shift_pct": 0.2,
        "strategy_regime_map": {"smart_hodler": "trending", "mean_reversion": "ranging"},
    },
    "system": {
        "timezone": "UTC",
        "environment": "production",
    },
}

SEED_STRATEGIES = {
    "smart_hodler": {
        "enabled": True,
        "timeframe_primary": "15m",
        "timeframe_confirmation": "1h",
        "ema_fast": 50,
        "ema_slow": 200,
        "adx_period": 14,
        "adx_threshold": 25,
        "volume_sma_period": 20,
        "ema_hourly": 21,
        "rsi_period": 14,
        "rsi_threshold": 45,
        "entry_initial_pct": 0.50,
        "entry_scale_1_pct": 0.25,
        "entry_scale_2_pct": 0.25,
        "entry_scale_1_candles": 8,
        "entry_scale_2_candles": 16,
        "exit_momentum_fade_pct": 0.50,
        "exit_confirmation_candles": 2,
        "atr_period": 14,
        "trailing_stop_atr_multiplier": 2.0,
        "hard_stop_pct": 0.03,
        "cooldown_candles": 16,
        "session_filter_enabled": True,
        "adx_falling_lookback": 3,
    },
    "mean_reversion": {
        "enabled": True,
        "timeframe_primary": "15m",
        "timeframe_confirmation": "1h",
        "bb_period": 20,
        "bb_std_dev": 2.0,
        "rsi_period": 14,
        "rsi_oversold": 30,
        "rsi_overbought": 70,
        "adx_period": 14,
        "adx_threshold": 25,
        "volume_sma_period": 20,
        "volume_spike_multiplier": 1.5,
        "ema_hourly": 21,
        "rsi_hourly_floor": 35,
        "entry_pct": 0.75,
        "exit_partial_pct": 0.50,
        "hard_stop_pct": 0.02,
        "atr_period": 14,
        "time_stop_candles": 16,
        "time_stop_cooldown_candles": 4,
        "cooldown_candles": 8,
        "session_filter_enabled": True,
    },
}

SEED_SYMBOLS = [
    {
        "symbol": "BTCUSDT",
        "exchange": "binance",
        "timeframes": ["15m", "1h"],
        "enabled": True,
        "description": "Bitcoin / Tether",
    },
    {
        "symbol": "ETHUSDT",
        "exchange": "binance",
        "timeframes": ["15m", "1h"],
        "enabled": True,
        "description": "Ethereum / Tether",
    },
]


def upgrade() -> None:
    # Create app_configs table
    op.create_table(
        "app_configs",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("namespace", sa.String(50), nullable=False),
        sa.Column("config_json", JSONB(), nullable=False, server_default="{}"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_app_configs_namespace", "app_configs", ["namespace"], unique=True)

    # Seed app_configs
    app_configs_table = sa.table(
        "app_configs",
        sa.column("namespace", sa.String),
        sa.column("config_json", JSONB),
    )
    for namespace, config in SEED_APP_CONFIGS.items():
        op.execute(
            app_configs_table.insert().values(
                namespace=namespace,
                config_json=config,
            )
        )

    # Seed strategy_configs (upsert — skip if already exists)
    strategy_configs_table = sa.table(
        "strategy_configs",
        sa.column("strategy_name", sa.String),
        sa.column("config_json", JSONB),
    )
    for name, config in SEED_STRATEGIES.items():
        op.execute(
            sa.text(
                "INSERT INTO strategy_configs (strategy_name, config_json, updated_at) "
                "VALUES (:name, :config, NOW()) "
                "ON CONFLICT (strategy_name) DO NOTHING"
            ).bindparams(name=name, config=json.dumps(config))
        )

    # Seed symbol_configs (upsert — skip if already exists)
    for sym in SEED_SYMBOLS:
        op.execute(
            sa.text(
                "INSERT INTO symbol_configs (symbol, exchange, timeframes, enabled, description, created_at, updated_at) "
                "VALUES (:symbol, :exchange, :timeframes, :enabled, :description, NOW(), NOW()) "
                "ON CONFLICT (symbol) DO NOTHING"
            ).bindparams(
                symbol=sym["symbol"],
                exchange=sym["exchange"],
                timeframes=json.dumps(sym["timeframes"]),
                enabled=sym["enabled"],
                description=sym["description"],
            )
        )


def downgrade() -> None:
    # Remove seeded data
    op.execute(sa.text("DELETE FROM symbol_configs WHERE symbol IN ('BTCUSDT', 'ETHUSDT')"))
    op.execute(sa.text("DELETE FROM strategy_configs WHERE strategy_name IN ('smart_hodler', 'mean_reversion')"))
    op.drop_index("ix_app_configs_namespace", table_name="app_configs")
    op.drop_table("app_configs")
