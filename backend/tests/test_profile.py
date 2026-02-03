from fastapi.testclient import TestClient
from sqlalchemy import select
from app.models import UserProfile

def test_get_and_update_profile(client: TestClient, db):
    # 1. Get initial (auto-created) profile
    response = client.get("/api/profile")
    assert response.status_code == 200
    data = response.json()
    assert "goal_type" in data
    
    # 2. Update profile
    updated_payload = {
        "goal_type": "marathon",
        "experience_level": "advanced",
        "weekly_days_available": 5,
        "current_weekly_km": 60,
        "upcoming_races": [{"name": "Boston 2026", "date": "2026-04-20", "distance_km": 42.2}],
        "injury_notes": "Left knee soreness"
    }
    
    response = client.put("/api/profile", json=updated_payload)
    assert response.status_code == 200
    res_data = response.json()
    assert res_data["goal_type"] == "marathon"
    assert res_data["current_weekly_km"] == 60
    assert len(res_data["upcoming_races"]) == 1
    assert res_data["upcoming_races"][0]["name"] == "Boston 2026"

    # 3. Verify persistence
    response = client.get("/api/profile")
    assert response.json()["injury_notes"] == "Left knee soreness"
