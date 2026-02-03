from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock
from sqlalchemy.orm import Session
from app.models import Activity, User, UserProfile, DerivedMetric, Advice
from datetime import datetime
import uuid

def test_chat_context_assembly(client: TestClient, db: Session):
    # 1. Setup Data
    user = User(email="chat_test@example.com")
    db.add(user)
    db.commit()
    
    profile = UserProfile(
        user_id=user.id, 
        goal_type="Marathon", 
        experience_level="Advanced", 
        weekly_days_available=5,
        target_date=None
    )
    db.add(profile)
    
    act = Activity(
        name="Long Run", 
        distance_m=20000, 
        moving_time_s=5400, 
        elapsed_time_s=5400,
        type="Run", 
        user_id=user.id,
        strava_activity_id=123123123,
        start_date=datetime.now()
    )
    db.add(act)
    db.commit()
    
    metric = DerivedMetric(activity_id=act.id, activity_class="Long Run", effort_score=50.0, flags=[], confidence="high")
    db.add(metric)
    
    advice = Advice(activity_id=act.id, verdict="Good job", full_text="...", next_run={}, evidence=[], week_adjustment="", warnings=[])
    db.add(advice)
    db.commit()

    # 2. Mock AI Client
    with patch("app.services.ai.client.ai_client.enabled", True):
        # We also need to patch the method on the class instance itself if it's singleton/imported
        with patch("app.services.ai.client.ai_client.generate_chat_response") as mock_chat:
            mock_chat.return_value = "Great long run!"
            
            # 3. Call Endpoint
            response = client.post("/api/chat", json={
                "message": "Should I run tomorrow?",
                "activity_id": str(act.id)
            })
            
            assert response.status_code == 200
            assert response.json()["reply"] == "Great long run!"
            
            # 4. Verify Context in Prompt
            # The prompt is passed as the first argument to generate_chat_response
            call_args = mock_chat.call_args[0][0]
            assert "Marathon" in call_args # Profile goal
            assert "Long Run" in call_args # Activity Name/Class
            assert "20.0km" in call_args   # Stats
            assert "Should I run tomorrow?" in call_args # User Q
