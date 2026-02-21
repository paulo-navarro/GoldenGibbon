"""add run_id and buy_hold columns

Revision ID: a3f1c7e9b042
Revises: 2d84a25288a5
Create Date: 2026-02-20 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'a3f1c7e9b042'
down_revision: Union[str, Sequence[str]] = '2d84a25288a5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add run_id FK to trade_records/portfolio_snapshots, buy_hold cols to backtest_results."""
    # ── backtest_results: buy-hold columns ───────────────────────
    op.add_column(
        'backtest_results',
        sa.Column('buy_hold_return', sa.Numeric(precision=10, scale=4), nullable=True),
    )
    op.add_column(
        'backtest_results',
        sa.Column('buy_hold_vs_strategy', sa.Numeric(precision=10, scale=4), nullable=True),
    )

    # ── trade_records: run_id column ─────────────────────────────
    op.add_column(
        'trade_records',
        sa.Column('run_id', sa.String(length=100), nullable=True),
    )
    op.create_index(
        'ix_trade_records_run_id', 'trade_records', ['run_id'], unique=False,
    )
    op.create_foreign_key(
        'fk_trade_records_run_id',
        'trade_records', 'backtest_results',
        ['run_id'], ['run_id'],
        ondelete='SET NULL',
    )

    # ── portfolio_snapshots: run_id column ───────────────────────
    op.add_column(
        'portfolio_snapshots',
        sa.Column('run_id', sa.String(length=100), nullable=True),
    )
    op.create_index(
        'ix_portfolio_snapshots_run_id', 'portfolio_snapshots', ['run_id'], unique=False,
    )
    op.create_foreign_key(
        'fk_portfolio_snapshots_run_id',
        'portfolio_snapshots', 'backtest_results',
        ['run_id'], ['run_id'],
        ondelete='SET NULL',
    )


def downgrade() -> None:
    """Remove run_id and buy_hold columns."""
    # ── portfolio_snapshots ──────────────────────────────────────
    op.drop_constraint('fk_portfolio_snapshots_run_id', 'portfolio_snapshots', type_='foreignkey')
    op.drop_index('ix_portfolio_snapshots_run_id', table_name='portfolio_snapshots')
    op.drop_column('portfolio_snapshots', 'run_id')

    # ── trade_records ────────────────────────────────────────────
    op.drop_constraint('fk_trade_records_run_id', 'trade_records', type_='foreignkey')
    op.drop_index('ix_trade_records_run_id', table_name='trade_records')
    op.drop_column('trade_records', 'run_id')

    # ── backtest_results ─────────────────────────────────────────
    op.drop_column('backtest_results', 'buy_hold_vs_strategy')
    op.drop_column('backtest_results', 'buy_hold_return')
