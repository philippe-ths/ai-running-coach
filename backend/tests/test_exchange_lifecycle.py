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
from sqlalchemy.exc import NoResultFound

from app.core.config import settings
from app.models import Activity, DerivedMetric, Exchange, User
from app.services.blocks import assign_activity_to_block
from app.services.coach import exchange_lifecycle as lifecycle


# --- fixtures -----------------------------------------------------------------


def _aware(dt: datetime) -> datetime:
    """SQLite returns naive datetimes; normalise for comparison against UTC."""
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


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


# --- structural row changes, absorbed from blocks.py (#740) -------------------
# The block-correction paths used to open-code these. The block-level tests
# (test_blocks_service.py) still pin the corrections end-to-end; these pin the
# shared implementations at the interface, including the branches a correction
# test never reaches (mixed nulls, a missing row, the transaction posture).


def test_create_exchange_for_block_starts_a_clean_exchange(db):
    ex = _exchange(db)
    block = ex.block
    db.delete(ex)
    db.commit()

    created = lifecycle.create_exchange_for_block(db, block)
    db.flush()

    assert created.block_id == block.id
    assert created.user_id == block.user_id
    for sentinel in lifecycle.STAGE_SENTINELS:
        assert getattr(created, sentinel) is None
    assert created.done_at is None


def test_create_exchange_for_block_inherits_every_stage_sentinel(db):
    # The split correction: the new half must start out already knowing what has been
    # delivered, so a correction can never re-open delivery (ADR 0011).
    stamp = datetime(2026, 5, 27, 10, 0, 0, tzinfo=timezone.utc)
    source = _exchange(db, opened_at=stamp, opener_sent_at=stamp, fuller_sent_at=stamp)
    target = _exchange(db)
    block = target.block
    db.delete(target)
    db.commit()

    created = lifecycle.create_exchange_for_block(db, block, inherit_from=source)
    db.flush()

    for sentinel in lifecycle.STAGE_SENTINELS:
        assert getattr(created, sentinel) is not None, f"{sentinel} was not inherited"
    assert lifecycle.is_closed(created) is True


def test_create_exchange_for_block_inherits_the_done_tap(db):
    # #750: `done_at` stays outside STAGE_SENTINELS (it is the runner's completion tap,
    # not a delivery stage) but IS inherited, because the window it guards is exactly
    # the window a correction lands in: tapped done, full report scheduled, not yet
    # sent. Dropping it there leaves the exchange reading as never-done, so a second
    # tap schedules a SECOND generation.
    stamp = datetime(2026, 5, 27, 10, 0, 0, tzinfo=timezone.utc)
    source = _exchange(db, opened_at=stamp, done_at=stamp)
    target = _exchange(db)
    block = target.block
    db.delete(target)
    db.commit()

    created = lifecycle.create_exchange_for_block(db, block, inherit_from=source)
    db.flush()

    assert created.opened_at is not None  # a stage sentinel: inherited
    assert _aware(created.done_at) == stamp  # the tap: inherited too (#750)


def test_create_exchange_for_block_inherits_every_inherited_sentinel(db):
    # The inherited set is INHERITED_SENTINELS (the delivery stages plus the tap), and
    # a split must carry all of it so the new half can neither re-fire a delivered
    # stage nor accept a duplicate "done".
    stamp = datetime(2026, 5, 27, 10, 0, 0, tzinfo=timezone.utc)
    source = _exchange(db, opened_at=stamp, opener_sent_at=stamp,
                       fuller_sent_at=stamp, done_at=stamp)
    target = _exchange(db)
    block = target.block
    db.delete(target)
    db.commit()

    created = lifecycle.create_exchange_for_block(db, block, inherit_from=source)
    db.flush()

    for sentinel in lifecycle.INHERITED_SENTINELS:
        assert getattr(created, sentinel) is not None, f"{sentinel} was not inherited"
    assert "done_at" in lifecycle.INHERITED_SENTINELS
    assert "done_at" not in lifecycle.STAGE_SENTINELS  # not a delivery stage


def test_create_exchange_for_block_leaves_an_untapped_source_untapped(db):
    # Inheritance copies the tap, it never invents one: a correction on an exchange the
    # runner never tapped must leave both sides freely tappable.
    stamp = datetime(2026, 5, 27, 10, 0, 0, tzinfo=timezone.utc)
    source = _exchange(db, opened_at=stamp)
    target = _exchange(db)
    block = target.block
    db.delete(target)
    db.commit()

    created = lifecycle.create_exchange_for_block(db, block, inherit_from=source)
    db.flush()

    assert created.done_at is None
    assert lifecycle.mark_done(db, created) is True  # still tappable


def test_create_exchange_for_block_does_not_commit(db):
    # Structural, not a transition: the exchange must land with the rest of the
    # caller's block work or not at all, so a correction that fails part-way through
    # leaves no orphan exchange behind.
    ex = _exchange(db)
    block = ex.block
    db.delete(ex)
    db.commit()

    savepoint = db.begin_nested()
    created = lifecycle.create_exchange_for_block(db, block)
    db.flush()
    assert lifecycle.get_exchange_for_block(db, block.id) is created
    savepoint.rollback()
    db.expire_all()

    assert lifecycle.get_exchange_for_block(db, block.id) is None


def test_absorb_exchange_keeps_the_later_of_two_set_sentinels(db):
    earlier = datetime(2026, 5, 27, 10, 0, 0, tzinfo=timezone.utc)
    later = datetime(2026, 5, 27, 12, 0, 0, tzinfo=timezone.utc)
    surviving = _exchange(db, opener_sent_at=earlier)
    absorbed = _exchange(db, opener_sent_at=later)
    # A merge reads both exchanges from the DB, so compare them as the DB returns
    # them (SQLite drops the tz that Postgres preserves; a mixed in-memory/DB pair
    # is not a shape the correction path can produce).
    db.expire_all()

    lifecycle.absorb_exchange(db, surviving, absorbed)

    assert _aware(surviving.opener_sent_at) == later


def test_absorb_exchange_takes_a_set_sentinel_from_either_side(db):
    # The most-advanced value wins whichever side carries it: a merge must never
    # re-fire a stage that EITHER block had already delivered.
    stamp = datetime(2026, 5, 27, 10, 0, 0, tzinfo=timezone.utc)
    surviving = _exchange(db, opened_at=stamp)  # absorbed side has the fuller
    absorbed = _exchange(db, fuller_sent_at=stamp)

    lifecycle.absorb_exchange(db, surviving, absorbed)

    assert _aware(surviving.opened_at) == stamp  # kept its own
    assert _aware(surviving.fuller_sent_at) == stamp  # took the absorbed side's
    assert lifecycle.is_closed(surviving) is True


def test_absorb_exchange_takes_the_done_tap_from_the_absorbed_side(db):
    # #750: the runner tapped "done" on the block that is being absorbed. The merged
    # block is a superset of that session, so the survivor must read as done —
    # otherwise a subsequent tap schedules a second full-report generation.
    stamp = datetime(2026, 5, 27, 10, 0, 0, tzinfo=timezone.utc)
    surviving = _exchange(db, opened_at=stamp)
    absorbed = _exchange(db, opened_at=stamp, done_at=stamp)
    db.expire_all()  # read both sides as the DB returns them (see the test above)

    lifecycle.absorb_exchange(db, surviving, absorbed)
    db.flush()  # structural: the caller's transaction is what lands the row

    assert _aware(surviving.done_at) == stamp
    assert lifecycle.mark_done(db, surviving) is False  # a later tap is a no-op


def test_absorb_exchange_leaves_the_survivor_tappable_when_neither_side_tapped(db):
    stamp = datetime(2026, 5, 27, 10, 0, 0, tzinfo=timezone.utc)
    surviving = _exchange(db, opened_at=stamp)
    absorbed = _exchange(db, opened_at=stamp)
    db.expire_all()  # read both sides as the DB returns them (see the test above)

    lifecycle.absorb_exchange(db, surviving, absorbed)

    assert surviving.done_at is None
    assert lifecycle.mark_done(db, surviving) is True


def test_absorb_exchange_deletes_the_absorbed_row(db):
    surviving = _exchange(db)
    absorbed = _exchange(db)
    absorbed_block_id = absorbed.block_id

    lifecycle.absorb_exchange(db, surviving, absorbed)
    db.flush()

    assert lifecycle.get_exchange_for_block(db, absorbed_block_id) is None


def test_delete_exchange_for_block_removes_the_row(db):
    ex = _exchange(db)
    block_id = ex.block_id

    assert lifecycle.delete_exchange_for_block(db, block_id) is True
    db.flush()
    assert lifecycle.get_exchange_for_block(db, block_id) is None


def test_delete_exchange_for_block_reports_when_there_was_none(db):
    # The emptied-block cleanup must not assume a row exists; the caller only
    # sequences its FK-ordering flush when something was actually deleted.
    assert lifecycle.delete_exchange_for_block(db, uuid4()) is False


def test_require_exchange_for_block_returns_the_row(db):
    ex = _exchange(db)
    assert lifecycle.require_exchange_for_block(db, ex.block_id) is ex


def test_require_exchange_for_block_raises_when_absent(db):
    # The correction paths treat a missing exchange as a broken invariant, and the
    # blocks API catches only ValueError — so this must NOT degrade into a 422.
    with pytest.raises(NoResultFound):
        lifecycle.require_exchange_for_block(db, uuid4())


# --- the fuller-turn claim/release dance (#740) -------------------------------


def test_fuller_claim_kept_leaves_the_exchange_closed(db):
    ex = _exchange(db, opened_at=datetime.now(timezone.utc))

    with lifecycle.fuller_claim(db, ex) as claim:
        assert claim.won is True
        claim.keep()  # the send succeeded

    db.refresh(ex)
    assert ex.fuller_sent_at is not None
    assert lifecycle.is_closed(ex) is True


def test_fuller_claim_releases_when_the_turn_was_not_kept(db):
    # No report / fallback report / no channel / send failure: the caller never keeps
    # the claim, so the stage must stay re-sendable (#114).
    ex = _exchange(db, opened_at=datetime.now(timezone.utc))

    with lifecycle.fuller_claim(db, ex) as claim:
        assert claim.won is True

    db.refresh(ex)
    assert ex.fuller_sent_at is None


def test_fuller_claim_releases_on_a_raised_exception(db):
    # The failure the claim was most likely to strand: a raise mid-generation (an RQ
    # job timeout over the ~120-360s window) must not leave the exchange CLOSED with
    # no notification, or the RQ retry's claim finds the sentinel set and bails.
    ex = _exchange(db, opened_at=datetime.now(timezone.utc))

    with pytest.raises(RuntimeError):
        with lifecycle.fuller_claim(db, ex) as claim:
            assert claim.won is True
            raise RuntimeError("generation blew up")

    db.refresh(ex)
    assert ex.fuller_sent_at is None


def test_losing_fuller_claim_never_releases_the_winners(db):
    # The hazard the `won` guard exists for: the sentinel a loser finds set belongs to
    # the winner, so releasing it would re-arm a turn somebody else is still running.
    ex = _exchange(db, opened_at=datetime.now(timezone.utc))

    with lifecycle.fuller_claim(db, ex) as winner:
        assert winner.won is True

        with lifecycle.fuller_claim(db, ex) as loser:
            assert loser.won is False

        db.refresh(ex)
        assert ex.fuller_sent_at is not None, "the loser's exit cleared the winner's claim"
        winner.keep()

    db.refresh(ex)
    assert ex.fuller_sent_at is not None
