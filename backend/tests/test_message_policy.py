"""Tests for validate_message_policy — the A3 prose-message policy gate (AC3).

The six rule bodies are already characterised in test_policy_validator.py; these
tests prove validate_message_policy assembles the primitives correctly from the
CoachMessageReport (message + tail), polices the FULL prose+tail surface, and
fires each of the six rules. They reuse the structured suite's pack builder.
"""

from app.schemas.coach import (
    CoachMessageQuestion,
    CoachMessageReport,
    CoachNextStep,
    CoachRisk,
    TappableOption,
)
from app.services.coach.validator import validate_message_policy

from tests.test_policy_validator import _make_pack


def _make_message(**overrides) -> CoachMessageReport:
    """A clean prose-message report with sensible defaults."""
    defaults = {
        "message": "Nice steady aerobic run. You held your pace well across the session.",
        "headline": "Solid easy run",
        "next_steps": [
            CoachNextStep(
                action="Easy run",
                details="30 min",
                why="Recovery",
                evidence=[{"field": "training_context.days_since_last_hard", "value": 1}],
            ),
        ],
        "risks": [],
        "questions": [],
    }
    defaults.update(overrides)
    return CoachMessageReport(**defaults)


class TestValidateMessagePolicy:
    def test_clean_message_no_violations(self):
        violations = validate_message_policy(_make_message(), _make_pack())
        assert violations == []

    # Rule 1 — null check-in with no questions must prompt for input.
    def test_rule1_missing_questions_on_null_checkin(self):
        report = _make_message(questions=[])
        pack = _make_pack(check_in={
            "rpe": None, "pain_score": None, "pain_location": None,
            "sleep_quality": None, "notes": None,
        })
        rules = [v.rule for v in validate_message_policy(report, pack)]
        assert "missing_questions_for_null_checkin" in rules

    def test_rule1_silent_when_tail_has_question(self):
        report = _make_message(questions=[
            CoachMessageQuestion(question="How did you feel?", reason="No check-in data"),
        ])
        pack = _make_pack(check_in={
            "rpe": None, "pain_score": None, "pain_location": None,
            "sleep_quality": None, "notes": None,
        })
        rules = [v.rule for v in validate_message_policy(report, pack)]
        assert "missing_questions_for_null_checkin" not in rules

    # Rule 2 — uncalibrated zones in the PROSE message must fire.
    def test_rule2_zone_reference_in_prose(self):
        report = _make_message(
            message="Keep your HR in Z2 for these recovery runs to build the aerobic base."
        )
        pack = _make_pack(metrics={"zones_calibrated": False, "flags": [], "confidence": "high"})
        rules = [v.rule for v in validate_message_policy(report, pack)]
        assert "uncalibrated_zone_reference" in rules

    def test_rule2_silent_when_calibrated(self):
        report = _make_message(message="Keep your HR in Z2 for recovery.")
        pack = _make_pack(metrics={"zones_calibrated": True, "flags": [], "confidence": "high"})
        rules = [v.rule for v in validate_message_policy(report, pack)]
        assert "uncalibrated_zone_reference" not in rules

    # Rule 3 — a risk referencing a flag not in the metrics flags must fire.
    def test_rule3_invalid_risk_flag(self):
        report = _make_message(
            risks=[CoachRisk(flag="invented_flag", explanation="Bad", mitigation="Fix")],
        )
        pack = _make_pack(metrics={"zones_calibrated": True, "flags": ["pain_reported"], "confidence": "high"})
        rules = [v.rule for v in validate_message_policy(report, pack)]
        assert "invalid_risk_flag" in rules

    def test_rule3_valid_flag_silent(self):
        report = _make_message(
            risks=[CoachRisk(flag="pain_reported", explanation="Pain noted", mitigation="Rest")],
        )
        pack = _make_pack(metrics={"zones_calibrated": True, "flags": ["pain_reported"], "confidence": "high"})
        rules = [v.rule for v in validate_message_policy(report, pack)]
        assert "invalid_risk_flag" not in rules

    # Rule 4 — a specific interval claim in the PROSE under low confidence fires.
    def test_rule4_ungated_interval_claim_in_prose(self):
        report = _make_message(
            message="You completed 8x400m intervals today with strong consistency across every rep."
        )
        pack = _make_pack(metrics={
            "zones_calibrated": True, "flags": [], "confidence": "medium",
            "workout_match": {"match_score": 0.5, "detection_confidence": "low"},
        })
        rules = [v.rule for v in validate_message_policy(report, pack)]
        assert "ungated_interval_claim" in rules

    # Rule 5 — medical overreach anywhere in prose or tail fires.
    def test_rule5_medical_overreach_in_prose(self):
        report = _make_message(message="This is consistent with a stress fracture; rest it.")
        rules = [v.rule for v in validate_message_policy(report, _make_pack())]
        assert "medical_overreach" in rules

    def test_rule5_medical_overreach_in_next_step(self):
        report = _make_message(next_steps=[
            CoachNextStep(action="Take 200mg of iron daily", details="With breakfast", why="Low ferritin"),
        ])
        rules = [v.rule for v in validate_message_policy(report, _make_pack())]
        assert "medical_overreach" in rules

    # Rule 6 — citing a narrative.* evidence path in the tail fires.
    def test_rule6_narrative_evidence_in_tail(self):
        report = _make_message(next_steps=[
            CoachNextStep(
                action="Keep building base",
                details="Easy miles",
                why="Continuity",
                evidence=[{"field": "narrative.tone", "value": "encouraging"}],
            ),
        ])
        rules = [v.rule for v in validate_message_policy(report, _make_pack())]
        assert "narrative_cited_as_fact" in rules

    # Surface-growth proofs: the message report polices fields the structured
    # path did not have a place for (tappable-option labels), so overreach there
    # is still caught.
    def test_overreach_in_tappable_option_label_is_policed(self):
        report = _make_message(questions=[
            CoachMessageQuestion(
                question="How's the knee?",
                reason="Following up on soreness",
                options=[TappableOption(id="o1", label="It is plantar fasciitis", kind="custom")],
            ),
        ])
        rules = [v.rule for v in validate_message_policy(report, _make_pack())]
        assert "medical_overreach" in rules

    def test_zone_reference_in_question_is_policed(self):
        report = _make_message(questions=[
            CoachMessageQuestion(question="Was Z4 sustainable?", reason="Gauging effort"),
        ])
        pack = _make_pack(metrics={"zones_calibrated": False, "flags": [], "confidence": "high"})
        rules = [v.rule for v in validate_message_policy(report, pack)]
        assert "uncalibrated_zone_reference" in rules

    # Degraded tail (empty next_steps/questions/risks) with a clean message: no
    # violations, and rule 1 does not fire when the check-in is populated.
    def test_degraded_tail_clean_message_no_violations(self):
        report = CoachMessageReport(
            message="Good easy run, nothing to flag.", tail_degraded=True,
        )
        assert validate_message_policy(report, _make_pack()) == []

    def test_multiple_rules_fire_together(self):
        report = _make_message(
            message="You nailed 8x400m and should hold HR in Z3.",
            risks=[CoachRisk(flag="fake_flag", explanation="Bad", mitigation="Fix")],
            questions=[],
        )
        pack = _make_pack(
            metrics={
                "zones_calibrated": False, "flags": ["pain_reported"], "confidence": "low",
                "workout_match": {"match_score": 0.4, "detection_confidence": "low"},
            },
            check_in={"rpe": None, "pain_score": None, "pain_location": None, "sleep_quality": None, "notes": None},
        )
        rules = [v.rule for v in validate_message_policy(report, pack)]
        assert "missing_questions_for_null_checkin" in rules
        assert "uncalibrated_zone_reference" in rules
        assert "invalid_risk_flag" in rules
        assert "ungated_interval_claim" in rules
