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
    ASSERTIONS,
    AssertionStatus,
    assert_abstained_on_thin_trend,
    assert_advanced_not_parroted,
    assert_coached_not_caveated,
    assert_corpus_preserved_safety_surface,
    assert_user_materials_preserved_safety_surface,
    assert_discounted_inflated_hr,
    assert_led_with_headline,
    assert_load_not_framed_as_intensity,
    assert_depth_matched_the_session,
    assert_forward_distance_matches_plan,
    assert_no_medical_overreach,
    score_report,
    UNREMARKABLE_REPORT_WORD_CEILING,
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
    "perceived_effort": {
        "rpe": None, "effort_axis": "easy", "effort_score": 3.0,
        "divergence": None, "divergence_direction": None,
        "hr_confounded": False, "recommended_weighting": "hr_only", "pain_trend": None,
    },
    "adherence": {"prior_report_date": None, "outcomes": []},
    "believed_facts": {"facts": []},
    "calibration": {"hr_drift": {"calibrated": False, "observed_drift_pct": None, "basis": "n/a"}, "referral": None},
    "preference_profile": {"themes": []},
    # #655: enough history for the novelty read to mean something, and nothing
    # first-of-its-kind about this session — the depth assertion's applicable case.
    "salience": {
        "novelty": {"first_of_kind": [], "has_history": True},
        "safety_override": {"force_fuller": False, "reasons": []},
    },
    "safety_rules": {"never_diagnose": True, "pain_severe_threshold": 7, "no_invented_facts": True},
}


def _make_pack(**section_overrides) -> CoachContextPack:
    pack = {key: dict(value) if isinstance(value, dict) else value for key, value in _DEFAULT_PACK_DICT.items()}
    for section, override in section_overrides.items():
        # `.get(section, {})` rather than `pack[section]`: a section absent from
        # `_DEFAULT_PACK_DICT` (e.g. `schedule`, an Optional section with no
        # default row here) starts from nothing rather than raising.
        pack[section] = {**pack.get(section, {}), **override}
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

    def test_confounder_with_non_elevated_drift_is_not_applicable(self):
        # #176: a confounder fired but the drift was not elevated, so there is
        # nothing to discount; the coach is not penalised for declining to. Same
        # "did not discount" shape as the FAIL case above, but with a low drift ->
        # NOT_APPLICABLE instead of FAIL (the contrast proves the magnitude gate).
        # The run's drift is its sole home metrics.hr_drift; the guard reads it there
        # (the duplicate discount_signals.hr_drift_pct is folded out of the pack).
        content = _make_content(
            thesis="Drift was just 0.8%, strong aerobic control throughout.",
        )
        result = assert_discounted_inflated_hr(
            content, _make_pack(metrics={"discount_signals": _HEAT_DISCOUNT, "hr_drift": 0.8})
        )
        assert result.status is AssertionStatus.NOT_APPLICABLE


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

    def test_comparative_question_is_not_a_trend_claim(self):
        # Real-data finding: "compared to your recent session" inside a QUESTION
        # is not an asserted trend, so an abstaining bucket must still PASS.
        content = _make_content(
            questions=[CoachQuestion(
                question="How did this tempo effort feel compared to your recent interval session?",
                reason="To calibrate RPE against the data.",
            )],
        )
        result = assert_abstained_on_thin_trend(content, _make_pack())
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


class TestFramedForAdherence:
    def _content(self, steps):
        return _make_content(next_steps=steps)

    def test_not_applicable_without_profile(self):
        from app.services.coach.eval.rubric import assert_framed_for_adherence
        content = self._content([CoachNextStep(action="Add a tempo run", details="20 min", why="x")])
        r = assert_framed_for_adherence(content, _make_pack())  # empty profile
        assert r.status.value == "not_applicable"

    def test_passes_when_leans_on_acts_on_theme(self):
        from app.services.coach.eval.rubric import assert_framed_for_adherence
        content = self._content([CoachNextStep(action="Add a tempo run", details="20 min", why="x")])
        pack = _make_pack(preference_profile={"themes": [
            {"theme": "add_quality", "tendency": "acts_on", "acted": 4, "total": 5},
        ]})
        assert assert_framed_for_adherence(content, pack).status.value == "pass"

    def test_fails_when_only_ignored_theme(self):
        from app.services.coach.eval.rubric import assert_framed_for_adherence
        content = self._content([CoachNextStep(action="Add a long run", details="build endurance", why="x")])
        pack = _make_pack(preference_profile={"themes": [
            {"theme": "easy_discipline", "tendency": "acts_on", "acted": 5, "total": 6},
            {"theme": "add_long_run", "tendency": "ignores", "acted": 0, "total": 4},
        ]})
        assert assert_framed_for_adherence(content, pack).status.value == "fail"

    def test_not_applicable_when_no_step_classifies(self):
        from app.services.coach.eval.rubric import assert_framed_for_adherence
        content = self._content([CoachNextStep(action="Stay hydrated", details="drink water", why="x")])
        pack = _make_pack(preference_profile={"themes": [
            {"theme": "add_long_run", "tendency": "ignores", "acted": 0, "total": 4},
        ]})
        assert assert_framed_for_adherence(content, pack).status.value == "not_applicable"


# --- assertion 7: cumulative load not framed as intensity (#168) -------------

class TestLoadNotFramedAsIntensity:
    """#168: effort_score is a TRIMP-like cumulative training LOAD that grows with
    duration, never an intensity reading (intensity is the separate HR-derived
    `effort` axis). The report must not narrate the load number as an intensity
    verdict ("effort score ... confirms recovery territory, below moderate
    intensity thresholds")."""

    def test_no_effort_score_in_prose_is_not_applicable(self):
        # The default content cites effort_score only as machine-readable evidence,
        # never naming it in prose, so there is nothing to misframe.
        result = assert_load_not_framed_as_intensity(_make_content(), _make_pack())
        assert result.status is AssertionStatus.NOT_APPLICABLE

    def test_effort_score_framed_as_intensity_fails(self):
        # The exact #168 failure: the load number narrated as an intensity verdict.
        content = _make_content(
            lead_argument=CoachTakeaway(
                text="An effort score of 265.6 confirms this stayed in true recovery "
                "territory, well below moderate intensity thresholds.",
            ),
        )
        result = assert_load_not_framed_as_intensity(content, _make_pack())
        assert result.status is AssertionStatus.FAIL

    def test_effort_score_framed_as_load_passes(self):
        # effort_score correctly narrated as accumulated load; intensity sourced
        # from the effort axis, no intensity-verdict phrasing tied to the score.
        content = _make_content(
            lead_argument=CoachTakeaway(
                text="Your effort stayed easy. The effort score of 265.6 reflects "
                "accumulated load from the long duration.",
            ),
        )
        result = assert_load_not_framed_as_intensity(content, _make_pack())
        assert result.status is AssertionStatus.PASS

    def test_explicit_load_not_intensity_disclaimer_passes(self):
        # A sentence may name an intensity phrase to DISTINGUISH the load number
        # from it; the not-intensity disclaimer in the same sentence clears it.
        content = _make_content(
            lead_argument=CoachTakeaway(
                text="The effort score is a cumulative load number, not an intensity "
                "reading, so it does not place you below moderate intensity.",
            ),
        )
        result = assert_load_not_framed_as_intensity(content, _make_pack())
        assert result.status is AssertionStatus.PASS

    def test_misframing_in_takeaway_fails(self):
        # The misframe is caught wherever it is asserted, not only the lead.
        content = _make_content(
            key_takeaways=[CoachTakeaway(
                text="The effort score puts this run in recovery territory.",
            )],
        )
        result = assert_load_not_framed_as_intensity(content, _make_pack())
        assert result.status is AssertionStatus.FAIL


# --- assertion 8: coached the available data, not the detection caveat (#171) -

# A clean interval session with per-rep data present. The stream-heuristic path
# can still return low detection_confidence (structure couldn't be matched to a
# uniform plan) even though every rep's effort/recovery is captured.
_LOW_CONF_INTERVAL_STRUCTURE = {
    "warmup_duration_s": 600,
    "cooldown_duration_s": 300,
    "work_segments": [
        {"duration_s": 48, "avg_speed_mps": 4.2},
        {"duration_s": 49, "avg_speed_mps": 4.1},
        {"duration_s": 47, "avg_speed_mps": 4.2},
    ],
    "rest_segments": [{"duration_s": 60}, {"duration_s": 60}],
    "summary": {"rep_count": 3, "avg_work_duration_s": 48},
}
_LOW_CONF_MATCH = {"detection_confidence": "low", "match_score": 0.4}

# Same per-rep data, but sourced from the runner's own recorded Strava laps
# (#170): authoritative structure, so detection_confidence is "high".
_RECORDED_LAPS_STRUCTURE = {**_LOW_CONF_INTERVAL_STRUCTURE, "source": "recorded_laps"}
_RECORDED_LAPS_MATCH = {"detection_confidence": "high", "match_score": 1.0}


class TestCoachedNotCaveated:
    """#171: when per-rep interval data is present, the report must coach it and
    express low *detection* confidence as a bounded caveat about exact structure,
    not as the headline/thesis, and must never advise an action the runner already
    took (suggesting the lap button when the laps were recorded)."""

    def test_no_interval_data_is_not_applicable(self):
        # Continuous run, no interval structure: nothing to over-caveat.
        result = assert_coached_not_caveated(_make_content(), _make_pack())
        assert result.status is AssertionStatus.NOT_APPLICABLE

    def test_high_confidence_stream_detection_is_not_applicable(self):
        # Per-rep data with high (non-lap) detection confidence: no caveat pressure
        # and no recorded laps, so the assertion has nothing to judge.
        pack = _make_pack(metrics={
            "interval_structure": _LOW_CONF_INTERVAL_STRUCTURE,
            "workout_match": {"detection_confidence": "high", "match_score": 0.9},
        })
        result = assert_coached_not_caveated(_make_content(), pack)
        assert result.status is AssertionStatus.NOT_APPLICABLE

    def test_caveat_led_lead_on_low_confidence_fails(self):
        # The #171 anti-pattern: the lead frames the session as undetected/unreliable
        # even though every rep is present.
        content = _make_content(
            headline="Intervals not consistently detected",
            thesis="The session structure could not be reliably detected from your data.",
            lead_argument=CoachTakeaway(text="Interval detection was unreliable this run."),
        )
        pack = _make_pack(metrics={
            "interval_structure": _LOW_CONF_INTERVAL_STRUCTURE,
            "workout_match": _LOW_CONF_MATCH,
        })
        result = assert_coached_not_caveated(content, pack)
        assert result.status is AssertionStatus.FAIL

    def test_coaches_rep_data_on_low_confidence_passes(self):
        # Leads with the available per-rep analysis; the structure caveat, if any,
        # is bounded and not the headline/thesis.
        content = _make_content(
            headline="Solid rep session",
            thesis="Your three reps held an even ~48s effort with clean recovery between them.",
            lead_argument=CoachTakeaway(text="Rep pace held steady across all three efforts."),
        )
        pack = _make_pack(metrics={
            "interval_structure": _LOW_CONF_INTERVAL_STRUCTURE,
            "workout_match": _LOW_CONF_MATCH,
        })
        result = assert_coached_not_caveated(content, pack)
        assert result.status is AssertionStatus.PASS

    def test_bounded_caveat_outside_the_lead_passes(self):
        # A bounded caveat about exact structure in a takeaway (not the lead) is
        # exactly the behaviour #171 wants, so it must not fail.
        content = _make_content(
            headline="Solid rep session",
            thesis="Three even reps with good recovery.",
            lead_argument=CoachTakeaway(text="Rep pace held steady across all three efforts."),
            key_takeaways=[CoachTakeaway(
                text="The exact rep boundaries are approximate, but the efforts themselves are clear.",
            )],
        )
        pack = _make_pack(metrics={
            "interval_structure": _LOW_CONF_INTERVAL_STRUCTURE,
            "workout_match": _LOW_CONF_MATCH,
        })
        result = assert_coached_not_caveated(content, pack)
        assert result.status is AssertionStatus.PASS

    def test_advises_lap_button_when_laps_recorded_fails(self):
        # AC#3: never advise an action already taken. The laps WERE recorded
        # (source=recorded_laps), so telling the runner to use the lap button is wrong.
        content = _make_content(
            next_steps=[CoachNextStep(
                action="Use the lap button",
                details="Press the lap button at each rep so the structure is captured",
                why="It will make detection cleaner next time",
            )],
        )
        pack = _make_pack(metrics={
            "interval_structure": _RECORDED_LAPS_STRUCTURE,
            "workout_match": _RECORDED_LAPS_MATCH,
        })
        result = assert_coached_not_caveated(content, pack)
        assert result.status is AssertionStatus.FAIL

    def test_recorded_laps_coached_without_lap_button_advice_passes(self):
        content = _make_content(
            headline="Clean lap-marked reps",
            lead_argument=CoachTakeaway(text="Your recorded laps show three even reps."),
        )
        pack = _make_pack(metrics={
            "interval_structure": _RECORDED_LAPS_STRUCTURE,
            "workout_match": _RECORDED_LAPS_MATCH,
        })
        result = assert_coached_not_caveated(content, pack)
        assert result.status is AssertionStatus.PASS


# --- prompt v8 versioning (#168) ---------------------------------------------

class TestPromptVersioningV8:
    """#168 ships the load-vs-intensity discipline as a new prompt version (rule
    22), additive over v7 so cached v1-v7 reports stay reproducible."""

    def test_v8_adds_load_not_intensity_rule(self):
        from app.services.coach.prompts import PROMPT_VERSIONS
        v8 = PROMPT_VERSIONS["coach_report_v8"]
        assert "22." in v8
        assert "LOAD, NOT INTENSITY" in v8
        assert "effort_score" in v8

    def test_v7_stays_byte_stable(self):
        from app.services.coach.prompts import PROMPT_VERSIONS, SYSTEM_PROMPT_V7
        assert PROMPT_VERSIONS["coach_report_v7"] == SYSTEM_PROMPT_V7
        assert "LOAD, NOT INTENSITY" not in PROMPT_VERSIONS["coach_report_v7"]

    def test_v8_extends_v7(self):
        from app.services.coach.prompts import PROMPT_VERSIONS
        v7 = PROMPT_VERSIONS["coach_report_v7"]
        v8 = PROMPT_VERSIONS["coach_report_v8"]
        assert v8 != v7
        assert v8.startswith(v7)


# --- prompt v9 versioning (#171) ---------------------------------------------

class TestPromptVersioningV9:
    """#171 ships the coach-the-available-data discipline as a new prompt version
    (rule 23), additive over v8 so cached v1-v8 reports stay reproducible."""

    def test_v9_is_active_default(self):
        from app.core.config import settings
        # The in-code default tracks the production feature-bearing prompt (#424).
        assert settings.COACH_PROMPT_ID == "coach_message_v8"

    def test_v9_adds_coach_the_data_rule(self):
        from app.services.coach.prompts import PROMPT_VERSIONS
        v9 = PROMPT_VERSIONS["coach_report_v9"]
        assert "23." in v9
        assert "COACH THE AVAILABLE DATA" in v9
        assert "recorded_laps" in v9

    def test_v8_stays_byte_stable(self):
        from app.services.coach.prompts import PROMPT_VERSIONS, SYSTEM_PROMPT_V8
        assert PROMPT_VERSIONS["coach_report_v8"] == SYSTEM_PROMPT_V8
        assert "COACH THE AVAILABLE DATA" not in PROMPT_VERSIONS["coach_report_v8"]

    def test_v9_extends_v8(self):
        from app.services.coach.prompts import PROMPT_VERSIONS
        v8 = PROMPT_VERSIONS["coach_report_v8"]
        v9 = PROMPT_VERSIONS["coach_report_v9"]
        assert v9 != v8
        assert v9.startswith(v8)


# --- interval playbook reframe (#171) ----------------------------------------

class TestIntervalPlaybookReframe:
    """The shared Intervals playbook is appended after the numbered rules, so it
    must align with rule 23: lead with the rep data, never headline the session as
    unreliable, and never advise the lap button when laps were recorded."""

    def test_playbook_no_longer_calls_detection_unreliable(self):
        from app.services.coach.prompts import ACTIVITY_PLAYBOOKS
        playbook = ACTIVITY_PLAYBOOKS["Intervals"]
        assert "detection was unreliable" not in playbook.lower()

    def test_playbook_leads_with_rep_data_and_guards_recorded_laps(self):
        from app.services.coach.prompts import ACTIVITY_PLAYBOOKS
        playbook = ACTIVITY_PLAYBOOKS["Intervals"]
        assert "recorded_laps" in playbook
        assert "never suggest using the lap button" in playbook.lower()


_REFERRAL = {"nudge": "Consider a check-in with a healthcare professional.",
             "basis": "illness_or_extreme_fatigue"}


class TestCorpusPreservedSafetySurface:
    """The 10th rubric assertion (P1.2, ADR 0014): the corpus-dimension regression
    sensor, parallel to the P1.1 voice sensor. The corpus reweights emphasis only,
    so a corpus-steered report must NOT drop the safety floor: when the pipeline
    fired a referral, the report must still relay a professional-consult nudge. The
    hard cross-school invariance gate is the deterministic test (tests/
    test_corpus_invariance.py); this is its soft per-report companion."""

    def test_not_applicable_when_no_referral(self):
        result = assert_corpus_preserved_safety_surface(_make_content(), _make_pack())
        assert result.status is AssertionStatus.NOT_APPLICABLE

    def test_passes_when_referral_relayed(self):
        content = _make_content(
            thesis="Aerobically this held up, but given the fatigue signal, "
            "it is worth getting checked by a healthcare professional.",
        )
        result = assert_corpus_preserved_safety_surface(
            content, _make_pack(calibration={"referral": _REFERRAL})
        )
        assert result.status is AssertionStatus.PASS

    def test_fails_when_referral_dropped(self):
        content = _make_content(
            thesis="Strong aerobic run, nothing to flag — keep building volume.",
        )
        result = assert_corpus_preserved_safety_surface(
            content, _make_pack(calibration={"referral": _REFERRAL})
        )
        assert result.status is AssertionStatus.FAIL

    def test_registered_in_the_rubric(self):
        names = [fn.__name__ for fn in ASSERTIONS]
        assert "assert_corpus_preserved_safety_surface" in names
        # score_report runs it and the result name is the assert_-stripped name.
        content, pack = _known_good()
        score = score_report(content, pack)
        assert any(a.name == "corpus_preserved_safety_surface" for a in score.assertions)


class TestUserMaterialsPreservedSafetySurface:
    """The 11th rubric assertion (P4, #286, ADR 0017): the user-materials-dimension
    regression sensor, parallel to the P1.1 voice and P1.2 corpus sensors. The
    runner's uploaded materials are the highest-authority steering input AND the
    untrusted one, so a materials-steered report must NOT drop the safety floor: when
    the pipeline fired a referral, the report must still relay a professional-consult
    nudge. The hard cross-material invariance gate is the deterministic test (tests/
    test_user_materials_invariance.py); this is its soft per-report companion."""

    def test_not_applicable_when_no_referral(self):
        result = assert_user_materials_preserved_safety_surface(_make_content(), _make_pack())
        assert result.status is AssertionStatus.NOT_APPLICABLE

    def test_passes_when_referral_relayed(self):
        content = _make_content(
            thesis="Aerobically this held up, but given the fatigue signal, "
            "it is worth getting checked by a healthcare professional.",
        )
        result = assert_user_materials_preserved_safety_surface(
            content, _make_pack(calibration={"referral": _REFERRAL})
        )
        assert result.status is AssertionStatus.PASS

    def test_fails_when_referral_dropped(self):
        content = _make_content(
            thesis="Strong aerobic run, nothing to flag — keep building volume.",
        )
        result = assert_user_materials_preserved_safety_surface(
            content, _make_pack(calibration={"referral": _REFERRAL})
        )
        assert result.status is AssertionStatus.FAIL

    def test_registered_in_the_rubric(self):
        names = [fn.__name__ for fn in ASSERTIONS]
        assert "assert_user_materials_preserved_safety_surface" in names
        content, pack = _known_good()
        score = score_report(content, pack)
        assert any(a.name == "user_materials_preserved_safety_surface" for a in score.assertions)


class TestDepthMatchedTheSession:
    """The 15th rubric assertion (#655): an unremarkable session earns an honest read,
    not a long one.

    With several sessions a day, a uniformly long write-up for every one of them buries
    the one that mattered. This is the only assertion about a report's SHAPE, and it is
    deliberately one-sided — it never asks a report to be longer, only to stop when the
    session has stopped giving. Everything that could legitimately buy length makes it
    abstain, so the branch that matters most here is NOT_APPLICABLE.

    All fixtures are synthetic (trust level 5), NOT production data.
    """

    _BRIEF = "Easy day, exactly as it should be. Nothing else to say about this one."

    def _long(self) -> str:
        # Comfortably over the ceiling, and deliberately made of unremarkable filler:
        # the assertion is about length, not about the words being wrong.
        return " ".join(["The effort sat in the easy band and everything held steady."] * 30)

    def _message(self, prose):
        from app.schemas.coach import CoachMessageReport
        return CoachMessageReport(message=prose, headline="Easy run", next_steps=[],
                                  risks=[], questions=[], tail_degraded=False)

    def test_brief_report_on_an_unremarkable_session_passes(self):
        result = assert_depth_matched_the_session(self._message(self._BRIEF), _make_pack())
        assert result.status is AssertionStatus.PASS
        assert result.evidence["words"] <= UNREMARKABLE_REPORT_WORD_CEILING

    def test_long_report_on_an_unremarkable_session_fails(self):
        result = assert_depth_matched_the_session(self._message(self._long()), _make_pack())
        assert result.status is AssertionStatus.FAIL
        assert result.evidence["words"] > UNREMARKABLE_REPORT_WORD_CEILING

    def test_the_ceiling_is_what_decides_it(self):
        """Sensitivity: the same pack, the same shape, only the length changes. If this
        pair ever agreed, the assertion would be reading something other than length."""
        pack = _make_pack()
        at = self._message(" ".join(["word"] * UNREMARKABLE_REPORT_WORD_CEILING))
        over = self._message(" ".join(["word"] * (UNREMARKABLE_REPORT_WORD_CEILING + 1)))
        assert assert_depth_matched_the_session(at, pack).status is AssertionStatus.PASS
        assert assert_depth_matched_the_session(over, pack).status is AssertionStatus.FAIL

    def test_abstains_when_the_pack_carries_no_salience_section(self):
        """COACH_SALIENCE_ENABLED=false drops the section outright. An unknown novelty
        read is not evidence of an unremarkable run, so this must abstain rather than
        call every report over-long."""
        pack = _make_pack()
        pack.salience = None
        result = assert_depth_matched_the_session(self._message(self._long()), pack)
        assert result.status is AssertionStatus.NOT_APPLICABLE

    def test_abstains_while_the_novelty_read_is_still_abstaining(self):
        result = assert_depth_matched_the_session(
            self._message(self._long()),
            _make_pack(salience={"novelty": {"first_of_kind": [], "has_history": False}}),
        )
        assert result.status is AssertionStatus.NOT_APPLICABLE

    def test_abstains_on_a_first_of_its_kind(self):
        result = assert_depth_matched_the_session(
            self._message(self._long()),
            _make_pack(salience={
                "novelty": {"first_of_kind": ["first_interval_session"], "has_history": True},
            }),
        )
        assert result.status is AssertionStatus.NOT_APPLICABLE
        assert result.evidence["first_of_kind"] == ["first_interval_session"]

    def test_abstains_on_a_risk_flag(self):
        result = assert_depth_matched_the_session(
            self._message(self._long()), _make_pack(metrics={"flags": ["hr_drift_high"]})
        )
        assert result.status is AssertionStatus.NOT_APPLICABLE

    def test_abstains_when_a_referral_fired(self):
        result = assert_depth_matched_the_session(
            self._message(self._long()),
            _make_pack(calibration={"referral": {
                "nudge": "Consider a check-in with a healthcare professional.",
                "basis": "illness_or_extreme_fatigue",
            }}),
        )
        assert result.status is AssertionStatus.NOT_APPLICABLE

    def test_abstains_when_a_prior_commitment_is_awaiting_an_answer(self):
        result = assert_depth_matched_the_session(
            self._message(self._long()),
            _make_pack(adherence={
                "prior_report_date": "2026-02-08T10:00:00+00:00",
                "outcomes": [{
                    "prior_action": "Add a long run",
                    "theme": "add_long_run",
                    "label": "acted_on",
                    "comparable_activity_date": "2026-02-12T10:00:00+00:00",
                    "basis": "a comparable run since",
                    "overridden": False,
                }],
            }),
        )
        assert result.status is AssertionStatus.NOT_APPLICABLE

    def test_scores_the_legacy_structured_shape_too(self):
        """The rubric covers both output shapes; a structured report has no single prose
        field, so its whole scoreable surface stands in for one."""
        brief = _make_content(
            thesis="Easy day.",
            lead_argument=CoachTakeaway(text="Nothing to fix."),
            key_takeaways=[CoachTakeaway(text="Done.")],
            next_steps=[CoachNextStep(action="Rest", details="sleep", why="recovery")],
        )
        assert assert_depth_matched_the_session(
            brief, _make_pack()
        ).status is AssertionStatus.PASS
        wordy = _make_content(thesis=self._long())
        assert assert_depth_matched_the_session(
            wordy, _make_pack()
        ).status is AssertionStatus.FAIL

    def test_registered_in_the_rubric(self):
        names = [fn.__name__ for fn in ASSERTIONS]
        assert "assert_depth_matched_the_session" in names
        score = score_report(self._message(self._BRIEF), _make_pack())
        assert any(a.name == "depth_matched_the_session" for a in score.assertions)


# --- assertion 16: forward distance matches the plan (#943) -----------------

class TestForwardDistanceMatchesPlan:
    """The 16th rubric assertion (#943): a distance named for a session that has
    not happened yet must be the plan's own number, not an invented one.

    A runner's schedule showed an 18 km long run next week; the report said
    "next week's 16.5km" — a figure the pack never carried, because the section
    only ever showed the current week. #943 gives the coach the real number
    (`right_now.schedule.next_week_committed`); this assertion checks the report
    used it rather than inventing one anyway.

    All fixtures are synthetic (trust level 5), NOT production data.
    """

    _PLAN_SESSION = {
        "session_id": None, "when": "next Sat", "title": "Long run",
        "intent": "long", "discipline": "run", "target": "18.0 km",
    }

    def _pack_with_next_week(self) -> CoachContextPack:
        return _make_pack(schedule={"next_week_committed": [self._PLAN_SESSION]})

    def _message(self, prose):
        from app.schemas.coach import CoachMessageReport
        return CoachMessageReport(
            message=prose, headline="Easy run", next_steps=[], risks=[], questions=[],
            tail_degraded=False,
        )

    def test_abstains_when_the_pack_carries_no_schedule_section(self):
        result = assert_forward_distance_matches_plan(
            self._message("For next week's 16.5km long run, ease into it."), _make_pack()
        )
        assert result.status is AssertionStatus.NOT_APPLICABLE

    def test_abstains_when_no_upcoming_session_states_a_distance(self):
        pack = _make_pack(schedule={"still_to_come_this_week": [
            {"session_id": None, "when": "any day", "title": "Strength",
             "intent": "strength", "discipline": "strength", "target": "40 min"},
        ]})
        result = assert_forward_distance_matches_plan(
            self._message("For next week's 16.5km long run, ease into it."), pack
        )
        assert result.status is AssertionStatus.NOT_APPLICABLE

    def test_a_matching_forward_distance_passes(self):
        result = assert_forward_distance_matches_plan(
            self._message("For next week's 18km long run, keep it conversational."),
            self._pack_with_next_week(),
        )
        assert result.status is AssertionStatus.PASS

    def test_an_invented_forward_distance_fails(self):
        result = assert_forward_distance_matches_plan(
            self._message("For next week's 16.5km long run, ease into the pace."),
            self._pack_with_next_week(),
        )
        assert result.status is AssertionStatus.FAIL
        assert result.evidence["claimed_km"] == [16.5]

    def test_a_report_that_makes_no_forward_claim_passes(self):
        """The section applying does not mean a report has to reference it — only
        that if it does, it has to be right."""
        result = assert_forward_distance_matches_plan(
            self._message("Comfortable easy run, exactly as planned."),
            self._pack_with_next_week(),
        )
        assert result.status is AssertionStatus.PASS

    def test_a_distance_about_todays_run_is_not_read_as_a_forward_claim(self):
        """No forward marker, so a distance about the run being reported on is
        never checked against the plan — that would be a false positive on
        every ordinary report."""
        result = assert_forward_distance_matches_plan(
            self._message("You covered 16.5km today at a steady pace."),
            self._pack_with_next_week(),
        )
        assert result.status is AssertionStatus.PASS

    def test_matches_within_a_small_tolerance(self):
        result = assert_forward_distance_matches_plan(
            self._message("For next week's 18.02km long run, hold steady."),
            self._pack_with_next_week(),
        )
        assert result.status is AssertionStatus.PASS

    def test_a_whole_km_rounding_of_the_one_decimal_pack_figure_passes(self):
        """`_target()` renders a half marathon (21097.5 m) as "21.1 km"; a
        runner — and the coach — naturally speak of it as "21km". That rounding
        is not an invented number and must not fail the assertion."""
        pack = _make_pack(schedule={"next_week_committed": [
            {"session_id": None, "when": "next Sun", "title": "Half marathon",
             "intent": "long", "discipline": "run", "target": "21.1 km"},
        ]})
        result = assert_forward_distance_matches_plan(
            self._message("For next week's 21km race, hold back through the first 5."),
            pack,
        )
        assert result.status is AssertionStatus.PASS

    def test_the_tolerance_still_catches_the_defect_that_motivated_this(self):
        """The exact #943 defect: "16.5km" against a planned 18.0 km long run is
        1.5 km out — comfortably past the whole-km-rounding tolerance above — and
        must still fail. Pinned as its own test so a future widening of the
        tolerance cannot silently swallow the case the assertion exists for."""
        result = assert_forward_distance_matches_plan(
            self._message("For next week's 16.5km long run, ease into the pace."),
            self._pack_with_next_week(),
        )
        assert result.status is AssertionStatus.FAIL

    def test_scores_the_legacy_structured_shape_too(self):
        good = _make_content(thesis="For next week's 18km long run, keep it easy.")
        bad = _make_content(thesis="For next week's 16.5km long run, ease into it.")
        assert assert_forward_distance_matches_plan(
            good, self._pack_with_next_week()
        ).status is AssertionStatus.PASS
        assert assert_forward_distance_matches_plan(
            bad, self._pack_with_next_week()
        ).status is AssertionStatus.FAIL

    def test_registered_in_the_rubric(self):
        names = [fn.__name__ for fn in ASSERTIONS]
        assert "assert_forward_distance_matches_plan" in names
        score = score_report(
            self._message("Comfortable easy run."), self._pack_with_next_week()
        )
        assert any(a.name == "forward_distance_matches_plan" for a in score.assertions)
