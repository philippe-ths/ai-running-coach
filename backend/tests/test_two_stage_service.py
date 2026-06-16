"""Service-layer tests for the A4 two-stage Exchange (deliverable 7).

Exercises generate_opener + generate_fuller against a stubbed generate_coach_message:
the opener writes a light state and no durable memory; the fuller fills the SAME
row in place preserving the opener prose and fires the learning loop; the schedule
decision OR-s the safety override; get_or_generate delegates to the fuller under
the two-stage prompt. No live API call.
"""

from datetime import datetime
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from app.models import Activity, DerivedMetric, StravaAccount, User, UserProfile
from app.models.coach_report import CoachReport
from app.services.coach.llm import MessageResult
from app.services.coach.output_contract import is_opener_only
from app.services.coach.service import (
    generate_fuller,
    generate_opener,
    get_active_report_row,
    get_or_generate_coach_report,
)

pytestmark = pytest.mark.asyncio


def _seed(db, *, flags=None) -> Activity:
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
        duration_class="standard", effort_score=50.0, flags=flags or [],
        confidence="medium", confidence_reasons=[],
    ))
    db.commit()
    db.refresh(activity)
    return activity


def _text(t):
    return {"type": "text", "text": t}


def _tail(**tail):
    return {"type": "tool_use", "name": "record_coach_tail", "input": tail}


def _result(blocks, stop_reason="end_turn"):
    return MessageResult(content_blocks=blocks, stop_reason=stop_reason)


def _opener_blocks(*, schedule, prose="Nice work getting that one done!"):
    return [_text(prose), _tail(
        headline="Quick reaction", next_steps=[], risks=[],
        questions=[{"question": "How did it feel?", "reason": "calibrate",
                    "options": [{"id": "rpe", "label": "Rate 1-10", "kind": "rpe"},
                                {"id": "pain", "label": "Any pain?", "kind": "pain"}]}],
        schedule_fuller_turn=schedule,
    )]


def _fuller_blocks(prose="Aerobically that held up well — drift stayed around 4%."):
    return [_text(prose), _tail(
        headline="Solid aerobic run",
        next_steps=[{"action": "Easy run", "details": "30 min", "why": "Recovery"}],
        risks=[],
        # A question so rule 1 (null check-in must prompt) does not fire a retry.
        questions=[{"question": "How did that feel?", "reason": "No RPE logged",
                    "options": [{"id": "rpe", "label": "Rate 1-10", "kind": "rpe"}]}],
    )]


def _client(*results):
    fake = AsyncMock()
    fake.generate_coach_message = AsyncMock(side_effect=list(results))
    return fake


@pytest.fixture
def _v2(monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "COACH_PROMPT_ID", "coach_message_v2")
    return settings


def _stored(db, activity) -> CoachReport:
    return get_active_report_row(db, activity.id)


# --- opener -------------------------------------------------------------------


async def test_opener_writes_light_state_no_learning_loop(db, _v2):
    activity = _seed(db)
    with patch("app.services.coach.service.AnthropicClient",
               return_value=_client(_result(_opener_blocks(schedule=True)))), \
         patch("app.services.coach.service.write_back_beliefs") as wb, \
         patch("app.services.coach.service.enqueue_consolidation") as ec:
        result = await generate_opener(db, str(activity.id))

    assert result is not None
    assert result.schedule_fuller_turn is True
    row = _stored(db, activity)
    assert is_opener_only(row.report) is True
    assert row.report["opener_message"].startswith("Nice work")
    assert row.report["message"] == ""
    assert row.digest is None  # opener carries no digest
    # the opener writes NOTHING to durable memory
    wb.assert_not_called()
    ec.assert_not_called()


async def test_opener_schedule_or_safety_override(db, _v2):
    # The LLM judged NOT to deepen, but a red-flag flag forces a fuller turn.
    activity = _seed(db, flags=["illness_or_extreme_fatigue"])
    with patch("app.services.coach.service.AnthropicClient",
               return_value=_client(_result(_opener_blocks(schedule=False)))):
        result = await generate_opener(db, str(activity.id))
    assert result.schedule_fuller_turn is True  # safety override wins


async def test_opener_unremarkable_does_not_schedule(db, _v2):
    activity = _seed(db)
    with patch("app.services.coach.service.AnthropicClient",
               return_value=_client(_result(_opener_blocks(schedule=False)))):
        result = await generate_opener(db, str(activity.id))
    assert result.schedule_fuller_turn is False


async def test_opener_idempotent_no_second_llm_call(db, _v2):
    activity = _seed(db)
    fake = _client(_result(_opener_blocks(schedule=True)))
    with patch("app.services.coach.service.AnthropicClient", return_value=fake):
        await generate_opener(db, str(activity.id))
        await generate_opener(db, str(activity.id))  # idempotent re-entry
    # only one LLM call despite two generate_opener calls
    assert fake.generate_coach_message.call_count == 1


# --- fuller -------------------------------------------------------------------


async def test_fuller_updates_opener_row_in_place_preserving_opener(db, _v2):
    activity = _seed(db)
    with patch("app.services.coach.service.AnthropicClient",
               return_value=_client(_result(_opener_blocks(schedule=True)))):
        await generate_opener(db, str(activity.id))
    opener_row_id = _stored(db, activity).id

    with patch("app.services.coach.service.AnthropicClient",
               return_value=_client(_result(_fuller_blocks()))), \
         patch("app.services.coach.service.write_back_beliefs") as wb, \
         patch("app.services.coach.service.enqueue_consolidation") as ec:
        read = await generate_fuller(db, str(activity.id))

    assert read is not None
    row = _stored(db, activity)
    # SAME physical row (one evolving coach_reports row, cache identity unchanged)
    assert row.id == opener_row_id
    assert is_opener_only(row.report) is False  # now complete
    assert row.report["opener_message"].startswith("Nice work")  # preserved
    assert row.report["message"].startswith("Aerobically")  # fuller prose
    assert len(row.report["next_steps"]) == 1
    assert row.digest is not None  # the fuller stores the exchange digest
    # the fuller fires the learning loop exactly once
    wb.assert_called_once()
    ec.assert_called_once()


async def test_fuller_inserts_when_no_opener(db, _v2):
    # On-demand / timer with no prior opener row: the fuller inserts a fresh row.
    activity = _seed(db)
    with patch("app.services.coach.service.AnthropicClient",
               return_value=_client(_result(_fuller_blocks()))):
        read = await generate_fuller(db, str(activity.id))
    assert read is not None
    row = _stored(db, activity)
    assert row.report["message"].startswith("Aerobically")
    assert is_opener_only(row.report) is False


async def test_fuller_cache_hit_skips_llm(db, _v2):
    activity = _seed(db)
    fake = _client(_result(_fuller_blocks()))
    with patch("app.services.coach.service.AnthropicClient", return_value=fake):
        await generate_fuller(db, str(activity.id))
        await generate_fuller(db, str(activity.id))  # complete row cached
    assert fake.generate_coach_message.call_count == 1


async def test_fuller_force_regenerates(db, _v2):
    activity = _seed(db)
    fake = _client(_result(_fuller_blocks("First.")), _result(_fuller_blocks("Second take.")))
    with patch("app.services.coach.service.AnthropicClient", return_value=fake):
        await generate_fuller(db, str(activity.id))
        await generate_fuller(db, str(activity.id), force=True)
    assert fake.generate_coach_message.call_count == 2
    assert _stored(db, activity).report["message"] == "Second take."


# --- get_or_generate delegation under the two-stage prompt --------------------


async def test_get_or_generate_delegates_to_fuller_under_v2(db, _v2):
    activity = _seed(db)
    with patch("app.services.coach.service.AnthropicClient",
               return_value=_client(_result(_fuller_blocks()))):
        read = await get_or_generate_coach_report(db, str(activity.id))
    assert read is not None
    # produced a COMPLETE report (the on-demand path wants the fuller, not an opener)
    row = _stored(db, activity)
    assert is_opener_only(row.report) is False
    assert row.report["message"].startswith("Aerobically")


# --- adversarial-review regression fixes --------------------------------------


def _overreach_opener_blocks():
    # Opener prose that trips validator rule 5 (medical_overreach); the validator
    # reads opener_message via _extract_message_text.
    return [_text("Quick reaction — this is consistent with a stress fracture; rest it."),
            _tail(headline="Quick", next_steps=[], risks=[],
                  questions=[{"question": "How did it feel?", "reason": "calibrate",
                              "options": [{"id": "rpe", "label": "Rate 1-10", "kind": "rpe"}]}],
                  schedule_fuller_turn=False)]


async def test_opener_medical_overreach_stays_opener_only_and_recovers(db, _v2):
    # BLOCKER regression: an opener medical-overreach surviving the retry must store
    # an OPENER-shaped fallback (opener-only), so the safety-forced fuller can still
    # regenerate — not a fuller-shaped fallback that the cadence can never replace.
    activity = _seed(db, flags=["illness_or_extreme_fatigue"])
    with patch("app.services.coach.service.AnthropicClient",
               return_value=_client(_result(_overreach_opener_blocks()),
                                     _result(_overreach_opener_blocks()))):
        opener = await generate_opener(db, str(activity.id))
    assert opener is not None
    assert opener.is_fallback is True
    assert opener.schedule_fuller_turn is True  # red flag + fallback both force it
    row = _stored(db, activity)
    assert is_opener_only(row.report) is True  # NOT a stuck fuller-shaped fallback
    assert row.report["message"] == ""
    assert bool(row.report["opener_message"]) is True

    # The safety-forced fuller turn must now regenerate a real (non-fallback) turn,
    # not cache-hit the stuck fallback.
    with patch("app.services.coach.service.AnthropicClient",
               return_value=_client(_result(_fuller_blocks()))), \
         patch("app.services.coach.service.write_back_beliefs"), \
         patch("app.services.coach.service.enqueue_consolidation"):
        await generate_fuller(db, str(activity.id))
    row2 = _stored(db, activity)
    assert row2.is_fallback is False
    assert row2.report["message"].startswith("Aerobically")


async def test_fuller_force_preserves_opener_prose(db, _v2):
    # MEDIUM regression: force-regenerating a complete two-line row must preserve
    # the opener half of the thread.
    activity = _seed(db)
    with patch("app.services.coach.service.AnthropicClient",
               return_value=_client(_result(_opener_blocks(schedule=True)))):
        await generate_opener(db, str(activity.id))
    with patch("app.services.coach.service.AnthropicClient",
               return_value=_client(_result(_fuller_blocks()))), \
         patch("app.services.coach.service.write_back_beliefs"), \
         patch("app.services.coach.service.enqueue_consolidation"):
        await generate_fuller(db, str(activity.id))
    opener_prose = _stored(db, activity).report["opener_message"]

    with patch("app.services.coach.service.AnthropicClient",
               return_value=_client(_result(_fuller_blocks("Second take, drift held.")))), \
         patch("app.services.coach.service.write_back_beliefs"), \
         patch("app.services.coach.service.enqueue_consolidation"):
        await generate_fuller(db, str(activity.id), force=True)
    row = _stored(db, activity)
    assert row.report["opener_message"] == opener_prose  # preserved across force
    assert row.report["message"] == "Second take, drift held."


async def test_fallback_opener_schedules_recovery_fuller(db, _v2):
    # MEDIUM/LOW regression: an opener LLM failure (fallback) on an UNREMARKABLE run
    # still schedules a fuller turn so the substantive coaching can recover.
    activity = _seed(db)  # no red flag
    with patch("app.services.coach.service.AnthropicClient",
               return_value=_client(_result([], stop_reason="refusal"))):
        opener = await generate_opener(db, str(activity.id))
    assert opener.is_fallback is True
    assert opener.schedule_fuller_turn is True  # recovery, despite no LLM judgment
    row = _stored(db, activity)
    assert is_opener_only(row.report) is True  # opener-shaped fallback


# --- #217: fuller-fallback recovery (no silently-abandoned safety-forced turn) -


def _empty_prose_blocks():
    # A response that carries a tail but NO prose text block: parse yields an empty
    # message, so merge raises EmptyMessageError (the observed transient #217 failure,
    # logged as "coach response carried no prose message"). A tail is present so the
    # tail-skip corrective retry does not fire and consume an extra stubbed result.
    return [_tail(headline="x", next_steps=[], risks=[], questions=[])]


async def test_fuller_retries_once_on_transient_empty_prose(db, _v2):
    # #217: the fuller LLM occasionally returns no prose block (transient — an
    # immediate re-run recovers it). One in-process retry turns that single hiccup
    # into a real turn instead of a silently-dropped fallback.
    activity = _seed(db)
    fake = _client(_result(_empty_prose_blocks()), _result(_fuller_blocks()))
    with patch("app.services.coach.service.AnthropicClient", return_value=fake), \
         patch("app.services.coach.service.write_back_beliefs"), \
         patch("app.services.coach.service.enqueue_consolidation"):
        read = await generate_fuller(db, str(activity.id))
    assert read is not None
    assert fake.generate_coach_message.call_count == 2  # retried once
    row = _stored(db, activity)
    assert row.is_fallback is False
    assert row.report["message"].startswith("Aerobically")


async def test_fuller_persistent_fallback_stays_opener_only_and_recovers(db, _v2):
    # #217: when BOTH fuller attempts return no prose, the fallback must not lock the
    # exchange as a complete (fuller-shaped) fallback. It stays opener-only — message
    # empty, opener prose preserved, is_fallback, no digest, no learning loop — so the
    # reply/force/timer paths can still produce the substantive turn. Mirrors
    # test_opener_medical_overreach_stays_opener_only_and_recovers for the fuller side.
    activity = _seed(db)
    with patch("app.services.coach.service.AnthropicClient",
               return_value=_client(_result(_opener_blocks(schedule=True)))):
        await generate_opener(db, str(activity.id))
    opener_row_id = _stored(db, activity).id

    with patch("app.services.coach.service.AnthropicClient",
               return_value=_client(_result(_empty_prose_blocks()),
                                     _result(_empty_prose_blocks()))), \
         patch("app.services.coach.service.write_back_beliefs") as wb, \
         patch("app.services.coach.service.enqueue_consolidation") as ec:
        read = await generate_fuller(db, str(activity.id))

    assert read is not None
    row = _stored(db, activity)
    assert row.id == opener_row_id            # same evolving row
    assert row.is_fallback is True
    assert is_opener_only(row.report) is True  # NOT a stuck fuller-shaped fallback
    assert row.report["message"] == ""
    assert row.report["opener_message"].startswith("Nice work")  # preserved
    assert row.digest is None                  # opener-only carries no digest
    wb.assert_not_called()                     # learning loop never fires on fallback
    ec.assert_not_called()

    # The exchange is still open: a subsequent fuller turn regenerates a real report
    # (it does NOT cache-hit the stuck fallback) and preserves the opener half.
    with patch("app.services.coach.service.AnthropicClient",
               return_value=_client(_result(_fuller_blocks()))), \
         patch("app.services.coach.service.write_back_beliefs"), \
         patch("app.services.coach.service.enqueue_consolidation"):
        await generate_fuller(db, str(activity.id))
    row2 = _stored(db, activity)
    assert row2.is_fallback is False
    assert row2.report["message"].startswith("Aerobically")
    assert row2.report["opener_message"].startswith("Nice work")


# --- #273: force-regen crash safety (no delete-then-die window) ---------------


async def test_force_regen_worker_death_preserves_prior_report(db, _v2):
    # #273: a worker death mid-force-regen must NOT lose the report. The old path
    # deleted+committed the active-version row BEFORE the LLM call, so an
    # interruption between the committed delete and the write left ZERO rows (the
    # observed prod incident: a 404 on the activity). With generate-then-swap the
    # prior row is held until the new content commits, so an interrupted
    # regeneration leaves the prior report fully intact.
    activity = _seed(db)
    with patch("app.services.coach.service.AnthropicClient",
               return_value=_client(_result(_fuller_blocks("Original report.")))), \
         patch("app.services.coach.service.write_back_beliefs"), \
         patch("app.services.coach.service.enqueue_consolidation"):
        await generate_fuller(db, str(activity.id))
    original = _stored(db, activity)
    assert original.report["message"] == "Original report."
    original_id = original.id

    # The worker is killed during the regeneration LLM step: _generate_message
    # never returns (an uncaught interruption, not a handled fallback).
    with patch("app.services.coach.service.AnthropicClient"), \
         patch("app.services.coach.service._generate_message",
               side_effect=RuntimeError("worker killed mid-regen")):
        with pytest.raises(RuntimeError):
            await generate_fuller(db, str(activity.id), force=True)

    survived = _stored(db, activity)
    assert survived is not None, \
        "force-regen interruption lost the report entirely (#273)"
    assert survived.id == original_id
    assert survived.report["message"] == "Original report."
    assert survived.is_fallback is False


async def test_generate_fuller_noops_under_single_shot_prompt(db, _v2, monkeypatch):
    # #216: generate_fuller is meaningful only under the two-stage prompt. After a
    # rollback to a single-shot id it must refuse to run — no LLM call, nothing
    # persisted — so a stale scheduled fuller cannot leak a wrong-path report.
    activity = _seed(db)
    monkeypatch.setattr(_v2, "COACH_PROMPT_ID", "coach_report_v10")
    with patch("app.services.coach.service.AnthropicClient") as client_cls:
        read = await generate_fuller(db, str(activity.id))
    assert read is None
    client_cls.assert_not_called()
    assert _stored(db, activity) is None  # nothing written under the rolled-back id
