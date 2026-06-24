"""make users.email non-null with placeholder backfill (Phase 2, ADR 0022)

Identity is now the verified email (social login via Clerk). The email column
becomes the durable identity key: non-null and unique. The unique constraint
already exists from the initial migration; this only backfills any NULL email to
a per-row unique placeholder and flips the column to NOT NULL.

The pre-Phase-2 single user (email NULL) is backfilled to
``legacy-<id>@placeholder.invalid``. It is reconciled to the owner's real Google
email on first sign-in by app/core/clerk_auth.resolve_user_by_email, which
adopts the placeholder row rather than stranding the owner's data behind a fresh
account.

Revision ID: a9d4f2c7e1b6
Revises: f9a3c1b7e2d5
Create Date: 2026-06-24
"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a9d4f2c7e1b6"
down_revision: Union[str, None] = "f9a3c1b7e2d5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Backfill any NULL email to a per-row unique placeholder so the NOT NULL
    # (and the existing unique) constraint holds. The cast keeps this portable
    # across the uuid PK type.
    op.execute(
        "UPDATE users "
        "SET email = 'legacy-' || id || '@placeholder.invalid' "
        "WHERE email IS NULL"
    )
    op.alter_column("users", "email", existing_type=sa.String(), nullable=False)


def downgrade() -> None:
    op.alter_column("users", "email", existing_type=sa.String(), nullable=True)
    # The placeholder backfill is intentionally not reverted: there is no
    # information about which rows were originally NULL, and a placeholder email
    # is harmless under the nullable column.
