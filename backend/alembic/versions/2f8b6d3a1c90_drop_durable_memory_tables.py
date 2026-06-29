"""drop durable-memory tables (coach memory M4, ADR 0025)

Retire the old durable-memory system replaced by the runner memory profile:
drop the `coaching_context` (M8 beliefs) and `coach_narratives` (A2c narrative)
tables and the `activities.beliefs_written_at` write-back sentinel.

DESTRUCTIVE: this drops two tables and a column. Owner decision (build plan
ADR 0025): drop WITHOUT export — the rows are retired/superseded and the raw
sources (coach reports, chat, check-ins) still hold the truth, so no backup or
dump step is needed. `downgrade` recreates the tables and column EMPTY (schema
only); it cannot restore dropped rows.

Revision ID: 2f8b6d3a1c90
Revises: 1b9c4a7e2f60
Create Date: 2026-06-29
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "2f8b6d3a1c90"
down_revision: Union[str, None] = "1b9c4a7e2f60"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_index(op.f("ix_coaching_context_user_id"), table_name="coaching_context")
    op.drop_table("coaching_context")
    op.drop_index("ix_coach_narratives_user_id", table_name="coach_narratives")
    op.drop_table("coach_narratives")
    op.drop_column("activities", "beliefs_written_at")


def downgrade() -> None:
    # Recreates the schema EMPTY (the dropped rows are not restored — see the
    # module docstring: the drop was intentional and export-free).
    op.add_column(
        "activities",
        sa.Column("beliefs_written_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        "coach_narratives",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("narrative", sa.Text(), nullable=True),
        sa.Column("model_id", sa.String(), nullable=True),
        sa.Column("source_report_count", sa.Integer(), nullable=True),
        sa.Column("grounded_through", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", name="uq_coach_narratives_user"),
    )
    op.create_index(
        "ix_coach_narratives_user_id", "coach_narratives", ["user_id"], unique=False
    )
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
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "kind", "key", name="uq_coaching_context_identity"),
    )
    op.create_index(
        op.f("ix_coaching_context_user_id"), "coaching_context", ["user_id"], unique=False
    )
