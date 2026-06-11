"""Structural tests for the A4 coach_message_v2 two-mode prompt (deliverable 2).

The prompt's coaching QUALITY is a live-cutover judgement, not a unit-test claim.
These pins guard the mechanical contract: v2 is registered in the message family;
the FULLER mode carries every discipline v1 set up PLUS a continuity rule; the
OPENER mode is lean but still carries the full SAFETY block (its prose is policed
exactly as the fuller turn, AC3) and the schedule/RPE-pain instructions; and the
v1 + v10 prompts stay byte-stable (A4 must not touch cached prompts).
"""

from app.services.coach.prompts import (
    ACTIVITY_PLAYBOOKS,
    MESSAGE_PROMPT_PREFIX,
    PROMPT_VERSIONS,
    SYSTEM_PROMPT_MESSAGE_V1,
    SYSTEM_PROMPT_MESSAGE_V2,
    SYSTEM_PROMPT_MESSAGE_V2_OPENER,
    SYSTEM_PROMPT_V10,
    TWO_STAGE_PROMPT_ID,
    build_system_prompt,
)

V2 = "coach_message_v2"


def test_v2_is_registered_in_message_family():
    assert V2 in PROMPT_VERSIONS
    assert V2.startswith(MESSAGE_PROMPT_PREFIX)
    assert TWO_STAGE_PROMPT_ID == V2


def test_v1_and_v10_byte_stable():
    # A4 must not touch cached prompts (rollback / prior cached rows).
    assert PROMPT_VERSIONS["coach_message_v1"] == SYSTEM_PROMPT_MESSAGE_V1
    assert PROMPT_VERSIONS["coach_report_v10"] == SYSTEM_PROMPT_V10


def test_fuller_is_v1_plus_continuity():
    # Fuller mode = v1 verbatim + a continuity addendum, so every v1 discipline is
    # preserved and only the continuity rule is added (the byte-stable-chain idiom).
    assert SYSTEM_PROMPT_MESSAGE_V2.startswith(SYSTEM_PROMPT_MESSAGE_V1)
    addendum = SYSTEM_PROMPT_MESSAGE_V2[len(SYSTEM_PROMPT_MESSAGE_V1):].lower()
    assert "fuller turn" in addendum
    assert "opener" in addendum
    assert "continuity.reply" in addendum
    assert "advance" in addendum


def test_fuller_carries_all_v1_disciplines():
    prompt = PROMPT_VERSIONS[V2].lower()
    for marker in (
        "zones_calibrated", "z1", "detection_confidence", "diagnos",
        "general-wellness", "discount_signals", "effort_score", "advance",
        "baseline_trend", "adherence", "believed_facts", "preference_profile",
        "perceived_effort", "interval_structure", "recorded_laps", "record_coach_tail",
    ):
        assert marker in prompt, f"fuller missing discipline marker: {marker}"


def test_opener_is_lean_but_carries_safety():
    opener = SYSTEM_PROMPT_MESSAGE_V2_OPENER.lower()
    # opener-specific instructions
    assert "opener" in opener
    assert "schedule_fuller_turn" in opener
    assert "rpe" in opener and "pain" in opener
    assert "salience.novelty" in opener
    assert "record_coach_tail" in opener
    # the full SAFETY block is still present (the opener prose is policed)
    assert "zones_calibrated" in opener and "z1" in opener
    assert "diagnos" in opener and "general-wellness" in opener
    assert "detection_confidence" in opener


def test_opener_makes_no_commitments():
    opener = SYSTEM_PROMPT_MESSAGE_V2_OPENER.lower()
    # The opener gives no advice / next_steps (the fuller turn owns those).
    assert "no next_steps" in opener or "do not emit next_steps" in opener.replace("not ", "not ")
    assert "reaction" in opener


def test_opener_is_shorter_than_fuller():
    # Lean: the opener prompt is materially smaller than the deep fuller prompt.
    assert len(SYSTEM_PROMPT_MESSAGE_V2_OPENER) < len(SYSTEM_PROMPT_MESSAGE_V2)


def test_build_system_prompt_fuller_mode_is_default_and_appends_playbook():
    # Default mode is fuller; the playbook appends as for any message prompt.
    assert build_system_prompt(V2) == PROMPT_VERSIONS[V2]
    withpb = build_system_prompt(V2, "Intervals")
    assert withpb.startswith(PROMPT_VERSIONS[V2])
    assert ACTIVITY_PLAYBOOKS["Intervals"] in withpb


def test_build_system_prompt_opener_mode_returns_lean_opener_no_playbook():
    opener = build_system_prompt(V2, "Intervals", mode="opener")
    assert opener == SYSTEM_PROMPT_MESSAGE_V2_OPENER
    # The playbook is NOT appended to the lean opener.
    assert ACTIVITY_PLAYBOOKS["Intervals"] not in opener


def test_mode_ignored_for_non_two_stage_prompts():
    # mode is only meaningful for coach_message_v2; everything else is unaffected.
    assert build_system_prompt("coach_message_v1", mode="opener") == SYSTEM_PROMPT_MESSAGE_V1
    assert build_system_prompt("coach_report_v10", mode="opener") == SYSTEM_PROMPT_V10
