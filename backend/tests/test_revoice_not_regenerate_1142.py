"""#1142: a report stale only because the runner changed voice is RE-VOICED, not
regenerated.

Since ADR 0030 the report is written voiceless and the voice is a rewrite of that
finished text, so a voice change can never need new substance. These tests drive
the real generation path once (stubbed Anthropic client) to get a row the system
itself stored, change the runner's voice, then run the worker job the panel's
voice-stale read triggers, and check what the runner would see and what ran.
"""

import asyncio
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4


from fastapi.testclient import TestClient

from app.core.config import settings
from app.jobs import process_new_activity as job_mod
from app.models import Activity, CoachReport, DerivedMetric, StravaAccount, User, UserProfile
from app.models.coaching_relationship import CoachingRelationship
from app.services.coach.llm import MessageResult
from app.services.coach.receipt_voice import voice_fingerprint
from app.services.coach.service import (
    get_active_report_row,
    get_or_generate_coach_report,
    report_voice_stale,
)
from app.services.coach.voice import PRESETS, resolve_voice

PROD_PROMPT = "coach_message_lean_grouped_v12"

BASELINE = "Solid easy run, nicely controlled. Keep the next one just as relaxed."
# Digit-free on purpose: the rewrite refuses any number the baseline lacks, so a
# fixture inventing one would be discarded and a test pass for the wrong reason.
ROAST_TEXT = "Look at you, running easy like a grown-up. Do it again, just as relaxed."
CORNERMAN_TEXT = "That was beautifully controlled, champ. Keep the next one just as relaxed."


def _seed(db) -> Activity:
    user = User(email=f"u-{uuid4()}@example.com")
    db.add(user)
    db.commit()
    db.add(UserProfile(
        user_id=user.id, goal_type="general", experience_level="intermediate",
        weekly_days_available=4, max_hr=190,
    ))
    db.add(StravaAccount(
        user_id=user.id, strava_athlete_id=abs(hash(str(user.id))) % 10**9,
        access_token="t", refresh_token="r", expires_at=9999999999, scope="read",
    ))
    activity = Activity(
        user_id=user.id, strava_activity_id=abs(hash(str(uuid4()))) % 10**9,
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
    db.add(CoachingRelationship(user_id=user.id, voice_preset="roast"))
    db.commit()
    db.refresh(activity)
    return activity


def _report_result() -> MessageResult:
    return MessageResult(
        content_blocks=[
            {"type": "text", "text": BASELINE},
            {"type": "tool_use", "name": "record_coach_tail", "input": {
                "headline": "Easy run, well judged",
                "next_steps": [{
                    "action": "Repeat the easy effort",
                    "details": "Same relaxed pace next time.",
                    "why": "Aerobic base builds on easy volume.",
                }],
                "risks": [],
                "questions": [{
                    "question": "How did your legs feel afterwards?",
                    "reason": "No check-in yet.",
                }],
            }},
        ],
        stop_reason="end_turn",
    )


def _answer(voiced: str) -> str:
    return f"<findings>\n- VERDICT: easy run well judged\n</findings>\n<report>\n{voiced}\n</report>"


def _client(voiced: str) -> AsyncMock:
    fake = AsyncMock()
    fake.generate_coach_message = AsyncMock(return_value=_report_result())
    fake.generate_json_with_usage = AsyncMock(return_value=(_answer(voiced), None))
    return fake


def _generated_then_voice_changed(db, monkeypatch) -> Activity:
    """A real stored report in the Roast's voice, then the runner picks the Cornerman."""
    monkeypatch.setattr(settings, "COACH_PROMPT_ID", PROD_PROMPT)
    activity = _seed(db)
    with patch("app.services.coach.turn.AnthropicClient", return_value=_client(ROAST_TEXT)), \
         patch("app.services.coach.service.enqueue_memory_update"):
        asyncio.run(get_or_generate_coach_report(db, str(activity.id)))
    row = get_active_report_row(db, activity.id)
    assert row.report["voiced_message"] == ROAST_TEXT  # precondition: it was voiced

    rel = db.query(CoachingRelationship).filter_by(user_id=activity.user_id).one()
    rel.voice_preset = "cornerman"
    db.commit()
    assert report_voice_stale(db, row) is True  # precondition: voice-only stale
    return activity


def _run_job(db, func, activity, fake):
    """Run an RQ entrypoint against the test session, with the LLM stubbed and the
    memory pass and the re-analysis observed."""
    memory = MagicMock()
    reanalyze = MagicMock(return_value=False)
    with patch.object(job_mod, "SessionLocal", return_value=db), \
         patch.object(db, "close"), \
         patch.object(job_mod, "reanalyze_activity_if_streams", reanalyze), \
         patch("app.services.coach.service.enqueue_memory_update", memory), \
         patch("app.services.coach.turn.AnthropicClient", return_value=fake):
        func(str(activity.id))
    db.expire_all()
    return memory, reanalyze


def _rows(db, activity):
    return db.query(CoachReport).filter(CoachReport.activity_id == activity.id).all()


def test_voice_only_stale_report_is_revoiced_without_regenerating(db, monkeypatch):
    activity = _generated_then_voice_changed(db, monkeypatch)
    before = dict(get_active_report_row(db, activity.id).report)
    rows_before = len(_rows(db, activity))

    fake = _client(CORNERMAN_TEXT)
    memory, reanalyze = _run_job(db, job_mod.revoice_report_job, activity, fake)

    # No report generation, no memory pass, no re-analysis: only the rewrite ran.
    fake.generate_coach_message.assert_not_awaited()
    fake.generate_json_with_usage.assert_awaited_once()
    memory.assert_not_called()
    reanalyze.assert_not_called()

    row = get_active_report_row(db, activity.id)
    after = row.report
    # The runner now reads the report in the new voice...
    assert after["voiced_message"] == CORNERMAN_TEXT
    assert row.meta["voice_name"] == PRESETS["cornerman"].name
    # ...and the substance is exactly what they already read.
    for key in ("message", "headline", "next_steps", "risks", "questions"):
        assert after[key] == before[key], key
    assert after["message"] == BASELINE
    # Same row, not a superseding regeneration.
    assert len(_rows(db, activity)) == rows_before
    assert row.superseded_at is None
    # Current voice stamped, so the next read is not stale again.
    current = voice_fingerprint(resolve_voice(
        db.query(CoachingRelationship).filter_by(user_id=activity.user_id).one()
    ))
    assert row.voice_key == current
    assert report_voice_stale(db, row) is False


def test_a_failed_rewrite_serves_the_baseline_and_does_not_loop(db, monkeypatch):
    activity = _generated_then_voice_changed(db, monkeypatch)

    fake = _client(CORNERMAN_TEXT)
    fake.generate_json_with_usage = AsyncMock(side_effect=RuntimeError("boom"))
    memory, _ = _run_job(db, job_mod.revoice_report_job, activity, fake)

    fake.generate_coach_message.assert_not_awaited()
    memory.assert_not_called()
    row = get_active_report_row(db, activity.id)
    # The old voice's text is gone; the runner reads the voiceless baseline.
    assert row.report["voiced_message"] is None
    assert row.report["message"] == BASELINE
    assert row.meta["voice_rewrite"].startswith("transport_error")
    # Stamped current anyway, so the panel does not re-trigger on the next view.
    assert report_voice_stale(db, row) is False


def test_a_prompt_stale_report_still_regenerates(db, monkeypatch):
    """A row from another prompt version is not voice-stale; the job keeps today's
    full regeneration for anything it cannot honestly re-voice."""
    activity = _generated_then_voice_changed(db, monkeypatch)
    monkeypatch.setattr(settings, "COACH_PROMPT_ID", "coach_message_lean_grouped_v11")

    gen = AsyncMock(return_value=None)
    with patch.object(job_mod, "get_or_generate_coach_report", gen):
        _run_job(db, job_mod.revoice_report_job, activity, _client(CORNERMAN_TEXT))
    gen.assert_awaited_once()
    assert gen.await_args.kwargs.get("force") is True


def test_a_report_with_no_voiceless_baseline_regenerates(db, monkeypatch):
    """A row that never went through the voiceless-then-rewrite pipeline (no
    voice_rewrite stamp: pre-#822, when voice steered generation) has no voiceless
    baseline to re-say, so it regenerates as before."""
    activity = _generated_then_voice_changed(db, monkeypatch)
    row = get_active_report_row(db, activity.id)
    row.meta = {k: v for k, v in row.meta.items() if k != "voice_rewrite"}
    row.voice_key = None
    db.commit()

    gen = AsyncMock(return_value=None)
    fake = _client(CORNERMAN_TEXT)
    with patch.object(job_mod, "get_or_generate_coach_report", gen):
        _run_job(db, job_mod.revoice_report_job, activity, fake)
    gen.assert_awaited_once()
    assert gen.await_args.kwargs.get("force") is True
    fake.generate_json_with_usage.assert_not_awaited()


def test_revoice_endpoint_enqueues_the_revoice_job(client: TestClient, db):
    from tests.test_coach_report_versioning import _seed_activity

    activity = _seed_activity(db)
    fake_queue = MagicMock()
    fake_queue.connection.set.return_value = True
    with patch("app.core.queue.queue", fake_queue):
        res = client.post(f"/api/activities/{activity.id}/coach-report/revoice")
    assert res.status_code == 202
    call = fake_queue.enqueue.call_args
    assert call.args[0] is job_mod.revoice_report_job
    assert call.args[1] == str(activity.id)
    assert call.kwargs.get("job_timeout") == settings.RQ_JOB_TIMEOUT_SECONDS
    assert call.kwargs.get("job_id") == f"coach-revoice-{activity.id}"
