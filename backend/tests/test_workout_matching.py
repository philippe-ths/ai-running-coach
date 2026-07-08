"""Tests for workout matching and interval KPI computation."""

import numpy as np
import pytest

from app.services.analysis.workout_matching import (
    match_planned_to_detected,
    build_interval_kpis,
)


def _make_interval_structure(
    reps: int = 4,
    work_duration: int = 90,
    rest_duration: int = 60,
    work_speed: float = 4.5,
    rest_speed: float = 2.0,
    distance_per_rep: float = 400.0,
    include_hr: bool = True,
    seed: int = 42,
):
    """Build a synthetic interval_structure dict matching detect_intervals output.

    The per-rep jitter on distance and speed is drawn from a locally-seeded
    generator so the fixture is deterministic regardless of test order. Pass
    a different seed when a test wants different jitter.
    """
    rng = np.random.default_rng(seed)
    work_segments = []
    rest_segments = []
    for i in range(reps):
        seg_distance = round(distance_per_rep + rng.uniform(-20, 20), 1)
        seg = {
            "segment_number": i + 1,
            "start_time_s": 300 + i * (work_duration + rest_duration),
            "duration_s": work_duration,
            "distance_m": seg_distance,
            "avg_speed_mps": round(work_speed + rng.uniform(-0.2, 0.2), 2),
            "pace_s_per_km": int(round(work_duration / (seg_distance / 1000.0))),
            "avg_hr": 170.0 if include_hr else None,
            "peak_hr": 178.0 if include_hr else None,
            "peak_hr_pct_max": 94 if include_hr else None,
        }
        work_segments.append(seg)
        if i < reps - 1:
            rest_segments.append({
                "segment_number": i + 1,
                "duration_s": rest_duration,
                "avg_hr": 145.0 if include_hr else None,
                "restart_hr": 145.0 if include_hr else None,
                "restart_pct_max": 76 if include_hr else None,
                "hr_recovery_bpm": 33.0 if include_hr else None,
            })

    distances = [w["distance_m"] for w in work_segments]
    speeds = [w["avg_speed_mps"] for w in work_segments]
    durations = [w["duration_s"] for w in work_segments]

    return {
        "warmup_duration_s": 300,
        "cooldown_duration_s": 200,
        "work_segments": work_segments,
        "rest_segments": rest_segments,
        "summary": {
            "total_work_time_s": sum(durations),
            "total_rest_time_s": sum(r["duration_s"] for r in rest_segments),
            "work_to_rest_ratio": round(sum(durations) / sum(r["duration_s"] for r in rest_segments), 2) if rest_segments else None,
            "rep_count": reps,
            "avg_work_duration_s": round(np.mean(durations)),
            "work_duration_cv": round(float(np.std(durations, ddof=1) / np.mean(durations) * 100), 1) if len(durations) > 1 else None,
            "avg_work_speed_mps": round(float(np.mean(speeds)), 2),
            "work_speed_cv": round(float(np.std(speeds, ddof=1) / np.mean(speeds) * 100), 1) if len(speeds) > 1 else None,
            "avg_rest_duration_s": round(np.mean([r["duration_s"] for r in rest_segments])) if rest_segments else None,
            "avg_hr_recovery_bpm": 33.0 if include_hr and rest_segments else None,
            "consistency_score": "high",
        },
    }


class TestMatchPlannedToDetected:
    def test_no_intervals_detected(self):
        result = match_planned_to_detected(None, {"reps_planned": 8})
        assert result["detection_confidence"] == "low"
        assert "no_intervals_detected" in result["confidence_reasons"]
        assert result["match_score"] is None
        assert result["detected_workout"] is None

    def test_no_planned_workout(self):
        structure = _make_interval_structure(reps=4)
        result = match_planned_to_detected(structure, None)
        assert result["detected_workout"] is not None
        assert result["detected_workout"]["reps_detected"] == 4
        assert "no_planned_workout" in result["confidence_reasons"]
        assert result["match_score"] is None

    def test_good_match(self):
        structure = _make_interval_structure(
            reps=8, work_duration=100, distance_per_rep=400
        )
        planned = {"reps_planned": 8, "rep_distance_m": 400, "rest_s": 60}
        result = match_planned_to_detected(structure, planned)
        assert result["match_score"] is not None
        assert result["match_score"] >= 0.7
        assert result["detection_confidence"] in ("high", "medium")

    def test_rep_count_mismatch(self):
        structure = _make_interval_structure(reps=5)
        planned = {"reps_planned": 8, "rep_distance_m": 400}
        result = match_planned_to_detected(structure, planned)
        # 5/8 = 0.625 rep ratio, so match score should reflect the mismatch
        assert result["match_score"] < 0.85
        reasons = result["confidence_reasons"]
        assert any("rep_count_mismatch" in r for r in reasons)

    def test_distance_mismatch(self):
        # Detected ~400m but planned 800m
        structure = _make_interval_structure(reps=4, distance_per_rep=400)
        planned = {"reps_planned": 4, "rep_distance_m": 800}
        result = match_planned_to_detected(structure, planned)
        assert result["match_score"] <= 0.75
        assert "rep_distance_mismatch" in result["confidence_reasons"]

    def test_empty_work_segments(self):
        structure = {"work_segments": [], "rest_segments": [], "summary": {}}
        result = match_planned_to_detected(structure, {"reps_planned": 4})
        assert result["detection_confidence"] == "low"
        assert "no_work_segments" in result["confidence_reasons"]

    def test_high_distance_variability_flagged(self):
        structure = _make_interval_structure(reps=4, distance_per_rep=400)
        # Inject outlier distance
        structure["work_segments"][2]["distance_m"] = 1081.0
        result = match_planned_to_detected(structure, None)
        reasons = result["confidence_reasons"]
        assert any("distance_outlier" in r for r in reasons)

    def test_detection_confidence_without_plan_high_consistency(self):
        structure = _make_interval_structure(reps=6)
        result = match_planned_to_detected(structure, None)
        # High consistency + no outliers → medium (not high, since no plan)
        assert result["detection_confidence"] == "medium"

    def test_detection_confidence_without_plan_low_consistency(self):
        structure = _make_interval_structure(reps=3)
        structure["summary"]["consistency_score"] = "low"
        result = match_planned_to_detected(structure, None)
        assert result["detection_confidence"] == "low"

    def test_detection_confidence_recorded_laps_high(self):
        """Recorded laps are ground-truth structure -> high detection confidence (#170)."""
        structure = _make_interval_structure(reps=6)
        structure["source"] = "recorded_laps"
        result = match_planned_to_detected(structure, None)
        assert result["detection_confidence"] == "high"

    def test_detection_confidence_recorded_laps_high_even_with_variable_reps(self):
        """Detection is certain from laps even when the reps themselves varied;
        consistency is a separate signal and does not lower detection confidence."""
        structure = _make_interval_structure(reps=5)
        structure["source"] = "recorded_laps"
        structure["summary"]["consistency_score"] = "low"
        result = match_planned_to_detected(structure, None)
        assert result["detection_confidence"] == "high"

    def test_recorded_laps_variability_not_a_confidence_reason(self):
        """A recorded-laps ladder varies by design; that variability is the
        runner's workout, not detection uncertainty, so it must not leak as a
        confidence-lowering reason (#170 review finding)."""
        structure = _make_interval_structure(reps=4, distance_per_rep=400)
        structure["source"] = "recorded_laps"
        structure["work_segments"][1]["distance_m"] = 800.0
        structure["work_segments"][2]["distance_m"] = 200.0
        result = match_planned_to_detected(structure, None)
        reasons = result["confidence_reasons"]
        assert not any("variability" in r for r in reasons), reasons
        assert not any("outlier" in r for r in reasons), reasons

    def test_recorded_laps_sets_match_score(self):
        """Recorded laps are the runner's own segmentation -- a perfect de-facto
        plan -- so match_score is set, keeping the prompt's high+match_score gate
        consistent with the validator (#170 review finding)."""
        structure = _make_interval_structure(reps=6)
        structure["source"] = "recorded_laps"
        result = match_planned_to_detected(structure, None)
        assert result["match_score"] == 1.0
        assert result["detection_confidence"] == "high"

    def test_recorded_laps_confidence_high_even_with_plan_deviation(self):
        """When a planned workout is present AND source is recorded_laps, detection
        confidence must stay 'high' and variability/outlier reasons must not appear
        (#190). The runner's own lap marks are ground-truth detection regardless of
        how far the run deviated from the plan. match_score reflecting plan adherence
        (plan deviation → low adherence) is acceptable."""
        # Build a ladder: reps vary a lot so variability reasons would fire
        structure = _make_interval_structure(reps=4, distance_per_rep=400)
        structure["source"] = "recorded_laps"
        # Force high distance variability by injecting extreme rep distances
        structure["work_segments"][0]["distance_m"] = 200.0
        structure["work_segments"][1]["distance_m"] = 400.0
        structure["work_segments"][2]["distance_m"] = 800.0
        structure["work_segments"][3]["distance_m"] = 1200.0
        # Planned workout that differs from the execution (e.g. 8x400 planned, 4 reps done)
        planned = {"reps_planned": 8, "rep_distance_m": 400, "rest_s": 60}
        result = match_planned_to_detected(structure, planned)
        # Detection certainty: the laps are ground truth, must be high
        assert result["detection_confidence"] == "high", (
            f"Expected 'high' but got {result['detection_confidence']!r}; "
            f"reasons: {result['confidence_reasons']}"
        )
        # Variability and outlier reasons must not appear (they are plan-adherence noise)
        reasons = result["confidence_reasons"]
        assert not any("variability" in r for r in reasons), (
            f"Variability reason leaked: {reasons}"
        )
        assert not any("outlier" in r for r in reasons), (
            f"Outlier reason leaked: {reasons}"
        )
        # match_score may reflect plan deviation (low adherence is fine here)
        assert result["match_score"] is not None


class TestBuildIntervalKPIs:
    def test_basic_kpis(self):
        structure = _make_interval_structure(reps=4, include_hr=True)
        kpis = build_interval_kpis(structure)
        assert "rep_pace_consistency_cv" in kpis
        assert "pace" in kpis
        assert "recovery_floor" in kpis
        assert "work_rest_ratio" in kpis
        assert "total_z4_plus_s" in kpis

    def test_pace_fade_reads_as_fading(self):
        structure = _make_interval_structure(reps=5)
        # First rep fast, last rep clearly slower (bigger s/km).
        structure["work_segments"][0]["pace_s_per_km"] = 225
        structure["work_segments"][-1]["pace_s_per_km"] = 248
        kpis = build_interval_kpis(structure)
        assert kpis["pace"]["direction"] == "fading"
        assert kpis["pace"]["fade_s_per_km"] == 23
        assert kpis["pace"]["first_s_per_km"] == 225
        assert kpis["pace"]["last_s_per_km"] == 248

    def test_equal_paces_read_as_holding(self):
        structure = _make_interval_structure(reps=4)
        for w in structure["work_segments"]:
            w["pace_s_per_km"] = 225
        kpis = build_interval_kpis(structure)
        assert kpis["pace"]["direction"] == "holding"
        assert kpis["pace"]["fade_s_per_km"] == 0

    def test_negative_split_reads_as_such(self):
        structure = _make_interval_structure(reps=4)
        structure["work_segments"][0]["pace_s_per_km"] = 240
        structure["work_segments"][-1]["pace_s_per_km"] = 222
        kpis = build_interval_kpis(structure)
        assert kpis["pace"]["direction"] == "negative_split"

    def test_recovery_floor_rising_is_the_fatigue_tell(self):
        structure = _make_interval_structure(reps=4, include_hr=True)
        # Runner restarts each rep progressively hotter -> rising floor.
        for pct, rest in zip([72, 77, 81], structure["rest_segments"]):
            rest["restart_pct_max"] = pct
        kpis = build_interval_kpis(structure)
        assert kpis["recovery_floor"]["trend"] == "rising"
        assert kpis["recovery_floor"]["first_pct_max"] == 72
        assert kpis["recovery_floor"]["last_pct_max"] == 81
        assert kpis["recovery_floor"]["delta_pct"] == 9

    def test_flat_recovery_floor_reads_flat(self):
        structure = _make_interval_structure(reps=4, include_hr=True)
        for rest in structure["rest_segments"]:
            rest["restart_pct_max"] = 75
        kpis = build_interval_kpis(structure)
        assert kpis["recovery_floor"]["trend"] == "flat"

    def test_z4_plus_only_when_calibrated(self):
        structure = _make_interval_structure(reps=4)
        zones = {"Z1": 60, "Z2": 120, "Z3": 180, "Z4": 200, "Z5": 100}

        # Not calibrated → None
        kpis = build_interval_kpis(structure, zones_calibrated=False, time_in_zones=zones)
        assert kpis["total_z4_plus_s"] is None

        # Calibrated → Z4 + Z5
        kpis = build_interval_kpis(structure, zones_calibrated=True, time_in_zones=zones)
        assert kpis["total_z4_plus_s"] == 300

    def test_single_rep_no_pace_trend(self):
        structure = _make_interval_structure(reps=1)
        kpis = build_interval_kpis(structure)
        assert kpis["pace"] is None

    def test_no_hr_recovery_floor_none(self):
        # Without HR the segments carry no restart floor -> abstain.
        structure = _make_interval_structure(reps=4, include_hr=False)
        kpis = build_interval_kpis(structure)
        assert kpis["recovery_floor"] is None
