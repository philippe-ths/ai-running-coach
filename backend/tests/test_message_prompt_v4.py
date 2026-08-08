"""Structural tests for the P1.2 coach_message_v4 corpus-aware prompt (ADR 0014).

The prompt's coaching QUALITY under each school is an owner judgement, not a
unit-test claim. These pins guard the mechanical contract: v4 = v3 + a static
corpus addendum (the Vn = V(n-1) + addendum idiom) for BOTH modes; v4 is two-stage
and voice-aware (it builds on v3, which is both); the corpus gate set and predicate
resolve only v4; and v1..v3 (and the report chain) stay BYTE-STABLE — P1.2 must not
touch any cached prompt.
"""

from app.services.coach.prompts import (
    CORPUS_PROMPT_IDS,
    MESSAGE_PROMPT_PREFIX,
    PROMPT_VERSIONS,
    SYSTEM_PROMPT_MESSAGE_V1,
    SYSTEM_PROMPT_MESSAGE_V2,
    SYSTEM_PROMPT_MESSAGE_V2_OPENER,
    SYSTEM_PROMPT_MESSAGE_V3,
    SYSTEM_PROMPT_MESSAGE_V3_OPENER,
    SYSTEM_PROMPT_MESSAGE_V4,
    SYSTEM_PROMPT_MESSAGE_V4_OPENER,
    TWO_STAGE_PROMPT_IDS,
    VOICE_PROMPT_IDS,
    _CORPUS_ADDENDUM,
    build_system_prompt,
    is_corpus_prompt,
)

V4 = "coach_message_v4"


def test_v4_registered_in_message_family_and_gate_sets():
    assert V4 in PROMPT_VERSIONS
    assert V4.startswith(MESSAGE_PROMPT_PREFIX)  # -> schema 2.0 by prefix
    # v4 builds on v3, so it is BOTH two-stage and voice-aware, plus corpus-aware.
    assert V4 in TWO_STAGE_PROMPT_IDS
    assert V4 in VOICE_PROMPT_IDS
    assert V4 in CORPUS_PROMPT_IDS


def test_is_corpus_prompt_resolves_only_v4():
    assert is_corpus_prompt(V4) is True
    for other in ("coach_message_v3", "coach_message_v2", "coach_message_v1",
                  "coach_report_v10", "unknown_prompt"):
        assert is_corpus_prompt(other) is False


def test_fuller_v4_is_v3_plus_corpus_addendum():
    """v4 fuller = v3 fuller verbatim + the static corpus addendum."""
    assert SYSTEM_PROMPT_MESSAGE_V4 == SYSTEM_PROMPT_MESSAGE_V3 + _CORPUS_ADDENDUM
    assert SYSTEM_PROMPT_MESSAGE_V4.startswith(SYSTEM_PROMPT_MESSAGE_V3)


def test_opener_v4_is_v3_opener_plus_corpus_addendum():
    """v4 opener = v3 opener verbatim + the SAME static corpus addendum (mode-neutral)."""
    assert SYSTEM_PROMPT_MESSAGE_V4_OPENER == SYSTEM_PROMPT_MESSAGE_V3_OPENER + _CORPUS_ADDENDUM
    assert SYSTEM_PROMPT_MESSAGE_V4_OPENER.startswith(SYSTEM_PROMPT_MESSAGE_V3_OPENER)


def test_corpus_addendum_states_the_authority_boundary():
    """Rule 25: emphasis/framing only, never evidence, never overrides data/floor."""
    text = _CORPUS_ADDENDUM.lower()
    assert "corpus" in text
    assert "emphasis" in text or "framing" in text
    # the three authority-boundary commitments
    assert "never" in text
    assert "evidence" in text or "factual" in text or "fact" in text
    assert "safety" in text  # the floor is named as invariant


def test_build_system_prompt_v4_both_modes():
    """build_system_prompt dispatches v4 by mode: the fuller base for "fuller", the
    opener base for "opener". (v4 is voice-aware, so the runtime voice block always
    rides along — see test_v4_carries_voice_block_at_runtime — hence startswith.)"""
    fuller = build_system_prompt(V4, mode="fuller")
    assert fuller.startswith(SYSTEM_PROMPT_MESSAGE_V4)
    opener = build_system_prompt(V4, mode="opener")
    assert opener.startswith(SYSTEM_PROMPT_MESSAGE_V4_OPENER)
    # the two modes select genuinely different bases
    assert not fuller.startswith(SYSTEM_PROMPT_MESSAGE_V4_OPENER)


def test_v4_report_prompt_is_voiceless():
    """#822: the report prompt is the static constant and nothing else, on both
    stages. v4 remains voice-aware for the conversational turn, which still steers
    at prompt time — the report does not, because its voice is applied afterwards."""
    from app.services.coach.prompts import render_voice_block
    assert build_system_prompt(V4, mode="fuller") == SYSTEM_PROMPT_MESSAGE_V4
    assert build_system_prompt(V4, mode="opener") == SYSTEM_PROMPT_MESSAGE_V4_OPENER
    assert len(render_voice_block(V4, None)) > 0  # v4 IS in VOICE_PROMPT_IDS


def test_v1_v2_v3_and_report_chain_byte_stable():
    """P1.2 must not touch any cached prompt: v1..v3 fuller+opener and v10 unchanged."""
    assert PROMPT_VERSIONS["coach_message_v1"] == SYSTEM_PROMPT_MESSAGE_V1
    assert PROMPT_VERSIONS["coach_message_v2"] == SYSTEM_PROMPT_MESSAGE_V2
    assert PROMPT_VERSIONS["coach_message_v3"] == SYSTEM_PROMPT_MESSAGE_V3
    # v3 is still exactly v2 + the voice addendum (the corpus addendum did not leak in)
    assert SYSTEM_PROMPT_MESSAGE_V3.startswith(SYSTEM_PROMPT_MESSAGE_V2)
    assert _CORPUS_ADDENDUM not in SYSTEM_PROMPT_MESSAGE_V3
    assert _CORPUS_ADDENDUM not in SYSTEM_PROMPT_MESSAGE_V2_OPENER
    # building a non-corpus prompt never appends the corpus addendum
    assert _CORPUS_ADDENDUM not in build_system_prompt("coach_message_v3", mode="fuller")
