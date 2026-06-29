"""add runner_memory table (coach memory M1, ADR 0025)

Revision ID: 1b9c4a7e2f60
Revises: f8bddfa076e7
Create Date: 2026-06-29 16:00:00.000000

Coach memory M1: the rewritten-from-source runner memory profile (ADR 0025), the
durable-memory replacement for the retired belief + narrative loop. One row per
user holds the whole `RunnerMemoryProfile` (five capped sections) as JSON plus
provenance (`model_id`, `source_report_count`, `grounded_through`).

Backward-safety (previews share the production DB, so this can run against prod
while prod still runs the old code): the table is brand new, references only the
existing users table, and starts empty, so creating it is inert until the writer
(M2) and reader (M3) ship and the v13 prompt is flipped. The `user_id` index is
UNIQUE (one memory row per user). The downgrade drops it cleanly (no other table
depends on it).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "1b9c4a7e2f60"
down_revision: Union[str, None] = "f8bddfa076e7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "runner_memory",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("profile", sa.JSON(), nullable=True),
        sa.Column("model_id", sa.String(), nullable=True),
        sa.Column("source_report_count", sa.Integer(), nullable=True),
        sa.Column("grounded_through", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_runner_memory_user_id"), "runner_memory", ["user_id"], unique=True
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_runner_memory_user_id"), table_name="runner_memory")
    op.drop_table("runner_memory")
