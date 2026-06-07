"""add coaching_context belief store (M8)

Revision ID: e8f3a9b1d4c2
Revises: d7e2b4a1c8f9
Create Date: 2026-06-07 09:00:00.000000

M8 belief-state write-back: coaching_context is the durable, user-scoped,
write-gated belief store. One row per (user, belief); identity (user_id, kind,
key) is enforced by a UNIQUE constraint so a concurrent write can never stack two
contradictory rows. This migration also adds the activities.beliefs_written_at
sentinel (at-most-once-per-activity belief write-back, like
coach_notification_sent_at).

Backward-safety (previews share the production DB, so this can run against prod
while prod still runs the old code): the new table is brand-new, references
nothing, and starts empty; the added activities column is nullable, so old-code
inserts that omit it still work. Both are clean to drop on downgrade.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e8f3a9b1d4c2"
down_revision: Union[str, None] = "d7e2b4a1c8f9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "coaching_context",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("key", sa.String(), nullable=False),
        sa.Column("value", sa.JSON(), nullable=False),
        sa.Column("confidence", sa.String(), nullable=False),
        sa.Column("observation_count", sa.Integer(), nullable=False),
        sa.Column("first_observed_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("last_reinforced_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "kind", "key", name="uq_coaching_context_identity"),
    )
    op.create_index(op.f("ix_coaching_context_user_id"), "coaching_context", ["user_id"], unique=False)
    op.add_column(
        "activities",
        sa.Column("beliefs_written_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("activities", "beliefs_written_at")
    op.drop_index(op.f("ix_coaching_context_user_id"), table_name="coaching_context")
    op.drop_table("coaching_context")
