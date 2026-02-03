"""Tests for the split V3 generation schemas and their validators."""

import pytest
from pydantic import ValidationError
from app.schemas import (
    VerdictScorecardResponse,
    StoryResponse,
    LeverResponse,
    NextStepsResponse,
    QuestionResponse,
    V3Headline,
    V3ScorecardItem,
    V3Lever
)

class TestVerdictScorecardResponse:
    def test_why_it_matters_length_constraint(self):
        """Should fail if why_it_matters is not exactly 2 items."""
        base_data = {
            "inputs_used_line": "Data used: HR",
            "headline": {"sentence": "Good job", "status": "green"},
            "scorecard": []
        }
        
        # Test valid
        VerdictScorecardResponse(
            why_it_matters=["Fitness point", "Fatigue point"],
            **base_data
        )

        # Test invalid (too short)
        with pytest.raises(ValidationError) as exc:
            VerdictScorecardResponse(
                why_it_matters=["Just one point"],
                **base_data
            )
        # Check that one of the errors relates to length
        assert any(e['type'] == 'too_short' for e in exc.value.errors())

        # Test invalid (too long)
        with pytest.raises(ValidationError) as exc:
            VerdictScorecardResponse(
                why_it_matters=["1", "2", "3"],
                **base_data
            )
        assert any(e['type'] == 'too_long' for e in exc.value.errors())

    def test_scorecard_uniqueness(self):
        """Should fail if scorecard contains duplicate items."""
        base_data = {
            "inputs_used_line": "Data used: HR",
            "headline": {"sentence": "Good job", "status": "green"},
            "why_it_matters": ["1", "2"]
        }

        # Valid
        VerdictScorecardResponse(
            scorecard=[
                {"item": "Purpose match", "rating": "ok", "reason": "ok"},
                {"item": "Control (smoothness)", "rating": "ok", "reason": "ok"}
            ],
            **base_data
        )

        # Invalid (Duplicate "Purpose match")
        with pytest.raises(ValidationError) as exc:
            VerdictScorecardResponse(
                scorecard=[
                    {"item": "Purpose match", "rating": "ok", "reason": "ok"},
                    {"item": "Purpose match", "rating": "fail", "reason": "bad"}
                ],
                **base_data
            )
        assert "Duplicate scorecard item" in str(exc.value)


class TestLeverResponse:
    def test_cue_must_be_quoted(self):
        """Cue field must accept only quoted strings."""
        
        # Valid
        r = LeverResponse(lever={
            "category": "pacing",
            "signal": "HR",
            "cause": "Drift",
            "fix": "Slow down",
            "cue": "\"Relax\""
        })
        assert r.lever.cue == "\"Relax\""

        # Valid (single quotes)
        r = LeverResponse(lever={
            "category": "pacing",
            "signal": "HR",
            "cause": "Drift",
            "fix": "Slow down",
            "cue": "'Relax'"
        })
        assert r.lever.cue == "'Relax'"

        # Invalid (no quotes)
        with pytest.raises(ValidationError) as exc:
            LeverResponse(lever={
                "category": "pacing",
                "signal": "HR",
                "cause": "Drift",
                "fix": "Slow down",
                "cue": "Relax"
            })
        assert "Cue must be enclosed in quotes" in str(exc.value)

class TestSubResponses:
    def test_story_response(self):
        """Basic structure check for story."""
        StoryResponse(run_story={
            "start": "Fast",
            "middle": "Steady",
            "finish": "Strong"
        })
        
    def test_next_steps_response(self):
        """Basic structure check for next steps."""
        NextStepsResponse(next_steps={
            "tomorrow": "Rest",
            "next_7_days": "Easy"
        })

    def test_question_response(self):
        """Basic structure for question."""
        QuestionResponse(question_for_you="How did the hills feel?")
