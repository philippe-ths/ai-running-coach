"""version-aware coach_reports cache: (activity_id, prompt_id, schema_version)

Revision ID: b2c1f9a47d33
Revises: a1b2c3d4e5f6
Create Date: 2026-06-05 11:20:00.000000

Cache identity moves from unique(activity_id) to
unique(activity_id, prompt_id, schema_version) so a report-shape change retains
prior versions instead of overwriting them.

Backward-safety (previews share the production DB, so this migration can run
against prod while prod still runs the *old* code):
  - The new columns are nullable, so old-code INSERTs that omit them still work.
  - Existing rows are backfilled from the meta JSON, which already carries
    prompt_id and schema_version, so no report is lost or needlessly regenerated.
  - The old code's read (filter by activity_id .first()) and force (delete by
    activity_id .first()) do not error against the composite-keyed table and
    never corrupt retained history. They are not fully equivalent, though: once
    new code has written a second version row for an activity, an old-code reader
    may transiently return a prior-version (stale-shape) row during the deploy
    window. This is accepted as a short, self-healing window, not a data hazard.

This migration was verified by hand against a populated local Postgres
(upgrade backfills both columns from meta, downgrade/upgrade roundtrips cleanly);
the SQLite-based test suite builds the schema from the ORM model, so it exercises
the composite constraint but not this migration's backfill/constraint-swap SQL.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b2c1f9a47d33"
down_revision: Union[str, None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_OLD_UNIQUE = "coach_reports_activity_id_key"  # Postgres default name for unique(activity_id)
_NEW_UNIQUE = "uq_coach_reports_activity_version"


def upgrade() -> None:
    op.add_column("coach_reports", sa.Column("prompt_id", sa.String(), nullable=True))
    op.add_column("coach_reports", sa.Column("schema_version", sa.String(), nullable=True))

    # Backfill from the meta JSON, which already stores both values.
    op.execute(
        "UPDATE coach_reports "
        "SET prompt_id = meta->>'prompt_id', "
        "    schema_version = meta->>'schema_version' "
        "WHERE prompt_id IS NULL OR schema_version IS NULL"
    )

    op.drop_constraint(_OLD_UNIQUE, "coach_reports", type_="unique")
    op.create_unique_constraint(
        _NEW_UNIQUE, "coach_reports", ["activity_id", "prompt_id", "schema_version"]
    )


def downgrade() -> None:
    op.drop_constraint(_NEW_UNIQUE, "coach_reports", type_="unique")
    # Restoring unique(activity_id) requires one row per activity. Collapse any
    # retained version rows to the most recent per activity first (downgrade is
    # lossy: prior-version history is dropped). ctid breaks created_at ties so
    # exactly one row survives per activity.
    op.execute(
        "DELETE FROM coach_reports a USING coach_reports b "
        "WHERE a.activity_id = b.activity_id "
        "AND (a.created_at < b.created_at "
        "     OR (a.created_at = b.created_at AND a.ctid < b.ctid))"
    )
    op.create_unique_constraint(_OLD_UNIQUE, "coach_reports", ["activity_id"])
    op.drop_column("coach_reports", "schema_version")
    op.drop_column("coach_reports", "prompt_id")
