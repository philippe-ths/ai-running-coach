"""Tests for the consolidated stream view (A2a processed-artifacts layer).

`build_stream_view` is the pure-function oracle: a hand-authored stream input
maps to a downsampled, index-aligned, point-count-bounded, shape-preserving
snapshot of HR / pace / grade / cadence. No DB, no I/O.

The contract (see docs/adr/0008-coach-memory-is-a-four-layer-pull-model.md):
- aligned: every returned channel array has the same length (== n_points),
  one entry per bucket, so the same index means the same moment across channels;
- bounded: n_points <= STREAM_VIEW_MAX_POINTS regardless of input size;
- shape-preserving: a sustained peak / climb survives the downsample;
- degrades to None when there is nothing worth viewing.
"""

from app.services.analysis.stream_view import (
    build_stream_view,
    STREAM_VIEW_MAX_POINTS,
)


def _const(value, n):
    return [value] * n


# --- bounding + alignment ---------------------------------------------------

def test_bounded_point_count_for_long_stream():
    n = 5000
    streams = {
        "time": list(range(n)),
        "heartrate": _const(150, n),
        "velocity_smooth": _const(3.0, n),
        "grade_smooth": _const(0.0, n),
        "cadence": _const(170, n),
    }
    view = build_stream_view(streams)
    assert view is not None
    assert view["n_points"] <= STREAM_VIEW_MAX_POINTS
    assert view["n_points"] == STREAM_VIEW_MAX_POINTS  # 5000 >> max -> exactly the cap
    assert view["source_n"] == n


def test_all_channel_arrays_are_aligned_to_n_points():
    n = 600
    streams = {
        "time": list(range(n)),
        "heartrate": _const(150, n),
        "velocity_smooth": _const(3.0, n),
        "grade_smooth": _const(1.0, n),
        "cadence": _const(170, n),
    }
    view = build_stream_view(streams)
    assert view is not None
    k = view["n_points"]
    assert len(view["time_s"]) == k
    assert len(view["hr"]) == k
    assert len(view["pace_s_per_km"]) == k
    assert len(view["grade_pct"]) == k
    assert len(view["cadence_spm"]) == k


def test_time_axis_is_strictly_increasing():
    n = 1000
    streams = {"time": list(range(n)), "heartrate": list(range(100, 100 + n))}
    view = build_stream_view(streams)
    assert view is not None
    ts = view["time_s"]
    assert all(ts[i] < ts[i + 1] for i in range(len(ts) - 1))


def test_subsecond_time_stays_monotonic_non_decreasing():
    # Sub-second sampling at a low point count can round adjacent bucket times to
    # equal integer seconds. The guaranteed contract is non-decreasing (never
    # reversed); strict increase holds only for the usual integer-second stream.
    n = 10
    streams = {"time": [i * 0.1 for i in range(n)], "heartrate": [150] * n}
    view = build_stream_view(streams)
    assert view is not None
    ts = view["time_s"]
    assert all(ts[i] <= ts[i + 1] for i in range(len(ts) - 1))


def test_ragged_channel_lengths_are_trimmed_to_common_min():
    # Strava channels are stored independently and are NOT guaranteed equal
    # length. The producer must trim to the common min before downsampling.
    streams = {
        "time": list(range(100)),
        "heartrate": _const(150, 100),
        "velocity_smooth": _const(3.0, 98),  # short
        "grade_smooth": _const(0.0, 100),
        "cadence": _const(170, 100),
    }
    view = build_stream_view(streams)
    assert view is not None
    k = view["n_points"]
    assert view["source_n"] == 98  # trimmed to the shortest present channel
    for key in ("time_s", "hr", "pace_s_per_km", "grade_pct", "cadence_spm"):
        assert len(view[key]) == k


# --- short stream passes through (no over-downsampling) ----------------------

def test_short_stream_keeps_every_sample():
    n = 40
    streams = {
        "time": list(range(n)),
        "heartrate": list(range(120, 120 + n)),
        "velocity_smooth": _const(3.0, n),
    }
    view = build_stream_view(streams)
    assert view is not None
    assert view["n_points"] == n  # <= max, so one bucket per sample
    assert view["source_n"] == n
    # one-sample buckets => values are the (rounded) raw samples
    assert view["hr"] == list(range(120, 120 + n))


# --- shape preservation ------------------------------------------------------

def test_sustained_hr_peak_survives_downsample():
    n = 300  # 60 buckets of exactly 5 samples each
    hr = _const(130, n)
    for i in range(100, 160):  # a sustained 180 bpm plateau spanning whole buckets
        hr[i] = 180
    streams = {"time": list(range(n)), "heartrate": hr}
    view = build_stream_view(streams)
    assert view is not None
    assert max(v for v in view["hr"] if v is not None) == 180  # peak survives
    assert min(v for v in view["hr"] if v is not None) == 130  # baseline survives


def test_grade_climb_survives_downsample():
    n = 300
    grade = _const(0.0, n)
    for i in range(200, 260):
        grade[i] = 8.0
    streams = {"time": list(range(n)), "grade_smooth": grade}
    view = build_stream_view(streams)
    assert view is not None
    assert max(v for v in view["grade_pct"] if v is not None) == 8.0


# --- pace derivation ---------------------------------------------------------

def test_pace_is_derived_from_velocity_in_seconds_per_km():
    n = 120
    streams = {"time": list(range(n)), "velocity_smooth": _const(4.0, n)}  # 4 m/s
    view = build_stream_view(streams)
    assert view is not None
    expected = round(1000.0 / 4.0)  # 250 s/km
    assert all(p == expected for p in view["pace_s_per_km"])


def test_stopped_segment_yields_none_pace_not_infinity():
    n = 300
    vel = _const(3.0, n)
    for i in range(50, 55):  # a full bucket of zero velocity (a stop)
        vel[i] = 0.0
    streams = {"time": list(range(n)), "velocity_smooth": vel}
    view = build_stream_view(streams)
    assert view is not None
    paces = view["pace_s_per_km"]
    assert None in paces  # the stopped bucket is None, never a division blow-up
    assert any(p is not None for p in paces)


# --- cadence normalisation ---------------------------------------------------

def test_cadence_in_strides_is_doubled_to_spm():
    n = 120
    streams = {"time": list(range(n)), "cadence": _const(85, n)}  # strides/min
    view = build_stream_view(streams)
    assert view is not None
    # series mean < 130 => doubled to steps-per-minute
    assert all(c == 170 for c in view["cadence_spm"])


def test_cadence_already_in_spm_is_left_alone():
    n = 120
    streams = {"time": list(range(n)), "cadence": _const(170, n)}
    view = build_stream_view(streams)
    assert view is not None
    assert all(c == 170 for c in view["cadence_spm"])


def test_cadence_normalisation_shares_the_units_threshold():
    """#442: the stream-view builder applies the SAME per-leg rule as the read-path
    units helper, so the threshold cannot drift between the two. Just below the
    shared threshold doubles; exactly at it does not."""
    from app.services.units.cadence import CADENCE_SINGLE_LEG_THRESHOLD_SPM

    n = 120
    below = CADENCE_SINGLE_LEG_THRESHOLD_SPM - 1
    at = CADENCE_SINGLE_LEG_THRESHOLD_SPM

    view_below = build_stream_view({"time": list(range(n)), "cadence": _const(below, n)})
    view_at = build_stream_view({"time": list(range(n)), "cadence": _const(at, n)})

    assert view_below is not None and view_at is not None
    assert all(c == below * 2 for c in view_below["cadence_spm"])  # single-leg -> doubled
    assert all(c == at for c in view_at["cadence_spm"])  # at threshold -> left alone


# --- None handling within a bucket ------------------------------------------

def test_all_none_bucket_becomes_none():
    n = 300
    hr = _const(150, n)
    for i in range(0, 5):  # first whole bucket is missing HR
        hr[i] = None
    streams = {"time": list(range(n)), "heartrate": hr}
    view = build_stream_view(streams)
    assert view is not None
    assert view["hr"][0] is None
    assert view["hr"][1] == 150


# --- absent channels are present-with-None (predictable schema) -------------

def test_absent_channels_are_none_not_missing():
    n = 100
    streams = {"time": list(range(n)), "heartrate": _const(150, n)}
    view = build_stream_view(streams)
    assert view is not None
    assert view["hr"] is not None
    assert view["pace_s_per_km"] is None
    assert view["grade_pct"] is None
    assert view["cadence_spm"] is None


# --- degradation -------------------------------------------------------------

def test_empty_streams_returns_none():
    assert build_stream_view({}) is None


def test_streams_without_time_returns_none():
    assert build_stream_view({"heartrate": _const(150, 100)}) is None


def test_streams_with_only_time_returns_none():
    # time but no metric channel: nothing worth viewing.
    assert build_stream_view({"time": list(range(100))}) is None


def test_none_input_returns_none():
    assert build_stream_view(None) is None
