"""Structural pins for the #561 coach_message_v12 training-history-aware prompt.

v12 = v11 + a static TRAINING-HISTORY addendum (fuller and opener), the Vn = V(n-1) +
addendum idiom. It carries every v11 capability plus TRAINING_HISTORY (an additive
superset). The safety floor is invariant by construction: v11 is reused byte-for-byte
as the prefix, so v12 differs from v11 ONLY by the appended training-history addendum,
and v1..v11 / the report chain stay byte-stable. v12 ships INERT (config default v8).
"""

from app.core.config import settings
from app.services.coach.prompts import (
    MESSAGE_PROMPT_PREFIX,
    PROMPT_VERSIONS,
    RECENT_TRAINING_PROMPT_IDS,
    TRAINING_HISTORY_PROMPT_IDS,
    SYSTEM_PROMPT_MESSAGE_V11,
    SYSTEM_PROMPT_MESSAGE_V11_OPENER,
    SYSTEM_PROMPT_MESSAGE_V12,
    SYSTEM_PROMPT_MESSAGE_V12_OPENER,
    _OPENER_PROMPTS,
    _TRAINING_HISTORY_ADDENDUM,
    build_system_prompt,
    is_training_history_prompt,
)
from app.services.coach.service import active_schema_version

V11 = "coach_message_v11"
V12 = "coach_message_v12"


def test_v12_registered_carries_every_v11_capability_plus_training_history():
    assert V12 in PROMPT_VERSIONS
    assert V12.startswith(MESSAGE_PROMPT_PREFIX)  # -> schema 2.0 by prefix
    assert V12 in RECENT_TRAINING_PROMPT_IDS  # inherits v11's capabilities
    assert V12 in TRAINING_HISTORY_PROMPT_IDS  # ...plus the new TRAINING_HISTORY capability
    assert TRAINING_HISTORY_PROMPT_IDS == {
        V12, "coach_message_v13", "coach_message_v14", "coach_message_lean_v1",
        "coach_message_lean_grouped_v1",
    }
    assert V12 in _OPENER_PROMPTS  # has a distinct opener form (two-stage)
    # same schema family as v11 (so v12 reports regenerate, prior history retained).
    assert active_schema_version(V12) == active_schema_version(V11)


def test_v12_is_v11_plus_training_history_addendum():
    assert SYSTEM_PROMPT_MESSAGE_V12 == SYSTEM_PROMPT_MESSAGE_V11 + _TRAINING_HISTORY_ADDENDUM
    assert (
        SYSTEM_PROMPT_MESSAGE_V12_OPENER
        == SYSTEM_PROMPT_MESSAGE_V11_OPENER + _TRAINING_HISTORY_ADDENDUM
    )
    # v11 preserved verbatim as the prefix -> safety floor invariant.
    assert SYSTEM_PROMPT_MESSAGE_V12.startswith(SYSTEM_PROMPT_MESSAGE_V11)
    assert SYSTEM_PROMPT_MESSAGE_V12_OPENER.startswith(SYSTEM_PROMPT_MESSAGE_V11_OPENER)


def test_training_history_addendum_present_in_v12_absent_in_v11():
    assert _TRAINING_HISTORY_ADDENDUM not in SYSTEM_PROMPT_MESSAGE_V11
    assert _TRAINING_HISTORY_ADDENDUM not in SYSTEM_PROMPT_MESSAGE_V11_OPENER
    assert _TRAINING_HISTORY_ADDENDUM in build_system_prompt(V12, mode="fuller", voice=None)
    assert _TRAINING_HISTORY_ADDENDUM in build_system_prompt(V12, mode="opener", voice=None)
    assert _TRAINING_HISTORY_ADDENDUM not in build_system_prompt(V11, mode="fuller", voice=None)
    assert _TRAINING_HISTORY_ADDENDUM not in build_system_prompt(V11, mode="opener", voice=None)


def test_training_history_addendum_holds_the_core_disciplines():
    text = _TRAINING_HISTORY_ADDENDUM.lower()
    assert "training_history" in text
    assert "training_age" in text  # the durability headline
    assert "durability" in text and "permission" in text  # history is durability, not license for more load
    assert "trajectory" in text  # the multi-year ramp read
    assert "never override" in text  # authority boundary held
    assert "safety floor" in text


def test_is_training_history_prompt_resolves():
    assert is_training_history_prompt(V12) is True
    assert is_training_history_prompt(V11) is False
    assert is_training_history_prompt(None) is False


def test_v12_ships_inert():
    """v12 is dormant until the owner flips COACH_PROMPT_ID — the config default is
    unchanged by #561."""
    assert settings.COACH_PROMPT_ID != V12
