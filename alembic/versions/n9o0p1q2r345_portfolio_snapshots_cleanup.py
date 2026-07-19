"""portfolio_snapshots: fix paper-as-live rows, dedup, enforce uniqueness

Task 9.11 cleanup (prod diagnosis 2026-07-08):

1. Three paper slices (``paper_smart_hodler_*``) were stored with
   ``trading_mode='live'``, contaminating live sums with fictitious money.
2. Whole-hour timestamps had duplicated slice rows (26 = 2x 13), doubling
   the equity curve. Deduplicate keeping the oldest row per
   ``(run_id, timestamp)``.
3. Add a unique index on ``(run_id, timestamp)`` so re-runs of the same
   tick can never double-write again (the writer now upserts).

Revision ID: n9o0p1q2r345
Revises: m8n9o0p1q234
Create Date: 2026-07-19 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'n9o0p1q2r345'
down_revision: Union[str, Sequence[str], None] = 'm8n9o0p1q234'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Paper slices recorded as live (pre-Phase 7 contamination)
    op.execute(
        "UPDATE portfolio_snapshots SET trading_mode = 'paper' "
        "WHERE trading_mode = 'live' AND run_id LIKE 'paper\\_%'"
    )

    # 2. Deduplicate (run_id, timestamp), keeping the oldest row
    op.execute(
        """
        DELETE FROM portfolio_snapshots a
        USING portfolio_snapshots b
        WHERE a.run_id = b.run_id
          AND a.timestamp = b.timestamp
          AND a.id > b.id
        """
    )

    # 3. Enforce one snapshot per (run_id, timestamp)
    op.create_index(
        'uq_portfolio_snapshots_run_ts',
        'portfolio_snapshots',
        ['run_id', 'timestamp'],
        unique=True,
    )


def downgrade() -> None:
    # Data fixes (1) and (2) are not reversible.
    op.drop_index('uq_portfolio_snapshots_run_ts', table_name='portfolio_snapshots')
