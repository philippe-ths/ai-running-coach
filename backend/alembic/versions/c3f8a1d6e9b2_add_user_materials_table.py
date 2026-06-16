"""add user_materials table (P4 user materials, #285, ADR 0017)

Revision ID: c3f8a1d6e9b2
Revises: 5f60bc938518
Create Date: 2026-06-16 12:00:00.000000

P4 introduces `User materials`: runner-uploaded markdown coaching content, the
first genuinely untrusted-input surface. One uploaded file is one row. The row
keeps the untrusted `raw_text` (source of truth, pull-on-demand, never placed in
a prompt) and a `distilled` corpus-`School`-shaped record produced on ingestion
by a structured-output-only Haiku call (containment, not detection — ADR 0017).
`status` is the ingestion lifecycle (processing -> active | failed; archived =
soft-hide), `content_hash` (sha256 of raw_text) dedups identical re-uploads and
detects content change, and `distill_model`/`distilled_at` record provenance.

Backward-safety (previews share the production DB, so this can run against prod
while prod still runs the old code): the table is brand new, references only the
existing users table, and starts empty, so creating it is inert until the new
code ships. The downgrade drops it cleanly (no other table depends on it).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c3f8a1d6e9b2"
down_revision: Union[str, None] = "5f60bc938518"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "user_materials",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("filename", sa.String(), nullable=False),
        sa.Column("raw_text", sa.Text(), nullable=False),
        sa.Column("distilled", sa.JSON(), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("content_hash", sa.String(), nullable=False),
        sa.Column("distill_model", sa.String(), nullable=True),
        sa.Column("distilled_at", sa.DateTime(timezone=True), nullable=True),
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
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_user_materials_user_id"), "user_materials", ["user_id"], unique=False
    )
    op.create_index(
        op.f("ix_user_materials_content_hash"),
        "user_materials",
        ["content_hash"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_user_materials_content_hash"), table_name="user_materials")
    op.drop_index(op.f("ix_user_materials_user_id"), table_name="user_materials")
    op.drop_table("user_materials")
