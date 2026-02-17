"""Tests for coach report Pydantic schema validation."""

import pytest
from pydantic import ValidationError
from app.schemas.coach import CoachReportContent, CoachTakeaway, EvidenceRef


def _valid_content(**overrides):
    base = {
        "key_takeaways": [
            {"text": "Takeaway one.", "evidence": [{"field": "metrics.effort_score", "value": 3.2}]},
            {"text": "Takeaway two.", "evidence": [{"field": "metrics.hr_drift", "value": 5.1}]},
        ],
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
        key_takeaways=[
            {"text": "A", "evidence": [{"field": "a", "value": 1}]},
            {"text": "B", "evidence": [{"field": "b", "value": 2}]},
            {"text": "C", "evidence": [{"field": "c", "value": 3}]},
            {"text": "D", "evidence": [{"field": "d", "value": 4}]},
        ],
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
        CoachReportContent.model_validate(
            _valid_content(key_takeaways=[{"text": "Only one"}])
        )


def test_rejects_too_many_takeaways():
    with pytest.raises(ValidationError):
        CoachReportContent.model_validate(
            _valid_content(key_takeaways=[{"text": t} for t in ["A", "B", "C", "D", "E"]])
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


# ---------------------------------------------------------------------------
# Evidence pointer tests
# ---------------------------------------------------------------------------

def test_takeaway_structured_evidence():
    """Structured evidence as array of {field, value} dicts."""
    content = CoachReportContent.model_validate(_valid_content(
        key_takeaways=[
            {"text": "Good session", "evidence": [{"field": "metrics.effort_score", "value": 3.5}, {"field": "metrics.hr_drift", "value": 2.1}]},
            {"text": "Pace was steady", "evidence": [{"field": "metrics.pace_variability", "value": 8.2}]},
        ]
    ))
    assert content.key_takeaways[0].text == "Good session"
    assert len(content.key_takeaways[0].evidence) == 2
    assert content.key_takeaways[0].evidence[0].field == "metrics.effort_score"
    assert content.key_takeaways[0].evidence[0].value == 3.5


def test_takeaway_legacy_string_evidence_coerced():
    """Legacy string evidence should be auto-coerced to structured format."""
    content = CoachReportContent.model_validate(_valid_content(
        key_takeaways=[
            {"text": "Good session", "evidence": "effort_score=3.5, hr_drift=2.1%"},
            {"text": "Pace was steady", "evidence": "pace_variability=8.2"},
        ]
    ))
    assert isinstance(content.key_takeaways[0].evidence, list)
    assert content.key_takeaways[0].evidence[0].field == "effort_score"


def test_takeaway_backward_compat_bare_strings():
    """Bare strings in key_takeaways should be auto-coerced to CoachTakeaway."""
    content = CoachReportContent.model_validate(_valid_content(
        key_takeaways=["First bare string.", "Second bare string."]
    ))
    assert isinstance(content.key_takeaways[0], CoachTakeaway)
    assert content.key_takeaways[0].text == "First bare string."
    assert content.key_takeaways[0].evidence is None
    assert content.key_takeaways[1].text == "Second bare string."


def test_takeaway_evidence_optional():
    """Evidence field should be optional (None is valid)."""
    content = CoachReportContent.model_validate(_valid_content(
        key_takeaways=[
            {"text": "No evidence here"},
            {"text": "Has evidence", "evidence": [{"field": "metrics.effort_score", "value": 4.0}]},
        ]
    ))
    assert content.key_takeaways[0].evidence is None
    assert len(content.key_takeaways[1].evidence) == 1


def test_next_step_with_structured_evidence():
    """Next steps should accept structured evidence."""
    content = CoachReportContent.model_validate(_valid_content(
        next_steps=[{
            "action": "Do an easy run",
            "details": "30 min at conversational pace",
            "why": "You ran hard yesterday",
            "evidence": [{"field": "training_context.days_since_last_hard", "value": 1}],
        }]
    ))
    assert len(content.next_steps[0].evidence) == 1


def test_next_step_legacy_string_evidence():
    """Legacy string evidence in next_steps should be coerced."""
    content = CoachReportContent.model_validate(_valid_content(
        next_steps=[{
            "action": "Run", "details": "Easy", "why": "Recovery",
            "evidence": "training_context.days_since_last_hard=1, effort_score=6.8",
        }]
    ))
    assert isinstance(content.next_steps[0].evidence, list)


def test_next_step_evidence_optional():
    """Next step evidence should default to None when not provided."""
    content = CoachReportContent.model_validate(_valid_content(
        next_steps=[{"action": "Run", "details": "Easy", "why": "Recovery"}]
    ))
    assert content.next_steps[0].evidence is None
