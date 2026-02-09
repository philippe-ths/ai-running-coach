import pytest
from unittest.mock import patch, MagicMock
from sqlalchemy.orm import Session
from app.api.activities import sync_activities
from app.models import Activity, StravaAccount, User
from app.schemas import SyncResponse

@pytest.mark.asyncio
async def test_integration_sync_upserts_and_runs_analysis(db: Session):
    """
    Verifies that calling the sync service:
    1. Fetches data from Strava (Mocked)
    2. Upserts Activity to DB
    3. Runs Analysis
    """
    # 1. Setup Data
    user = User(email="sync_test@example.com")
    db.add(user)
    db.commit()
    
    account = StravaAccount(
        user_id=user.id,
        strava_athlete_id=99999,
        access_token="fake_token",
        refresh_token="fake_refresh",
        expires_at=9999999999,
        scope="read,activity:read_all"
    )
    db.add(account)
    db.commit()

    # Mock Data
    mock_activity_payload = [
        {
            "id": 1001,
            "name": "Integration Run",
            "type": "Run",
            "start_date": "2024-01-01T10:00:00Z",
            "distance": 5000,
            "moving_time": 1500,
            "elapsed_time": 1500,
            "total_elevation_gain": 50,
            "average_heartrate": 150
        }
    ]

    with patch("app.services.activity_service.strava_client.ensure_valid_token", return_value="valid_token") as mock_auth:
        with patch("app.services.activity_service.strava_client.get_athlete_activities", return_value=mock_activity_payload) as mock_fetch:
            
            # 3. Execute
            result = await sync_activities(strava_athlete_id=99999, db=db)
            
            # 4. Verify Stats
            assert isinstance(result, SyncResponse)
            assert result.fetched == 1
            assert result.upserted == 1
            assert len(result.errors) == 0

            # 5. Verify Persistence
            activity = db.query(Activity).filter_by(strava_activity_id=1001).first()
            assert activity is not None
            assert activity.name == "Integration Run"
