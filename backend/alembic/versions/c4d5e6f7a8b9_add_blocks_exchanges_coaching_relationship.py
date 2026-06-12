"""add blocks, exchanges, coaching_relationship + backfill (A1, ADR 0011)

Revision ID: c4d5e6f7a8b9
Revises: e5f6a7b8c9d0
Create Date: 2026-06-12 10:00:00.000000

A1 (issue #235): the Block becomes the event unit the coach speaks about.

Schema: `blocks` (deterministic time-gap grouping, primary activity, split/merge
audit bit), `exchanges` (one per block strictly — block_id UNIQUE — owning the
two-stage lifecycle sentinels A4 parked on `Activity`), `coaching_relationship`
(thin one-row-per-user anchor), and `activities.block_id` (nullable FK, indexed).

Backfill: every historical analysed, non-deleted activity (one with a
`derived_metrics` row) becomes a block-of-one with a CLOSED exchange, so reads
never special-case pre-A1 rows and the block-complete trigger can never pick up
history. Closed means `fuller_sent_at` is non-null: the real Activity sentinels
are copied where present, and an activity that was never notified (pre-
notification era, fallback reports) falls back to its own `start_date` — a
deterministic, obviously-historical timestamp rather than an invented delivery
time. The `Activity` sentinel columns stop being written by A1 code but are NOT
dropped here (separate, owner-approved delete).

Backward-safety (previews share the production DB): purely additive — new
tables plus one nullable activities column, so old code's INSERTs and reads are
unaffected while this runs against prod.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c4d5e6f7a8b9"
down_revision: Union[str, None] = "e5f6a7b8c9d0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "blocks",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("start_date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_date", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "primary_activity_id",
            sa.Uuid(),
            sa.ForeignKey("activities.id"),
            nullable=False,
        ),
        sa.Column("user_corrected", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_blocks_user_id", "blocks", ["user_id"])
    op.create_index("ix_blocks_primary_activity_id", "blocks", ["primary_activity_id"])

    op.create_table(
        "exchanges",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "block_id", sa.Uuid(), sa.ForeignKey("blocks.id"), nullable=False, unique=True
        ),
        sa.Column("opener_sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("fuller_sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_exchanges_user_id", "exchanges", ["user_id"])

    op.create_table(
        "coaching_relationship",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False, unique=True
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )

    op.add_column("activities", sa.Column("block_id", sa.Uuid(), nullable=True))
    op.create_index("ix_activities_block_id", "activities", ["block_id"])
    op.create_foreign_key(
        "fk_activities_block_id_blocks", "activities", "blocks", ["block_id"], ["id"]
    )

    # Backfill: block-of-one + closed exchange per analysed, non-deleted activity.
    op.execute(
        """
        INSERT INTO blocks (id, user_id, start_date, end_date, primary_activity_id, user_corrected, created_at)
        SELECT gen_random_uuid(), a.user_id, a.start_date,
               a.start_date + make_interval(secs => a.elapsed_time_s),
               a.id, false, now()
        FROM activities a
        WHERE a.is_deleted = false
          AND EXISTS (SELECT 1 FROM derived_metrics dm WHERE dm.activity_id = a.id)
        """
    )
    op.execute(
        """
        UPDATE activities SET block_id = b.id
        FROM blocks b
        WHERE b.primary_activity_id = activities.id
        """
    )
    op.execute(
        """
        INSERT INTO exchanges (id, user_id, block_id, opener_sent_at, fuller_sent_at, created_at)
        SELECT gen_random_uuid(), b.user_id, b.id,
               COALESCE(a.opener_notification_sent_at, a.coach_notification_sent_at, a.start_date),
               COALESCE(a.coach_notification_sent_at, a.start_date),
               now()
        FROM blocks b
        JOIN activities a ON a.id = b.primary_activity_id
        """
    )


def downgrade() -> None:
    op.drop_constraint("fk_activities_block_id_blocks", "activities", type_="foreignkey")
    op.drop_index("ix_activities_block_id", table_name="activities")
    op.drop_column("activities", "block_id")
    op.drop_table("coaching_relationship")
    op.drop_index("ix_exchanges_user_id", table_name="exchanges")
    op.drop_table("exchanges")
    op.drop_index("ix_blocks_primary_activity_id", table_name="blocks")
    op.drop_index("ix_blocks_user_id", table_name="blocks")
    op.drop_table("blocks")
