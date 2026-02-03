"""Tests for the safety override logic."""
import pytest
from app.services.ai.verdict_v3.safety import enforce_scorecard_safety, enforce_next_steps_safety
from app.schemas import VerdictScorecardResponse, NextStepsResponse, V3Headline, V3NextSteps

# --- Mock Data ---
def make_scorecard(status="green"):
    return VerdictScorecardResponse(
        inputs_used_line="...",
        headline=V3Headline(sentence="Great job", status=status),
        why_it_matters=["A", "B"],
        scorecard=[]
    )

def make_next_steps(tomorrow="Speed work"):
    return NextStepsResponse(
        next_steps=V3NextSteps(tomorrow=tomorrow, next_7_days="...")
    )

class TestScorecardSafety:
    def test_high_pain_downgrades_to_red(self):
        context = {"check_in": {"pain_0_10": 8}, "flags": []}
        sc = make_scorecard("green")
        
        result = enforce_scorecard_safety(sc, context)
        
        assert result.headline.status == "red"
        assert "[Safety Override]" in result.headline.sentence

    def test_risk_flag_downgrades_to_amber(self):
        context = {
            "check_in": {"pain_0_10": 2}, 
            "flags": [{"severity": "risk", "code": "overtraining"}]
        }
        sc = make_scorecard("green")
        
        result = enforce_scorecard_safety(sc, context)
        
        assert result.headline.status == "amber"

    def test_safe_inputs_no_change(self):
        context = {"check_in": {"pain_0_10": 3}, "flags": []}
        sc = make_scorecard("green")
        
        result = enforce_scorecard_safety(sc, context)
        
        assert result.headline.status == "green"
        assert "[Safety Override]" not in result.headline.sentence

class TestNextStepsSafety:
    def test_red_status_forces_rest(self):
        sc = make_scorecard("red")
        ns = make_next_steps("Tempos and hills")
        context = {}
        
        result = enforce_next_steps_safety(ns, sc, context)
        
        assert "Rest" in result.next_steps.tomorrow or "recovery" in result.next_steps.tomorrow

    def test_amber_status_prevents_quality(self):
        sc = make_scorecard("amber")
        ns = make_next_steps("Hard intervals")
        context = {}
        
        result = enforce_next_steps_safety(ns, sc, context)
        
        # specific hard keywords check
        assert result.next_steps.tomorrow != "Hard intervals"
        assert "Easy" in result.next_steps.tomorrow

    def test_green_status_allows_quality(self):
        sc = make_scorecard("green")
        ns = make_next_steps("Hard intervals")
        context = {}
        
        result = enforce_next_steps_safety(ns, sc, context)
        
        assert result.next_steps.tomorrow == "Hard intervals"
