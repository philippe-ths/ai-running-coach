"""add hr_zones to user_profiles (Strava-sourced HR zones, #297)

Revision ID: d4e7f2a9c1b3
Revises: c3f8a1d6e9b2
Create Date: 2026-06-16 20:30:00.000000

#297: the app's time-in-zone used a generic %-of-max-HR scheme that did not line
up with the runner's Strava (or watch) zones. We now store the runner's own HR
zone lower bounds, pulled from their Strava athlete zones, and bin against those.

`hr_zones` is the list of 5 ascending lower bounds (bpm); `hr_zones_source`
records provenance (currently "strava"). Both are nullable: a profile with no
synced zones reads null and analysis falls back to the %max scheme, so this is
backward-safe. Previews share the production DB, so the columns are additive and
nullable, inert until the new code writes them.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d4e7f2a9c1b3"
down_revision: Union[str, None] = "c3f8a1d6e9b2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("user_profiles", sa.Column("hr_zones", sa.JSON(), nullable=True))
    op.add_column(
        "user_profiles", sa.Column("hr_zones_source", sa.String(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("user_profiles", "hr_zones_source")
    op.drop_column("user_profiles", "hr_zones")
