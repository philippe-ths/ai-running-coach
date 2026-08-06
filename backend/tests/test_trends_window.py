"""Fixed-range windows span exactly N days, not N+1 (#179).

`7D` must cover 7 calendar days (today-6 .. today inclusive), and the
previous-period window must abut it with no gap or overlap so deltas line up.
"""

import uuid
from datetime import date, datetime, time, timedelta

from app.models import Activity, DerivedMetric, User
from app.services.trends import get_trends_report, get_weekly_stats


def _user(db) -> uuid.UUID:
    user = User(email=f"{uuid.uuid4()}@example.com")
    db.add(user)
    db.flush()
    return user.id


def _activity_on(
    db, user_id, on: date, *, distance_m: int = 5000, type: str = "Run",
    moving_time_s: int = 1500,
) -> Activity:
    activity = Activity(
        user_id=user_id,
        strava_activity_id=int(uuid.uuid4().int % 1_000_000_000),
        # Midday so the local date is unambiguous regardless of run time.
        start_date=datetime.combine(on, time(12, 0)),
        type=type,
        name=type,
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
            effort_score=1.0,
            confidence="high",
            flags=[],
            confidence_reasons=[],
        )
    )
    db.flush()
    return activity


def test_7d_continuous_charts_span_exactly_7_days(db):
    report = get_trends_report(db, "7D")
    assert len(report.daily_distance) == 7
    assert len(report.daily_time) == 7
    assert len(report.daily_suffer_score) == 7

    today = date.today()
    assert report.daily_distance[0].date == today - timedelta(days=6)
    assert report.daily_distance[-1].date == today


def test_30d_continuous_charts_span_exactly_30_days(db):
    report = get_trends_report(db, "30D")
    assert len(report.daily_distance) == 30
    assert report.daily_distance[0].date == date.today() - timedelta(days=29)
    assert report.daily_distance[-1].date == date.today()


def test_7d_window_boundary_is_inclusive_of_6_days_ago_only(db):
    user_id = _user(db)
    today = date.today()
    # The 7-day window is [today-6, today]. today-7 falls in the PREVIOUS window.
    _activity_on(db, user_id, today - timedelta(days=6), distance_m=1000)  # current edge
    _activity_on(db, user_id, today - timedelta(days=7), distance_m=2000)  # previous edge

    stats = get_weekly_stats(db)

    assert stats.summary.activity_count == 1
    assert stats.summary.total_distance_m == 1000
    assert stats.previous_summary.activity_count == 1
    assert stats.previous_summary.total_distance_m == 2000


def test_previous_window_abuts_current_with_no_gap(db):
    user_id = _user(db)
    today = date.today()
    # Previous window is [today-13, today-7]. today-13 is its earliest day;
    # today-14 must fall outside both windows.
    _activity_on(db, user_id, today - timedelta(days=13), distance_m=3000)
    _activity_on(db, user_id, today - timedelta(days=14), distance_m=9999)

    stats = get_weekly_stats(db)

    assert stats.previous_summary.activity_count == 1
    assert stats.previous_summary.total_distance_m == 3000


# --- #400 global rolling/calendar window resolver ----------------------------

from app.services.activity_facts import (  # noqa: E402
    resolve_window as _resolve_window,
)

_T = date(2026, 6, 20)  # a Saturday


def test_resolve_window_rolling_matches_legacy():
    # 7D rolling: 7 days ending today; previous is the abutting prior 7 days.
    since, prev_start, prev_end = _resolve_window("7D", "rolling", _T)
    assert since == date(2026, 6, 14)
    assert (prev_start, prev_end) == (date(2026, 6, 7), date(2026, 6, 14))


def test_resolve_window_calendar_week():
    # 2026-06-20 is a Saturday -> current week Mon 06-15; previous is the ENTIRE
    # prior week Mon 06-08 .. Sun 06-14 (#413, vs the full previous calendar term).
    since, prev_start, prev_end = _resolve_window("7D", "calendar", _T)
    assert since == date(2026, 6, 15)
    assert (prev_start, prev_end) == (date(2026, 6, 8), date(2026, 6, 15))  # [Mon, next Mon)


def test_resolve_window_calendar_month():
    # June to date; previous is ALL of May (the full prior calendar month, #413).
    since, prev_start, prev_end = _resolve_window("30D", "calendar", _T)
    assert since == date(2026, 6, 1)
    assert prev_start == date(2026, 5, 1)
    assert prev_end == date(2026, 6, 1)  # exclusive -> all of May


def test_resolve_window_calendar_quarter():
    # Q2 starts Apr 1; prior quarter Q1 starts Jan 1.
    since, prev_start, prev_end = _resolve_window("3M", "calendar", _T)
    assert since == date(2026, 4, 1)
    assert prev_start == date(2026, 1, 1)


def test_resolve_window_all_has_no_bounds():
    assert _resolve_window("ALL", "rolling", _T) == (None, None, None)
    assert _resolve_window("ALL", "calendar", _T) == (None, None, None)


# --- #413 calendar charts frame the FULL period (not truncated at today) ------

import calendar as _cal  # noqa: E402


def test_calendar_7d_charts_frame_full_mon_to_sun_week(db):
    """Calendar 7D spans the whole Mon–Sun week, with days after today rendered
    as empty bars, so the week reads as a fixed frame rather than truncating at
    today (#413)."""
    today = date.today()
    monday = today - timedelta(days=today.weekday())
    sunday = monday + timedelta(days=6)

    report = get_trends_report(db, "7D", mode="calendar")

    assert len(report.daily_distance) == 7
    assert report.daily_distance[0].date == monday
    assert report.daily_distance[-1].date == sunday
    # The suffer-score chart frames the same week.
    assert report.daily_suffer_score[0].date == monday
    assert report.daily_suffer_score[-1].date == sunday


def test_rolling_7d_still_ends_today(db):
    """Rolling mode is unchanged: the frame stops at today, not the week end."""
    report = get_trends_report(db, "7D", mode="rolling")
    assert report.daily_distance[-1].date == date.today()


def test_calendar_30d_charts_frame_full_month(db):
    """Calendar 30D spans the entire calendar month (1st .. last day)."""
    today = date.today()
    first = today.replace(day=1)
    last = today.replace(day=_cal.monthrange(today.year, today.month)[1])

    report = get_trends_report(db, "30D", mode="calendar")

    assert report.daily_distance[0].date == first
    assert report.daily_distance[-1].date == last
    assert len(report.daily_distance) == (last - first).days + 1


def test_volume_report_honors_activity_type_filter(db):
    """The volume norm / typical line must be scoped to the same activity-type
    filter as the charts (#413), so a Run-only view never measures filtered run
    bars against an all-activity norm. The window's current_all for moving_time
    must drop the walks when types=["Run"]."""
    from app.services.trends import get_volume_report

    user_id = _user(db)
    today = date.today()
    # Two runs and two walks inside the rolling 7-day window.
    _activity_on(db, user_id, today, type="Run", moving_time_s=1800)
    _activity_on(db, user_id, today - timedelta(days=1), type="Run", moving_time_s=1800)
    _activity_on(db, user_id, today - timedelta(days=2), type="Walk", moving_time_s=3600)
    _activity_on(db, user_id, today - timedelta(days=3), type="Walk", moving_time_s=3600)

    def _moving_current(report):
        m = [x for x in report.rolling.metrics if x.metric == "moving_time_s"][0]
        return m.current_all

    all_report = get_volume_report(db, user_id, "7D")
    run_report = get_volume_report(db, user_id, "7D", types=["Run"])

    assert _moving_current(all_report) == 1800 + 1800 + 3600 + 3600
    assert _moving_current(run_report) == 1800 + 1800  # walks excluded


def test_calendar_3m_weekly_chart_reaches_quarter_end(db):
    """Calendar 3M (a quarter) frames weekly buckets through the quarter's end
    week, not just the week containing today."""
    today = date.today()
    q_first_month = ((today.month - 1) // 3) * 3 + 1
    end_month = q_first_month + 2
    quarter_last = date(today.year, end_month, _cal.monthrange(today.year, end_month)[1])
    last_week_monday = quarter_last - timedelta(days=quarter_last.weekday())

    report = get_trends_report(db, "3M", mode="calendar")

    assert report.weekly_distance[-1].week_start == last_week_monday
