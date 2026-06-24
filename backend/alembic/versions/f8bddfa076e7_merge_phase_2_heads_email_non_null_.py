"""merge phase-2 heads: email non-null + telegram_chat_id

Revision ID: f8bddfa076e7
Revises: a9d4f2c7e1b6, c7f3a9e1b2d4
Create Date: 2026-06-24 18:54:50.074611

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f8bddfa076e7'
down_revision: Union[str, None] = ('a9d4f2c7e1b6', 'c7f3a9e1b2d4')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
