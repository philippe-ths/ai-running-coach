"""#1142: a report stale only because the runner changed voice is RE-VOICED, not
regenerated.

Since ADR 0030 the report is written voiceless and the voice is a rewrite of that
finished text, so a voice change can never need new substance. These tests drive
the real generation path once (stubbed Anthropic client) to get a row the system
itself stored, change the runner's voice, then run the worker job the panel's
voice-stale read triggers, and check what the runner would see and what ran.
"""

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.jobs import process_new_activity as job_mod
from app.models import Activity, CoachReport, DerivedMetric, StravaAccount, User, UserProfile
from app.models.coaching_relationship import CoachingRelationship
from app.schemas.coach import CoachMessageReport
from app.schemas.coach_context import load_stored_pack
from app.services.coach.llm import MessageResult
from app.services.coach.receipt_voice import voice_fingerprint
from app.services.coach.service import (
    _apply_voice,
    _MsgAttempt,
    get_active_report_row,
    get_or_generate_coach_report,
    report_voice_stale,
)
from app.services.coach.validator import validate_message_policy
from app.services.coach.voice import PRESETS, resolve_voice

PROD_PROMPT = "coach_message_lean_grouped_v12"

BASELINE = "Solid easy run, nicely controlled. Keep the next 2 runs just as relaxed."
# Every figure in a rewrite must be sourced by the baseline or the rewrite is
# refused, so these fixtures only reuse the baseline's "2".
ROAST_TEXT = "Look at you, running easy like a grown-up. Do the next 2 runs just as relaxed."
CORNERMAN_TEXT = "That was beautifully controlled, champ. Keep the next 2 runs just as relaxed."

# The only stored fields a re-voicing may change.
VOICE_REPORT_KEYS = {"voiced_message", "voiced_opener_message"}
VOICE_META_KEYS = {"voice_name", "voice_rewrite", "revoiced_at"}


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


def _client(*voiced: str) -> AsyncMock:
    """A stub client: the report call returns the baseline; successive rewrite calls
    return each `voiced` text in turn (the last one repeats)."""
    fake = AsyncMock()
    fake.generate_coach_message = AsyncMock(return_value=_report_result())
    answers = [(_answer(v), None) for v in voiced]

    def _rewrite(*_a, **_k):
        return answers.pop(0) if len(answers) > 1 else answers[0]

    fake.generate_json_with_usage = AsyncMock(side_effect=_rewrite)
    return fake


def _relationship(db, activity) -> CoachingRelationship:
    return db.query(CoachingRelationship).filter_by(user_id=activity.user_id).one()


def _generate(db, activity, *, force=False):
    with patch("app.services.coach.turn.AnthropicClient", return_value=_client(ROAST_TEXT)), \
         patch("app.services.coach.service.enqueue_memory_update"):
        asyncio.run(get_or_generate_coach_report(db, str(activity.id), force=force))


def _generated_then_voice_changed(db, monkeypatch) -> Activity:
    """A real stored report in the Roast's voice, then the runner picks the Cornerman."""
    monkeypatch.setattr(settings, "COACH_PROMPT_ID", PROD_PROMPT)
    activity = _seed(db)
    _generate(db, activity)
    row = get_active_report_row(db, activity.id)
    assert row.report["voiced_message"] == ROAST_TEXT  # precondition: it was voiced

    _relationship(db, activity).voice_preset = "cornerman"
    db.commit()
    assert report_voice_stale(db, row) is True  # precondition: voice-only stale
    return activity


def _snapshot(row):
    return dict(row.report), dict(row.meta), row.voice_key, row.superseded_at


def _run_job(db, activity, fake, *, regen=None):
    """Run the revoice RQ entrypoint against the test session, with the LLM stubbed,
    the memory pass and re-analysis observed, and the regenerate path observed."""
    memory = MagicMock()
    reanalyze = MagicMock(return_value=False)
    regen = regen if regen is not None else AsyncMock(return_value=None)
    with patch.object(job_mod, "SessionLocal", return_value=db), \
         patch.object(db, "close"), \
         patch.object(job_mod, "reanalyze_activity_if_streams", reanalyze), \
         patch.object(job_mod, "get_or_generate_coach_report", regen), \
         patch("app.services.coach.service.enqueue_memory_update", memory), \
         patch("app.services.coach.turn.AnthropicClient", return_value=fake):
        job_mod.revoice_report_job(str(activity.id))
    db.expire_all()
    return memory, reanalyze, regen


def _rows(db, activity):
    return db.query(CoachReport).filter(CoachReport.activity_id == activity.id).all()


# --- the re-voicing itself ------------------------------------------------------


def test_voice_only_stale_report_is_revoiced_without_regenerating(db, monkeypatch):
    activity = _generated_then_voice_changed(db, monkeypatch)
    before_report, before_meta, _, _ = _snapshot(get_active_report_row(db, activity.id))
    rows_before = len(_rows(db, activity))

    fake = _client(CORNERMAN_TEXT)
    memory, reanalyze, regen = _run_job(db, activity, fake)

    # No report generation, no memory pass, no re-analysis: only the rewrite ran.
    fake.generate_coach_message.assert_not_awaited()
    regen.assert_not_awaited()
    fake.generate_json_with_usage.assert_awaited_once()
    memory.assert_not_called()
    reanalyze.assert_not_called()

    row = get_active_report_row(db, activity.id)
    assert row.report["voiced_message"] == CORNERMAN_TEXT
    assert row.meta["voice_name"] == PRESETS["cornerman"].name
    assert row.meta["voice_rewrite"].startswith("applied")
    assert row.meta["revoiced_at"] is not None
    # Nothing but the voice moved: substance, generation stamp, policy record.
    for key in set(before_report) - VOICE_REPORT_KEYS:
        assert row.report[key] == before_report[key], key
    for key in set(before_meta) - VOICE_META_KEYS:
        assert row.meta[key] == before_meta[key], key
    assert row.report["message"] == BASELINE
    # Same row, not a superseding regeneration.
    assert len(_rows(db, activity)) == rows_before
    assert row.superseded_at is None
    # Current voice stamped, so the next read is not stale again.
    assert row.voice_key == voice_fingerprint(resolve_voice(_relationship(db, activity)))
    assert report_voice_stale(db, row) is False


def test_both_stages_of_a_two_stage_row_are_revoiced(db, monkeypatch):
    activity = _generated_then_voice_changed(db, monkeypatch)
    row = get_active_report_row(db, activity.id)
    row.report = {
        **row.report,
        "opener_message": "Nice one, easy does it.",
        "voiced_opener_message": "Oh look, an easy run. Groundbreaking.",
    }
    db.commit()

    opener_voiced = "Easy does it, champ, nice one."
    fake = _client(opener_voiced, CORNERMAN_TEXT)
    _run_job(db, activity, fake)

    row = get_active_report_row(db, activity.id)
    assert fake.generate_json_with_usage.await_count == 2
    assert row.report["voiced_opener_message"] == opener_voiced
    assert row.report["voiced_message"] == CORNERMAN_TEXT
    assert row.report["opener_message"] == "Nice one, easy does it."


def test_a_rewrite_introducing_a_policy_violation_is_refused(db, monkeypatch):
    """The floor still judges the rewrite: a non-medical rule the baseline did not
    break (zone names for an uncalibrated runner) refuses it, the baseline is
    served, and the row is stamped current (a definitive refusal, no loop)."""
    activity = _generated_then_voice_changed(db, monkeypatch)
    row = get_active_report_row(db, activity.id)
    pack = load_stored_pack(row.context_pack, prompt_id=row.prompt_id)
    assert pack.metrics.zones_calibrated is False  # precondition for the rule

    _run_job(db, activity, _client("Tidy Z2 work, champ. Keep the next 2 runs just as relaxed."))

    row = get_active_report_row(db, activity.id)
    assert row.meta["voice_rewrite"].startswith("policy:uncalibrated_zone_reference")
    assert row.report["voiced_message"] is None  # the old voice is not kept either
    assert row.report["message"] == BASELINE
    assert report_voice_stale(db, row) is False


def test_a_rewrite_introducing_medical_overreach_is_refused(db, monkeypatch):
    activity = _generated_then_voice_changed(db, monkeypatch)
    _run_job(db, activity, _client("You have shin splints, champ. Keep the next 2 runs just as relaxed."))

    row = get_active_report_row(db, activity.id)
    assert row.meta["voice_rewrite"].startswith("policy:medical_overreach")
    assert row.report["voiced_message"] is None
    assert row.report["message"] == BASELINE


def test_a_baseline_over_todays_medical_floor_is_regenerated_not_revoiced(db, monkeypatch):
    """A report stored under older, looser rules may trip the medical floor today.
    Re-voicing it would judge the rewrite only on rules the baseline did not already
    break, exempting it from the medical check, so it regenerates instead."""
    activity = _generated_then_voice_changed(db, monkeypatch)
    row = get_active_report_row(db, activity.id)
    row.report = {**row.report, "message": "Sounds like shin splints, ease off the next 2 runs."}
    db.commit()
    before = _snapshot(get_active_report_row(db, activity.id))

    fake = _client("Shin splints, champ, so take ibuprofen and ease off the next 2 runs.")
    _, _, regen = _run_job(db, activity, fake)

    fake.generate_json_with_usage.assert_not_awaited()
    regen.assert_awaited_once()
    assert regen.await_args.kwargs.get("force") is True
    assert _snapshot(get_active_report_row(db, activity.id)) == before


def test_the_medical_floor_is_never_inherited_from_the_baseline(db, monkeypatch):
    """Even handed a baseline that already trips medical_overreach, the shared
    rewrite step refuses a rewrite that trips it."""
    activity = _generated_then_voice_changed(db, monkeypatch)
    row = get_active_report_row(db, activity.id)
    pack = load_stored_pack(row.context_pack, prompt_id=row.prompt_id)
    report = CoachMessageReport.model_validate(
        {**row.report, "message": "Sounds like shin splints, ease off the next 2 runs.",
         "voiced_message": None}
    )
    violations = validate_message_policy(report, pack)
    assert "medical_overreach" in {v.rule for v in violations}
    chosen = _MsgAttempt(report=report, raw_response="", truncated=False, violations=violations)

    fake = _client("You have shin splints, champ, ease off the next 2 runs.")
    with patch("app.services.coach.turn.AnthropicClient", return_value=fake):
        out, reason = asyncio.run(_apply_voice(
            chosen, pack, voice=resolve_voice(_relationship(db, activity)),
            user_id=activity.user_id, is_opener=False,
        ))
    assert out.report.voiced_message is None
    assert reason.startswith("policy:medical_overreach")


# --- transient failures leave the old voice in place ----------------------------


def test_a_transport_failure_leaves_the_row_untouched_and_stale(db, monkeypatch):
    activity = _generated_then_voice_changed(db, monkeypatch)
    before = _snapshot(get_active_report_row(db, activity.id))

    fake = _client(CORNERMAN_TEXT)
    fake.generate_json_with_usage = AsyncMock(side_effect=RuntimeError("boom"))
    memory, _, regen = _run_job(db, activity, fake)

    fake.generate_coach_message.assert_not_awaited()
    regen.assert_not_awaited()
    memory.assert_not_called()
    row = get_active_report_row(db, activity.id)
    assert _snapshot(row) == before  # old voice kept, nothing stamped
    assert row.report["voiced_message"] == ROAST_TEXT
    assert report_voice_stale(db, row) is True  # a later view retries


def test_over_budget_leaves_the_row_untouched_and_stale(db, monkeypatch):
    activity = _generated_then_voice_changed(db, monkeypatch)
    before = _snapshot(get_active_report_row(db, activity.id))

    fake = _client(CORNERMAN_TEXT)
    with patch("app.services.coach.turn.over_budget", return_value=True):
        _, _, regen = _run_job(db, activity, fake)

    fake.generate_json_with_usage.assert_not_awaited()
    regen.assert_not_awaited()
    row = get_active_report_row(db, activity.id)
    assert _snapshot(row) == before
    assert report_voice_stale(db, row) is True


# --- what is NOT re-voiced ------------------------------------------------------


def test_a_report_no_longer_stale_is_left_alone(db, monkeypatch):
    """The voice was switched back (or another device's revoice landed) before the
    job ran: nothing to do, and certainly no full regeneration."""
    activity = _generated_then_voice_changed(db, monkeypatch)
    _relationship(db, activity).voice_preset = "roast"
    db.commit()
    before = _snapshot(get_active_report_row(db, activity.id))

    fake = _client(CORNERMAN_TEXT)
    _, _, regen = _run_job(db, activity, fake)

    fake.generate_json_with_usage.assert_not_awaited()
    regen.assert_not_awaited()
    assert _snapshot(get_active_report_row(db, activity.id)) == before


def test_no_active_row_after_a_prompt_flip_is_left_alone(db, monkeypatch):
    """A prompt flip leaves no active-version row; the read never flags it
    voice-stale, and a stray revoice job must not regenerate it either (an
    explicit Re-run still does, through its own endpoint)."""
    activity = _generated_then_voice_changed(db, monkeypatch)
    monkeypatch.setattr(settings, "COACH_PROMPT_ID", "coach_message_lean_grouped_v11")
    fake = _client(CORNERMAN_TEXT)
    _, _, regen = _run_job(db, activity, fake)
    regen.assert_not_awaited()
    fake.generate_json_with_usage.assert_not_awaited()


@pytest.mark.parametrize("damage", ["no_rewrite_stamp", "fallback", "empty_text"])
def test_a_stale_row_with_no_voiceless_baseline_regenerates(db, monkeypatch, damage):
    """No voice_rewrite stamp (pre-#822, when voice steered generation), a fallback,
    or no prose at all: nothing honest to re-say, so the full regeneration runs."""
    activity = _generated_then_voice_changed(db, monkeypatch)
    row = get_active_report_row(db, activity.id)
    if damage == "no_rewrite_stamp":
        row.meta = {k: v for k, v in row.meta.items() if k != "voice_rewrite"}
    elif damage == "fallback":
        row.is_fallback = True
    else:
        row.report = {**row.report, "message": "", "opener_message": None}
    db.commit()

    fake = _client(CORNERMAN_TEXT)
    _, _, regen = _run_job(db, activity, fake)
    regen.assert_awaited_once()
    assert regen.await_args.kwargs.get("force") is True
    fake.generate_json_with_usage.assert_not_awaited()


def test_a_rerun_landing_during_the_rewrite_is_not_overwritten(db, monkeypatch):
    """A Re-run superseded the row while the rewrite was in flight: the archived
    row keeps exactly what it held, and nothing is written."""
    activity = _generated_then_voice_changed(db, monkeypatch)
    row_id = get_active_report_row(db, activity.id).id

    fake = _client(CORNERMAN_TEXT)
    answer = (_answer(CORNERMAN_TEXT), None)

    def _supersede_then_answer(*_a, **_k):
        archived = db.query(CoachReport).filter(CoachReport.id == row_id).one()
        archived.superseded_at = datetime.now(timezone.utc)
        db.commit()
        return answer

    fake.generate_json_with_usage = AsyncMock(side_effect=_supersede_then_answer)
    _run_job(db, activity, fake)

    archived = db.query(CoachReport).filter(CoachReport.id == row_id).one()
    assert archived.report["voiced_message"] == ROAST_TEXT
    assert archived.meta.get("revoiced_at") is None


def test_only_the_active_row_is_revoiced_never_an_archived_one(db, monkeypatch):
    """After a Re-run there is an archived row and an active one; the revoice
    works on the active row and leaves the audit copy as it was."""
    monkeypatch.setattr(settings, "COACH_PROMPT_ID", PROD_PROMPT)
    activity = _seed(db)
    _generate(db, activity)
    _generate(db, activity, force=True)  # supersede: archive + new active row
    archived = [r for r in _rows(db, activity) if r.superseded_at is not None]
    assert len(archived) == 1
    archived_before = _snapshot(archived[0])
    _relationship(db, activity).voice_preset = "cornerman"
    db.commit()

    _run_job(db, activity, _client(CORNERMAN_TEXT))

    active = get_active_report_row(db, activity.id)
    assert active.report["voiced_message"] == CORNERMAN_TEXT
    assert report_voice_stale(db, active) is False
    archived_row = db.query(CoachReport).filter(CoachReport.id == archived[0].id).one()
    assert _snapshot(archived_row) == archived_before


# --- endpoint -------------------------------------------------------------------


def _nx_queue():
    """A queue whose Redis SET honours NX, so cooldowns behave as in production."""
    held: set = set()

    def _set(key, _value, nx=False, ex=None):
        if nx and key in held:
            return None
        held.add(key)
        return True

    fake_queue = MagicMock()
    fake_queue.connection.set.side_effect = _set
    return fake_queue


def test_revoice_endpoint_enqueues_the_revoice_job(client: TestClient, db):
    from tests.test_coach_report_versioning import _seed_activity

    activity = _seed_activity(db)
    fake_queue = _nx_queue()
    with patch("app.core.queue.queue", fake_queue):
        res = client.post(f"/api/activities/{activity.id}/coach-report/revoice")
    assert res.status_code == 202
    assert res.json() == {"status": "revoicing"}
    call = fake_queue.enqueue.call_args
    assert call.args[0] is job_mod.revoice_report_job
    assert call.args[1] == str(activity.id)
    assert call.kwargs.get("job_timeout") == settings.RQ_JOB_TIMEOUT_SECONDS
    assert call.kwargs.get("job_id") == f"coach-revoice-{activity.id}"


def test_an_automatic_revoice_never_swallows_an_explicit_rerun(client: TestClient, db):
    from tests.test_coach_report_versioning import _seed_activity

    activity = _seed_activity(db)
    fake_queue = _nx_queue()
    with patch("app.core.queue.queue", fake_queue):
        client.post(f"/api/activities/{activity.id}/coach-report/revoice")
        res = client.post(f"/api/activities/{activity.id}/coach-report/regenerate")
        again = client.post(f"/api/activities/{activity.id}/coach-report/revoice")
    assert res.json() == {"status": "regenerating"}
    assert fake_queue.enqueue.call_args_list[-1].args[0] is job_mod.regenerate_report_job
    assert again.json() == {"status": "cooldown"}  # revoice still dedups itself
