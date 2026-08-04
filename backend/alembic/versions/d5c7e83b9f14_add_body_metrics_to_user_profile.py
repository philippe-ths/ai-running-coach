"""add weight_kg and height_cm to user_profiles

Revision ID: d5c7e83b9f14
Revises: c3f8a1d0e527
Create Date: 2026-08-04 00:00:00.000000

Body metrics (#742): the runner's build, which the coach could not see at all.
Before this, the only channel that reached the coach was the free-text
`injury_notes`, and an A/B probe under `coach_message_lean_grouped_v7` showed a
~109 kg note there produced a substantively identical report -- the "coach this
runner, not the median" disposition had no structured signal to act on.

Deliberately raw facts only. No derived index (BMI or otherwise) is stored or
computed anywhere: a ratio is exactly the "trade one median for another" trap,
and the coach is meant to adapt method to the build, not apply a population
formula to it.

Backward-safety (previews share the production DB, so this can run against prod
while prod still runs the old code): both columns are nullable, so old-code
INSERTs that omit them still work and existing rows are simply left NULL, which
reads as "not stated" and drops the pack signal entirely.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d5c7e83b9f14"
down_revision: Union[str, None] = "c3f8a1d0e527"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Float, not Integer: weight is meaningfully fractional (a runner who tracks
    # 78.4 kg should not be rounded), and height in cm is stored the same way for
    # symmetry rather than mixing units-of-precision across two sibling columns.
    op.add_column("user_profiles", sa.Column("weight_kg", sa.Float(), nullable=True))
    op.add_column("user_profiles", sa.Column("height_cm", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column("user_profiles", "height_cm")
    op.drop_column("user_profiles", "weight_kg")
