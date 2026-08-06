"""Coarse bar-granularity rollups for Trends (#432).

The 2-week and month series must (a) align deterministically, (b) aggregate the
underlying daily facts exactly, (c) be continuous (empty buckets filled), and
(d) preserve the window total — the same activities summed at any granularity
yield the same total, so the existing daily/weekly bars and the new coarser bars
never disagree.
"""

import uuid
from datetime import date, datetime, time, timedelta

from app.models import Activity, DerivedMetric, User
from app.services.activity_facts import Bucket as PeriodBucket
from app.services.activity_facts import DailyFact
from app.services.activity_facts import next_period_start as _next_period_start
from app.services.activity_facts import period_start as _period_start
from app.services.trends import build_period_buckets, get_trends_report


# --- pure helpers ------------------------------------------------------------


def _daily(d: date, *, dist=0, time_s=0, effort=0.0, count=1) -> DailyFact:
    df = DailyFact(d)
    df.total_distance_m = dist
    df.total_moving_time_s = time_s
    df.total_effort_score = effort
    df.activity_count = count
    return df


def test_biweekly_period_start_is_aligned_monday():
    # Every day maps to a Monday at most 13 days back, and the mapping is stable
    # for all 14 days of the bin (so the bins are a fixed 14-day grid).
    base = date(2026, 6, 20)  # a Saturday
    for delta in range(-40, 40):
        d = base + timedelta(days=delta)
        ps = _period_start(d, "biweekly")
        assert ps.weekday() == 0
        assert 0 <= (d - ps).days <= 13
        assert _period_start(ps, "biweekly") == ps  # idempotent on the bin start


def test_biweekly_bins_are_a_fixed_14_day_grid():
    ps = _period_start(date(2026, 6, 20), "biweekly")
    # All 14 days from the bin start share that start; day 14 opens the next bin.
    for offset in range(14):
        assert _period_start(ps + timedelta(days=offset), "biweekly") == ps
    assert _period_start(ps + timedelta(days=14), "biweekly") == _next_period_start(ps, "biweekly")
    assert _next_period_start(ps, "biweekly") == ps + timedelta(days=14)


def test_monthly_period_start_is_first_of_month():
    assert _period_start(date(2026, 6, 20), "monthly") == date(2026, 6, 1)
    assert _period_start(date(2026, 6, 1), "monthly") == date(2026, 6, 1)
    assert _next_period_start(date(2026, 6, 1), "monthly") == date(2026, 7, 1)
    assert _next_period_start(date(2026, 12, 1), "monthly") == date(2027, 1, 1)


def test_biweekly_bucket_sums_days_in_the_same_fortnight():
    ps = _period_start(date(2026, 6, 15), "biweekly")
    facts = [
        _daily(ps, dist=1000, time_s=600, effort=2.0),
        _daily(ps + timedelta(days=3), dist=2000, time_s=1200, effort=3.0),
        _daily(ps + timedelta(days=13), dist=500, time_s=300, effort=1.0),
    ]
    buckets = build_period_buckets(facts, "biweekly", since=ps, until=ps + timedelta(days=13))
    assert len(buckets) == 1
    b = buckets[0]
    assert b.period_start == ps
    assert b.total_distance_m == 3500
    assert b.total_moving_time_s == 2100
    assert round(b.total_effort_score, 1) == 6.0
    assert b.activity_count == 3


def test_monthly_buckets_split_on_calendar_month():
    facts = [
        _daily(date(2026, 5, 10), dist=1000),
        _daily(date(2026, 5, 28), dist=1000),
        _daily(date(2026, 6, 2), dist=4000),
    ]
    buckets = build_period_buckets(
        facts, "monthly", since=date(2026, 5, 1), until=date(2026, 6, 30)
    )
    by_start = {b.period_start: b for b in buckets}
    assert by_start[date(2026, 5, 1)].total_distance_m == 2000
    assert by_start[date(2026, 6, 1)].total_distance_m == 4000


def test_period_buckets_are_continuous():
    # One fact at the window start, none after — the empty bins must still appear.
    start = _period_start(date(2026, 1, 5), "biweekly")
    end = start + timedelta(days=70)  # ~5 fortnights later
    facts = [_daily(start, dist=1000)]
    buckets = build_period_buckets(facts, "biweekly", since=start, until=end)
    starts = [b.period_start for b in buckets]
    # Consecutive bins differ by exactly 14 days, no gaps.
    for a, b in zip(starts, starts[1:]):
        assert (b - a).days == 14
    assert starts[0] == start
    assert all(isinstance(b, PeriodBucket) for b in buckets)


# --- end-to-end: totals must agree across granularities ----------------------


def _user(db) -> uuid.UUID:
    user = User(email=f"{uuid.uuid4()}@example.com")
    db.add(user)
    db.flush()
    return user.id


def _activity_on(db, user_id, on: date, *, distance_m=5000, moving_time_s=1500, effort=3.0):
    activity = Activity(
        user_id=user_id,
        strava_activity_id=int(uuid.uuid4().int % 1_000_000_000),
        start_date=datetime.combine(on, time(12, 0)),
        type="Run",
        name="Run",
        distance_m=distance_m,
        moving_time_s=moving_time_s,
        elapsed_time_s=moving_time_s,
        elev_gain_m=0.0,
        raw_summary={},
    )
    db.add(activity)
    db.flush()
    db.add(
        DerivedMetric(
            id=uuid.uuid4(),
            activity_id=activity.id,
            effort_score=effort,
            confidence="high",
            flags=[],
            confidence_reasons=[],
        )
    )
    db.flush()
    return activity


def test_window_total_is_identical_across_all_granularities(db):
    user_id = _user(db)
    today = date.today()
    # A handful of runs spread across the last ~80 days (covered by 3M).
    for offset in (0, 5, 12, 30, 47, 70):
        _activity_on(db, user_id, today - timedelta(days=offset), distance_m=4000, effort=2.5)

    report = get_trends_report(db, "3M", user_id=user_id)

    daily_total = sum(p.total_distance_m for p in report.daily_distance)
    weekly_total = sum(p.total_distance_m for p in report.weekly_distance)
    biweekly_total = sum(p.total_distance_m for p in report.biweekly_distance)
    monthly_total = sum(p.total_distance_m for p in report.monthly_distance)

    assert daily_total == weekly_total == biweekly_total == monthly_total == report.summary.total_distance_m

    # Suffer-score (load) total is likewise conserved across granularities.
    bi_load = round(sum(p.effort_score for p in report.biweekly_suffer_score), 1)
    mo_load = round(sum(p.effort_score for p in report.monthly_suffer_score), 1)
    assert bi_load == mo_load == round(report.summary.total_suffer_score, 1)


def test_coarse_series_are_continuous_over_the_range(db):
    """Default (rolling) coarse series step by a fixed block width (#630): 14-day
    fortnights and 30-day 'months' rolling back from today, with no gaps."""
    user_id = _user(db)
    _activity_on(db, user_id, date.today(), distance_m=4000)

    report = get_trends_report(db, "1Y", user_id=user_id)

    bi = [p.period_start for p in report.biweekly_distance]
    for a, b in zip(bi, bi[1:]):
        assert (b - a).days == 14
    mo = [p.period_start for p in report.monthly_distance]
    for a, b in zip(mo, mo[1:]):
        assert (b - a).days == 30


def test_calendar_coarse_series_stay_on_the_calendar_grid(db):
    """Calendar mode is unchanged (#630 is rolling-only): fortnights step 14 days
    and months land on the 1st, incrementing by one calendar month."""
    user_id = _user(db)
    _activity_on(db, user_id, date.today(), distance_m=4000)

    report = get_trends_report(db, "1Y", user_id=user_id, mode="calendar")

    bi = [p.period_start for p in report.biweekly_distance]
    for a, b in zip(bi, bi[1:]):
        assert (b - a).days == 14
    mo = [p.period_start for p in report.monthly_distance]
    for a, b in zip(mo, mo[1:]):
        assert a.day == 1 and b.day == 1
        assert (b.year, b.month) == (a.year + (a.month == 12), a.month % 12 + 1)
