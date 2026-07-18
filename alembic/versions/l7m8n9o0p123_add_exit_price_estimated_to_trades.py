"""add exit_price_estimated to trade_records

Marks trades whose exit_price could not be resolved from the exchange and
was estimated (e.g. reconciliation force-close falling back to a ticker or
the entry price). PnL on such rows is not trustworthy for analysis.

Revision ID: l7m8n9o0p123
Revises: fb2f54f41350
Create Date: 2026-07-18 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'l7m8n9o0p123'
down_revision: Union[str, Sequence[str], None] = 'fb2f54f41350'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'trade_records',
        sa.Column(
            'exit_price_estimated',
            sa.Boolean(),
            nullable=False,
            server_default=sa.text('false'),
        ),
    )


def downgrade() -> None:
    op.drop_column('trade_records', 'exit_price_estimated')
