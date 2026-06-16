"""add_receipt_cadence_columns

#296 receipt cadence (ADR 0010/0011 delta). Additive, all-nullable columns:

- exchanges.done_at: the runner's explicit "done" tap on a receipt.
- activities.receipt_sent_at: the per-activity instant-receipt dedup sentinel.
- coaching_relationship.receipt_templates (+ voice_key + generated_at): the
  pre-generated voiced receipt phrasings and their provenance.

Null resolves to the prior behaviour at read time (no receipt sent, no done tap,
house-default deterministic receipt copy), so this is a zero-backfill migration
with no behaviour change until COACH_RECEIPT_CADENCE is turned on.

Revision ID: f1a2b3c4d5e6
Revises: c3f8a1d6e9b2
Create Date: 2026-06-16 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f1a2b3c4d5e6'
down_revision: Union[str, None] = 'c3f8a1d6e9b2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('exchanges', sa.Column('done_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('activities', sa.Column('receipt_sent_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('coaching_relationship', sa.Column('receipt_templates', sa.JSON(), nullable=True))
    op.add_column('coaching_relationship', sa.Column('receipt_templates_voice_key', sa.String(), nullable=True))
    op.add_column(
        'coaching_relationship',
        sa.Column('receipt_templates_generated_at', sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('coaching_relationship', 'receipt_templates_generated_at')
    op.drop_column('coaching_relationship', 'receipt_templates_voice_key')
    op.drop_column('coaching_relationship', 'receipt_templates')
    op.drop_column('activities', 'receipt_sent_at')
    op.drop_column('exchanges', 'done_at')
