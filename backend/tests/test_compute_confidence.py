"""Confidence-computation source awareness (#170 finding F4).

The two interval sanity checks (work_time_implausibly_high, no_warmup_detected)
are stream-detection heuristics. Recorded laps are the runner's own ground-truth
segmentation, so those checks must not fire on a source="recorded_laps"
structure and depress its confidence — that contradicts the high
detection_confidence the same structure carries.
"""

from types import SimpleNamespace

from app.services.analysis._orchestrator import compute_confidence


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
        interval_structure=interval_structure, workout_match=workout_match,
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
        interval_structure=interval_structure, workout_match=workout_match,
    )

    assert "work_time_implausibly_high" in reasons
    assert "no_warmup_detected" in reasons
    assert level == "medium"  # one critical hit
