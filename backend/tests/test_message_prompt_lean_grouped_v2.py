"""coach_message_lean_grouped_v2 and _v3 (ADR 0026 Slices 2 and 3).

Each redefines what a context group HOLDS rather than what the coach is told to do with
it, so each takes a different group-orientation clause and leaves everything else alone.
The orientation is read off the feature manifest, so a version's prose cannot name a
group content it is not actually served.
"""

from app.services.coach import prompt_clauses as clauses
from app.services.coach import prompts
from app.services.coach.prompt_features import PromptFeature as F
from app.services.coach.prompt_features import features_for
from app.services.coach.prompts import (
    MESSAGE_PROMPT_PREFIX,
    PROMPT_VERSIONS,
    SYSTEM_PROMPT_MESSAGE_LEAN_V1,
    SYSTEM_PROMPT_MESSAGE_LEAN_V1_OPENER,
    _OPENER_PROMPTS,
    is_grouped_pack_prompt,
)
from app.services.coach.service import active_schema_version
from tests.test_message_prompt_lean_grouped import (
    regrouped_from_lean_v1,
    regrouped_opener_from_lean_v1,
)

GROUPED3 = "coach_message_lean_grouped_v3"
GROUPED2 = "coach_message_lean_grouped_v2"
GROUPED1 = "coach_message_lean_grouped_v1"
LEAN = "coach_message_lean_v1"


def test_grouped_v2_registered_and_flagged_grouped():
    assert GROUPED2 in PROMPT_VERSIONS
    assert GROUPED2 in _OPENER_PROMPTS
    assert GROUPED2.startswith(MESSAGE_PROMPT_PREFIX)
    assert active_schema_version(GROUPED2) == active_schema_version(LEAN)
    assert is_grouped_pack_prompt(GROUPED2)


def test_grouped_v2_swaps_the_redefined_features():
    """It carries every lean_v1 capability EXCEPT the ones ADR 0026 Slice 2 redefines:
    right_now's training_load/volume/recent_training -> readiness/recent_weeks, and (PR 2)
    the_runner's training_history -> training_history_2wk (the rebased/enriched ladder). A
    deliberate non-superset — the redefined sections carry their new features INSTEAD."""
    expected = (
        features_for(LEAN)
        - {F.TRAINING_LOAD, F.VOLUME, F.RECENT_TRAINING, F.TRAINING_HISTORY}
        # #800: GROUPED_PACK is the serialization SHAPE flag every grouped prompt
        # carries (relocated from a hand-maintained set in prompts.py into the
        # manifest); it is not one of the redefined CONTENT capabilities.
    ) | {F.READINESS, F.RECENT_WEEKS, F.TRAINING_HISTORY_2WK, F.GROUPED_PACK}
    assert features_for(GROUPED2) == expected


def test_grouped_v2_is_lean_v1_plus_the_v2_orientation_and_continuity_repath_only():
    """The byte pin: composed grouped_v2 reproduces lean_v1's prose with only the v2
    orientation added and the continuity paths re-anchored, so every safety-critical line
    is unchanged."""
    assert PROMPT_VERSIONS[GROUPED2] == regrouped_from_lean_v1(
        clauses.GROUP_ORIENTATION_V2.text
    )
    reverted = (
        PROMPT_VERSIONS[GROUPED2]
        .replace(clauses.GROUP_ORIENTATION_V2.text, "")
        .replace("our_thread.continuity", "continuity")
    )
    assert reverted == SYSTEM_PROMPT_MESSAGE_LEAN_V1


def test_grouped_v2_opener_is_lean_v1_opener_plus_orientation_only():
    assert _OPENER_PROMPTS[GROUPED2] == regrouped_opener_from_lean_v1()
    reverted = _OPENER_PROMPTS[GROUPED2].replace(
        clauses.OPENER_GROUP_ORIENTATION.text, ""
    )
    assert reverted == SYSTEM_PROMPT_MESSAGE_LEAN_V1_OPENER


def test_grouped_v2_takes_the_v2_orientation_clause():
    """It carries READINESS, so the orientation naming readiness + recent_weeks is what
    the manifest resolves to. Only the orientation clause differs from grouped_v1's set."""
    assert F.READINESS in features_for(GROUPED2)
    assert clauses.clause_names(GROUPED2) == tuple(
        "group_orientation_v2" if name == "group_orientation_v1" else name
        for name in clauses.clause_names(GROUPED1)
    )


def test_grouped_v2_orientation_names_readiness_and_recent_weeks():
    text = PROMPT_VERSIONS[GROUPED2]
    assert "`readiness`" in text
    assert "`recent_weeks`" in text
    # The old right_now line (naming "recent training load") is gone.
    assert "recent training load and readiness" not in text
    # ...but grouped_v1's prompt still carries it, byte-stable.
    assert "recent training load and readiness" in PROMPT_VERSIONS[GROUPED1]


def test_grouped_v3_takes_the_v3_orientation_clause():
    """Slice 3 replaces INTENSITY with INTENSITY_READ + INTENSITY_MIX, so the manifest
    resolves the orientation that names `this_run.intensity_read` and
    `right_now.intensity_mix`. Everything else is grouped_v2's clause set."""
    assert F.INTENSITY_READ in features_for(GROUPED3)
    assert clauses.clause_names(GROUPED3) == tuple(
        "group_orientation_v3" if name == "group_orientation_v2" else name
        for name in clauses.clause_names(GROUPED2)
    )
    text = PROMPT_VERSIONS[GROUPED3]
    assert "`intensity_read`" in text
    assert "`intensity_mix`" in text
    assert PROMPT_VERSIONS[GROUPED3] == regrouped_from_lean_v1(
        clauses.GROUP_ORIENTATION_V3.text
    )


def test_grouped_v4_and_v5_change_the_pack_view_not_the_prose():
    """Slices 4 and 5 reframe the LLM's view of the pack; neither touches the system
    prompt, so both compose exactly grouped_v3's clause set."""
    for prompt_id in ("coach_message_lean_grouped_v4", "coach_message_lean_grouped_v5"):
        assert clauses.clause_names(prompt_id) == clauses.clause_names(GROUPED3)
        assert PROMPT_VERSIONS[prompt_id] == PROMPT_VERSIONS[GROUPED3]
        assert _OPENER_PROMPTS[prompt_id] == _OPENER_PROMPTS[GROUPED3]


def test_grouped_v1_prompt_is_unchanged_by_slice_2():
    """grouped_v1 must stay byte-identical (its own pin already covers the derivation;
    this belt-and-suspenders proves Slice 2 did not perturb it)."""
    assert PROMPT_VERSIONS[GROUPED1] == regrouped_from_lean_v1(
        clauses.GROUP_ORIENTATION_V1.text
    )
    assert PROMPT_VERSIONS[GROUPED2] != PROMPT_VERSIONS[GROUPED1]
    assert prompts.PROMPT_VERSIONS[GROUPED1] == PROMPT_VERSIONS[GROUPED1]
