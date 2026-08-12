"""Structural tests for the P4 coach_message_v7 user-materials-aware prompt (#286, ADR 0017).

The prompt's coaching QUALITY is an owner judgement, not a unit-test claim. These pins
guard the mechanical contract: v7 = v6 + a static USER MATERIALS addendum (the
Vn = V(n-1) + addendum idiom) for BOTH modes; v7 is two-stage, voice-, corpus-,
stance-, AND training-load-aware (it builds on v6) plus user-materials-aware; the
user-materials gate set and predicate resolve only v7; and v1..v6 (and the report
chain) stay BYTE-STABLE — #286 must not touch any cached prompt.
"""

from app.services.coach.prompts import (
    CORPUS_PROMPT_IDS,
    MESSAGE_PROMPT_PREFIX,
    PROMPT_VERSIONS,
    STANCE_PROMPT_IDS,
    TRAINING_LOAD_PROMPT_IDS,
    SYSTEM_PROMPT_MESSAGE_V1,
    SYSTEM_PROMPT_MESSAGE_V2,
    SYSTEM_PROMPT_MESSAGE_V3,
    SYSTEM_PROMPT_MESSAGE_V4,
    SYSTEM_PROMPT_MESSAGE_V5,
    SYSTEM_PROMPT_MESSAGE_V6,
    SYSTEM_PROMPT_MESSAGE_V6_OPENER,
    SYSTEM_PROMPT_MESSAGE_V7,
    SYSTEM_PROMPT_MESSAGE_V7_OPENER,
    TWO_STAGE_PROMPT_IDS,
    USER_MATERIALS_PROMPT_IDS,
    VOICE_PROMPT_IDS,
    _READINESS_ADDENDUM,
    _USER_MATERIALS_ADDENDUM,
    build_system_prompt,
    is_user_materials_prompt,
)
from app.services.coach.service import active_schema_version

V7 = "coach_message_v7"


def test_v7_registered_in_message_family_and_gate_sets():
    assert V7 in PROMPT_VERSIONS
    assert V7.startswith(MESSAGE_PROMPT_PREFIX)  # -> schema 2.0 by prefix
    # v7 builds on v6, so it inherits every awareness, plus user-materials.
    assert V7 in TWO_STAGE_PROMPT_IDS
    assert V7 in VOICE_PROMPT_IDS
    assert V7 in CORPUS_PROMPT_IDS
    assert V7 in STANCE_PROMPT_IDS
    assert V7 in TRAINING_LOAD_PROMPT_IDS
    assert V7 in USER_MATERIALS_PROMPT_IDS
    # cache identity stays schema 2.0, same family as v6 (so v7 reports regenerate).
    assert active_schema_version(V7) == active_schema_version("coach_message_v6")


def test_is_user_materials_prompt_resolves_only_v7():
    assert is_user_materials_prompt(V7) is True
    for other in ("coach_message_v6", "coach_message_v5", "coach_message_v4",
                  "coach_message_v3", "coach_message_v2", "coach_message_v1",
                  "coach_report_v10", "unknown_prompt", None):
        assert is_user_materials_prompt(other) is False


def test_fuller_v7_is_v6_plus_user_materials_addendum():
    """v7 fuller = v6 fuller verbatim + the static user-materials addendum."""
    assert SYSTEM_PROMPT_MESSAGE_V7 == SYSTEM_PROMPT_MESSAGE_V6 + _USER_MATERIALS_ADDENDUM
    assert SYSTEM_PROMPT_MESSAGE_V7.startswith(SYSTEM_PROMPT_MESSAGE_V6)


def test_opener_v7_is_v6_opener_plus_user_materials_addendum():
    """v7 opener = v6 opener verbatim + the SAME static addendum (mode-neutral)."""
    assert SYSTEM_PROMPT_MESSAGE_V7_OPENER == SYSTEM_PROMPT_MESSAGE_V6_OPENER + _USER_MATERIALS_ADDENDUM
    assert SYSTEM_PROMPT_MESSAGE_V7_OPENER.startswith(SYSTEM_PROMPT_MESSAGE_V6_OPENER)


def test_user_materials_addendum_states_the_authority_boundary():
    """Rule 28: materials are reference (not instructions), beat house philosophy for
    stance, never override measured data / the runner's goal / the safety floor."""
    text = _USER_MATERIALS_ADDENDUM.lower()
    assert "material" in text                         # names the user materials
    assert "instruction" in text                      # reference, NEVER instructions
    assert "never override" in text or "never overrides" in text
    assert "safety" in text                           # the floor is named as invariant
    assert "goal" in text                             # tethered to the runner's real goal
    # the tier claim: materials outrank house philosophy
    assert "house" in text


def test_build_system_prompt_v7_both_modes():
    """build_system_prompt dispatches v7 by mode. (v7 is voice-aware, so the runtime
    voice block always rides along — hence startswith, not ==.)"""
    fuller = build_system_prompt(V7, mode="fuller")
    assert fuller.startswith(SYSTEM_PROMPT_MESSAGE_V7)
    opener = build_system_prompt(V7, mode="opener")
    assert opener.startswith(SYSTEM_PROMPT_MESSAGE_V7_OPENER)
    assert not fuller.startswith(SYSTEM_PROMPT_MESSAGE_V7_OPENER)


def test_v7_report_prompt_is_voiceless():
    """#822: the report prompt is the static constant and nothing else."""
    from app.services.coach.prompts import VOICE_PROMPT_IDS
    assert build_system_prompt(V7, mode="fuller") == SYSTEM_PROMPT_MESSAGE_V7
    # Stated directly rather than probed through a render: the point is that
    # the report is voiceless BECAUSE build_system_prompt carries no voice,
    # not because v7 sits outside the voice-aware set.
    assert V7 in VOICE_PROMPT_IDS


def test_v1_through_v6_and_report_chain_byte_stable():
    """#286 must not touch any cached prompt: v1..v6 and the report chain unchanged."""
    assert PROMPT_VERSIONS["coach_message_v1"] == SYSTEM_PROMPT_MESSAGE_V1
    assert PROMPT_VERSIONS["coach_message_v2"] == SYSTEM_PROMPT_MESSAGE_V2
    assert PROMPT_VERSIONS["coach_message_v3"] == SYSTEM_PROMPT_MESSAGE_V3
    assert PROMPT_VERSIONS["coach_message_v4"] == SYSTEM_PROMPT_MESSAGE_V4
    assert PROMPT_VERSIONS["coach_message_v5"] == SYSTEM_PROMPT_MESSAGE_V5
    assert PROMPT_VERSIONS["coach_message_v6"] == SYSTEM_PROMPT_MESSAGE_V6
    # v6 is still exactly v5 + the readiness addendum (user-materials did not leak in)
    assert SYSTEM_PROMPT_MESSAGE_V6 == SYSTEM_PROMPT_MESSAGE_V5 + _READINESS_ADDENDUM
    assert _USER_MATERIALS_ADDENDUM not in SYSTEM_PROMPT_MESSAGE_V6
    assert _USER_MATERIALS_ADDENDUM not in SYSTEM_PROMPT_MESSAGE_V6_OPENER
    # building a non-user-materials prompt never appends the user-materials addendum
    assert _USER_MATERIALS_ADDENDUM not in build_system_prompt("coach_message_v6", mode="fuller")
    assert _USER_MATERIALS_ADDENDUM not in build_system_prompt("coach_message_v5", mode="opener")
