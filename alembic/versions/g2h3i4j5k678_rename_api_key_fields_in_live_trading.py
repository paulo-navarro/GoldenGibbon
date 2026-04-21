"""Rename api_key_env/api_secret_env to api_key/api_secret in live_trading app_config

The credentials are now stored directly in the DB instead of being looked up
from environment variable names.

Revision ID: g2h3i4j5k678
Revises: a2b3c4d5e678
Create Date: 2026-04-21
"""

import os

from alembic import op
import sqlalchemy as sa

revision = "g2h3i4j5k678"
down_revision = "a2b3c4d5e678"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()

    # Rename api_key_env -> api_key and api_secret_env -> api_secret,
    # replacing the placeholder env var names with actual values from ENV
    # (empty string if not set — operator must configure via Settings UI).
    api_key = os.environ.get("BINANCE_API_KEY", "")
    api_secret = os.environ.get("BINANCE_API_SECRET", "")

    conn.execute(
        sa.text(
            """
            UPDATE app_configs
            SET config_json = (
                config_json
                - 'api_key_env'
                - 'api_secret_env'
                || jsonb_build_object('api_key', :api_key, 'api_secret', :api_secret)
            )
            WHERE namespace = 'live_trading'
            """
        ),
        {"api_key": api_key, "api_secret": api_secret},
    )


def downgrade() -> None:
    conn = op.get_bind()

    conn.execute(
        sa.text(
            """
            UPDATE app_configs
            SET config_json = (
                config_json
                - 'api_key'
                - 'api_secret'
                || jsonb_build_object(
                    'api_key_env', 'BINANCE_API_KEY',
                    'api_secret_env', 'BINANCE_API_SECRET'
                )
            )
            WHERE namespace = 'live_trading'
            """
        )
    )
