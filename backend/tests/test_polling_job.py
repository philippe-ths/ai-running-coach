"""Polling job: discovers new activities via Strava and enqueues the pipeline."""

from datetime import datetime
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from app.jobs.polling import poll_for_new_activities
from app.models import Activity, StravaAccount, User
from app.services.strava_ingestion import InMemoryStravaAdapter


@pytest.fixture
def fake_queue():
    fake = MagicMock()
    with patch("app.jobs.polling.queue", fake):
        yield fake


def _seed_user_and_account(db, athlete_id: int = 555):
    user = User(email=f"u-{uuid4()}@example.com")
    db.add(user)
    db.commit()
    account = StravaAccount(
        user_id=user.id,
        strava_athlete_id=athlete_id,
        access_token="t",
        refresh_token="r",
        expires_at=9999999999,
        scope="read,activity:read_all",
    )
    db.add(account)
    db.commit()
    return user, account


@pytest.mark.asyncio
async def test_polling_enqueues_pipeline_for_each_new_activity(db, fake_queue):
    user, account = _seed_user_and_account(db)

    # Activity 1000 is already in DB (known); 1001 is new.
    existing = Activity(
        user_id=user.id,
        strava_activity_id=1000,
        start_date=datetime(2026, 5, 26, 10, 0, 0),
        type="Run",
        name="Yesterday",
        distance_m=5000,
        moving_time_s=1500,
        elapsed_time_s=1500,
        elev_gain_m=10,
    )
    db.add(existing)
    db.commit()

    adapter = InMemoryStravaAdapter()
    adapter.seed_activities(
        [
            {
                "id": 1000,
                "name": "Yesterday",
                "type": "Run",
                "start_date": "2026-05-26T10:00:00Z",
                "distance": 5000,
                "moving_time": 1500,
                "elapsed_time": 1500,
                "total_elevation_gain": 10,
            },
            {
                "id": 1001,
                "name": "Fresh run",
                "type": "Run",
                "start_date": "2026-05-27T10:00:00Z",
                "distance": 6000,
                "moving_time": 1800,
                "elapsed_time": 1800,
                "total_elevation_gain": 12,
            },
        ]
    )

    enqueued = await poll_for_new_activities(db=db, strava_port=adapter)

    assert enqueued == [1001]
    fake_queue.enqueue.assert_called_once()
    args, kwargs = fake_queue.enqueue.call_args
    from app.jobs.process_new_activity import process_new_activity_job

    assert args[0] is process_new_activity_job
    assert kwargs["strava_athlete_id"] == 555
    assert kwargs["strava_activity_id"] == 1001


@pytest.mark.asyncio
async def test_polling_with_no_new_activities_enqueues_nothing(db, fake_queue):
    user, _account = _seed_user_and_account(db)
    existing = Activity(
        user_id=user.id,
        strava_activity_id=1000,
        start_date=datetime(2026, 5, 26, 10, 0, 0),
        type="Run",
        name="Yesterday",
        distance_m=5000,
        moving_time_s=1500,
        elapsed_time_s=1500,
        elev_gain_m=10,
    )
    db.add(existing)
    db.commit()

    adapter = InMemoryStravaAdapter()
    adapter.seed_activities([
        {
            "id": 1000,
            "name": "Yesterday",
            "type": "Run",
            "start_date": "2026-05-26T10:00:00Z",
            "distance": 5000,
            "moving_time": 1500,
            "elapsed_time": 1500,
            "total_elevation_gain": 10,
        }
    ])

    enqueued = await poll_for_new_activities(db=db, strava_port=adapter)
    assert enqueued == []
    fake_queue.enqueue.assert_not_called()


@pytest.mark.asyncio
async def test_polling_with_no_accounts_is_a_noop(db, fake_queue):
    adapter = InMemoryStravaAdapter()
    enqueued = await poll_for_new_activities(db=db, strava_port=adapter)
    assert enqueued == []
    fake_queue.enqueue.assert_not_called()
