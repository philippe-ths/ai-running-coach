"""Time-based split duration correctness (#174).

When an activity has a `time` stream but no `distance` stream, splits fall
back to fixed 5-minute (300 s) windows. The boundary handling used to exclude
the crossing sample, so each full split spanned ~299 s instead of 300 s — which
then floored to "4 min" in the UI under a "Splits (5 min)" title. A full split
must span the whole interval and consecutive splits must be contiguous (their
durations sum to the total activity duration).
"""

from types import SimpleNamespace

from app.services.analysis.splits import calculate_splits


def _stream(stream_type, data):
    return SimpleNamespace(stream_type=stream_type, data=data)


def _time_only_streams(total_s):
    # 1 Hz time stream, no distance stream -> time-based split fallback.
    return [_stream("time", list(range(total_s + 1)))]


def test_full_time_splits_span_the_whole_interval():
    # 11 minutes -> two full 5-min splits + a 1-min partial.
    splits = calculate_splits(_time_only_streams(660))

    assert [s["split_type"] for s in splits] == ["time", "time", "time"]
    full = [s for s in splits[:2]]
    assert all(s["elapsed_time"] == 300 for s in full), [s["elapsed_time"] for s in full]


def test_partial_last_split_keeps_its_true_duration():
    splits = calculate_splits(_time_only_streams(660))

    # 660 s total - 600 s of full splits = 60 s remainder.
    assert splits[-1]["elapsed_time"] == 60


def test_time_splits_are_contiguous_no_gap_between_them():
    # The durations of all splits must sum to the full activity duration; the
    # off-by-one previously dropped one sample-interval per full split.
    splits = calculate_splits(_time_only_streams(660))

    assert sum(s["elapsed_time"] for s in splits) == 660
