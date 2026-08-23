"""Add the period_reports table (#946).

The coach could only ever be asked about one activity. This is the runner-facing
review of a STRETCH of training over disciplines the runner chooses, generated
asynchronously (a month-wide pack on a stronger model can exceed the gateway
timeout, the same reason `training_plans` drafts off the request cycle).

Deliberately not `coach_reports`: that table's cache identity is a non-nullable
FK to one activity baked into a partial unique index, and a period spans many
activities or none. This is its own table, referencing only `users` — no
existing table is altered, so this migration is inert against a still-old-code
production the way `b7d2e4f19a83` (the schedule tables) was.

No DB-level uniqueness constraint on the request identity
(user_id/period_start/period_end/disciplines_key/prompt_id/schema_version): held
by the writer in `services/coach/period_report_store.py`, the `TrainingPlan`
precedent, because a Postgres partial index is syntax the SQLite test database
cannot exercise.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a4f6d9c2e871"
down_revision: Union[str, None] = "a4c8e19d6f52"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "period_reports",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("disciplines", sa.JSON(), nullable=False),
        sa.Column("disciplines_key", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("prompt_id", sa.String(), nullable=False),
        sa.Column("schema_version", sa.String(), nullable=False),
        sa.Column("model_id", sa.String(), nullable=True),
        sa.Column("report", sa.JSON(), nullable=True),
        sa.Column("context_pack", sa.JSON(), nullable=True),
        sa.Column("meta", sa.JSON(), nullable=True),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_period_reports_user_id"), "period_reports", ["user_id"], unique=False
    )
    op.create_index(
        op.f("ix_period_reports_period_start"),
        "period_reports",
        ["period_start"],
        unique=False,
    )
    op.create_index(
        op.f("ix_period_reports_period_end"),
        "period_reports",
        ["period_end"],
        unique=False,
    )
    op.create_index(
        op.f("ix_period_reports_status"), "period_reports", ["status"], unique=False
    )
    op.create_index(
        "ix_period_reports_identity",
        "period_reports",
        ["user_id", "period_start", "period_end", "disciplines_key", "prompt_id", "schema_version"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_period_reports_identity", table_name="period_reports")
    op.drop_index(op.f("ix_period_reports_status"), table_name="period_reports")
    op.drop_index(op.f("ix_period_reports_period_end"), table_name="period_reports")
    op.drop_index(op.f("ix_period_reports_period_start"), table_name="period_reports")
    op.drop_index(op.f("ix_period_reports_user_id"), table_name="period_reports")
    op.drop_table("period_reports")
