"""A2a: the exchange digest projection (build_report_digest).

Characterises the EXACT projection the M4 longitudinal digest has always used, so
the stored artifact is provably equal to the recomputed one. Since A2b the
read-time digest is resolved by the retrieval seam (retrieval._resolve_digest),
which prefers the stored artifact and falls back to this projection;
test_longitudinal_context.py and test_retrieval.py remain the behaviour-preserving
guards.
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


def test_seam_resolution_matches_shared_projection():
    """The read-time digest the retrieval seam produces for a row WITHOUT a stored
    artifact (a pre-A2a row) equals this shared projection — the invariant the M4
    longitudinal read has always upheld, now owned by retrieval._resolve_digest."""
    from types import SimpleNamespace

    from app.services.coach.retrieval import _resolve_digest

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
    # A row with no stored digest re-projects via build_report_digest.
    row = SimpleNamespace(digest=None, report=report)
    assert _resolve_digest(row, start).model_dump() == build_report_digest(report, start).model_dump()
