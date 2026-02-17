"""Tests for coach report Pydantic schema validation."""

import pytest
from pydantic import ValidationError
from app.schemas.coach import CoachReportContent


def _valid_content(**overrides):
    base = {
        "key_takeaways": ["Takeaway one.", "Takeaway two."],
        "next_steps": [
            {"action": "Easy run", "details": "30 min conversational", "why": "Recovery"}
        ],
        "risks": [],
        "questions": [],
    }
    base.update(overrides)
    return base


def test_valid_minimal_report():
    content = CoachReportContent.model_validate(_valid_content())
    assert len(content.key_takeaways) == 2
    assert len(content.next_steps) == 1
    assert content.risks == []
    assert content.questions == []


def test_valid_full_report():
    data = _valid_content(
        key_takeaways=["A", "B", "C", "D"],
        next_steps=[
            {"action": "X", "details": "Y", "why": "Z"},
            {"action": "X2", "details": "Y2", "why": "Z2"},
            {"action": "X3", "details": "Y3", "why": "Z3"},
        ],
        risks=[{"flag": "pain_reported", "explanation": "Pain", "mitigation": "Rest"}],
        questions=[{"question": "How?", "reason": "Unclear"}],
    )
    content = CoachReportContent.model_validate(data)
    assert len(content.key_takeaways) == 4
    assert len(content.next_steps) == 3
    assert len(content.risks) == 1
    assert len(content.questions) == 1


def test_rejects_too_few_takeaways():
    with pytest.raises(ValidationError):
        CoachReportContent.model_validate(_valid_content(key_takeaways=["Only one"]))


def test_rejects_too_many_takeaways():
    with pytest.raises(ValidationError):
        CoachReportContent.model_validate(
            _valid_content(key_takeaways=["A", "B", "C", "D", "E"])
        )


def test_rejects_no_next_steps():
    with pytest.raises(ValidationError):
        CoachReportContent.model_validate(_valid_content(next_steps=[]))


def test_rejects_too_many_next_steps():
    steps = [{"action": f"S{i}", "details": "D", "why": "W"} for i in range(4)]
    with pytest.raises(ValidationError):
        CoachReportContent.model_validate(_valid_content(next_steps=steps))


def test_rejects_too_many_questions():
    qs = [{"question": f"Q{i}?", "reason": "R"} for i in range(5)]
    with pytest.raises(ValidationError):
        CoachReportContent.model_validate(_valid_content(questions=qs))


def test_accepts_empty_risks_and_questions():
    content = CoachReportContent.model_validate(
        _valid_content(risks=[], questions=[])
    )
    assert content.risks == []
    assert content.questions == []
