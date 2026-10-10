"""add max_activities_per_day to user_profiles

Revision ID: 1ec5c26bc1fc
Revises: f3a9d27c6b18
Create Date: 2026-10-10 00:00:00.000000

The runner's own daily limit (#1080): the most activities they will do in a
day, walks included. Every plan the coach drafts or amends is held to it.

Backward-safety (previews share the production DB, so this can run against prod
while prod still runs the old code): the column is nullable, so old-code INSERTs
that omit it still work and existing rows are left NULL, which reads as "no
limit of their own".
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "1ec5c26bc1fc"
down_revision: Union[str, None] = "f3a9d27c6b18"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "user_profiles", sa.Column("max_activities_per_day", sa.Integer(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("user_profiles", "max_activities_per_day")
