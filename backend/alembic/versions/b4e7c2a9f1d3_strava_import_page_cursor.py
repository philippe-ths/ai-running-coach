"""strava import: replace before-epoch cursor with a page cursor

The historical import (#239) paginated with a `before` epoch cursor set to the
oldest activity in each page. But Strava returns `/athlete/activities`
oldest-first when `after` is set, so the first page is the oldest 50 activities
and `before = oldest` excluded everything — the import stopped after one page.
Switch to page-number pagination (fixed `after`, advancing page), which walks
the whole window regardless of sort order.

Revision ID: b4e7c2a9f1d3
Revises: d9f1a2b3c4e5
Create Date: 2026-06-12 22:40:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b4e7c2a9f1d3'
down_revision: Union[str, None] = 'd9f1a2b3c4e5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'strava_imports',
        sa.Column('cursor_page', sa.Integer(), nullable=False, server_default='1'),
    )
    op.drop_column('strava_imports', 'cursor_before')


def downgrade() -> None:
    op.add_column(
        'strava_imports',
        sa.Column('cursor_before', sa.BigInteger(), nullable=True),
    )
    op.drop_column('strava_imports', 'cursor_page')
