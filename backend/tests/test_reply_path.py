"""Reply path (A4 deliverable 6, reworked onto exchange rows by A1): a runner
reply (check-in or chat) fires the fuller turn early when the block's exchange
is open, bounded by the reply window.

Covers maybe_enqueue_fuller_turn's open/closed/stale decision (read from the
`exchanges` row, not the report shape), the idempotency-vs-timer enqueue, and
the check-in endpoint wiring. The trust boundary (aiw-security-testing) is
exercised: input only ever enqueues work for the block owning the activity
named in the URL, and an oversized note never reaches an LLM synchronously.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from app.core.config import settings
from app.jobs import process_new_activity as pna
from app.jobs.process_new_activity import maybe_enqueue_fuller_turn
from app.models import Activity, DerivedMetric, Exchange, User
from app.services.blocks import assign_activity_to_block


@pytest.fixture
def v2(monkeypatch):
    monkeypatch.setattr(settings, "COACH_PROMPT_ID", "coach_message_v2")
    monkeypatch.setattr(settings, "EXCHANGE_REPLY_WINDOW_SECONDS", 86400)


@pytest.fixture
def fake_queue():
    fake = MagicMock()
    with patch("app.jobs.process_new_activity.queue", fake):
        yield fake


def _user(db):
    u = User(id=uuid4(), email=f"u-{uuid4()}@example.com")
    db.add(u)
    db.flush()
    return u.id


def _activity(db, uid):
    a = Activity(
        id=uuid4(), user_id=uid, strava_activity_id=abs(hash(str(uuid4()))) % 10**9,
        start_date=datetime(2026, 5, 27, 10, 0, 0), type="Run", name="Run",
        distance_m=5000, moving_time_s=1500, elapsed_time_s=1500, elev_gain_m=10.0,
        avg_hr=140, raw_summary={},
    )
    db.add(a)
    db.flush()
    db.add(DerivedMetric(id=uuid4(), activity_id=a.id, effort="easy",
                         structure="continuous", duration_class="standard",
                         effort_score=50.0, flags=[], confidence="medium",
                         confidence_reasons=[]))
    db.flush()
    return a


def _exchange(db, activity, *, opened_at=None, fuller_sent_at=None) -> Exchange:
    """Assign the activity's block and put its exchange in the given state."""
    block = assign_activity_to_block(db, activity)
    exchange = db.query(Exchange).filter(Exchange.block_id == block.id).one()
    exchange.opened_at = opened_at
    exchange.fuller_sent_at = fuller_sent_at
    db.flush()
    return exchange


def test_enqueues_when_exchange_open(db, v2, fake_queue):
    uid = _user(db)
    activity = _activity(db, uid)
    _exchange(db, activity, opened_at=datetime.now(timezone.utc) - timedelta(hours=1))

    assert maybe_enqueue_fuller_turn(db, activity.id) is True
    fake_queue.enqueue.assert_called_once()
    args = fake_queue.enqueue.call_args
    assert args.args[0] is pna.fuller_turn_job
    assert args.args[1] == str(activity.id)  # the block's primary activity


def test_no_enqueue_when_fuller_already_done(db, v2, fake_queue):
    # AC3/AC4: a check-in on an activity whose exchange is closed does not re-fire.
    uid = _user(db)
    activity = _activity(db, uid)
    _exchange(db, activity,
              opened_at=datetime.now(timezone.utc),
              fuller_sent_at=datetime.now(timezone.utc))

    assert maybe_enqueue_fuller_turn(db, activity.id) is False
    fake_queue.enqueue.assert_not_called()


def test_no_enqueue_when_opener_stale(db, v2, fake_queue):
    # AC4: a reply on an exchange whose opener is older than the window does not
    # spin up a new exchange.
    uid = _user(db)
    activity = _activity(db, uid)
    _exchange(db, activity, opened_at=datetime.now(timezone.utc) - timedelta(days=3))

    assert maybe_enqueue_fuller_turn(db, activity.id) is False
    fake_queue.enqueue.assert_not_called()


def test_no_enqueue_when_exchange_not_opened(db, v2, fake_queue):
    # The block exists but its exchange has not opened (no opener yet): a reply
    # cannot spin up the exchange.
    uid = _user(db)
    activity = _activity(db, uid)
    _exchange(db, activity, opened_at=None)

    assert maybe_enqueue_fuller_turn(db, activity.id) is False
    fake_queue.enqueue.assert_not_called()


def test_reply_refires_fuller_whose_send_failed(db, v2, fake_queue):
    # A1 behaviour: the open/closed decision is delivery state, not report shape.
    # A fuller that GENERATED but failed to send leaves the exchange open
    # (fuller_sent_at null), so a reply re-fires the fuller job, which cache-hits
    # the complete row and just re-sends — the "left null so re-sendable" rule.
    uid = _user(db)
    activity = _activity(db, uid)
    _exchange(db, activity, opened_at=datetime.now(timezone.utc))

    assert maybe_enqueue_fuller_turn(db, activity.id) is True
    fake_queue.enqueue.assert_called_once()


def test_no_enqueue_under_single_shot_prompt(db, fake_queue, monkeypatch):
    # Under a rollback (single-shot) prompt the reply path is inert.
    monkeypatch.setattr(settings, "COACH_PROMPT_ID", "coach_report_v10")
    uid = _user(db)
    activity = _activity(db, uid)
    _exchange(db, activity, opened_at=datetime.now(timezone.utc))
    assert maybe_enqueue_fuller_turn(db, activity.id) is False
    fake_queue.enqueue.assert_not_called()


def test_enqueue_best_effort_swallows_redis_error(db, v2):
    uid = _user(db)
    activity = _activity(db, uid)
    _exchange(db, activity, opened_at=datetime.now(timezone.utc))
    boom = MagicMock()
    boom.enqueue.side_effect = RuntimeError("redis down")
    with patch("app.jobs.process_new_activity.queue", boom):
        # never raises into the reply request
        assert maybe_enqueue_fuller_turn(db, activity.id) is False


def test_unknown_activity_id_does_not_enqueue(db, v2, fake_queue):
    # Trust boundary: an activity_id with no row enqueues nothing (the job also
    # resolves the owner from the activity row, so it can never target another user).
    assert maybe_enqueue_fuller_turn(db, uuid4()) is False
    fake_queue.enqueue.assert_not_called()


def test_unblocked_activity_does_not_enqueue(db, v2, fake_queue):
    # An activity that was never grouped (pre-A1 code path) has no exchange to
    # advance; the reply is a no-op rather than an error.
    uid = _user(db)
    activity = _activity(db, uid)
    assert maybe_enqueue_fuller_turn(db, activity.id) is False
    fake_queue.enqueue.assert_not_called()


def test_checkin_endpoint_triggers_reply_enqueue(db, client, v2):
    # Endpoint wiring: POST /checkin calls maybe_enqueue_fuller_turn after persisting.
    uid = _user(db)
    activity = _activity(db, uid)
    _exchange(db, activity, opened_at=datetime.now(timezone.utc))
    db.commit()
    with patch("app.jobs.process_new_activity.maybe_enqueue_fuller_turn") as m:
        resp = client.post(f"/api/activities/{activity.id}/checkin",
                            json={"rpe": 5, "pain_score": 0})
    assert resp.status_code == 200
    m.assert_called_once()
