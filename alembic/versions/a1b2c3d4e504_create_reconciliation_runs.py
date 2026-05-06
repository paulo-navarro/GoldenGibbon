"""create reconciliation_runs table

Revision ID: a1b2c3d4e504
Revises: a1b2c3d4e503
Create Date: 2026-05-06 19:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e504'
down_revision: Union[str, None] = 'a1b2c3d4e503'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "reconciliation_runs",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("pairs_checked", sa.Integer, nullable=False, server_default="0"),
        sa.Column("mismatches", sa.Integer, nullable=False, server_default="0"),
        sa.Column("repairs", sa.Integer, nullable=False, server_default="0"),
        sa.Column("summary", JSONB, nullable=False, server_default="{}"),
        sa.Column("events", JSONB, nullable=False, server_default="[]"),
    )
    op.create_index(
        "ix_reconciliation_runs_started_at",
        "reconciliation_runs",
        ["started_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_reconciliation_runs_started_at", table_name="reconciliation_runs")
    op.drop_table("reconciliation_runs")
