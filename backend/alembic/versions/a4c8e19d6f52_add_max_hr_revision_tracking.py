"""add max HR revision anti-nag bookkeeping to user_profiles

Revision ID: a4c8e19d6f52
Revises: e2b9c4d7a138
Create Date: 2026-08-23 00:00:00.000000

#945: a durable profile fact (max HR) is set once and never revisited, even as
the runner's own training produces evidence it has been overtaken. The coach
may now OFFER a `revise_max_hr` proposed action when its deterministic
detector (app/services/coach/max_hr_calibration.py) finds more than one recent
activity with a recorded peak meaningfully above the stated max.

These two columns are bookkeeping for that offer's anti-nag rule ONLY -- never
a fact about the runner: the value/timestamp of the last such offer actually
put in front of the runner, so the detector does not re-raise the same
evidence on every turn (a materially higher peak arriving later still
re-raises; a cooldown lifts the suppression otherwise). Both nullable, so old-
code INSERTs that omit them still work and existing rows read as "never
offered", which is the correct default.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a4c8e19d6f52"
down_revision: Union[str, None] = "e2b9c4d7a138"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "user_profiles",
        sa.Column("max_hr_revision_last_surfaced_value", sa.Float(), nullable=True),
    )
    op.add_column(
        "user_profiles",
        sa.Column(
            "max_hr_revision_last_surfaced_at", sa.DateTime(timezone=True), nullable=True
        ),
    )


def downgrade() -> None:
    op.drop_column("user_profiles", "max_hr_revision_last_surfaced_at")
    op.drop_column("user_profiles", "max_hr_revision_last_surfaced_value")
