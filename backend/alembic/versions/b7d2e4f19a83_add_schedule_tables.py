"""Add the schedule tables: goal_races, training_plans, planned_sessions (#830).

The product has been entirely retrospective: every surface answers "what did I do
and what did it mean". These three tables are the first that answer "what am I
doing next". A `TrainingPlan` holds the horizon's week shapes and the spacing
rules the week must satisfy; `PlannedSession` rows are the concrete sessions; a
`GoalRace` is the dated, distanced anchor the horizon's phases and taper are
measured back from (`UserProfile.upcoming_races` is an untyped JSON blob that
nothing reads, and is left alone here).

Backward-safety (previews share the production DB, so this can run against prod
while prod still runs the old code): all three tables are brand new and start
empty, and they reference only the existing `users` and `activities` tables —
neither of which is altered. No column is added to, removed from, or retyped on
any existing table, and no existing query can see these tables at all. Running
this migration against production while production still serves the pre-schedule
code is therefore completely inert.

The completion columns on `planned_sessions` (`completed_at`,
`completed_activity_id`, `completion_source`) are created here but written by a
later slice. They are declared now so the feature costs one migration rather than
two; an unwritten nullable column is inert.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b7d2e4f19a83"
down_revision: Union[str, None] = "a1c4e77b9d02"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "goal_races",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("race_date", sa.Date(), nullable=False),
        sa.Column("distance_m", sa.Float(), nullable=False),
        sa.Column("priority", sa.String(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_goal_races_user_id"), "goal_races", ["user_id"], unique=False
    )
    op.create_index(
        op.f("ix_goal_races_race_date"), "goal_races", ["race_date"], unique=False
    )

    op.create_table(
        "training_plans",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("goal_race_id", sa.Uuid(), nullable=True),
        sa.Column("horizon_end", sa.Date(), nullable=True),
        sa.Column("rules", sa.JSON(), nullable=False),
        sa.Column("week_shapes", sa.JSON(), nullable=False),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("model_id", sa.String(), nullable=True),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["goal_race_id"], ["goal_races.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_training_plans_user_id"), "training_plans", ["user_id"], unique=False
    )
    op.create_index(
        op.f("ix_training_plans_status"), "training_plans", ["status"], unique=False
    )

    op.create_table(
        "planned_sessions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("plan_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("window_start", sa.Date(), nullable=False),
        sa.Column("window_end", sa.Date(), nullable=False),
        sa.Column("intent", sa.String(), nullable=False),
        sa.Column("discipline", sa.String(), nullable=False),
        sa.Column("commitment", sa.String(), nullable=False),
        sa.Column("title", sa.String(length=120), nullable=False),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("target_distance_m", sa.Float(), nullable=True),
        sa.Column("target_duration_s", sa.Integer(), nullable=True),
        sa.Column("target_effort_score", sa.Float(), nullable=True),
        sa.Column("structure", sa.JSON(), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_activity_id", sa.Uuid(), nullable=True),
        sa.Column("completion_source", sa.String(), nullable=True),
        sa.Column("dismissed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.ForeignKeyConstraint(["plan_id"], ["training_plans.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["completed_activity_id"], ["activities.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_planned_sessions_plan_id"),
        "planned_sessions",
        ["plan_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_planned_sessions_user_id"),
        "planned_sessions",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_planned_sessions_window_start"),
        "planned_sessions",
        ["window_start"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_planned_sessions_window_start"), table_name="planned_sessions"
    )
    op.drop_index(op.f("ix_planned_sessions_user_id"), table_name="planned_sessions")
    op.drop_index(op.f("ix_planned_sessions_plan_id"), table_name="planned_sessions")
    op.drop_table("planned_sessions")

    op.drop_index(op.f("ix_training_plans_status"), table_name="training_plans")
    op.drop_index(op.f("ix_training_plans_user_id"), table_name="training_plans")
    op.drop_table("training_plans")

    op.drop_index(op.f("ix_goal_races_race_date"), table_name="goal_races")
    op.drop_index(op.f("ix_goal_races_user_id"), table_name="goal_races")
    op.drop_table("goal_races")
