"""Wiring + invariant pins for coach_message_lean_grouped_v1 (ADR 0026 Slice 1, #672).

The grouped-pack variant of lean_v1: it receives the SAME pack CONTENT re-nested into
the five coaching-question groups (service serves pack.to_grouped_dict()). The prompt
is lean_v1 with exactly two navigational changes — a group orientation and the two
continuity.* dotted paths re-anchored under our_thread — so the safety floor is
invariant BY CONSTRUCTION. These tests pin exactly that.
"""

from app.core.config import settings
from app.services.coach import prompts
from app.services.coach.prompt_features import features_for
from app.services.coach.prompts import (
    MESSAGE_PROMPT_PREFIX,
    PROMPT_VERSIONS,
    SYSTEM_PROMPT_MESSAGE_LEAN_V1,
    SYSTEM_PROMPT_MESSAGE_LEAN_V1_OPENER,
    SYSTEM_PROMPT_MESSAGE_LEAN_GROUPED_V1,
    SYSTEM_PROMPT_MESSAGE_LEAN_GROUPED_V1_OPENER,
    _OPENER_PROMPTS,
    build_system_prompt,
    is_grouped_pack_prompt,
)
from app.services.coach.service import active_schema_version

GROUPED = "coach_message_lean_grouped_v1"
LEAN = "coach_message_lean_v1"
V14 = "coach_message_v14"


def test_grouped_registered_in_message_family_and_flagged_grouped():
    assert GROUPED in PROMPT_VERSIONS
    assert GROUPED in _OPENER_PROMPTS
    assert GROUPED.startswith(MESSAGE_PROMPT_PREFIX)
    assert active_schema_version(GROUPED) == active_schema_version(LEAN)
    # The grouped-serving flag is on for GROUPED and off for the flat prompts.
    assert is_grouped_pack_prompt(GROUPED)
    assert not is_grouped_pack_prompt(LEAN)
    assert not is_grouped_pack_prompt(V14)
    assert not is_grouped_pack_prompt(None)


def test_grouped_has_full_capability_parity_with_lean_v1():
    """Same gated sections -> identical pack CONTENT. The A/B isolates the pack SHAPE
    (+ the orientation), exactly as lean_v1 isolates the prose vs v14."""
    assert features_for(GROUPED) == features_for(LEAN)


def test_grouped_is_lean_v1_plus_orientation_and_continuity_repath_only():
    """The load-bearing invariant: the grouped prompt is lean_v1 with ONLY the group
    orientation inserted and the two continuity.* paths re-anchored under our_thread.
    Reverting both changes reproduces lean_v1 BYTE-FOR-BYTE, so every safety-critical
    line (the lane, the misread numbers, the truth rule, the worked examples) is
    unchanged and the floor cannot have drifted."""
    # Pinned as a pure derivation of lean_v1.
    assert SYSTEM_PROMPT_MESSAGE_LEAN_GROUPED_V1 == prompts._regroup_lean_prompt(
        SYSTEM_PROMPT_MESSAGE_LEAN_V1
    )
    # Reverting the two navigational changes yields lean_v1 exactly.
    reverted = SYSTEM_PROMPT_MESSAGE_LEAN_GROUPED_V1.replace(
        prompts._GROUP_ORIENTATION, ""
    ).replace("our_thread.continuity", "continuity")
    assert reverted == SYSTEM_PROMPT_MESSAGE_LEAN_V1


def test_grouped_opener_is_lean_v1_opener_plus_orientation_only():
    assert SYSTEM_PROMPT_MESSAGE_LEAN_GROUPED_V1_OPENER == prompts._regroup_lean_opener_prompt(
        SYSTEM_PROMPT_MESSAGE_LEAN_V1_OPENER
    )
    reverted = SYSTEM_PROMPT_MESSAGE_LEAN_GROUPED_V1_OPENER.replace(
        prompts._GROUP_ORIENTATION_OPENER, ""
    )
    assert reverted == SYSTEM_PROMPT_MESSAGE_LEAN_V1_OPENER


def test_grouped_orientation_names_the_five_coaching_question_groups():
    text = SYSTEM_PROMPT_MESSAGE_LEAN_GROUPED_V1
    assert "How your context is organized" in text
    for group in ("this_run", "right_now", "the_runner", "our_thread", "how_to_coach"):
        assert f"`{group}`" in text
    # continuity is re-anchored under our_thread; the bare flat path is gone.
    assert "our_thread.continuity.opener_message" in text
    assert "our_thread.continuity.reply" in text


def test_grouped_keeps_the_load_bearing_safety_surface_verbatim():
    """Defense in depth over the derivation pin: the safety lane, the misread-number
    facts, and the truth rule appear verbatim in the grouped prompt."""
    lean = SYSTEM_PROMPT_MESSAGE_LEAN_V1
    grouped = SYSTEM_PROMPT_MESSAGE_LEAN_GROUPED_V1
    for anchor in (
        "Stay in general-wellness coaching.",
        "`effort_score` is cumulative training LOAD",
        "`discount_signals` is authoritative.",
        "When `zones_calibrated` is false",
        "For acute pain (pain_score >= 7)",
    ):
        assert anchor in lean and anchor in grouped, anchor


def test_grouped_builds_for_both_modes():
    fuller = build_system_prompt(GROUPED, mode="fuller", voice=None)
    opener = build_system_prompt(GROUPED, mode="opener", voice=None)
    assert SYSTEM_PROMPT_MESSAGE_LEAN_GROUPED_V1 in fuller
    assert SYSTEM_PROMPT_MESSAGE_LEAN_GROUPED_V1_OPENER in opener
    assert "YOUR VOICE FOR THIS RUNNER" in fuller  # voice-aware, like lean_v1


def test_grouped_ships_inert():
    assert settings.COACH_PROMPT_ID != GROUPED


def test_message_prompt_lean_grouped_v6_extends_laps_rule_to_past_sessions():
    """#712: grouped_v6 = grouped_v5's fuller prose with the recorded-laps discipline
    extended to a past session pulled from recent_weeks. It differs from grouped_v5 ONLY
    in that one laps clause; the opener is byte-identical; it serves the grouped pack."""
    v5 = prompts.PROMPT_VERSIONS["coach_message_lean_grouped_v5"]
    v6 = prompts.PROMPT_VERSIONS["coach_message_lean_grouped_v6"]
    # v6 differs from v5 ONLY by raising the laps clause from this-run to the class
    # (any runner-recorded session, this run or a past one revisited).
    assert v6 != v5
    assert v6.replace(prompts._LEAN_LAPS_RULE_WITH_PAST, prompts._LEAN_LAPS_RULE_SUBJECT_ONLY) == v5
    assert "an earlier one you're revisiting" in v6
    assert "an earlier one you're revisiting" not in v5
    # opener byte-identical (no lap clause in the opener).
    assert (
        prompts._OPENER_PROMPTS["coach_message_lean_grouped_v6"]
        == prompts._OPENER_PROMPTS["coach_message_lean_grouped_v5"]
    )
    # grouped serialization + feature parity with the flip target.
    assert "coach_message_lean_grouped_v6" in prompts.GROUPED_PACK_PROMPT_IDS
