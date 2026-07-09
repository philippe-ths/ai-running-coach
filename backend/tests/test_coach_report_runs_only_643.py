"""#643: automatic coach reports only for runs; receipt fires for every activity.

The per-activity receipt (check-in notification) must still fire for EVERY
activity type. The automatic coach report (the two-stage block-complete report,
and the single-shot on-ingest report) must only generate when the block's
primary activity is a run. On-request regeneration is a separate path and stays
ungated (not exercised here).

"Running" is the coarse Strava ``type == "Run"`` (ordinary, trail, and
Strava-app treadmill runs all keep ``type == "Run"``), matching
``blocks.pick_primary`` so the gate agrees with which activity is chosen as the
block primary the report generates on. A distinct ``type`` such as "VirtualRun"
is a known gap (follow-up).
"""

from datetime import datetime
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

import pytest

from app.core.config import settings
from app.jobs import process_new_activity as pna
from app.jobs.process_new_activity import (
    _run_single_shot,
    _send_receipt,
    process_block_complete,
)
from app.models import Activity, Block, DerivedMetric, Exchange, StravaAccount, User, UserProfile
from app.services.blocks import assign_activity_to_block
from app.services.coach.llm import MessageResult
from app.services.coach.service import get_active_report_row
from app.services.notifications import set_notifier
from app.services.notifications.in_memory_adapter import InMemoryNotifier

pytestmark = pytest.mark.asyncio


@pytest.fixture
def receipt_cadence(monkeypatch):
    monkeypatch.setattr(settings, "COACH_PROMPT_ID", "coach_message_v6")
    monkeypatch.setattr(settings, "COACH_RECEIPT_CADENCE", True)
    monkeypatch.setattr(settings, "APP_BASE_URL", "https://app.example.com")
    monkeypatch.setattr(settings, "TELEGRAM_BOT_TOKEN", "123:ABC")
    monkeypatch.setattr(settings, "TELEGRAM_CHAT_ID", "runner-chat")


@pytest.fixture
def notifier():
    n = InMemoryNotifier()
    set_notifier(n)
    yield n
    set_notifier(None)


def _seed(db, *, type="Run", start=None, name="Test activity", user=None) -> Activity:
    if user is None:
        user = User(email=f"u-{uuid4()}@example.com")
        db.add(user)
        db.commit()
        db.add(UserProfile(user_id=user.id, goal_type="general",
                           experience_level="intermediate", weekly_days_available=4, max_hr=190))
        db.add(StravaAccount(user_id=user.id, strava_athlete_id=abs(hash(str(user.id))) % 10**9,
                             access_token="t", refresh_token="r", expires_at=9999999999, scope="read"))
        db.commit()
    activity = Activity(user_id=user.id, strava_activity_id=abs(hash(str(uuid4()))) % 10**9,
                        start_date=start or datetime(2026, 5, 27, 10, 0, 0), type=type,
                        name=name, distance_m=5000, moving_time_s=1500,
                        elapsed_time_s=1500, elev_gain_m=10.0, avg_hr=140, raw_summary={})
    db.add(activity)
    db.commit()
    db.add(DerivedMetric(activity_id=activity.id, effort="easy", structure="continuous",
                         duration_class="standard", effort_score=50.0, flags=[],
                         confidence="medium", confidence_reasons=[]))
    db.commit()
    db.refresh(activity)
    return activity


def _block_of(db, activity) -> Block:
    if activity.block_id is None:
        assign_activity_to_block(db, activity)
        db.refresh(activity)
    return db.query(Block).filter(Block.id == activity.block_id).one()


def _exchange_of(db, activity) -> Exchange:
    block = _block_of(db, activity)
    return db.query(Exchange).filter(Exchange.block_id == block.id).one()


def _text(t):
    return {"type": "text", "text": t}


def _tail(**tail):
    return {"type": "tool_use", "name": "record_coach_tail", "input": tail}


def _fuller_result():
    blocks = [_text("Aerobically that held up well — drift around 4%."), _tail(
        headline="Solid run",
        next_steps=[{"action": "Easy run", "details": "30 min", "why": "Recovery"}],
        risks=[],
        questions=[],
    )]
    return MessageResult(content_blocks=blocks, stop_reason="end_turn")


def _client_returning_fuller():
    fake = AsyncMock()
    fake.generate_coach_message = AsyncMock(return_value=_fuller_result())
    return fake


# --- two-stage receipt cadence ------------------------------------------------


async def test_walk_gets_receipt_but_no_auto_report(db, receipt_cadence, notifier):
    """A walk still gets its receipt, but the block-complete check generates no
    coach report and does not close the exchange."""
    walk = _seed(db, type="Walk", name="Evening walk")
    block = _block_of(db, walk)

    receipt = await _send_receipt(db=db, activity=walk, block=block, notifier=notifier)
    assert receipt is not None
    assert len(notifier.sent) == 1  # the receipt fired for the walk

    with patch("app.services.coach.service.AnthropicClient") as client_cls:
        result = await process_block_complete(
            db=db, block_id=str(block.id), activity_id=str(walk.id), notifier=notifier
        )

    assert result is None
    client_cls.assert_not_called()  # no LLM report for a walk
    assert len(notifier.sent) == 1  # still only the receipt
    ex = _exchange_of(db, walk)
    assert ex.fuller_sent_at is None  # not closed by a report that never came
    assert get_active_report_row(db, walk.id) is None


async def test_run_still_gets_auto_report(db, receipt_cadence, notifier):
    run = _seed(db, type="Run", name="Morning run")
    block = _block_of(db, run)
    await _send_receipt(db=db, activity=run, block=block, notifier=notifier)

    with patch("app.services.coach.service.AnthropicClient",
               return_value=_client_returning_fuller()):
        result = await process_block_complete(
            db=db, block_id=str(block.id), activity_id=str(run.id), notifier=notifier
        )

    assert result is not None
    ex = _exchange_of(db, run)
    assert ex.fuller_sent_at is not None  # closed: the report went
    row = get_active_report_row(db, run.id)
    assert row is not None and not row.is_fallback
    assert len(notifier.sent) == 2  # receipt + report


async def test_trail_run_still_gets_auto_report(db, receipt_cadence, notifier):
    """A trail run keeps Strava `type == "Run"` (only `sport_type` differs), so it
    is treated as a run and still gets its automatic report."""
    run = _seed(db, type="Run", name="Trail run")
    run.raw_summary = {"sport_type": "TrailRun"}
    db.commit()
    block = _block_of(db, run)
    await _send_receipt(db=db, activity=run, block=block, notifier=notifier)
    with patch("app.services.coach.service.AnthropicClient",
               return_value=_client_returning_fuller()):
        result = await process_block_complete(
            db=db, block_id=str(block.id), activity_id=str(run.id), notifier=notifier
        )
    assert result is not None
    row = get_active_report_row(db, run.id)
    assert row is not None and not row.is_fallback


async def test_mixed_block_reports_on_run_primary(db, receipt_cadence, notifier):
    """A run followed by a cooldown walk share a block whose primary is the run,
    so the block still gets a report (the block contains a run)."""
    run = _seed(db, type="Run", name="Long run", start=datetime(2026, 5, 27, 10, 0, 0))
    walk = _seed(db, type="Walk", name="Cooldown walk",
                 start=datetime(2026, 5, 27, 10, 40, 0), user=run.user)
    assign_activity_to_block(db, run)
    assign_activity_to_block(db, walk)
    db.refresh(run)
    db.refresh(walk)
    assert run.block_id == walk.block_id  # grouped
    block = db.query(Block).filter(Block.id == run.block_id).one()
    assert block.primary_activity_id == run.id  # the run is primary

    await _send_receipt(db=db, activity=walk, block=block, notifier=notifier)
    # the block-complete check fires on the LAST member (the walk), but generates
    # on the run primary, so a report is produced.
    with patch("app.services.coach.service.AnthropicClient",
               return_value=_client_returning_fuller()):
        result = await process_block_complete(
            db=db, block_id=str(block.id), activity_id=str(walk.id), notifier=notifier
        )

    assert result is not None
    row = get_active_report_row(db, run.id)
    assert row is not None and not row.is_fallback


# --- single-shot cadence ------------------------------------------------------


async def test_single_shot_walk_generates_no_report(db, notifier, monkeypatch):
    monkeypatch.setattr(settings, "COACH_PROMPT_ID", "coach_report_v10")  # single-shot
    monkeypatch.setattr(settings, "COACH_RECEIPT_CADENCE", False)
    walk = _seed(db, type="Walk", name="Lunch walk")
    with patch("app.services.coach.service.AnthropicClient") as client_cls:
        result = await _run_single_shot(
            db=db, activity=walk, strava_activity_id=walk.strava_activity_id, notifier=notifier
        )
    assert result is None
    client_cls.assert_not_called()
    assert len(notifier.sent) == 0


# --- opener/fuller cadence ----------------------------------------------------


async def test_opener_fuller_walk_generates_no_opener(db, notifier, monkeypatch):
    monkeypatch.setattr(settings, "COACH_PROMPT_ID", "coach_message_v6")  # two-stage
    monkeypatch.setattr(settings, "COACH_RECEIPT_CADENCE", False)  # opener/fuller
    walk = _seed(db, type="Walk", name="Recovery walk")
    block = _block_of(db, walk)
    with patch.object(pna, "_run_opener_stage", new=AsyncMock(return_value=None)) as opener:
        result = await process_block_complete(
            db=db, block_id=str(block.id), activity_id=str(walk.id), notifier=notifier
        )
    assert result is None
    opener.assert_not_called()  # no opener for a walk
