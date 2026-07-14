"""ADR 0026 Slice 5 (#682): the completed coach LLM view (`refine_coach_view` / `coach_llm_view`).

Pure unit tests over hand-built (already-framed) grouped dicts: the six reshapes a good coach
would otherwise misread, plus the gating that keeps every prior prompt byte-identical. The
in-pack byte-stability against the real builder is pinned in test_salience_dropped_in_pack.py
and test_coach_framing_in_pack.py.
"""

import copy

from app.services.coach.coach_framing import refine_coach_view, coach_llm_view

GROUPED5 = "coach_message_lean_grouped_v5"
GROUPED4 = "coach_message_lean_grouped_v4"


def _framed_pack():
    """A minimal already-framed grouped view carrying every section the reshape touches."""
    return {
        "safety_rules": {"never_diagnose": True},
        "this_run": {
            "metrics": {
                "effort": "tempo",
                "hr_drift": 8,
                "interval_structure": {
                    "source": "recorded_laps",
                    "warmup": "7:30",
                    "cooldown": "3:59",
                    "work_segments": [
                        {"segment_number": 1, "distance_m": 400, "avg_hr": 164, "peak_hr": 181, "duration": "1:30"},
                        {"segment_number": 2, "distance_m": 400, "avg_hr": 173, "peak_hr": 184, "duration": "1:29"},
                    ],
                    "rest_segments": [
                        {"segment_number": 1, "avg_hr": 165, "hr_recovery_bpm": 15.9, "duration": "1:29"},
                    ],
                    "summary": {
                        "rep_count": 2, "work_to_rest_ratio": 1.24, "consistency_score": "high",
                        "work_speed_cv": 4.2, "work_duration_cv": 4.2, "avg_hr_recovery_bpm": 10.2,
                        "avg_work_duration": "1:29", "avg_rest_duration": "1:29", "avg_work_pace": "3:55/km",
                        "total_work_time": "3:00", "total_rest_time": "1:29",
                    },
                },
                "interval_kpis": {
                    "rep_pace_consistency_cv": 4.2, "first_vs_last_fade": 0.95,
                    "recovery_quality_per_60s": 6.9, "work_rest_ratio": 1.24, "total_z4_plus": "14:28",
                },
                "workout_match": {
                    "match_score": 1.0, "detection_confidence": "high",
                    "confidence_reasons": ["no_planned_workout"],
                    "detected_workout": {"reps_detected": 2},
                },
            },
        },
        "right_now": {
            "readiness": {
                "fitness": 125.9, "fatigue": 138.0, "form": -12.1, "ramp_rate": 2.9,
                "condition": "balanced", "trend": "steady", "ramp_aggressive": False,
                "warming_up": False, "sample_count": 344,
            },
            "recent_weeks": {
                "this_week": {"days": [{"activities": [{"type": "Run", "avg_hr": 115.1, "intensity": "recovery"}]}]},
                "last_week": {"days": [{"activities": [{"type": "Walk", "avg_hr": 120.7}]}]},
            },
        },
        "the_runner": {
            "profile": {"max_hr": 191},
            "training_history": {
                "traits": {"current_vs_peak_pct": 73.0, "current_vs_peak_load_pct": 79.6,
                           "trajectory_direction": "no_norm", "trajectory_pct": None},
                "timeline": [{"label": "x", "run_share_pct": 22.2, "by_type": [{"type": "Run", "share_pct": 22.2}]}],
            },
        },
        "our_thread": {"adherence": {"prior_report_date": None, "outcomes": []}},
        "salience": {"novelty": {"has_history": True}, "safety_override": {"force_fuller": False}},
    }


def test_interval_blocks_collapse_to_one_interval_read():
    v = refine_coach_view(_framed_pack())
    m = v["this_run"]["metrics"]
    assert "interval_structure" not in m and "interval_kpis" not in m and "workout_match" not in m
    ir = m["interval_read"]
    assert ir["source"] == "recorded_laps" and ir["rep_count"] == 2
    assert ir["reps"][0] == {"n": 1, "distance_m": 400, "duration": "1:30", "avg_hr": 164,
                             "peak_hr": 181, "recovery": "1:29", "recovery_drop_bpm": 15.9}
    assert "recovery" not in ir["reps"][1]              # rep 2 has no following rest
    assert ir["rep_variation_cv"] == 4.2               # one CV, not five
    assert ir["first_vs_last_fade"] == 0.95 and ir["total_z4_plus"] == "14:28"


def test_hr_drift_and_plan_less_workout_match_dropped():
    v = refine_coach_view(_framed_pack())
    assert "hr_drift" not in v["this_run"]["metrics"]
    assert "workout_match" not in v["this_run"]["metrics"]


def test_workout_match_kept_when_a_plan_exists():
    p = _framed_pack()
    p["this_run"]["metrics"]["workout_match"]["confidence_reasons"] = []  # a real plan
    v = refine_coach_view(p)
    assert "workout_match" in v["this_run"]["metrics"]


def test_readiness_reduced_to_verdict():
    v = refine_coach_view(_framed_pack())
    assert v["right_now"]["readiness"] == {"condition": "balanced", "trend": "steady"}


def test_readiness_surfaces_aggressive_ramp_flag_only_when_true():
    p = _framed_pack()
    p["right_now"]["readiness"]["ramp_aggressive"] = True
    v = refine_coach_view(p)
    assert v["right_now"]["readiness"]["ramp_aggressive"] is True


def test_recent_weeks_per_session_hr_is_plain_bpm():
    # u.bpm rounds to a plain int (the Slice-4 convention for non-headline HRs), replacing
    # the raw "115.1" float so the pack's HR units read consistently.
    v = refine_coach_view(_framed_pack())
    assert v["right_now"]["recent_weeks"]["this_week"]["days"][0]["activities"][0]["avg_hr"] == 115
    assert v["right_now"]["recent_weeks"]["last_week"]["days"][0]["activities"][0]["avg_hr"] == 121


def test_training_history_sentinel_and_dupes_cleaned():
    v = refine_coach_view(_framed_pack())
    traits = v["the_runner"]["training_history"]["traits"]
    assert "trajectory_direction" not in traits and "trajectory_pct" not in traits
    assert traits["current_vs_peak_distance_pct"] == 73.0 and "current_vs_peak_pct" not in traits
    assert traits["current_vs_peak_load_pct"] == 79.6
    assert "run_share_pct" not in v["the_runner"]["training_history"]["timeline"][0]


def test_empty_our_thread_dropped_but_populated_kept():
    assert "our_thread" not in refine_coach_view(_framed_pack())
    p = _framed_pack()
    p["our_thread"]["adherence"]["outcomes"] = [{"step": "x", "label": "acted_on"}]
    assert "our_thread" in refine_coach_view(p)


def test_refine_does_not_mutate_input():
    p = _framed_pack()
    before = copy.deepcopy(p)
    refine_coach_view(p)
    assert p == before


def test_coach_llm_view_gating():
    p = _framed_pack()
    # grouped_v5: refine applied (interval_read appears, salience dropped).
    v5 = coach_llm_view(p, GROUPED5)
    assert "interval_read" in v5["this_run"]["metrics"] and "salience" not in v5
    # grouped_v4: frame only, NO refine (interval blocks + salience remain).
    v4 = coach_llm_view(p, GROUPED4)
    assert "interval_structure" in v4["this_run"]["metrics"] and "salience" in v4
    # opener mode on grouped_v5 keeps salience (fuller-only drop) but still refines.
    v5o = coach_llm_view(p, GROUPED5, mode="opener")
    assert "salience" in v5o and "interval_read" in v5o["this_run"]["metrics"]
    # unknown prompt: untouched.
    assert coach_llm_view(p, None) == p
