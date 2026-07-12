"""add coach_chat_messages.tools_used

#648 follow-up: persist the on-demand data tools the coach ran for an assistant
turn, so the chat UI can render a "looked up …" trace that survives a reload.
Nullable JSON list of tool names; existing rows read as null (no trace).

Revision ID: a3f1c9d7e208
Revises: 2f8b6d3a1c90
Create Date: 2026-07-12
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a3f1c9d7e208"
down_revision: Union[str, None] = "2f8b6d3a1c90"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "coach_chat_messages",
        sa.Column("tools_used", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("coach_chat_messages", "tools_used")
