"""add_voice_dials_to_coaching_relationship

P1.1 Voice dials (ADR 0012/0013). Additive, all-nullable columns on the thin
coaching_relationship singleton: the voice preset, the four operable dial values
(1-5), and the free-text escape-hatch. Null resolves to the moderate default at
read time, so this is a zero-backfill migration with no behaviour change until a
runner declares a voice.

Revision ID: e1f2a3b4c5d6
Revises: b4e7c2a9f1d3
Create Date: 2026-06-14 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e1f2a3b4c5d6'
down_revision: Union[str, None] = 'b4e7c2a9f1d3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('coaching_relationship', sa.Column('voice_preset', sa.String(), nullable=True))
    op.add_column('coaching_relationship', sa.Column('voice_warmth', sa.SmallInteger(), nullable=True))
    op.add_column('coaching_relationship', sa.Column('voice_humor', sa.SmallInteger(), nullable=True))
    op.add_column('coaching_relationship', sa.Column('voice_directness', sa.SmallInteger(), nullable=True))
    op.add_column('coaching_relationship', sa.Column('voice_energy', sa.SmallInteger(), nullable=True))
    op.add_column('coaching_relationship', sa.Column('voice_freetext', sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column('coaching_relationship', 'voice_freetext')
    op.drop_column('coaching_relationship', 'voice_energy')
    op.drop_column('coaching_relationship', 'voice_directness')
    op.drop_column('coaching_relationship', 'voice_humor')
    op.drop_column('coaching_relationship', 'voice_warmth')
    op.drop_column('coaching_relationship', 'voice_preset')
