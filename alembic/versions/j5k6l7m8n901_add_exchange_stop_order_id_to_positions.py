"""add exchange_stop_order_id to positions

Revision ID: j5k6l7m8n901
Revises: i4j5k6l7m890
Create Date: 2026-04-26 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'j5k6l7m8n901'
down_revision: Union[str, Sequence[str]] = 'i4j5k6l7m890'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add exchange_stop_order_id for Binance-side stop orders."""
    op.add_column(
        'positions',
        sa.Column('exchange_stop_order_id', sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    """Remove exchange_stop_order_id."""
    op.drop_column('positions', 'exchange_stop_order_id')
