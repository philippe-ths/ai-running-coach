"""add login_tokens and sessions tables

Backs the email magic-link identity layer (ADR 0005, #118): single-use login
tokens and server-side sessions. Additive only. `users.email` already carries a
unique constraint and stays nullable here; the non-null + placeholder backfill
lands with the basic-auth cutover (PR-B).

Revision ID: a1b2c3d4e5f6
Revises: f3a91c2d7e10
Create Date: 2026-06-02 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, None] = 'f3a91c2d7e10'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'login_tokens',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('token_hash', sa.String(), nullable=False),
        sa.Column('email', sa.String(), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('used_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_login_tokens_token_hash', 'login_tokens', ['token_hash'], unique=True)
    op.create_index('ix_login_tokens_email', 'login_tokens', ['email'], unique=False)

    op.create_table(
        'sessions',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('user_id', sa.Uuid(), nullable=False),
        sa.Column('token_hash', sa.String(), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_sessions_token_hash', 'sessions', ['token_hash'], unique=True)
    op.create_index('ix_sessions_user_id', 'sessions', ['user_id'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_sessions_user_id', table_name='sessions')
    op.drop_index('ix_sessions_token_hash', table_name='sessions')
    op.drop_table('sessions')
    op.drop_index('ix_login_tokens_email', table_name='login_tokens')
    op.drop_index('ix_login_tokens_token_hash', table_name='login_tokens')
    op.drop_table('login_tokens')
