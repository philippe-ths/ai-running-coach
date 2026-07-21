"""coach_report non-destructive regeneration (superseded_at + partial unique index)

Revision ID: c3f8a1d0e527
Revises: b7d4e9f2a1c8
Create Date: 2026-07-21 00:00:00.000000

#646 non-destructive "Re-run": a force regeneration no longer overwrites the active
coach_reports row in place. It sets `superseded_at` on the prior current row (an
immutable audit copy of what the coach saw, including its point-in-time context_pack)
and inserts the regenerated report as a NEW current row.

"Current" = superseded_at IS NULL, so the cache uniqueness moves from a FULL unique
constraint on (activity_id, prompt_id, schema_version) to a PARTIAL unique index over
the same columns scoped to current rows — exactly one current row per key, unlimited
archived copies.

Backward-safety (previews share the production DB, so this can run against prod while
prod still runs the old code): the new column is nullable, so old-code INSERTs that
omit it still work. Existing rows get superseded_at = NULL, i.e. all become "current"
— correct, since each key currently has exactly one generation (the old full unique
constraint guaranteed no duplicates), so the partial index enforces the identical
uniqueness for the existing data. The old code path still does an in-place UPDATE of
the current row, which is fine under the partial index.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c3f8a1d0e527"
down_revision: Union[str, None] = "b7d4e9f2a1c8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_OLD_UNIQUE = "uq_coach_reports_activity_version"
_NEW_INDEX = "uq_coach_reports_activity_version_current"


def upgrade() -> None:
    op.add_column(
        "coach_reports",
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
    )
    # Swap the full unique constraint for a partial unique index over the current rows.
    op.drop_constraint(_OLD_UNIQUE, "coach_reports", type_="unique")
    op.create_index(
        _NEW_INDEX,
        "coach_reports",
        ["activity_id", "prompt_id", "schema_version"],
        unique=True,
        postgresql_where=sa.text("superseded_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index(_NEW_INDEX, table_name="coach_reports")
    op.create_unique_constraint(
        _OLD_UNIQUE,
        "coach_reports",
        ["activity_id", "prompt_id", "schema_version"],
    )
    op.drop_column("coach_reports", "superseded_at")
