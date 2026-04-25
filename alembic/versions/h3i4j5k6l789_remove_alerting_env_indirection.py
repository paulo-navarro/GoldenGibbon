"""Remove bot_token_env/chat_id_env from alerting app_config

Credentials now come directly from TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID
env vars, hardcoded in get_alerter(). No more indirection via DB fields.

Revision ID: h3i4j5k6l789
Revises: g2h3i4j5k678
Create Date: 2026-04-25
"""

from alembic import op
import sqlalchemy as sa

revision = "h3i4j5k6l789"
down_revision = "g2h3i4j5k678"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(
        sa.text(
            """
            UPDATE app_configs
            SET config_json = config_json - 'bot_token_env' - 'chat_id_env'
            WHERE namespace = 'alerting'
            """
        )
    )


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(
        sa.text(
            """
            UPDATE app_configs
            SET config_json = config_json || jsonb_build_object(
                'bot_token_env', 'TELEGRAM_BOT_TOKEN',
                'chat_id_env', 'TELEGRAM_CHAT_ID'
            )
            WHERE namespace = 'alerting'
            """
        )
    )
