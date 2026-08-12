"""coach_message_lean_grouped_v9 (#830): the coach reads the runner's own plan.

The report already read the past and the present well; what it lacked was the
future. v9 is v8 plus ONE capability — `right_now.schedule` — and, in the prose,
plus exactly the one clause that capability carries.

This file states v9's own claims and touches no earlier version's test, which is
what keeps a lineage of prompts from turning into a lineage of test edits.

All row data is synthetic test setup (exercises code paths; represents no real
runner).
"""

from app.services.coach import prompt_clauses as clauses
from app.services.coach import prompts
from app.services.coach.prompt_features import PromptFeature as F
from app.services.coach.prompt_features import features_for
from app.services.coach.service import active_schema_version

V9 = "coach_message_lean_grouped_v9"
V8 = "coach_message_lean_grouped_v8"


def test_v9_adds_exactly_one_capability_to_v8():
    """One version, one capability. The pack v9 receives is v8's plus the plan."""
    added = features_for(V9) - features_for(V8)
    removed = features_for(V8) - features_for(V9)

    assert added == {F.SCHEDULE}
    assert removed == frozenset()


def test_v9_prose_is_v8_prose_plus_the_schedule_clause():
    """The prompt difference is one clause and nothing else.

    A version that quietly retuned other prose while adding a capability would
    make the flip two experiments at once, and a regression impossible to
    attribute.
    """
    v8 = prompts.build_system_prompt(V8, mode="fuller")
    v9 = prompts.build_system_prompt(V9, mode="fuller")

    added = [line for line in v9.splitlines() if line not in v8.splitlines()]

    assert len(added) == 1
    assert added[0] == clauses.SCHEDULE.text.strip()


def test_the_schedule_clause_says_a_plan_is_intent_not_a_scorecard():
    """The one thing this clause exists to prevent.

    Handed a plan and a result, a model reaches for a compliance verdict — "you
    missed two sessions" — which is exactly the nagging the runner-memory
    redesign (ADR 0025) was built to remove. The clause has to close that door
    explicitly, and it must do it in the coach's own voice rather than as a rule
    bolted on, or it reads as something to work around.
    """
    text = clauses.SCHEDULE.text

    assert "intent, not a record" in text
    assert "coach the gap rather than score it" in text
    assert "never a charge" in text


def test_the_schedule_clause_reaches_only_versions_served_the_plan():
    """Prose cannot name a signal its version does not receive.

    The clause is keyed on the capability, so a version without
    `PromptFeature.SCHEDULE` is never told it has a plan to read — the same
    derivation that keeps the body clause off versions with no `profile.body`.
    """
    assert clauses.SCHEDULE.text.strip() not in prompts.build_system_prompt(
        V8, mode="fuller"
    )
    assert clauses.SCHEDULE.text.strip() in prompts.build_system_prompt(
        V9, mode="fuller"
    )


def test_the_schedule_clause_is_fuller_only_like_every_disposition_clause():
    """The opener does not carry it, and that is the existing shape rather than an
    omission.

    The opener is a brief immediate reaction written moments after a run; it
    carries none of the disposition clauses (body, personalisation), and under
    the production receipt cadence there is no LLM opener at all — the receipt is
    deterministic. Reading the plan belongs to the turn that actually coaches.
    """
    opener = prompts.build_system_prompt(V9, mode="opener")

    assert clauses.SCHEDULE.text.strip() not in opener
    # Not a special case: the whole disposition set behaves this way.
    assert clauses.BODY.text.strip() not in opener
    assert clauses.PERSONALISATION.text.strip() not in opener


def test_v9_is_a_schedule_prompt_and_v8_is_not():
    assert prompts.is_schedule_prompt(V9) is True
    assert prompts.is_schedule_prompt(V8) is False


def test_v9_keeps_the_schema_version_and_the_two_stage_cadence():
    """A capability addition, not an output-shape change: the cache identity and
    the cadence are v8's, so a flip is a config change and a rollback is one too."""
    assert active_schema_version(V9) == active_schema_version(V8)
    assert F.TWO_STAGE in features_for(V9)
    assert F.GROUPED_PACK in features_for(V9)


def test_v9_is_registered_as_a_composed_prompt():
    assert V9 in clauses.COMPOSED_PROMPT_IDS
    assert V9 in prompts.PROMPT_VERSIONS
