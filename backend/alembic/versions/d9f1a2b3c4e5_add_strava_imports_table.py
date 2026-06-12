"""add strava_imports table

State for a resumable historical Strava import (#239): a self-pacing background
job walks the runner's Strava history backward in time, ingesting activity
summaries only (no streams, no AI coach analysis, no notifications). The row
tracks the resume cursor and progress.

Revision ID: d9f1a2b3c4e5
Revises: c4d5e6f7a8b9
Create Date: 2026-06-12 21:50:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd9f1a2b3c4e5'
down_revision: Union[str, None] = 'c4d5e6f7a8b9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'strava_imports',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('user_id', sa.Uuid(), nullable=False),
        sa.Column('since_date', sa.Date(), nullable=False),
        sa.Column('status', sa.String(), nullable=False),
        sa.Column('cursor_before', sa.BigInteger(), nullable=True),
        sa.Column('activities_imported', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('error', sa.String(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_strava_imports_user_id', 'strava_imports', ['user_id'])


def downgrade() -> None:
    op.drop_index('ix_strava_imports_user_id', table_name='strava_imports')
    op.drop_table('strava_imports')
