import pytest
import time
from unittest.mock import patch, AsyncMock
from app.services.strava.client import StravaClient
from app.models import StravaAccount

@pytest.mark.asyncio
async def test_ensure_valid_token_refreshes_when_expired():
    # Setup
    client = StravaClient()
    mock_db = AsyncMock() # Mock the DB session
    
    # Create an expired account (expired 1 hour ago)
    expired_time = int(time.time()) - 3600
    account = StravaAccount(
        access_token="old_access",
        refresh_token="old_refresh",
        expires_at=expired_time
    )

    # Mock success response from Strava
    new_token_data = {
        "access_token": "new_access",
        "refresh_token": "new_refresh",
        "expires_at": int(time.time()) + 21600 # +6 hours
    }

    # Mock httpx.AsyncClient.post
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        # Action
        # We need to construct the mock tree carefully because AsyncMock is aggressive.
        
        mock_response = AsyncMock()
        mock_response.status_code = 200
        # This makes .json() NOT async
        mock_response.json = lambda: new_token_data
        # This makes .raise_for_status() NOT async/awaitable
        mock_response.raise_for_status = lambda: None
        
        mock_post.return_value = mock_response

        valid_token = await client.ensure_valid_token(mock_db, account)

        # Assert
        assert valid_token == "new_access"
        assert account.access_token == "new_access"
        assert account.refresh_token == "new_refresh"
        assert mock_post.called
        assert mock_post.call_args[1]['data']['grant_type'] == 'refresh_token'

@pytest.mark.asyncio
async def test_ensure_valid_token_returns_existing_when_valid():
    # Setup
    client = StravaClient()
    mock_db = AsyncMock()
    
    # Valid for another hour
    future_time = int(time.time()) + 3600
    account = StravaAccount(
        access_token="current_access",
        refresh_token="current_refresh",
        expires_at=future_time
    )

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        # Action
        valid_token = await client.ensure_valid_token(mock_db, account)

        # Assert
        assert valid_token == "current_access"
        assert not mock_post.called
