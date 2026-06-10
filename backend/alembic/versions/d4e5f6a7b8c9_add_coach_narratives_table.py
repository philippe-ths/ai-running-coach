"""add coach_narratives table (A2c durable-memory narrative layer)

Revision ID: d4e5f6a7b8c9
Revises: c1d2e3f4a5b6
Create Date: 2026-06-10 16:00:00.000000

A2c splits durable memory into deterministic facts (already present: beliefs in
coaching_context, norms in runner_baselines) and the LLM narrative — the bounded
per-runner relationship story, voice only, re-grounded from the facts by a
background Consolidation job. This adds the narrative store: one row per user.

Backward-safety (previews share the production DB, so this can run against prod
while prod still runs the old code): the table is brand new and nothing in the
old code references it, so creating it is inert until the new code ships. The
downgrade drops it cleanly (no other table depends on it).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d4e5f6a7b8c9"
down_revision: Union[str, None] = "c1d2e3f4a5b6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "coach_narratives",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("narrative", sa.Text(), nullable=True),
        sa.Column("model_id", sa.String(), nullable=True),
        sa.Column("source_report_count", sa.Integer(), nullable=True),
        sa.Column("grounded_through", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", name="uq_coach_narratives_user"),
    )
    op.create_index(
        "ix_coach_narratives_user_id", "coach_narratives", ["user_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_coach_narratives_user_id", table_name="coach_narratives")
    op.drop_table("coach_narratives")
