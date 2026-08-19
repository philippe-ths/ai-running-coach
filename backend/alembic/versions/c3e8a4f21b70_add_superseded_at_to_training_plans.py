"""Add training_plans.superseded_at (#857).

When the coach writes a new plan the old one is retained, but nothing recorded
WHEN it stopped being the runner's plan, so "the plan I was training to before
this one" could only be inferred from a proxy, and every available proxy gives
the wrong answer once a plan has been restored. This column records the
transition itself.

Backward-safety (previews share the production DB, so this can run against prod
while prod still runs the old code): one nullable column with no server default
on a table only the schedule reads. Existing rows keep a null, which the reader
sorts last, and pre-#857 code never selects the column at all (an ORM model
without it issues an explicit column list, not `SELECT *`). Running this against
production while production serves the old code is inert.

No backfill. A null here means "we did not record when this stopped being
current", which is the truth for every row written before this migration; the
alternative would be inventing a timestamp and then ordering by it.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c3e8a4f21b70"
down_revision: Union[str, None] = "b7d2e4f19a83"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "training_plans",
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("training_plans", "superseded_at")
