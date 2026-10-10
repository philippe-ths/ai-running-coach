"""add planned_sessions.activity_type

Revision ID: e6d3352b4a4a
Revises: 4d3e11ec8d60
Create Date: 2026-10-10 12:00:00.000000

The exact sport of a planned session in Strava's names (#1089), so the schedule
can show a swim as a swim rather than "other".

Backward-safety (previews share the production DB, so this can run against prod
while prod still runs the old code): one nullable column nothing old reads.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e6d3352b4a4a"
down_revision: Union[str, None] = "4d3e11ec8d60"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("planned_sessions", sa.Column("activity_type", sa.String(length=40), nullable=True))


def downgrade() -> None:
    op.drop_column("planned_sessions", "activity_type")
