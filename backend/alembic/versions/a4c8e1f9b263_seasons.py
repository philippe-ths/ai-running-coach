"""Seasons: the coach's read of every goal and its phase timeline (#1064).

Additive and backward-safe (previews share the production DB): a new table
nothing existing reads, and two nullable columns on `training_plans` that old
code never selects by name. `training_plans.season_id` is ON DELETE SET NULL so
removing a season never takes a plan with it.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a4c8e1f9b263"
down_revision: Union[str, None] = "e7b2c91d5a43"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "seasons",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("plan", sa.JSON(), nullable=True),
        sa.Column("goals_fingerprint", sa.String(), nullable=False),
        sa.Column("draft_log", sa.JSON(), nullable=True),
        sa.Column("model_id", sa.String(), nullable=True),
        sa.Column("failure_message", sa.Text(), nullable=True),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
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
    op.create_index(op.f("ix_seasons_user_id"), "seasons", ["user_id"], unique=False)
    op.create_index(op.f("ix_seasons_status"), "seasons", ["status"], unique=False)

    op.add_column("training_plans", sa.Column("season_id", sa.Uuid(), nullable=True))
    op.add_column("training_plans", sa.Column("draft_log", sa.JSON(), nullable=True))
    op.create_foreign_key(
        "fk_training_plans_season_id",
        "training_plans",
        "seasons",
        ["season_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_training_plans_season_id", "training_plans", type_="foreignkey")
    op.drop_column("training_plans", "draft_log")
    op.drop_column("training_plans", "season_id")
    op.drop_index(op.f("ix_seasons_status"), table_name="seasons")
    op.drop_index(op.f("ix_seasons_user_id"), table_name="seasons")
    op.drop_table("seasons")
