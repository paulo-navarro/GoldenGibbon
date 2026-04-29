"""widen candle volume and quote_volume precision

Revision ID: k6l7m8n9o012
Revises: j5k6l7m8n901
Create Date: 2026-04-29 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'k6l7m8n9o012'
down_revision: Union[str, Sequence[str]] = 'j5k6l7m8n901'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Widen volume/quote_volume from Numeric(20,8) to Numeric(30,8) for high-volume tokens."""
    op.alter_column(
        'candles', 'volume',
        type_=sa.Numeric(precision=30, scale=8),
        existing_type=sa.Numeric(precision=20, scale=8),
        existing_nullable=False,
    )
    op.alter_column(
        'candles', 'quote_volume',
        type_=sa.Numeric(precision=30, scale=8),
        existing_type=sa.Numeric(precision=20, scale=8),
        existing_nullable=True,
    )


def downgrade() -> None:
    """Revert volume/quote_volume back to Numeric(20,8)."""
    op.alter_column(
        'candles', 'volume',
        type_=sa.Numeric(precision=20, scale=8),
        existing_type=sa.Numeric(precision=30, scale=8),
        existing_nullable=False,
    )
    op.alter_column(
        'candles', 'quote_volume',
        type_=sa.Numeric(precision=20, scale=8),
        existing_type=sa.Numeric(precision=30, scale=8),
        existing_nullable=True,
    )
