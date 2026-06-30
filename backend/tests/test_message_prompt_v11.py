"""Structural pins for the #444 coach_message_v11 recent-training-aware prompt.

v11 = v10 + a static RECENT-TRAINING addendum (fuller and opener), the Vn = V(n-1) +
addendum idiom. It carries every v10 capability plus RECENT_TRAINING (an additive
superset). The safety floor is invariant by construction: v10 is reused byte-for-byte
as the prefix, so v11 differs from v10 ONLY by the appended recent-training addendum,
and v1..v10 / the report chain stay byte-stable. v11 ships INERT (config default v8).
"""

from app.core.config import settings
from app.services.coach.prompts import (
    MESSAGE_PROMPT_PREFIX,
    PROMPT_VERSIONS,
    RECENT_TRAINING_PROMPT_IDS,
    STREAM_VIEW_PROMPT_IDS,
    SYSTEM_PROMPT_MESSAGE_V10,
    SYSTEM_PROMPT_MESSAGE_V10_OPENER,
    SYSTEM_PROMPT_MESSAGE_V11,
    SYSTEM_PROMPT_MESSAGE_V11_OPENER,
    _OPENER_PROMPTS,
    _RECENT_TRAINING_ADDENDUM,
    build_system_prompt,
    is_recent_training_prompt,
)
from app.services.coach.service import active_schema_version

V10 = "coach_message_v10"
V11 = "coach_message_v11"


def test_v11_registered_carries_every_v10_capability_plus_recent_training():
    assert V11 in PROMPT_VERSIONS
    assert V11.startswith(MESSAGE_PROMPT_PREFIX)  # -> schema 2.0 by prefix
    assert V11 in STREAM_VIEW_PROMPT_IDS  # inherits v10's capabilities
    assert V11 in RECENT_TRAINING_PROMPT_IDS  # ...plus the new RECENT_TRAINING capability
    assert RECENT_TRAINING_PROMPT_IDS == {
        V11, "coach_message_v12", "coach_message_v13", "coach_message_v14",
    }
    assert V11 in _OPENER_PROMPTS  # has a distinct opener form (two-stage)
    # same schema family as v10 (so v11 reports regenerate, prior history retained).
    assert active_schema_version(V11) == active_schema_version(V10)


def test_v11_is_v10_plus_recent_training_addendum():
    assert SYSTEM_PROMPT_MESSAGE_V11 == SYSTEM_PROMPT_MESSAGE_V10 + _RECENT_TRAINING_ADDENDUM
    assert (
        SYSTEM_PROMPT_MESSAGE_V11_OPENER
        == SYSTEM_PROMPT_MESSAGE_V10_OPENER + _RECENT_TRAINING_ADDENDUM
    )
    # v10 preserved verbatim as the prefix -> safety floor invariant.
    assert SYSTEM_PROMPT_MESSAGE_V11.startswith(SYSTEM_PROMPT_MESSAGE_V10)
    assert SYSTEM_PROMPT_MESSAGE_V11_OPENER.startswith(SYSTEM_PROMPT_MESSAGE_V10_OPENER)


def test_recent_training_addendum_present_in_v11_absent_in_v10():
    assert _RECENT_TRAINING_ADDENDUM not in SYSTEM_PROMPT_MESSAGE_V10
    assert _RECENT_TRAINING_ADDENDUM not in SYSTEM_PROMPT_MESSAGE_V10_OPENER
    assert _RECENT_TRAINING_ADDENDUM in build_system_prompt(V11, mode="fuller", voice=None)
    assert _RECENT_TRAINING_ADDENDUM in build_system_prompt(V11, mode="opener", voice=None)
    assert _RECENT_TRAINING_ADDENDUM not in build_system_prompt(V10, mode="fuller", voice=None)
    assert _RECENT_TRAINING_ADDENDUM not in build_system_prompt(V10, mode="opener", voice=None)


def test_recent_training_addendum_holds_the_core_disciplines():
    text = _RECENT_TRAINING_ADDENDUM.lower()
    assert "recent_training" in text
    assert "modality" in text or "by_type" in text  # read the type mix, not all runs
    assert "basis" in text  # cite a comparison with its basis
    assert "down" in text and "intentional" in text  # the anti-nag discipline
    assert "never override" in text  # authority boundary held


def test_is_recent_training_prompt_resolves():
    assert is_recent_training_prompt(V11) is True
    assert is_recent_training_prompt(V10) is False
    assert is_recent_training_prompt(None) is False


def test_v11_ships_inert():
    """v11 is dormant until the owner flips COACH_PROMPT_ID — the config default is
    unchanged by #444."""
    assert settings.COACH_PROMPT_ID != V11
