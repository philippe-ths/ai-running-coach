"""add users.telegram_chat_id for per-user notification routing (P2.4, #120)

Each user can bind their own Telegram chat so coach-report notifications reach the
activity owner on their own channel (ADR 0023) instead of a single
deployment-wide TELEGRAM_CHAT_ID. Nullable: an unbound user falls back to the
configured global recipient (single-user back-compat) until they link.

Revision ID: c7f3a9e1b2d4
Revises: f9a3c1b7e2d5
Create Date: 2026-06-24
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c7f3a9e1b2d4"
down_revision: Union[str, None] = "f9a3c1b7e2d5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("telegram_chat_id", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "telegram_chat_id")
