"""add discount_signals to derived_metrics and stimulant_use to user_profiles

Revision ID: c4d8e1f60a92
Revises: b2c1f9a47d33
Create Date: 2026-06-05 12:30:00.000000

N4 confounder stage: derived_metrics.discount_signals holds the pipeline-computed
templated annotation; user_profiles.stimulant_use is the opt-in physiology flag
the stage reads.

Backward-safety (previews share the production DB, so this can run against prod
while prod still runs the old code): both columns are nullable, so old-code
INSERTs that omit them still work, and existing rows are simply left NULL.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c4d8e1f60a92"
down_revision: Union[str, None] = "b2c1f9a47d33"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("derived_metrics", sa.Column("discount_signals", sa.JSON(), nullable=True))
    op.add_column("user_profiles", sa.Column("stimulant_use", sa.Boolean(), nullable=True))


def downgrade() -> None:
    op.drop_column("user_profiles", "stimulant_use")
    op.drop_column("derived_metrics", "discount_signals")
