"""Tests that every /api/verdict/v3/* endpoint returns debug_context + debug_prompt."""
import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
from uuid import uuid4

from app.main import app
from app.schemas import (
    VerdictScorecardResponse,
    StoryResponse,
    LeverResponse,
    NextStepsResponse,
    QuestionResponse,
    SummaryResponse,
    V3Headline,
    V3ScorecardItem,
    V3RunStory,
    V3Lever,
    V3NextSteps,
    V3ExecutiveSummary,
)
from app.services.ai.context_pack import ContextPack
from app.services.ai.verdict_v3.generators import _attach_debug

client = TestClient(app)

ACTIVITY_ID = uuid4()

MOCK_CONTEXT = ContextPack(
    activity={
        "id": str(ACTIVITY_ID),
        "name": "Debug Test Run",
        "start_time": "2024-06-01T08:00:00Z",
        "type": "Run",
        "distance_m": 5000,
        "moving_time_s": 1500,
        "elapsed_time_s": 1500,
        "elevation_gain_m": 0,
        "avg_hr": 140,
        "avg_pace_s_per_km": 300,
        "avg_cadence": 170,
    },
    athlete={"goal": "5k", "experience_level": "Beginner"},
    derived_metrics=[],
    flags=[],
    last_7_days={},
    check_in={},
    available_signals=["hr"],
    missing_signals=[],
    generated_at_iso="2024-06-01T08:00:00Z",
)

MOCK_DEBUG_CONTEXT = {"activity": {"name": "Debug Test Run"}}

# ---------- mock responses WITH debug fields populated ----------

MOCK_SCORECARD = VerdictScorecardResponse(
    inputs_used_line="HR, pace",
    headline=V3Headline(sentence="Solid effort", status="green"),
    why_it_matters=["Fit", "Fresh"],
    scorecard=[
        V3ScorecardItem(item="Purpose match", rating="ok", reason="ok"),
        V3ScorecardItem(item="Control (smoothness)", rating="ok", reason="ok"),
        V3ScorecardItem(item="Aerobic value", rating="ok", reason="ok"),
        V3ScorecardItem(item="Mechanical quality", rating="ok", reason="ok"),
        V3ScorecardItem(item="Risk / recoverability", rating="ok", reason="ok"),
    ],
    debug_context=MOCK_DEBUG_CONTEXT,
    debug_prompt={"scorecard": "You are a running coach...", "why_it_matters": "You are a running coach..."},
)

MOCK_STORY = StoryResponse(
    run_story=V3RunStory(start="Cold", middle="Warm", finish="Hot"),
    debug_context=MOCK_DEBUG_CONTEXT,
    debug_prompt={"story": "Generate a narrative..."},
)

MOCK_LEVER = LeverResponse(
    lever=V3Lever(
        category="pacing", signal="HR", cause="High", fix="Slow", cue='"Easy"'
    ),
    debug_context=MOCK_DEBUG_CONTEXT,
    debug_prompt={"lever": "Identify the single lever..."},
)

MOCK_NEXT = NextStepsResponse(
    next_steps=V3NextSteps(tomorrow="Rest", next_7_days="Easy"),
    debug_context=MOCK_DEBUG_CONTEXT,
    debug_prompt={"next_steps": "Plan the next 7 days..."},
)

MOCK_QUESTION = QuestionResponse(
    question_for_you="How did that feel?",
    debug_context=MOCK_DEBUG_CONTEXT,
    debug_prompt={"question": "Ask a coaching question..."},
)

MOCK_SUMMARY = SummaryResponse(
    executive_summary=V3ExecutiveSummary(
        title="Good run", status="green", opinion="Keep it up"
    ),
    debug_context=MOCK_DEBUG_CONTEXT,
    debug_prompt={"summary": "Summarise the verdict..."},
)


# ────────────────────────────────────────────────────
# Helper
# ────────────────────────────────────────────────────

def _assert_debug_fields(data: dict, expected_key: str):
    """Assert that the response JSON contains the correct debug fields."""
    assert "debug_context" in data, "Response missing debug_context"
    assert data["debug_context"] is not None, "debug_context is None"
    assert "debug_prompt" in data, "Response missing debug_prompt"
    assert data["debug_prompt"] is not None, "debug_prompt is None"
    assert expected_key in data["debug_prompt"], (
        f"debug_prompt missing key '{expected_key}', got keys: {list(data['debug_prompt'].keys())}"
    )
    assert isinstance(data["debug_prompt"][expected_key], str), (
        f"debug_prompt['{expected_key}'] should be a string"
    )


# ────────────────────────────────────────────────────
# Tests
# ────────────────────────────────────────────────────

@patch("app.api.verdict_v3.build_context_pack", return_value=MOCK_CONTEXT)
def test_scorecard_returns_debug_fields(mock_cp):
    with patch("app.api.verdict_v3.generate_scorecard", return_value=MOCK_SCORECARD):
        resp = client.post(
            "/api/verdict/v3/scorecard",
            json={"activity_id": str(ACTIVITY_ID)},
        )
        assert resp.status_code == 200
        _assert_debug_fields(resp.json(), "scorecard")
        _assert_debug_fields(resp.json(), "why_it_matters")


@patch("app.api.verdict_v3.build_context_pack", return_value=MOCK_CONTEXT)
def test_story_returns_debug_fields(mock_cp):
    with patch("app.api.verdict_v3.generate_story", return_value=MOCK_STORY):
        resp = client.post(
            "/api/verdict/v3/story",
            json={"activity_id": str(ACTIVITY_ID)},
        )
        assert resp.status_code == 200
        _assert_debug_fields(resp.json(), "story")


@patch("app.api.verdict_v3.build_context_pack", return_value=MOCK_CONTEXT)
def test_lever_returns_debug_fields(mock_cp):
    payload = {
        "activity_id": str(ACTIVITY_ID),
        "scorecard": MOCK_SCORECARD.model_dump(),
    }
    with patch("app.api.verdict_v3.generate_lever", return_value=MOCK_LEVER):
        resp = client.post("/api/verdict/v3/lever", json=payload)
        assert resp.status_code == 200
        _assert_debug_fields(resp.json(), "lever")


@patch("app.api.verdict_v3.build_context_pack", return_value=MOCK_CONTEXT)
def test_next_steps_returns_debug_fields(mock_cp):
    payload = {
        "activity_id": str(ACTIVITY_ID),
        "scorecard": MOCK_SCORECARD.model_dump(),
        "lever": MOCK_LEVER.model_dump(),
    }
    with patch("app.api.verdict_v3.generate_next_steps", return_value=MOCK_NEXT):
        resp = client.post("/api/verdict/v3/next-steps", json=payload)
        assert resp.status_code == 200
        _assert_debug_fields(resp.json(), "next_steps")


@patch("app.api.verdict_v3.build_context_pack", return_value=MOCK_CONTEXT)
def test_question_returns_debug_fields(mock_cp):
    payload = {
        "activity_id": str(ACTIVITY_ID),
        "scorecard": MOCK_SCORECARD.model_dump(),
    }
    with patch("app.api.verdict_v3.generate_question", return_value=MOCK_QUESTION):
        resp = client.post("/api/verdict/v3/question", json=payload)
        assert resp.status_code == 200
        _assert_debug_fields(resp.json(), "question")


@patch("app.api.verdict_v3.build_context_pack", return_value=MOCK_CONTEXT)
def test_summary_returns_debug_fields(mock_cp):
    payload = {
        "activity_id": str(ACTIVITY_ID),
        "scorecard": MOCK_SCORECARD.model_dump(),
        "lever": MOCK_LEVER.model_dump(),
        "story": MOCK_STORY.model_dump(),
        "next_steps": MOCK_NEXT.model_dump(),
    }
    with patch(
        "app.api.verdict_v3.generate_executive_summary", return_value=MOCK_SUMMARY
    ):
        resp = client.post("/api/verdict/v3/summary", json=payload)
        assert resp.status_code == 200
        _assert_debug_fields(resp.json(), "summary")


@patch("app.api.verdict_v3.build_context_pack", return_value=MOCK_CONTEXT)
def test_full_generate_returns_debug_fields(mock_cp):
    """The composite /generate endpoint must have a merged debug_prompt with all keys."""
    with patch("app.api.verdict_v3.generate_full_verdict_orchestrator") as mock_orch:
        from app.schemas import CoachVerdictV3

        mock_orch.return_value = CoachVerdictV3(
            inputs_used_line="HR, pace",
            headline=None,
            executive_summary=V3ExecutiveSummary(
                title="Good run", status="green", opinion="Keep it up"
            ),
            why_it_matters=["Fit", "Fresh"],
            scorecard=MOCK_SCORECARD.scorecard,
            run_story=MOCK_STORY.run_story,
            lever=MOCK_LEVER.lever,
            next_steps=MOCK_NEXT.next_steps,
            question_for_you="How did that feel?",
            debug_context=MOCK_DEBUG_CONTEXT,
            debug_prompt={
                "scorecard": "...",
                "why_it_matters": "...",
                "story": "...",
                "lever": "...",
                "next_steps": "...",
                "question": "...",
                "summary": "...",
            },
        )

        resp = client.post(
            "/api/verdict/v3/generate",
            json={"activity_id": str(ACTIVITY_ID)},
        )
        assert resp.status_code == 200
        data = resp.json()

        assert data["debug_context"] is not None
        assert data["debug_prompt"] is not None
        for key in ("scorecard", "why_it_matters", "story", "lever", "next_steps", "question", "summary"):
            assert key in data["debug_prompt"], f"Missing '{key}' in debug_prompt"


# ────────────────────────────────────────────────────
# DEBUG_AI=False gating tests
# ────────────────────────────────────────────────────

def test_attach_debug_disabled():
    """When DEBUG_AI is False, _attach_debug sets fields to None."""
    model = StoryResponse(
        run_story=V3RunStory(start="A", middle="B", finish="C"),
        debug_context={"old": True},
        debug_prompt={"story": "old"},
    )
    with patch("app.services.ai.verdict_v3.generators.settings") as mock_settings:
        mock_settings.DEBUG_AI = False
        _attach_debug(model, {"new": True}, {"story": "new prompt"})

    assert model.debug_context is None
    assert model.debug_prompt is None


def test_attach_debug_enabled():
    """When DEBUG_AI is True, _attach_debug populates fields."""
    model = StoryResponse(
        run_story=V3RunStory(start="A", middle="B", finish="C"),
    )
    with patch("app.services.ai.verdict_v3.generators.settings") as mock_settings:
        mock_settings.DEBUG_AI = True
        _attach_debug(model, {"ctx": 1}, {"story": "prompt text"})

    assert model.debug_context == {"ctx": 1}
    assert model.debug_prompt == {"story": "prompt text"}


@patch("app.api.verdict_v3.build_context_pack", return_value=MOCK_CONTEXT)
def test_scorecard_omits_debug_when_disabled(mock_cp):
    """When DEBUG_AI=False, the scorecard endpoint returns null debug fields."""
    # Build a scorecard response with debug fields stripped
    scorecard_no_debug = MOCK_SCORECARD.model_copy(deep=True)
    scorecard_no_debug.debug_context = None
    scorecard_no_debug.debug_prompt = None

    with patch("app.api.verdict_v3.generate_scorecard", return_value=scorecard_no_debug):
        resp = client.post(
            "/api/verdict/v3/scorecard",
            json={"activity_id": str(ACTIVITY_ID)},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["debug_context"] is None
        assert data["debug_prompt"] is None
