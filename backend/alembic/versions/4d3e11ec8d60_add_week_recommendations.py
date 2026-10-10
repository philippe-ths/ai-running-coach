"""add week_recommendations

Revision ID: 4d3e11ec8d60
Revises: 6cd12c6235cb
Create Date: 2026-10-10 00:00:00.000000

The recommended week the runner was last shown (#1082), one row per runner per
week, so re-planning can keep sessions where they were and say what moved.

Backward-safety (previews share the production DB, so this can run against prod
while prod still runs the old code): a new table nothing old reads or writes.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "4d3e11ec8d60"
down_revision: Union[str, None] = "6cd12c6235cb"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "week_recommendations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("week_start", sa.Date(), nullable=False),
        sa.Column("plan_id", sa.Uuid(), nullable=True),
        sa.Column("assignment", sa.JSON(), nullable=False),
        sa.Column("changes", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "week_start", name="uq_week_recommendation_user_week"),
    )
    op.create_index(
        op.f("ix_week_recommendations_user_id"), "week_recommendations", ["user_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_week_recommendations_user_id"), table_name="week_recommendations")
    op.drop_table("week_recommendations")
