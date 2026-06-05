"""add bucketed_trends to runner_baselines

Revision ID: d7e2b4a1c8f9
Revises: c4d8e1f60a92
Create Date: 2026-06-05 13:00:00.000000

M2 trend substrate: runner_baselines.bucketed_trends holds the context-bucketed
per-run signal trends (efficiency factor, hr drift) keyed by
"{effort}|{terrain}|{temp_band}".

Backward-safety (previews share the production DB, so this can run against prod
while prod still runs the old code): the column is nullable, so old-code writes
that omit it still work, and the (currently empty) table needs no backfill.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d7e2b4a1c8f9"
down_revision: Union[str, None] = "c4d8e1f60a92"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("runner_baselines", sa.Column("bucketed_trends", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("runner_baselines", "bucketed_trends")
