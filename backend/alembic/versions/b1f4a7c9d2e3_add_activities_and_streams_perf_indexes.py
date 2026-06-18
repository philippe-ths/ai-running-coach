"""add performance indexes on activities(user_id, start_date, is_deleted) and activity_streams(activity_id)

#357 / #358: two hot, frequently-scanned columns were never indexed (a Postgres FK
constraint does NOT create an index).

- `activities`: every per-runner history scan (coach context, readiness, trends,
  retrieval) filters `user_id` then orders by `start_date DESC` with a LIMIT, and
  almost always filters `is_deleted = false`. Without an index each is a full
  sequential scan. The composite `(user_id, start_date, is_deleted)` serves the
  user_id equality + start_date order pattern from one range scan, with is_deleted
  available in-index as a filter. user_id-leading is load-bearing for the imminent
  multi-user expansion (a cross-user seq-scan is the classic multi-tenant failure).
- `activity_streams`: rows are always fetched by `activity_id`, scanned on every
  analysis run, detail render, and (hundreds of times) on the backfill/reanalyze
  batch jobs.

Backward-safety (previews share the production DB): purely additive — two new
indexes, no column or table change — so old code's reads/writes are unaffected
while this runs against prod. The table is small at current single-user scale, so
a non-CONCURRENT CREATE INDEX is acceptable; revisit with postgresql_concurrently
if the tables grow large before this ships.

Revision ID: b1f4a7c9d2e3
Revises: b7e9d2c4a1f8
Create Date: 2026-06-18 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'b1f4a7c9d2e3'
down_revision: Union[str, None] = 'b7e9d2c4a1f8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        "ix_activities_user_start_deleted",
        "activities",
        ["user_id", "start_date", "is_deleted"],
    )
    op.create_index(
        "ix_activity_streams_activity_id",
        "activity_streams",
        ["activity_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_activity_streams_activity_id", table_name="activity_streams")
    op.drop_index("ix_activities_user_start_deleted", table_name="activities")
