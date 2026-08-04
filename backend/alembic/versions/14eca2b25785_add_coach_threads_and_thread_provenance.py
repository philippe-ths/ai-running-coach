"""add coach threads and thread provenance (#765, ADR 0027)

Creates `coach_threads` (the runner-initiated conversation unit), attaches
`coach_chat_messages` to it, adds the per-turn provenance columns
(`asked_from`, `skills_used`), relaxes `activity_id` to nullable (a thread turn
need not be anchored to an activity), and backfills: every activity that has
chat becomes ONE thread anchored to that activity, owned by the activity's
owner, its messages attributed in place. Existing rows get
`asked_from='activity'` — the activity chat box was the only chat surface that
has ever existed, so the label is a fact, not a guess.

`thread_id` stays nullable by design: a row written by pre-thread code during
the deploy window must not violate a constraint; the read path adopts orphans
(services/coach/threads.py), so the data converges.

Revision ID: 14eca2b25785
Revises: d5c7e83b9f14
Create Date: 2026-08-04 14:07:59.588492

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '14eca2b25785'
down_revision: Union[str, None] = 'd5c7e83b9f14'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "coach_threads",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("activity_id", sa.Uuid(), nullable=True),
        sa.Column("title", sa.String(length=200), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("last_message_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["activity_id"], ["activities.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_coach_threads_user_id"), "coach_threads", ["user_id"], unique=False
    )
    op.create_index(
        op.f("ix_coach_threads_activity_id"),
        "coach_threads",
        ["activity_id"],
        unique=False,
    )

    op.add_column(
        "coach_chat_messages", sa.Column("thread_id", sa.Uuid(), nullable=True)
    )
    op.add_column(
        "coach_chat_messages",
        sa.Column("asked_from", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "coach_chat_messages", sa.Column("skills_used", sa.JSON(), nullable=True)
    )
    op.create_index(
        op.f("ix_coach_chat_messages_thread_id"),
        "coach_chat_messages",
        ["thread_id"],
        unique=False,
    )
    op.create_foreign_key(
        "fk_coach_chat_messages_thread_id",
        "coach_chat_messages",
        "coach_threads",
        ["thread_id"],
        ["id"],
    )
    op.alter_column(
        "coach_chat_messages", "activity_id", existing_type=sa.Uuid(), nullable=True
    )

    # Backfill (the Migrate step, ADR 0027): one thread per activity-with-chat,
    # owned by the activity's owner, spanning its messages' timestamps.
    op.execute(
        """
        INSERT INTO coach_threads (id, user_id, activity_id, title, created_at, last_message_at)
        SELECT gen_random_uuid(), a.user_id, m.activity_id, NULL,
               MIN(m.created_at), MAX(m.created_at)
        FROM coach_chat_messages m
        JOIN activities a ON a.id = m.activity_id
        GROUP BY m.activity_id, a.user_id
        """
    )
    op.execute(
        """
        UPDATE coach_chat_messages m
        SET thread_id = t.id,
            asked_from = 'activity'
        FROM coach_threads t
        WHERE t.activity_id = m.activity_id
          AND m.thread_id IS NULL
        """
    )


def downgrade() -> None:
    # Local verification convenience only: prod never downgrades. Restoring
    # activity_id to NOT NULL is safe here because this slice's write path
    # always dual-writes it; thread-only rows (later slices) would block the
    # downgrade loudly rather than lose data silently.
    op.drop_constraint(
        "fk_coach_chat_messages_thread_id", "coach_chat_messages", type_="foreignkey"
    )
    op.drop_index(
        op.f("ix_coach_chat_messages_thread_id"), table_name="coach_chat_messages"
    )
    op.drop_column("coach_chat_messages", "skills_used")
    op.drop_column("coach_chat_messages", "asked_from")
    op.drop_column("coach_chat_messages", "thread_id")
    op.alter_column(
        "coach_chat_messages", "activity_id", existing_type=sa.Uuid(), nullable=False
    )
    op.drop_index(op.f("ix_coach_threads_activity_id"), table_name="coach_threads")
    op.drop_index(op.f("ix_coach_threads_user_id"), table_name="coach_threads")
    op.drop_table("coach_threads")
