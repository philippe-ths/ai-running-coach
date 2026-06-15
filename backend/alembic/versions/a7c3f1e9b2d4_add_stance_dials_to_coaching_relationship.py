"""add_stance_dials_to_coaching_relationship

P1.3 Coaching stance (ADR 0015). Additive, all-nullable columns on the thin
coaching_relationship singleton: the selected school (a corpus.SCHOOLS key the
coach reasons from) and the two emphasis axes (1-5: Data 1 - Sentiment 5,
Process 1 - Outcome 5). Null resolves to the default school (aerobic-base) +
balanced emphasis at read time, so this is a zero-backfill migration with no
behaviour change until a runner declares a stance. Mirrors the P1.1 voice-dials
migration one layer down.

Revision ID: a7c3f1e9b2d4
Revises: e1f2a3b4c5d6
Create Date: 2026-06-15 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a7c3f1e9b2d4'
down_revision: Union[str, None] = 'e1f2a3b4c5d6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('coaching_relationship', sa.Column('stance_school', sa.String(), nullable=True))
    op.add_column('coaching_relationship', sa.Column('stance_data_sentiment', sa.SmallInteger(), nullable=True))
    op.add_column('coaching_relationship', sa.Column('stance_process_outcome', sa.SmallInteger(), nullable=True))


def downgrade() -> None:
    op.drop_column('coaching_relationship', 'stance_process_outcome')
    op.drop_column('coaching_relationship', 'stance_data_sentiment')
    op.drop_column('coaching_relationship', 'stance_school')
