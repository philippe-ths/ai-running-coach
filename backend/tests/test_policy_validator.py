"""Tests for the coach policy validator."""

from app.schemas.coach import CoachReportContent, CoachTakeaway, CoachNextStep, CoachRisk, CoachQuestion
from app.schemas.coach_context import CoachContextPack
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


_DEFAULT_PACK_DICT = {
    "activity": {
        "date": "2026-02-15T10:00:00+00:00", "name": "Run", "type": "Run",
        "distance_m": 10000, "moving_time_s": 3600,
        "avg_hr": 150.0, "max_hr": 175.0, "avg_cadence": 170.0, "elev_gain_m": 50.0,
    },
    "metrics": {
        "headline": "Easy run", "effort": "easy", "duration_class": "standard",
        "structure": "continuous", "is_hilly": False, "is_race": False,
        "effort_score": 3.0,
        "hr_drift": None, "pace_variability": None,
        "flags": [], "confidence": "high", "confidence_reasons": [],
        "time_in_zones": None, "zones_calibrated": True, "zones_basis": "user_user_entered",
        "efficiency_analysis": None, "stops_analysis": None, "interval_structure": None,
        "workout_match": None, "interval_kpis": None,
        "risk_level": None, "risk_score": None, "risk_reasons": [],
        "training_context": None, "discount_signals": None,
    },
    "check_in": {
        "rpe": 6, "pain_score": 0, "pain_location": None, "sleep_quality": 4, "notes": None,
    },
    "profile": {
        "goal_type": None, "experience_level": None, "weekly_days_available": None,
        "injury_notes": None, "max_hr": None, "max_hr_source": None, "current_weekly_km": None,
    },
    "recent_training_summary": {
        "last_7d": {"activity_count": 0, "total_distance_m": 0, "total_moving_time_s": 0, "total_effort": 0.0},
        "last_28d": {"activity_count": 0, "total_distance_m": 0, "total_moving_time_s": 0, "total_effort": 0.0},
        "previous_28d": {"activity_count": 0, "total_distance_m": 0, "total_moving_time_s": 0, "total_effort": 0.0},
    },
    "longitudinal": {"prior_reports": [], "baseline_trend": None},
    "perceived_effort": {
        "rpe": None, "effort_axis": "easy", "effort_score": 3.0,
        "divergence": None, "divergence_direction": None,
        "hr_confounded": False, "recommended_weighting": "hr_only", "pain_trend": None,
    },
    "safety_rules": {"never_diagnose": True, "pain_severe_threshold": 7, "no_invented_facts": True},
}


def _make_pack(**section_overrides) -> CoachContextPack:
    """Build a CoachContextPack from a default pack, with shallow per-section overrides."""
    pack = {key: dict(value) if isinstance(value, dict) else value for key, value in _DEFAULT_PACK_DICT.items()}
    for section, override in section_overrides.items():
        pack[section].update(override)
    return CoachContextPack.model_validate(pack)


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
                CoachTakeaway(text="Good recovery run.", evidence=[{"field": "metrics.headline", "value": "Easy run"}]),
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


class TestMedicalScopeRule:
    """Rule 5 (N2): reject dose advice, diagnosis verbs, and escalation of a
    single wearable number into a health claim. Permit interpretive metric
    correction and the (future M9) non-diagnostic referral nudge.

    Oracle: the medical-scope boundary stated in
    docs/coach-report-improvement-plan.md section 8.
    """

    def test_dose_advice_in_milligrams_rejected(self):
        content = _make_content(
            next_steps=[CoachNextStep(
                action="Supplement iron",
                details="Take 200mg of iron daily for the next month.",
                why="To address the drift.",
            )],
        )
        violations = validate_policy(content, _make_pack())
        assert "medical_overreach" in [v.rule for v in violations]

    def test_dosage_word_rejected(self):
        content = _make_content(
            next_steps=[CoachNextStep(
                action="Adjust meds",
                details="Increase your dosage before hard sessions.",
                why="Performance.",
            )],
        )
        violations = validate_policy(content, _make_pack())
        assert "medical_overreach" in [v.rule for v in violations]

    def test_diagnosis_verb_rejected(self):
        content = _make_content(
            key_takeaways=[
                CoachTakeaway(text="This drift pattern lets me diagnose overtraining."),
                CoachTakeaway(text="Strong session."),
            ],
        )
        violations = validate_policy(content, _make_pack())
        assert "medical_overreach" in [v.rule for v in violations]

    def test_wearable_number_escalated_to_health_claim_rejected(self):
        content = _make_content(
            key_takeaways=[
                CoachTakeaway(text="Your resting HR of 60 means you have a heart condition."),
                CoachTakeaway(text="Take it easy."),
            ],
        )
        violations = validate_policy(content, _make_pack())
        assert "medical_overreach" in [v.rule for v in violations]

    def test_named_condition_assertion_rejected(self):
        content = _make_content(
            key_takeaways=[
                CoachTakeaway(text="Based on this elevated HR, you are anemic."),
                CoachTakeaway(text="Rest up."),
            ],
        )
        violations = validate_policy(content, _make_pack())
        assert "medical_overreach" in [v.rule for v in violations]

    def test_directive_medication_advice_rejected(self):
        content = _make_content(
            next_steps=[CoachNextStep(
                action="Medication",
                details="Stop taking your beta-blocker before your race.",
                why="It blunts heart rate.",
            )],
        )
        violations = validate_policy(content, _make_pack())
        assert "medical_overreach" in [v.rule for v in violations]

    # --- permitted: interpretive correction and non-diagnostic referral ---

    def test_interpretive_heat_correction_permitted(self):
        content = _make_content(
            key_takeaways=[
                CoachTakeaway(text="Discount this HR drift: it was 28C, and heat inflates HR, so the drift overstates fatigue."),
                CoachTakeaway(text="Solid easy run."),
            ],
        )
        violations = validate_policy(content, _make_pack())
        assert "medical_overreach" not in [v.rule for v in violations]

    def test_medication_interpretive_context_permitted(self):
        content = _make_content(
            key_takeaways=[
                CoachTakeaway(text="Your HR reads a few bpm high because you noted a stimulant this week, so this drift overstates fatigue."),
                CoachTakeaway(text="Keep the easy days easy."),
            ],
        )
        violations = validate_policy(content, _make_pack())
        assert "medical_overreach" not in [v.rule for v in violations]

    def test_non_diagnostic_referral_nudge_permitted(self):
        """The M9 referral layer must survive: a 'consider seeing a clinician'
        nudge with no dose, diagnosis verb, or asserted condition is permitted."""
        content = _make_content(
            next_steps=[CoachNextStep(
                action="Consider checking in with a clinician",
                details="If your resting HR stays elevated for two weeks, it is worth seeing a doctor.",
                why="A persistent pattern is worth a professional look.",
            )],
        )
        violations = validate_policy(content, _make_pack())
        assert "medical_overreach" not in [v.rule for v in violations]

    def test_nutrition_grams_permitted(self):
        """Sports-nutrition framing in grams is not a pharmaceutical dose."""
        content = _make_content(
            next_steps=[CoachNextStep(
                action="Fuel the long run",
                details="Aim for 60g of carbs per hour on runs over 90 minutes.",
                why="Glycogen.",
            )],
        )
        violations = validate_policy(content, _make_pack())
        assert "medical_overreach" not in [v.rule for v in violations]

    def test_preventive_condition_mention_permitted(self):
        """Naming a condition to prevent it (not asserting the runner has it) is fine."""
        content = _make_content(
            next_steps=[CoachNextStep(
                action="Eat iron-rich foods",
                details="To avoid iron deficiency, include leafy greens and red meat.",
                why="General nutrition.",
            )],
        )
        violations = validate_policy(content, _make_pack())
        assert "medical_overreach" not in [v.rule for v in violations]

    def test_negated_condition_not_flagged(self):
        content = _make_content(
            key_takeaways=[
                CoachTakeaway(text="Your numbers look clean: no signs of overtraining syndrome here."),
                CoachTakeaway(text="Nice work."),
            ],
        )
        violations = validate_policy(content, _make_pack())
        assert "medical_overreach" not in [v.rule for v in violations]

    def test_default_valid_report_has_no_medical_overreach(self):
        violations = validate_policy(_make_content(), _make_pack())
        assert "medical_overreach" not in [v.rule for v in violations]

    # --- grounded-reshape (N3): the verdict layer must be policed too ---

    def test_medical_overreach_in_lead_argument_scanned(self):
        """The strongest claim is the most prominent text; the medical-scope
        rule must scan lead_argument, not just key_takeaways."""
        content = _make_content(
            lead_argument=CoachTakeaway(text="This elevated HR means you have anemia."),
        )
        violations = validate_policy(content, _make_pack())
        assert "medical_overreach" in [v.rule for v in violations]

    def test_medical_overreach_in_thesis_scanned(self):
        content = _make_content(
            thesis="Your numbers mean you have hypertension.",
        )
        violations = validate_policy(content, _make_pack())
        assert "medical_overreach" in [v.rule for v in violations]

    def test_medical_overreach_in_headline_scanned(self):
        content = _make_content(
            headline="Run that diagnosed your overtraining syndrome",
        )
        violations = validate_policy(content, _make_pack())
        assert "medical_overreach" in [v.rule for v in violations]

    def test_uncalibrated_zone_reference_in_lead_argument_scanned(self):
        """Zone-language rule must also cover the verdict layer."""
        content = _make_content(
            lead_argument=CoachTakeaway(text="You spent most of the run in Z2."),
        )
        pack = _make_pack(metrics={"zones_calibrated": False})
        violations = validate_policy(content, pack)
        assert "uncalibrated_zone_reference" in [v.rule for v in violations]

    def test_clean_verdict_layer_no_violation(self):
        """A populated but clean verdict layer adds no false positives."""
        content = _make_content(
            headline="Solid aerobic long run",
            thesis="Your aerobic base held up well across the session.",
            lead_argument=CoachTakeaway(
                text="HR drift stayed low for the distance.",
                evidence=[{"field": "metrics.hr_drift", "value": 2.1}],
            ),
        )
        violations = validate_policy(content, _make_pack())
        assert violations == []

    # --- hardening from adversarial review ---

    def test_any_in_preamble_does_not_suppress_claim(self):
        """'any' is not a negation: it must not disable the gate on a real claim."""
        content = _make_content(
            key_takeaways=[
                CoachTakeaway(text="Do you notice any fatigue? You clearly have overtraining syndrome."),
                CoachTakeaway(text="Rest up."),
            ],
        )
        violations = validate_policy(content, _make_pack())
        assert "medical_overreach" in [v.rule for v in violations]

    def test_asthma_assertion_rejected(self):
        content = _make_content(
            key_takeaways=[
                CoachTakeaway(text="Your breathing pattern suggests you have asthma."),
                CoachTakeaway(text="Ease off."),
            ],
        )
        violations = validate_policy(content, _make_pack())
        assert "medical_overreach" in [v.rule for v in violations]

    def test_hypertension_assertion_rejected(self):
        content = _make_content(
            key_takeaways=[
                CoachTakeaway(text="These numbers mean you have hypertension."),
                CoachTakeaway(text="Take it easy."),
            ],
        )
        violations = validate_policy(content, _make_pack())
        assert "medical_overreach" in [v.rule for v in violations]

    def test_standalone_depression_assertion_rejected(self):
        content = _make_content(
            key_takeaways=[
                CoachTakeaway(text="Your low motivation means you have depression."),
                CoachTakeaway(text="Be kind to yourself."),
            ],
        )
        violations = validate_policy(content, _make_pack())
        assert "medical_overreach" in [v.rule for v in violations]

    def test_dose_of_training_idiom_permitted(self):
        """'dose' as a coaching idiom (no drug/supplement context) must pass."""
        content = _make_content(
            key_takeaways=[
                CoachTakeaway(text="Small doses of easy running build the base."),
                CoachTakeaway(text="The right dose of intensity matters."),
            ],
        )
        violations = validate_policy(content, _make_pack())
        assert "medical_overreach" not in [v.rule for v in violations]

    def test_directive_dose_of_supplement_rejected(self):
        content = _make_content(
            next_steps=[CoachNextStep(
                action="Caffeine",
                details="Take a higher dose of caffeine pills pre-race.",
                why="Ergogenic boost.",
            )],
        )
        violations = validate_policy(content, _make_pack())
        assert "medical_overreach" in [v.rule for v in violations]

    def test_med_directive_begin_statin_rejected(self):
        content = _make_content(
            next_steps=[CoachNextStep(
                action="Meds", details="Begin a statin for your cholesterol.", why="Health.",
            )],
        )
        violations = validate_policy(content, _make_pack())
        assert "medical_overreach" in [v.rule for v in violations]

    def test_med_directive_put_you_on_rejected(self):
        content = _make_content(
            next_steps=[CoachNextStep(
                action="Meds", details="I would put you on a beta-blocker for the race.", why="Calm the HR.",
            )],
        )
        violations = validate_policy(content, _make_pack())
        assert "medical_overreach" in [v.rule for v in violations]

    def test_med_directive_need_supplements_rejected(self):
        content = _make_content(
            next_steps=[CoachNextStep(
                action="Iron", details="You need more iron supplements this block.", why="Recovery.",
            )],
        )
        violations = validate_policy(content, _make_pack())
        assert "medical_overreach" in [v.rule for v in violations]

    def test_consider_ssri_rejected(self):
        content = _make_content(
            next_steps=[CoachNextStep(
                action="Meds", details="Consider an SSRI to steady your mood.", why="Wellbeing.",
            )],
        )
        violations = validate_policy(content, _make_pack())
        assert "medical_overreach" in [v.rule for v in violations]

    def test_consider_easy_week_permitted(self):
        """'consider' is only a problem when aimed at a medication noun."""
        content = _make_content(
            next_steps=[CoachNextStep(
                action="Recover", details="Consider an easy week to absorb the training.", why="Adaptation.",
            )],
        )
        violations = validate_policy(content, _make_pack())
        assert "medical_overreach" not in [v.rule for v in violations]

    def test_plural_mgs_dose_rejected(self):
        content = _make_content(
            next_steps=[CoachNextStep(
                action="Iron", details="Take 200mgs of iron daily.", why="Levels.",
            )],
        )
        violations = validate_policy(content, _make_pack())
        assert "medical_overreach" in [v.rule for v in violations]
