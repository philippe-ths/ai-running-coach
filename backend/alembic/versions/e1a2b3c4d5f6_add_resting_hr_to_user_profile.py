"""add resting_hr to user_profiles

Revision ID: e1a2b3c4d5f6
Revises: a3f1c9d7e208
Create Date: 2026-07-17 00:00:00.000000

Manual resting HR (bpm) is the interim data source for the referral red-flag set
(#555), ahead of a device integration. The coach referral layer abstains while
it is null; #166's sustained-rise detection activates once a trend source lands.

Backward-safety (previews share the production DB, so this can run against prod
while prod still runs the old code): the column is nullable, so old-code INSERTs
that omit it still work and existing rows are simply left NULL.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e1a2b3c4d5f6"
down_revision: Union[str, None] = "a3f1c9d7e208"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("user_profiles", sa.Column("resting_hr", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("user_profiles", "resting_hr")
