"""The activity list exposes the classification headline for analysed activities (#136)."""

import uuid
from datetime import datetime, timezone

from app.models import Activity, DerivedMetric, User


def _make_activity(db, *, name: str, with_metrics: bool) -> Activity:
    user_id = uuid.uuid4()
    db.add(User(id=user_id, email=f"list_{user_id}@example.com"))
    db.flush()
    a = Activity(
        id=uuid.uuid4(),
        user_id=user_id,
        strava_activity_id=abs(hash(str(uuid.uuid4()))) % 10**9,
        name=name,
        type="Run",
        start_date=datetime(2026, 6, 2, 14, 57, tzinfo=timezone.utc),
        distance_m=12500,
        moving_time_s=4200,
        elapsed_time_s=4300,
        elev_gain_m=20.0,
        avg_hr=150.0,
        max_hr=190.0,
    )
    db.add(a)
    db.flush()
    if with_metrics:
        db.add(
            DerivedMetric(
                activity_id=a.id,
                effort="easy",
                duration_class="long",
                structure="continuous",
                is_hilly=False,
                is_race=False,
                effort_score=50.0,
                confidence="high",
            )
        )
    db.commit()
    return a


def test_list_includes_headline_for_analysed_activity(client, db):
    _make_activity(db, name="Analysed Run", with_metrics=True)
    resp = client.get("/api/activities")
    assert resp.status_code == 200, resp.text
    items = resp.json()
    analysed = next(i for i in items if i["name"] == "Analysed Run")
    assert analysed["headline"] == "Long run"


def test_list_headline_is_null_without_metrics(client, db):
    _make_activity(db, name="Unanalysed Run", with_metrics=False)
    resp = client.get("/api/activities")
    assert resp.status_code == 200, resp.text
    items = resp.json()
    unanalysed = next(i for i in items if i["name"] == "Unanalysed Run")
    assert unanalysed["headline"] is None
