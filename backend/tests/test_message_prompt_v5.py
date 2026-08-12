"""Structural tests for the P1.3 coach_message_v5 stance-aware prompt (ADR 0015).

The prompt's coaching QUALITY under each stance is an owner judgement, not a
unit-test claim. These pins guard the mechanical contract: v5 = v4 + a static
emphasis addendum (the Vn = V(n-1) + addendum idiom) for BOTH modes; v5 is
two-stage, voice-aware AND corpus-aware (it builds on v4); the stance gate set and
predicate resolve only v5; and v1..v4 (and the report chain) stay BYTE-STABLE —
P1.3 must not touch any cached prompt.
"""

from app.services.coach.prompts import (
    CORPUS_PROMPT_IDS,
    MESSAGE_PROMPT_PREFIX,
    PROMPT_VERSIONS,
    STANCE_PROMPT_IDS,
    SYSTEM_PROMPT_MESSAGE_V1,
    SYSTEM_PROMPT_MESSAGE_V2,
    SYSTEM_PROMPT_MESSAGE_V2_OPENER,
    SYSTEM_PROMPT_MESSAGE_V3,
    SYSTEM_PROMPT_MESSAGE_V4,
    SYSTEM_PROMPT_MESSAGE_V4_OPENER,
    SYSTEM_PROMPT_MESSAGE_V5,
    SYSTEM_PROMPT_MESSAGE_V5_OPENER,
    TWO_STAGE_PROMPT_IDS,
    VOICE_PROMPT_IDS,
    _CORPUS_ADDENDUM,
    _EMPHASIS_ADDENDUM,
    build_system_prompt,
    is_corpus_prompt,
    is_stance_prompt,
)

V5 = "coach_message_v5"


def test_v5_registered_in_message_family_and_gate_sets():
    assert V5 in PROMPT_VERSIONS
    assert V5.startswith(MESSAGE_PROMPT_PREFIX)  # -> schema 2.0 by prefix
    # v5 builds on v4, so it is two-stage, voice-aware, AND corpus-aware, plus stance.
    assert V5 in TWO_STAGE_PROMPT_IDS
    assert V5 in VOICE_PROMPT_IDS
    assert V5 in CORPUS_PROMPT_IDS
    assert V5 in STANCE_PROMPT_IDS


def test_is_stance_prompt_resolves_only_v5():
    assert is_stance_prompt(V5) is True
    for other in ("coach_message_v4", "coach_message_v3", "coach_message_v2",
                  "coach_message_v1", "coach_report_v10", "unknown_prompt"):
        assert is_stance_prompt(other) is False


def test_v5_is_corpus_aware_so_selected_school_rides_existing_seam():
    # The selected school needs no new rule: v5 is corpus-aware, so v4's rule 25
    # consumes whatever school the runner's stance threads into the `corpus` section.
    assert is_corpus_prompt(V5) is True


def test_fuller_v5_is_v4_plus_emphasis_addendum():
    """v5 fuller = v4 fuller verbatim + the static emphasis addendum."""
    assert SYSTEM_PROMPT_MESSAGE_V5 == SYSTEM_PROMPT_MESSAGE_V4 + _EMPHASIS_ADDENDUM
    assert SYSTEM_PROMPT_MESSAGE_V5.startswith(SYSTEM_PROMPT_MESSAGE_V4)


def test_opener_v5_is_v4_opener_plus_emphasis_addendum():
    """v5 opener = v4 opener verbatim + the SAME static emphasis addendum (mode-neutral)."""
    assert SYSTEM_PROMPT_MESSAGE_V5_OPENER == SYSTEM_PROMPT_MESSAGE_V4_OPENER + _EMPHASIS_ADDENDUM
    assert SYSTEM_PROMPT_MESSAGE_V5_OPENER.startswith(SYSTEM_PROMPT_MESSAGE_V4_OPENER)


def test_emphasis_addendum_states_the_authority_boundary():
    """Rule 26: emphasis/foregrounding only, never evidence, never overrides data/floor."""
    text = _EMPHASIS_ADDENDUM.lower()
    assert "stance" in text
    assert "emphasis" in text or "foreground" in text
    # both axes are named with their poles
    assert "data" in text and "sentiment" in text
    assert "process" in text and "outcome" in text
    # the authority-boundary commitments
    assert "never" in text
    assert "evidence" in text or "fact" in text
    assert "safety" in text  # the floor is named as invariant
    assert "invariant" in text


def test_build_system_prompt_v5_both_modes():
    """build_system_prompt dispatches v5 by mode: the fuller base for "fuller", the
    opener base for "opener". (v5 is voice-aware, so the runtime voice block always
    rides along — hence startswith, not ==.)"""
    fuller = build_system_prompt(V5, mode="fuller")
    assert fuller.startswith(SYSTEM_PROMPT_MESSAGE_V5)
    opener = build_system_prompt(V5, mode="opener")
    assert opener.startswith(SYSTEM_PROMPT_MESSAGE_V5_OPENER)
    # the two modes select genuinely different bases
    assert not fuller.startswith(SYSTEM_PROMPT_MESSAGE_V5_OPENER)


def test_v5_report_prompt_is_voiceless():
    """#822: the report prompt is the static constant and nothing else. v5 remains
    voice-aware for the conversational turn; the report's voice is applied after."""
    from app.services.coach.prompts import VOICE_PROMPT_IDS
    assert build_system_prompt(V5, mode="fuller") == SYSTEM_PROMPT_MESSAGE_V5
    # Stated directly rather than probed through a render: the point is that
    # the report is voiceless BECAUSE build_system_prompt carries no voice,
    # not because v5 sits outside the voice-aware set.
    assert V5 in VOICE_PROMPT_IDS


def test_v1_through_v4_and_report_chain_byte_stable():
    """P1.3 must not touch any cached prompt: v1..v4 fuller+opener and v10 unchanged."""
    assert PROMPT_VERSIONS["coach_message_v1"] == SYSTEM_PROMPT_MESSAGE_V1
    assert PROMPT_VERSIONS["coach_message_v2"] == SYSTEM_PROMPT_MESSAGE_V2
    assert PROMPT_VERSIONS["coach_message_v3"] == SYSTEM_PROMPT_MESSAGE_V3
    assert PROMPT_VERSIONS["coach_message_v4"] == SYSTEM_PROMPT_MESSAGE_V4
    # v4 is still exactly v3 + the corpus addendum (the emphasis addendum did not leak in)
    assert SYSTEM_PROMPT_MESSAGE_V4 == SYSTEM_PROMPT_MESSAGE_V3 + _CORPUS_ADDENDUM
    assert _EMPHASIS_ADDENDUM not in SYSTEM_PROMPT_MESSAGE_V4
    assert _EMPHASIS_ADDENDUM not in SYSTEM_PROMPT_MESSAGE_V4_OPENER
    # building a non-stance prompt never appends the emphasis addendum
    assert _EMPHASIS_ADDENDUM not in build_system_prompt("coach_message_v4", mode="fuller")
    assert _EMPHASIS_ADDENDUM not in build_system_prompt("coach_message_v3", mode="opener")
