"""Structural tests for the P3 coach_message_v6 training-load-aware prompt (ADR 0016).

The prompt's coaching QUALITY is an owner judgement, not a unit-test claim. These pins
guard the mechanical contract: v6 = v5 + a static readiness addendum (the Vn = V(n-1)
+ addendum idiom) for BOTH modes; v6 is two-stage, voice-, corpus-, AND stance-aware
(it builds on v5); the training-load gate set and predicate resolve only v6; and
v1..v5 (and the report chain) stay BYTE-STABLE — P3 must not touch any cached prompt.
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
    SYSTEM_PROMPT_MESSAGE_V5_OPENER,
    SYSTEM_PROMPT_MESSAGE_V6,
    SYSTEM_PROMPT_MESSAGE_V6_OPENER,
    TWO_STAGE_PROMPT_IDS,
    VOICE_PROMPT_IDS,
    _EMPHASIS_ADDENDUM,
    _READINESS_ADDENDUM,
    build_system_prompt,
    is_training_load_prompt,
)
from app.services.coach.service import active_schema_version

V6 = "coach_message_v6"


def test_v6_registered_in_message_family_and_gate_sets():
    assert V6 in PROMPT_VERSIONS
    assert V6.startswith(MESSAGE_PROMPT_PREFIX)  # -> schema 2.0 by prefix
    # v6 builds on v5, so it inherits every awareness, plus training-load.
    assert V6 in TWO_STAGE_PROMPT_IDS
    assert V6 in VOICE_PROMPT_IDS
    assert V6 in CORPUS_PROMPT_IDS
    assert V6 in STANCE_PROMPT_IDS
    assert V6 in TRAINING_LOAD_PROMPT_IDS
    # cache identity stays schema 2.0, same family as v5 (so v6 reports regenerate).
    assert active_schema_version(V6) == active_schema_version("coach_message_v5")


def test_is_training_load_prompt_resolves_only_v6():
    assert is_training_load_prompt(V6) is True
    for other in ("coach_message_v5", "coach_message_v4", "coach_message_v3",
                  "coach_message_v2", "coach_message_v1", "coach_report_v10",
                  "unknown_prompt", None):
        assert is_training_load_prompt(other) is False


def test_fuller_v6_is_v5_plus_readiness_addendum():
    """v6 fuller = v5 fuller verbatim + the static readiness addendum."""
    assert SYSTEM_PROMPT_MESSAGE_V6 == SYSTEM_PROMPT_MESSAGE_V5 + _READINESS_ADDENDUM
    assert SYSTEM_PROMPT_MESSAGE_V6.startswith(SYSTEM_PROMPT_MESSAGE_V5)


def test_opener_v6_is_v5_opener_plus_readiness_addendum():
    """v6 opener = v5 opener verbatim + the SAME static readiness addendum (mode-neutral)."""
    assert SYSTEM_PROMPT_MESSAGE_V6_OPENER == SYSTEM_PROMPT_MESSAGE_V5_OPENER + _READINESS_ADDENDUM
    assert SYSTEM_PROMPT_MESSAGE_V6_OPENER.startswith(SYSTEM_PROMPT_MESSAGE_V5_OPENER)


def test_readiness_addendum_states_the_authority_boundary():
    """Rule 27: context for the run, never overrides data/floor, not a verdict/diagnosis."""
    text = _READINESS_ADDENDUM.lower()
    assert "training_load" in text or "training load" in text
    assert "fitness" in text and "fatigue" in text and "form" in text
    # the authority-boundary commitments
    assert "never overrides" in text
    assert "safety" in text                       # the floor is named as invariant
    assert "verdict" in text or "diagnosis" in text  # not an intensity verdict / diagnosis
    assert "warming_up" in text or "warming up" in text  # the abstention clause


def test_build_system_prompt_v6_both_modes():
    """build_system_prompt dispatches v6 by mode. (v6 is voice-aware, so the runtime
    voice block always rides along — hence startswith, not ==.)"""
    fuller = build_system_prompt(V6, mode="fuller")
    assert fuller.startswith(SYSTEM_PROMPT_MESSAGE_V6)
    opener = build_system_prompt(V6, mode="opener")
    assert opener.startswith(SYSTEM_PROMPT_MESSAGE_V6_OPENER)
    assert not fuller.startswith(SYSTEM_PROMPT_MESSAGE_V6_OPENER)


def test_v6_carries_voice_block_at_runtime():
    from app.services.coach.prompts import render_voice_block
    fuller = build_system_prompt(V6, mode="fuller", voice=None)
    assert fuller == SYSTEM_PROMPT_MESSAGE_V6 + render_voice_block(V6, None)
    assert len(render_voice_block(V6, None)) > 0  # v6 IS in VOICE_PROMPT_IDS


def test_v1_through_v5_and_report_chain_byte_stable():
    """P3 must not touch any cached prompt: v1..v5 and v10 unchanged."""
    assert PROMPT_VERSIONS["coach_message_v1"] == SYSTEM_PROMPT_MESSAGE_V1
    assert PROMPT_VERSIONS["coach_message_v2"] == SYSTEM_PROMPT_MESSAGE_V2
    assert PROMPT_VERSIONS["coach_message_v3"] == SYSTEM_PROMPT_MESSAGE_V3
    assert PROMPT_VERSIONS["coach_message_v4"] == SYSTEM_PROMPT_MESSAGE_V4
    assert PROMPT_VERSIONS["coach_message_v5"] == SYSTEM_PROMPT_MESSAGE_V5
    # v5 is still exactly v4 + the emphasis addendum (readiness did not leak in)
    assert SYSTEM_PROMPT_MESSAGE_V5 == SYSTEM_PROMPT_MESSAGE_V4 + _EMPHASIS_ADDENDUM
    assert _READINESS_ADDENDUM not in SYSTEM_PROMPT_MESSAGE_V5
    assert _READINESS_ADDENDUM not in SYSTEM_PROMPT_MESSAGE_V5_OPENER
    # building a non-training-load prompt never appends the readiness addendum
    assert _READINESS_ADDENDUM not in build_system_prompt("coach_message_v5", mode="fuller", voice=None)
    assert _READINESS_ADDENDUM not in build_system_prompt("coach_message_v4", mode="opener", voice=None)
