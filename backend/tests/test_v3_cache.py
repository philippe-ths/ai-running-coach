"""Tests for V3 coaching cache mechanism."""
import pytest
import json
from unittest.mock import MagicMock, patch
from app.services.ai.verdict_v3.generators import generate_scorecard
from app.services.ai.verdict_v3.cache import compute_stable_hash
from app.schemas import VerdictScorecardResponse
from tests.test_v3_generators import FakeAIClient, SUCCESS_JSON, SAMPLE_CONTEXT

@pytest.fixture
def mock_redis():
    with patch("app.services.ai.verdict_v3.cache.redis_conn") as m:
        # Simulate dict storage
        storage = {}
        
        def setex(key, ttl, val):
            storage[key] = val
        
        def get(key):
            return storage.get(key)
            
        m.setex.side_effect = setex
        m.get.side_effect = get
        m.storage = storage # Access for assertion
        yield m

def test_cache_hit_prevents_ai_call(mock_redis):
    client = FakeAIClient([SUCCESS_JSON])
    
    # 1. First Call: AI Hit, Cache Set
    res1 = generate_scorecard(SAMPLE_CONTEXT, client)
    
    assert len(client.log) == 1
    assert len(mock_redis.storage) == 1 # One key stored
    
    # 2. Second Call: Cache Hit, AI Skipped
    client.log = [] # Reset log
    res2 = generate_scorecard(SAMPLE_CONTEXT, client)
    
    assert len(client.log) == 0 # Should NOT have called AI
    assert res1.headline.status == res2.headline.status

def test_cache_invalidation_on_context_change(mock_redis):
    client = FakeAIClient([SUCCESS_JSON, SUCCESS_JSON])
    
    # 1. Initial Call
    generate_scorecard(SAMPLE_CONTEXT, client)
    
    # 2. Change Context (e.g. pain score changed)
    new_context = SAMPLE_CONTEXT.copy()
    new_context["check_in"] = {"pain_0_10": 9}
    
    # 3. Second Call with New Context -> Should Miss Cache
    generate_scorecard(new_context, client)
    
    assert len(client.log) == 2 # Called twice
    assert len(mock_redis.storage) == 2 # Two different keys stored

def test_hash_stability():
    """Ensure hash is deterministic regardless of key order."""
    d1 = {"a": 1, "b": 2}
    d2 = {"b": 2, "a": 1}
    assert compute_stable_hash(d1) == compute_stable_hash(d2)
