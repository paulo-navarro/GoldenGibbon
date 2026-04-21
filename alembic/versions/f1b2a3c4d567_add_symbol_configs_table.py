"""Add symbol_configs table

Revision ID: f1b2a3c4d567
Revises: e9a5c3d7f201
Create Date: 2026-04-20
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "f1b2a3c4d567"
down_revision = "e9a5c3d7f201"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "symbol_configs",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("symbol", sa.String(20), nullable=False),
        sa.Column("exchange", sa.String(20), nullable=False, server_default="binance"),
        sa.Column("timeframes", JSONB(), nullable=False, server_default='["15m", "1h"]'),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("description", sa.String(100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_symbol_configs_symbol", "symbol_configs", ["symbol"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_symbol_configs_symbol", table_name="symbol_configs")
    op.drop_table("symbol_configs")
