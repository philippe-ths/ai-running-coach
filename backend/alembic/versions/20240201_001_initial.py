"""initial

Revision ID: 001
Revises: 
Create Date: 2024-02-01 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '001'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- Users ---
    op.create_table('users',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('email', sa.String(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('email')
    )

    # --- Strava Accounts ---
    op.create_table('strava_accounts',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('strava_athlete_id', sa.Integer(), nullable=False),
        sa.Column('access_token', sa.String(), nullable=False),
        sa.Column('refresh_token', sa.String(), nullable=False),
        sa.Column('expires_at', sa.Integer(), nullable=False),
        sa.Column('scope', sa.String(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('strava_athlete_id')
    )

    # --- User Profiles ---
    op.create_table('user_profiles',
        sa.Column('user_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('goal_type', sa.String(), nullable=False),
        sa.Column('target_date', sa.Date(), nullable=True),
        sa.Column('experience_level', sa.String(), nullable=False),
        sa.Column('weekly_days_available', sa.Integer(), nullable=False),
        sa.Column('injury_notes', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('user_id')
    )

    # --- Activities ---
    op.create_table('activities',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('strava_activity_id', sa.BigInteger(), nullable=False),
        sa.Column('start_date', sa.DateTime(timezone=True), nullable=False),
        sa.Column('type', sa.String(), nullable=False),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('distance_m', sa.Integer(), nullable=False),
        sa.Column('moving_time_s', sa.Integer(), nullable=False),
        sa.Column('elapsed_time_s', sa.Integer(), nullable=False),
        sa.Column('elev_gain_m', sa.Float(), nullable=False),
        sa.Column('avg_hr', sa.Float(), nullable=True),
        sa.Column('max_hr', sa.Float(), nullable=True),
        sa.Column('avg_cadence', sa.Float(), nullable=True),
        sa.Column('average_speed_mps', sa.Float(), nullable=True),
        sa.Column('raw_summary', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('is_deleted', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('strava_activity_id')
    )

    # --- Activity Streams ---
    op.create_table('activity_streams',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('activity_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('stream_type', sa.String(), nullable=False),
        sa.Column('data', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.ForeignKeyConstraint(['activity_id'], ['activities.id'], ),
        sa.PrimaryKeyConstraint('id')
    )

    # --- Derived Metrics ---
    op.create_table('derived_metrics',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('activity_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('activity_class', sa.String(), nullable=False),
        sa.Column('effort_score', sa.Float(), nullable=False),
        sa.Column('pace_variability', sa.Float(), nullable=True),
        sa.Column('hr_drift', sa.Float(), nullable=True),
        sa.Column('time_in_zones', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('flags', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('confidence', sa.String(), nullable=False),
        sa.Column('confidence_reasons', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['activity_id'], ['activities.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('activity_id')
    )

    # --- Advice ---
    op.create_table('advice',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('activity_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('verdict', sa.Text(), nullable=False),
        sa.Column('evidence', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('next_run', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('week_adjustment', sa.Text(), nullable=False),
        sa.Column('warnings', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('question', sa.Text(), nullable=True),
        sa.Column('full_text', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['activity_id'], ['activities.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('activity_id')
    )

    # --- Check Ins ---
    op.create_table('check_ins',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('activity_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('rpe', sa.Integer(), nullable=True),
        sa.Column('pain_score', sa.Integer(), nullable=True),
        sa.Column('pain_location', sa.String(), nullable=True),
        sa.Column('sleep_quality', sa.Integer(), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['activity_id'], ['activities.id'], ),
        sa.PrimaryKeyConstraint('id')
    )

def downgrade() -> None:
    op.drop_table('check_ins')
    op.drop_table('advice')
    op.drop_table('derived_metrics')
    op.drop_table('activity_streams')
    op.drop_table('activities')
    op.drop_table('user_profiles')
    op.drop_table('strava_accounts')
    op.drop_table('users')
