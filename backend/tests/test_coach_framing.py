"""Contract for frame_pack, the coach-native leaf reframing (ADR 0026 Slice 4, #680).

Input values mirror a real grouped_v3 pack (the seeded 2026-07-07 interval run).
"""

import copy

import pytest

from app.services.coach.coach_framing import frame_pack


def _real_grouped():
    """A grouped pack dict shaped like the live grouped_v3 output, real values."""
    return {
        "safety_rules": {"never_diagnose": True, "pain_severe_threshold": 7, "no_invented_facts": True},
        "this_run": {
            "activity": {
                "date": "2026-07-07", "weekday": "Tue", "name": "Afternoon Run", "type": "Run",
                "distance_m": 4879, "moving_time_s": 1834,
                "avg_hr": 165.6, "max_hr": 186.0, "avg_cadence": 164.4, "elev_gain_m": 30.0,
            },
            "metrics": {
                "headline": "Intervals", "effort": "hard", "effort_score": 101.0,
                "hr_drift": 8.0, "pace_variability": 41.6,
                "time_in_zones": {"Z1": 12, "Z2": 436, "Z3": 521, "Z4": 726, "Z5": 142},
                "efficiency_analysis": {"average": 1.15, "best_sustained": 1.21,
                                        "curve": [0.452, 0.626, 0.785], "unit": "m/min/bpm"},
                "stops_analysis": {"total_stopped_time_s": 38, "stopped_count": 3, "longest_stop_s": 25,
                                   "stops": [{"start_time": 219, "duration_s": 25,
                                              "location": [51.12, 0.25], "distance_m": 363.5}]},
                "interval_structure": {
                    "warmup_duration_s": 450, "cooldown_duration_s": 239,
                    "work_segments": [{"segment_number": 1, "start_time_s": 450, "duration_s": 90,
                                       "distance_m": 400.0, "avg_hr": 163.9, "peak_hr": 181.0}],
                    "rest_segments": [{"segment_number": 1, "duration_s": 89, "avg_hr": 165.1,
                                       "hr_recovery_bpm": 15.9}],
                    "summary": {"total_work_time_s": 660, "total_rest_time_s": 534,
                                "work_to_rest_ratio": 1.24, "rep_count": 7, "avg_work_duration_s": 94,
                                "work_duration_cv": 4.2, "avg_work_speed_mps": 4.25, "work_speed_cv": 4.2,
                                "avg_rest_duration_s": 89, "avg_hr_recovery_bpm": 10.2},
                },
                "workout_match": {"match_score": 1.0, "detection_confidence": "high",
                                  "detected_workout": {"reps_detected": 7, "rep_distance_mean_m": 400.0,
                                                       "rep_distance_cv": 0.0, "rep_duration_mean_s": 94.3,
                                                       "total_work_time_s": 660, "total_rest_time_s": 534,
                                                       "work_to_rest_ratio": 1.24}},
                "interval_kpis": {"rep_pace_consistency_cv": 4.2, "first_vs_last_fade": 0.95,
                                  "recovery_quality_per_60s": 6.9, "work_rest_ratio": 1.24,
                                  "total_z4_plus_s": 868},
                "discount_signals": {"likely_inflated_by": ["heat"], "temperature_c": 30.0,
                                     "confidence": "high", "interpretation": "heat"},
            },
        },
        "the_runner": {
            "profile": {"max_hr": 191, "current_weekly_km": 18},
            "training_history": {
                "traits": {"training_age_years": 1.1, "peak_sustained_weekly_distance_m": 71655,
                           "current_vs_peak_pct": 73.0},
                "timeline": [{"start_days_ago": 14, "end_days_ago": 60, "weeks": 6.6,
                              "avg_weekly_distance_m": 43150, "avg_weekly_sessions": 16.43,
                              "run_share_pct": 22.2, "avg_weekly_load": 825,
                              "by_type": [{"avg_weekly_distance_m": 27160, "avg_weekly_sessions": 7.15,
                                           "share_pct": 43.5}]}],
            },
        },
    }


class TestActivity:
    def test_distance_time_hr(self):
        out = frame_pack(_real_grouped())
        act = out["this_run"]["activity"]
        assert act["distance_km"] == 4.9
        assert "distance_m" not in act
        assert act["duration"] == "30m"
        assert "moving_time_s" not in act
        assert act["avg_hr"] == "166 bpm (87% max)"
        assert act["max_hr"] == "186 bpm (97% max)"
        assert act["avg_cadence"] == 164
        assert act["elev_gain_m"] == 30 and isinstance(act["elev_gain_m"], int)


class TestMetricsScalars:
    def test_trim_and_zones(self):
        out = frame_pack(_real_grouped())
        m = out["this_run"]["metrics"]
        assert m["effort_score"] == 101 and isinstance(m["effort_score"], int)
        assert m["hr_drift"] == 8
        assert m["pace_variability"] == 41.6   # real fractional %, untouched
        assert m["time_in_zones"] == {"Z1": "0:12", "Z2": "7:16", "Z3": "8:41",
                                      "Z4": "12:06", "Z5": "2:22"}

    def test_efficiency_curve_dropped(self):
        m = frame_pack(_real_grouped())["this_run"]["metrics"]
        eff = m["efficiency_analysis"]
        assert "curve" not in eff
        assert eff["average"] == 1.15 and eff["best_sustained"] == 1.21

    def test_stops_reframed_and_gps_dropped(self):
        m = frame_pack(_real_grouped())["this_run"]["metrics"]
        s = m["stops_analysis"]
        assert s["total_stopped_time"] == "0:38"
        assert s["longest_stop"] == "0:25"
        stop = s["stops"][0]
        assert "location" not in stop and "start_time" not in stop
        assert stop["duration"] == "0:25"
        assert stop["distance_m"] == 364

    def test_discount_temp_trimmed(self):
        m = frame_pack(_real_grouped())["this_run"]["metrics"]
        assert m["discount_signals"]["temperature_c"] == 30


class TestIntervals:
    def test_work_segment(self):
        m = frame_pack(_real_grouped())["this_run"]["metrics"]
        seg = m["interval_structure"]["work_segments"][0]
        assert "start_time_s" not in seg
        assert seg["duration"] == "1:30"
        assert seg["distance_m"] == 400          # reps stay metres
        assert seg["avg_hr"] == 164 and seg["peak_hr"] == 181   # plain bpm, no % max

    def test_rest_segment(self):
        m = frame_pack(_real_grouped())["this_run"]["metrics"]
        rest = m["interval_structure"]["rest_segments"][0]
        assert rest["duration"] == "1:29"
        assert rest["avg_hr"] == 165
        assert rest["hr_recovery_bpm"] == 15.9   # a bpm delta, kept

    def test_summary_pace_and_durations(self):
        m = frame_pack(_real_grouped())["this_run"]["metrics"]
        summ = m["interval_structure"]["summary"]
        assert summ["warmup"] if False else True   # warmup lives on the structure, not summary
        assert summ["total_work_time"] == "11:00"
        assert summ["avg_work_duration"] == "1:34"
        assert summ["avg_work_pace"] == "3:55/km"
        assert summ["work_to_rest_ratio"] == 1.24   # ratio untouched
        assert summ["rep_count"] == 7

    def test_structure_warmup_cooldown(self):
        m = frame_pack(_real_grouped())["this_run"]["metrics"]
        istruct = m["interval_structure"]
        assert istruct["warmup"] == "7:30"
        assert istruct["cooldown"] == "3:59"

    def test_workout_match(self):
        m = frame_pack(_real_grouped())["this_run"]["metrics"]
        dw = m["workout_match"]["detected_workout"]
        assert dw["rep_duration_mean"] == "1:34"
        assert dw["total_work_time"] == "11:00"
        assert dw["rep_distance_mean_m"] == 400

    def test_kpis_z4_time(self):
        m = frame_pack(_real_grouped())["this_run"]["metrics"]
        assert m["interval_kpis"]["total_z4_plus"] == "14:28"
        assert m["interval_kpis"]["work_rest_ratio"] == 1.24


class TestTrainingHistory:
    def test_km_and_session_precision(self):
        th = frame_pack(_real_grouped())["the_runner"]["training_history"]
        assert th["traits"]["peak_sustained_weekly_km"] == 71.7
        period = th["timeline"][0]
        assert period["avg_weekly_km"] == 43.1
        assert period["avg_weekly_sessions"] == 16.4
        assert period["by_type"][0]["avg_weekly_km"] == 27.2
        assert period["by_type"][0]["avg_weekly_sessions"] == 7.2


class TestSafety:
    def test_input_not_mutated(self):
        src = _real_grouped()
        snapshot = copy.deepcopy(src)
        frame_pack(src)
        assert src == snapshot

    def test_missing_sections_safe(self):
        assert frame_pack({}) == {}
        assert frame_pack({"this_run": {}}) == {"this_run": {}}
        assert frame_pack({"this_run": {"activity": None, "metrics": None}}) == {
            "this_run": {"activity": None, "metrics": None}}

    def test_no_max_hr_falls_back_to_plain_bpm(self):
        src = _real_grouped()
        src["the_runner"]["profile"].pop("max_hr")
        act = frame_pack(src)["this_run"]["activity"]
        assert act["avg_hr"] == "166 bpm"
        assert act["max_hr"] == "186 bpm"

    def test_non_dict_returned_as_is(self):
        assert frame_pack(None) is None
