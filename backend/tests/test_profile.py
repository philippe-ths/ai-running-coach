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
        "max_hr": 188,
        "resting_hr": 48,
        "upcoming_races": [{"name": "Boston 2026", "date": "2026-04-20", "distance_km": 42.2}],
        "injury_notes": "Left knee soreness"
    }

    response = client.put("/api/profile", json=updated_payload)
    assert response.status_code == 200
    res_data = response.json()
    assert res_data["goal_type"] == "marathon"
    assert res_data["current_weekly_km"] == 60
    assert res_data["resting_hr"] == 48
    assert len(res_data["upcoming_races"]) == 1
    assert res_data["upcoming_races"][0]["name"] == "Boston 2026"

    # 3. Verify persistence
    response = client.get("/api/profile")
    body = response.json()
    assert body["injury_notes"] == "Left knee soreness"
    assert body["resting_hr"] == 48


def test_body_metrics_round_trip(client: TestClient, db):
    """#742: the runner's build is captured on the profile, which is the channel that
    reaches the coach pack. Before this the only route was free-text injury_notes, and
    an A/B probe showed the coach could not act on it."""
    response = client.put("/api/profile", json={
        "goal_type": "general",
        "experience_level": "intermediate",
        "weekly_days_available": 4,
        "weight_kg": 109.4,
        "height_cm": 193.0,
    })

    assert response.status_code == 200
    assert response.json()["weight_kg"] == 109.4
    assert client.get("/api/profile").json()["height_cm"] == 193.0


def test_body_metrics_default_to_unstated(client: TestClient, db):
    # Not stated is not average: the field stays null so the coach pack drops the
    # signal rather than substituting a typical runner.
    body = client.get("/api/profile").json()

    assert body["weight_kg"] is None
    assert body["height_cm"] is None


def test_a_unit_slip_is_rejected_rather_than_coached_on(client: TestClient, db):
    """Pounds typed into a kg field reads as ~240 and inches into a cm field as ~74.
    Either would reach the coach as a FACT about the runner's build and skew every
    method judgement made from it, so the envelope rejects them at the edge."""
    base = {
        "goal_type": "general", "experience_level": "intermediate",
        "weekly_days_available": 4,
    }

    assert client.put("/api/profile", json={**base, "weight_kg": 240.0}).status_code == 200
    assert client.put("/api/profile", json={**base, "weight_kg": 400.0}).status_code == 422
    assert client.put("/api/profile", json={**base, "height_cm": 74.0}).status_code == 422
    assert client.put("/api/profile", json={**base, "height_cm": 1.87}).status_code == 422
