"""coach_message_lean_grouped_v11 (#943): never invent a forward number.

A runner's schedule screen showed an 18.00 km long run for next week. Asked about
it, the coach reached past this week's boundary on its own — good coaching — and,
finding no real figure in front of it there, said "next week's 16.5km": a number
nobody had written down. Two things were true at once: `right_now.schedule` only
ever carried THIS week, so the pack held nothing to check the claim against, and
nothing in the prompt said a forward reach has to stay honest when the pack comes
up empty.

v11 is the prose half of the fix (the pack half — next week's committed sessions
now riding `right_now.schedule` for every schedule-aware prompt, this one
included — needs no new capability and so no new manifest feature). It swaps the
SCHEDULE clause for SCHEDULE_V2, which adds one sentence: when I name a session
that has not happened yet, the numbers I give it are the ones in front of me.

This file states v11's own claims and touches no earlier version's test.
"""

from app.services.coach import prompt_clauses as clauses
from app.services.coach import prompts
from app.services.coach.prompt_features import PromptFeature as F
from app.services.coach.prompt_features import features_for
from app.services.coach.service import active_schema_version

V11 = "coach_message_lean_grouped_v11"
V10 = "coach_message_lean_grouped_v10"


def test_v11_adds_no_new_capability():
    """The pack v11 receives is v10's, unchanged. Next week's committed sessions
    ride the existing SCHEDULE feature, not a new one — every schedule-aware
    prompt gets the fact; only v11 gets the instruction not to invent past it."""
    assert features_for(V11) == features_for(V10)


def test_v11_prose_is_v10_prose_with_the_schedule_clause_swapped():
    """The prompt difference is one clause swapped for its sibling, and nothing
    else — a version that quietly retuned other prose while fixing #943 would
    make the flip two experiments at once."""
    v10 = prompts.build_system_prompt(V10, mode="fuller")
    v11 = prompts.build_system_prompt(V11, mode="fuller")

    assert v11 != v10
    assert v11.replace(clauses.SCHEDULE_V2.text, clauses.SCHEDULE.text) == v10


def test_the_schedule_v2_clause_carries_v9s_prose_verbatim_plus_one_sentence():
    """A relocation-free addition: the original clause's text survives byte for
    byte, with the forward-numbers sentence appended rather than the bullet
    rewritten around it."""
    assert clauses.SCHEDULE.text.strip() in clauses.SCHEDULE_V2.text
    added = clauses.SCHEDULE_V2.text.replace(clauses.SCHEDULE.text.strip(), "")
    assert "the numbers I give it are the ones in front of me" in added
    assert "if the plan does not show me a distance for that session" in added
    assert "I talk about it without inventing one" in added


def test_v10_keeps_the_original_schedule_clause_untouched():
    """v9 and v10 keep composing SCHEDULE, byte for byte — the swap costs them
    nothing, which is what makes v11 a pure addition rather than an edit to a
    clause two live prompts already carry."""
    assert clauses.SCHEDULE.text.strip() in prompts.build_system_prompt(V10, mode="fuller")
    assert clauses.SCHEDULE_V2.text.strip() not in prompts.build_system_prompt(
        V10, mode="fuller"
    )
    assert "schedule" in clauses.clause_names(V10)
    assert "schedule_v2" not in clauses.clause_names(V10)


def test_v11_carries_the_schedule_v2_clause_and_not_the_original():
    """SCHEDULE_V2's text starts with SCHEDULE's verbatim (it is an addition, not a
    rewrite), so the two composed prompts cannot be told apart by substring alone —
    the clause SET is the real claim, checked by name."""
    assert "schedule_v2" in clauses.clause_names(V11)
    assert "schedule" not in clauses.clause_names(V11)
    assert clauses.SCHEDULE_V2.text.strip() in prompts.build_system_prompt(V11, mode="fuller")
    # The one sentence v11 adds is not in v10's prompt at all.
    added_sentence = "the numbers I give it are the ones in front of me"
    assert added_sentence in prompts.build_system_prompt(V11, mode="fuller")
    assert added_sentence not in prompts.build_system_prompt(V10, mode="fuller")


def test_the_schedule_v2_clause_reaches_only_versions_served_the_plan():
    """Same derivation as the original clause: keyed on PromptFeature.SCHEDULE, so
    a version not served the plan is never told about the forward-numbers rule."""
    assert F.SCHEDULE in features_for(V11)
    v11_fuller = prompts.build_system_prompt(V11, mode="fuller")
    assert clauses.SCHEDULE_V2.text.strip() in v11_fuller


def test_the_schedule_v2_clause_is_fuller_only():
    """The opener is a brief immediate reaction and carries no disposition
    clauses at all — the existing shape, not a v11 omission."""
    opener = prompts.build_system_prompt(V11, mode="opener")
    assert clauses.SCHEDULE_V2.text.strip() not in opener
    assert clauses.SCHEDULE.text.strip() not in opener


def test_v11_leaves_the_opener_exactly_as_v10_wrote_it():
    assert prompts.build_system_prompt(V11, mode="opener") == prompts.build_system_prompt(
        V10, mode="opener"
    )


def test_v11_carries_the_safety_floor_in_both_modes():
    """Structural, not remembered: `compose` refuses a clause set with no floor."""
    assert clauses.SAFETY_FLOOR in clauses.fuller_clauses(V11)
    assert clauses.OPENER_SAFETY_FLOOR in clauses.opener_clauses(V11)
    assert clauses.SAFETY_FLOOR.text in prompts.build_system_prompt(V11, mode="fuller")
    assert clauses.OPENER_SAFETY_FLOOR.text in prompts.build_system_prompt(V11, mode="opener")


def test_v11_keeps_v10s_capabilities_the_schema_version_and_the_cadence():
    assert features_for(V11) == features_for(V10)
    assert active_schema_version(V11) == active_schema_version(V10)
    assert F.TWO_STAGE in features_for(V11)
    assert F.GROUPED_PACK in features_for(V11)
    assert F.SCHEDULE in features_for(V11)
    assert F.BODY in features_for(V11)


def test_v11_ships_inert():
    from app.core.config import settings

    assert settings.COACH_PROMPT_ID != V11


def test_v11_is_registered_as_a_composed_prompt():
    assert V11 in clauses.COMPOSED_PROMPT_IDS
    assert V11 in prompts.PROMPT_VERSIONS
