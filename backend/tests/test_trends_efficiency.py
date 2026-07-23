"""Unit coverage for build_efficiency_trend's per-point shape (#745, #746).

Pure-function tests over hand-constructed ActivityFact objects: the metric itself
is unchanged, and each point now carries a stable activity_id plus condition
confounder flags (hills, stops) derived from already-projected fields.
"""

from datetime import date

from app.services.trends import (
    build_efficiency_trend,
    ActivityFact,
    _EFFICIENCY_HILLY_GAIN_PER_KM,
    _EFFICIENCY_STOPPY_FRACTION,
)


def _fact(**kw) -> ActivityFact:
    """Build an ActivityFact without the ORM, mirroring from_row's slot writes."""
    f = ActivityFact.__new__(ActivityFact)
    f.activity_id = kw.get("activity_id", 1)
    f.local_date = kw.get("local_date", date(2026, 7, 1))
    f.activity_type = kw.get("activity_type", "Run")
    f.user_intent = None
    f.distance_m = kw.get("distance_m", 10000)
    f.moving_time_s = kw.get("moving_time_s", 3000)
    f.elapsed_time_s = kw.get("elapsed_time_s", kw.get("moving_time_s", 3000))
    f.elev_gain_m = kw.get("elev_gain_m", 0.0)
    f.avg_hr = kw.get("avg_hr", 150)
    f.avg_cadence = None
    f.average_speed_mps = kw.get("average_speed_mps", 3.0)
    f.effort_score = None
    f.effort = None
    f.time_in_zones = None
    f.structure = None
    f.interval_structure = None
    f.duration_class = None
    f.hr_drift = None
    return f


def test_metric_unchanged_and_carries_activity_id():
    # 3.0 m/s / 150 bpm = 0.02
    pts = build_efficiency_trend([_fact(activity_id=42, average_speed_mps=3.0, avg_hr=150)])
    assert len(pts) == 1
    p = pts[0]
    assert p["efficiency_mps_per_bpm"] == 0.02
    # activity_id is stringified (real Activity.id is a UUID).
    assert p["activity_id"] == "42"
    assert p["type"] == "Run"


def test_flat_continuous_run_has_no_confounder_flags():
    p = build_efficiency_trend([_fact(distance_m=10000, elev_gain_m=0.0, moving_time_s=3000, elapsed_time_s=3000)])[0]
    assert p["hilly"] is False
    assert p["stoppy"] is False
    assert p["gain_per_km"] == 0.0
    assert p["stopped_frac"] == 0.0


def test_hilly_flag_at_and_below_threshold():
    # 10 km with 150 m gain = 15.0 m/km == threshold -> hilly
    hilly = build_efficiency_trend([_fact(distance_m=10000, elev_gain_m=150.0)])[0]
    assert hilly["gain_per_km"] == 15.0
    assert _EFFICIENCY_HILLY_GAIN_PER_KM == 15.0
    assert hilly["hilly"] is True
    # 10 km with 100 m gain = 10.0 m/km -> not hilly
    flat = build_efficiency_trend([_fact(distance_m=10000, elev_gain_m=100.0)])[0]
    assert flat["gain_per_km"] == 10.0
    assert flat["hilly"] is False


def test_stoppy_flag_from_elapsed_vs_moving():
    # moving 3000 / elapsed 3600 -> 600/3600 = 0.1667 stopped -> stoppy
    stoppy = build_efficiency_trend([_fact(moving_time_s=3000, elapsed_time_s=3600)])[0]
    assert stoppy["stopped_frac"] == 0.167
    assert stoppy["stoppy"] is True
    assert _EFFICIENCY_STOPPY_FRACTION == 0.10
    # moving 3000 / elapsed 3200 -> 200/3200 = 0.0625 -> not stoppy
    cont = build_efficiency_trend([_fact(moving_time_s=3000, elapsed_time_s=3200)])[0]
    assert cont["stopped_frac"] == 0.062
    assert cont["stoppy"] is False


def test_same_day_activities_are_both_emitted_with_distinct_ids():
    day = date(2026, 7, 1)
    facts = [
        _fact(activity_id=1, local_date=day, activity_type="Run", average_speed_mps=3.0),
        _fact(activity_id=2, local_date=day, activity_type="Walk", average_speed_mps=1.5),
    ]
    pts = build_efficiency_trend(facts)
    assert len(pts) == 2
    ids = {p["activity_id"] for p in pts}
    assert ids == {"1", "2"}
    types = {p["type"] for p in pts}
    assert types == {"Run", "Walk"}


def test_speed_falls_back_to_distance_over_moving_time():
    # No average_speed_mps -> distance/moving_time = 3000/1500 = 2.0 m/s; /100 = 0.02
    p = build_efficiency_trend([_fact(average_speed_mps=None, distance_m=3000, moving_time_s=1500, avg_hr=100)])[0]
    assert p["efficiency_mps_per_bpm"] == 0.02
