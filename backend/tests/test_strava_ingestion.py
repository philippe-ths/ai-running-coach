import httpx
import pytest

from app.models import Activity, ActivityStream, StravaAccount, User, UserProfile
from app.services.strava_ingestion import (
    InMemoryStravaAdapter,
    ingest_activity_by_id,
    ingest_recent_activities,
    refetch_streams,
)
from app.services.strava_ingestion.port import Tokens

# Real-shaped Strava /athlete/zones payload (this runner's actual zones, #297).
_STRAVA_ZONES = {
    "heart_rate": {
        "custom_zones": False,
        "zones": [
            {"min": 0, "max": 124},
            {"min": 125, "max": 154},
            {"min": 155, "max": 169},
            {"min": 170, "max": 184},
            {"min": 185, "max": -1},
        ],
    },
}


def _make_profile(db, user_id) -> UserProfile:
    profile = UserProfile(
        user_id=user_id,
        goal_type="general",
        experience_level="intermediate",
        weekly_days_available=4,
    )
    db.add(profile)
    db.commit()
    return profile


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
async def test_ingest_recent_summary_only_skips_stream_calls(db):
    """fetch_streams=False upserts summaries without any per-activity stream call.

    This is the rate-limit-safe backfill path: the activity rows land (so the
    list and distance/time trend charts populate) but no stream rows are written
    and no stream call is made. See #109.
    """
    account = _make_account(db, athlete_id=22222)
    adapter = InMemoryStravaAdapter()
    adapter.seed_activities([_raw_activity(801, "Backfilled"), _raw_activity(802, "Backfilled 2")])
    adapter.seed_streams(801, {"time": {"data": [0, 1, 2]}})

    ingested, stats = await ingest_recent_activities(
        db, account, adapter, fetch_streams=False
    )

    assert stats.upserted == 2
    assert {a.strava_activity_id for a in ingested} == {801, 802}
    assert adapter.stream_calls == []
    assert db.query(ActivityStream).count() == 0


@pytest.mark.asyncio
async def test_ingest_recent_default_fetches_streams(db):
    """Regression: the default path still fetches streams for each activity."""
    account = _make_account(db, athlete_id=33333)
    adapter = InMemoryStravaAdapter()
    adapter.seed_activities([_raw_activity(901, "Routine")])
    adapter.seed_streams(901, {"time": {"data": [0, 1, 2]}, "heartrate": {"data": [120, 130, 140]}})

    ingested, stats = await ingest_recent_activities(db, account, adapter)

    assert stats.upserted == 1
    assert len(adapter.stream_calls) == 1
    assert adapter.stream_calls[0][0] == 901
    stored = {
        s.stream_type
        for s in db.query(ActivityStream)
        .filter(ActivityStream.activity_id == ingested[0].id)
        .all()
    }
    assert stored == {"time", "heartrate"}


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


@pytest.mark.asyncio
async def test_sync_stores_runner_strava_hr_zones_on_profile(db):
    """#297: a sync pulls the runner's Strava HR zones and stores their lower
    bounds on the profile, so analysis can bin time-in-zone against them."""
    account = _make_account(db, athlete_id=44444)
    profile = _make_profile(db, account.user_id)
    adapter = InMemoryStravaAdapter()
    adapter.seed_activities([_raw_activity(1001, "Run")])
    adapter.seed_streams(1001, {"time": {"data": [0, 1, 2]}})
    adapter.seed_athlete_zones(_STRAVA_ZONES)

    await ingest_recent_activities(db, account, adapter)

    db.refresh(profile)
    assert profile.hr_zones == [0, 125, 155, 170, 185]
    assert profile.hr_zones_source == "strava"


@pytest.mark.asyncio
async def test_sync_without_zones_leaves_profile_unchanged_and_succeeds(db):
    """When Strava returns no usable zones, the sync still succeeds and the
    profile's zones stay null so analysis falls back to the %max scheme."""
    account = _make_account(db, athlete_id=55555)
    profile = _make_profile(db, account.user_id)
    adapter = InMemoryStravaAdapter()  # athlete_zones defaults to None
    adapter.seed_activities([_raw_activity(1101, "Run")])
    adapter.seed_streams(1101, {"time": {"data": [0, 1, 2]}})

    ingested, stats = await ingest_recent_activities(db, account, adapter)

    assert stats.upserted == 1
    db.refresh(profile)
    assert profile.hr_zones is None
    assert profile.hr_zones_source is None


def _make_401_error(url: str = "https://www.strava.com/api/v3/athlete/activities"):
    request = httpx.Request("GET", url)
    response = httpx.Response(401, request=request)
    return httpx.HTTPStatusError("401 Unauthorized", request=request, response=response)


class TestIngestRetryOn401:
    """A mid-flight 401 triggers a forced token refresh and a single retry."""

    @pytest.mark.asyncio
    async def test_list_activities_retries_after_401(self, db):
        account = _make_account(db, athlete_id=60001)

        class _Once401Adapter(InMemoryStravaAdapter):
            _calls = 0

            async def list_recent_activities(self, access_token, since, per_page=50):
                self._calls += 1
                if self._calls == 1:
                    raise _make_401_error()
                return await super().list_recent_activities(access_token, since, per_page)

        adapter = _Once401Adapter()
        adapter.seed_activities([_raw_activity(600, "Post-401 Run")])
        adapter.seed_refresh_response(
            Tokens(access_token="fresh_token", refresh_token="new_refresh", expires_at=9999999999)
        )

        ingested, stats = await ingest_recent_activities(
            db, account, adapter, fetch_streams=False
        )

        assert stats.upserted == 1
        assert ingested[0].strava_activity_id == 600
        assert adapter.refresh_calls == ["fake_refresh"]
        assert adapter._calls == 2

    @pytest.mark.asyncio
    async def test_get_activity_retries_after_401(self, db):
        account = _make_account(db, athlete_id=60002)

        class _Once401Adapter(InMemoryStravaAdapter):
            _calls = 0

            async def get_activity(self, access_token, activity_id):
                self._calls += 1
                if self._calls == 1:
                    raise _make_401_error(
                        f"https://www.strava.com/api/v3/activities/{activity_id}"
                    )
                return await super().get_activity(access_token, activity_id)

        adapter = _Once401Adapter()
        adapter.seed_activities([_raw_activity(700, "Post-401 Single")])
        adapter.seed_refresh_response(
            Tokens(access_token="fresh_token", refresh_token="new_refresh", expires_at=9999999999)
        )

        activity = await ingest_activity_by_id(db, account, adapter, 700)

        assert activity.strava_activity_id == 700
        assert adapter.refresh_calls == ["fake_refresh"]
        assert adapter._calls == 2
