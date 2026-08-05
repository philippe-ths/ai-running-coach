"""The activity list carries the coach's opening line (#797).

Telegram is the only channel that ever ANNOUNCES a report. A runner who declines
to link one still has every report generated, stored and visible — and, before
this, nothing anywhere in the app said so. Declining was a silent dead end rather
than a supported choice.

These cover the selection rules that decide WHICH report speaks for a row, since
getting that wrong shows a runner the wrong run's coaching, and the batching that
keeps the home page from issuing one query per activity.
"""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from app.core.config import settings
from app.models import Activity, User
from app.models.coach_report import CoachReport
from app.services.coach.service import (
    active_schema_version,
    get_displayable_report_leads,
)

_LEAD = "You held drift under 3% the whole way, which is new for you."


@pytest.fixture
def user(db):
    u = User(id=uuid4(), email=f"{uuid4()}@x.dev")
    db.add(u)
    db.commit()
    return u


def _activity(db, user, *, offset_days=0, name="Evening Run"):
    a = Activity(
        id=uuid4(), user_id=user.id, strava_activity_id=int(uuid4().int % 10**12),
        name=name, type="Run",
        start_date=datetime(2026, 8, 1, tzinfo=timezone.utc) - timedelta(days=offset_days),
        distance_m=10000, moving_time_s=3000, elapsed_time_s=3000, elev_gain_m=10.0,
    )
    db.add(a)
    db.commit()
    return a


def _report(db, activity, *, message=_LEAD, digest=None, is_fallback=False,
            prompt_id=None, schema_version=None, superseded_at=None, created_offset=0):
    prompt_id = prompt_id or settings.COACH_PROMPT_ID
    schema_version = schema_version or active_schema_version(prompt_id)
    row = CoachReport(
        id=uuid4(), activity_id=activity.id,
        prompt_id=prompt_id,
        schema_version=schema_version,
        report={"message": message, "headline": "Solid run"},
        meta={
            "confidence": "medium", "model_id": "claude-sonnet-4-6",
            "prompt_id": prompt_id, "schema_version": schema_version,
            "input_hash": "h", "generated_at": "2026-08-01T00:00:00Z",
        },
        context_pack={},
        digest=digest,
        is_fallback=is_fallback,
        superseded_at=superseded_at,
        created_at=datetime(2026, 8, 1, tzinfo=timezone.utc) + timedelta(hours=created_offset),
    )
    db.add(row)
    db.commit()
    return row


def test_lead_is_the_reports_first_sentence(db, user):
    a = _activity(db, user)
    _report(db, a, message=f"{_LEAD} The second sentence should not appear.")
    assert get_displayable_report_leads(db, [a])[a.id] == _LEAD


def test_stored_digest_is_preferred_over_reprojection(db, user):
    # The stored digest is what the longitudinal read treats as this report's
    # lead. The list must show the SAME text, not a second projection that could
    # drift from it.
    a = _activity(db, user)
    _report(db, a, message="Re-projected text.", digest={"lead_argument": "Stored lead."})
    assert get_displayable_report_leads(db, [a])[a.id] == "Stored lead."


def test_activity_without_a_report_has_no_lead(db, user):
    a = _activity(db, user)
    assert get_displayable_report_leads(db, [a]) == {}


def test_fallback_report_is_never_shown(db, user):
    # is_fallback marks a generation that FAILED. Its apology text is not
    # coaching, and advertising it on the list would be worse than silence.
    a = _activity(db, user)
    _report(db, a, message="I could not analyse this run.", is_fallback=True)
    assert get_displayable_report_leads(db, [a]) == {}


def test_superseded_audit_copy_is_never_shown(db, user):
    # #646: a force-regeneration keeps the prior row as an immutable audit copy.
    a = _activity(db, user)
    _report(db, a, message="The old, replaced take.",
            superseded_at=datetime(2026, 8, 1, tzinfo=timezone.utc))
    assert get_displayable_report_leads(db, [a]) == {}


def test_active_version_wins_over_a_newer_stale_version(db, user):
    """Selection must match `get_displayable_report_row`, or the list and the
    detail page quote two different reports for one run."""
    a = _activity(db, user)
    _report(db, a, message="Active version lead.", created_offset=0)
    _report(db, a, message="Older prompt, written later.",
            prompt_id="coach_message_v1", schema_version="2.0", created_offset=5)
    assert get_displayable_report_leads(db, [a])[a.id] == "Active version lead."


def test_falls_back_to_most_recent_when_no_active_version_exists(db, user):
    # #261 display-safety: after a COACH_PROMPT_ID flip, prior-version reports
    # must still render rather than vanishing from the list.
    a = _activity(db, user)
    _report(db, a, message="Older of the two.",
            prompt_id="coach_message_v1", schema_version="2.0", created_offset=0)
    _report(db, a, message="Newer of the two.",
            prompt_id="coach_message_v2", schema_version="2.0", created_offset=5)
    assert get_displayable_report_leads(db, [a])[a.id] == "Newer of the two."


def test_opener_only_row_yields_no_lead(db, user):
    # A two-stage opener row has an empty `message`; the run is not yet coached.
    a = _activity(db, user)
    _report(db, a, message="")
    assert get_displayable_report_leads(db, [a]) == {}


def test_malformed_report_does_not_break_the_list(db, user):
    a = _activity(db, user)
    row = _report(db, a)
    row.report = "not a dict"
    db.commit()
    assert get_displayable_report_leads(db, [a]) == {}  # no exception


def test_leads_are_matched_to_their_own_activity(db, user):
    a1 = _activity(db, user, offset_days=0, name="Run A")
    a2 = _activity(db, user, offset_days=1, name="Run B")
    a3 = _activity(db, user, offset_days=2, name="Run C")
    _report(db, a1, message="Lead for A.")
    _report(db, a2, message="Lead for B.")
    leads = get_displayable_report_leads(db, [a1, a2, a3])
    assert leads == {a1.id: "Lead for A.", a2.id: "Lead for B."}


def test_one_query_regardless_of_page_size(db, user):
    """Batched on purpose: this runs on every app open, for 20 activities by
    default. A per-row `get_displayable_report_row` would be N+1 here."""
    activities = [_activity(db, user, offset_days=i) for i in range(12)]
    for a in activities:
        _report(db, a)

    statements = []
    from sqlalchemy import event

    def record(conn, cursor, stmt, *args):
        if "coach_reports" in stmt:
            statements.append(stmt)

    event.listen(db.bind, "before_cursor_execute", record)
    try:
        leads = get_displayable_report_leads(db, activities)
    finally:
        event.remove(db.bind, "before_cursor_execute", record)

    assert len(leads) == 12
    assert len(statements) == 1, f"expected 1 coach_reports query, got {len(statements)}"


def test_empty_page_does_not_query_at_all(db, user):
    assert get_displayable_report_leads(db, []) == {}


# --- endpoint wiring ----------------------------------------------------------
#
# The tests above exercise the projection. These prove the WIRING: the schema
# field exists, the router populates it, and it survives serialisation. #684
# serialises the list by hand (jsonable_encoder over pre-validated models) rather
# than via response_model, so a new field reaching the client is not automatic.


def test_endpoint_exposes_the_coach_lead(client, db):
    u = User(id=uuid4(), email=f"{uuid4()}@x.dev")
    db.add(u)
    db.commit()
    a = _activity(db, u, name="Coached Run")
    _report(db, a, message=f"{_LEAD} Trailing sentence.")

    resp = client.get("/api/activities")
    assert resp.status_code == 200, resp.text
    item = next(i for i in resp.json() if i["name"] == "Coached Run")
    assert item["coach_lead"] == _LEAD


def test_endpoint_returns_null_lead_for_an_uncoached_run(client, db):
    u = User(id=uuid4(), email=f"{uuid4()}@x.dev")
    db.add(u)
    db.commit()
    _activity(db, u, name="Uncoached Run")

    resp = client.get("/api/activities")
    item = next(i for i in resp.json() if i["name"] == "Uncoached Run")
    assert item["coach_lead"] is None
