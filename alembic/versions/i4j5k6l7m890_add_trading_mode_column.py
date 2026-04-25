"""Add trading_mode column to order_records, trade_records, portfolio_snapshots

Adds VARCHAR(10) column with server default 'paper' and index.
Migrates existing rows: run_id LIKE 'live_%' → 'live', rest stays 'paper'.

Revision ID: i4j5k6l7m890
Revises: h3i4j5k6l789
Create Date: 2026-04-25
"""

from alembic import op
import sqlalchemy as sa

revision = "i4j5k6l7m890"
down_revision = "h3i4j5k6l789"
branch_labels = None
depends_on = None

_tables = ("order_records", "trade_records", "portfolio_snapshots")


def upgrade() -> None:
    for table in _tables:
        op.add_column(
            table,
            sa.Column("trading_mode", sa.String(10), nullable=False, server_default="paper"),
        )
        op.create_index(f"ix_{table}_trading_mode", table, ["trading_mode"])

    for table in _tables:
        op.execute(
            f"UPDATE {table} SET trading_mode = 'live' WHERE run_id LIKE 'live_%'"
        )


def downgrade() -> None:
    for table in _tables:
        op.drop_index(f"ix_{table}_trading_mode", table_name=table)
        op.drop_column(table, "trading_mode")
