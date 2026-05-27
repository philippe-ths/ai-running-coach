import pytest

from app.api.activities import sync_activities
from app.models import Activity, ActivityStream, StravaAccount, User
from app.schemas import SyncResponse
from app.services.strava_ingestion import (
    InMemoryStravaAdapter,
    set_strava_port,
)


@pytest.fixture
def strava_adapter():
    adapter = InMemoryStravaAdapter()
    set_strava_port(adapter)
    yield adapter
    set_strava_port(None)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_sync_upserts_activity_and_streams_and_runs_analysis(
    db, strava_adapter
):
    user = User(email="sync_test@example.com")
    db.add(user)
    db.commit()

    account = StravaAccount(
        user_id=user.id,
        strava_athlete_id=99999,
        access_token="valid_token",
        refresh_token="fake_refresh",
        expires_at=9999999999,
        scope="read,activity:read_all",
    )
    db.add(account)
    db.commit()

    strava_adapter.seed_activities(
        [
            {
                "id": 1001,
                "name": "Integration Run",
                "type": "Run",
                "start_date": "2024-01-01T10:00:00Z",
                "distance": 5000,
                "moving_time": 1500,
                "elapsed_time": 1500,
                "total_elevation_gain": 50,
                "average_heartrate": 150,
            }
        ]
    )
    strava_adapter.seed_streams(
        1001,
        {
            "time": {"data": [0, 1, 2, 3]},
            "heartrate": {"data": [140, 150, 155, 160]},
        },
    )

    result = await sync_activities(strava_athlete_id=99999, db=db)

    assert isinstance(result, SyncResponse)
    assert result.fetched == 1
    assert result.upserted == 1

    activity = db.query(Activity).filter_by(strava_activity_id=1001).first()
    assert activity is not None
    assert activity.name == "Integration Run"

    streams = (
        db.query(ActivityStream).filter(ActivityStream.activity_id == activity.id).all()
    )
    stream_types = {s.stream_type for s in streams}
    assert stream_types == {"time", "heartrate"}
