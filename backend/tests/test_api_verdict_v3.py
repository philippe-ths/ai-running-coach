"""API Tests for split Coach Verdict V3 endpoints."""
import pytest
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient
from uuid import uuid4

from app.main import app
from app.schemas import (
    VerdictScorecardResponse,
    VerdictScorecardResponse,
    V3Headline, 
    V3ScorecardItem,
    StoryResponse,
    V3RunStory,
    LeverResponse,
    V3Lever,
    NextStepsResponse,
    V3NextSteps,
    QuestionResponse
)
from app.services.ai.context_pack import ContextPack

client = TestClient(app)

# --- Mock Data ---
ACTIVITY_ID = uuid4()

MOCK_CONTEXT = ContextPack(
    activity={
        "id": str(ACTIVITY_ID), 
        "name": "Test Run", 
        "start_time": "2023-01-01T00:00:00Z", 
        "type": "Run", 
        "distance_m": 5000, 
        "moving_time_s": 1500, 
        "elapsed_time_s": 1500, 
        "elevation_gain_m": 0,
        "avg_hr": 140,
        "avg_pace_s_per_km": 300,
        "avg_cadence": 170
    },
    athlete={"goal": "5k", "experience_level": "Beginner"},
    derived_metrics=[],
    flags=[],
    last_7_days={},
    check_in={},
    available_signals=["hr"],
    missing_signals=[],
    generated_at_iso="2023-01-01T00:00:00Z"
)

MOCK_SCORECARD = VerdictScorecardResponse(
    inputs_used_line="Found items",
    headline=V3Headline(sentence="Great job", status="green"),
    why_it_matters=["Fit", "Fresh"],
    scorecard=[
        V3ScorecardItem(item="Purpose match", rating="ok", reason="ok"),
        V3ScorecardItem(item="Control (smoothness)", rating="ok", reason="ok"),
        V3ScorecardItem(item="Aerobic value", rating="ok", reason="ok"),
        V3ScorecardItem(item="Mechanical quality", rating="ok", reason="ok"),
        V3ScorecardItem(item="Risk / recoverability", rating="ok", reason="ok"),
    ]
)

MOCK_STORY = StoryResponse(
    run_story=V3RunStory(start="Cold", middle="Warm", finish="Hot")
)

MOCK_LEVER = LeverResponse(
    lever=V3Lever(category="pacing", signal="HR", cause="High", fix="Slow", cue="\"Easy\"")
)

MOCK_NEXT = NextStepsResponse(
    next_steps=V3NextSteps(tomorrow="Rest", next_7_days="Easy")
)

MOCK_QUESTION = QuestionResponse(
    question_for_you="How?"
)

# --- Tests ---

@patch("app.api.verdict_v3.build_context_pack", return_value=MOCK_CONTEXT)
@patch("app.api.verdict_v3.get_db") # Mock dependency (though TestClient usually needs overrides)
def test_scorecard_endpoint(mock_get_db, mock_build_cp):
    # Mock generator
    with patch("app.api.verdict_v3.generate_scorecard", return_value=MOCK_SCORECARD):
        response = client.post(
            "/api/verdict/v3/scorecard", 
            json={"activity_id": str(ACTIVITY_ID)}
        )
        assert response.status_code == 200
        data = response.json()
        assert data["headline"]["status"] == "green"

@patch("app.api.verdict_v3.build_context_pack", return_value=MOCK_CONTEXT)
def test_story_endpoint(mock_build_cp):
    with patch("app.api.verdict_v3.generate_story", return_value=MOCK_STORY):
        response = client.post(
            "/api/verdict/v3/story", 
            json={"activity_id": str(ACTIVITY_ID)}
        )
        data = response.json()
        assert data["run_story"]["start"] == "Cold"

@patch("app.api.verdict_v3.build_context_pack", return_value=MOCK_CONTEXT)
def test_lever_endpoint_requires_scorecard(mock_build_cp):
    # Passing scorecard in body
    payload = {
        "activity_id": str(ACTIVITY_ID),
        "scorecard": MOCK_SCORECARD.model_dump()
    }
    
    with patch("app.api.verdict_v3.generate_lever", return_value=MOCK_LEVER) as mock_gen:
        response = client.post("/api/verdict/v3/lever", json=payload)
        
        assert response.status_code == 200
        data = response.json()
        assert data["lever"]["cue"] == "\"Easy\""
        
        # Verify mock called with correct objects
        args, _ = mock_gen.call_args
        # args[0] is context pack dict, args[1] is scorecard obj
        assert args[1].headline.status == "green"

@patch("app.api.verdict_v3.build_context_pack", return_value=MOCK_CONTEXT)
def test_next_steps_safety_endpoint(mock_build_cp):
    # Mock return status Red to trigger safety
    unsafe_scorecard = MOCK_SCORECARD.model_copy()
    unsafe_scorecard.headline.status = "red"
    
    # Mock generator returns "Hard Run" (unsafe)
    unsafe_response = MOCK_NEXT.model_copy(deep=True)
    unsafe_response.next_steps.tomorrow = "Hard Run"

    payload = {
        "activity_id": str(ACTIVITY_ID),
        "scorecard": unsafe_scorecard.model_dump(),
        "lever": MOCK_LEVER.model_dump()
    }

    with patch("app.api.verdict_v3.generate_next_steps", return_value=unsafe_response):
        response = client.post("/api/verdict/v3/next-steps", json=payload)
        
        assert response.status_code == 200
        data = response.json()
        # Safety enforcement should have run in the endpoint
        assert "Rest" in data["next_steps"]["tomorrow"] or "recovery" in data["next_steps"]["tomorrow"]

@patch("app.api.verdict_v3.build_context_pack", return_value=MOCK_CONTEXT)
def test_full_generate_orchestrator(mock_build_cp):
    """Verifies that all steps run in order and return merged object."""
    
    with patch("app.api.verdict_v3.generate_scorecard", return_value=MOCK_SCORECARD) as m_sc, \
         patch("app.api.verdict_v3.generate_story", return_value=MOCK_STORY) as m_st, \
         patch("app.api.verdict_v3.generate_lever", return_value=MOCK_LEVER) as m_lv, \
         patch("app.api.verdict_v3.generate_next_steps", return_value=MOCK_NEXT) as m_ns, \
         patch("app.api.verdict_v3.generate_question", return_value=MOCK_QUESTION) as m_q:
         
        response = client.post(
            "/api/verdict/v3/generate", 
            json={"activity_id": str(ACTIVITY_ID)}
        )
        
        assert response.status_code == 200
        data = response.json()
        
        # Check structure matches composite V3
        assert "headline" in data
        assert "run_story" in data
        assert "lever" in data
        assert "next_steps" in data
        assert "question_for_you" in data
        
        # Verify calls happened
        assert m_sc.called
        assert m_st.called
        assert m_lv.called
        assert m_ns.called
        assert m_q.called
