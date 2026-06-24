"""Pipeline tests for the two-stage Exchange cadence (A4 deliverable 5, reworked
onto exchange rows by A1).

Covers the opener stage (generate + notify + conditional schedule), the
idempotent fuller turn (timer/reply preemption can't double-send), the
two-notifications-max dedup (AC5), the deterministic safety-override schedule,
and the rq-scheduler enqueue_in wiring. Stage sentinels live on the block's
`exchanges` row (A1); the legacy Activity sentinels must stay unwritten. The
opener/fuller LLM calls are stubbed; the scheduler helper is patched (then
exercised separately).
"""

from datetime import datetime
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

import pytest

from app.core.config import settings
from app.jobs import process_new_activity as pna
from app.jobs.process_new_activity import (
    _run_opener_stage,
    _schedule_fuller_turn,
    maybe_enqueue_fuller_turn,
    process_fuller_turn,
)
from app.models import Activity, DerivedMetric, Exchange, StravaAccount, User, UserProfile
from app.services.blocks import assign_activity_to_block
from app.services.coach.llm import MessageResult
from app.services.coach.output_contract import is_opener_only
from app.services.coach.service import get_active_report_row
from app.services.notifications import set_notifier
from app.services.notifications.in_memory_adapter import InMemoryNotifier

pytestmark = pytest.mark.asyncio


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setattr(settings, "COACH_PROMPT_ID", "coach_message_v2")
    monkeypatch.setattr(settings, "SMTP_HOST", "smtp.example.com")
    monkeypatch.setattr(settings, "NOTIFY_TO", "runner@example.com")
    monkeypatch.setattr(settings, "APP_BASE_URL", "https://app.example.com")
    monkeypatch.setattr(settings, "TELEGRAM_BOT_TOKEN", "")
    monkeypatch.setattr(settings, "TELEGRAM_CHAT_ID", "")


@pytest.fixture
def notifier():
    n = InMemoryNotifier()
    set_notifier(n)
    yield n
    set_notifier(None)


def _seed(db, *, flags=None) -> Activity:
    user = User(email=f"u-{uuid4()}@example.com")
    db.add(user)
    db.commit()
    db.add(UserProfile(user_id=user.id, goal_type="general",
                       experience_level="intermediate", weekly_days_available=4, max_hr=190))
    db.add(StravaAccount(user_id=user.id, strava_athlete_id=abs(hash(str(user.id))) % 10**9,
                         access_token="t", refresh_token="r", expires_at=9999999999, scope="read"))
    activity = Activity(user_id=user.id, strava_activity_id=abs(hash(str(uuid4()))) % 10**9,
                        start_date=datetime(2026, 5, 27, 10, 0, 0), type="Run", name="Test run",
                        distance_m=5000, moving_time_s=1500, elapsed_time_s=1500, elev_gain_m=10.0,
                        avg_hr=140, raw_summary={})
    db.add(activity)
    db.commit()
    db.add(DerivedMetric(activity_id=activity.id, effort="easy", structure="continuous",
                         duration_class="standard", effort_score=50.0, flags=flags or [],
                         confidence="medium", confidence_reasons=[]))
    db.commit()
    db.refresh(activity)
    return activity


def _exchange_of(db, activity) -> Exchange:
    """The activity's block's exchange, assigning the block if needed."""
    if activity.block_id is None:
        assign_activity_to_block(db, activity)
        db.refresh(activity)
    return db.query(Exchange).filter(Exchange.block_id == activity.block_id).one()


def _text(t):
    return {"type": "text", "text": t}


def _tail(**tail):
    return {"type": "tool_use", "name": "record_coach_tail", "input": tail}


def _result(blocks, stop_reason="end_turn"):
    return MessageResult(content_blocks=blocks, stop_reason=stop_reason)


def _opener_blocks(*, schedule):
    return [_text("Nice work getting that one in!"), _tail(
        headline="Quick", next_steps=[], risks=[],
        questions=[{"question": "How did it feel?", "reason": "calibrate",
                    "options": [{"id": "rpe", "label": "Rate 1-10", "kind": "rpe"}]}],
        schedule_fuller_turn=schedule,
    )]


def _fuller_blocks():
    return [_text("Aerobically that held up well — drift around 4%."), _tail(
        headline="Solid run",
        next_steps=[{"action": "Easy run", "details": "30 min", "why": "Recovery"}],
        risks=[],
        questions=[{"question": "How did that feel?", "reason": "No RPE",
                    "options": [{"id": "rpe", "label": "Rate 1-10", "kind": "rpe"}]}],
    )]


def _client(*results):
    fake = AsyncMock()
    fake.generate_coach_message = AsyncMock(side_effect=list(results))
    return fake


# --- opener stage -------------------------------------------------------------


async def test_opener_notifies_and_schedules_when_salient(db, configured, notifier):
    activity = _seed(db)
    exchange = _exchange_of(db, activity)
    with patch("app.services.coach.service.AnthropicClient",
               return_value=_client(_result(_opener_blocks(schedule=True)))), \
         patch.object(pna, "_schedule_fuller_turn") as sched:
        result = await _run_opener_stage(
            db=db, activity=activity, exchange=exchange, notifier=notifier
        )

    assert result is not None  # opener notification sent
    assert len(notifier.sent) == 1
    db.refresh(exchange)
    assert exchange.opened_at is not None
    assert exchange.opener_sent_at is not None
    assert exchange.fuller_sent_at is None  # fuller not done
    sched.assert_called_once_with(str(activity.id))
    # A1: the legacy Activity sentinels are no longer written.
    db.refresh(activity)
    assert activity.opener_notification_sent_at is None


async def test_fallback_opener_still_notified_and_schedules(db, configured, notifier):
    # AC3 / review regression: a fallback opener (LLM refused) is still notified
    # (its templated prose is benign) so a red-flag opener is never silent, and it
    # schedules a recovery fuller.
    activity = _seed(db, flags=["illness_or_extreme_fatigue"])
    exchange = _exchange_of(db, activity)
    with patch("app.services.coach.service.AnthropicClient",
               return_value=_client(_result([], stop_reason="refusal"))), \
         patch.object(pna, "_schedule_fuller_turn") as sched:
        result = await _run_opener_stage(
            db=db, activity=activity, exchange=exchange, notifier=notifier
        )
    assert result is not None  # the fallback opener WAS notified
    assert len(notifier.sent) == 1
    sched.assert_called_once()  # recovery fuller scheduled


async def test_opener_does_not_schedule_when_unremarkable(db, configured, notifier):
    activity = _seed(db)
    exchange = _exchange_of(db, activity)
    with patch("app.services.coach.service.AnthropicClient",
               return_value=_client(_result(_opener_blocks(schedule=False)))), \
         patch.object(pna, "_schedule_fuller_turn") as sched:
        await _run_opener_stage(
            db=db, activity=activity, exchange=exchange, notifier=notifier
        )
    # AC1: an unremarkable run ends at the opener — no fuller scheduled.
    sched.assert_not_called()


async def test_red_flag_forces_schedule_despite_llm(db, configured, notifier):
    # The deterministic safety override forces a fuller turn regardless of the
    # LLM's schedule judgment.
    activity = _seed(db, flags=["illness_or_extreme_fatigue"])
    exchange = _exchange_of(db, activity)
    with patch("app.services.coach.service.AnthropicClient",
               return_value=_client(_result(_opener_blocks(schedule=False)))), \
         patch.object(pna, "_schedule_fuller_turn") as sched:
        await _run_opener_stage(
            db=db, activity=activity, exchange=exchange, notifier=notifier
        )
    sched.assert_called_once()


async def test_opener_idempotent_skips_when_already_sent(db, configured, notifier):
    activity = _seed(db)
    exchange = _exchange_of(db, activity)
    with patch("app.services.coach.service.AnthropicClient",
               return_value=_client(_result(_opener_blocks(schedule=True)))), \
         patch.object(pna, "_schedule_fuller_turn"):
        await _run_opener_stage(db=db, activity=activity, exchange=exchange, notifier=notifier)
    # second run: opener already notified -> no-op, no second notification
    with patch("app.services.coach.service.AnthropicClient",
               return_value=_client(_result(_opener_blocks(schedule=True)))), \
         patch.object(pna, "_schedule_fuller_turn") as sched2:
        result = await _run_opener_stage(db=db, activity=activity, exchange=exchange, notifier=notifier)
    assert result is None
    assert len(notifier.sent) == 1
    sched2.assert_not_called()


# --- fuller turn (idempotency, AC2/AC5) ---------------------------------------


async def test_fuller_turn_notifies_and_sets_sentinel(db, configured, notifier):
    activity = _seed(db)
    exchange = _exchange_of(db, activity)
    with patch("app.services.coach.service.AnthropicClient",
               return_value=_client(_result(_fuller_blocks()))), \
         patch("app.services.coach.service.write_back_beliefs"), \
         patch("app.services.coach.service.enqueue_consolidation"):
        result = await process_fuller_turn(db=db, activity=activity, notifier=notifier)
    assert result is not None
    assert len(notifier.sent) == 1
    db.refresh(exchange)
    assert exchange.fuller_sent_at is not None
    db.refresh(activity)
    assert activity.coach_notification_sent_at is None  # legacy sentinel unwritten


async def test_fuller_turn_idempotent_racing_timer_noops(db, configured, notifier):
    # AC2/AC5: a reply fires the fuller; the racing timer then no-ops.
    activity = _seed(db)
    with patch("app.services.coach.service.AnthropicClient",
               return_value=_client(_result(_fuller_blocks()), _result(_fuller_blocks()))), \
         patch("app.services.coach.service.write_back_beliefs"), \
         patch("app.services.coach.service.enqueue_consolidation"):
        first = await process_fuller_turn(db=db, activity=activity, notifier=notifier)
        second = await process_fuller_turn(db=db, activity=activity, notifier=notifier)
    assert first is not None
    assert second is None  # timer-after-reply is a harmless no-op
    assert len(notifier.sent) == 1  # exactly one fuller notification


async def test_safety_forced_fuller_fallback_stays_recoverable(db, configured, notifier):
    # #217: a red-flag run forces a fuller turn. If that forced fuller's LLM call
    # falls back persistently (both prose attempts empty), the turn must NOT be
    # silently abandoned: no fuller notification fires, the fuller sentinel stays
    # null, and the row stays opener-only so a reply re-opens the exchange and the
    # substantive coaching can still land — the safety floor's guarantee survives.
    activity = _seed(db, flags=["illness_or_extreme_fatigue"])
    exchange = _exchange_of(db, activity)
    with patch("app.services.coach.service.AnthropicClient",
               return_value=_client(_result(_opener_blocks(schedule=False)))), \
         patch.object(pna, "_schedule_fuller_turn"):
        await _run_opener_stage(db=db, activity=activity, exchange=exchange, notifier=notifier)
    assert len(notifier.sent) == 1  # opener only

    empty = [_tail(headline="x", next_steps=[], risks=[], questions=[])]
    with patch("app.services.coach.service.AnthropicClient",
               return_value=_client(_result(empty), _result(empty))), \
         patch("app.services.coach.service.write_back_beliefs"), \
         patch("app.services.coach.service.enqueue_consolidation"):
        result = await process_fuller_turn(db=db, activity=activity, notifier=notifier)
    assert result is None              # a fallback fuller is not notified
    assert len(notifier.sent) == 1     # still just the opener
    db.refresh(exchange)
    assert exchange.fuller_sent_at is None  # sentinel left open for retry
    row = get_active_report_row(db, activity.id)
    assert is_opener_only(row.report) is True  # recoverable, not a stuck fallback

    # A reply re-opens the exchange — the recovery path the stuck fallback used to
    # defeat (the exchange is opened and unclosed, so maybe_enqueue re-fires).
    with patch("app.jobs.process_new_activity.queue", Mock()):
        assert maybe_enqueue_fuller_turn(db, activity.id) is True

    # And the re-fired fuller now produces and notifies the real substantive turn.
    with patch("app.services.coach.service.AnthropicClient",
               return_value=_client(_result(_fuller_blocks()))), \
         patch("app.services.coach.service.write_back_beliefs"), \
         patch("app.services.coach.service.enqueue_consolidation"):
        recovered = await process_fuller_turn(db=db, activity=activity, notifier=notifier)
    assert recovered is not None
    assert len(notifier.sent) == 2
    db.refresh(exchange)
    assert exchange.fuller_sent_at is not None


async def test_two_notifications_max_opener_then_fuller(db, configured, notifier):
    # AC5: at most two notifications per exchange (one opener, one fuller).
    activity = _seed(db)
    exchange = _exchange_of(db, activity)
    with patch("app.services.coach.service.AnthropicClient",
               return_value=_client(_result(_opener_blocks(schedule=True)))), \
         patch.object(pna, "_schedule_fuller_turn"):
        await _run_opener_stage(db=db, activity=activity, exchange=exchange, notifier=notifier)
    with patch("app.services.coach.service.AnthropicClient",
               return_value=_client(_result(_fuller_blocks()))), \
         patch("app.services.coach.service.write_back_beliefs"), \
         patch("app.services.coach.service.enqueue_consolidation"):
        await process_fuller_turn(db=db, activity=activity, notifier=notifier)

    assert len(notifier.sent) == 2  # opener + fuller, no more
    db.refresh(exchange)
    assert exchange.opener_sent_at is not None
    assert exchange.fuller_sent_at is not None
    # A1: the legacy Activity sentinels stay unwritten under the two-stage path.
    db.refresh(activity)
    assert activity.opener_notification_sent_at is None
    assert activity.coach_notification_sent_at is None


# --- rollback inertness (#216) -------------------------------------------------


async def test_scheduled_fuller_is_inert_after_rollback_to_single_shot(
    db, configured, notifier, monkeypatch
):
    # #216 / AC6: a fuller scheduled under coach_message_v2 that fires AFTER the
    # owner rolls COACH_PROMPT_ID back to a single-shot prompt must be a no-op —
    # no generation, no notification, no learning-loop write-back.
    activity = _seed(db)
    exchange = _exchange_of(db, activity)
    with patch("app.services.coach.service.AnthropicClient",
               return_value=_client(_result(_opener_blocks(schedule=True)))), \
         patch.object(pna, "_schedule_fuller_turn"):
        await _run_opener_stage(db=db, activity=activity, exchange=exchange, notifier=notifier)
    assert len(notifier.sent) == 1  # the opener went out before the rollback

    monkeypatch.setattr(settings, "COACH_PROMPT_ID", "coach_report_v10")  # rollback
    with patch("app.services.coach.service.AnthropicClient") as client_cls, \
         patch("app.services.coach.service.write_back_beliefs") as wb, \
         patch("app.services.coach.service.enqueue_consolidation") as ec:
        result = await process_fuller_turn(db=db, activity=activity, notifier=notifier)

    assert result is None
    assert len(notifier.sent) == 1   # no stray single-shot notification
    client_cls.assert_not_called()   # no generation at all
    wb.assert_not_called()
    ec.assert_not_called()
    db.refresh(exchange)
    assert exchange.fuller_sent_at is None


# --- crash recovery via job retry (#215) ---------------------------------------


async def test_opener_crash_before_schedule_recovers_on_rerun(db, configured, notifier):
    # #215: a worker crash after the opener row is committed but before the fuller
    # is scheduled and the opener notified must be recoverable by re-running the
    # job (the RQ retry). The re-run completes the exchange — fuller scheduled,
    # opener notified — without a second LLM call (generate_opener re-enters on
    # the stored row). This pins the re-entrancy the retry policy relies on.
    activity = _seed(db)
    exchange = _exchange_of(db, activity)
    with patch("app.services.coach.service.AnthropicClient",
               return_value=_client(_result(_opener_blocks(schedule=True)))), \
         patch.object(pna, "_schedule_fuller_turn", side_effect=RuntimeError("worker died")):
        with pytest.raises(RuntimeError):
            await _run_opener_stage(db=db, activity=activity, exchange=exchange, notifier=notifier)
    assert len(notifier.sent) == 0  # crashed before notify

    with patch("app.services.coach.service.AnthropicClient") as client_cls, \
         patch.object(pna, "_schedule_fuller_turn") as sched:
        result = await _run_opener_stage(db=db, activity=activity, exchange=exchange, notifier=notifier)

    assert result is not None
    client_cls.assert_not_called()  # idempotent re-entry, no fresh generation
    sched.assert_called_once_with(str(activity.id))
    assert len(notifier.sent) == 1
    db.refresh(exchange)
    assert exchange.opener_sent_at is not None


# --- the scheduler wiring (enqueue_in) ----------------------------------------


async def test_schedule_fuller_turn_uses_enqueue_in(monkeypatch):
    # (sync body; async only to satisfy the module-wide asyncio mark)
    # #123/ADR 0006: deferred jobs use RQ-native queue.enqueue_in (drained by the
    # worker's with_scheduler — no separate rq-scheduler process). RQ-native uses
    # `job_timeout`, not rq-scheduler's `timeout`.
    from datetime import timedelta

    captured = {}

    class _FakeQueue:
        def enqueue_in(self, delta, func, *args, **kwargs):
            captured["delta"] = delta
            captured["func"] = func
            captured["args"] = args
            captured["kwargs"] = kwargs

    monkeypatch.setattr(settings, "EXCHANGE_STAGE2_DELAY_SECONDS", 10800)
    monkeypatch.setattr("app.core.queue.queue", _FakeQueue())

    _schedule_fuller_turn("abc-123")

    assert captured["delta"] == timedelta(seconds=10800)
    assert captured["func"] is pna.fuller_turn_job
    assert captured["args"] == ("abc-123",)
    # #264: the slow fuller turn must outlast RQ's 180s default death penalty.
    assert captured["kwargs"].get("job_timeout") == settings.RQ_JOB_TIMEOUT_SECONDS


async def test_schedule_block_complete_uses_enqueue_in(monkeypatch):
    from datetime import timedelta

    captured = {}

    class _FakeQueue:
        def enqueue_in(self, delta, func, *args, **kwargs):
            captured["delta"] = delta
            captured["func"] = func
            captured["args"] = args
            captured["kwargs"] = kwargs

    monkeypatch.setattr(settings, "BLOCK_GAP_SECONDS", 1800)
    monkeypatch.setattr("app.core.queue.queue", _FakeQueue())

    pna._schedule_block_complete("block-1", "act-1")

    assert captured["delta"] == timedelta(seconds=1800)
    assert captured["func"] is pna.block_complete_job
    assert captured["args"] == ("block-1", "act-1")
    assert captured["kwargs"].get("job_timeout") == settings.RQ_JOB_TIMEOUT_SECONDS
