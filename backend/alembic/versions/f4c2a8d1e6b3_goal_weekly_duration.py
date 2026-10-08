"""A goal can be a weekly time target: `goal_races.weekly_duration_s`.

"10h a week, October to December" had nowhere to put the 10 h but the goal's
name, so nothing could hold a plan to it. A nullable integer, seconds, like
`target_time_s`.

Backward-safe (previews share the production DB): an added nullable column
changes nothing the old code reads or writes. No backfill: the runner sets it.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f4c2a8d1e6b3"
down_revision: Union[str, None] = "e7b2c91d5a43"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "goal_races", sa.Column("weekly_duration_s", sa.Integer(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("goal_races", "weekly_duration_s")
