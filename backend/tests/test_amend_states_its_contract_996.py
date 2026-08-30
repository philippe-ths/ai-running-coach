"""#996: a rule the gate enforces is a rule the prompt states.

`plan_validator` applies ONE session gate to both the draft and an amendment,
but only the draft's prompt taught the rules behind it. Amending inherited the
gate without the instructions, so the model wrote sessions a coach would say out
loud and the gate rejected them on rules it had never been given:

    "session 'Rest or easy walk' gives no distance, duration or rep structure"
    "session 'Long Run 16.5 km': window 2026-09-06..2026-09-07 crosses a week boundary"
    "detail: String should have at most 400 characters"

Each cost a whole generation, and under #995's ceiling there is usually only one.
Measured against the real plan, stating the contract took a one-week amendment
from 0/2 first-pass successes to 3/3.

The guard is on the CLASS, not the three instances: the shared blocks must reach
both prompts. A fourth rule added to the draft's copy then reaches the amendment
for free, which is the property that was missing.
"""

import pytest

from app.services.schedule import amend, draft
from app.services.schedule.draft_contract import (
    DETAIL_MAX_LENGTH,
    SESSION_PROPERTIES,
    DraftedSession,
)


# --- the shared blocks reach both prompts ------------------------------------


@pytest.mark.parametrize(
    "block", [draft.PLACING_AND_COMMITTING, draft.WRITING_A_SESSION]
)
def test_both_prompts_carry_the_shared_contract_blocks(block):
    assert block.strip(), "a shared block that is empty would pass every check below"
    assert block in draft._SYSTEM_PROMPT
    assert block in amend._SYSTEM_PROMPT, (
        "the amendment prompt must state the same session contract the draft "
        "does, because plan_validator holds both to it (#996)"
    )


def test_the_blocks_carry_the_rules_that_actually_failed():
    """Named so a future rewrite cannot quietly drop the ones that cost a plan."""
    both = draft.PLACING_AND_COMMITTING + draft.WRITING_A_SESSION
    assert "stay INSIDE one week" in both        # the Sunday-into-Monday window
    assert "A rest day is REST" in both          # 'Rest or easy walk' with no target
    assert "needs enough to size it" in both     # the sizing gate
    assert "ADD UP" in both                      # warm-up and cool-down in metres


# --- the detail cap is stated where it is enforced ---------------------------


def test_the_detail_cap_is_in_the_schema_the_model_reads():
    """It was enforced by Pydantic and absent from the tool schema, so the model
    walked off an edge it could not see."""
    assert SESSION_PROPERTIES["detail"]["maxLength"] == DETAIL_MAX_LENGTH
    assert str(DETAIL_MAX_LENGTH) in SESSION_PROPERTIES["detail"]["description"]


def test_the_schema_cap_and_the_model_cap_cannot_drift():
    constraints = DraftedSession.model_fields["detail"].metadata
    caps = [getattr(c, "max_length", None) for c in constraints]
    assert DETAIL_MAX_LENGTH in caps, (
        f"DraftedSession.detail enforces {caps}, the schema advertises "
        f"{DETAIL_MAX_LENGTH}; a model told one limit and judged by another "
        "loses the whole amendment to one field"
    )


# --- the guard is not vacuous ------------------------------------------------


def test_the_guard_fails_when_a_block_is_dropped_from_the_amend_prompt(monkeypatch):
    """Prove it bites: this is exactly the state the code shipped in."""
    monkeypatch.setattr(
        amend, "_SYSTEM_PROMPT", amend._SYSTEM_PROMPT.replace(draft.WRITING_A_SESSION, "")
    )
    with pytest.raises(AssertionError, match="same session contract"):
        test_both_prompts_carry_the_shared_contract_blocks(draft.WRITING_A_SESSION)
