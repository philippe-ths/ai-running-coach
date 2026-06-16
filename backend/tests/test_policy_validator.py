"""Tests for the coach policy validator."""

from app.schemas.coach import (
    CoachReportContent, CoachTakeaway, CoachNextStep, CoachRisk, CoachQuestion,
    CoachMessageReport,
)
from app.schemas.coach_context import CoachContextPack
from app.services.coach.validator import (
    validate_policy,
    validate_message_policy,
    check_missing_questions,
    check_uncalibrated_zones,
    check_invalid_risk_flags,
    check_ungated_interval_claim,
    check_medical_overreach,
    check_narrative_evidence,
    check_corpus_evidence,
    check_user_materials_evidence,
)


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
    "adherence": {"prior_report_date": None, "outcomes": []},
    "believed_facts": {"facts": []},
    "calibration": {"hr_drift": {"calibrated": False, "observed_drift_pct": None, "basis": "n/a"}, "referral": None},
    "preference_profile": {"themes": []},
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

    def test_m9_referral_nudges_pass_medical_scope(self):
        """The deterministic M9 referral nudges must survive rule 5 if the model
        surfaces them verbatim (no diagnosis verb, condition, or dose)."""
        from app.services.coach.calibration import _NUDGES

        for nudge in _NUDGES.values():
            content = _make_content(
                key_takeaways=[
                    CoachTakeaway(text=nudge, evidence=[{"field": "calibration.referral", "value": "pattern"}]),
                    CoachTakeaway(text="Solid aerobic effort.", evidence=[{"field": "metrics.effort_score", "value": 3.0}]),
                ],
            )
            violations = validate_policy(content, _make_pack())
            assert "medical_overreach" not in [v.rule for v in violations], nudge

    def test_m9_referral_turned_into_diagnosis_is_rejected(self):
        """A referral that overreaches into a diagnosis is still caught by rule 5."""
        content = _make_content(
            key_takeaways=[
                CoachTakeaway(text="This pattern means you have overtraining syndrome; see a doctor.",
                              evidence=[{"field": "calibration.referral", "value": "pattern"}]),
                CoachTakeaway(text="Solid aerobic effort.", evidence=[{"field": "metrics.effort_score", "value": 3.0}]),
            ],
        )
        violations = validate_policy(content, _make_pack())
        assert "medical_overreach" in [v.rule for v in violations]

    def test_m9_widened_conditions_catch_running_injuries(self):
        """M9 extended rule 5 to backstop the running injuries a referral most
        invites the model to name (previously uncaught)."""
        for claim in (
            "This is probably a stress fracture; get it checked.",
            "That pattern is a sign of shin splints.",
            "You likely have plantar fasciitis.",
            "This is consistent with IT band syndrome.",
            "These are signs of burnout.",
        ):
            content = _make_content(
                key_takeaways=[
                    CoachTakeaway(text=claim, evidence=[{"field": "calibration.referral", "value": "x"}]),
                    CoachTakeaway(text="Solid effort.", evidence=[{"field": "metrics.effort_score", "value": 3.0}]),
                ],
            )
            violations = validate_policy(content, _make_pack())
            assert "medical_overreach" in [v.rule for v in violations], claim

    def test_m9_avoid_overtraining_still_permitted(self):
        """Coaching language ('avoid overtraining') is not an asserted claim."""
        content = _make_content(
            next_steps=[CoachNextStep(action="Recover", details="Add an easy week to avoid overtraining.", why="Adaptation.")],
        )
        violations = validate_policy(content, _make_pack())
        assert "medical_overreach" not in [v.rule for v in violations]


class TestSharedRuleBodies:
    """The six rules are extracted into shared functions over primitives so a
    future prose entry point can reuse them. These exercise each body directly
    (the reuse contract); the byte-equivalence of the assembled validate_policy
    is pinned by the rest of this file's 46 behaviour tests staying green."""

    def _null_check_in(self):
        return _make_pack(check_in={
            "rpe": None, "pain_score": None, "pain_location": None,
            "sleep_quality": None, "notes": None,
        }).check_in

    def _populated_check_in(self):
        return _make_pack().check_in  # rpe=6, etc.

    # Rule 1
    def test_missing_questions_fires_on_null_checkin_no_questions(self):
        v = check_missing_questions(self._null_check_in(), 0)
        assert [x.rule for x in v] == ["missing_questions_for_null_checkin"]

    def test_missing_questions_silent_with_questions_present(self):
        assert check_missing_questions(self._null_check_in(), 2) == []

    def test_missing_questions_silent_when_checkin_populated(self):
        assert check_missing_questions(self._populated_check_in(), 0) == []

    # Rule 2
    def test_uncalibrated_zones_fires_on_zone_reference(self):
        v = check_uncalibrated_zones(False, "You spent time in Z3 today.")
        assert [x.rule for x in v] == ["uncalibrated_zone_reference"]

    def test_uncalibrated_zones_silent_when_calibrated(self):
        assert check_uncalibrated_zones(True, "You spent time in Z3 today.") == []

    def test_uncalibrated_zones_silent_without_zone_tokens(self):
        assert check_uncalibrated_zones(False, "Easy conversational effort.") == []

    # Rule 3
    def test_invalid_risk_flags_fires_per_unknown_flag(self):
        v = check_invalid_risk_flags({"hr_drift"}, ["hr_drift", "made_up", "also_fake"])
        assert [x.rule for x in v] == ["invalid_risk_flag", "invalid_risk_flag"]
        assert "made_up" in v[0].detail and "also_fake" in v[1].detail

    def test_invalid_risk_flags_silent_when_all_valid(self):
        assert check_invalid_risk_flags({"hr_drift", "stops"}, ["hr_drift"]) == []

    # Rule 4
    def test_ungated_interval_claim_fires_on_low_confidence(self):
        wm = {"detection_confidence": "low", "match_score": None}
        v = check_ungated_interval_claim(wm, "You ran 8x400m today.")
        assert [x.rule for x in v] == ["ungated_interval_claim"]

    def test_ungated_interval_claim_silent_on_high_confidence(self):
        wm = {"detection_confidence": "high", "match_score": 1.0}
        assert check_ungated_interval_claim(wm, "You ran 8x400m today.") == []

    def test_ungated_interval_claim_silent_without_workout_match(self):
        assert check_ungated_interval_claim(None, "You ran 8x400m today.") == []

    # Rule 5
    def test_medical_overreach_fires_on_dose(self):
        v = check_medical_overreach("Take 200mg of iron daily.")
        assert [x.rule for x in v] == ["medical_overreach"]

    def test_medical_overreach_silent_on_clean_text(self):
        assert check_medical_overreach("Nice steady aerobic run.") == []

    # Rule 6
    def test_narrative_evidence_fires_on_narrative_path(self):
        v = check_narrative_evidence(["metrics.hr_drift", "narrative.tone"])
        assert [x.rule for x in v] == ["narrative_cited_as_fact"]
        assert "narrative.tone" in v[0].detail

    def test_narrative_evidence_silent_on_real_paths(self):
        assert check_narrative_evidence(["metrics.hr_drift", "training_context.x"]) == []


class TestCorpusEvidenceRule:
    """Rule 7 (P1.2, ADR 0014): the coaching corpus is judgment knowledge, never
    evidence — citing a `corpus.*` field path as report evidence is rejected. The
    rule is deliberately narrow (it matches ONLY corpus path citations, mirroring
    the rule-6 narrative precedent), so it never forces a false-positive fallback."""

    def test_corpus_evidence_fires_on_corpus_path(self):
        v = check_corpus_evidence(["metrics.hr_drift", "corpus.school.principles"])
        assert [x.rule for x in v] == ["corpus_cited_as_fact"]
        assert "corpus.school.principles" in v[0].detail

    def test_corpus_evidence_fires_on_bare_and_bracket_and_case(self):
        for path in ("corpus", "corpus.house_principles", "corpus[0]", "CORPUS.school"):
            v = check_corpus_evidence([path])
            assert [x.rule for x in v] == ["corpus_cited_as_fact"], path

    def test_corpus_evidence_silent_on_real_paths(self):
        assert check_corpus_evidence(["metrics.hr_drift", "training_context.x"]) == []

    def test_corpus_evidence_anchored_no_false_positive(self):
        # Anchored at the start, so a field that merely CONTAINS "corpus" never matches.
        assert check_corpus_evidence(["my_corpus_data", "corpus_metrics", "metrics.corpus"]) == []

    def test_validate_policy_fires_corpus_rule_on_citation(self):
        content = _make_content(
            key_takeaways=[
                CoachTakeaway(text="Volume is building.",
                              evidence=[{"field": "corpus.school.method_framing", "value": "x"}]),
                CoachTakeaway(text="Good easy control.",
                              evidence=[{"field": "metrics.effort_score", "value": 3.0}]),
            ],
        )
        rules = [v.rule for v in validate_policy(content, _make_pack())]
        assert "corpus_cited_as_fact" in rules

    def test_validate_message_policy_fires_corpus_rule_on_tail_citation(self):
        report = CoachMessageReport(
            message="Solid easy run; the effort stayed in the easy band the whole way.",
            headline="Easy run",
            next_steps=[CoachNextStep(
                action="Add easy volume", details="another easy 5k this week",
                why="builds the base", evidence=[{"field": "corpus.house_principles", "value": "x"}],
            )],
            risks=[], questions=[],
        )
        rules = [v.rule for v in validate_message_policy(report, _make_pack())]
        assert "corpus_cited_as_fact" in rules

    def test_corpus_rule_silent_when_no_corpus_citation(self):
        # A clean report (default evidence is all real field paths) draws no corpus
        # violation even though the rule is wired into the validator.
        rules = [v.rule for v in validate_policy(_make_content(), _make_pack())]
        assert "corpus_cited_as_fact" not in rules


class TestUserMaterialsEvidenceRule:
    """Rule 8 (P4, #286, ADR 0017): the runner's uploaded materials are judgment
    reference, never evidence — citing a `corpus.user_materials.*` field path as
    report evidence is rejected. Materials carry the hardest Authority tiering tier
    (they outrank house philosophy for stance) but, exactly like the corpus, can
    never GROUND a factual claim. The rule is deliberately narrow (it matches ONLY
    the user_materials sub-path, mirroring the rule-6/rule-7 precedent), so it never
    forces a false-positive fallback."""

    def test_user_materials_evidence_fires_on_materials_path(self):
        v = check_user_materials_evidence(
            ["metrics.hr_drift", "corpus.user_materials.0.stance"]
        )
        assert [x.rule for x in v] == ["user_materials_cited_as_fact"]
        assert "corpus.user_materials.0.stance" in v[0].detail

    def test_user_materials_evidence_fires_on_bare_bracket_and_case(self):
        for path in (
            "corpus.user_materials",
            "corpus.user_materials.method_framing",
            "corpus.user_materials[1]",
            "CORPUS.USER_MATERIALS.0.stance",
        ):
            v = check_user_materials_evidence([path])
            assert [x.rule for x in v] == ["user_materials_cited_as_fact"], path

    def test_user_materials_evidence_silent_on_real_and_school_paths(self):
        # A real metric, and a plain corpus/school path (rule 7's territory, not
        # rule 8's), draw no user_materials violation.
        assert check_user_materials_evidence(
            ["metrics.hr_drift", "corpus.school.principles", "corpus.house_principles"]
        ) == []

    def test_user_materials_evidence_anchored_no_false_positive(self):
        # Anchored, so a field that merely contains the substring never matches.
        assert check_user_materials_evidence(
            ["my_corpus.user_materials", "corpus.user_materials_extra", "metrics.user_materials"]
        ) == []

    def test_validate_policy_fires_user_materials_rule_on_citation(self):
        content = _make_content(
            key_takeaways=[
                CoachTakeaway(text="My plan says go long.",
                              evidence=[{"field": "corpus.user_materials.0.principles", "value": "x"}]),
                CoachTakeaway(text="Good easy control.",
                              evidence=[{"field": "metrics.effort_score", "value": 3.0}]),
            ],
        )
        rules = [v.rule for v in validate_policy(content, _make_pack())]
        assert "user_materials_cited_as_fact" in rules

    def test_validate_message_policy_fires_user_materials_rule_on_tail_citation(self):
        report = CoachMessageReport(
            message="Solid easy run; the effort stayed in the easy band the whole way.",
            headline="Easy run",
            next_steps=[CoachNextStep(
                action="Add easy volume", details="another easy 5k this week",
                why="builds the base",
                evidence=[{"field": "corpus.user_materials.0.method_framing", "value": "x"}],
            )],
            risks=[], questions=[],
        )
        rules = [v.rule for v in validate_message_policy(report, _make_pack())]
        assert "user_materials_cited_as_fact" in rules

    def test_user_materials_rule_silent_when_no_materials_citation(self):
        rules = [v.rule for v in validate_policy(_make_content(), _make_pack())]
        assert "user_materials_cited_as_fact" not in rules
