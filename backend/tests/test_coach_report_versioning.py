"""N1: version-aware CoachReport cache.

Cache identity moves from unique(activity_id) to
(activity_id, prompt_id, schema_version). Prior versions are retained when the
active version moves; reading auto-regenerates the active version; force
regenerates the active version without destroying prior versions.
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from app.services.coach.llm import Usage
from sqlalchemy.exc import IntegrityError

from app.core.config import settings
from app.models import Activity, DerivedMetric, StravaAccount, User, UserProfile
from app.models.coach_report import CoachReport
from app.services.coach.service import (
    SCHEMA_VERSION,
    get_or_generate_coach_report,
)

# This suite exercises the STRUCTURED single-shot cache path (schema 1.2), so it
# pins the structured prompt rather than following the active default, which now
# tracks the feature-bearing message prompt (#424).
ACTIVE_PROMPT_ID = "coach_report_v10"


@pytest.fixture(autouse=True)
def _pin_structured_prompt(monkeypatch):
    monkeypatch.setattr(settings, "COACH_PROMPT_ID", ACTIVE_PROMPT_ID)

_VALID_REPORT = {
    "key_takeaways": [{"text": "Cached takeaway one."}, {"text": "Cached takeaway two."}],
    "next_steps": [{"action": "Rest", "details": "Easy day", "why": "Recovery"}],
    "risks": [],
    "questions": [],
}

_HAPPY_JSON = (
    '{"key_takeaways":[{"text":"Freshly generated one."},{"text":"Freshly generated two."}],'
    '"next_steps":[{"action":"Keep going","details":"Stay easy","why":"Build base"}],'
    '"questions":[{"question":"How did you feel?","reason":"No check-in data"}]}'
)


def _seed_activity(db) -> Activity:
    user = User(email=f"u-{uuid4()}@example.com")
    db.add(user)
    db.commit()
    db.add(UserProfile(user_id=user.id, goal_type="general", experience_level="intermediate", weekly_days_available=4, max_hr=190))
    db.add(StravaAccount(user_id=user.id, strava_athlete_id=1, access_token="t", refresh_token="r", expires_at=9999999999, scope="read"))
    activity = Activity(
        user_id=user.id, strava_activity_id=42,
        start_date=datetime(2026, 5, 27, 10, 0, 0), type="Run", name="Test run",
        distance_m=5000, moving_time_s=1500, elapsed_time_s=1500, elev_gain_m=10.0,
        avg_hr=140, raw_summary={},
    )
    db.add(activity)
    db.commit()
    db.add(DerivedMetric(
        activity_id=activity.id, effort="easy", structure="continuous",
        duration_class="standard", effort_score=50.0, flags=[],
        confidence="medium", confidence_reasons=[],
    ))
    db.commit()
    db.refresh(activity)
    return activity


def _meta(prompt_id, schema_version):
    return {
        "confidence": "medium", "model_id": "test-model", "prompt_id": prompt_id,
        "schema_version": schema_version, "input_hash": "deadbeef",
        "generated_at": datetime.now(timezone.utc).isoformat(), "policy_violations": [],
    }


def _insert_report(db, activity_id, prompt_id, schema_version):
    row = CoachReport(
        activity_id=activity_id, report=dict(_VALID_REPORT),
        meta=_meta(prompt_id, schema_version), context_pack={},
        prompt_id=prompt_id, schema_version=schema_version, is_fallback=False,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _mock_llm():
    fake = AsyncMock()
    fake.generate_json_with_usage = AsyncMock(
        return_value=(_HAPPY_JSON, Usage())
    )
    return patch("app.services.coach.turn.AnthropicClient", return_value=fake), fake


# --- model / schema -------------------------------------------------------

@pytest.mark.asyncio
async def test_version_columns_persisted_on_generate(db):
    activity = _seed_activity(db)
    ctx, _ = _mock_llm()
    with ctx:
        await get_or_generate_coach_report(db, str(activity.id))
    row = db.query(CoachReport).filter(CoachReport.activity_id == activity.id).first()
    assert row.prompt_id == ACTIVE_PROMPT_ID
    assert row.schema_version == SCHEMA_VERSION


def test_same_version_duplicate_rejected_by_unique(db):
    activity = _seed_activity(db)
    _insert_report(db, activity.id, ACTIVE_PROMPT_ID, SCHEMA_VERSION)
    with pytest.raises(IntegrityError):
        _insert_report(db, activity.id, ACTIVE_PROMPT_ID, SCHEMA_VERSION)
    db.rollback()  # clear the aborted transaction so teardown is clean


def test_two_versions_coexist(db):
    activity = _seed_activity(db)
    _insert_report(db, activity.id, ACTIVE_PROMPT_ID, "1.0")
    _insert_report(db, activity.id, ACTIVE_PROMPT_ID, SCHEMA_VERSION)
    count = db.query(CoachReport).filter(CoachReport.activity_id == activity.id).count()
    assert count == 2


# --- cache read behaviour -------------------------------------------------

@pytest.mark.asyncio
async def test_cache_hit_same_version_does_not_call_llm(db):
    activity = _seed_activity(db)
    _insert_report(db, activity.id, ACTIVE_PROMPT_ID, SCHEMA_VERSION)
    ctx, fake = _mock_llm()
    with ctx:
        result = await get_or_generate_coach_report(db, str(activity.id))
    assert result is not None
    fake.generate_json_with_usage.assert_not_called()
    # Returned the cached content, not a fresh generation.
    assert result.report.key_takeaways[0].text == "Cached takeaway one."


@pytest.mark.asyncio
async def test_version_bump_regenerates_and_retains_old_version(db):
    activity = _seed_activity(db)
    old = _insert_report(db, activity.id, ACTIVE_PROMPT_ID, "1.0")  # stale version
    ctx, fake = _mock_llm()
    with ctx:
        result = await get_or_generate_coach_report(db, str(activity.id))
    fake.generate_json_with_usage.assert_called_once()
    # New active-version row exists alongside the retained old-version row.
    rows = db.query(CoachReport).filter(CoachReport.activity_id == activity.id).all()
    versions = {r.schema_version for r in rows}
    assert versions == {"1.0", SCHEMA_VERSION}
    assert db.query(CoachReport).filter(CoachReport.id == old.id).first() is not None
    assert result.report.key_takeaways[0].text == "Freshly generated one."


# --- force ----------------------------------------------------------------

@pytest.mark.asyncio
async def test_force_regenerates_nondestructively_archiving_prior(db):
    # #646: a force "Re-run" over a complete report is NON-DESTRUCTIVE. It archives
    # the prior current row (superseded_at set — an immutable audit copy of what the
    # coach saw) and inserts the regenerated report as a NEW current row.
    activity = _seed_activity(db)
    old = _insert_report(db, activity.id, ACTIVE_PROMPT_ID, "1.0")
    active = _insert_report(db, activity.id, ACTIVE_PROMPT_ID, SCHEMA_VERSION)
    active_id = active.id
    ctx, fake = _mock_llm()
    with ctx:
        result = await get_or_generate_coach_report(db, str(activity.id), force=True)
    fake.generate_json_with_usage.assert_called_once()
    # Prior (1.0) version survives the force.
    assert db.query(CoachReport).filter(CoachReport.id == old.id).first() is not None

    # The prior active row is preserved AS AN ARCHIVE (superseded_at set, original
    # content intact) — the point-in-time snapshot the audit/review loop relies on.
    archived = db.query(CoachReport).filter(CoachReport.id == active_id).first()
    assert archived is not None
    assert archived.superseded_at is not None
    assert archived.report["key_takeaways"][0]["text"] == "Cached takeaway one."

    # Exactly ONE current active-version row, and it is the freshly generated one
    # (a new physical row, not the archived original).
    current_rows = db.query(CoachReport).filter(
        CoachReport.activity_id == activity.id,
        CoachReport.schema_version == SCHEMA_VERSION,
        CoachReport.superseded_at.is_(None),
    ).all()
    assert len(current_rows) == 1
    assert current_rows[0].id != active_id  # new row, original preserved
    assert result.report.key_takeaways[0].text == "Freshly generated one."
    # Two schema-2.0-family rows now coexist for this activity: 1 archived + 1 current.
    assert db.query(CoachReport).filter(
        CoachReport.activity_id == activity.id,
        CoachReport.schema_version == SCHEMA_VERSION,
    ).count() == 2


def test_force_via_api_does_not_destroy_prior_versions(client, db):
    activity = _seed_activity(db)
    old = _insert_report(db, activity.id, ACTIVE_PROMPT_ID, "1.0")
    _insert_report(db, activity.id, ACTIVE_PROMPT_ID, SCHEMA_VERSION)
    ctx, _ = _mock_llm()
    with ctx:
        resp = client.get(f"/api/activities/{activity.id}/coach-report?force=true")
    assert resp.status_code == 200
    # The destructive old behaviour wiped the only report; versioning must retain 1.0.
    assert db.query(CoachReport).filter(CoachReport.id == old.id).first() is not None


# --- #273: force-regen crash safety (no delete-then-die window) -----------


@pytest.mark.asyncio
async def test_force_regen_worker_death_preserves_prior_report(db):
    # #273: an interrupted force-regen on the single-shot path must NOT lose the
    # report. The old path deleted+committed the active row before the LLM call,
    # so a worker death between the committed delete and the write left zero rows.
    activity = _seed_activity(db)
    active = _insert_report(db, activity.id, ACTIVE_PROMPT_ID, SCHEMA_VERSION)
    active_id = active.id

    # The worker is killed during the regeneration LLM step: _generate_structured
    # never returns (an uncaught interruption, not a handled fallback).
    with patch("app.services.coach.turn.AnthropicClient"), \
         patch("app.services.coach.service._generate_structured",
               side_effect=RuntimeError("worker killed mid-regen")):
        with pytest.raises(RuntimeError):
            await get_or_generate_coach_report(db, str(activity.id), force=True)

    survived = db.query(CoachReport).filter(CoachReport.id == active_id).first()
    assert survived is not None, \
        "single-shot force-regen interruption lost the report entirely (#273)"
    assert survived.report["key_takeaways"][0]["text"] == "Cached takeaway one."


# --- #279: a failed force-regen must not clobber a prior good report ------


@pytest.mark.asyncio
async def test_force_regen_fallback_preserves_prior_good_report(db):
    # #279: a force-regenerate whose generation fails (unparseable -> fallback) must
    # KEEP the prior complete, non-fallback report rather than overwrite it.
    activity = _seed_activity(db)
    good = _insert_report(db, activity.id, ACTIVE_PROMPT_ID, SCHEMA_VERSION)
    good_id = good.id

    # generate_json returns unparseable content -> _generate_structured falls back.
    fake = AsyncMock()
    fake.generate_json_with_usage = AsyncMock(
        return_value=("not valid json", Usage())
    )
    with patch("app.services.coach.turn.AnthropicClient", return_value=fake):
        result = await get_or_generate_coach_report(db, str(activity.id), force=True)

    survived = db.query(CoachReport).filter(CoachReport.id == good_id).first()
    assert survived is not None
    assert survived.is_fallback is False  # NOT downgraded to a fallback
    assert survived.report["key_takeaways"][0]["text"] == "Cached takeaway one."
    assert result is not None  # returns the preserved good report
