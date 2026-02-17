"""Tests for workout matching and interval KPI computation."""

import numpy as np
import pytest

from app.services.processing.workout_matching import (
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
):
    """Build a synthetic interval_structure dict matching detect_intervals output."""
    work_segments = []
    rest_segments = []
    for i in range(reps):
        seg = {
            "segment_number": i + 1,
            "start_time_s": 300 + i * (work_duration + rest_duration),
            "duration_s": work_duration,
            "distance_m": round(distance_per_rep + np.random.uniform(-20, 20), 1),
            "avg_speed_mps": round(work_speed + np.random.uniform(-0.2, 0.2), 2),
            "avg_hr": 170.0 if include_hr else None,
            "peak_hr": 178.0 if include_hr else None,
        }
        work_segments.append(seg)
        if i < reps - 1:
            rest_segments.append({
                "segment_number": i + 1,
                "duration_s": rest_duration,
                "avg_hr": 145.0 if include_hr else None,
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


class TestBuildIntervalKPIs:
    def test_basic_kpis(self):
        structure = _make_interval_structure(reps=4, include_hr=True)
        kpis = build_interval_kpis(structure)
        assert "rep_pace_consistency_cv" in kpis
        assert "first_vs_last_fade" in kpis
        assert "recovery_quality_per_60s" in kpis
        assert "work_rest_ratio" in kpis
        assert "total_z4_plus_s" in kpis

    def test_first_vs_last_fade(self):
        structure = _make_interval_structure(reps=5)
        # Make last rep slower
        structure["work_segments"][-1]["avg_speed_mps"] = 3.5
        structure["work_segments"][0]["avg_speed_mps"] = 4.5
        kpis = build_interval_kpis(structure)
        assert kpis["first_vs_last_fade"] is not None
        assert kpis["first_vs_last_fade"] < 0.9  # significant fade

    def test_no_fade_equal_speeds(self):
        structure = _make_interval_structure(reps=4)
        # Force equal speeds
        for w in structure["work_segments"]:
            w["avg_speed_mps"] = 4.5
        kpis = build_interval_kpis(structure)
        assert kpis["first_vs_last_fade"] == 1.0

    def test_recovery_quality_normalized_to_60s(self):
        structure = _make_interval_structure(reps=4, rest_duration=120, include_hr=True)
        # Rest is 120s with 33bpm drop → per 60s = ~16.5
        kpis = build_interval_kpis(structure)
        rq = kpis["recovery_quality_per_60s"]
        assert rq is not None
        assert rq > 0

    def test_z4_plus_only_when_calibrated(self):
        structure = _make_interval_structure(reps=4)
        zones = {"Z1": 60, "Z2": 120, "Z3": 180, "Z4": 200, "Z5": 100}

        # Not calibrated → None
        kpis = build_interval_kpis(structure, zones_calibrated=False, time_in_zones=zones)
        assert kpis["total_z4_plus_s"] is None

        # Calibrated → Z4 + Z5
        kpis = build_interval_kpis(structure, zones_calibrated=True, time_in_zones=zones)
        assert kpis["total_z4_plus_s"] == 300

    def test_single_rep_no_fade(self):
        structure = _make_interval_structure(reps=1)
        kpis = build_interval_kpis(structure)
        assert kpis["first_vs_last_fade"] is None

    def test_no_hr_recovery_quality_none(self):
        structure = _make_interval_structure(reps=4, include_hr=False)
        kpis = build_interval_kpis(structure)
        assert kpis["recovery_quality_per_60s"] is None
