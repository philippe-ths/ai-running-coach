"""The activity intent endpoint accepts an explicit type and a null clear (#137)."""

import uuid
from datetime import datetime, timezone

from app.models import Activity, User


def _make_activity(db) -> Activity:
    user_id = uuid.uuid4()
    db.add(User(id=user_id, email=f"intent_{user_id}@example.com"))
    db.flush()
    a = Activity(
        id=uuid.uuid4(),
        user_id=user_id,
        strava_activity_id=abs(hash(str(uuid.uuid4()))) % 10**9,
        name="Afternoon Run",
        type="Run",
        start_date=datetime(2026, 6, 2, 14, 57, tzinfo=timezone.utc),
        distance_m=3660,
        moving_time_s=1200,
        elapsed_time_s=1300,
        elev_gain_m=20.0,
        avg_hr=166.0,
        max_hr=190.0,
    )
    db.add(a)
    db.commit()
    return a


def test_intent_sets_explicit_type(client, db):
    a = _make_activity(db)
    resp = client.put(f"/api/activities/{a.id}/intent", json={"user_intent": "Tempo"})
    assert resp.status_code == 200, resp.text
    db.refresh(a)
    assert a.user_intent == "Tempo"


def test_intent_null_clears_stated_intent(client, db):
    a = _make_activity(db)
    client.put(f"/api/activities/{a.id}/intent", json={"user_intent": "Easy Run"})
    db.refresh(a)
    assert a.user_intent == "Easy Run"

    # Submitting an unset selection clears it rather than recording a type.
    resp = client.put(f"/api/activities/{a.id}/intent", json={"user_intent": None})
    assert resp.status_code == 200, resp.text
    db.refresh(a)
    assert a.user_intent is None


def test_intent_omitted_is_treated_as_unset(client, db):
    a = _make_activity(db)
    resp = client.put(f"/api/activities/{a.id}/intent", json={})
    assert resp.status_code == 200, resp.text
    db.refresh(a)
    assert a.user_intent is None
