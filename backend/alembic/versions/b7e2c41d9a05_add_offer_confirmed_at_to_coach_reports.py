"""add offer_confirmed_at to coach_reports

Revision ID: b7e2c41d9a05
Revises: e6d3352b4a4a
Create Date: 2026-10-21 00:00:00.000000

A report's offer is spent once the runner confirms it (#956). Single use was a
property of the token alone, and every read of the report minted a fresh one, so
the card came back live. This records the spend beside the report rather than
inside its stored JSON, which stays a record of what the coach wrote.

Backward-safety (previews share the production DB, so this can run against prod
while prod still runs the old code): the column is nullable, so old-code INSERTs
that omit it still work and existing rows are left NULL, which reads as "not
confirmed".
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b7e2c41d9a05"
down_revision: Union[str, None] = "e6d3352b4a4a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "coach_reports",
        sa.Column("offer_confirmed_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("coach_reports", "offer_confirmed_at")
