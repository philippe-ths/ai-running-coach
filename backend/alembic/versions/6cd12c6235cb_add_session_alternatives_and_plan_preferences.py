"""add session alternatives, done_option and plan preferences

Revision ID: 6cd12c6235cb
Revises: 1ec5c26bc1fc
Create Date: 2026-10-10 00:00:00.000000

The recommended week (#1082): a planned session may offer alternatives ("easy
run, or easy bike"), the session records which option was done, and a plan
carries the coach's preferences for arranging a flexible week.

Backward-safety (previews share the production DB, so this can run against prod
while prod still runs the old code): every column is nullable, so old-code
INSERTs that omit them still work and existing rows read as "no alternatives",
"option not recorded" and "no preferences".
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "6cd12c6235cb"
down_revision: Union[str, None] = "1ec5c26bc1fc"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("planned_sessions", sa.Column("alternatives", sa.JSON(), nullable=True))
    op.add_column("planned_sessions", sa.Column("done_option", sa.Integer(), nullable=True))
    op.add_column("training_plans", sa.Column("preferences", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("training_plans", "preferences")
    op.drop_column("planned_sessions", "done_option")
    op.drop_column("planned_sessions", "alternatives")
