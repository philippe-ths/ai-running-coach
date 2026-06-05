"""Tests for the offline coach-report eval rubric (M5).

The rubric is the oracle for coach reports; this suite is the *inverted* oracle
for the rubric itself (aiw-ground-truth): hand-authored synthetic reports
(trust level 5, NOT production data) that the scorer must judge correctly. A
deliberately-bad report must FAIL the relevant assertions; a grounded report
must PASS. Each assertion is also exercised on its NOT_APPLICABLE branch so the
scorer never silently fails a report for a dimension that does not apply.
"""

from app.schemas.coach import (
    CoachNextStep,
    CoachQuestion,
    CoachReportContent,
    CoachRisk,
    CoachTakeaway,
)
from app.schemas.coach_context import CoachContextPack
from app.services.coach.eval.rubric import (
    AssertionStatus,
    assert_abstained_on_thin_trend,
    assert_advanced_not_parroted,
    assert_discounted_inflated_hr,
    assert_led_with_headline,
    assert_no_medical_overreach,
    score_report,
)


# --- fixture builders (mirror tests/test_policy_validator.py) -----------------

def _make_content(**overrides):
    """Build a valid CoachReportContent with sensible, grounded defaults."""
    defaults = {
        "headline": "Steady easy run",
        "thesis": "An easy aerobic run, run as intended.",
        "lead_argument": CoachTakeaway(
            text="Your effort sat comfortably in the easy band.",
            evidence=[{"field": "metrics.effort_score", "value": 3.0}],
        ),
        "key_takeaways": [
            CoachTakeaway(text="Good effort.", evidence=[{"field": "metrics.effort_score", "value": 3.0}]),
            CoachTakeaway(text="Pace was steady.", evidence=[{"field": "metrics.pace_variability", "value": 8.2}]),
        ],
        "next_steps": [
            CoachNextStep(action="Easy run", details="30 min conversational", why="Recovery"),
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
    "safety_rules": {"never_diagnose": True, "pain_severe_threshold": 7, "no_invented_facts": True},
}


def _make_pack(**section_overrides) -> CoachContextPack:
    pack = {key: dict(value) if isinstance(value, dict) else value for key, value in _DEFAULT_PACK_DICT.items()}
    for section, override in section_overrides.items():
        pack[section].update(override)
    return CoachContextPack.model_validate(pack)


_HEAT_DISCOUNT = {
    "hr_drift_pct": 9.0,
    "likely_inflated_by": ["heat"],
    "temperature_c": 29.0,
    "confidence": "high",
    "interpretation": "This HR drift is likely inflated by heat; discount it as a fatigue signal.",
}

_PRIOR_DIGEST = {
    "activity_date": "2026-02-08T10:00:00+00:00",
    "headline": "Tempo run",
    "lead_argument": "You held threshold pace for the full 20 minutes.",
    "next_steps": ["Recover with two easy runs before the next quality session."],
}

_GROUNDED_TREND = {
    "bucket": "easy|flat|mild",
    "sample_count": 6,
    "efficiency_factor": {"direction": "improving", "magnitude_pct": 4.0, "slope": 0.01, "n": 6},
    "hr_drift": {"direction": "stable", "magnitude_pct": 1.0, "slope": 0.0, "n": 6},
}


# --- assertion 1: led with a headline ----------------------------------------

class TestLedWithHeadline:
    def test_present_headline_passes(self):
        result = assert_led_with_headline(_make_content(), _make_pack())
        assert result.status is AssertionStatus.PASS

    def test_missing_headline_fails(self):
        result = assert_led_with_headline(_make_content(headline=None), _make_pack())
        assert result.status is AssertionStatus.FAIL

    def test_blank_headline_fails(self):
        result = assert_led_with_headline(_make_content(headline="   "), _make_pack())
        assert result.status is AssertionStatus.FAIL


# --- assertion 2: discounted inflated HR when a signal fired ------------------

class TestDiscountedInflatedHr:
    def test_no_signal_is_not_applicable(self):
        result = assert_discounted_inflated_hr(_make_content(), _make_pack())
        assert result.status is AssertionStatus.NOT_APPLICABLE

    def test_empty_inflators_is_not_applicable(self):
        # temperature-unknown caution case: dict present but no concrete confound.
        signal = {**_HEAT_DISCOUNT, "likely_inflated_by": [], "temperature_c": None, "confidence": "low"}
        result = assert_discounted_inflated_hr(_make_content(), _make_pack(metrics={"discount_signals": signal}))
        assert result.status is AssertionStatus.NOT_APPLICABLE

    def test_report_acknowledges_heat_passes(self):
        content = _make_content(
            thesis="Your HR drift looks high but it was 29C, so discount it as a heat artefact.",
        )
        result = assert_discounted_inflated_hr(content, _make_pack(metrics={"discount_signals": _HEAT_DISCOUNT}))
        assert result.status is AssertionStatus.PASS

    def test_report_ignores_fired_confound_fails(self):
        content = _make_content(
            thesis="Your HR drift was high, a clear sign of accumulating fatigue.",
            lead_argument=CoachTakeaway(text="The drift shows you are tiring."),
            key_takeaways=[CoachTakeaway(text="Fatigue is building.")],
            next_steps=[CoachNextStep(action="Rest", details="Take a day off", why="You are fatigued")],
        )
        result = assert_discounted_inflated_hr(content, _make_pack(metrics={"discount_signals": _HEAT_DISCOUNT}))
        assert result.status is AssertionStatus.FAIL


# --- assertion 3: avoided medical overreach ----------------------------------

class TestNoMedicalOverreach:
    def test_grounded_report_passes(self):
        result = assert_no_medical_overreach(_make_content(), _make_pack())
        assert result.status is AssertionStatus.PASS

    def test_diagnosis_verb_fails(self):
        content = _make_content(thesis="I would diagnose this as overtraining syndrome.")
        result = assert_no_medical_overreach(content, _make_pack())
        assert result.status is AssertionStatus.FAIL

    def test_dose_advice_fails(self):
        content = _make_content(
            next_steps=[CoachNextStep(action="Supplement", details="Take 200mg of iron daily", why="Low ferritin")],
        )
        result = assert_no_medical_overreach(content, _make_pack())
        assert result.status is AssertionStatus.FAIL


# --- assertion 4: advanced rather than parroted ------------------------------

class TestAdvancedNotParroted:
    def test_no_prior_report_is_not_applicable(self):
        result = assert_advanced_not_parroted(_make_content(), _make_pack())
        assert result.status is AssertionStatus.NOT_APPLICABLE

    def test_distinct_next_steps_pass(self):
        content = _make_content(
            next_steps=[CoachNextStep(action="Long run", details="90 min easy on Sunday", why="Build aerobic base")],
        )
        pack = _make_pack(longitudinal={"prior_reports": [_PRIOR_DIGEST], "baseline_trend": None})
        result = assert_advanced_not_parroted(content, pack)
        assert result.status is AssertionStatus.PASS

    def test_verbatim_parrot_fails(self):
        # report N repeats the prior next-step almost verbatim and says nothing new.
        content = _make_content(
            headline="Tempo run",
            lead_argument=CoachTakeaway(text="You held threshold pace for the full 20 minutes."),
            next_steps=[CoachNextStep(
                action="Recover",
                details="Recover with two easy runs before the next quality session.",
                why="Same as last time",
            )],
        )
        pack = _make_pack(longitudinal={"prior_reports": [_PRIOR_DIGEST], "baseline_trend": None})
        result = assert_advanced_not_parroted(content, pack)
        assert result.status is AssertionStatus.FAIL


# --- assertion 5: abstained on a thin trend ----------------------------------

class TestAbstainedOnThinTrend:
    def test_grounded_trend_claim_passes(self):
        content = _make_content(
            thesis="Your efficiency factor has been improving over your recent easy runs.",
        )
        pack = _make_pack(longitudinal={"prior_reports": [], "baseline_trend": _GROUNDED_TREND})
        result = assert_abstained_on_thin_trend(content, pack)
        assert result.status is AssertionStatus.PASS

    def test_ungrounded_trend_claim_fails(self):
        content = _make_content(
            thesis="Your fitness has clearly been trending upward over the last several weeks.",
        )
        # baseline_trend None => bucket abstaining => a trend claim is ungrounded.
        result = assert_abstained_on_thin_trend(content, _make_pack())
        assert result.status is AssertionStatus.FAIL

    def test_no_trend_claim_when_abstaining_passes(self):
        result = assert_abstained_on_thin_trend(_make_content(), _make_pack())
        assert result.status is AssertionStatus.PASS


# --- whole-report scoring: the good/bad oracle --------------------------------

def _known_good():
    content = _make_content(
        headline="Easy run, run easy",
        thesis="Your HR drift was high but it was 29C; discount it as heat, not fatigue.",
        lead_argument=CoachTakeaway(text="Effort stayed in the easy band despite the heat."),
        next_steps=[CoachNextStep(action="Long run", details="90 min easy Sunday", why="Aerobic base")],
    )
    pack = _make_pack(
        metrics={"discount_signals": _HEAT_DISCOUNT},
        longitudinal={"prior_reports": [_PRIOR_DIGEST], "baseline_trend": None},
    )
    return content, pack


def _deliberately_bad():
    content = _make_content(
        headline=None,  # fails assertion 1
        thesis="Your HR drift proves you are overtraining and your fitness is trending up fast.",
        lead_argument=CoachTakeaway(text="You held threshold pace for the full 20 minutes."),  # parrots prior
        key_takeaways=[CoachTakeaway(text="I would diagnose chronic fatigue.")],  # medical overreach
        next_steps=[CoachNextStep(
            action="Recover",
            details="Recover with two easy runs before the next quality session.",  # parrots prior
            why="Take 200mg of iron",  # dose advice
        )],
    )
    pack = _make_pack(
        metrics={"discount_signals": _HEAT_DISCOUNT},  # confound fired but report ignores it
        longitudinal={"prior_reports": [_PRIOR_DIGEST], "baseline_trend": None},  # abstaining; claims a trend anyway
    )
    return content, pack


class TestScoreReport:
    def test_known_good_report_passes_all_applicable(self):
        content, pack = _known_good()
        score = score_report(content, pack, report_id="good-1")
        failed = [a for a in score.assertions if a.status is AssertionStatus.FAIL]
        assert failed == [], f"known-good report should not fail any assertion, got {failed}"
        assert score.applicable_count > 0
        assert score.pass_rate == 1.0

    def test_deliberately_bad_report_fails(self):
        content, pack = _deliberately_bad()
        score = score_report(content, pack, report_id="bad-1")
        failed = {a.name for a in score.assertions if a.status is AssertionStatus.FAIL}
        # every dimension the bad report violates should be caught
        assert "led_with_headline" in failed
        assert "discounted_inflated_hr" in failed
        assert "no_medical_overreach" in failed
        assert "advanced_not_parroted" in failed
        assert "abstained_on_thin_trend" in failed
        assert score.pass_rate < 1.0

    def test_score_is_deterministic(self):
        content, pack = _known_good()
        a = score_report(content, pack, report_id="good-1").to_dict()
        b = score_report(content, pack, report_id="good-1").to_dict()
        assert a == b


class TestRegressionsFromAdversarialReview:
    """Bad reports that earlier versions of the rubric let through."""

    def test_confound_named_but_drift_read_as_fatigue_fails(self):
        # Naming the confound is not enough: this reads the drift as fatigue.
        content = _make_content(
            thesis="Despite the heat, your HR drift shows clear accumulating fatigue.",
        )
        result = assert_discounted_inflated_hr(content, _make_pack(metrics={"discount_signals": _HEAT_DISCOUNT}))
        assert result.status is AssertionStatus.FAIL

    def test_discount_word_without_confound_fails(self):
        # "discount" used innocuously, the actual confound ignored.
        content = _make_content(
            thesis="Discount any doubt: this was a strong, hard session and you are tiring.",
        )
        result = assert_discounted_inflated_hr(content, _make_pack(metrics={"discount_signals": _HEAT_DISCOUNT}))
        assert result.status is AssertionStatus.FAIL


class TestKnownBlindSpots:
    """The deterministic floor's documented gaps (see docs/testing/coach-report-eval.md).

    These assert the CURRENT limited behaviour so the blind spots are visible and
    regression-tracked. When the LLM-judge tier lands, these should flip to FAIL
    and move into the regression suite above.
    """

    def test_semantic_parrot_passes_blind_spot(self):
        # Same advice and same lead claim, fully reworded -> low lexical overlap.
        content = _make_content(
            lead_argument=CoachTakeaway(text="You sustained your lactate-threshold effort across the entire third of an hour."),
            next_steps=[CoachNextStep(action="Jog gently", details="Two relaxed jogs before your next hard session", why="Recovery")],
        )
        pack = _make_pack(longitudinal={"prior_reports": [_PRIOR_DIGEST], "baseline_trend": None})
        result = assert_advanced_not_parroted(content, pack)
        assert result.status is AssertionStatus.PASS  # blind spot: semantic parroting escapes

    def test_keyword_free_trend_claim_passes_blind_spot(self):
        content = _make_content(
            thesis="Your aerobic base looks considerably bigger now than it did earlier this block.",
        )
        result = assert_abstained_on_thin_trend(content, _make_pack())
        assert result.status is AssertionStatus.PASS  # blind spot: keyword-free trend claim escapes

    def test_spelled_out_dose_passes_blind_spot(self):
        # The reused validator's dose pattern is digit-based, so a spelled-out
        # dose escapes. The eval inherits that gap by design (single definition).
        content = _make_content(
            next_steps=[CoachNextStep(action="Supplement", details="Take five hundred milligrams of iron each morning", why="Energy")],
        )
        result = assert_no_medical_overreach(content, _make_pack())
        assert result.status is AssertionStatus.PASS  # blind spot inherited from validator rule 5
