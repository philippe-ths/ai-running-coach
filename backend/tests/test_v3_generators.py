"""Tests for the split V3 generators and retry logic."""
import pytest
import json
from unittest.mock import MagicMock
from app.services.ai.verdict_v3.generators import (
    generate_scorecard, 
    generate_story, 
    VerdictV3GenerationError,
    ClientInterface
)
from app.schemas import VerdictScorecardResponse, StoryResponse

# --- Mock Data ---
SAMPLE_CONTEXT = {
    "activity": {"type": "Run", "id": "123"}, 
    "derived_metrics": [], 
    "flags": [],
    "available_signals": ["hr"],
    "missing_signals": []
}

SUCCESS_JSON = json.dumps({
    "inputs_used_line": "HR data",
    "headline": {"sentence": "Good job", "status": "green"},
    "why_it_matters": ["Fit", "Fresh"],
    "scorecard": [
        {"item": "Purpose match", "rating": "ok", "reason": "ok"},
        {"item": "Control (smoothness)", "rating": "ok", "reason": "ok"},
        {"item": "Aerobic value", "rating": "ok", "reason": "ok"},
        {"item": "Mechanical quality", "rating": "ok", "reason": "ok"},
        {"item": "Risk / recoverability", "rating": "ok", "reason": "ok"}
    ]
})

INVALID_JSON = '{"headline": "This is broken JSON...' # Missing brace

# --- Test Helpers ---
class FakeAIClient(ClientInterface):
    def __init__(self, responses):
        self.responses = responses # List of responses to pop
        self.log = []

    def get_raw_json_response(self, prompt: str) -> str:
        self.log.append(prompt)
        if not self.responses:
            return "{}"
        return self.responses.pop(0)

# --- Tests ---
def test_generate_scorecard_success():
    """Happy path: AI returns valid JSON first try."""
    client = FakeAIClient([SUCCESS_JSON])
    
    result = generate_scorecard(SAMPLE_CONTEXT, client)
    
    assert isinstance(result, VerdictScorecardResponse)
    assert result.headline.status == "green"
    assert len(client.log) == 1

def test_generate_retry_success():
    """Retry path: Invalid first, Valid second."""
    client = FakeAIClient([INVALID_JSON, SUCCESS_JSON])
    
    result = generate_scorecard(SAMPLE_CONTEXT, client)
    
    assert isinstance(result, VerdictScorecardResponse)
    assert len(client.log) == 2
    # Verify second prompt contains error context
    assert "[SYSTEM ERROR]" in client.log[1]
    assert "Invalid JSON" in client.log[1]

def test_generate_retry_failure():
    """Failure path: Invalid first, Invalid second => Exception."""
    client = FakeAIClient([INVALID_JSON, INVALID_JSON])
    
    with pytest.raises(VerdictV3GenerationError) as exc:
        generate_scorecard(SAMPLE_CONTEXT, client)
    
    assert "scorecard" in exc.value.section
    assert "Validation failed" in exc.value.message

def test_generate_story_integration():
    """Test another type to ensure generic logic works broadly."""
    valid_story = json.dumps({
        "run_story": {
            "start": "Was cold",
            "middle": "Warmed up",
            "finish": "Finished strong"
        }
    })
    client = FakeAIClient([valid_story])
    
    result = generate_story(SAMPLE_CONTEXT, client)
    
    assert isinstance(result, StoryResponse)
    assert result.run_story.start == "Was cold"
