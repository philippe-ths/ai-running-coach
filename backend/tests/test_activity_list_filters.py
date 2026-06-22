"""All Activities list supports server-side type and date-range filters (#404).

Filtering must apply to the whole history (before pagination) and compose, so these
tests assert against the /api/activities endpoint with combinations of params.
"""

import uuid
from datetime import datetime, timezone

import pytest

from app.models import Activity, User


def _make_activity(
    db,
    *,
    name: str,
    type_: str,
    start: datetime,
    start_local: datetime | None = None,
    is_deleted: bool = False,
) -> Activity:
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
        start_date_local=start_local,
        distance_m=10000,
        moving_time_s=3600,
        elapsed_time_s=3700,
        elev_gain_m=10.0,
        is_deleted=is_deleted,
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


def test_soft_deleted_excluded_from_list(client, db):
    """A soft-deleted activity must not appear in the All Activities list (#410),
    consistent with every other read path that filters is_deleted."""
    _make_activity(
        db, name="Live Run", type_="Run",
        start=datetime(2026, 5, 1, 9, 0, tzinfo=timezone.utc),
    )
    _make_activity(
        db, name="Deleted Run", type_="Run",
        start=datetime(2026, 5, 2, 9, 0, tzinfo=timezone.utc),
        is_deleted=True,
    )
    resp = client.get("/api/activities")
    assert resp.status_code == 200, resp.text
    assert _names(resp) == {"Live Run"}


def test_soft_deleted_excluded_with_type_filter(client, db):
    """The type filter must not re-admit a soft-deleted activity (#410)."""
    _make_activity(
        db, name="Deleted Run", type_="Run",
        start=datetime(2026, 5, 2, 9, 0, tzinfo=timezone.utc),
        is_deleted=True,
    )
    resp = client.get("/api/activities?types=Run")
    assert resp.status_code == 200, resp.text
    assert resp.json() == []


def test_detail_view_404s_for_soft_deleted(client, db):
    """The single-activity detail view reads a soft-deleted activity as
    not-present and 404s, consistent with the list (#410)."""
    a = _make_activity(
        db, name="Deleted Run", type_="Run",
        start=datetime(2026, 5, 2, 9, 0, tzinfo=timezone.utc),
        is_deleted=True,
    )
    resp = client.get(f"/api/activities/{a.id}")
    assert resp.status_code == 404, resp.text


@pytest.fixture
def near_midnight(db):
    """A run whose LOCAL day differs from its UTC day (#411).

    Runner in a UTC-5 zone runs at 23:30 local on May 5; the UTC instant is
    therefore May 6 04:30. The list displays this row as May 5 (the local day), so a
    date filter must agree with May 5, not the UTC May 6.
    """
    _make_activity(
        db,
        name="Late Run",
        type_="Run",
        start=datetime(2026, 5, 6, 4, 30, tzinfo=timezone.utc),
        start_local=datetime(2026, 5, 5, 23, 30),
    )
    return db


def test_end_date_matches_displayed_local_day(client, near_midnight):
    # end_date = the LOCAL day (May 5). The UTC instant is May 6, so a UTC filter would
    # wrongly exclude it; the local-day filter must include it.
    resp = client.get("/api/activities?end_date=2026-05-05")
    assert resp.status_code == 200, resp.text
    assert _names(resp) == {"Late Run"}


def test_start_date_excludes_on_utc_day_when_local_is_earlier(client, near_midnight):
    # start_date = the UTC day (May 6). The row's displayed (local) day is May 5, so it
    # falls BEFORE this lower bound and must be excluded.
    resp = client.get("/api/activities?start_date=2026-05-06")
    assert resp.status_code == 200, resp.text
    assert _names(resp) == set()


def test_local_day_range_includes_near_midnight_row(client, near_midnight):
    # A range whose end lands exactly on the displayed local day includes it.
    resp = client.get("/api/activities?start_date=2026-05-01&end_date=2026-05-05")
    assert resp.status_code == 200, resp.text
    assert _names(resp) == {"Late Run"}


def test_filter_falls_back_to_utc_when_no_local_start(client, db):
    # Pre-#399 rows have no start_date_local; the filter falls back to the UTC instant
    # (mirroring Activity.local_start), so a same-day bound still matches.
    _make_activity(
        db,
        name="Legacy Run",
        type_="Run",
        start=datetime(2026, 7, 4, 9, 0, tzinfo=timezone.utc),
        start_local=None,
    )
    resp = client.get("/api/activities?start_date=2026-07-04&end_date=2026-07-04")
    assert resp.status_code == 200, resp.text
    assert _names(resp) == {"Legacy Run"}
