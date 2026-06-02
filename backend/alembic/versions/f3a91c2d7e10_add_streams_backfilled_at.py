"""add streams_backfilled_at on activities

Marks an activity as attempted by the historical stream-analysis backfill (#110),
so the backfill is resumable and converges (each summary-only activity attempted
exactly once).

Revision ID: f3a91c2d7e10
Revises: e2f7a3b9d4c1
Create Date: 2026-06-02 11:20:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'f3a91c2d7e10'
down_revision: Union[str, None] = 'e2f7a3b9d4c1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'activities',
        sa.Column('streams_backfilled_at', sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('activities', 'streams_backfilled_at')
