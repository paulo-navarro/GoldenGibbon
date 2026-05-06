"""add strategy column to order_records

Revision ID: a1b2c3d4e502
Revises: a1b2c3d4e501
Create Date: 2026-05-06 12:01:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e502'
down_revision: Union[str, Sequence[str], None] = 'a1b2c3d4e501'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('order_records', sa.Column('strategy', sa.String(length=50), nullable=True))
    op.create_index('ix_order_records_strategy', 'order_records', ['strategy'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_order_records_strategy', table_name='order_records')
    op.drop_column('order_records', 'strategy')
