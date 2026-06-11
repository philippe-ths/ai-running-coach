"""Structural tests for the A3 coach_message_v1 prompt (deliverable 3).

The prompt's coaching QUALITY is a live-cutover judgement, not a unit-test claim.
These pins guard the mechanical contract: it is registered, it carries every
substantive safety/grounding discipline the structured rules enforced (so the
validator and eval are not policing a surface the prompt never set up), it
inverts the output (prose first, not JSON), and the legacy v1-v10 prompts stay
byte-stable.
"""

from app.services.coach.prompts import (
    ACTIVITY_PLAYBOOKS,
    MESSAGE_PROMPT_PREFIX,
    PROMPT_VERSIONS,
    SYSTEM_PROMPT_V10,
    build_system_prompt,
)

MESSAGE_ID = "coach_message_v1"


def test_message_prompt_is_registered():
    assert MESSAGE_ID in PROMPT_VERSIONS
    assert MESSAGE_ID.startswith(MESSAGE_PROMPT_PREFIX)


def test_message_prompt_inverts_output_no_json_only():
    prompt = PROMPT_VERSIONS[MESSAGE_ID]
    # The whole point of A3: the coach writes prose, not "ONLY valid JSON".
    assert "Output ONLY valid JSON" not in prompt
    assert "JSON SCHEMA:" not in prompt
    # It commits to the three-movement protocol and the single tail call.
    assert "record_coach_tail" in prompt
    assert "WRITE THE MESSAGE" in prompt


def test_message_prompt_carries_safety_disciplines():
    """Every code-enforced rule must be set up by the prompt, else the validator
    polices a surface the prompt never asked the model to respect."""
    prompt = PROMPT_VERSIONS[MESSAGE_ID].lower()
    # Rule 2 (validator: uncalibrated zones)
    assert "zones_calibrated" in prompt and "z1" in prompt
    # Rule 4/13 (validator: ungated interval claim)
    assert "detection_confidence" in prompt
    # Rule 5 (validator: medical scope)
    assert "diagnos" in prompt and "general-wellness" in prompt
    # Rule 6 (validator: narrative is not evidence)
    assert "never cite" in prompt or "never cited" in prompt


def test_message_prompt_carries_substantive_coaching_disciplines():
    prompt = PROMPT_VERSIONS[MESSAGE_ID].lower()
    for marker in (
        "discount_signals",       # confound discounting (eval assertion 2)
        "effort_score",           # load-not-intensity (eval assertion 7)
        "advance",                # advance-not-restate (eval assertion 4)
        "baseline_trend",         # abstain-on-thin-trend (eval assertion 5)
        "adherence",              # advise-not-nag
        "believed_facts",         # runner-model
        "preference_profile",     # framing
        "perceived_effort",       # RPE vs HR
        "interval_structure",     # coach-the-data (eval assertion 8)
        "recorded_laps",          # never advise the lap button already used
    ):
        assert marker in prompt, f"missing discipline marker: {marker}"


def test_message_prompt_states_headline_bound():
    # The strict tool subset forbids string maxLength, so the 80-char headline
    # bound must live in the prompt.
    assert "80 character" in PROMPT_VERSIONS[MESSAGE_ID]


def test_build_system_prompt_appends_playbook_to_message_prompt():
    prompt = build_system_prompt(MESSAGE_ID, "Intervals")
    assert prompt.startswith(PROMPT_VERSIONS[MESSAGE_ID])
    assert ACTIVITY_PLAYBOOKS["Intervals"] in prompt


def test_build_system_prompt_message_no_playbook_is_bare():
    assert build_system_prompt(MESSAGE_ID, None) == PROMPT_VERSIONS[MESSAGE_ID]
    assert build_system_prompt(MESSAGE_ID, "Unknown") == PROMPT_VERSIONS[MESSAGE_ID]


def test_legacy_v10_byte_stable():
    # A3 must not touch the cached legacy prompts.
    assert PROMPT_VERSIONS["coach_report_v10"] == SYSTEM_PROMPT_V10
    assert PROMPT_VERSIONS["coach_report_v10"].startswith(
        "You are a running coach assistant."
    )
