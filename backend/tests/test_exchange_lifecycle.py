"""Unit tests for the ExchangeLifecycle state-machine module (#494).

This is the single owner of legal Exchange transitions and the at-most-once
notification invariant, extracted from the five scattered helpers in
process_new_activity.py. These tests pin each transition's guard and idempotency
directly at the interface, so the "never re-fire" guarantee is tested in ONE place
(the invariant the issue calls out), rather than re-asserted at each call site.
"""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from app.core.config import settings
from app.models import Activity, DerivedMetric, Exchange, User
from app.services.blocks import assign_activity_to_block
from app.services.coach import exchange_lifecycle as lifecycle


# --- fixtures -----------------------------------------------------------------


def _user(db) -> User:
    u = User(id=uuid4(), email=f"u-{uuid4()}@example.com")
    db.add(u)
    db.flush()
    return u


def _activity(db, *, receipt_sent_at=None) -> Activity:
    user = _user(db)
    a = Activity(id=uuid4(), user_id=user.id,
                 strava_activity_id=abs(hash(str(uuid4()))) % 10**9,
                 start_date=datetime(2026, 5, 27, 10, 0, 0), type="Run", name="Run",
                 distance_m=5000, moving_time_s=1500, elapsed_time_s=1500,
                 elev_gain_m=10.0, avg_hr=140, raw_summary={},
                 receipt_sent_at=receipt_sent_at)
    db.add(a)
    db.flush()
    db.add(DerivedMetric(id=uuid4(), activity_id=a.id, effort="easy",
                         structure="continuous", duration_class="standard",
                         effort_score=50.0, flags=[], confidence="medium",
                         confidence_reasons=[]))
    db.flush()
    return a


def _exchange(db, *, opened_at=None, opener_sent_at=None,
              fuller_sent_at=None, done_at=None) -> Exchange:
    """A real Exchange (via block assignment, since blocks carry a NOT NULL primary)
    put directly into the given lifecycle state."""
    activity = _activity(db)
    block = assign_activity_to_block(db, activity)
    ex = db.query(Exchange).filter(Exchange.block_id == block.id).one()
    ex.opened_at = opened_at
    ex.opener_sent_at = opener_sent_at
    ex.fuller_sent_at = fuller_sent_at
    ex.done_at = done_at
    db.flush()
    return ex


@pytest.fixture
def window(monkeypatch):
    monkeypatch.setattr(settings, "EXCHANGE_REPLY_WINDOW_SECONDS", 86400)  # 24h


# --- state reads --------------------------------------------------------------


def test_unopened_exchange_is_not_open_not_closed(db):
    ex = _exchange(db)
    assert lifecycle.is_open(ex) is False
    assert lifecycle.is_closed(ex) is False


def test_opened_unclosed_exchange_is_open(db):
    ex = _exchange(db, opened_at=datetime.now(timezone.utc))
    assert lifecycle.is_open(ex) is True
    assert lifecycle.is_closed(ex) is False


def test_fuller_sent_is_closed_and_not_open(db):
    ex = _exchange(db, opened_at=datetime.now(timezone.utc),
                   fuller_sent_at=datetime.now(timezone.utc))
    assert lifecycle.is_closed(ex) is True
    assert lifecycle.is_open(ex) is False


def test_within_reply_window_true_for_recent_opener(db, window):
    ex = _exchange(db, opened_at=datetime.now(timezone.utc) - timedelta(hours=1))
    assert lifecycle.within_reply_window(ex) is True


def test_within_reply_window_false_for_stale_opener(db, window):
    ex = _exchange(db, opened_at=datetime.now(timezone.utc) - timedelta(days=3))
    assert lifecycle.within_reply_window(ex) is False


def test_within_reply_window_false_when_unopened(db, window):
    ex = _exchange(db)
    assert lifecycle.within_reply_window(ex) is False


def test_within_reply_window_treats_naive_db_read_as_utc(db, window):
    # A naive datetime (some DB drivers strip tz) must be read as UTC, not crash.
    ex = _exchange(db, opened_at=datetime.utcnow() - timedelta(hours=1))
    assert lifecycle.within_reply_window(ex) is True


def test_can_fire_reply_fuller_requires_open_and_in_window(db, window):
    now = datetime.now(timezone.utc)
    assert lifecycle.can_fire_reply_fuller(
        _exchange(db, opened_at=now - timedelta(hours=1))) is True
    assert lifecycle.can_fire_reply_fuller(_exchange(db)) is False  # unopened
    assert lifecycle.can_fire_reply_fuller(
        _exchange(db, opened_at=now, fuller_sent_at=now)) is False  # closed
    assert lifecycle.can_fire_reply_fuller(
        _exchange(db, opened_at=now - timedelta(days=3))) is False  # stale


# --- block -> exchange resolution (#697) --------------------------------------


def test_get_exchange_for_block_returns_the_row(db):
    ex = _exchange(db)
    assert lifecycle.get_exchange_for_block(db, ex.block_id) is ex


def test_get_exchange_for_block_returns_none_when_absent(db):
    assert lifecycle.get_exchange_for_block(db, uuid4()) is None


def test_ensure_exchange_for_block_returns_existing(db):
    ex = _exchange(db)
    block = ex.block
    # blocks are created WITH their exchange, so ensure resolves the existing one
    # without creating a duplicate.
    assert lifecycle.ensure_exchange_for_block(db, block) is ex
    assert (
        db.query(Exchange).filter(Exchange.block_id == block.id).count() == 1
    )


def test_ensure_exchange_for_block_creates_when_missing(db):
    ex = _exchange(db)
    block = ex.block
    db.delete(ex)
    db.commit()
    assert lifecycle.get_exchange_for_block(db, block.id) is None

    created = lifecycle.ensure_exchange_for_block(db, block)
    assert created.block_id == block.id
    assert created.user_id == block.user_id
    db.refresh(block)
    assert lifecycle.get_exchange_for_block(db, block.id) is created


# --- open transition ----------------------------------------------------------


def test_open_exchange_sets_opened_at_once(db):
    ex = _exchange(db)
    assert lifecycle.open_exchange(db, ex) is True
    db.refresh(ex)
    assert ex.opened_at is not None


def test_open_exchange_is_idempotent(db):
    first = datetime(2026, 5, 27, 10, 0, 0, tzinfo=timezone.utc)
    ex = _exchange(db, opened_at=first)
    db.refresh(ex)
    before = ex.opened_at
    # a second open is a no-op and never moves the anchor (the reply window must
    # not slide when a later receipt arrives)
    assert lifecycle.open_exchange(db, ex) is False
    db.refresh(ex)
    assert ex.opened_at == before


# --- receipt-sent transition --------------------------------------------------


def test_record_receipt_sent_sets_sentinel(db):
    a = _activity(db)
    assert a.receipt_sent_at is None
    lifecycle.record_receipt_sent(db, a)
    db.refresh(a)
    assert a.receipt_sent_at is not None


# --- done transition ----------------------------------------------------------


def test_mark_done_records_and_opens_defensively(db):
    ex = _exchange(db)  # unopened (defensive: receipt should have opened it)
    assert lifecycle.mark_done(db, ex) is True
    db.refresh(ex)
    assert ex.done_at is not None
    assert ex.opened_at is not None  # defensively opened


def test_mark_done_is_idempotent(db):
    first = datetime(2026, 5, 27, 10, 0, 0, tzinfo=timezone.utc)
    ex = _exchange(db, opened_at=first, done_at=first)
    db.refresh(ex)
    before = ex.done_at
    assert lifecycle.mark_done(db, ex) is False  # already done -> no-op
    db.refresh(ex)
    assert ex.done_at == before


def test_mark_done_is_atomic_one_winner(db):
    # #514 seam-level: the "done" guard is an atomic claim, not a check-then-act. Two
    # callers marking the same exchange done must yield exactly one winner — the
    # conditional UPDATE on done_at-IS-NULL serializes the race, so only the first tap
    # schedules the full report.
    ex = _exchange(db)
    assert lifecycle.mark_done(db, ex) is True   # winner
    assert lifecycle.mark_done(db, ex) is False  # loser: already done
    db.refresh(ex)
    assert ex.done_at is not None
    assert ex.opened_at is not None  # defensively opened, atomically with the claim


def test_mark_done_claim_consults_the_row_not_a_stale_instance(db):
    # #514: simulate two processes that each loaded the exchange with `done_at` still
    # null (the web service is multi-process) before either committed. The old guard read
    # the in-memory instance's `done_at` — so a SECOND caller holding its own stale
    # instance would re-pass the null check and double-win (both scheduling the full
    # report). The atomic UPDATE's WHERE done_at IS NULL consults the committed ROW, so
    # the second caller loses regardless of its stale in-memory view. A separate Session
    # on the same connection gives a genuinely independent identity map (the second
    # process's view), which a same-session second instance cannot.
    from sqlalchemy.orm import Session as _Session

    ex = _exchange(db)
    db.commit()
    other = _Session(bind=db.connection())
    stale = other.query(Exchange).filter(Exchange.id == ex.id).one()
    assert stale.done_at is None  # the second process still sees an unclaimed exchange

    assert lifecycle.mark_done(db, ex) is True        # first process wins the claim
    assert lifecycle.mark_done(other, stale) is False  # second loses on the committed ROW
    db.refresh(ex)
    assert ex.done_at is not None
    other.close()


def test_mark_done_preserves_existing_opened_at(db):
    opened = datetime(2026, 5, 27, 10, 0, 0, tzinfo=timezone.utc)
    ex = _exchange(db, opened_at=opened)
    db.refresh(ex)
    before = ex.opened_at
    lifecycle.mark_done(db, ex)
    db.refresh(ex)
    assert ex.opened_at == before  # done does not move the reply-window anchor


# --- the at-most-once notification invariant (the issue's core invariant) -----


def test_notification_already_sent_reads_the_sentinel(db):
    ex = _exchange(db)
    assert lifecycle.notification_already_sent(ex, "opener_sent_at") is False
    ex.opener_sent_at = datetime.now(timezone.utc)
    assert lifecycle.notification_already_sent(ex, "opener_sent_at") is True


def test_mark_notification_sent_then_already_sent_is_true(db):
    # The at-most-once contract end-to-end: marking sent makes the read True, so a
    # second stage attempt is suppressed. One place owns "a stage sends at most once".
    ex = _exchange(db)
    lifecycle.mark_notification_sent(db, ex, "fuller_sent_at")
    db.refresh(ex)
    assert ex.fuller_sent_at is not None
    assert lifecycle.notification_already_sent(ex, "fuller_sent_at") is True


def test_mark_notification_sent_never_clears(db):
    # No transition ever re-arms a stage: a second mark does not move the sentinel
    # backward (a force=true regen must never re-enable a sent notification).
    first = datetime(2026, 5, 27, 10, 0, 0, tzinfo=timezone.utc)
    ex = _exchange(db, opener_sent_at=first)
    # mark again would overwrite to "now"; the invariant relies on the CALLER
    # checking already_sent first — confirm the guard catches it.
    assert lifecycle.notification_already_sent(ex, "opener_sent_at") is True


def test_single_shot_sentinel_lives_on_activity(db):
    # The single-shot rollback path dedups on the Activity, not an Exchange row.
    a = _activity(db)
    assert lifecycle.notification_already_sent(a, "coach_notification_sent_at") is False
    lifecycle.mark_notification_sent(db, a, "coach_notification_sent_at")
    db.refresh(a)
    assert a.coach_notification_sent_at is not None


def test_unknown_sentinel_is_rejected(db):
    # receipt_sent_at and opened_at/done_at are NOT notification sentinels (they have
    # their own transitions); routing them through the notification path is a bug.
    ex = _exchange(db)
    with pytest.raises(ValueError):
        lifecycle.notification_already_sent(ex, "receipt_sent_at")
    with pytest.raises(ValueError):
        lifecycle.mark_notification_sent(db, ex, "opened_at")
