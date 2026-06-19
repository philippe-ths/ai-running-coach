"""Distance-based split boundary and metric correctness (#364).

`calculate_splits` builds one split per `split_distance_m` (default 1000 m)
when a cumulative `distance` stream is present. The boundary detection was
vectorized with `np.searchsorted` (#364); these tests pin the boundaries, the
partial-last-split rule, and the per-split metrics so the vectorized form
cannot drift from the documented behavior.

Ground truth: a synthetic constant-pace run (2.5 m/s, 1 Hz). Expected values
are hand-derived from the split formula in `_compute_split_metrics`, which uses
`distance[end_idx - 1]` as a split's end distance and `time[end_idx]`-style
indexing — so a full 1 km split spans samples [start, boundary) and reports the
distance covered up to the sample just before the boundary (997.5 m here), not
a rounded 1000 m. That quirk is existing behavior and is pinned deliberately.
"""

from types import SimpleNamespace

from app.services.analysis.splits import calculate_splits


def _stream(stream_type, data):
    return SimpleNamespace(stream_type=stream_type, data=data)


def _constant_pace_streams(total_s, mps=2.5, hr=150):
    """1 Hz run at a constant pace, with a flat HR stream for metric pinning."""
    time = list(range(total_s + 1))
    distance = [mps * i for i in time]
    heartrate = [hr] * len(time)
    return [
        _stream("time", time),
        _stream("distance", distance),
        _stream("heartrate", heartrate),
    ]


def test_distance_splits_boundaries_and_metrics():
    # 1000 s @ 2.5 m/s = 2500 m -> two full 1 km splits + a 500 m partial.
    splits = calculate_splits(_constant_pace_streams(1000))

    assert [s["split"] for s in splits] == [1, 2, 3]
    assert all(s["split_type"] == "distance" for s in splits)

    # Two full splits: boundary at the first sample reaching the km mark
    # (index 400 and 800), so each spans 399 samples of elapsed time and the
    # distance up to the sample before the boundary (997.5 m).
    for full in splits[:2]:
        assert full["elapsed_time"] == 399
        assert abs(full["distance"] - 997.5) < 1e-6
        assert abs(full["speed"] - 2.5) < 1e-6
        assert abs(full["pace"] - 400.0) < 1e-6  # 400 s/km = 6:40/km
        assert abs(full["avg_hr"] - 150.0) < 1e-6

    # Partial last split: samples [800, 1001) -> 2000..2500 m over 200 s.
    partial = splits[2]
    assert partial["elapsed_time"] == 200
    assert abs(partial["distance"] - 500.0) < 1e-6
    assert abs(partial["speed"] - 2.5) < 1e-6


def test_no_partial_split_when_remainder_under_100m():
    # 820 s @ 2.5 m/s = 2050 m: 50 m past the 2 km mark, under the 100 m floor.
    splits = calculate_splits(_constant_pace_streams(820))

    assert [s["split"] for s in splits] == [1, 2]
    assert all(s["split_type"] == "distance" for s in splits)


def test_exact_km_finish_emits_no_partial():
    # 800 s @ 2.5 m/s = exactly 2000 m: the final km boundary lands on the last
    # sample, so there is no remainder and no partial split.
    splits = calculate_splits(_constant_pace_streams(800))

    assert [s["split"] for s in splits] == [1, 2]
    assert all(s["split_type"] == "distance" for s in splits)
