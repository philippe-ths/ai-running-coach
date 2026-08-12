"""Structural tests for the #266 coach_message_v8 philosophy-extracted, retuned prompt.

The prompt's coaching QUALITY (does it feel more human / less samey?) is an owner
judgement settled by the v7-vs-v8 B-vs-A pack, not a unit-test claim. These pins
guard the mechanical contract:

  - v8 is the first message version that is NOT v(n-1) + an addendum: its novelty is
    the BASE prose. It carries EXACTLY v7's six capabilities (no new feature).
  - v8 reuses the SAME six addenda v7 carries, byte-for-byte, so v8 differs from v7
    ONLY in the base prose (fuller and opener alike).
  - The safety floor is INVARIANT under #266: the output protocol, GROUNDING, SAFETY,
    and the reading/relationship disciplines are sliced verbatim from coach_message_v1
    (fuller) and the v2 opener, so they are byte-identical to v7's.
  - The retune is actually present (a new HOW YOU SOUND section, the corpus-philosophy
    deferral), and it did NOT leak into any cached prompt: v1..v7 and the report chain
    stay BYTE-STABLE.
"""

from app.services.coach.prompts import (
    CORPUS_PROMPT_IDS,
    MESSAGE_PROMPT_PREFIX,
    PROMPT_VERSIONS,
    STANCE_PROMPT_IDS,
    SYSTEM_PROMPT_MESSAGE_V1,
    SYSTEM_PROMPT_MESSAGE_V2,
    SYSTEM_PROMPT_MESSAGE_V2_OPENER,
    SYSTEM_PROMPT_MESSAGE_V6,
    SYSTEM_PROMPT_MESSAGE_V6_OPENER,
    SYSTEM_PROMPT_MESSAGE_V7,
    SYSTEM_PROMPT_MESSAGE_V7_OPENER,
    SYSTEM_PROMPT_MESSAGE_V8,
    SYSTEM_PROMPT_MESSAGE_V8_OPENER,
    TRAINING_LOAD_PROMPT_IDS,
    TWO_STAGE_PROMPT_IDS,
    USER_MATERIALS_PROMPT_IDS,
    VOICE_PROMPT_IDS,
    _CORPUS_ADDENDUM,
    _EMPHASIS_ADDENDUM,
    _MESSAGE_V2_CONTINUITY,
    _READINESS_ADDENDUM,
    _USER_MATERIALS_ADDENDUM,
    _V8_FULLER_HOW_YOU_SOUND,
    _V8_OPENER_HOW_YOU_SOUND,
    _VOICE_ADDENDUM,
    build_system_prompt,
    is_user_materials_prompt,
)
from app.services.coach.service import active_schema_version

V7 = "coach_message_v7"
V8 = "coach_message_v8"

# The exact addendum suffix the fuller prompt of v7/v8 ends with (same order they are
# chained in prompts.py). v7 = v1 + this; v8 = retuned base + this.
_FULLER_SUFFIX = (
    _MESSAGE_V2_CONTINUITY
    + _VOICE_ADDENDUM
    + _CORPUS_ADDENDUM
    + _EMPHASIS_ADDENDUM
    + _READINESS_ADDENDUM
    + _USER_MATERIALS_ADDENDUM
)
# The opener never gets the fuller-turn continuity addendum.
_OPENER_SUFFIX = (
    _VOICE_ADDENDUM
    + _CORPUS_ADDENDUM
    + _EMPHASIS_ADDENDUM
    + _READINESS_ADDENDUM
    + _USER_MATERIALS_ADDENDUM
)


def test_v8_registered_in_message_family_and_gate_sets():
    assert V8 in PROMPT_VERSIONS
    assert V8.startswith(MESSAGE_PROMPT_PREFIX)  # -> schema 2.0 by prefix
    # v8 carries exactly v7's capability set (a retune, not a new feature).
    assert V8 in TWO_STAGE_PROMPT_IDS
    assert V8 in VOICE_PROMPT_IDS
    assert V8 in CORPUS_PROMPT_IDS
    assert V8 in STANCE_PROMPT_IDS
    assert V8 in TRAINING_LOAD_PROMPT_IDS
    assert V8 in USER_MATERIALS_PROMPT_IDS
    # cache identity stays schema 2.0, same family as v7 (so v8 reports regenerate).
    assert active_schema_version(V8) == active_schema_version(V7)


def test_is_user_materials_prompt_resolves_v8():
    """v8 is user-materials-aware exactly like v7 (it shares the capability set)."""
    assert is_user_materials_prompt(V8) is True


def test_v8_fuller_differs_from_v7_only_in_the_base():
    """v7 = v1 + suffix; v8 = retuned base + the SAME suffix. So they share the whole
    addendum tail and differ only in the leading base prose."""
    assert SYSTEM_PROMPT_MESSAGE_V7.endswith(_FULLER_SUFFIX)
    assert SYSTEM_PROMPT_MESSAGE_V8.endswith(_FULLER_SUFFIX)
    # v7's base is exactly coach_message_v1.
    assert SYSTEM_PROMPT_MESSAGE_V7[: -len(_FULLER_SUFFIX)] == SYSTEM_PROMPT_MESSAGE_V1
    # v8's base is a DIFFERENT, retuned base.
    v8_base = SYSTEM_PROMPT_MESSAGE_V8[: -len(_FULLER_SUFFIX)]
    assert v8_base != SYSTEM_PROMPT_MESSAGE_V1
    assert SYSTEM_PROMPT_MESSAGE_V8 != SYSTEM_PROMPT_MESSAGE_V7


def test_v8_floor_is_byte_identical_to_v1_from_the_output_protocol_onward():
    """The safety floor is invariant under #266 BY CONSTRUCTION: everything from the
    output protocol onward (the protocol, GROUNDING, SAFETY, the disciplines, the
    closing) is sliced verbatim from coach_message_v1."""
    marker = "# HOW YOU WORK (output protocol)"
    v1_tail = SYSTEM_PROMPT_MESSAGE_V1[SYSTEM_PROMPT_MESSAGE_V1.index(marker) :]
    v8_base = SYSTEM_PROMPT_MESSAGE_V8[: -len(_FULLER_SUFFIX)]
    assert v8_base.endswith(v1_tail)


def test_v8_grounding_and_safety_blocks_match_v7_exactly():
    """A direct, human-readable floor pin: the GROUNDING and SAFETY blocks are
    byte-identical between v7 and v8 fuller."""

    def section(text: str, start: str, end: str) -> str:
        i = text.index(start)
        j = text.index(end, i)
        return text[i:j]

    for start, end in [
        ("# GROUNDING (never invent", "# SAFETY (these are enforced"),
        ("# SAFETY (these are enforced", "# READING THIS RUN"),
        ("# READING THIS RUN", "# CARRYING THE RELATIONSHIP FORWARD"),
    ]:
        assert section(SYSTEM_PROMPT_MESSAGE_V8, start, end) == section(
            SYSTEM_PROMPT_MESSAGE_V7, start, end
        )


def test_v8_reuses_every_addendum_byte_for_byte():
    for addendum in (
        _MESSAGE_V2_CONTINUITY,
        _VOICE_ADDENDUM,
        _CORPUS_ADDENDUM,
        _EMPHASIS_ADDENDUM,
        _READINESS_ADDENDUM,
        _USER_MATERIALS_ADDENDUM,
    ):
        assert addendum in SYSTEM_PROMPT_MESSAGE_V8


def test_v8_opener_differs_from_v7_opener_only_in_the_base():
    assert SYSTEM_PROMPT_MESSAGE_V7_OPENER.endswith(_OPENER_SUFFIX)
    assert SYSTEM_PROMPT_MESSAGE_V8_OPENER.endswith(_OPENER_SUFFIX)
    # v7 opener base is exactly the v2 opener.
    assert (
        SYSTEM_PROMPT_MESSAGE_V7_OPENER[: -len(_OPENER_SUFFIX)]
        == SYSTEM_PROMPT_MESSAGE_V2_OPENER
    )
    # v8 opener base is retuned, but its protocol/GROUNDING/SAFETY tail is sliced from
    # the v2 opener verbatim (floor invariant).
    v8_opener_base = SYSTEM_PROMPT_MESSAGE_V8_OPENER[: -len(_OPENER_SUFFIX)]
    assert v8_opener_base != SYSTEM_PROMPT_MESSAGE_V2_OPENER
    marker = "# HOW YOU WORK (opener output protocol)"
    v2_opener_tail = SYSTEM_PROMPT_MESSAGE_V2_OPENER[
        SYSTEM_PROMPT_MESSAGE_V2_OPENER.index(marker) :
    ]
    assert v8_opener_base.endswith(v2_opener_tail)


def test_v8_retune_is_actually_present():
    """The point of #266: a new HOW YOU SOUND character section + corpus-philosophy
    deferral, present in BOTH modes and absent from every prior version."""
    assert _V8_FULLER_HOW_YOU_SOUND in SYSTEM_PROMPT_MESSAGE_V8
    assert _V8_OPENER_HOW_YOU_SOUND in SYSTEM_PROMPT_MESSAGE_V8_OPENER
    sound = _V8_FULLER_HOW_YOU_SOUND.lower()
    assert "how you sound" in sound
    assert "point of view" in sound          # have a point of view, commit to it
    assert "corpus" in sound                 # training philosophy comes from the corpus
    assert "template" in sound or "mould" in sound  # the anti-sameness instruction
    # the floor's hard rule names still survive intact in the full v8 prompt
    for floor in ("MEDICAL SCOPE", "ZONE LANGUAGE", "INTERVAL CONFIDENCE"):
        assert floor in SYSTEM_PROMPT_MESSAGE_V8


def test_v8_report_prompt_is_voiceless():
    """#822: the report prompt is the static constant and nothing else."""
    assert build_system_prompt(V8, mode="fuller") == SYSTEM_PROMPT_MESSAGE_V8
    # Stated directly rather than probed through a render: the point is that the
    # report is voiceless BECAUSE build_system_prompt carries no voice, not
    # because v8 sits outside the voice-aware set.
    assert V8 in VOICE_PROMPT_IDS


def test_build_system_prompt_v8_both_modes():
    fuller = build_system_prompt(V8, mode="fuller")
    assert fuller.startswith(SYSTEM_PROMPT_MESSAGE_V8)
    opener = build_system_prompt(V8, mode="opener")
    assert opener.startswith(SYSTEM_PROMPT_MESSAGE_V8_OPENER)
    assert not fuller.startswith(SYSTEM_PROMPT_MESSAGE_V8_OPENER)


def test_v1_through_v7_and_report_chain_byte_stable():
    """#266 must not touch any cached prompt: v1..v7 and the report chain unchanged,
    and the HOW YOU SOUND retune must not have leaked into any of them."""
    assert PROMPT_VERSIONS["coach_message_v1"] == SYSTEM_PROMPT_MESSAGE_V1
    assert PROMPT_VERSIONS["coach_message_v2"] == SYSTEM_PROMPT_MESSAGE_V2
    assert PROMPT_VERSIONS["coach_message_v6"] == SYSTEM_PROMPT_MESSAGE_V6
    assert PROMPT_VERSIONS["coach_message_v7"] == SYSTEM_PROMPT_MESSAGE_V7
    # v7 is still exactly v6 + the user-materials addendum (v8 did not disturb it).
    assert SYSTEM_PROMPT_MESSAGE_V7 == SYSTEM_PROMPT_MESSAGE_V6 + _USER_MATERIALS_ADDENDUM
    # the retune did not leak into any prior version (fuller or opener).
    for prior in (
        SYSTEM_PROMPT_MESSAGE_V1,
        SYSTEM_PROMPT_MESSAGE_V2,
        SYSTEM_PROMPT_MESSAGE_V6,
        SYSTEM_PROMPT_MESSAGE_V7,
        SYSTEM_PROMPT_MESSAGE_V2_OPENER,
        SYSTEM_PROMPT_MESSAGE_V6_OPENER,
        SYSTEM_PROMPT_MESSAGE_V7_OPENER,
    ):
        assert "# HOW YOU SOUND" not in prior
    # building a prior prompt never appends the v8 retune.
    assert "# HOW YOU SOUND" not in build_system_prompt("coach_message_v7", mode="fuller")
    assert "# HOW YOU SOUND" not in build_system_prompt("coach_message_v7", mode="opener")
