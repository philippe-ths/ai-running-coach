"""Tests for the coach policy validator."""

from app.schemas.coach import CoachReportContent, CoachTakeaway, CoachNextStep, CoachRisk, CoachQuestion
from app.services.coach.validator import validate_policy


def _make_content(**overrides):
    """Build a valid CoachReportContent with sensible defaults."""
    defaults = {
        "key_takeaways": [
            CoachTakeaway(text="Good effort.", evidence=[{"field": "metrics.effort_score", "value": 3.5}]),
            CoachTakeaway(text="Pace was steady.", evidence=[{"field": "metrics.pace_variability", "value": 8.2}]),
        ],
        "next_steps": [
            CoachNextStep(action="Easy run", details="30 min", why="Recovery", evidence=[{"field": "training_context.days_since_last_hard", "value": 1}]),
        ],
        "risks": [],
        "questions": [],
    }
    defaults.update(overrides)
    return CoachReportContent(**defaults)


def _make_pack(**overrides):
    """Build a minimal context pack with sensible defaults."""
    pack = {
        "metrics": {
            "zones_calibrated": True,
            "flags": [],
            "confidence": "high",
        },
        "check_in": {
            "rpe": 6,
            "pain_score": 0,
            "pain_location": None,
            "sleep_quality": 4,
            "notes": None,
        },
    }
    for key, val in overrides.items():
        if key in pack:
            pack[key].update(val)
        else:
            pack[key] = val
    return pack


class TestPolicyValidator:
    def test_valid_report_no_violations(self):
        content = _make_content()
        pack = _make_pack()
        violations = validate_policy(content, pack)
        assert violations == []

    def test_violation_null_checkin_no_questions(self):
        content = _make_content(questions=[])
        pack = _make_pack(check_in={
            "rpe": None, "pain_score": None, "pain_location": None,
            "sleep_quality": None, "notes": None,
        })
        violations = validate_policy(content, pack)
        assert len(violations) == 1
        assert violations[0].rule == "missing_questions_for_null_checkin"

    def test_no_violation_when_checkin_null_but_questions_present(self):
        content = _make_content(questions=[
            CoachQuestion(question="How did you feel?", reason="No check-in data"),
        ])
        pack = _make_pack(check_in={
            "rpe": None, "pain_score": None, "pain_location": None,
            "sleep_quality": None, "notes": None,
        })
        violations = validate_policy(content, pack)
        rules = [v.rule for v in violations]
        assert "missing_questions_for_null_checkin" not in rules

    def test_violation_uncalibrated_zones(self):
        content = _make_content(
            key_takeaways=[
                CoachTakeaway(text="Keep HR in Z2 for recovery.", evidence=[{"field": "metrics.time_in_zones.Z2", "value": 1200}]),
                CoachTakeaway(text="Good work.", evidence=[{"field": "metrics.effort_score", "value": 3.0}]),
            ],
        )
        pack = _make_pack(metrics={"zones_calibrated": False, "flags": [], "confidence": "high"})
        violations = validate_policy(content, pack)
        assert len(violations) == 1
        assert violations[0].rule == "uncalibrated_zone_reference"

    def test_no_violation_when_calibrated(self):
        content = _make_content(
            key_takeaways=[
                CoachTakeaway(text="Keep HR in Z2.", evidence=[{"field": "metrics.time_in_zones.Z2", "value": 1200}]),
                CoachTakeaway(text="Good pace.", evidence=[{"field": "metrics.pace_variability", "value": 5.0}]),
            ],
        )
        pack = _make_pack(metrics={"zones_calibrated": True, "flags": [], "confidence": "high"})
        violations = validate_policy(content, pack)
        rules = [v.rule for v in violations]
        assert "uncalibrated_zone_reference" not in rules

    def test_no_violation_uncalibrated_but_no_zone_refs(self):
        content = _make_content(
            key_takeaways=[
                CoachTakeaway(text="Keep at easy conversational pace.", evidence=[{"field": "metrics.effort_score", "value": 2.0}]),
                CoachTakeaway(text="Good recovery run.", evidence=[{"field": "metrics.activity_class", "value": "Easy Run"}]),
            ],
        )
        pack = _make_pack(metrics={"zones_calibrated": False, "flags": [], "confidence": "high"})
        violations = validate_policy(content, pack)
        rules = [v.rule for v in violations]
        assert "uncalibrated_zone_reference" not in rules

    def test_violation_invalid_risk_flag(self):
        content = _make_content(
            risks=[CoachRisk(flag="invented_flag", explanation="Bad", mitigation="Fix")],
        )
        pack = _make_pack(metrics={"zones_calibrated": True, "flags": ["pain_reported"], "confidence": "high"})
        violations = validate_policy(content, pack)
        assert len(violations) == 1
        assert violations[0].rule == "invalid_risk_flag"

    def test_valid_risk_flag_in_array(self):
        content = _make_content(
            risks=[CoachRisk(flag="pain_reported", explanation="Pain noted", mitigation="Rest")],
        )
        pack = _make_pack(metrics={"zones_calibrated": True, "flags": ["pain_reported"], "confidence": "high"})
        violations = validate_policy(content, pack)
        rules = [v.rule for v in violations]
        assert "invalid_risk_flag" not in rules

    def test_multiple_violations(self):
        """Multiple rules can fire at once."""
        content = _make_content(
            key_takeaways=[
                CoachTakeaway(text="Stay in Z1.", evidence=[{"field": "metrics.time_in_zones.Z1", "value": 800}]),
                CoachTakeaway(text="Good work.", evidence=[{"field": "metrics.effort_score", "value": 3.0}]),
            ],
            risks=[CoachRisk(flag="fake_flag", explanation="Bad", mitigation="Fix")],
            questions=[],
        )
        pack = _make_pack(
            metrics={"zones_calibrated": False, "flags": ["pain_reported"], "confidence": "high"},
            check_in={"rpe": None, "pain_score": None, "pain_location": None, "sleep_quality": None, "notes": None},
        )
        violations = validate_policy(content, pack)
        rules = [v.rule for v in violations]
        assert "missing_questions_for_null_checkin" in rules
        assert "uncalibrated_zone_reference" in rules
        assert "invalid_risk_flag" in rules

    def test_violation_ungated_interval_claim_low_confidence(self):
        """LLM claiming '8x400' with low detection confidence should violate."""
        content = _make_content(
            key_takeaways=[
                CoachTakeaway(text="You completed 8x400m intervals with good consistency."),
                CoachTakeaway(text="Strong session."),
            ],
        )
        pack = _make_pack(
            metrics={
                "zones_calibrated": True, "flags": [], "confidence": "medium",
                "workout_match": {
                    "match_score": 0.5,
                    "detection_confidence": "low",
                    "confidence_reasons": ["high_rep_distance_variability"],
                },
            },
        )
        violations = validate_policy(content, pack)
        rules = [v.rule for v in violations]
        assert "ungated_interval_claim" in rules

    def test_no_violation_interval_claim_high_confidence(self):
        """LLM claiming intervals with high detection confidence is fine."""
        content = _make_content(
            key_takeaways=[
                CoachTakeaway(text="You completed 8x400m intervals with good consistency."),
                CoachTakeaway(text="Strong session."),
            ],
        )
        pack = _make_pack(
            metrics={
                "zones_calibrated": True, "flags": [], "confidence": "high",
                "workout_match": {
                    "match_score": 0.9,
                    "detection_confidence": "high",
                    "confidence_reasons": [],
                },
            },
        )
        violations = validate_policy(content, pack)
        rules = [v.rule for v in violations]
        assert "ungated_interval_claim" not in rules

    def test_no_violation_no_workout_match(self):
        """No workout_match in context pack → no interval gating check."""
        content = _make_content(
            key_takeaways=[
                CoachTakeaway(text="Good interval session."),
                CoachTakeaway(text="Consistent pacing."),
            ],
        )
        pack = _make_pack()
        violations = validate_policy(content, pack)
        rules = [v.rule for v in violations]
        assert "ungated_interval_claim" not in rules
