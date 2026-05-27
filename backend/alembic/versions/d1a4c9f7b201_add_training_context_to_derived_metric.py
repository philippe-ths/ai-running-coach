"""add training_context to derived_metrics

Revision ID: d1a4c9f7b201
Revises: b88c115eb887
Create Date: 2026-05-27 16:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd1a4c9f7b201'
down_revision: Union[str, None] = 'b88c115eb887'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('derived_metrics', sa.Column('training_context', sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column('derived_metrics', 'training_context')
