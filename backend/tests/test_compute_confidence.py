"""Confidence-computation source awareness (#170 finding F4).

The two interval sanity checks (work_time_implausibly_high, no_warmup_detected)
are stream-detection heuristics. Recorded laps are the runner's own ground-truth
segmentation, so those checks must not fire on a source="recorded_laps"
structure and depress its confidence — that contradicts the high
detection_confidence the same structure carries.
"""

from types import SimpleNamespace

from app.services.analysis._orchestrator import compute_confidence
from app.services.analysis.composition import IntervalSession


def _activity(avg_hr=160.0):
    return SimpleNamespace(avg_hr=avg_hr)


def _full_streams():
    # Non-empty and GPS-bearing, so the only confidence reasons in play are the
    # interval checks under test.
    return {"latlng": [[0.0, 0.0]], "velocity_smooth": [3.0]}


# A long session with no warmup: both stream-era checks would fire if applied.
_LONG_NO_WARMUP_SUMMARY = {"total_work_time_s": 3000}


def test_recorded_laps_structure_is_not_penalised():
    interval_structure = {
        "source": "recorded_laps",
        "summary": _LONG_NO_WARMUP_SUMMARY,
        "warmup_duration_s": None,
    }
    workout_match = {"confidence_reasons": [], "match_score": 1.0}

    level, reasons = compute_confidence(
        _activity(), _full_streams(), check_in=SimpleNamespace(),
        interval_session=IntervalSession(structure=interval_structure), workout_match=workout_match,
    )

    assert "work_time_implausibly_high" not in reasons
    assert "no_warmup_detected" not in reasons
    assert level == "high"


def test_stream_sourced_structure_still_penalised():
    # Same summary, but inferred from the stream (no source marker): the checks
    # apply and drop confidence.
    interval_structure = {
        "summary": _LONG_NO_WARMUP_SUMMARY,
        "warmup_duration_s": None,
    }
    workout_match = {"confidence_reasons": [], "match_score": 1.0}

    level, reasons = compute_confidence(
        _activity(), _full_streams(), check_in=SimpleNamespace(),
        interval_session=IntervalSession(structure=interval_structure), workout_match=workout_match,
    )

    assert "work_time_implausibly_high" in reasons
    assert "no_warmup_detected" in reasons
    assert level == "medium"  # one critical hit


# --- #169: interval-detection signals must not leak into the overall
# confidence of a non-interval activity. When the structure axis did not
# resolve to intervals, the single gate (`IntervalSession`, #701) yields a
# structure-less session, so workout_match still reports "no_intervals_detected"
# but it must not merge into the activity-level reasons.

def test_non_interval_activity_does_not_carry_no_intervals_detected():
    # A steady run: no interval structure, but match_planned_to_detected still
    # produced its "no_intervals_detected" reason. It must not leak through.
    workout_match = {
        "confidence_reasons": ["no_intervals_detected"],
        "match_score": None,
    }

    level, reasons = compute_confidence(
        _activity(), _full_streams(), check_in=SimpleNamespace(),
        interval_session=IntervalSession(structure=None), workout_match=workout_match,
    )

    assert "no_intervals_detected" not in reasons


def test_non_interval_activity_with_complete_data_stays_high():
    # Otherwise-complete continuous activity (HR + GPS streams + check-in): the
    # only thing that could drop it from high is the leaked interval reason.
    workout_match = {
        "confidence_reasons": ["no_intervals_detected"],
        "match_score": None,
    }

    level, reasons = compute_confidence(
        _activity(), _full_streams(), check_in=SimpleNamespace(),
        interval_session=IntervalSession(structure=None), workout_match=workout_match,
    )

    assert reasons == []
    assert level == "high"


def test_interval_session_still_merges_its_reasons():
    # When the activity IS an interval session, the workout-match reasons and
    # the match-score mismatch check still flow into overall confidence.
    interval_structure = {"summary": {"total_work_time_s": 600}, "warmup_duration_s": 120}
    workout_match = {
        "confidence_reasons": ["high_rep_distance_variability"],
        "match_score": 0.5,  # < 0.7 -> interval_structure_mismatch
    }

    level, reasons = compute_confidence(
        _activity(), _full_streams(), check_in=SimpleNamespace(),
        interval_session=IntervalSession(structure=interval_structure), workout_match=workout_match,
    )

    assert "high_rep_distance_variability" in reasons
    assert "interval_structure_mismatch" in reasons
