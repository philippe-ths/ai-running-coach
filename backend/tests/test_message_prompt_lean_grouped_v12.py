"""coach_message_lean_grouped_v12 (#1032): a race, a PB, a record is what the session was.

The owner's goal half marathon was Strava's fastest-ever at eight distances. Nothing
told the coach it was a race or a personal best, so the report opened "1:44:34. Target
was sub-1:40. That's the verdict." v12 is v11 plus the `this_run.notable` section and
the NOTABLE clause that reads it: when a session stands out, the verdict starts from
what makes it stand out, against the runner's own history before any goal.

This file states v12's own claims and touches no earlier version's test.
"""

from app.services.coach import prompt_clauses as clauses
from app.services.coach import prompts
from app.services.coach.prompt_features import PromptFeature as F
from app.services.coach.prompt_features import features_for
from app.services.coach.service import active_schema_version

V12 = "coach_message_lean_grouped_v12"
V11 = "coach_message_lean_grouped_v11"


def test_v12_is_v11_plus_the_notable_capability():
    assert features_for(V12) == features_for(V11) | {F.NOTABLE}


def test_v12_prose_is_v11_prose_plus_the_notable_clause():
    """One clause added and nothing else retuned, so a flip to v12 is one experiment."""
    v11 = prompts.build_system_prompt(V11, mode="fuller")
    v12 = prompts.build_system_prompt(V12, mode="fuller")
    assert v12.replace(clauses.NOTABLE.text, "") == v11


def test_the_notable_clause_reaches_only_versions_served_the_section():
    assert "notable" in clauses.clause_names(V12)
    assert "notable" not in clauses.clause_names(V11)


def test_the_clause_names_the_section_it_reads():
    """A clause pointing at a field the pack does not carry instructs the coach to look
    for something it cannot see."""
    assert "`this_run.notable`" in clauses.NOTABLE.text


def test_v12_leaves_the_opener_exactly_as_v11_wrote_it():
    assert prompts.build_system_prompt(V12, mode="opener") == prompts.build_system_prompt(
        V11, mode="opener"
    )


def test_v12_carries_the_safety_floor_in_both_modes():
    assert clauses.SAFETY_FLOOR in clauses.fuller_clauses(V12)
    assert clauses.OPENER_SAFETY_FLOOR in clauses.opener_clauses(V12)


def test_v12_keeps_v11s_schema_version():
    assert active_schema_version(V12) == active_schema_version(V11)



def test_v12_is_registered_as_a_composed_prompt():
    assert V12 in clauses.COMPOSED_PROMPT_IDS
    assert V12 in prompts.PROMPT_VERSIONS
