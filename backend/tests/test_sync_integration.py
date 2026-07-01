from datetime import datetime, timedelta
from unittest.mock import patch

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


def _seed_account(db, athlete_id: int) -> StravaAccount:
    user = User(email=f"sync_{athlete_id}@example.com")
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


def _run_sync_capturing_args(client, db, athlete_id, query=""):
    """POST /api/sync through the real HTTP path (so Query defaults resolve),
    capturing the since/fetch_streams the endpoint hands to ingestion."""
    _seed_account(db, athlete_id=athlete_id)
    captured = {}

    async def _capture(db_, account_, port_, *, since, fetch_streams):
        captured["since"] = since
        captured["fetch_streams"] = fetch_streams
        return [], SyncResponse()

    with patch("app.api.activities.ingest_recent_activities", _capture):
        resp = client.post(f"/api/sync?strava_athlete_id={athlete_id}{query}")
    assert resp.status_code == 200, resp.text
    return captured


def test_sync_default_window_fetches_streams_over_30_days(client, db):
    """Default sync: 30-day window, streams fetched (routine deep processing)."""
    captured = _run_sync_capturing_args(client, db, athlete_id=70001)

    assert captured["fetch_streams"] is True
    expected = datetime.now() - timedelta(days=30)
    assert abs((captured["since"] - expected).total_seconds()) < 5


def test_sync_large_window_is_summary_only_backfill(client, db):
    """A window beyond 30 days imports summaries only (rate-limit-safe backfill)."""
    captured = _run_sync_capturing_args(client, db, athlete_id=70002, query="&since_days=3650")

    assert captured["fetch_streams"] is False
    expected = datetime.now() - timedelta(days=3650)
    assert abs((captured["since"] - expected).total_seconds()) < 5


def test_sync_returns_429_when_strava_rate_limited(client, db):
    """A Strava rate limit on the live sync path surfaces as HTTP 429 with the
    true Retry-After, not a 500 (#602)."""
    from app.services.strava_ingestion import StravaRateLimited

    _seed_account(db, athlete_id=70003)

    async def _rate_limited(db_, account_, port_, *, since, fetch_streams):
        raise StravaRateLimited(retry_after=42, label="list_recent_activities")

    with patch("app.api.activities.ingest_recent_activities", _rate_limited):
        resp = client.post("/api/sync?strava_athlete_id=70003")

    assert resp.status_code == 429, resp.text
    assert resp.headers.get("Retry-After") == "42"


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

    # Phase 2 (#473) made sync user-scoped (require_current_user) instead of
    # athlete-id-keyed: the endpoint resolves the account from the authenticated
    # user. since_days defaults to the routine 30-day stream-fetch window.
    result = await sync_activities(db=db, user=user)

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
