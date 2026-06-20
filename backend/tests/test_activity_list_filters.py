"""All Activities list supports server-side type and date-range filters (#404).

Filtering must apply to the whole history (before pagination) and compose, so these
tests assert against the /api/activities endpoint with combinations of params.
"""

import uuid
from datetime import datetime, timezone

import pytest

from app.models import Activity, User


def _make_activity(db, *, name: str, type_: str, start: datetime) -> Activity:
    user_id = uuid.uuid4()
    db.add(User(id=user_id, email=f"filt_{user_id}@example.com"))
    db.flush()
    a = Activity(
        id=uuid.uuid4(),
        user_id=user_id,
        strava_activity_id=abs(hash(str(uuid.uuid4()))) % 10**9,
        name=name,
        type=type_,
        start_date=start,
        distance_m=10000,
        moving_time_s=3600,
        elapsed_time_s=3700,
        elev_gain_m=10.0,
    )
    db.add(a)
    db.commit()
    return a


@pytest.fixture
def seeded(db):
    """A small mixed history across types and dates."""
    _make_activity(db, name="Run Jan", type_="Run", start=datetime(2026, 1, 10, 9, 0, tzinfo=timezone.utc))
    _make_activity(db, name="Walk Feb", type_="Walk", start=datetime(2026, 2, 15, 9, 0, tzinfo=timezone.utc))
    _make_activity(db, name="Ride Mar", type_="Ride", start=datetime(2026, 3, 20, 9, 0, tzinfo=timezone.utc))
    _make_activity(db, name="Run Apr", type_="Run", start=datetime(2026, 4, 5, 9, 0, tzinfo=timezone.utc))
    return db


def _names(resp):
    return {item["name"] for item in resp.json()}


def test_no_filter_returns_all(client, seeded):
    resp = client.get("/api/activities")
    assert resp.status_code == 200, resp.text
    assert _names(resp) == {"Run Jan", "Walk Feb", "Ride Mar", "Run Apr"}


def test_single_type_filter(client, seeded):
    resp = client.get("/api/activities?types=Run")
    assert resp.status_code == 200, resp.text
    assert _names(resp) == {"Run Jan", "Run Apr"}


def test_multi_type_filter_is_union(client, seeded):
    resp = client.get("/api/activities?types=Run&types=Ride")
    assert resp.status_code == 200, resp.text
    assert _names(resp) == {"Run Jan", "Ride Mar", "Run Apr"}


def test_date_range_is_inclusive_both_ends(client, seeded):
    # Bounds land exactly on the Feb and Mar activity dates; both must be included.
    resp = client.get("/api/activities?start_date=2026-02-15&end_date=2026-03-20")
    assert resp.status_code == 200, resp.text
    assert _names(resp) == {"Walk Feb", "Ride Mar"}


def test_start_date_only(client, seeded):
    resp = client.get("/api/activities?start_date=2026-03-01")
    assert resp.status_code == 200, resp.text
    assert _names(resp) == {"Ride Mar", "Run Apr"}


def test_end_date_only(client, seeded):
    resp = client.get("/api/activities?end_date=2026-02-15")
    assert resp.status_code == 200, resp.text
    assert _names(resp) == {"Run Jan", "Walk Feb"}


def test_type_and_date_compose(client, seeded):
    # Runs, but only from March onward -> excludes the January run.
    resp = client.get("/api/activities?types=Run&start_date=2026-03-01")
    assert resp.status_code == 200, resp.text
    assert _names(resp) == {"Run Apr"}


def test_empty_result_when_nothing_matches(client, seeded):
    resp = client.get("/api/activities?types=Swim")
    assert resp.status_code == 200, resp.text
    assert resp.json() == []
