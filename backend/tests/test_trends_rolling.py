"""Rolling-mode bars roll back from today, not calendar chunks (#630).

In rolling mode the week / 2-week / month bars must be fixed-width blocks
anchored to the current date — the newest bar ends today and each older bar is
the preceding block — instead of snapping to ISO-Monday weeks, the epoch
fortnight grid, or calendar months (which is what calendar mode keeps). A
leading block that only partly overlaps the window shows its out-of-window days
as a faded segment, exactly like a calendar edge bucket (the bar shows the whole
block, in-window solid and the excluded part faded).
"""

import uuid
from datetime import date, datetime, time, timedelta

from app.models import Activity, DerivedMetric, User
from app.services.activity_facts import DailyFact
from app.services.activity_facts import rolling_bin_start as _rolling_bin_start
from app.services.trends import (
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


def test_rolling_weekly_leading_block_fades_its_out_of_window_days():
    # 30-day window ending the anchor: since = anchor-29. Weekly (7-day) blocks
    # back from the anchor give a leading block that straddles the window start,
    # so its out-of-window days show as a faded segment (like a calendar edge
    # week), carrying the value of a run that fell before the window.
    since = _ANCHOR - timedelta(days=29)
    in_window = [DailyFact(_ANCHOR)]
    in_window[0].total_distance_m = 4000
    # A run 3 days before the window start lands in the leading block's faded part.
    pre = [DailyFact(since - timedelta(days=3))]
    pre[0].total_distance_m = 5000
    weeks = build_weekly_buckets(
        in_window, since=since, until=_ANCHOR, rolling_anchor=_ANCHOR,
        pre_window_daily=pre,
    )

    # Newest block ends on the anchor; blocks step back by exactly 7 days.
    assert weeks[-1].week_start == _ANCHOR - timedelta(days=6)
    starts = [w.week_start for w in weeks]
    for a, b in zip(starts, starts[1:]):
        assert (b - a).days == 7

    # Leading block: 2 in-window days + 5 out-of-window, with the pre-window run
    # carried as the faded out-of-window value (the bar shows the whole week).
    lead = weeks[0]
    assert lead.in_period_days == 2
    assert lead.out_of_period_days == 5
    assert lead.out_of_period_distance_m == 5000
    # The newest, fully-in-window block never fades.
    assert weeks[-1].in_period_days == 7
    assert weeks[-1].out_of_period_days == 0


def test_rolling_monthly_blocks_are_thirty_day_steps_from_the_anchor():
    # 100-day window: not a multiple of 30, so the leading 30-day block straddles
    # the window start and must fade (a clean multiple like 90 would not).
    since = _ANCHOR - timedelta(days=100)
    facts = [DailyFact(_ANCHOR)]
    months = build_period_buckets(
        facts, "monthly", since=since, until=_ANCHOR, rolling_anchor=_ANCHOR
    )
    assert months[-1].period_start == _ANCHOR - timedelta(days=29)
    starts = [m.period_start for m in months]
    for a, b in zip(starts, starts[1:]):
        assert (b - a).days == 30
    # Newest block is fully in-window (no fade); the leading block straddles the
    # window start, so it reports out-of-window days.
    assert months[-1].out_of_period_days == 0
    assert months[0].out_of_period_days > 0


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
    preceding 7 days, and the leading partial fades its excluded days like a
    calendar edge week."""
    user_id = _user(db)
    today = date.today()
    _activity_on(db, user_id, today, distance_m=4000)
    _activity_on(db, user_id, today - timedelta(days=29), distance_m=1000)
    # A run just before the 30-day window: it falls in the leading block's
    # out-of-window (faded) part, not in the in-window totals.
    _activity_on(db, user_id, today - timedelta(days=31), distance_m=700)

    report = get_trends_report(db, "30D", user_id=user_id)
    weekly = report.weekly_distance

    # Newest block ends today (starts 6 days before it), blocks step back by 7.
    assert weekly[-1].week_start == today - timedelta(days=6)
    for a, b in zip(weekly, weekly[1:]):
        assert (b.week_start - a.week_start).days == 7
    # Newest block is fully in-window: no fade.
    assert weekly[-1].out_of_period_days == 0
    assert weekly[-1].out_of_period_distance_m == 0
    # Leading block straddles the window start: it fades its excluded days and
    # carries the pre-window run as the faded value (bar shows the whole week).
    assert weekly[0].out_of_period_days > 0
    assert weekly[0].out_of_period_distance_m == 700
    # Today's run lands in the newest block; in-window totals stay conserved and
    # exclude the faded pre-window run.
    assert weekly[-1].total_distance_m == 4000
    assert sum(w.total_distance_m for w in weekly) == report.summary.total_distance_m
    assert report.summary.total_distance_m == 5000  # 4000 + 1000, not the 700


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
