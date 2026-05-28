"""add coach_notification_sent_at on activities and is_fallback on coach_reports

Revision ID: e2f7a3b9d4c1
Revises: d1a4c9f7b201
Create Date: 2026-05-27 18:40:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'e2f7a3b9d4c1'
down_revision: Union[str, None] = 'd1a4c9f7b201'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'activities',
        sa.Column('coach_notification_sent_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        'coach_reports',
        sa.Column(
            'is_fallback',
            sa.Boolean(),
            nullable=False,
            server_default=sa.text('false'),
        ),
    )


def downgrade() -> None:
    op.drop_column('coach_reports', 'is_fallback')
    op.drop_column('activities', 'coach_notification_sent_at')
