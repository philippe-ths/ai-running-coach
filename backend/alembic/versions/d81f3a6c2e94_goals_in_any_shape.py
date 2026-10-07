"""Goals in any shape: optional date and distance, a window, a target, notes, booked (#1042).

A goal used to need an exact date and a distance, so "half marathon ~ March" or
"10h a week through December" could not be entered at all. `race_date` and
`distance_m` become nullable, and the goal gains a `window_start`/`window_end`
for an approximate date, `target_time_s`, the runner's `notes` for the coach, and
`booked`.

Backward-safety (previews share the production DB, so this can run against prod
while prod still runs the old code): relaxing NOT NULL and adding nullable
columns are both invisible to the old code until new code writes a row that uses
them. `booked` carries a server default so existing inserts keep working.

No backfill beyond that default: every existing row has an exact date and a
distance, and whether it was booked was never recorded, so it reads as not booked.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d81f3a6c2e94"
down_revision: Union[str, None] = "a4f6d9c2e871"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column("goal_races", "race_date", existing_type=sa.Date(), nullable=True)
    op.alter_column("goal_races", "distance_m", existing_type=sa.Float(), nullable=True)
    op.add_column("goal_races", sa.Column("window_start", sa.Date(), nullable=True))
    op.add_column("goal_races", sa.Column("window_end", sa.Date(), nullable=True))
    op.add_column("goal_races", sa.Column("target_time_s", sa.Integer(), nullable=True))
    op.add_column("goal_races", sa.Column("notes", sa.Text(), nullable=True))
    op.add_column(
        "goal_races",
        sa.Column("booked", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("goal_races", "booked")
    op.drop_column("goal_races", "notes")
    op.drop_column("goal_races", "target_time_s")
    op.drop_column("goal_races", "window_end")
    op.drop_column("goal_races", "window_start")
    # A goal without a date or distance has no form in the old shape, and
    # deleting it would lose what the runner told us. Refuse rather than guess.
    undatable = op.get_bind().execute(
        sa.text("SELECT count(*) FROM goal_races WHERE race_date IS NULL OR distance_m IS NULL")
    ).scalar()
    if undatable:
        raise RuntimeError(
            f"{undatable} goal(s) have no exact date or distance; the old schema cannot hold them"
        )
    op.alter_column("goal_races", "distance_m", existing_type=sa.Float(), nullable=False)
    op.alter_column("goal_races", "race_date", existing_type=sa.Date(), nullable=False)
