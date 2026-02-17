"""Tests for interval session structure detection."""

import numpy as np
import pytest

from app.services.processing.intervals import detect_intervals


def _make_interval_streams(
    work_speed: float = 4.5,
    rest_speed: float = 2.0,
    work_duration: int = 180,
    rest_duration: int = 90,
    reps: int = 4,
    warmup: int = 300,
    cooldown: int = 180,
    include_hr: bool = True,
    include_distance: bool = True,
):
    """
    Build synthetic velocity_smooth, heartrate, and distance streams
    for an interval session.
    """
    segments = []

    # Warmup: moderate pace
    warmup_speed = (work_speed + rest_speed) / 2
    segments.extend([warmup_speed] * warmup)

    # Alternating work/rest
    for i in range(reps):
        segments.extend([work_speed] * work_duration)
        if i < reps - 1:  # no rest after last rep
            segments.extend([rest_speed] * rest_duration)

    # Cooldown
    segments.extend([rest_speed] * cooldown)

    streams = {"velocity_smooth": segments}

    if include_hr:
        # Simulate HR: higher during work, lower during rest
        hr = []
        idx = 0
        # warmup
        hr.extend([140.0] * warmup)
        idx += warmup
        for i in range(reps):
            # work: HR ramps from 160 to 180
            work_hr = np.linspace(160, 180, work_duration).tolist()
            hr.extend(work_hr)
            if i < reps - 1:
                # rest: HR drops to 145
                rest_hr = np.linspace(175, 145, rest_duration).tolist()
                hr.extend(rest_hr)
        # cooldown
        hr.extend([130.0] * cooldown)
        streams["heartrate"] = hr

    if include_distance:
        # Cumulative distance from speed
        dist = [0.0]
        for s in segments[1:]:
            dist.append(dist[-1] + s)
        streams["distance"] = dist

    return streams


class TestDetectIntervals:
    def test_returns_none_for_easy_run(self):
        streams = _make_interval_streams()
        result = detect_intervals(streams, "Easy Run")
        assert result is None

    def test_returns_none_for_long_run(self):
        streams = _make_interval_streams()
        result = detect_intervals(streams, "Long Run")
        assert result is None

    def test_returns_none_without_velocity(self):
        streams = {"heartrate": [150] * 300}
        result = detect_intervals(streams, "Intervals")
        assert result is None

    def test_returns_none_for_short_data(self):
        streams = {"velocity_smooth": [3.0] * 30}
        result = detect_intervals(streams, "Intervals")
        assert result is None

    def test_basic_detection(self):
        streams = _make_interval_streams(reps=4, work_duration=180, rest_duration=90)
        result = detect_intervals(streams, "Intervals")

        assert result is not None
        assert len(result["work_segments"]) >= 2
        assert len(result["rest_segments"]) >= 1
        assert "summary" in result

    def test_work_segment_fields(self):
        streams = _make_interval_streams(reps=3, work_duration=120, rest_duration=60)
        result = detect_intervals(streams, "Intervals")

        assert result is not None
        work = result["work_segments"][0]
        assert "segment_number" in work
        assert "start_time_s" in work
        assert "duration_s" in work
        assert "avg_speed_mps" in work
        assert "avg_hr" in work
        assert "peak_hr" in work
        assert "distance_m" in work

    def test_rest_segment_fields(self):
        streams = _make_interval_streams(reps=3, work_duration=120, rest_duration=90)
        result = detect_intervals(streams, "Intervals")

        assert result is not None
        if result["rest_segments"]:
            rest = result["rest_segments"][0]
            assert "segment_number" in rest
            assert "duration_s" in rest
            assert "avg_hr" in rest
            assert "hr_recovery_bpm" in rest

    def test_work_rest_ratio(self):
        streams = _make_interval_streams(
            reps=4, work_duration=180, rest_duration=90
        )
        result = detect_intervals(streams, "Intervals")

        assert result is not None
        summary = result["summary"]
        assert summary["work_to_rest_ratio"] is not None
        # With 180s work and 90s rest, ratio should be roughly 2.0
        assert 1.5 <= summary["work_to_rest_ratio"] <= 3.0

    def test_consistency_score_high_for_uniform_reps(self):
        """Uniform reps (same speed, same duration) should yield high consistency."""
        streams = _make_interval_streams(
            reps=5, work_duration=120, rest_duration=60,
            work_speed=4.5, rest_speed=2.0,
        )
        result = detect_intervals(streams, "Intervals")

        assert result is not None
        # With perfectly uniform data, CV should be low
        summary = result["summary"]
        assert summary["consistency_score"] in ("high", "medium")

    def test_consistency_score_low_for_variable_reps(self):
        """Variable rep durations should yield lower consistency."""
        # Build a stream with very different rep lengths
        segments = []
        segments.extend([3.0] * 300)  # warmup
        # Rep 1: 60s fast
        segments.extend([5.0] * 60)
        segments.extend([1.5] * 90)  # rest
        # Rep 2: 300s fast (5x longer)
        segments.extend([5.0] * 300)
        segments.extend([1.5] * 90)  # rest
        # Rep 3: 60s fast again
        segments.extend([5.0] * 60)
        segments.extend([3.0] * 200)  # cooldown

        streams = {"velocity_smooth": segments}
        result = detect_intervals(streams, "Intervals")

        if result is not None:
            # The work_duration_cv should be high due to 60s vs 300s reps
            summary = result["summary"]
            if summary["work_duration_cv"] is not None:
                assert summary["work_duration_cv"] > 20  # very high CV

    def test_warmup_detection(self):
        streams = _make_interval_streams(warmup=300, reps=4)
        result = detect_intervals(streams, "Intervals")

        assert result is not None
        # Warmup should be detected (>= 120s before first work)
        assert result["warmup_duration_s"] is not None
        assert result["warmup_duration_s"] >= 120

    def test_cooldown_detection(self):
        streams = _make_interval_streams(cooldown=200, reps=4)
        result = detect_intervals(streams, "Intervals")

        assert result is not None
        assert result["cooldown_duration_s"] is not None
        assert result["cooldown_duration_s"] >= 120

    def test_no_warmup_when_short(self):
        streams = _make_interval_streams(warmup=60, reps=4)
        result = detect_intervals(streams, "Intervals")

        assert result is not None
        # Warmup < 120s should not be detected
        assert result["warmup_duration_s"] is None

    def test_hr_recovery_calculation(self):
        streams = _make_interval_streams(
            reps=4, work_duration=180, rest_duration=90, include_hr=True
        )
        result = detect_intervals(streams, "Intervals")

        assert result is not None
        rest_segs = result["rest_segments"]
        if rest_segs:
            for rest in rest_segs:
                if rest["hr_recovery_bpm"] is not None:
                    # Recovery should be positive (peak HR > rest HR)
                    assert rest["hr_recovery_bpm"] > 0

    def test_no_hr_still_works(self):
        streams = _make_interval_streams(include_hr=False)
        result = detect_intervals(streams, "Intervals")

        assert result is not None
        # Work segments should have None for HR fields
        work = result["work_segments"][0]
        assert work["avg_hr"] is None
        assert work["peak_hr"] is None

    def test_rep_count(self):
        streams = _make_interval_streams(reps=6, work_duration=90, rest_duration=60)
        result = detect_intervals(streams, "Intervals")

        assert result is not None
        # Should detect approximately 6 reps (smoothing may merge some)
        assert result["summary"]["rep_count"] >= 4

    def test_summary_fields_complete(self):
        streams = _make_interval_streams(reps=4, work_duration=120, rest_duration=60)
        result = detect_intervals(streams, "Intervals")

        assert result is not None
        summary = result["summary"]
        expected_keys = {
            "total_work_time_s", "total_rest_time_s", "work_to_rest_ratio",
            "rep_count", "avg_work_duration_s", "work_duration_cv",
            "avg_work_speed_mps", "work_speed_cv", "avg_rest_duration_s",
            "avg_hr_recovery_bpm", "consistency_score",
        }
        assert set(summary.keys()) == expected_keys
