"""Structural pins for the #578 coach_message_v14 intensity-aware prompt.

v14 = v13 + a static INTENSITY addendum (fuller and opener), the Vn = V(n-1) + addendum
idiom. It carries every v13 capability plus INTENSITY (an additive superset). The safety
floor is invariant by construction: v13 is reused byte-for-byte as the prefix, so v14
differs from v13 ONLY by the appended intensity addendum, and v1..v13 / the report chain
stay byte-stable. v14 ships INERT (config default v8).
"""

from app.core.config import settings
from app.services.coach.prompts import (
    INTENSITY_PROMPT_IDS,
    MEMORY_PROMPT_IDS,
    MESSAGE_PROMPT_PREFIX,
    PROMPT_VERSIONS,
    SYSTEM_PROMPT_MESSAGE_V13,
    SYSTEM_PROMPT_MESSAGE_V13_OPENER,
    SYSTEM_PROMPT_MESSAGE_V14,
    SYSTEM_PROMPT_MESSAGE_V14_OPENER,
    _INTENSITY_ADDENDUM,
    _OPENER_PROMPTS,
    build_system_prompt,
    is_intensity_prompt,
)
from app.services.coach.prompt_features import PromptFeature, features_for
from app.services.coach.service import active_schema_version

V13 = "coach_message_v13"
V14 = "coach_message_v14"


def test_v14_registered_carries_every_v13_capability_plus_intensity():
    assert V14 in PROMPT_VERSIONS
    assert V14.startswith(MESSAGE_PROMPT_PREFIX)  # -> schema 2.0 by prefix
    assert V14 in MEMORY_PROMPT_IDS  # inherits v13's capabilities
    assert V14 in INTENSITY_PROMPT_IDS  # ...plus the new INTENSITY capability
    # Stated as v14's own delta from v13, so a later version joining or declining the
    # capability costs no edit here (#803: #578 shipping v14 is what had to touch five
    # prior version test files, purely to widen their forward-looking membership sets).
    assert V13 not in INTENSITY_PROMPT_IDS  # inert under a rollback to v13
    assert features_for(V14) == features_for(V13) | {PromptFeature.INTENSITY}
    assert V14 in _OPENER_PROMPTS  # has a distinct opener form (two-stage)
    # same schema family as v13 (so v14 reports regenerate, prior history retained).
    assert active_schema_version(V14) == active_schema_version(V13)


def test_v14_is_v13_plus_intensity_addendum():
    assert SYSTEM_PROMPT_MESSAGE_V14 == SYSTEM_PROMPT_MESSAGE_V13 + _INTENSITY_ADDENDUM
    assert (
        SYSTEM_PROMPT_MESSAGE_V14_OPENER
        == SYSTEM_PROMPT_MESSAGE_V13_OPENER + _INTENSITY_ADDENDUM
    )
    # v13 preserved verbatim as the prefix -> safety floor invariant.
    assert SYSTEM_PROMPT_MESSAGE_V14.startswith(SYSTEM_PROMPT_MESSAGE_V13)
    assert SYSTEM_PROMPT_MESSAGE_V14_OPENER.startswith(SYSTEM_PROMPT_MESSAGE_V13_OPENER)


def test_intensity_addendum_present_in_v14_absent_in_v13():
    assert _INTENSITY_ADDENDUM not in SYSTEM_PROMPT_MESSAGE_V13
    assert _INTENSITY_ADDENDUM not in SYSTEM_PROMPT_MESSAGE_V13_OPENER
    assert _INTENSITY_ADDENDUM in build_system_prompt(V14, mode="fuller")
    assert _INTENSITY_ADDENDUM in build_system_prompt(V14, mode="opener")
    assert _INTENSITY_ADDENDUM not in build_system_prompt(V13, mode="fuller")
    assert _INTENSITY_ADDENDUM not in build_system_prompt(V13, mode="opener")


def test_intensity_addendum_holds_the_core_disciplines():
    text = _INTENSITY_ADDENDUM.lower()
    assert "intensity" in text
    assert "easy" in text and "moderate" in text and "hard" in text
    # A fact, never a verdict; confounders exculpatory; never nag; abstain when thin.
    assert "verdict" in text
    assert "confounder" in text or "exculpat" in text
    assert "nag" in text
    # Never overrides the run's re-derived data or the safety floor.
    assert "derivedmetric" in text
    assert "safety floor" in text


def test_is_intensity_prompt_resolves():
    assert is_intensity_prompt(V14) is True
    assert is_intensity_prompt(V13) is False
    assert is_intensity_prompt(None) is False


def test_v14_ships_inert():
    """v14 is dormant until the owner flips COACH_PROMPT_ID — the config default is
    unchanged by #578."""
    assert settings.COACH_PROMPT_ID != V14
