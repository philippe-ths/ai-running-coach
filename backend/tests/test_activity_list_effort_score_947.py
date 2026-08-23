"""The activity list carries `effort_score` for day-grouping totals (#947).

The frontend groups the list under the day it happened and totals TIME and
LOAD per day. Distance cannot total a mixed day — a bike or a strength session
logs no distance at all — but `effort_score` is one comparable, summable scale
across every activity type with or without HR (#186), so it is the figure the
day's LOAD total is built from. It is null until the activity has been
analysed, distinct from a real zero.
"""

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import event

from app.models import Activity, DerivedMetric, User


def _user(db) -> uuid.UUID:
    user_id = uuid.uuid4()
    db.add(User(id=user_id, email=f"list_{user_id}@example.com"))
    db.flush()
    return user_id


def _activity(
    db,
    user_id,
    *,
    offset_days: int = 0,
    name: str = "Run",
    with_metrics: bool,
    effort_score: float = 50.0,
) -> Activity:
    a = Activity(
        id=uuid.uuid4(),
        user_id=user_id,
        strava_activity_id=abs(hash(str(uuid.uuid4()))) % 10**9,
        name=name,
        type="Run",
        start_date=datetime(2026, 6, 2, 14, 57, tzinfo=timezone.utc) - timedelta(days=offset_days),
        distance_m=12500,
        moving_time_s=4200,
        elapsed_time_s=4300,
        elev_gain_m=20.0,
    )
    db.add(a)
    db.flush()
    if with_metrics:
        db.add(
            DerivedMetric(
                activity_id=a.id,
                effort="easy",
                duration_class="standard",
                structure="continuous",
                is_hilly=False,
                is_race=False,
                effort_score=effort_score,
                confidence="high",
            )
        )
    db.commit()
    return a


def test_list_includes_effort_score_for_analysed_activity(client, db):
    user_id = _user(db)
    _activity(db, user_id, name="Analysed Run", with_metrics=True, effort_score=63.5)

    resp = client.get("/api/activities")
    assert resp.status_code == 200, resp.text
    item = next(i for i in resp.json() if i["name"] == "Analysed Run")
    assert item["effort_score"] == 63.5


def test_list_effort_score_is_null_without_metrics(client, db):
    user_id = _user(db)
    _activity(db, user_id, name="Unanalysed Run", with_metrics=False)

    resp = client.get("/api/activities")
    assert resp.status_code == 200, resp.text
    item = next(i for i in resp.json() if i["name"] == "Unanalysed Run")
    assert item["effort_score"] is None


def test_effort_score_does_not_add_a_query_per_row(client, db):
    """`DerivedMetric` already rides the `joinedload(Activity.metrics)` applied
    in `activity_queries.get_activities` — reading `effort_score` off it must
    stay free. A per-row lookup would be N+1 on a page of 20, hit on every app
    open (the same shape #797's `get_displayable_report_leads` batching guards
    against for the coach lead).
    """
    user_id = _user(db)
    for i in range(15):
        _activity(
            db, user_id, offset_days=i, name=f"Run {i}",
            with_metrics=True, effort_score=float(i),
        )

    statements = []

    def record(conn, cursor, stmt, *args):
        statements.append(stmt)

    event.listen(db.bind, "before_cursor_execute", record)
    try:
        resp = client.get("/api/activities?limit=20")
    finally:
        event.remove(db.bind, "before_cursor_execute", record)

    assert resp.status_code == 200, resp.text
    items = resp.json()
    assert len(items) == 15
    for item in items:
        assert item["effort_score"] is not None

    # One SELECT for the activities+metrics join, one for the #797 coach-lead
    # batch, plus a small constant for auth/session resolution -- never one per
    # activity. Asserting a small ceiling (not scaling with the 15 rows above)
    # is the N+1 proof: a per-row lookup would blow well past it.
    select_statements = [s for s in statements if s.strip().upper().startswith("SELECT")]
    assert len(select_statements) <= 6, (
        f"expected a small constant number of SELECTs, got {len(select_statements)}:\n"
        + "\n".join(select_statements)
    )
