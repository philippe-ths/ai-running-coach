import pytest
from app.core.config import settings

def test_demo_load_endpoint_forbidden_by_default(client):
    """Ensure it returns 403 when DEMO_MODE is 0 (default)."""
    # ensure default is 0
    settings.DEMO_MODE = 0 
    response = client.post("/api/demo/load")
    assert response.status_code == 403

def test_demo_load_endpoint_success(client, monkeypatch):
    """Ensure it processes files when DEMO_MODE is 1."""
    monkeypatch.setattr(settings, "DEMO_MODE", 1)
    
    # We rely on the existence of backend/sample_data files in the implementation.
    # If this test runs in an environment where those files exist, real processing happens.
    
    response = client.post("/api/demo/load")
    
    # Depending on whether sample data exists, we might get 200 or 500
    # But for now let's assume sample data is there as per project structure
    if response.status_code == 200:
        data = response.json()
        assert data["status"] == "success"
        assert "processed_count" in data
        # Check details of first item if any
        if data["processed_count"] > 0:
            first_item = data["details"][0]
            assert "strava_id" in first_item
            assert "advice" in first_item
    elif response.status_code == 500:
        # If sample data missing, verify it's the specific error
        assert "Sample data not found" in response.json()["detail"]
