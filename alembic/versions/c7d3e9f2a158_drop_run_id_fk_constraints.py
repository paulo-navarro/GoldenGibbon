"""drop run_id FK constraints for paper trading compatibility

Paper trading generates run_id values (e.g. paper_smart_hodler_BTCUSDT_...)
that don't exist in backtest_results.  The FK constraints block every
persist, so we drop them and keep run_id as a plain indexed string.

Revision ID: c7d3e9f2a158
Revises: b5e2f8a1c304
Create Date: 2026-03-08 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'c7d3e9f2a158'
down_revision: Union[str, Sequence[str]] = 'b5e2f8a1c304'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Drop FK constraints on run_id so paper-trading run_ids are accepted."""
    op.drop_constraint('fk_portfolio_snapshots_run_id', 'portfolio_snapshots', type_='foreignkey')
    op.drop_constraint('fk_trade_records_run_id', 'trade_records', type_='foreignkey')

    # order_records may or may not have the FK depending on migration history
    from alembic import context
    bind = op.get_bind()
    result = bind.execute(
        sa.text(
            "SELECT 1 FROM pg_constraint WHERE conname = 'fk_order_records_run_id'"
        )
    )
    if result.fetchone():
        op.drop_constraint('fk_order_records_run_id', 'order_records', type_='foreignkey')


def downgrade() -> None:
    """Re-add FK constraints on run_id → backtest_results.run_id."""
    op.create_foreign_key(
        'fk_portfolio_snapshots_run_id',
        'portfolio_snapshots', 'backtest_results',
        ['run_id'], ['run_id'],
        ondelete='SET NULL',
    )
    op.create_foreign_key(
        'fk_trade_records_run_id',
        'trade_records', 'backtest_results',
        ['run_id'], ['run_id'],
        ondelete='SET NULL',
    )
    op.create_foreign_key(
        'fk_order_records_run_id',
        'order_records', 'backtest_results',
        ['run_id'], ['run_id'],
        ondelete='SET NULL',
    )
