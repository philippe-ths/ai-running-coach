import pytest
from app.services.coaching.v3.slicers import (
    slice_for_scorecard, 
    slice_for_story, 
    slice_for_lever, 
    slice_for_next_steps, 
    slice_for_question
)
from app.schemas import (
    VerdictScorecardResponse, 
    LeverResponse, 
    V3Headline, 
    V3ScorecardItem, 
    V3Lever
)

@pytest.fixture
def sample_context_pack():
    return {
        "activity": {
            "id": "123",
            "type": "Run",
            "name": "Test Run",
            "moving_time_s": 3600,
            "distance_m": 10000,
            "avg_hr": 150,
            "avg_pace_s_per_km": 360,
            "elevation_gain_m": 50,
            "start_time": "2023-01-01T10:00:00Z"
        },
        "athlete": {
            "goal": "Marathon",
            "experience_level": "Advanced"
        },
        "derived_metrics": [
            {"key": "efficiency", "value": 1.2, "evidence": "High efficiency"},
            {"key": "hr_drift", "value": 2.5, "evidence": "Low drift"}
        ],
        "flags": [
            {"code": "high_hr", "severity": "warn", "message": "HR high", "evidence": ">180bpm"}
        ],
        "check_in": {
            "rpe_0_10": 7,
            "notes": "Felt good",
            "pain_score": 2
        },
        "last_7_days": {
            "total_distance_m": 50000
        },
        "available_signals": ["hr", "gps"],
        "missing_signals": ["power"]
    }

@pytest.fixture
def mock_scorecard_response():
    return VerdictScorecardResponse(
        inputs_used_line="All good",
        headline=V3Headline(sentence="Great job", status="green"),
        why_it_matters=["Fit", "Fresh"],
        scorecard=[
            V3ScorecardItem(item="Purpose match", rating="ok", reason="Hit targets"),
            V3ScorecardItem(item="Control (smoothness)", rating="warn", reason="Jerky")
        ]
    )

@pytest.fixture
def mock_lever_response():
    return LeverResponse(lever=V3Lever(
        category="mechanics",
        signal="Cadence",
        cause="Low turnover",
        fix="Quick steps",
        cue="\"Pop pop\""
    ))

def test_slice_for_scorecard(sample_context_pack):
    result = slice_for_scorecard(sample_context_pack)
    
    assert "activity_summary" in result
    assert result["activity_summary"]["distance_m"] == 10000
    # Ensure raw ID is removed (privacy/token saving)
    assert "id" not in result["activity_summary"]
    
    assert "derived_metrics" in result
    assert len(result["derived_metrics"]) == 2
    
    assert "available_signals" in result
    assert "missing_signals" in result

def test_slice_for_story(sample_context_pack):
    result = slice_for_story(sample_context_pack)
    
    assert "subjective" in result
    assert result["subjective"]["notes"] == "Felt good"
    
    assert "key_events_and_data" in result
    evidence = result["key_events_and_data"]
    # check that we formatted the strings
    assert any("High efficiency" in e for e in evidence)
    assert any("HR high" in e for e in evidence)
    
    # Raw metrics list shouldn't be there, we converted to text
    assert "derived_metrics" not in result

def test_slice_for_lever(sample_context_pack, mock_scorecard_response):
    result = slice_for_lever(sample_context_pack, mock_scorecard_response)
    
    assert "scorecard_weaknesses" in result
    weaknesses = result["scorecard_weaknesses"]
    # Should only contain the 'warn' item, not the 'ok' one
    assert len(weaknesses) == 1
    assert weaknesses[0]["item"] == "Control (smoothness)"
    
    assert "diagnostics" in result
    assert "athlete_level" in result

def test_slice_for_next_steps(sample_context_pack, mock_scorecard_response, mock_lever_response):
    result = slice_for_next_steps(sample_context_pack, mock_scorecard_response, mock_lever_response)
    
    assert result["current_status"]["verdict"] == "green"
    assert result["prescribed_focus"] == "mechanics"
    assert result["recent_load"]["total_distance_m"] == 50000

def test_slice_for_question(sample_context_pack, mock_scorecard_response):
    result = slice_for_question(sample_context_pack, mock_scorecard_response)
    
    assert result["verdict_theme"] == "Great job"
    assert result["user_notes"] == "Felt good"
    # Should contain the warn item key
    assert "Control (smoothness)" in result["pain_points"]
