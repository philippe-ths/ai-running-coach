"""add processed-artifacts columns: derived_metrics.stream_view and coach_reports.digest

Revision ID: c1d2e3f4a5b6
Revises: e8f3a9b1d4c2
Create Date: 2026-06-10 13:30:00.000000

A2a processed-artifacts layer. Two derived-on-ingestion artifacts stored so
retrieval is cheap:
  - derived_metrics.stream_view: a small downsampled aligned HR/pace/grade/
    cadence snapshot, produced during analysis (re-derived every analysis).
  - coach_reports.digest: the token-bounded exchange digest (activity_date,
    headline, lead_argument, next-steps), produced at report write time for
    non-fallback reports.

Backward-safety (previews share the production DB, so this can run against prod
while prod still runs the old code): both columns are nullable, so old-code
INSERTs that omit them still work, and existing rows are simply left NULL.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c1d2e3f4a5b6"
down_revision: Union[str, None] = "e8f3a9b1d4c2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("derived_metrics", sa.Column("stream_view", sa.JSON(), nullable=True))
    op.add_column("coach_reports", sa.Column("digest", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("coach_reports", "digest")
    op.drop_column("derived_metrics", "stream_view")
