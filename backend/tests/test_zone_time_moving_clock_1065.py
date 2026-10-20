"""#1065: zone time is measured on the moving clock, not by counting samples.

A watch on smart recording logs a walk or ride about every 4 s, so counting
heart-rate samples as seconds credited a walk with about a quarter of its zone
time, and its load leaned on the sub-zone fallback for the rest. The same
session recorded at 1 s and at 4 s must now read the same.

All stream data is synthetic test setup (exercises code paths; represents no
real runner).
"""

from types import SimpleNamespace

from app.services.analysis.metrics import (
    calculate_time_in_zones,
    compute_derived_metrics_data,
)

MAX_HR = 200  # Z2 is 120-140 on the %-of-max fallback


def _walk(seconds: int, every: int, hr: int = 130) -> dict:
    times = list(range(0, seconds, every))
    return {"time": times, "heartrate": [hr] * len(times)}


def test_a_smart_recorded_walk_gets_its_whole_moving_time_in_zone():
    """The issue's example: 2,194 s long, 508 heart-rate samples."""
    streams = {"time": list(range(508)), "heartrate": [130] * 508}

    zones = calculate_time_in_zones(streams, MAX_HR, moving_time_s=2194)

    assert zones["Z2"] == 2194
    assert sum(zones.values()) == 2194


def test_a_one_second_recording_is_unchanged():
    streams = _walk(3000, 1)

    assert calculate_time_in_zones(streams, MAX_HR, moving_time_s=3000) == (
        calculate_time_in_zones(streams, MAX_HR)
    )


def test_dropouts_stay_outside_the_zones():
    """A reading of 0 is not time in a zone; its share stays out, as before,
    for the load's sub-zone fallback to count."""
    streams = {"time": list(range(100)), "heartrate": [130] * 50 + [0] * 50}

    zones = calculate_time_in_zones(streams, MAX_HR, moving_time_s=400)

    assert sum(zones.values()) == 200


def test_the_runners_own_zone_boundaries_take_the_same_clock():
    bounds = [0, 110, 140, 160, 175]
    streams = {"time": list(range(100)), "heartrate": [120] * 100}

    zones = calculate_time_in_zones(
        streams, MAX_HR, zone_boundaries=bounds, moving_time_s=400
    )

    assert zones["Z2"] == 400


def test_the_same_walk_at_one_and_four_second_sampling_carries_the_same_load():
    activity = SimpleNamespace(moving_time_s=3600, avg_hr=150)
    hard_walk = 150  # Z3, so the sub-zone fallback's weight 1 would undercount

    every_second = compute_derived_metrics_data(activity, _walk(3600, 1, hard_walk), MAX_HR)
    every_four = compute_derived_metrics_data(activity, _walk(3600, 4, hard_walk), MAX_HR)

    assert every_four["time_in_zones"] == every_second["time_in_zones"]
    assert every_four["effort_score"] == every_second["effort_score"]
