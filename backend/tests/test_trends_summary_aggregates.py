"""Trends summary carries period aggregates for the graph-card deltas (#385).

`avg_efficiency_mps_per_bpm` and `total_zone_minutes` must be computed over the
current window for ``summary`` and the prior window for ``previous_summary`` so
the Efficiency and Zone-Load graph cards can show a period-over-period delta.
"""

import uuid
from datetime import date, datetime, time, timedelta

from app.models import Activity, DerivedMetric, User
from app.services.trends import get_trends_report


def _user(db) -> uuid.UUID:
    user = User(email=f"{uuid.uuid4()}@example.com")
    db.add(user)
    db.flush()
    return user.id


def _activity(
    db, user_id, on, *, distance_m, moving_time_s, avg_hr, time_in_zones,
    elapsed_time_s=None, elev_gain_m=0.0, raw_summary=None,
):
    activity = Activity(
        user_id=user_id,
        strava_activity_id=int(uuid.uuid4().int % 1_000_000_000),
        start_date=datetime.combine(on, time(12, 0)),
        type="Run",
        name="Run",
        distance_m=distance_m,
        moving_time_s=moving_time_s,
        elapsed_time_s=moving_time_s if elapsed_time_s is None else elapsed_time_s,
        elev_gain_m=elev_gain_m,
        avg_hr=avg_hr,
        raw_summary={} if raw_summary is None else raw_summary,
    )
    db.add(activity)
    db.flush()
    db.add(
        DerivedMetric(
            id=uuid.uuid4(),
            activity_id=activity.id,
            effort_score=1.0,
            confidence="high",
            flags=[],
            confidence_reasons=[],
            time_in_zones=time_in_zones,
        )
    )
    db.flush()
    return activity


def test_summary_carries_efficiency_and_zone_minutes_per_window(db):
    user_id = _user(db)
    today = date.today()

    # Current 7D window: speed = 3000/1000 = 3.0 m/s, hr = 150
    #   efficiency = 3.0 / 150 = 0.02 mps/bpm
    #   zones: easy = (300+300) = 600 s = 10.0 min; moderate = 300 s = 5.0 min;
    #          hard = (60+40) = 100 s = 1.7 min
    _activity(
        db, user_id, today - timedelta(days=1),
        distance_m=3000, moving_time_s=1000, avg_hr=150,
        time_in_zones={"Z1": 300, "Z2": 300, "Z3": 300, "Z4": 60, "Z5": 40},
    )
    # Previous 7D window: speed = 2000/1000 = 2.0 m/s, hr = 200
    #   efficiency = 2.0 / 200 = 0.01 mps/bpm
    #   zones: easy = (200+100) = 300 s = 5.0 min; moderate = 100 s = 1.7 min;
    #          hard = (50+50) = 100 s = 1.7 min
    _activity(
        db, user_id, today - timedelta(days=8),
        distance_m=2000, moving_time_s=1000, avg_hr=200,
        time_in_zones={"Z1": 200, "Z2": 100, "Z3": 100, "Z4": 50, "Z5": 50},
    )

    report = get_trends_report(db, "7D", user_id=user_id)

    assert report.summary.avg_efficiency_mps_per_bpm == 0.02
    assert report.previous_summary.avg_efficiency_mps_per_bpm == 0.01

    assert report.summary.zone_easy_minutes == 10.0
    assert report.summary.zone_moderate_minutes == 5.0
    assert report.summary.zone_hard_minutes == 1.7

    assert report.previous_summary.zone_easy_minutes == 5.0
    assert report.previous_summary.zone_moderate_minutes == 1.7
    assert report.previous_summary.zone_hard_minutes == 1.7


def test_efficiency_is_none_when_no_activity_has_usable_hr(db):
    user_id = _user(db)
    today = date.today()
    # Distance qualifies but no HR → efficiency cannot be computed.
    _activity(
        db, user_id, today - timedelta(days=1),
        distance_m=3000, moving_time_s=1000, avg_hr=None,
        time_in_zones=None,
    )

    report = get_trends_report(db, "7D", user_id=user_id)

    assert report.summary.avg_efficiency_mps_per_bpm is None
    assert report.summary.zone_easy_minutes == 0.0
    assert report.summary.zone_moderate_minutes == 0.0
    assert report.summary.zone_hard_minutes == 0.0


# ---------------------------------------------------------------------------
# #746: the clean-conditions aggregate, exercised through the REAL query path.
#
# These are the only tests that prove the SQL projection of `average_temp` out of
# the deferred `raw_summary` JSON column actually works — the pure-function tests
# above it hand-build ActivityFacts and would stay green with the projection
# broken or absent entirely.
# ---------------------------------------------------------------------------


def _clean_run(db, user_id, on, **kw):
    """A flat, continuous, cool run: 3000 m in 1000 s at 150 bpm = 0.02 mps/bpm."""
    kw.setdefault("distance_m", 3000)
    kw.setdefault("moving_time_s", 1000)
    kw.setdefault("avg_hr", 150)
    kw.setdefault("time_in_zones", None)
    kw.setdefault("raw_summary", {"average_temp": 12})
    return _activity(db, user_id, on, **kw)


def test_average_temp_is_projected_out_of_raw_summary_by_the_query(db):
    """The heat flag on each chart point comes from the JSON column via SQL."""
    user_id = _user(db)
    today = date.today()
    _clean_run(db, user_id, today - timedelta(days=1), raw_summary={"average_temp": 29})
    _clean_run(db, user_id, today - timedelta(days=2), raw_summary={"average_temp": 12})
    _clean_run(db, user_id, today - timedelta(days=3), raw_summary={})

    points = get_trends_report(db, "7D", user_id=user_id).efficiency_trend
    # Chronological: day-3 (no temp), day-2 (12 C), day-1 (29 C).
    # 29 degrees C is flagged hot; 12 is not; an activity with no recorded temperature
    # carries None and is NOT flagged — absent is not cool and not hot.
    assert [(p.average_temp, p.hot) for p in points] == [
        (None, False), (12.0, False), (29.0, True),
    ]


def test_clean_aggregate_excludes_hot_hilly_and_stoppy_runs(db):
    """The headline's clean basis is computed over the real projection: three
    confounded runs at a much lower efficiency move the all-activity mean and
    leave the clean mean alone."""
    user_id = _user(db)
    today = date.today()
    # Two clean runs at 0.02.
    _clean_run(db, user_id, today - timedelta(days=1))
    _clean_run(db, user_id, today - timedelta(days=2))
    # Three confounded runs at 0.01 (1500 m in 1000 s at 150 bpm), one per confounder.
    slow = dict(distance_m=1500, moving_time_s=1000)
    _clean_run(db, user_id, today - timedelta(days=3), elev_gain_m=100.0, **slow)   # 66 m/km
    _clean_run(db, user_id, today - timedelta(days=4),
               elapsed_time_s=2000, **slow)                                        # 50% stopped
    _clean_run(db, user_id, today - timedelta(days=5),
               raw_summary={"average_temp": 31}, **slow)                            # hot

    summary = get_trends_report(db, "7D", user_id=user_id).summary

    assert summary.efficiency_total_count == 5
    assert summary.efficiency_clean_count == 2
    # All five: (0.02*2 + 0.01*3) / 5 = 0.014 — dragged down by conditions.
    assert summary.avg_efficiency_mps_per_bpm == 0.014
    # Clean only: the like-for-like read.
    assert summary.avg_efficiency_clean_mps_per_bpm == 0.02


def test_clean_aggregate_is_computed_for_the_previous_window_too(db):
    """The comparison needs both sides on the same basis, or it is not like-for-like."""
    user_id = _user(db)
    today = date.today()
    _clean_run(db, user_id, today - timedelta(days=1))
    # Previous window: one clean run at 0.01 and one hot run at 0.02.
    _clean_run(db, user_id, today - timedelta(days=8),
               distance_m=1500, moving_time_s=1000)
    _clean_run(db, user_id, today - timedelta(days=9),
               raw_summary={"average_temp": 30})

    report = get_trends_report(db, "7D", user_id=user_id)

    assert report.previous_summary.efficiency_total_count == 2
    assert report.previous_summary.efficiency_clean_count == 1
    assert report.previous_summary.avg_efficiency_mps_per_bpm == 0.015
    assert report.previous_summary.avg_efficiency_clean_mps_per_bpm == 0.01


def test_clean_aggregate_is_none_when_every_run_is_confounded(db):
    user_id = _user(db)
    today = date.today()
    _clean_run(db, user_id, today - timedelta(days=1), raw_summary={"average_temp": 33})

    summary = get_trends_report(db, "7D", user_id=user_id).summary

    assert summary.avg_efficiency_mps_per_bpm == 0.02
    assert summary.avg_efficiency_clean_mps_per_bpm is None
    assert summary.efficiency_clean_count == 0
    assert summary.efficiency_total_count == 1


def test_a_non_numeric_stored_temperature_does_not_break_the_report(db):
    """`raw_summary` is untyped. A junk `average_temp` must degrade to unrecorded,
    never abort the scan — casting to float in SQL would raise on Postgres while
    passing silently on SQLite, which is why the coercion is in Python."""
    user_id = _user(db)
    today = date.today()
    _clean_run(db, user_id, today - timedelta(days=1),
               raw_summary={"average_temp": "very warm"})
    _clean_run(db, user_id, today - timedelta(days=2),
               raw_summary={"average_temp": {"c": 20}})

    report = get_trends_report(db, "7D", user_id=user_id)

    assert [p.average_temp for p in report.efficiency_trend] == [None, None]
    assert [p.hot for p in report.efficiency_trend] == [False, False]
    # Unrecorded is not confounded, so both still count as clean.
    assert report.summary.efficiency_clean_count == 2
