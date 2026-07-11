"""#648: the on-demand coach-chat data tools (`query_tools.py`).

Covers the two load-bearing invariants — server-held owner scoping (a cross-user
id can never widen scope) and server-resolved windows (the model never does date
math) — plus the coach-framed output shape and graceful degradation.
"""

from datetime import date, datetime, timedelta
from uuid import uuid4

from app.models import Activity, DerivedMetric, User
from app.models.activity_stream import ActivityStream
from app.services.coach import query_tools as qt

TODAY = date(2026, 7, 11)


def _user(db) -> User:
    u = User(email=f"u-{uuid4()}@example.com")
    db.add(u)
    db.commit()
    return u


def _activity(
    db, user, *, days_ago, type="Run", distance_m=8000, moving_time_s=2400,
    structure="continuous", duration_class="standard", interval_structure=None,
    effort="easy", with_streams=False,
) -> Activity:
    start = datetime(2026, 7, 11, 9, 0, 0) - timedelta(days=days_ago)
    a = Activity(
        user_id=user.id, strava_activity_id=int(uuid4().int % 1_000_000_000),
        start_date=start, type=type, name="r", distance_m=distance_m,
        moving_time_s=moving_time_s, elapsed_time_s=moving_time_s, elev_gain_m=10.0,
        avg_hr=145, avg_cadence=170, raw_summary={},
    )
    db.add(a)
    db.commit()
    db.add(DerivedMetric(
        activity_id=a.id, effort=effort, structure=structure,
        duration_class=duration_class, interval_structure=interval_structure,
        effort_score=60.0, hr_drift=4.2, flags=[], confidence="medium",
        confidence_reasons=[],
    ))
    if with_streams:
        db.add(ActivityStream(activity_id=a.id, stream_type="time",
                              data=list(range(0, moving_time_s, 10))))
        n = len(list(range(0, moving_time_s, 10)))
        db.add(ActivityStream(activity_id=a.id, stream_type="distance",
                              data=[i * (distance_m / n) for i in range(n)]))
        db.add(ActivityStream(activity_id=a.id, stream_type="heartrate",
                              data=[145] * n))
    db.commit()
    db.refresh(a)
    return a


# --- owner scoping (the security surface) ------------------------------------

def test_list_activities_is_owner_scoped(db):
    a_user, b_user = _user(db), _user(db)
    _activity(db, a_user, days_ago=3, distance_m=10000)
    _activity(db, b_user, days_ago=3, distance_m=99000)  # another runner's run

    out = qt.list_activities_in_range(
        db, a_user.id, window="last_30_days", type_filter=None, today=TODAY
    )
    assert out["count"] == 1
    # only user A's run, never B's 99km outlier
    assert all(act["distance_km"] == 10.0 for act in out["activities"])


def test_get_session_detail_refuses_cross_user_id(db):
    """The core security invariant: a model-supplied activity_id belonging to
    ANOTHER runner returns not_found, never that runner's data."""
    a_user, b_user = _user(db), _user(db)
    b_activity = _activity(db, b_user, days_ago=2, with_streams=True)

    out = qt.get_session_detail(
        db, a_user.id, activity_id=str(b_activity.id), today=TODAY
    )
    assert out == {"error": "not_found", "activity_id": str(b_activity.id)}


def test_get_session_detail_returns_own_session(db):
    a_user = _user(db)
    a = _activity(db, a_user, days_ago=2, with_streams=True,
                  interval_structure={"source": "recorded_laps",
                                      "work_segments": [{}] * 7,
                                      "detection_confidence": "medium"})
    out = qt.get_session_detail(db, a_user.id, activity_id=str(a.id), today=TODAY)
    assert out["activity_id"] == str(a.id)
    # the interval SOURCE is surfaced (the #661 lap-button concern)
    assert out["interval"]["source"] == "recorded_laps"
    assert out["interval"]["rep_count"] == 7
    assert out["splits"], "splits summary present when streams exist"


def test_training_summary_is_owner_scoped(db):
    a_user, b_user = _user(db), _user(db)
    _activity(db, a_user, days_ago=3, distance_m=10000)
    _activity(db, b_user, days_ago=3, distance_m=50000)

    out = qt.get_training_summary(
        db, a_user.id, window="last_30_days", type_filter=None, today=TODAY
    )
    assert out["totals"]["sessions"] == 1
    assert out["totals"]["distance_km"] == 10.0


# --- server-resolved windows (no model date math) ----------------------------

def test_window_bounds_exclude_outside_range(db):
    u = _user(db)
    _activity(db, u, days_ago=5)    # inside last_30_days
    _activity(db, u, days_ago=40)   # outside last_30_days, inside last_90_days

    out30 = qt.list_activities_in_range(db, u.id, window="last_30_days", type_filter=None, today=TODAY)
    out90 = qt.list_activities_in_range(db, u.id, window="last_90_days", type_filter=None, today=TODAY)
    assert out30["count"] == 1
    assert out90["count"] == 2


def test_unknown_window_returns_error(db):
    u = _user(db)
    out = qt.list_activities_in_range(db, u.id, window="two_tuesdays_ago", type_filter=None, today=TODAY)
    assert out["error"] == "unknown_window"


def test_window_range_is_echoed(db):
    u = _user(db)
    out = qt.list_activities_in_range(db, u.id, window="last_7_days", type_filter=None, today=TODAY)
    # the server states the exact resolved range so the coach grounds its answer in it
    assert "2026-07-05 to 2026-07-11" in out["window"]["label"]  # 7 days incl. today


# --- coach-framed shape ------------------------------------------------------

def test_list_entries_are_coach_framed(db):
    u = _user(db)
    _activity(db, u, days_ago=1, distance_m=8000, moving_time_s=2400,
              duration_class="long")
    out = qt.list_activities_in_range(db, u.id, window="last_7_days", type_filter=None, today=TODAY)
    entry = out["activities"][0]
    assert entry["distance_km"] == 8.0
    assert entry["pace_per_km"] == "5:00"   # 2400s / 8km = 300s/km
    assert entry["duration"] == "40m"
    assert entry["long_run"] is True
    assert entry["weekday"]  # a given day, not a computed one
    assert entry["activity_id"]  # the handle for get_session_detail


def test_type_filter_maps_to_modality(db):
    u = _user(db)
    _activity(db, u, days_ago=2, type="Run")
    _activity(db, u, days_ago=2, type="WeightTraining", distance_m=0, moving_time_s=1800)

    runs = qt.list_activities_in_range(db, u.id, window="last_7_days", type_filter="run", today=TODAY)
    strength = qt.list_activities_in_range(db, u.id, window="last_7_days", type_filter="strength", today=TODAY)
    assert {a["type"] for a in runs["activities"]} == {"Run"}
    assert {a["type"] for a in strength["activities"]} == {"WeightTraining"}


def test_training_summary_totals_and_by_type(db):
    u = _user(db)
    _activity(db, u, days_ago=2, type="Run", distance_m=10000)
    _activity(db, u, days_ago=3, type="Run", distance_m=6000)
    _activity(db, u, days_ago=4, type="WeightTraining", distance_m=0, moving_time_s=1800)

    out = qt.get_training_summary(db, u.id, window="last_30_days", type_filter=None, today=TODAY)
    assert out["totals"]["sessions"] == 3
    assert out["totals"]["distance_km"] == 16.0
    by_type = {b["type"]: b for b in out["by_type"]}
    assert by_type["Run"]["sessions"] == 2
    assert by_type["WeightTraining"]["sessions"] == 1


# --- bounds + graceful degradation -------------------------------------------

def test_list_caps_and_reports_truncation(db):
    u = _user(db)
    for i in range(qt._MAX_LIST_ACTIVITIES + 5):
        _activity(db, u, days_ago=i % 20 + 1)
    out = qt.list_activities_in_range(db, u.id, window="last_90_days", type_filter=None, today=TODAY)
    assert out["count"] == qt._MAX_LIST_ACTIVITIES + 5
    assert out["showing"] == qt._MAX_LIST_ACTIVITIES  # capped, and it says so


def test_get_session_detail_bad_id_is_not_found(db):
    u = _user(db)
    out = qt.get_session_detail(db, u.id, activity_id="not-a-uuid", today=TODAY)
    assert out["error"] == "not_found"


def test_execute_unknown_tool(db):
    u = _user(db)
    out = qt.execute_chat_tool(db, u.id, "drop_table", {}, today=TODAY)
    assert out["error"] == "unknown_tool"


def test_coverage_note_when_window_predates_record(db):
    """The 'peak for the year' guard: when the requested window reaches back before the
    app's recorded history, the tool states that as a FACT so the coach won't claim a
    superlative it cannot support."""
    u = _user(db)
    _activity(db, u, days_ago=40)  # records begin ~40 days before today
    out = qt.list_activities_in_range(db, u.id, window="this_year", type_filter=None, today=TODAY)
    assert out["window"]["records_begin"] is not None
    assert "recorded history" in out["window"]["coverage_note"]


def test_no_coverage_note_when_window_fully_covered(db):
    u = _user(db)
    _activity(db, u, days_ago=40)  # records begin well before a 7-day window
    out = qt.list_activities_in_range(db, u.id, window="last_7_days", type_filter=None, today=TODAY)
    assert "coverage_note" not in out["window"]
    assert out["window"]["records_begin"] is not None


def test_training_summary_all_time_states_record_start(db):
    u = _user(db)
    _activity(db, u, days_ago=10)
    out = qt.get_training_summary(db, u.id, window="all_time", type_filter=None, today=TODAY)
    assert out["window"]["records_begin"] is not None
    assert "coverage_note" in out["window"]  # all_time always reaches before the record


def test_execute_dispatch_reaches_each_tool(db):
    u = _user(db)
    _activity(db, u, days_ago=1)
    assert "activities" in qt.execute_chat_tool(
        db, u.id, "list_activities_in_range", {"window": "last_7_days"}, today=TODAY)
    assert "totals" in qt.execute_chat_tool(
        db, u.id, "get_training_summary", {"window": "last_7_days"}, today=TODAY)
