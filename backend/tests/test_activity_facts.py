"""The shared fact stream: projection, windows, buckets, zones, norms (#804).

The bucketing behaviour that used to be asserted separately against four
independently-written bucketers is asserted here ONCE, against the one bucketer they
were consolidated into; the granularity-specific suites keep only the questions that
are genuinely about their granularity.
"""

from datetime import date, datetime, timedelta, timezone

import pytest

from app.services.activity_facts import (
    ActivityFact,
    Bucket,
    DailyFact,
    bucket_daily_facts,
    bucket_key_fn,
    bucket_zone_seconds,
    collapse_to_3_zones,
    facts_in_window,
    query_facts,
    scan,
    scan_cache,
    sum_zone_seconds,
    window_bounds,
)
from app.models import Activity, User


# ---------------------------------------------------------------------------
# The in-memory narrowing must match the SQL predicate exactly
# ---------------------------------------------------------------------------


def _fact(start: datetime, *, zones=None) -> ActivityFact:
    f = ActivityFact.__new__(ActivityFact)
    f.activity_id = start.isoformat()
    f.start_date = start
    f.local_date = start.date()
    f.activity_type = "Run"
    f.user_intent = None
    f.distance_m = 1000
    f.moving_time_s = 300
    f.elapsed_time_s = 300
    f.elev_gain_m = 0.0
    f.avg_hr = 140
    f.avg_cadence = None
    f.average_speed_mps = 3.3
    f.effort_score = 10.0
    f.effort = "easy"
    f.time_in_zones = zones
    f.average_temp = None
    f.structure = None
    f.interval_structure = None
    f.duration_class = None
    f.hr_drift = None
    return f


def test_window_is_start_inclusive_end_exclusive():
    lo, hi = window_bounds(date(2026, 6, 1), date(2026, 6, 3))
    facts = [
        _fact(datetime(2026, 5, 31, 23, 59)),
        _fact(datetime(2026, 6, 1, 0, 0)),      # exactly the inclusive lower bound
        _fact(datetime(2026, 6, 2, 12, 0)),
        _fact(datetime(2026, 6, 3, 0, 0)),      # exactly the exclusive upper bound
    ]
    kept = facts_in_window(facts, date(2026, 6, 1), date(2026, 6, 3))
    assert [f.start_date for f in kept] == [
        datetime(2026, 6, 1, 0, 0),
        datetime(2026, 6, 2, 12, 0),
    ]
    assert (lo, hi) == (datetime(2026, 6, 1), datetime(2026, 6, 3))


def test_narrowing_handles_timezone_aware_instants():
    """Postgres returns `start_date` timezone-AWARE; the in-memory SQLite the suite
    runs on returns it NAIVE, and the window bound is always naive. Comparing the two
    raises TypeError in Python even though SQL compares them happily, so the narrowing
    must normalise. Caught only by a real-data run, hence this unit pin.
    """
    aware = [
        _fact(datetime(2026, 6, 1, 0, 0, tzinfo=timezone.utc)),
        _fact(datetime(2026, 6, 2, 12, 0, tzinfo=timezone.utc)),
        _fact(datetime(2026, 6, 4, 0, 0, tzinfo=timezone.utc)),
    ]
    kept = facts_in_window(aware, date(2026, 6, 1), date(2026, 6, 3))
    assert len(kept) == 2


def test_unbounded_window_keeps_everything():
    facts = [_fact(datetime(2020, 1, 1)), _fact(datetime(2026, 6, 1))]
    assert len(facts_in_window(facts, None, None)) == 2


# ---------------------------------------------------------------------------
# The cached scan
# ---------------------------------------------------------------------------


def _seed(db) -> User:
    user = User(email="facts@example.com")
    db.add(user)
    db.commit()
    for i, day in enumerate([date(2026, 6, 1), date(2026, 6, 20), date(2020, 1, 5)]):
        db.add(
            Activity(
                user_id=user.id,
                strava_activity_id=8800 + i,
                start_date=datetime(day.year, day.month, day.day, 9, 0),
                type="Run",
                name="Run",
                distance_m=8000,
                moving_time_s=2400,
                elapsed_time_s=2400,
            )
        )
    db.commit()
    return user


def _count_queries(db):
    """Count fact-projection statements issued on this session."""
    from sqlalchemy import event

    seen = []

    def before(conn, cursor, statement, params, ctx, many):
        if "avg_cadence" in statement:
            seen.append(statement)

    event.listen(db.get_bind().engine, "before_cursor_execute", before)
    return seen, lambda: event.remove(db.get_bind().engine, "before_cursor_execute", before)


def test_scan_outside_a_cache_queries_every_time(db):
    user = _seed(db)
    seen, stop = _count_queries(db)
    try:
        scan(db, date(2026, 5, 1), date(2026, 7, 1), user_id=user.id)
        scan(db, date(2026, 6, 1), date(2026, 6, 15), user_id=user.id)
    finally:
        stop()
    assert len(seen) == 2


def test_cached_scan_serves_a_contained_window_without_a_second_query(db):
    user = _seed(db)
    seen, stop = _count_queries(db)
    try:
        with scan_cache(db):
            wide = scan(db, date(2015, 1, 1), date(2026, 7, 1), user_id=user.id)
            narrow = scan(db, date(2026, 6, 1), date(2026, 6, 15), user_id=user.id)
    finally:
        stop()

    assert len(seen) == 1, "the contained window must not re-issue the projection"
    assert len(wide) == 3
    # ...and it must be the SAME answer the narrow query would have given.
    assert [f.activity_id for f in narrow] == [
        f.activity_id for f in query_facts(db, date(2026, 6, 1), date(2026, 6, 15), user_id=user.id)
    ]


def test_cached_scan_widens_rather_than_growing_without_bound(db):
    """A request the cache does not cover refetches the UNION and replaces the entry,
    so a third, narrower request costs nothing."""
    user = _seed(db)
    seen, stop = _count_queries(db)
    try:
        with scan_cache(db):
            scan(db, date(2026, 6, 1), date(2026, 7, 1), user_id=user.id)   # narrow first
            scan(db, date(2015, 1, 1), date(2026, 7, 1), user_id=user.id)   # not covered
            scan(db, date(2026, 6, 15), date(2026, 7, 1), user_id=user.id)  # now covered
    finally:
        stop()
    assert len(seen) == 2


def test_session_shape_keys_the_cache_separately(db):
    """The wide scans must stay lean (#650): a shape-less fetch can never answer a
    request that needs the per-rep interval columns."""
    user = _seed(db)
    seen, stop = _count_queries(db)
    try:
        with scan_cache(db):
            scan(db, date(2015, 1, 1), date(2026, 7, 1), user_id=user.id)
            scan(
                db, date(2026, 6, 1), date(2026, 7, 1),
                user_id=user.id, include_session_shape=True,
            )
    finally:
        stop()
    assert len(seen) == 2


def test_scan_cache_does_not_outlive_its_block(db):
    user = _seed(db)
    with scan_cache(db):
        scan(db, date(2015, 1, 1), date(2026, 7, 1), user_id=user.id)
    seen, stop = _count_queries(db)
    try:
        scan(db, date(2026, 6, 1), date(2026, 6, 15), user_id=user.id)
    finally:
        stop()
    assert len(seen) == 1, "a read-time snapshot must not be served to a later assembly"


def test_scan_is_owner_scoped(db):
    """The cache key carries the owner, so one runner's fetch can never answer
    another's question."""
    alice = _seed(db)
    bob = User(email="bob-facts@example.com")
    db.add(bob)
    db.commit()
    with scan_cache(db):
        assert len(scan(db, None, date(2026, 7, 1), user_id=alice.id)) == 3
        assert scan(db, None, date(2026, 7, 1), user_id=bob.id) == []


# ---------------------------------------------------------------------------
# The one bucketer
# ---------------------------------------------------------------------------


def _daily(d: date, dist: int = 0, effort: float = 0.0) -> DailyFact:
    df = DailyFact(d)
    df.total_distance_m = dist
    df.total_effort_score = effort
    df.activity_count = 1 if dist or effort else 0
    return df


@pytest.mark.parametrize(
    "period,expected_starts",
    [
        ("weekly", [date(2026, 6, 1), date(2026, 6, 8), date(2026, 6, 15)]),
        ("monthly", [date(2026, 6, 1)]),
    ],
)
def test_calendar_buckets_are_continuous_across_the_window(period, expected_starts):
    """Empty buckets are filled so a chart has a continuous x-axis, at every
    granularity — the property each of the old bucketers asserted for itself."""
    buckets = bucket_daily_facts(
        [_daily(date(2026, 6, 2), dist=5000)],
        period,
        since=date(2026, 6, 1),
        end=date(2026, 6, 20),
    )
    assert [b.start for b in buckets] == expected_starts
    assert buckets[0].total_distance_m == 5000


def test_bucket_exposes_both_legacy_start_names():
    """`WeekBucket.week_start` and `PeriodBucket.period_start` were the only thing
    distinguishing the two classes; both now read the one field."""
    b = Bucket(date(2026, 6, 1))
    assert b.week_start == b.period_start == b.start


def test_rolling_buckets_roll_back_from_the_anchor_not_the_calendar():
    anchor = date(2026, 7, 8)  # a Wednesday, deliberately not week-aligned
    buckets = bucket_daily_facts(
        [_daily(anchor, dist=1000)],
        "weekly",
        since=anchor - timedelta(days=13),
        end=anchor,
        rolling_anchor=anchor,
    )
    assert [b.start for b in buckets] == [
        anchor - timedelta(days=13),
        anchor - timedelta(days=6),
    ]


def test_edge_bucket_records_coverage_and_carries_out_of_window_value():
    """The distinction that looks like duplication and is not: a bucket straddling
    the window boundary keeps its in-period totals separate from the out-of-period
    ones, so the chart can stack the excluded part faded."""
    buckets = bucket_daily_facts(
        [_daily(date(2026, 6, 4), dist=3000)],
        "weekly",
        since=date(2026, 6, 3),          # a Wednesday: the leading week is partial
        end=date(2026, 6, 9),
        pre_window_daily=[_daily(date(2026, 6, 1), dist=7000)],
    )
    lead = buckets[0]
    assert lead.start == date(2026, 6, 1)
    assert (lead.in_period_days, lead.out_of_period_days) == (5, 2)
    assert lead.total_distance_m == 3000, "totals stay strictly in-period"
    assert lead.out_of_period_distance_m == 7000


def test_all_range_treats_every_bucket_as_fully_in_period():
    buckets = bucket_daily_facts(
        [_daily(date(2026, 6, 4), dist=1000)],
        "weekly",
        since=None,
        end=date(2026, 6, 9),
    )
    assert all(b.in_period_days == 7 and b.out_of_period_days == 0 for b in buckets)


# ---------------------------------------------------------------------------
# Zones
# ---------------------------------------------------------------------------


def test_zone_collapse_is_z1z2_z3_z4z5():
    assert collapse_to_3_zones({"Z1": 60, "Z2": 30, "Z3": 20, "Z4": 10, "Z5": 5}) == (90, 20, 15)
    assert collapse_to_3_zones({}) == (0, 0, 0)


def test_zone_accumulation_shares_the_buckets_keying():
    """The zone bars must key exactly as the value bars do, or they would not line up."""
    facts = [
        _fact(datetime(2026, 6, 2, 9), zones={"Z1": 600, "Z3": 300}),
        _fact(datetime(2026, 6, 3, 9), zones={"Z4": 120}),
        _fact(datetime(2026, 6, 10, 9), zones={"Z2": 60}),
        _fact(datetime(2026, 6, 11, 9), zones=None),  # no zone data contributes nothing
    ]
    key = bucket_key_fn("weekly", None)
    assert bucket_zone_seconds(facts, key) == {
        date(2026, 6, 1): (600, 300, 120),
        date(2026, 6, 8): (60, 0, 0),
    }
    assert sum_zone_seconds(facts) == (660, 300, 120)
