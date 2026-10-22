"""re-role app-written thread notes as notices

Revision ID: c3f8a2d6e1b7
Revises: b7e2c41d9a05
Create Date: 2026-10-21 00:00:00.000000

#1124: text the app writes into a thread in the coach's place is now stored with
role `notice`, which no model reads as either side speaking. Notes written
before that are `assistant` rows, so they still reached the model as the coach's
own words. This moves the two kinds whose facts reach the coach another way,
matched on the exact sentence their one writer opens with:

- the amendment landing note (`jobs/amend_schedule._say_it_landed`), whose change
  the coach reads from the confirmed ledger;
- the draft failure note (`jobs/generate_schedule._say_it_failed`), whose
  failure the coach reads from the draft state.

The amendment failure note stays `assistant`: nothing else tells the coach it
failed, so it remains the coach's own turn. Failed turns are left as they are;
`threads.said` already drops them by their text.

Backward-safety (previews share the production DB): old code filters every
model-facing reader to `user`/`assistant`, so it skips these rows as well, and
its transcript renders an unknown role as the coach. Downgrade turns every
notice back into an assistant row, which is what old code wrote.
"""
from typing import Sequence, Union

from alembic import op


revision: str = "c3f8a2d6e1b7"
down_revision: Union[str, None] = "b7e2c41d9a05"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE coach_chat_messages SET role = 'notice'
        WHERE role = 'assistant'
          AND (
            content LIKE 'That''s in — % written across % on your Schedule screen now. Everything else in your plan is as it was.'
            OR content LIKE 'That plan did not make it into your schedule. %'
          )
        """
    )


def downgrade() -> None:
    op.execute("UPDATE coach_chat_messages SET role = 'assistant' WHERE role = 'notice'")
