"""backtest_results.run_id: drop unique (rows group per job)

Task 9.2: every backtest job persists one row per (strategy, symbol) sharing
the job's run_id, so the unique constraint must go. The index stays for the
grouping/lookup queries.

Revision ID: m8n9o0p1q234
Revises: l7m8n9o0p123
Create Date: 2026-07-18 15:00:00.000000

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'm8n9o0p1q234'
down_revision: Union[str, Sequence[str], None] = 'l7m8n9o0p123'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_index('ix_backtest_results_run_id', table_name='backtest_results')
    op.create_index(
        'ix_backtest_results_run_id', 'backtest_results', ['run_id'], unique=False
    )


def downgrade() -> None:
    op.drop_index('ix_backtest_results_run_id', table_name='backtest_results')
    op.create_index(
        'ix_backtest_results_run_id', 'backtest_results', ['run_id'], unique=True
    )
