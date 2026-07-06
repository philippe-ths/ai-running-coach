"""Rolling-mode bars roll back from today, not calendar chunks (#630).

In rolling mode the week / 2-week / month bars must be fixed-width blocks
anchored to the current date — the newest bar ends today and each older bar is
the preceding block — instead of snapping to ISO-Monday weeks, the epoch
fortnight grid, or calendar months (which is what calendar mode keeps). The
leading block is simply a shorter bar (fewer in-window days) with no faded
out-of-window segment.
"""

import uuid
from datetime import date, datetime, time, timedelta

from app.models import Activity, DerivedMetric, User
from app.services.trends import (
    DailyFact,
    _rolling_bin_start,
    build_period_buckets,
    build_weekly_buckets,
    get_trends_report,
)


# --- pure keying -------------------------------------------------------------

_ANCHOR = date(2026, 7, 8)  # a Wednesday — deliberately not week/month aligned


def test_rolling_bin_newest_block_ends_on_the_anchor():
    # The block containing the anchor starts bin_days-1 before it.
    assert _rolling_bin_start(_ANCHOR, _ANCHOR, 7) == _ANCHOR - timedelta(days=6)
    assert _rolling_bin_start(_ANCHOR, _ANCHOR, 14) == _ANCHOR - timedelta(days=13)
    assert _rolling_bin_start(_ANCHOR, _ANCHOR, 30) == _ANCHOR - timedelta(days=29)


def test_rolling_bin_is_a_fixed_grid_back_from_the_anchor():
    for bin_days in (7, 14, 30):
        start = _rolling_bin_start(_ANCHOR, _ANCHOR, bin_days)
        # Every day within the block maps to the same start...
        for offset in range(bin_days):
            assert _rolling_bin_start(start + timedelta(days=offset), _ANCHOR, bin_days) == start
        # ...and the day before opens the previous block, exactly bin_days back.
        assert (
            _rolling_bin_start(start - timedelta(days=1), _ANCHOR, bin_days)
            == start - timedelta(days=bin_days)
        )


def test_rolling_weekly_leading_block_is_a_short_bar_with_no_fade():
    # 30-day window ending the anchor: since = anchor-29. Weekly (7-day) blocks
    # back from the anchor give a 2-day leading block, in-window only, no spill.
    since = _ANCHOR - timedelta(days=29)
    facts = [DailyFact(_ANCHOR)]
    facts[0].total_distance_m = 4000
    weeks = build_weekly_buckets(
        facts, since=since, until=_ANCHOR, rolling_anchor=_ANCHOR
    )

    # Newest block ends on the anchor; blocks step back by exactly 7 days.
    assert weeks[-1].week_start == _ANCHOR - timedelta(days=6)
    starts = [w.week_start for w in weeks]
    for a, b in zip(starts, starts[1:]):
        assert (b - a).days == 7

    # Leading block: only its in-window days count, and there is NO faded segment.
    assert weeks[0].in_period_days == 2       # since (anchor-29) .. block end
    assert weeks[0].out_of_period_days == 0
    assert all(w.out_of_period_days == 0 for w in weeks)
    assert all(w.out_of_period_distance_m == 0 for w in weeks)
    # Interior blocks are full 7-day weeks.
    assert weeks[1].in_period_days == 7


def test_rolling_monthly_blocks_are_thirty_day_steps_from_the_anchor():
    since = _ANCHOR - timedelta(days=89)  # ~3M
    facts = [DailyFact(_ANCHOR)]
    months = build_period_buckets(
        facts, "monthly", since=since, until=_ANCHOR, rolling_anchor=_ANCHOR
    )
    assert months[-1].period_start == _ANCHOR - timedelta(days=29)
    starts = [m.period_start for m in months]
    for a, b in zip(starts, starts[1:]):
        assert (b - a).days == 30
    assert all(m.out_of_period_days == 0 for m in months)


# --- end-to-end (anchored to the real today) ---------------------------------


def _user(db) -> uuid.UUID:
    user = User(email=f"{uuid.uuid4()}@example.com")
    db.add(user)
    db.flush()
    return user.id


def _activity_on(db, user_id, on: date, *, distance_m=5000, moving_time_s=1500):
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
            id=uuid.uuid4(), activity_id=activity.id, effort_score=3.0,
            confidence="high", flags=[], confidence_reasons=[],
        )
    )
    db.flush()
    return activity


def test_rolling_weekly_bars_end_today_and_step_back(db):
    """Default (rolling) weekly bars: newest ends today, each older bar is the
    preceding 7 days, and none carries a faded out-of-window segment."""
    user_id = _user(db)
    today = date.today()
    _activity_on(db, user_id, today, distance_m=4000)
    _activity_on(db, user_id, today - timedelta(days=29), distance_m=1000)

    report = get_trends_report(db, "30D", user_id=user_id)
    weekly = report.weekly_distance

    # Newest block ends today (starts 6 days before it), blocks step back by 7.
    assert weekly[-1].week_start == today - timedelta(days=6)
    for a, b in zip(weekly, weekly[1:]):
        assert (b.week_start - a.week_start).days == 7
    # Rolling never fades: no out-of-window segment on any bar.
    assert all(w.out_of_period_days == 0 for w in weekly)
    assert all(w.out_of_period_distance_m == 0 for w in weekly)
    # Today's run lands in the newest block; totals are conserved.
    assert weekly[-1].total_distance_m == 4000
    assert sum(w.total_distance_m for w in weekly) == report.summary.total_distance_m


def test_rolling_monthly_and_biweekly_bars_end_today(db):
    user_id = _user(db)
    today = date.today()
    _activity_on(db, user_id, today, distance_m=4000)

    report = get_trends_report(db, "3M", user_id=user_id)

    assert report.monthly_distance[-1].period_start == today - timedelta(days=29)
    for a, b in zip(report.monthly_distance, report.monthly_distance[1:]):
        assert (b.period_start - a.period_start).days == 30

    assert report.biweekly_distance[-1].period_start == today - timedelta(days=13)
    for a, b in zip(report.biweekly_distance, report.biweekly_distance[1:]):
        assert (b.period_start - a.period_start).days == 14


def test_calendar_mode_still_snaps_weekly_bars_to_monday(db):
    """Calendar mode is untouched (#630 is rolling-only): weekly bars still start
    on Mondays, unlike the rolling bars above."""
    user_id = _user(db)
    _activity_on(db, user_id, date.today(), distance_m=4000)

    report = get_trends_report(db, "3M", user_id=user_id, mode="calendar")

    assert all(w.week_start.weekday() == 0 for w in report.weekly_distance)
