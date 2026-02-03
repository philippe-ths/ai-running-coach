"""Tests for V3 prompt generation to ensure they enforce format rules."""

import json
import pytest
from app.services.ai.verdict_v3.prompts import (
    build_scorecard_prompt,
    build_story_prompt,
    build_lever_prompt,
    build_next_steps_prompt,
    build_question_prompt
)

@pytest.fixture
def mock_slice():
    return {"key": "value", "sub": {"a": 1}}

def test_prompts_enforce_json_and_input_usage(mock_slice):
    prompts = [
        build_scorecard_prompt(mock_slice),
        build_story_prompt(mock_slice),
        build_lever_prompt(mock_slice),
        build_next_steps_prompt(mock_slice),
        build_question_prompt(mock_slice)
    ]
    
    for p in prompts:
        # Check core constraints
        assert "Strict JSON" in p
        assert "INPUT CONTEXT" in p
        # Check context injection
        assert "\"key\": \"value\"" in p
        assert "\"sub\"" in p

def test_scorecard_prompt_spec(mock_slice):
    p = build_scorecard_prompt(mock_slice)
    assert "why_it_matters" in p
    assert "Fitness System" in p
    assert "Fatigue Cost" in p
    assert "Purpose match" in p
    assert "VerdictScorecardResponse" in p
    # New constraints check
    assert "explicitly name the system trained" in p
    assert "state the recovery implications" in p
    assert "cite specific evidence" in p

def test_lever_prompt_spec(mock_slice):
    p = build_lever_prompt(mock_slice)
    assert "wrappe in quotes" in p or "quotes" in p
    assert "LeverResponse" in p
    assert "Category choices" in p
    # New constraints check
    assert "supportive" in p

def test_next_steps_prompt_spec(mock_slice):
    p = build_next_steps_prompt(mock_slice)
    assert "Option A / Option B" in p
    assert "NextStepsResponse" in p
    # New constraints check
    assert "If Verdict is AMBER" in p

def test_story_prompt_spec(mock_slice):
    p = build_story_prompt(mock_slice)
    assert "Start" in p
    assert "Middle" in p
    assert "Finish" in p
    assert "StoryResponse" in p
    # New constraints check
    assert "grounded encouraging phrase" in p
