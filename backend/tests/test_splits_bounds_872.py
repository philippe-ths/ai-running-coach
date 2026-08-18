"""#872: `_compute_split_metrics` carried a `return {}` guard commented "Should
not happen".

#853 (PR #862) made `read_activity` validate each split dict against
`SplitRead` before assignment, and `SplitRead` requires `split` and
`elapsed_time` — so that branch stopped serving a malformed split to the
activity page and started raising a `ValidationError` instead. The branch was
traced as unreachable but never proved, and the `d_start` line above it indexed
the stream before the guard's own bounds check, so a genuinely out-of-range
index raised there first and the guard could only ever be reached with an empty
distance stream.

These tests supply the missing proof and pin the behaviour the fix defines:

1. the caller never hands out an out-of-range start index, across every stream
   shape that reaches it (the invariant the `assert` now states);
2. an empty distance stream is answered with no splits rather than an
   `IndexError` from `distance[-1]` — the one shape that could have reached the
   old guard;
3. an out-of-range start index is loud rather than silently malformed, which is
   the behaviour change #862 introduced and this pass makes deliberate.
"""

import numpy as np
import pytest
from types import SimpleNamespace

from app.schemas.detail import SplitRead
from app.services.analysis import splits as splits_module
from app.services.analysis.splits import _compute_split_metrics, calculate_splits


def _stream(stream_type, data):
    return SimpleNamespace(stream_type=stream_type, data=data)


def _streams(distance, time=None, hr=True):
    time = list(range(len(distance))) if time is None else time
    out = [_stream("time", time), _stream("distance", distance)]
    if hr:
        out.append(_stream("heartrate", [150] * len(distance)))
    return out


# Every distance-stream shape that reaches `_compute_split_metrics`, chosen to
# hit each branch of the caller's boundary arithmetic. Named so a failure says
# which shape broke the invariant.
SHAPES = {
    "single_sample": [0.0],
    "two_samples": [0.0, 2.5],
    "shorter_than_one_km": [2.5 * i for i in range(200)],          # 497.5 m
    "exactly_one_km": [5.0 * i for i in range(201)],               # 1000.0 m
    "one_km_plus_50m_remainder": [5.0 * i for i in range(211)],    # partial dropped (<100 m)
    "one_km_plus_500m_remainder": [5.0 * i for i in range(301)],   # partial kept
    "many_full_splits": [2.5 * i for i in range(4001)],            # 10 km
    "stationary_zero_distance": [0.0] * 100,
    "does_not_start_at_zero": [37.0 + 2.5 * i for i in range(600)],
    "stalls_then_moves": [0.0] * 300 + [2.5 * i for i in range(700)],
}


@pytest.fixture
def observed_start_indices(monkeypatch):
    """Record every (start_idx, stream length) pair the caller actually passes."""
    seen: list[tuple[int, int]] = []
    real = splits_module._compute_split_metrics

    def spy(number, start_idx, end_idx, distance_stream, *args, **kwargs):
        seen.append((start_idx, len(distance_stream)))
        return real(number, start_idx, end_idx, distance_stream, *args, **kwargs)

    monkeypatch.setattr(splits_module, "_compute_split_metrics", spy)
    return seen


@pytest.mark.parametrize("shape", sorted(SHAPES))
def test_every_start_index_the_caller_passes_is_within_the_distance_stream(
    shape, observed_start_indices
):
    """The invariant the `assert` in `_compute_split_metrics` now states."""
    calculate_splits(_streams(SHAPES[shape]))

    for start_idx, stream_len in observed_start_indices:
        assert 0 <= start_idx < stream_len, (
            f"{shape}: start_idx={start_idx} out of range for a "
            f"{stream_len}-sample distance stream"
        )


def test_the_shape_matrix_actually_exercises_the_call(observed_start_indices):
    """Guard on the guard: a matrix that never reaches the callee proves nothing."""
    for distance in SHAPES.values():
        calculate_splits(_streams(distance))

    assert observed_start_indices, "no shape reached _compute_split_metrics"
    # And the matrix must reach it more than trivially often, or it is only
    # pinning the first-split case.
    assert len(observed_start_indices) > 10


@pytest.mark.parametrize("shape", sorted(SHAPES))
def test_no_shape_produces_a_split_that_fails_the_served_schema(shape):
    """The old `{}` return would fail this; so would any partially-built split."""
    for split in calculate_splits(_streams(SHAPES[shape])):
        assert split != {}
        SplitRead.model_validate(split)


def test_an_empty_distance_stream_is_answered_with_no_splits():
    """The one shape that could have reached the old guard.

    Before the fix this raised `IndexError` from `total_dist = distance[-1]`,
    which is why the guard below it read as unreachable "by accident".
    """
    assert calculate_splits(_streams([], time=[])) == []


def test_an_out_of_range_start_index_raises_rather_than_returning_a_malformed_split():
    """The replaced branch's behaviour, made deliberate.

    `{}` used to be returned here; since #862 it would have reached
    `SplitRead.model_validate` and raised there, one layer away from the cause.
    """
    distance = [0.0, 2.5, 5.0]
    with pytest.raises(AssertionError):
        _compute_split_metrics(1, 3, 3, distance, [0, 1, 2], None, None, None)


def test_the_searchsorted_boundary_can_never_reach_the_end_of_the_stream():
    """The arithmetic the `assert`'s reasoning rests on, checked directly.

    Boundaries come from targets no larger than `distance[-1]`, so
    `side="left"` returns at most `n_points - 1` and never `n_points`.
    """
    for distance in SHAPES.values():
        if not distance:
            continue
        arr = np.asarray(distance, dtype=float)
        n_full = int(arr[-1] // 1000)
        if n_full <= 0:
            continue
        targets = np.arange(1, n_full + 1) * 1000
        boundaries = np.searchsorted(arr, targets, side="left")
        assert boundaries.max() <= len(distance) - 1
