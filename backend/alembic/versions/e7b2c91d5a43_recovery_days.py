"""Daily recovery store: sleep, overnight HRV, resting HR (#555).

Additive and backward-safe: a new table nothing existing reads, so the old code
running against it during a deploy is unaffected (previews share the production
DB). Unique per (user, day, source) so the sync is an idempotent upsert.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e7b2c91d5a43"
down_revision: Union[str, None] = "d81f3a6c2e94"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "recovery_days",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("sleep_duration_s", sa.Integer(), nullable=True),
        sa.Column("sleep_score", sa.Integer(), nullable=True),
        sa.Column("hrv_avg_ms", sa.Float(), nullable=True),
        sa.Column("hrv_status", sa.String(), nullable=True),
        sa.Column("hrv_baseline_low_ms", sa.Float(), nullable=True),
        sa.Column("hrv_baseline_high_ms", sa.Float(), nullable=True),
        sa.Column("resting_hr", sa.Integer(), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id", "day", "source", name="uq_recovery_user_day_source"
        ),
    )
    op.create_index(
        op.f("ix_recovery_days_user_id"), "recovery_days", ["user_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_recovery_days_user_id"), table_name="recovery_days")
    op.drop_table("recovery_days")
