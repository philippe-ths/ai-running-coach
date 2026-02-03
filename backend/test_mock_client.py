
import pytest
from app.services.ai.client import AIClient
from app.core.config import settings

def test_mock_client_routing():
    # Force mock provider
    settings.AI_PROVIDER = "mock"
    client = AIClient()
    
    # Test Scorecard routing
    scorecard_json = client.get_raw_json_response("Please generate a JSON matching VerdictScorecardResponse schema ...")
    assert "scorecard" in scorecard_json
    assert "Consistency" in scorecard_json
    
    # Test Story routing
    story_json = client.get_raw_json_response("Please generate a JSON matching StoryResponse schema ...")
    assert "narrative" in story_json
    
    # Test default
    empty_json = client.get_raw_json_response("Just tell me a joke")
    assert empty_json == "{}"

if __name__ == "__main__":
    test_mock_client_routing()
    print("Mock client routing checks passed!")
