"""Window-navigation arrows on Trends/Activities/Load step by exactly one
selected period and never silently re-anchor to real "today" (#948).

`get_trends_report`/`get_volume_report` already resolved most of the window
from an explicit `as_of`/`today` date; the bug this closes is narrower but would
have made every rolling-mode step a no-op: the chart x-axis end (`until`) fell
through to a builder's own `date.today()` default whenever the caller passed an
`as_of` that was not literally today, so a stepped-back rolling window still
rendered bars up to the real current date. These tests pin the fix at the
function level (deterministic, independent of the real wall-clock date) and at
the API level (the new `as_of` query params + the earliest-activity-date
floor endpoint).
"""

import uuid
from datetime import date, datetime, time, timedelta

from app.api.profile import get_current_user_profile
from app.models import Activity, DerivedMetric, User
from app.services.activity_facts import resolve_window
from app.services.activity_queries import get_earliest_activity_local_date
from app.services.trends import get_trends_report


def _user(db) -> uuid.UUID:
    user = User(email=f"{uuid.uuid4()}@example.com")
    db.add(user)
    db.flush()
    return user.id


def _activity_on(db, user_id, on: date, *, distance_m: int = 5000) -> Activity:
    activity = Activity(
        user_id=user_id,
        strava_activity_id=int(uuid.uuid4().int % 1_000_000_000),
        start_date=datetime.combine(on, time(12, 0)),
        type="Run",
        name="Run",
        distance_m=distance_m,
        moving_time_s=1500,
        elapsed_time_s=1500,
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


# A fixed anchor divorced from the real wall-clock date, so these tests are
# deterministic regardless of when they run and cannot pass by accident just
# because `as_of` happens to equal the real `date.today()`.
_ANCHOR = date(2021, 3, 17)  # a Wednesday


# ---------------------------------------------------------------------------
# resolve_window: the (range, mode) -> (since, prev_start, prev_end) primitive
# ---------------------------------------------------------------------------


def test_resolve_window_rolling_honours_an_explicit_today():
    since, prev_start, prev_end = resolve_window("7D", "rolling", today=_ANCHOR)
    assert since == _ANCHOR - timedelta(days=6)
    assert prev_end == since
    assert prev_start == since - timedelta(days=7)


def test_resolve_window_calendar_honours_an_explicit_today():
    since, prev_start, prev_end = resolve_window("30D", "calendar", today=_ANCHOR)
    assert since == _ANCHOR.replace(day=1)
    assert prev_end == since


def test_resolve_window_all_range_ignores_today():
    assert resolve_window("ALL", "rolling", today=_ANCHOR) == (None, None, None)


# ---------------------------------------------------------------------------
# get_trends_report: the regression this issue is actually about — the whole
# report (including the chart x-axis end) must follow `as_of`, not real today.
# ---------------------------------------------------------------------------


def test_rolling_report_window_ends_on_as_of_not_real_today(db):
    uid = _user(db)
    _activity_on(db, uid, _ANCHOR)
    _activity_on(db, uid, _ANCHOR - timedelta(days=6))

    report = get_trends_report(db, "7D", user_id=uid, mode="rolling", as_of=_ANCHOR)

    # Exactly 7 continuous days, the last one being as_of itself. Before the fix
    # `until` fell through to a builder's own `date.today()` default, so this
    # would fail (wrong length and/or wrong last date) on any day that isn't
    # literally `_ANCHOR`.
    assert len(report.daily_distance) == 7
    assert report.daily_distance[0].date == _ANCHOR - timedelta(days=6)
    assert report.daily_distance[-1].date == _ANCHOR
    assert report.summary.activity_count == 2


def test_rolling_report_stepping_back_one_period_is_gap_and_overlap_free(db):
    uid = _user(db)
    report_now = get_trends_report(db, "7D", user_id=uid, mode="rolling", as_of=_ANCHOR)
    stepped_back = get_trends_report(
        db, "7D", user_id=uid, mode="rolling", as_of=_ANCHOR - timedelta(days=7)
    )
    # The frontend steps "previous" by moving as_of to (current window start - 1
    # day); the resulting window must abut the current one exactly.
    assert stepped_back.daily_distance[-1].date == report_now.daily_distance[0].date - timedelta(days=1)
    assert len(stepped_back.daily_distance) == len(report_now.daily_distance) == 7


def test_calendar_report_as_of_shows_the_previous_month(db):
    uid = _user(db)
    report = get_trends_report(db, "30D", user_id=uid, mode="calendar", as_of=_ANCHOR)
    assert report.daily_distance[0].date == _ANCHOR.replace(day=1)
    # March 2021 has 31 days; calendar mode frames the whole month.
    assert report.daily_distance[-1].date == date(2021, 3, 31)

    prev_month_anchor = _ANCHOR.replace(day=1) - timedelta(days=1)  # Feb 28, 2021
    prev = get_trends_report(db, "30D", user_id=uid, mode="calendar", as_of=prev_month_anchor)
    assert prev.daily_distance[0].date == date(2021, 2, 1)
    assert prev.daily_distance[-1].date == date(2021, 2, 28)


def test_empty_history_as_of_a_past_date_abstains_cleanly(db):
    uid = _user(db)
    report = get_trends_report(db, "7D", user_id=uid, mode="rolling", as_of=_ANCHOR)
    assert report.summary.activity_count == 0
    assert len(report.daily_distance) == 7
    assert all(p.total_distance_m == 0 for p in report.daily_distance)


def test_as_of_absent_defaults_to_today_byte_identical(db):
    """The compatibility requirement: omitting `as_of` must be unchanged."""
    uid = _user(db)
    _activity_on(db, uid, date.today())

    with_default = get_trends_report(db, "30D", user_id=uid, mode="rolling")
    with_explicit_today = get_trends_report(
        db, "30D", user_id=uid, mode="rolling", as_of=date.today()
    )
    assert with_default.model_dump() == with_explicit_today.model_dump()

    with_default_cal = get_trends_report(db, "30D", user_id=uid, mode="calendar")
    with_explicit_today_cal = get_trends_report(
        db, "30D", user_id=uid, mode="calendar", as_of=date.today()
    )
    assert with_default_cal.model_dump() == with_explicit_today_cal.model_dump()


# ---------------------------------------------------------------------------
# API level: the as_of query params + the earliest-activity-date floor endpoint
# ---------------------------------------------------------------------------


def test_trends_endpoint_honours_as_of(client, db):
    uid = get_current_user_profile(db).user_id
    _activity_on(db, uid, _ANCHOR)

    resp = client.get(f"/api/trends?range=7D&mode=rolling&as_of={_ANCHOR.isoformat()}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["daily_distance"][-1]["date"] == _ANCHOR.isoformat()
    assert body["summary"]["activity_count"] == 1


def test_trends_endpoint_as_of_absent_is_unchanged(client, db):
    resp_default = client.get("/api/trends?range=7D&mode=rolling")
    resp_explicit = client.get(f"/api/trends?range=7D&mode=rolling&as_of={date.today().isoformat()}")
    assert resp_default.status_code == resp_explicit.status_code == 200
    assert resp_default.json() == resp_explicit.json()


def test_volume_endpoint_honours_as_of(client, db):
    uid = get_current_user_profile(db).user_id
    _activity_on(db, uid, _ANCHOR)

    resp = client.get(f"/api/trends/volume?range=7D&as_of={_ANCHOR.isoformat()}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["rolling"]["period_end"] == _ANCHOR.isoformat()
    assert body["rolling"]["period_start"] == (_ANCHOR - timedelta(days=6)).isoformat()


def test_volume_endpoint_as_of_absent_is_unchanged(client, db):
    resp_default = client.get("/api/trends/volume?range=7D")
    resp_explicit = client.get(f"/api/trends/volume?range=7D&as_of={date.today().isoformat()}")
    assert resp_default.status_code == resp_explicit.status_code == 200
    assert resp_default.json() == resp_explicit.json()


def test_earliest_activity_date_endpoint_no_history(client):
    resp = client.get("/api/activities/earliest-date")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"earliest_activity_date": None}


def test_earliest_activity_date_endpoint_with_history(client, db):
    uid = get_current_user_profile(db).user_id
    _activity_on(db, uid, date(2020, 5, 1))
    _activity_on(db, uid, date(2020, 6, 1))
    _activity_on(db, uid, date(2020, 4, 15))

    resp = client.get("/api/activities/earliest-date")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"earliest_activity_date": "2020-04-15"}


def test_get_earliest_activity_local_date_scopes_to_owner_and_excludes_deleted(db):
    uid = _user(db)
    other_uid = _user(db)
    _activity_on(db, other_uid, date(2019, 1, 1))
    a = _activity_on(db, uid, date(2022, 1, 1))
    deleted = _activity_on(db, uid, date(2018, 1, 1))
    deleted.is_deleted = True
    db.flush()

    assert get_earliest_activity_local_date(db, user_id=uid) == date(2022, 1, 1)
    assert get_earliest_activity_local_date(db, user_id=other_uid) == date(2019, 1, 1)
