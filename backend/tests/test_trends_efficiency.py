"""Unit coverage for build_efficiency_trend and the window means (#745, #746).

Pure-function tests over hand-constructed ActivityFact objects: the metric itself
is unchanged, and each point carries a stable activity_id plus condition confounder
flags (hills, stops, heat) derived from already-projected fields. The window stats
then use those same flags to offer a clean-conditions-only mean alongside the
all-activity one, so the headline comparison can be like-for-like.
"""

from datetime import date

import pytest

from app.services.activity_facts import ActivityFact, coerce_temp
from app.services.analysis.classifier import _HILLY_GAIN_PER_KM
from app.services.analysis.discount_signals import HEAT_TEMP_C
from app.services.trends import (
    build_efficiency_trend,
    efficiency_window_stats,
    _EFFICIENCY_HILLY_GAIN_PER_KM,
    _EFFICIENCY_HOT_TEMP_C,
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
    f.average_temp = kw.get("average_temp", None)
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


# --- heat (#746) -------------------------------------------------------------


def test_hot_flag_at_and_below_threshold():
    hot = build_efficiency_trend([_fact(average_temp=25.0)])[0]
    assert hot["average_temp"] == 25.0
    assert hot["hot"] is True
    # A tenth below the gate is not hot.
    cool = build_efficiency_trend([_fact(average_temp=24.9)])[0]
    assert cool["average_temp"] == 24.9
    assert cool["hot"] is False


def test_unrecorded_temperature_is_not_hot():
    """Absent is absent: no temperature must never be read as heat, and must not
    be read as cool either — the point simply carries None."""
    p = build_efficiency_trend([_fact(average_temp=None)])[0]
    assert p["average_temp"] is None
    assert p["hot"] is False


def test_thresholds_are_the_analysis_layer_s_own_not_copies():
    """#746 AC: reuse existing logic rather than duplicate it. These are imports,
    so a change to the analysis layer's gate moves the chart's flag with it."""
    assert _EFFICIENCY_HILLY_GAIN_PER_KM is _HILLY_GAIN_PER_KM
    assert _EFFICIENCY_HOT_TEMP_C is HEAT_TEMP_C


@pytest.mark.parametrize(
    "raw, expected",
    [
        (29, 29.0),          # Strava's usual integer degrees
        (22.5, 22.5),
        ("29", 29.0),        # Postgres ->> hands back text
        (None, None),
        ("hot", None),       # a stray non-numeric must degrade, never raise
        ("", None),
        (True, None),        # float(True) would be a plausible-looking 1 degrees C
        ({"c": 20}, None),
    ],
)
def test_temperature_coercion_never_raises(raw, expected):
    assert coerce_temp(raw) == expected


def test_non_numeric_temperature_yields_an_unflagged_point():
    """The coercion is reachable through the builder: a junk temperature produces a
    point with no heat claim rather than a 500."""
    f = _fact()
    f.average_temp = coerce_temp("scorching")
    p = build_efficiency_trend([f])[0]
    assert p["average_temp"] is None
    assert p["hot"] is False


# --- window means (#746): the headline comparison is like-for-like -----------


def _clean(**kw) -> ActivityFact:
    """A flat, continuous, cool activity — nothing to exculpate."""
    kw.setdefault("elev_gain_m", 0.0)
    kw.setdefault("moving_time_s", 3000)
    kw.setdefault("elapsed_time_s", 3000)
    kw.setdefault("average_temp", 12.0)
    return _fact(**kw)


def test_clean_mean_equals_all_mean_when_every_activity_is_clean():
    facts = [_clean(activity_id=1, average_speed_mps=3.0),
             _clean(activity_id=2, average_speed_mps=4.5)]
    stats = efficiency_window_stats(facts)
    # (0.02 + 0.03) / 2 = 0.025
    assert stats.avg == 0.025
    assert stats.avg_clean == 0.025
    assert stats.clean_count == 2
    assert stats.total_count == 2


@pytest.mark.parametrize(
    "confounder",
    [
        {"elev_gain_m": 200.0},                              # hilly: 20 m/km
        {"moving_time_s": 3000, "elapsed_time_s": 3600},     # stoppy: 16.7%
        {"average_temp": 30.0},                              # hot
    ],
    ids=["hilly", "stoppy", "hot"],
)
def test_each_confounder_independently_excludes_an_activity_from_the_clean_mean(confounder):
    """One confounded activity at a very different efficiency must move the
    all-activity mean and leave the clean mean untouched."""
    facts = [
        _clean(activity_id=1, average_speed_mps=3.0),   # 0.02
        _clean(activity_id=2, average_speed_mps=3.0),   # 0.02
        _clean(activity_id=3, average_speed_mps=1.5, **confounder),  # 0.01, confounded
    ]
    stats = efficiency_window_stats(facts)
    assert stats.total_count == 3
    assert stats.clean_count == 2
    # All three: (0.02 + 0.02 + 0.01) / 3 = 0.0167 — dragged down by conditions.
    assert stats.avg == 0.0167
    # Clean only: the confounded activity is out.
    assert stats.avg_clean == 0.02


def test_clean_mean_is_none_when_no_activity_is_clean():
    """Every activity confounded → there is no like-for-like basis to offer, and
    the caller falls back to the all-activity pair rather than a fabricated one."""
    stats = efficiency_window_stats([
        _clean(activity_id=1, elev_gain_m=300.0),
        _clean(activity_id=2, average_temp=31.0),
    ])
    assert stats.avg == 0.02
    assert stats.avg_clean is None
    assert stats.clean_count == 0
    assert stats.total_count == 2


def test_empty_window_yields_no_means_and_zero_counts():
    stats = efficiency_window_stats([])
    assert stats.avg is None
    assert stats.avg_clean is None
    assert stats.clean_count == 0
    assert stats.total_count == 0


def test_counts_only_include_activities_the_chart_actually_plots():
    """A sub-1km activity and an HR-less one are filtered out of the chart, so they
    must not inflate either count — the means and the counts describe one set."""
    stats = efficiency_window_stats([
        _clean(activity_id=1, average_speed_mps=3.0),
        _clean(activity_id=2, distance_m=500),      # under the 1 km floor
        _clean(activity_id=3, avg_hr=None),         # no usable HR
    ])
    assert stats.total_count == 1
    assert stats.clean_count == 1
