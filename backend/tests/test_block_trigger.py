"""A1 block-complete trigger (ADR 0011): the debounced opener.

Pins the new cadence: processing an activity schedules a block-complete check
at +BLOCK_GAP_SECONDS instead of opening the exchange inline; the check no-ops
when superseded (a newer member's check owns the block) or rolled back to a
single-shot prompt; exactly one opener fires per block, on the primary
activity, with the exchange row carrying the lifecycle and sentinels.

LLM calls are stubbed; timing fixtures are synthetic test setup.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from app.core.config import settings
from app.jobs import exchange_ops
from app.jobs import process_new_activity as pna
from app.jobs.cadence import opener_fuller
from app.jobs.process_new_activity import (
    maybe_enqueue_fuller_turn,
    process_block_complete,
    process_new_activity,
)
from app.models import (
    Activity,
    Block,
    DerivedMetric,
    Exchange,
    StravaAccount,
    User,
    UserProfile,
)
from app.services.blocks import assign_activity_to_block
from app.services.coach.llm import MessageResult
from app.services.notifications import set_notifier
from app.services.notifications.in_memory_adapter import InMemoryNotifier
from app.services.strava_ingestion import InMemoryStravaAdapter, set_strava_port

pytestmark = pytest.mark.asyncio

T0 = datetime(2026, 5, 27, 8, 0, 0)


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setattr(settings, "COACH_PROMPT_ID", "coach_message_v2")
    monkeypatch.setattr(settings, "APP_BASE_URL", "https://app.example.com")
    monkeypatch.setattr(settings, "TELEGRAM_BOT_TOKEN", "123:ABC")
    monkeypatch.setattr(settings, "TELEGRAM_CHAT_ID", "runner-chat")


@pytest.fixture
def notifier():
    n = InMemoryNotifier()
    set_notifier(n)
    yield n
    set_notifier(None)


@pytest.fixture
def strava_adapter():
    adapter = InMemoryStravaAdapter()
    set_strava_port(adapter)
    yield adapter
    set_strava_port(None)


def _seed_user(db):
    user = User(email=f"u-{uuid4()}@example.com")
    db.add(user)
    db.commit()
    db.add(UserProfile(user_id=user.id, goal_type="general",
                       experience_level="intermediate", weekly_days_available=4, max_hr=190))
    account = StravaAccount(user_id=user.id, strava_athlete_id=abs(hash(str(user.id))) % 10**9,
                            access_token="t", refresh_token="r", expires_at=9999999999, scope="read")
    db.add(account)
    db.commit()
    return user, account


def _seed_activity(db, user, *, start=T0, elapsed=1500, type="Run", flags=None):
    activity = Activity(user_id=user.id, strava_activity_id=abs(hash(str(uuid4()))) % 10**9,
                        start_date=start, type=type, name="Test", distance_m=5000,
                        moving_time_s=elapsed, elapsed_time_s=elapsed, elev_gain_m=10.0,
                        avg_hr=140, raw_summary={})
    db.add(activity)
    db.commit()
    db.add(DerivedMetric(activity_id=activity.id, effort="easy", structure="continuous",
                         duration_class="standard", effort_score=50.0, flags=flags or [],
                         confidence="medium", confidence_reasons=[]))
    db.commit()
    db.refresh(activity)
    return activity


def _text(t):
    return {"type": "text", "text": t}


def _tail(**tail):
    return {"type": "tool_use", "name": "record_coach_tail", "input": tail}


def _opener_blocks(*, schedule):
    return [_text("Nice work!"), _tail(
        headline="Quick", next_steps=[], risks=[],
        questions=[{"question": "How did it feel?", "reason": "calibrate",
                    "options": [{"id": "rpe", "label": "Rate 1-10", "kind": "rpe"}]}],
        schedule_fuller_turn=schedule,
    )]


def _client(*results):
    fake = AsyncMock()
    fake.generate_coach_message = AsyncMock(
        side_effect=[MessageResult(content_blocks=b, stop_reason="end_turn") for b in results]
    )
    return fake


def _exchange(db, block):
    return db.query(Exchange).filter(Exchange.block_id == block.id).one()


# --- the pipeline schedules the completion check, no inline opener (AC1) -------


async def test_pipeline_schedules_block_complete_instead_of_inline_opener(
    db, configured, notifier, strava_adapter
):
    user, account = _seed_user(db)
    strava_adapter.seed_activities([{
        "id": 9001, "name": "Morning run", "type": "Run",
        "start_date": "2026-05-27T08:00:00Z", "distance": 8200,
        "moving_time": 2400, "elapsed_time": 2400, "total_elevation_gain": 40,
        "average_heartrate": 140,
    }])

    with patch.object(exchange_ops, "schedule_block_complete") as sched, \
         patch("app.services.coach.service.AnthropicClient") as client_cls:
        result = await process_new_activity(
            db=db, account=account, strava_activity_id=9001,
            strava_port=strava_adapter, notifier=notifier,
        )

    assert result is None
    assert len(notifier.sent) == 0          # nothing fires at processing time
    client_cls.assert_not_called()          # no opener generation inline
    activity = db.query(Activity).filter(Activity.strava_activity_id == 9001).one()
    assert activity.block_id is not None    # assigned at ingestion
    sched.assert_called_once_with(str(activity.block_id), str(activity.id))


# --- block-complete opens the exchange on the primary --------------------------


async def test_block_complete_opens_exchange_and_notifies_primary(
    db, configured, notifier
):
    user, _ = _seed_user(db)
    activity = _seed_activity(db, user)
    block = assign_activity_to_block(db, activity)

    with patch("app.services.coach.service.AnthropicClient",
               return_value=_client(_opener_blocks(schedule=True))), \
         patch.object(exchange_ops, "schedule_fuller_turn") as sched:
        result = await process_block_complete(
            db=db, block_id=str(block.id), activity_id=str(activity.id), notifier=notifier
        )

    assert result is not None
    assert len(notifier.sent) == 1
    exchange = _exchange(db, block)
    assert exchange.opened_at is not None
    assert exchange.opener_sent_at is not None
    assert exchange.fuller_sent_at is None
    sched.assert_called_once_with(str(activity.id))
    # The Activity sentinels are no longer written under the two-stage path.
    db.refresh(activity)
    assert activity.opener_notification_sent_at is None
    assert activity.coach_notification_sent_at is None


# --- debounce idempotency (AC2) -------------------------------------------------


async def test_stale_completion_check_noops_when_superseded(db, configured, notifier):
    user, _ = _seed_user(db)
    first = _seed_activity(db, user, start=T0, elapsed=1200, type="Walk")
    block = assign_activity_to_block(db, first)
    second = _seed_activity(db, user, start=T0 + timedelta(seconds=1800), elapsed=2400)
    assert assign_activity_to_block(db, second).id == block.id

    # The first activity's check fires: superseded, a fresh check is pending.
    with patch("app.services.coach.service.AnthropicClient") as client_cls:
        result = await process_block_complete(
            db=db, block_id=str(block.id), activity_id=str(first.id), notifier=notifier
        )
    assert result is None
    client_cls.assert_not_called()
    assert len(notifier.sent) == 0
    assert _exchange(db, block).opened_at is None

    # The second activity's check fires: the block is complete, one opener, on
    # the primary (the run, not the walk).
    db.refresh(block)
    with patch("app.services.coach.service.AnthropicClient",
               return_value=_client(_opener_blocks(schedule=False))), \
         patch.object(exchange_ops, "schedule_fuller_turn"):
        result = await process_block_complete(
            db=db, block_id=str(block.id), activity_id=str(second.id), notifier=notifier
        )
    assert result is not None
    assert len(notifier.sent) == 1
    assert block.primary_activity_id == second.id

    # A re-fire of either check is a no-op: exactly one opener per block.
    with patch("app.services.coach.service.AnthropicClient") as client_cls2:
        again = await process_block_complete(
            db=db, block_id=str(block.id), activity_id=str(second.id), notifier=notifier
        )
    assert again is None
    assert len(notifier.sent) == 1
    client_cls2.assert_not_called()


# --- rollback gate (AC6) ---------------------------------------------------------


async def test_block_complete_noops_under_single_shot_prompt(
    db, configured, notifier, monkeypatch, caplog
):
    user, _ = _seed_user(db)
    activity = _seed_activity(db, user)
    block = assign_activity_to_block(db, activity)

    monkeypatch.setattr(settings, "COACH_PROMPT_ID", "coach_report_v10")
    with patch("app.services.coach.service.AnthropicClient") as client_cls:
        result = await process_block_complete(
            db=db, block_id=str(block.id), activity_id=str(activity.id), notifier=notifier
        )

    assert result is None
    client_cls.assert_not_called()
    assert len(notifier.sent) == 0
    assert "single-shot" in caplog.text


# --- reply path reads exchange state ---------------------------------------------


async def test_reply_into_open_exchange_enqueues_fuller_on_primary(db, configured):
    user, _ = _seed_user(db)
    walk = _seed_activity(db, user, start=T0, elapsed=1200, type="Walk")
    block = assign_activity_to_block(db, walk)
    run = _seed_activity(db, user, start=T0 + timedelta(seconds=1800), elapsed=2400)
    assign_activity_to_block(db, run)
    db.refresh(block)
    exchange = _exchange(db, block)
    exchange.opened_at = datetime.now(timezone.utc)
    db.commit()

    queue = type("Q", (), {"enqueue": staticmethod(lambda *a, **k: None)})
    captured = {}

    def fake_enqueue(func, *args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs

    queue.enqueue = fake_enqueue
    with patch.object(opener_fuller, "queue", queue):
        # the reply lands on the WALK; the fuller still fires on the primary
        assert maybe_enqueue_fuller_turn(db, walk.id) is True

    assert captured["args"] == (str(block.primary_activity_id),)


async def test_reply_after_close_or_before_open_does_nothing(db, configured):
    user, _ = _seed_user(db)
    activity = _seed_activity(db, user)
    block = assign_activity_to_block(db, activity)
    exchange = _exchange(db, block)

    # Not yet opened: a reply cannot spin up an exchange.
    assert maybe_enqueue_fuller_turn(db, activity.id) is False

    # Closed: never re-fires (AC3).
    exchange.opened_at = datetime.now(timezone.utc)
    exchange.fuller_sent_at = datetime.now(timezone.utc)
    db.commit()
    assert maybe_enqueue_fuller_turn(db, activity.id) is False


async def test_force_regeneration_never_resets_exchange_sentinels(db, configured):
    # AC5: force=true regenerates the report artifact but re-fires nothing — the
    # exchange's delivery sentinels are untouched by regeneration.
    from app.services.coach.service import generate_fuller

    user, _ = _seed_user(db)
    activity = _seed_activity(db, user)
    block = assign_activity_to_block(db, activity)
    exchange = _exchange(db, block)
    opened = datetime.now(timezone.utc) - timedelta(hours=2)
    sent = datetime.now(timezone.utc) - timedelta(hours=1)
    exchange.opened_at = opened
    exchange.opener_sent_at = sent
    exchange.fuller_sent_at = sent
    db.commit()

    fuller = [_text("Updated full take."), _tail(
        headline="Again", next_steps=[], risks=[],
        questions=[{"question": "How did it feel?", "reason": "calibrate",
                    "options": [{"id": "rpe", "label": "Rate 1-10", "kind": "rpe"}]}],
    )]
    with patch("app.services.coach.service.AnthropicClient",
               return_value=_client(fuller)):
        read = await generate_fuller(db, str(activity.id), force=True)

    assert read is not None  # the artifact regenerated
    db.refresh(exchange)
    # (SQLite returns naive datetimes; compare in naive UTC)
    assert exchange.opener_sent_at == sent.replace(tzinfo=None)
    assert exchange.fuller_sent_at == sent.replace(tzinfo=None)  # still closed; nothing re-fires


async def test_reply_outside_window_does_nothing(db, configured):
    user, _ = _seed_user(db)
    activity = _seed_activity(db, user)
    block = assign_activity_to_block(db, activity)
    exchange = _exchange(db, block)
    exchange.opened_at = datetime.now(timezone.utc) - timedelta(
        seconds=settings.EXCHANGE_REPLY_WINDOW_SECONDS + 60
    )
    db.commit()

    assert maybe_enqueue_fuller_turn(db, activity.id) is False
