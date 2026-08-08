"""Structural pins for the #443 coach_message_v10 stream-view-aware prompt.

v10 = v9 + a static STREAM-VIEW addendum (fuller and opener), the Vn = V(n-1) +
addendum idiom. It carries every v9 capability plus STREAM_VIEW. The safety floor is
invariant by construction: v9 is reused byte-for-byte as the prefix, so v10 differs
from v9 ONLY by the appended timeline addendum, and v1..v9 / the report chain stay
byte-stable. v10 ships INERT (the config default stays v8).
"""

from app.core.config import settings
from app.services.coach.prompts import (
    MESSAGE_PROMPT_PREFIX,
    PROMPT_VERSIONS,
    STREAM_VIEW_PROMPT_IDS,
    SYSTEM_PROMPT_MESSAGE_V9,
    SYSTEM_PROMPT_MESSAGE_V9_OPENER,
    SYSTEM_PROMPT_MESSAGE_V10,
    SYSTEM_PROMPT_MESSAGE_V10_OPENER,
    VOLUME_PROMPT_IDS,
    _OPENER_PROMPTS,
    _STREAM_VIEW_ADDENDUM,
    build_system_prompt,
    is_stream_view_prompt,
)
from app.services.coach.prompt_features import PromptFeature, features_for
from app.services.coach.service import active_schema_version

V9 = "coach_message_v9"
V10 = "coach_message_v10"


def test_v10_registered_carries_every_v9_capability_plus_stream_view():
    assert V10 in PROMPT_VERSIONS
    assert V10.startswith(MESSAGE_PROMPT_PREFIX)  # -> schema 2.0 by prefix
    assert V10 in VOLUME_PROMPT_IDS  # inherits v9's capabilities
    assert V10 in STREAM_VIEW_PROMPT_IDS  # ...plus the new STREAM_VIEW capability
    # Stated as v10's own delta from v9, so a later version joining or declining the
    # capability costs no edit here (#803: the old forward-looking membership set was
    # why #578 had to touch five prior version test files).
    assert V9 not in STREAM_VIEW_PROMPT_IDS  # inert under a rollback to v9
    assert features_for(V10) == features_for(V9) | {PromptFeature.STREAM_VIEW}
    assert V10 in _OPENER_PROMPTS  # has a distinct opener form (two-stage)
    # same schema family as v9 (so v10 reports regenerate, prior history retained).
    assert active_schema_version(V10) == active_schema_version(V9)


def test_v10_is_v9_plus_stream_view_addendum():
    assert SYSTEM_PROMPT_MESSAGE_V10 == SYSTEM_PROMPT_MESSAGE_V9 + _STREAM_VIEW_ADDENDUM
    assert (
        SYSTEM_PROMPT_MESSAGE_V10_OPENER
        == SYSTEM_PROMPT_MESSAGE_V9_OPENER + _STREAM_VIEW_ADDENDUM
    )
    # v9 preserved verbatim as the prefix -> safety floor invariant.
    assert SYSTEM_PROMPT_MESSAGE_V10.startswith(SYSTEM_PROMPT_MESSAGE_V9)
    assert SYSTEM_PROMPT_MESSAGE_V10_OPENER.startswith(SYSTEM_PROMPT_MESSAGE_V9_OPENER)


def test_stream_view_addendum_present_in_v10_absent_in_v9():
    assert _STREAM_VIEW_ADDENDUM not in SYSTEM_PROMPT_MESSAGE_V9
    assert _STREAM_VIEW_ADDENDUM not in SYSTEM_PROMPT_MESSAGE_V9_OPENER
    # And via the builder: v10 carries it, v9 does not (inert under rollback).
    assert _STREAM_VIEW_ADDENDUM in build_system_prompt(V10, mode="fuller")
    assert _STREAM_VIEW_ADDENDUM in build_system_prompt(V10, mode="opener")
    assert _STREAM_VIEW_ADDENDUM not in build_system_prompt(V9, mode="fuller")
    assert _STREAM_VIEW_ADDENDUM not in build_system_prompt(V9, mode="opener")


def test_stream_view_addendum_keeps_timeline_subordinate_to_metrics():
    text = _STREAM_VIEW_ADDENDUM.lower()
    assert "stream_view" in text
    assert "never override" in text and "metric" in text  # authority boundary held
    assert "downsampled" in text or "coarse" in text  # read as shape, not exact


def test_is_stream_view_prompt_resolves():
    assert is_stream_view_prompt(V10) is True
    assert is_stream_view_prompt(V9) is False
    assert is_stream_view_prompt(None) is False


def test_v10_ships_inert():
    """v10 is dormant until the owner flips COACH_PROMPT_ID — the config default is
    unchanged by #443."""
    assert settings.COACH_PROMPT_ID != V10
