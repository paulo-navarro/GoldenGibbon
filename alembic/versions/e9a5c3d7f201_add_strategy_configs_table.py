"""Add strategy_configs table

Revision ID: e9a5c3d7f201
Revises: d8f4b2c6e910
Create Date: 2026-04-19

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers, used by Alembic.
revision = "e9a5c3d7f201"
down_revision = "d8f4b2c6e910"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "strategy_configs",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("strategy_name", sa.String(50), nullable=False),
        sa.Column("config_json", JSONB, nullable=False, server_default="{}"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_strategy_configs_strategy_name", "strategy_configs", ["strategy_name"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_strategy_configs_strategy_name")
    op.drop_table("strategy_configs")
