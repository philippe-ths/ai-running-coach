"""Add training_plans.failure_kind (#859).

A draft that the coherence gate rejects leaves a `failed` row and nothing else,
so every failure — a plan that ramps far past what the runner's history supports,
an off-contract answer, an unreachable provider, an exhausted allowance — reaches
the runner as one generic sentence they cannot act on. This column records WHICH
kind of failure it was, as a category from the closed vocabulary in
`services/schedule/store.py`, so the API can serve a sentence with a next step in
it.

A category, never the gate's own text. The failure prose is written to be fed
back into a rewrite prompt and would leak the machinery the moment it was
displayed; the whole point of a category is that it is chosen deliberately and
maps to wording written for a runner.

Backward-safety (previews share the production DB, so this can run against prod
while prod still runs the old code): one nullable column with no server default,
on a table only the schedule reads. Existing rows keep a null, which reads as
"we did not record why", and pre-#859 code never selects the column at all (an
ORM model without it issues an explicit column list, not `SELECT *`). Running
this against production while production serves the old code is inert.

No backfill. Null is the truth for every row written before this migration:
guessing a category for a past failure would put invented advice in front of a
runner.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e2b9c4d7a138"
down_revision: Union[str, None] = "c3e8a4f21b70"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "training_plans",
        sa.Column("failure_kind", sa.String(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("training_plans", "failure_kind")
