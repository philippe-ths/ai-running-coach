import pytest

from app.models import Activity, ActivityStream, StravaAccount, User
from app.services.strava_ingestion import (
    InMemoryStravaAdapter,
    ingest_activity_by_id,
    refetch_streams,
)


def _make_account(db, athlete_id: int = 12345) -> StravaAccount:
    user = User(email=f"ingest_{athlete_id}@example.com")
    db.add(user)
    db.commit()
    account = StravaAccount(
        user_id=user.id,
        strava_athlete_id=athlete_id,
        access_token="valid_token",
        refresh_token="fake_refresh",
        expires_at=9999999999,
        scope="read,activity:read_all",
    )
    db.add(account)
    db.commit()
    return account


def _raw_activity(activity_id: int, name: str = "Run") -> dict:
    return {
        "id": activity_id,
        "name": name,
        "type": "Run",
        "start_date": "2024-02-01T10:00:00Z",
        "distance": 5000,
        "moving_time": 1500,
        "elapsed_time": 1500,
        "total_elevation_gain": 25,
        "average_heartrate": 145,
    }


@pytest.mark.asyncio
async def test_ingest_activity_by_id_upserts_single_activity_no_streams(db):
    account = _make_account(db)
    adapter = InMemoryStravaAdapter()
    adapter.seed_activities([_raw_activity(777, "Single Run")])
    adapter.seed_streams(777, {"time": {"data": [0, 1, 2]}})

    activity = await ingest_activity_by_id(db, account, adapter, 777)

    assert activity.strava_activity_id == 777
    assert activity.name == "Single Run"

    streams = (
        db.query(ActivityStream).filter(ActivityStream.activity_id == activity.id).all()
    )
    assert streams == []
    assert adapter.stream_calls == []
    assert adapter.activity_calls == [777]


@pytest.mark.asyncio
async def test_refetch_streams_replaces_existing_rows(db):
    account = _make_account(db)
    activity = Activity(
        user_id=account.user_id,
        strava_activity_id=42,
        name="Existing",
        type="Run",
        start_date=__import__("datetime").datetime(2024, 1, 1, 10, 0, 0),
        distance_m=4000,
        moving_time_s=1200,
        elapsed_time_s=1200,
        elev_gain_m=10,
        raw_summary={},
    )
    db.add(activity)
    db.commit()
    db.add(
        ActivityStream(
            activity_id=activity.id,
            stream_type="legacy",
            data=[1, 2, 3],
        )
    )
    db.commit()

    adapter = InMemoryStravaAdapter()
    adapter.seed_streams(
        42,
        {
            "time": {"data": [0, 1, 2]},
            "heartrate": {"data": [120, 130, 140]},
        },
    )

    refreshed = await refetch_streams(db, account, activity, adapter)

    assert refreshed is True
    stream_types = {
        s.stream_type
        for s in db.query(ActivityStream)
        .filter(ActivityStream.activity_id == activity.id)
        .all()
    }
    assert stream_types == {"time", "heartrate"}
