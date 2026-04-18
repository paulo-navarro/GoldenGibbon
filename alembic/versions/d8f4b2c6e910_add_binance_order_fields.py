"""add Binance order fields (time_in_force, limit_price, reject_reason, exchange_status)

Revision ID: d8f4b2c6e910
Revises: c7d3e9f2a158
Create Date: 2026-04-17 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'd8f4b2c6e910'
down_revision: Union[str, Sequence[str]] = 'c7d3e9f2a158'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add fields needed for real Binance order tracking."""
    op.add_column(
        'order_records',
        sa.Column('time_in_force', sa.String(length=10), nullable=True),
    )
    op.add_column(
        'order_records',
        sa.Column('limit_price', sa.Numeric(precision=20, scale=8), nullable=True),
    )
    op.add_column(
        'order_records',
        sa.Column('reject_reason', sa.String(length=500), nullable=True),
    )
    op.add_column(
        'order_records',
        sa.Column('exchange_status', sa.String(length=50), nullable=True),
    )


def downgrade() -> None:
    """Remove Binance order fields."""
    op.drop_column('order_records', 'exchange_status')
    op.drop_column('order_records', 'reject_reason')
    op.drop_column('order_records', 'limit_price')
    op.drop_column('order_records', 'time_in_force')
