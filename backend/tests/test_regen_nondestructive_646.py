"""#646: coach-report "Re-run" is a non-destructive fix-refresh.

Three coupled defects fixed here:
1. RE-ANALYZE FIRST — the regen job re-derives analysis from the activity's
   already-stored streams (no Strava call) so the report reflects current deployed
   analysis code, skipping gracefully when there are no stored streams.
2. PRESERVE THE ORIGINAL — a force regen archives the prior current row
   (superseded_at set, its report + context_pack snapshot immutable) and inserts a
   new current row, instead of overwriting in place.
3. STAMP — the regenerated (current) report carries a regenerated-on timestamp and
   the runner-memory as-of date, so a report regenerated with hindsight is honest.
"""

from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from app.core.config import settings
from app.jobs.reanalyze_history import reanalyze_activity_if_streams
from app.models import ActivityStream, RunnerMemory
from app.models.coach_report import CoachReport
from app.services.coach.service import (
    SCHEMA_VERSION,
    get_active_report_row,
    get_displayable_report_row,
    get_or_generate_coach_report,
)

# Reuse the structured single-shot seed + LLM mock (schema 1.2, easy to drive).
from tests.test_coach_report_versioning import _mock_llm, _seed_activity

ACTIVE_PROMPT_ID = "coach_report_v10"


@pytest.fixture(autouse=True)
def _pin_structured_prompt(monkeypatch):
    monkeypatch.setattr(settings, "COACH_PROMPT_ID", ACTIVE_PROMPT_ID)


def _seed_original_report(db, activity, *, context_pack) -> CoachReport:
    """A complete, non-fallback current report with a distinctive context_pack — the
    point-in-time snapshot a Re-run must preserve."""
    row = CoachReport(
        activity_id=activity.id,
        prompt_id=ACTIVE_PROMPT_ID,
        schema_version=SCHEMA_VERSION,
        report={
            "key_takeaways": [{"text": "Original takeaway."}],
            "next_steps": [{"action": "Rest", "details": "Easy", "why": "Recovery"}],
            "risks": [],
            "questions": [],
        },
        meta={
            "confidence": "medium", "model_id": "test-model",
            "prompt_id": ACTIVE_PROMPT_ID, "schema_version": SCHEMA_VERSION,
            "input_hash": "deadbeef",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "policy_violations": [],
        },
        context_pack=context_pack,
        is_fallback=False,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


# --- (c) re-analyze from stored streams --------------------------------------


def test_reanalyze_runs_when_streams_present(db):
    activity = _seed_activity(db)
    db.add(ActivityStream(activity_id=activity.id, stream_type="heartrate", data=[120, 130, 140]))
    db.commit()
    with patch("app.jobs.reanalyze_history.analyze") as analyze:
        ran = reanalyze_activity_if_streams(db, str(activity.id))
    assert ran is True
    analyze.assert_called_once()
    assert analyze.call_args.args[1] == str(activity.id)  # re-derive THIS activity


def test_reanalyze_skips_gracefully_without_streams(db):
    activity = _seed_activity(db)  # summary-only import: no ActivityStream rows
    with patch("app.jobs.reanalyze_history.analyze") as analyze:
        ran = reanalyze_activity_if_streams(db, str(activity.id))
    assert ran is False
    analyze.assert_not_called()  # never fabricate analysis / never call Strava


# --- (a) preserve the original + (b) exactly one current row ------------------


@pytest.mark.asyncio
async def test_force_regen_preserves_original_context_pack_as_archive(db):
    activity = _seed_activity(db)
    snapshot = {"memory": {"lately": "as of the original vantage point"}}
    original = _seed_original_report(db, activity, context_pack=snapshot)
    original_id = original.id

    ctx, _ = _mock_llm()
    with ctx:
        await get_or_generate_coach_report(db, str(activity.id), force=True)

    # The original survives as an immutable audit copy: superseded_at set, its
    # report + point-in-time context_pack snapshot intact.
    archived = db.query(CoachReport).filter(CoachReport.id == original_id).first()
    assert archived is not None
    assert archived.superseded_at is not None
    assert archived.context_pack == snapshot
    assert archived.report["key_takeaways"][0]["text"] == "Original takeaway."


@pytest.mark.asyncio
async def test_after_regen_exactly_one_current_row_is_served(db):
    activity = _seed_activity(db)
    original = _seed_original_report(db, activity, context_pack={"v": "orig"})
    original_id = original.id

    ctx, _ = _mock_llm()
    with ctx:
        await get_or_generate_coach_report(db, str(activity.id), force=True)

    # Exactly one CURRENT row, and it is the fresh one — not the archived original.
    current = db.query(CoachReport).filter(
        CoachReport.activity_id == activity.id,
        CoachReport.superseded_at.is_(None),
    ).all()
    assert len(current) == 1
    assert current[0].id != original_id

    # Both the active and displayable read paths serve the current row, never the archive.
    assert get_active_report_row(db, activity.id).id == current[0].id
    assert get_displayable_report_row(db, activity.id).id == current[0].id
    assert get_active_report_row(db, activity.id).id != original_id


# --- (d) the regeneration stamp ----------------------------------------------


@pytest.mark.asyncio
async def test_current_report_carries_regeneration_stamp(db):
    activity = _seed_activity(db)
    _seed_original_report(db, activity, context_pack={"v": "orig"})
    # A runner-memory profile grounded through a known date — the "memory as of" the
    # regenerated report should stamp, so its hindsight is made honest.
    grounded = datetime(2026, 7, 7, 12, 0, 0, tzinfo=timezone.utc)
    db.add(RunnerMemory(user_id=activity.user_id, profile={}, grounded_through=grounded))
    db.commit()

    ctx, _ = _mock_llm()
    with ctx:
        await get_or_generate_coach_report(db, str(activity.id), force=True)

    read = get_active_report_row(db, activity.id)
    meta = read.meta
    from app.schemas.coach import CoachReportMeta
    parsed = CoachReportMeta.model_validate(meta)
    assert parsed.regenerated_at is not None  # stamped as a Re-run
    assert parsed.memory_as_of is not None
    assert parsed.memory_as_of.date() == grounded.date()  # memory as-of date carried


# --- (e) the normal first-generation path is unchanged -----------------------


@pytest.mark.asyncio
async def test_first_generation_has_no_archive_and_no_stamp(db):
    activity = _seed_activity(db)  # no prior report
    ctx, _ = _mock_llm()
    with ctx:
        await get_or_generate_coach_report(db, str(activity.id))

    rows = db.query(CoachReport).filter(CoachReport.activity_id == activity.id).all()
    assert len(rows) == 1
    assert rows[0].superseded_at is None  # exactly one current row, nothing archived
    from app.schemas.coach import CoachReportMeta
    parsed = CoachReportMeta.model_validate(rows[0].meta)
    assert parsed.regenerated_at is None  # a first generation is not a regeneration
    assert parsed.memory_as_of is None
