"""add start_date_local on activities (#399)

The runner's local wall-clock start, sourced from Strava's start_date_local.
start_date stays UTC for ordering/windowing; this column is read only where a
wall-clock time or calendar day is shown (the coach's time-of-day, trends/load
day-bucketing). Nullable, with readers falling back to start_date.

Backfills every existing row from the stored raw_summary payload, which carries
start_date_local on every activity (the trailing Z is misleading — it is already
local time, so it is parsed naive).

Revision ID: c2d5e8f1a9b4
Revises: b1f4a7c9d2e3
Create Date: 2026-06-20 12:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c2d5e8f1a9b4'
down_revision: Union[str, None] = 'b1f4a7c9d2e3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'activities',
        sa.Column('start_date_local', sa.DateTime(timezone=False), nullable=True),
    )
    # Backfill from the stored Strava payload. The value looks like
    # "2026-06-20T09:00:18Z"; strip the misleading Z and cast to a naive timestamp
    # (it is already the runner's local wall clock). Rows missing the key keep NULL
    # and fall back to start_date at read time.
    op.execute(
        """
        UPDATE activities
        SET start_date_local =
            replace(raw_summary->>'start_date_local', 'Z', '')::timestamp
        WHERE raw_summary->>'start_date_local' IS NOT NULL
          AND start_date_local IS NULL
        """
    )


def downgrade() -> None:
    op.drop_column('activities', 'start_date_local')
