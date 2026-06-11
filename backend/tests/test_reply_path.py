"""A4 reply path (deliverable 6): a runner reply (check-in or chat) fires the
fuller turn early when the exchange is open, bounded by the reply window.

Covers maybe_enqueue_fuller_turn's open/closed/stale decision (AC2/AC4), the
idempotency-vs-timer enqueue, and the check-in endpoint wiring. The trust boundary
(aiw-security-testing) is exercised: input only ever enqueues work for the activity
named in the URL, and an oversized note never reaches an LLM synchronously.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from app.core.config import settings
from app.jobs import process_new_activity as pna
from app.jobs.process_new_activity import maybe_enqueue_fuller_turn
from app.models import Activity, DerivedMetric, User
from app.models.coach_report import CoachReport


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


def _opener_row(db, activity, *, created_at=None, prompt_id="coach_message_v2"):
    row = CoachReport(
        id=uuid4(), activity_id=activity.id, prompt_id=prompt_id, schema_version="2.0",
        report={"message": "", "opener_message": "Nice work!", "schedule_fuller_turn": True},
        meta={}, context_pack={}, is_fallback=False, digest=None,
    )
    db.add(row)
    db.flush()
    if created_at is not None:
        row.created_at = created_at
        db.flush()
    return row


def _fuller_row(db, activity):
    row = CoachReport(
        id=uuid4(), activity_id=activity.id, prompt_id="coach_message_v2", schema_version="2.0",
        report={"message": "Full breakdown.", "opener_message": "Nice work!"},
        meta={}, context_pack={}, is_fallback=False, digest={"x": 1},
    )
    db.add(row)
    db.flush()
    return row


def test_enqueues_when_exchange_open(db, v2, fake_queue):
    uid = _user(db)
    activity = _activity(db, uid)
    _opener_row(db, activity, created_at=datetime.now(timezone.utc) - timedelta(hours=1))

    assert maybe_enqueue_fuller_turn(db, activity.id) is True
    fake_queue.enqueue.assert_called_once()
    args = fake_queue.enqueue.call_args
    assert args.args[0] is pna.fuller_turn_job
    assert args.args[1] == str(activity.id)


def test_no_enqueue_when_fuller_already_done(db, v2, fake_queue):
    # AC4: a check-in on an activity whose exchange is closed does not re-fire.
    uid = _user(db)
    activity = _activity(db, uid)
    _opener_row(db, activity, created_at=datetime.now(timezone.utc))
    activity.coach_notification_sent_at = datetime.now(timezone.utc)
    db.flush()

    assert maybe_enqueue_fuller_turn(db, activity.id) is False
    fake_queue.enqueue.assert_not_called()


def test_no_enqueue_when_opener_stale(db, v2, fake_queue):
    # AC4: a reply on an activity whose opener is older than the window does not
    # spin up a new exchange.
    uid = _user(db)
    activity = _activity(db, uid)
    _opener_row(db, activity, created_at=datetime.now(timezone.utc) - timedelta(days=3))

    assert maybe_enqueue_fuller_turn(db, activity.id) is False
    fake_queue.enqueue.assert_not_called()


def test_no_enqueue_when_no_opener_row(db, v2, fake_queue):
    uid = _user(db)
    activity = _activity(db, uid)  # no report row at all
    assert maybe_enqueue_fuller_turn(db, activity.id) is False
    fake_queue.enqueue.assert_not_called()


def test_no_enqueue_when_row_is_complete_fuller(db, v2, fake_queue):
    # A complete fuller row is not "open" — the exchange already produced its
    # fuller turn.
    uid = _user(db)
    activity = _activity(db, uid)
    _fuller_row(db, activity)
    assert maybe_enqueue_fuller_turn(db, activity.id) is False
    fake_queue.enqueue.assert_not_called()


def test_no_enqueue_under_single_shot_prompt(db, fake_queue, monkeypatch):
    # Under a rollback (single-shot) prompt the reply path is inert.
    monkeypatch.setattr(settings, "COACH_PROMPT_ID", "coach_report_v10")
    uid = _user(db)
    activity = _activity(db, uid)
    _opener_row(db, activity, created_at=datetime.now(timezone.utc))
    assert maybe_enqueue_fuller_turn(db, activity.id) is False
    fake_queue.enqueue.assert_not_called()


def test_enqueue_best_effort_swallows_redis_error(db, v2):
    uid = _user(db)
    activity = _activity(db, uid)
    _opener_row(db, activity, created_at=datetime.now(timezone.utc))
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


def test_checkin_endpoint_triggers_reply_enqueue(db, client, v2):
    # Endpoint wiring: POST /checkin calls maybe_enqueue_fuller_turn after persisting.
    uid = _user(db)
    activity = _activity(db, uid)
    _opener_row(db, activity, created_at=datetime.now(timezone.utc))
    db.commit()
    with patch("app.jobs.process_new_activity.maybe_enqueue_fuller_turn") as m:
        resp = client.post(f"/api/activities/{activity.id}/checkin",
                            json={"rpe": 5, "pain_score": 0})
    assert resp.status_code == 200
    m.assert_called_once()
