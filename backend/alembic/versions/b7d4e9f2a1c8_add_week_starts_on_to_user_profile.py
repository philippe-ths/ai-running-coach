"""add week_starts_on to user_profiles

Revision ID: b7d4e9f2a1c8
Revises: e1a2b3c4d5f6
Create Date: 2026-07-17 00:00:00.000000

Per-runner week start (#676): the day the runner's week begins, in Python
weekday() space (0=Monday, 6=Sunday). Null resolves to Monday, so every coach
pack and Trends surface keeps byte-identical "this week" framing for existing
runners; a runner may choose Sunday to shift the boundary.

Backward-safety (previews share the production DB, so this can run against prod
while prod still runs the old code): the column is nullable, so old-code INSERTs
that omit it still work and existing rows are simply left NULL (= Monday).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b7d4e9f2a1c8"
down_revision: Union[str, None] = "e1a2b3c4d5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("user_profiles", sa.Column("week_starts_on", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("user_profiles", "week_starts_on")
