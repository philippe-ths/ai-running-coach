"""M9 self-calibrating correction + non-diagnostic referral — pure-logic tests.

calibrate_drift compares this run's HR drift to the runner's OWN typical drift for
the same conditions (not a population constant), and falls back to a labeled
population heuristic when there is not enough comparable history. assess_referral
fires a non-diagnostic clinician nudge on deterministically-computable red-flag
patterns, abstaining otherwise, and its text must never contain a diagnosis verb.
"""

from app.services.coach.calibration import (
    MIN_SAMPLES_FOR_CALIBRATION,
    POPULATION_DRIFT_THRESHOLD_PCT,
    assess_referral,
    calibrate_drift,
)


# --------------------------------------------------------------------------- #
# calibrate_drift
# --------------------------------------------------------------------------- #

def test_calibrated_above_personal_norm():
    cal = calibrate_drift(12.0, [5.0, 6.0, 5.0, 6.0])
    assert cal["calibrated"] is True
    assert cal["expected_drift_pct"] == 5.5
    assert cal["observed_drift_pct"] == 12.0
    assert cal["delta_pct"] == 6.5
    assert cal["comparison"] == "above"
    assert cal["sample_count"] == 4


def test_calibrated_in_line_with_personal_norm():
    cal = calibrate_drift(6.0, [5.0, 6.0, 5.0, 6.0])
    assert cal["calibrated"] is True
    assert cal["comparison"] == "in_line"  # within the noise margin of personal norm


def test_calibrated_below_personal_norm():
    cal = calibrate_drift(1.0, [6.0, 7.0, 6.0, 7.0])
    assert cal["calibrated"] is True
    assert cal["comparison"] == "below"


def test_thin_history_falls_back_to_labeled_heuristic():
    cal = calibrate_drift(12.0, [5.0, 6.0])  # only 2 < MIN
    assert cal["calibrated"] is False
    assert cal["observed_drift_pct"] == 12.0
    assert cal["heuristic_threshold_pct"] == POPULATION_DRIFT_THRESHOLD_PCT
    assert "heuristic" in cal["basis"].lower()


def test_no_drift_measured_degrades_cleanly():
    cal = calibrate_drift(None, [5.0, 6.0, 5.0, 6.0])
    assert cal["calibrated"] is False
    assert cal["observed_drift_pct"] is None


def test_comparable_drifts_ignore_none():
    cal = calibrate_drift(12.0, [5.0, None, 6.0, 5.0, None, 6.0])
    assert cal["calibrated"] is True
    assert cal["sample_count"] == 4


def test_min_samples_constant_is_four():
    assert MIN_SAMPLES_FOR_CALIBRATION == 4


# --------------------------------------------------------------------------- #
# assess_referral
# --------------------------------------------------------------------------- #

def test_illness_flag_fires_referral():
    ref = assess_referral(flags=["fatigue_possible", "illness_or_extreme_fatigue"], pain_scores=[])
    assert ref is not None
    assert ref["pattern"] == "multi_signal_fatigue"
    assert "professional" in ref["nudge"].lower()


def test_persistent_pain_fires_referral():
    ref = assess_referral(flags=[], pain_scores=[5, 6, 7])  # 3 notable readings
    assert ref is not None
    assert ref["pattern"] == "persistent_pain"


def test_single_painful_run_does_not_fire():
    ref = assess_referral(flags=["pain_reported"], pain_scores=[7])
    assert ref is None


def test_no_red_flags_no_referral():
    assert assess_referral(flags=["fatigue_possible"], pain_scores=[2, 3]) is None
    assert assess_referral(flags=[], pain_scores=[]) is None


def test_referral_nudges_contain_no_diagnosis_verb():
    """The nudge is surfaced verbatim and must survive the medical-scope validator,
    which forbids the word 'diagnos*'."""
    for flags, pain in (
        (["illness_or_extreme_fatigue"], []),
        ([], [5, 6, 7]),
    ):
        ref = assess_referral(flags=flags, pain_scores=pain)
        assert "diagnos" not in ref["nudge"].lower()


# --------------------------------------------------------------------------- #
# review fixes: elevated personal norm + drift boundaries
# --------------------------------------------------------------------------- #

def test_elevated_personal_norm_is_flagged():
    """An objectively-high personal norm must not let 'in_line' read as fine."""
    cal = calibrate_drift(14.0, [13.0, 14.0, 13.0, 14.0])  # norm ~13.5%, above 5% guideline
    assert cal["calibrated"] is True
    assert cal["comparison"] == "in_line"
    assert cal["personal_norm_elevated"] is True
    assert "above the general" in cal["basis"].lower()


def test_normal_personal_norm_not_flagged():
    cal = calibrate_drift(6.0, [4.0, 5.0, 4.0, 5.0])  # norm ~4.5%
    assert cal["personal_norm_elevated"] is False


def test_negative_drift_handled():
    """HR decoupling improving (negative drift) reads as below the norm."""
    cal = calibrate_drift(-2.0, [5.0, 6.0, 5.0, 6.0])
    assert cal["calibrated"] is True
    assert cal["comparison"] == "below"


def test_delta_exactly_at_margin_is_anomalous():
    """delta == +3.0 (the margin) counts as above (>= boundary)."""
    cal = calibrate_drift(8.5, [5.0, 5.5, 5.5, 6.0])  # mean 5.5, delta 3.0
    assert cal["delta_pct"] == 3.0
    assert cal["comparison"] == "above"
