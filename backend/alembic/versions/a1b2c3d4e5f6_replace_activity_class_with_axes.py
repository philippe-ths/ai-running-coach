"""replace activity_class with orthogonal classification axes

Revision ID: a1b2c3d4e5f6
Revises: f3a91c2d7e10
Create Date: 2026-06-02

Drops the single `activity_class` label on derived_metrics in favour of
independent classification axes (ADR 0007): effort, duration_class, structure,
is_hilly, is_race. Existing rows get NULL axes until re-analyzed; run the
`reanalyze_all` management command after upgrading to repopulate them.
"""
from alembic import op
import sqlalchemy as sa


revision = "a1b2c3d4e5f6"
down_revision = "f3a91c2d7e10"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("derived_metrics", sa.Column("effort", sa.String(), nullable=True))
    op.add_column("derived_metrics", sa.Column("duration_class", sa.String(), nullable=True))
    op.add_column("derived_metrics", sa.Column("structure", sa.String(), nullable=True))
    op.add_column("derived_metrics", sa.Column("is_hilly", sa.Boolean(), nullable=True))
    op.add_column("derived_metrics", sa.Column("is_race", sa.Boolean(), nullable=True))
    op.drop_column("derived_metrics", "activity_class")


def downgrade() -> None:
    op.add_column(
        "derived_metrics",
        sa.Column("activity_class", sa.String(), nullable=False, server_default="Easy Run"),
    )
    op.alter_column("derived_metrics", "activity_class", server_default=None)
    op.drop_column("derived_metrics", "is_race")
    op.drop_column("derived_metrics", "is_hilly")
    op.drop_column("derived_metrics", "structure")
    op.drop_column("derived_metrics", "duration_class")
    op.drop_column("derived_metrics", "effort")
