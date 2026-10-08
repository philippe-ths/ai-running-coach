"""add stated_pbs to user_profiles

Revision ID: f3a9d27c6b18
Revises: a4c8e1f9b263
Create Date: 2026-10-08 00:00:00.000000

Stated personal bests (#1068): the PBs a runner tells us, for the ones our
records cannot see. Strava gives us best efforts only on runs we ingested in
detail, so an older PB on a summary-only run is invisible without this.

Backward-safety (previews share the production DB, so this can run against prod
while prod still runs the old code): the column is nullable, so old-code INSERTs
that omit it still work and existing rows are left NULL, which reads as "none
stated".
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f3a9d27c6b18"
down_revision: Union[str, None] = "a4c8e1f9b263"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("user_profiles", sa.Column("stated_pbs", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("user_profiles", "stated_pbs")
