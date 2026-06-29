"""Structural pins for the #400 coach_message_v9 volume-vs-norm prompt.

v9 = v8 + a static VOLUME addendum (fuller and opener), the Vn = V(n-1) + addendum
idiom. It carries every v8 capability plus VOLUME. The safety floor is invariant by
construction: v8 is reused byte-for-byte as the prefix, so v9 differs from v8 ONLY
by the appended volume addendum, and v1..v8 / the report chain stay byte-stable.
"""

from app.services.coach.prompts import (
    CORPUS_PROMPT_IDS,
    MESSAGE_PROMPT_PREFIX,
    PROMPT_VERSIONS,
    STANCE_PROMPT_IDS,
    SYSTEM_PROMPT_MESSAGE_V8,
    SYSTEM_PROMPT_MESSAGE_V8_OPENER,
    SYSTEM_PROMPT_MESSAGE_V9,
    SYSTEM_PROMPT_MESSAGE_V9_OPENER,
    TRAINING_LOAD_PROMPT_IDS,
    TWO_STAGE_PROMPT_IDS,
    USER_MATERIALS_PROMPT_IDS,
    VOICE_PROMPT_IDS,
    VOLUME_PROMPT_IDS,
    _VOLUME_ADDENDUM,
    build_system_prompt,
    is_volume_prompt,
)
from app.services.coach.service import active_schema_version

V8 = "coach_message_v8"
V9 = "coach_message_v9"


def test_v9_registered_in_message_family_and_gate_sets():
    assert V9 in PROMPT_VERSIONS
    assert V9.startswith(MESSAGE_PROMPT_PREFIX)  # -> schema 2.0 by prefix
    # v9 carries every v8 capability...
    assert V9 in TWO_STAGE_PROMPT_IDS
    assert V9 in VOICE_PROMPT_IDS
    assert V9 in CORPUS_PROMPT_IDS
    assert V9 in STANCE_PROMPT_IDS
    assert V9 in TRAINING_LOAD_PROMPT_IDS
    assert V9 in USER_MATERIALS_PROMPT_IDS
    # ...plus the new VOLUME capability (v9 onward; later versions inherit it).
    assert V9 in VOLUME_PROMPT_IDS
    assert VOLUME_PROMPT_IDS == {
        V9, "coach_message_v10", "coach_message_v11", "coach_message_v12",
        "coach_message_v13",
    }
    # same schema family as v8 (so v9 reports regenerate, prior history retained).
    assert active_schema_version(V9) == active_schema_version(V8)


def test_v9_is_v8_plus_volume_addendum():
    assert SYSTEM_PROMPT_MESSAGE_V9 == SYSTEM_PROMPT_MESSAGE_V8 + _VOLUME_ADDENDUM
    assert SYSTEM_PROMPT_MESSAGE_V9_OPENER == SYSTEM_PROMPT_MESSAGE_V8_OPENER + _VOLUME_ADDENDUM
    # v8 is preserved verbatim as the prefix -> safety floor invariant.
    assert SYSTEM_PROMPT_MESSAGE_V9.startswith(SYSTEM_PROMPT_MESSAGE_V8)
    assert SYSTEM_PROMPT_MESSAGE_V9_OPENER.startswith(SYSTEM_PROMPT_MESSAGE_V8_OPENER)


def test_volume_addendum_present_in_v9_absent_in_v8():
    assert _VOLUME_ADDENDUM not in SYSTEM_PROMPT_MESSAGE_V8
    assert _VOLUME_ADDENDUM not in SYSTEM_PROMPT_MESSAGE_V8_OPENER
    # And via the builder: v9 carries it, v8 does not (inert under rollback).
    assert _VOLUME_ADDENDUM in build_system_prompt(V9, mode="fuller", voice=None)
    assert _VOLUME_ADDENDUM in build_system_prompt(V9, mode="opener", voice=None)
    assert _VOLUME_ADDENDUM not in build_system_prompt(V8, mode="fuller", voice=None)
    assert _VOLUME_ADDENDUM not in build_system_prompt(V8, mode="opener", voice=None)


def test_volume_addendum_speaks_to_the_deliberate_down_week():
    text = _VOLUME_ADDENDUM.lower()
    assert "training_volume" in text
    assert "down" in text and "intentional" in text  # the core anti-nag discipline
    assert "never override" in text or "floor win" in text  # authority boundary held


def test_is_volume_prompt_resolves():
    assert is_volume_prompt(V9) is True
    assert is_volume_prompt(V8) is False
    assert is_volume_prompt(None) is False
