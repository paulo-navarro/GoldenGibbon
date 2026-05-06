"""add exchange_order_id to trade_records

Revision ID: a1b2c3d4e503
Revises: a1b2c3d4e502
Create Date: 2026-05-06 12:02:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e503'
down_revision: Union[str, Sequence[str], None] = 'a1b2c3d4e502'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('trade_records', sa.Column('exchange_order_id', sa.String(length=100), nullable=True))
    op.create_index('ix_trade_records_exchange_order_id', 'trade_records', ['exchange_order_id'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_trade_records_exchange_order_id', table_name='trade_records')
    op.drop_column('trade_records', 'exchange_order_id')
