"""merge receipt-cadence and hr-zones heads

#305: two migrations were authored off the same parent (c3f8a1d6e9b2) and merged
to main in parallel — `f1a2b3c4d5e6` (receipt cadence, #299) and `d4e7f2a9c1b3`
(HR zones, #297 via #298) — leaving two alembic heads, which makes
`alembic upgrade head` fail at deploy ("Multiple head revisions are present") and
blocks the web service. This is a no-op merge revision that unifies the two heads
into one so `alembic upgrade head` works again; it adds no schema of its own.

Revision ID: b7e9d2c4a1f8
Revises: d4e7f2a9c1b3, f1a2b3c4d5e6
Create Date: 2026-06-16 22:29:11.962092

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b7e9d2c4a1f8'
down_revision: Union[str, None] = ('d4e7f2a9c1b3', 'f1a2b3c4d5e6')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
