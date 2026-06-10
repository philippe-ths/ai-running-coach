"""A2a: the exchange digest projection (build_report_digest).

Characterises the EXACT projection the M4 longitudinal digest has always used
(context._digest_from_report), so the stored artifact is provably equal to the
recomputed one. context._digest_from_report now delegates here, and
test_longitudinal_context.py remains the behaviour-preserving guard.
"""

from datetime import datetime, timezone

from app.services.coach.digest import build_report_digest


def test_full_report_projects_all_four_fields():
    report = {
        "headline": "Solid tempo",
        "thesis": "ignored body",
        "lead_argument": {"text": "Aerobic base is holding", "evidence": [{"field": "x", "value": 1}]},
        "key_takeaways": [{"text": "ignored"}],
        "next_steps": [{"action": "Add a tempo segment", "details": "20 min", "why": "ignored"}],
        "risks": [],
        "questions": [],
    }
    start = datetime(2026, 3, 1, 8, 0, tzinfo=timezone.utc)
    d = build_report_digest(report, start)
    assert d.headline == "Solid tempo"
    assert d.lead_argument == "Aerobic base is holding"
    assert d.next_steps == ["Add a tempo segment (20 min)"]
    assert d.activity_date == start.isoformat()


def test_lead_argument_as_bare_string():
    d = build_report_digest({"lead_argument": "plain text lead"}, None)
    assert d.lead_argument == "plain text lead"


def test_lead_argument_missing_is_none():
    assert build_report_digest({}, None).lead_argument is None


def test_lead_argument_unexpected_type_is_none():
    assert build_report_digest({"lead_argument": 42}, None).lead_argument is None


def test_next_step_action_only_has_no_parenthetical():
    d = build_report_digest({"next_steps": [{"action": "Run easy"}]}, None)
    assert d.next_steps == ["Run easy"]


def test_next_step_empty_action_is_skipped():
    d = build_report_digest(
        {"next_steps": [{"action": "", "details": "orphan"}, {"action": "Keep cadence", "details": ""}]},
        None,
    )
    assert d.next_steps == ["Keep cadence"]


def test_non_dict_next_step_is_skipped():
    d = build_report_digest({"next_steps": ["not a dict", {"action": "Recover"}]}, None)
    assert d.next_steps == ["Recover"]


def test_missing_headline_is_none():
    assert build_report_digest({"next_steps": []}, None).headline is None


def test_no_start_date_yields_empty_string():
    assert build_report_digest({"headline": "x"}, None).activity_date == ""


def test_empty_report_is_safe():
    d = build_report_digest({}, None)
    assert d.headline is None
    assert d.lead_argument is None
    assert d.next_steps == []
    assert d.activity_date == ""


def test_matches_context_digest_projection():
    """The stored artifact must equal what the in-memory M4 digest produces."""
    from app.services.coach.context import _digest_from_report

    report = {
        "headline": "Recovery day",
        "lead_argument": {"text": "Easy effort confirmed"},
        "next_steps": [
            {"action": "Hold easy", "details": "keep HR low"},
            {"action": "Sleep", "details": ""},
            "junk",
        ],
    }
    start = datetime(2026, 4, 2, 7, 30, tzinfo=timezone.utc)
    assert build_report_digest(report, start).model_dump() == _digest_from_report(report, start).model_dump()
