"""#261: display-safe coach-report read path.

Flipping COACH_PROMPT_ID (e.g. to activate P1.1 voice coach_message_v3) leaves
every prior-version report a cache miss. The DISPLAY path must fall back to the
most recent cached report of any version instead of 404-ing or triggering a
synchronous regeneration that exceeds the gateway timeout (#260). The M0
versioned-cache identity used for regeneration is unchanged: get_active_report_row
and get_or_generate_coach_report still key strictly on the active version, so the
background pipeline still generates the active version for new activities.
"""

from datetime import datetime, timezone
from unittest.mock import patch
from uuid import uuid4

import pytest

from app.core.config import settings
from app.models.coach_report import CoachReport
from app.services.coach.service import (
    get_active_report_row,
    get_displayable_report_row,
)

from tests.test_coach_report_versioning import _seed_activity


def _insert_report(db, activity_id, prompt_id, schema_version, *, message=None):
    report = (
        {"message": message, "headline": "Cached verdict", "next_steps": [], "risks": [], "questions": []}
        if message is not None
        else {"key_takeaways": [{"text": "Cached takeaway."}], "next_steps": [], "risks": [], "questions": []}
    )
    row = CoachReport(
        activity_id=activity_id, report=report,
        meta={"confidence": "medium", "model_id": "m", "prompt_id": prompt_id,
              "schema_version": schema_version, "input_hash": "x",
              "generated_at": datetime.now(timezone.utc).isoformat(), "policy_violations": []},
        context_pack={}, prompt_id=prompt_id, schema_version=schema_version, is_fallback=False,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


class TestDisplayableReportRow:
    def test_prefers_active_version(self, db, monkeypatch):
        monkeypatch.setattr(settings, "COACH_PROMPT_ID", "coach_message_v3")
        activity = _seed_activity(db)
        _insert_report(db, activity.id, "coach_message_v2", "2.0", message="old v2")
        active = _insert_report(db, activity.id, "coach_message_v3", "2.0", message="new v3")
        got = get_displayable_report_row(db, str(activity.id))
        assert got.id == active.id  # the active version wins when present

    def test_falls_back_to_prior_version_when_active_missing(self, db, monkeypatch):
        # The flip scenario: active is coach_message_v3, but only a v2 report exists.
        monkeypatch.setattr(settings, "COACH_PROMPT_ID", "coach_message_v3")
        activity = _seed_activity(db)
        v2 = _insert_report(db, activity.id, "coach_message_v2", "2.0", message="old v2")
        assert get_active_report_row(db, str(activity.id)) is None  # genuine cache miss
        got = get_displayable_report_row(db, str(activity.id))
        assert got is not None and got.id == v2.id  # prior version still displayable

    def test_none_when_no_report_at_all(self, db, monkeypatch):
        monkeypatch.setattr(settings, "COACH_PROMPT_ID", "coach_message_v3")
        activity = _seed_activity(db)
        assert get_displayable_report_row(db, str(activity.id)) is None


class TestEndpointDisplaySafe:
    def test_get_after_flip_returns_prior_report_without_regenerating(self, client, db, monkeypatch):
        # The #261 regression: under a flipped prompt, viewing an activity that has a
        # prior-version report returns it (200) instead of 404 or a slow regen.
        monkeypatch.setattr(settings, "COACH_PROMPT_ID", "coach_message_v3")
        activity = _seed_activity(db)
        _insert_report(db, activity.id, "coach_message_v2", "2.0", message="prior report body")

        # AnthropicClient must never be constructed on the display path (no regen, no 504).
        with patch("app.services.coach.service.AnthropicClient",
                   side_effect=AssertionError("must not call the LLM on the display path")):
            res = client.get(f"/api/activities/{activity.id}/coach-report?generate=true")
        assert res.status_code == 200
        assert res.json()["report"]["message"] == "prior report body"

    def test_generate_false_still_404s_when_no_report_at_all(self, client, db, monkeypatch):
        monkeypatch.setattr(settings, "COACH_PROMPT_ID", "coach_message_v3")
        activity = _seed_activity(db)
        res = client.get(f"/api/activities/{activity.id}/coach-report?generate=false")
        assert res.status_code == 404
