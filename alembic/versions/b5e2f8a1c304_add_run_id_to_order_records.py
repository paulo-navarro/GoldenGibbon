"""add run_id to order_records

Revision ID: b5e2f8a1c304
Revises: a3f1c7e9b042
Create Date: 2026-02-25 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'b5e2f8a1c304'
down_revision: Union[str, Sequence[str]] = 'a3f1c7e9b042'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add run_id FK to order_records, matching trade_records pattern."""
    op.add_column(
        'order_records',
        sa.Column('run_id', sa.String(length=100), nullable=True),
    )
    op.create_index(
        'ix_order_records_run_id', 'order_records', ['run_id'], unique=False,
    )
    op.create_foreign_key(
        'fk_order_records_run_id',
        'order_records', 'backtest_results',
        ['run_id'], ['run_id'],
        ondelete='SET NULL',
    )


def downgrade() -> None:
    """Remove run_id from order_records."""
    op.drop_constraint('fk_order_records_run_id', 'order_records', type_='foreignkey')
    op.drop_index('ix_order_records_run_id', table_name='order_records')
    op.drop_column('order_records', 'run_id')
