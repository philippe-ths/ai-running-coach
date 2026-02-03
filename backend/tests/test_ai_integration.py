from unittest.mock import patch, MagicMock
from app.services.coaching import engine
from app.core.config import settings
from app.models import Activity, DerivedMetric, UserProfile, User
from sqlalchemy.orm import Session
from datetime import datetime
import pytest

def test_advice_generation_calls_ai_when_enabled(db: Session):
    # 1. Setup Data
    user = User(email="test_ai@example.com")
    db.add(user)
    db.commit()

    act = Activity(
        name="Test Run", 
        distance_m=5000, 
        moving_time_s=1500, 
        elapsed_time_s=1500,
        type="Run", 
        user_id=user.id, 
        start_date=datetime.strptime("2024-01-01T10:00:00Z", "%Y-%m-%dT%H:%M:%SZ"),
        strava_activity_id=123456789
    )
    db.add(act)
    db.commit()
    
    metrics = DerivedMetric(activity_id=act.id, activity_class="Easy Run", effort_score=10.0, flags=[], confidence="high")
    db.add(metrics)
    db.commit()

    # 2. Mock AI Client and Config
    with patch("app.services.coaching.engine.settings.AI_ENABLED", True):
        # We patch the V3 orchestrator function imported in engine.py
        with patch("app.services.coaching.engine.generate_full_verdict_orchestrator") as mock_gen:
            from app.schemas import CoachVerdictV3, V3ScorecardItem, V3Headline, V3RunStory, V3Lever, V3NextSteps
            
            # Construct a minimal valid CoachVerdictV3
            mock_verdict = CoachVerdictV3(
                inputs_used_line="Inputs used",
                headline=V3Headline(sentence="AI Generated Test Verdict", status="green"),
                why_it_matters=["Reason 1", "Reason 2"],
                scorecard=[
                    V3ScorecardItem(item="Purpose match", rating="ok", reason="reason")
                ],
                run_story=V3RunStory(start="s", middle="m", finish="f"),
                lever=V3Lever(category="pacing", signal="Sig", cause="C", fix="F", cue='"Cue"'),
                next_steps=V3NextSteps(tomorrow="Rest", next_7_days="Easy"),
                question_for_you="Q?"
            )
            
            mock_gen.return_value = mock_verdict
            
            # 3. Execute
            advice = engine.generate_and_save_advice(db, str(act.id))
            
            # 4. Verify
            assert advice is not None
            # The engine extracts 'headline.sentence' as 'verdict'
            assert advice.verdict == "AI Generated Test Verdict"
            assert advice.structured_report["headline"]["sentence"] == "AI Generated Test Verdict"
            
            # Ensure full_text was rebuilt including the new AI verdict
            assert "**Verdict**: AI Generated Test Verdict" in advice.full_text
            
            mock_gen.assert_called_once()
            
            # Verify context pack dict was passed
            call_args = mock_gen.call_args[0][0]
            assert isinstance(call_args, dict)
            assert "activity" in call_args

