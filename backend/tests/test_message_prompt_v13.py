"""Structural pins for the ADR 0025 coach_message_v13 memory-aware prompt.

v13 = v12 + a static RUNNER-MEMORY addendum (fuller and opener), the Vn = V(n-1) +
addendum idiom. It carries every v12 capability plus MEMORY (an additive superset).
The safety floor is invariant by construction: v12 is reused byte-for-byte as the
prefix, so v13 differs from v12 ONLY by the appended memory addendum, and v1..v12 /
the report chain stay byte-stable. v13 ships INERT (config default v8).
"""

from app.core.config import settings
from app.services.coach.prompts import (
    MESSAGE_PROMPT_PREFIX,
    MEMORY_PROMPT_IDS,
    PROMPT_VERSIONS,
    TRAINING_HISTORY_PROMPT_IDS,
    SYSTEM_PROMPT_MESSAGE_V12,
    SYSTEM_PROMPT_MESSAGE_V12_OPENER,
    SYSTEM_PROMPT_MESSAGE_V13,
    SYSTEM_PROMPT_MESSAGE_V13_OPENER,
    _MEMORY_ADDENDUM,
    _OPENER_PROMPTS,
    build_system_prompt,
    is_memory_prompt,
)
from app.services.coach.service import active_schema_version

V12 = "coach_message_v12"
V13 = "coach_message_v13"


def test_v13_registered_carries_every_v12_capability_plus_memory():
    assert V13 in PROMPT_VERSIONS
    assert V13.startswith(MESSAGE_PROMPT_PREFIX)  # -> schema 2.0 by prefix
    assert V13 in TRAINING_HISTORY_PROMPT_IDS  # inherits v12's capabilities
    assert V13 in MEMORY_PROMPT_IDS  # ...plus the new MEMORY capability
    # v13 is where MEMORY was introduced; later superset versions (v14+) carry it too
    # (the additive Vn = V(n-1) + capability chain), so v13 is no longer the SOLE member.
    assert V12 not in MEMORY_PROMPT_IDS
    assert V13 in _OPENER_PROMPTS  # has a distinct opener form (two-stage)
    # same schema family as v12 (so v13 reports regenerate, prior history retained).
    assert active_schema_version(V13) == active_schema_version(V12)


def test_v13_is_v12_plus_memory_addendum():
    assert SYSTEM_PROMPT_MESSAGE_V13 == SYSTEM_PROMPT_MESSAGE_V12 + _MEMORY_ADDENDUM
    assert (
        SYSTEM_PROMPT_MESSAGE_V13_OPENER
        == SYSTEM_PROMPT_MESSAGE_V12_OPENER + _MEMORY_ADDENDUM
    )
    # v12 preserved verbatim as the prefix -> safety floor invariant.
    assert SYSTEM_PROMPT_MESSAGE_V13.startswith(SYSTEM_PROMPT_MESSAGE_V12)
    assert SYSTEM_PROMPT_MESSAGE_V13_OPENER.startswith(SYSTEM_PROMPT_MESSAGE_V12_OPENER)


def test_memory_addendum_present_in_v13_absent_in_v12():
    assert _MEMORY_ADDENDUM not in SYSTEM_PROMPT_MESSAGE_V12
    assert _MEMORY_ADDENDUM not in SYSTEM_PROMPT_MESSAGE_V12_OPENER
    assert _MEMORY_ADDENDUM in build_system_prompt(V13, mode="fuller")
    assert _MEMORY_ADDENDUM in build_system_prompt(V13, mode="opener")
    assert _MEMORY_ADDENDUM not in build_system_prompt(V12, mode="fuller")
    assert _MEMORY_ADDENDUM not in build_system_prompt(V12, mode="opener")


def test_memory_addendum_holds_the_core_disciplines():
    text = _MEMORY_ADDENDUM.lower()
    # G5 authority tiering: stated, citable, yields to measured data, never lowers floor.
    assert "memory" in text
    assert "citable" in text or "you may reference it" in text
    assert "yields to" in text  # yields to this run's measured data
    assert "safety floor" in text
    # G2/G3 non-nag directional discipline.
    assert "time_in_zones" in text and "recent_training" in text and "discount" in text
    assert "never" in text and "nag" in text
    assert "verdict" in text  # no binary acted/ignored verdict


def test_is_memory_prompt_resolves():
    assert is_memory_prompt(V13) is True
    assert is_memory_prompt(V12) is False
    assert is_memory_prompt(None) is False


def test_v13_ships_inert():
    """v13 is dormant until the owner flips COACH_PROMPT_ID — the config default is
    unchanged by ADR 0025."""
    assert settings.COACH_PROMPT_ID != V13
