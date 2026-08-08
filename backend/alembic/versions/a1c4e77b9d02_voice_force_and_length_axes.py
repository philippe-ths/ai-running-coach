"""#822 voice axes: rename directness -> force, add length

The axis set moved from four tonal dials to five (warmth / humor / force / energy
/ length):

- `voice_directness` becomes `voice_force`. A pure RENAME, so every runner's
  stored setting carries over: the axis always measured how hard a verdict lands,
  and "directness" named clarity instead. Renaming rather than dropping-and-adding
  is what keeps a declared voice intact through the change.
- `voice_length` is new and starts null, which resolves to the balanced middle at
  read time. Nothing previously controlled how much room the coach takes.

Revision ID: a1c4e77b9d02
Revises: 14eca2b25785
"""

from alembic import op
import sqlalchemy as sa


revision = "a1c4e77b9d02"
down_revision = "14eca2b25785"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "coaching_relationship",
        "voice_directness",
        new_column_name="voice_force",
    )
    op.add_column(
        "coaching_relationship",
        sa.Column("voice_length", sa.SmallInteger(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("coaching_relationship", "voice_length")
    op.alter_column(
        "coaching_relationship",
        "voice_force",
        new_column_name="voice_directness",
    )
