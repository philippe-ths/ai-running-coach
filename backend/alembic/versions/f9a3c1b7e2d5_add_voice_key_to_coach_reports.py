"""add voice_key to coach_reports

Records the voice fingerprint a coach report was generated under (P1.1), so a later
voice change is detectable and the report can regenerate onto the new voice. Nullable
and additive: existing rows backfill to NULL (read as stale on the active voice-aware
prompt, so they regenerate once onto the current voice). Not part of the report's
unique cache identity — there is still one row per (activity_id, prompt_id,
schema_version); voice_key only tracks which voice that row currently speaks in.

Revision ID: f9a3c1b7e2d5
Revises: c2d5e8f1a9b4
Create Date: 2026-06-21
"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f9a3c1b7e2d5"
down_revision: Union[str, None] = "c2d5e8f1a9b4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "coach_reports",
        sa.Column("voice_key", sa.String(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("coach_reports", "voice_key")
