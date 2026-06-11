"""add activities.opener_notification_sent_at (A4 two-stage Exchange opener sentinel)

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-06-11 14:30:00.000000

A4 two-stage Exchange (ADR 0010). The post-activity exchange now has two stages,
each with its own at-most-once notification sentinel on the activity row: the
existing `coach_notification_sent_at` becomes the FULLER-turn sentinel, and this
migration adds `opener_notification_sent_at` for the immediate opener.

The opener prose itself rides the existing `coach_reports.report` JSON (the schema
2.0 CoachMessageReport gained an `opener_message` field), so no coach_reports
column is needed — this migration carries only the new activity sentinel.

Backward-safety (previews share the production DB, so this can run against prod
while prod still runs the old code): the column is nullable, so old-code INSERTs
that omit it still work, and existing rows are simply left NULL.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e5f6a7b8c9d0"
down_revision: Union[str, None] = "d4e5f6a7b8c9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "activities",
        sa.Column("opener_notification_sent_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("activities", "opener_notification_sent_at")
