"""Pipeline tests for the #296 receipt cadence.

Under COACH_RECEIPT_CADENCE the post-activity touchpoint is an instant
deterministic receipt (per activity, opens the exchange, dedup on
Activity.receipt_sent_at) and the block-complete debounce fires the FULL report
(not an LLM opener), closing on fuller_sent_at. RPE/pain replies no longer
early-fire; a "done" tap schedules the full report. Rollback (flag off) restores
the prior cadence at fire time.
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

import pytest

from app.core.config import settings
from app.jobs import process_new_activity as pna
from app.jobs.process_new_activity import (
    _send_receipt,
    mark_done_and_schedule,
    maybe_enqueue_fuller_turn,
    process_block_complete,
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
    # Two-stage prompt + the receipt-cadence flag ON, with an email channel so
    # build_receipt_notification produces a Notification the InMemoryNotifier sends.
    monkeypatch.setattr(settings, "COACH_PROMPT_ID", "coach_message_v6")
    monkeypatch.setattr(settings, "COACH_RECEIPT_CADENCE", True)
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


def _seed(db, *, start=None, flags=None) -> Activity:
    user = User(email=f"u-{uuid4()}@example.com")
    db.add(user)
    db.commit()
    db.add(UserProfile(user_id=user.id, goal_type="general",
                       experience_level="intermediate", weekly_days_available=4, max_hr=190))
    db.add(StravaAccount(user_id=user.id, strava_athlete_id=abs(hash(str(user.id))) % 10**9,
                         access_token="t", refresh_token="r", expires_at=9999999999, scope="read"))
    activity = Activity(user_id=user.id, strava_activity_id=abs(hash(str(uuid4()))) % 10**9,
                        start_date=start or datetime(2026, 5, 27, 10, 0, 0), type="Run",
                        name="Test run", distance_m=5000, moving_time_s=1500,
                        elapsed_time_s=1500, elev_gain_m=10.0, avg_hr=140, raw_summary={})
    db.add(activity)
    db.commit()
    db.add(DerivedMetric(activity_id=activity.id, effort="easy", structure="continuous",
                         duration_class="standard", effort_score=50.0, flags=flags or [],
                         confidence="medium", confidence_reasons=[]))
    db.commit()
    db.refresh(activity)
    return activity


def _block_of(db, activity):
    if activity.block_id is None:
        assign_activity_to_block(db, activity)
        db.refresh(activity)
    from app.models import Block
    return db.query(Block).filter(Block.id == activity.block_id).one()


def _exchange_of(db, activity) -> Exchange:
    block = _block_of(db, activity)
    return db.query(Exchange).filter(Exchange.block_id == block.id).one()


def _text(t):
    return {"type": "text", "text": t}


def _tail(**tail):
    return {"type": "tool_use", "name": "record_coach_tail", "input": tail}


def _result(blocks, stop_reason="end_turn"):
    return MessageResult(content_blocks=blocks, stop_reason=stop_reason)


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


# --- the instant receipt ------------------------------------------------------


async def test_receipt_sends_opens_exchange_and_dedups(db, configured, notifier):
    activity = _seed(db)
    block = _block_of(db, activity)
    n = await _send_receipt(db=db, activity=activity, block=block, notifier=notifier)
    assert n is not None
    assert len(notifier.sent) == 1
    db.refresh(activity)
    assert activity.receipt_sent_at is not None  # dedup sentinel set
    ex = _exchange_of(db, activity)
    assert ex.opened_at is not None  # first receipt opens the exchange

    # Idempotent: a second send (pipeline retry) no-ops, no second notification.
    again = await _send_receipt(db=db, activity=activity, block=block, notifier=notifier)
    assert again is None
    assert len(notifier.sent) == 1


async def test_receipt_never_calls_llm(db, configured, notifier):
    # The whole point: no LLM on the instant path, so it cannot fall back.
    activity = _seed(db)
    block = _block_of(db, activity)
    with patch("app.services.coach.service.AnthropicClient") as client_cls:
        await _send_receipt(db=db, activity=activity, block=block, notifier=notifier)
    client_cls.assert_not_called()


# --- the full report fires from the block-complete debounce -------------------


async def test_block_complete_fires_full_report_not_opener(db, configured, notifier):
    activity = _seed(db)
    block = _block_of(db, activity)
    # receipt first (opens the exchange)
    await _send_receipt(db=db, activity=activity, block=block, notifier=notifier)
    assert len(notifier.sent) == 1

    with patch("app.services.coach.service.AnthropicClient",
               return_value=_client(_result(_fuller_blocks()))), \
         patch("app.services.coach.service.write_back_beliefs"), \
         patch("app.services.coach.service.enqueue_consolidation"), \
         patch.object(pna, "_run_opener_stage") as opener:
        result = await process_block_complete(
            db=db, block_id=str(block.id), activity_id=str(activity.id), notifier=notifier
        )

    assert result is not None
    opener.assert_not_called()  # no LLM opener stage under the receipt cadence
    db.refresh(activity)
    ex = _exchange_of(db, activity)
    assert ex.fuller_sent_at is not None  # closed: the full report went
    row = get_active_report_row(db, activity.id)
    assert row is not None and not row.is_fallback
    assert is_opener_only(row.report) is False  # a complete full report
    assert len(notifier.sent) == 2  # receipt + full report


async def test_block_complete_superseded_by_newer_member_noops(db, configured, notifier):
    first = _seed(db, start=datetime(2026, 5, 27, 10, 0, 0))
    # a second activity in the same block, later
    later = Activity(user_id=first.user_id, strava_activity_id=abs(hash(str(uuid4()))) % 10**9,
                     start_date=datetime(2026, 5, 27, 10, 20, 0), type="Walk", name="cooldown",
                     distance_m=1000, moving_time_s=600, elapsed_time_s=600, elev_gain_m=1.0,
                     raw_summary={})
    db.add(later)
    db.commit()
    db.add(DerivedMetric(activity_id=later.id, effort="easy", structure="continuous",
                         duration_class="short", effort_score=10.0, flags=[],
                         confidence="medium", confidence_reasons=[]))
    db.commit()
    assign_activity_to_block(db, later)
    block = _block_of(db, first)

    # the FIRST activity's check is now stale (later is the last member) -> no-op
    with patch("app.services.coach.service.AnthropicClient") as client_cls:
        result = await process_block_complete(
            db=db, block_id=str(block.id), activity_id=str(first.id), notifier=notifier
        )
    assert result is None
    client_cls.assert_not_called()


# --- replies do not early-fire; "done" schedules ------------------------------


async def test_rpe_reply_does_not_early_fire_under_receipt_cadence(db, configured, notifier):
    activity = _seed(db)
    block = _block_of(db, activity)
    await _send_receipt(db=db, activity=activity, block=block, notifier=notifier)
    # an RPE/pain reply just records the check-in; it must not fire the full report
    assert maybe_enqueue_fuller_turn(db, activity.id) is False


async def test_done_tap_records_and_schedules(db, configured, notifier):
    activity = _seed(db)
    block = _block_of(db, activity)
    await _send_receipt(db=db, activity=activity, block=block, notifier=notifier)
    with patch.object(pna, "_schedule_block_complete") as sched:
        assert mark_done_and_schedule(db, activity.id) is True
    sched.assert_called_once()
    ex = _exchange_of(db, activity)
    assert ex.done_at is not None


async def test_repeated_done_taps_schedule_exactly_one_full_report(db, configured, notifier):
    # #505: a second "done" tap on an already-done exchange must NOT schedule another
    # full-report generation. mark_done is idempotent (done_at set once), but the caller
    # previously discarded its return and scheduled unconditionally, so rapid/repeat taps
    # queued redundant generations (deduped only at fire time, after spend may be live).
    activity = _seed(db)
    block = _block_of(db, activity)
    await _send_receipt(db=db, activity=activity, block=block, notifier=notifier)
    with patch.object(pna, "_schedule_block_complete") as sched:
        first = mark_done_and_schedule(db, activity.id)
        second = mark_done_and_schedule(db, activity.id)
        third = mark_done_and_schedule(db, activity.id)
    assert first is True               # the first tap schedules
    assert second is False             # the duplicate taps are scheduling no-ops
    assert third is False
    sched.assert_called_once()         # exactly ONE full-report job from repeated taps
    ex = _exchange_of(db, activity)
    assert ex.done_at is not None


async def test_done_tap_noops_when_exchange_closed(db, configured, notifier):
    activity = _seed(db)
    ex = _exchange_of(db, activity)
    ex.fuller_sent_at = datetime.now(timezone.utc)  # already closed
    db.add(ex)
    db.commit()
    with patch.object(pna, "_schedule_block_complete") as sched:
        assert mark_done_and_schedule(db, activity.id) is False
    sched.assert_not_called()


# --- rollback inertness -------------------------------------------------------


async def test_block_complete_runs_opener_after_cadence_flag_flipped_off(
    db, configured, notifier, monkeypatch
):
    # Flag flipped off but the prompt stays two-stage: a pending block-complete
    # check adopts the now-current cadence (the LLM opener), decided at fire time.
    activity = _seed(db)
    block = _block_of(db, activity)
    monkeypatch.setattr(settings, "COACH_RECEIPT_CADENCE", False)
    with patch.object(pna, "_run_opener_stage", new=AsyncMock(return_value=None)) as opener:
        await process_block_complete(
            db=db, block_id=str(block.id), activity_id=str(activity.id), notifier=notifier
        )
    opener.assert_called_once()


async def test_block_complete_inert_after_rollback_to_single_shot(
    db, configured, notifier, monkeypatch
):
    activity = _seed(db)
    block = _block_of(db, activity)
    monkeypatch.setattr(settings, "COACH_PROMPT_ID", "coach_report_v10")  # single-shot
    with patch("app.services.coach.service.AnthropicClient") as client_cls, \
         patch.object(pna, "_run_opener_stage") as opener:
        result = await process_block_complete(
            db=db, block_id=str(block.id), activity_id=str(activity.id), notifier=notifier
        )
    assert result is None
    opener.assert_not_called()
    client_cls.assert_not_called()
