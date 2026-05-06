"""widen order_type column to 20 chars

Revision ID: a1b2c3d4e501
Revises: 60c1d49f6519
Create Date: 2026-05-06 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e501'
down_revision: Union[str, Sequence[str], None] = '60c1d49f6519'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        'order_records',
        'order_type',
        type_=sa.String(length=20),
        existing_type=sa.String(length=10),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        'order_records',
        'order_type',
        type_=sa.String(length=10),
        existing_type=sa.String(length=20),
        existing_nullable=False,
    )
