"""#238: Deleting a Strava activity removes it from its Block.

When a Strava delete webhook soft-deletes an activity the activity must also
leave its Block. Today the soft-deleted activity keeps its `block_id`, so it
can shape block bounds, hold the primary anchor, count as the block's last
member in the opener debounce, and surface as a grouping candidate for late
arrivals.

Acceptance criteria tested here:
  AC1 — deletion removes the activity from its block; surviving members' bounds
         and primary are recomputed.
  AC2 — a block emptied by deletion is not a candidate for grouping late
         arrivals and cannot trigger an opener.
  AC3 — deletion never re-fires any notification (exchange sentinels are
         preserved unchanged).
  AC4 — a deletion arriving after the block exchange has opened or closed
         leaves the at-most-once delivery semantics intact.
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.models import Activity, Block, Exchange, User
from app.services.blocks import (
    assign_activity_to_block,
    remove_activity_from_block,
    activity_end as activity_end_of,
)

T0 = datetime(2026, 6, 1, 8, 0, tzinfo=timezone.utc)
GAP = 1800  # seconds


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _make_user(db) -> User:
    user = User(email=f"del-block-{uuid.uuid4().hex[:8]}@example.com")
    db.add(user)
    db.commit()
    return user


def _make_activity(
    db, user, *, start, elapsed=1500, type="Run", **overrides
) -> Activity:
    fields = dict(
        user_id=user.id,
        strava_activity_id=int(uuid.uuid4().int % 10**12),
        start_date=start,
        type=type,
        name=f"{type} at {start:%H:%M}",
        distance_m=5000,
        moving_time_s=elapsed,
        elapsed_time_s=elapsed,
        elev_gain_m=10.0,
        raw_summary={},
    )
    fields.update(overrides)
    a = Activity(**fields)
    db.add(a)
    db.commit()
    return a


def _exchange(db, block: Block) -> Exchange:
    return db.query(Exchange).filter(Exchange.block_id == block.id).one()


# ---------------------------------------------------------------------------
# AC1: removing an activity recomputes surviving members' bounds and primary
# ---------------------------------------------------------------------------


def test_delete_removes_activity_and_recomputes_bounds(db):
    """Deleting the non-primary member recomputes block bounds to surviving member."""
    user = _make_user(db)
    walk = _make_activity(db, user, start=T0, elapsed=1200, type="Walk")
    block = assign_activity_to_block(db, walk, gap_seconds=GAP)
    run_start = T0 + timedelta(seconds=1800)
    run = _make_activity(db, user, start=run_start, elapsed=2400, type="Run")
    assign_activity_to_block(db, run, gap_seconds=GAP)
    db.refresh(block)
    assert block.primary_activity_id == run.id

    # Soft-delete the walk and remove it from the block.
    walk.is_deleted = True
    db.commit()
    remove_activity_from_block(db, walk)

    db.refresh(walk)
    db.refresh(block)
    assert walk.block_id is None  # detached
    assert block.primary_activity_id == run.id  # run is still primary
    assert _aware(block.start_date) == run_start  # bounds recomputed from run only
    assert _aware(block.end_date) == run_start + timedelta(seconds=2400)
    # Run is still in the block
    assert run.block_id == block.id


def test_delete_primary_recomputes_primary_to_surviving_member(db):
    """Deleting the primary member transfers primary to the next-best survivor."""
    user = _make_user(db)
    walk = _make_activity(db, user, start=T0, elapsed=1200, type="Walk")
    block = assign_activity_to_block(db, walk, gap_seconds=GAP)
    run_start = T0 + timedelta(seconds=1800)
    run = _make_activity(db, user, start=run_start, elapsed=2400, type="Run")
    assign_activity_to_block(db, run, gap_seconds=GAP)
    db.refresh(block)
    assert block.primary_activity_id == run.id

    # Now delete the run (the primary).
    run.is_deleted = True
    db.commit()
    remove_activity_from_block(db, run)

    db.refresh(run)
    db.refresh(block)
    assert run.block_id is None
    assert block.primary_activity_id == walk.id  # only survivor becomes primary
    assert _aware(block.start_date) == T0
    assert _aware(block.end_date) == T0 + timedelta(seconds=1200)


def test_delete_only_member_empties_block(db):
    """Deleting the sole member leaves the block with no members. The block row
    and its Exchange are removed so nothing can spuriously reference them."""
    user = _make_user(db)
    run = _make_activity(db, user, start=T0, elapsed=1800, type="Run")
    block = assign_activity_to_block(db, run, gap_seconds=GAP)
    block_id = block.id

    run.is_deleted = True
    db.commit()
    remove_activity_from_block(db, run)

    db.refresh(run)
    assert run.block_id is None
    # Block and Exchange are removed when emptied.
    assert db.query(Block).filter(Block.id == block_id).count() == 0
    assert db.query(Exchange).filter(Exchange.block_id == block_id).count() == 0


# ---------------------------------------------------------------------------
# AC2: an emptied block is not a grouping candidate and cannot trigger an opener
# ---------------------------------------------------------------------------


def test_emptied_block_not_a_grouping_candidate_for_late_arrival(db):
    """A late activity does not join the emptied (deleted) block's slot; it starts
    a fresh block of its own."""
    user = _make_user(db)
    run = _make_activity(db, user, start=T0, elapsed=1800, type="Run")
    block = assign_activity_to_block(db, run, gap_seconds=GAP)
    run.is_deleted = True
    db.commit()
    remove_activity_from_block(db, run)

    # A late activity that would have been contiguous with the deleted run.
    late_start = T0 + timedelta(seconds=1800 + 600)
    late = _make_activity(db, user, start=late_start, elapsed=1500, type="Run")
    new_block = assign_activity_to_block(db, late, gap_seconds=GAP)

    # The emptied block no longer exists, so this must be a new block.
    assert new_block.id != block.id
    assert late.block_id == new_block.id


def test_block_complete_opener_skips_empty_block(db):
    """The block-complete opener check must no-op when the triggering activity's
    block has been emptied (all members deleted). process_block_complete already
    short-circuits on empty member sets; this verifies the detached activity leaves
    block_complete with nothing to open."""
    import asyncio
    from unittest.mock import patch, AsyncMock

    from app.core.config import settings
    from app.jobs.process_new_activity import process_block_complete
    from app.services.notifications.in_memory_adapter import InMemoryNotifier
    from app.services.notifications import set_notifier

    notifier = InMemoryNotifier()
    set_notifier(notifier)

    try:
        user = _make_user(db)
        run = _make_activity(db, user, start=T0, elapsed=1800, type="Run")
        block = assign_activity_to_block(db, run, gap_seconds=GAP)
        block_id = str(block.id)
        activity_id = str(run.id)

        # Delete the only member.
        run.is_deleted = True
        db.commit()
        remove_activity_from_block(db, run)

        # Simulate the block-complete job firing after the block was emptied.
        # process_block_complete must not open an exchange or notify.
        from app.core.config import settings as _settings
        orig = _settings.COACH_PROMPT_ID

        try:
            _settings.COACH_PROMPT_ID = "coach_message_v2"
            with patch("app.services.coach.service.AnthropicClient") as client_cls:
                result = asyncio.run(
                    process_block_complete(
                        db=db,
                        block_id=block_id,
                        activity_id=activity_id,
                        notifier=notifier,
                    )
                )
        finally:
            _settings.COACH_PROMPT_ID = orig

        assert result is None
        assert len(notifier.sent) == 0
        client_cls.assert_not_called()
    finally:
        set_notifier(None)


# ---------------------------------------------------------------------------
# AC3: deletion never re-fires any notification
# ---------------------------------------------------------------------------


def test_deletion_preserves_exchange_sentinels_when_opener_sent(db):
    """When an activity is deleted from a block whose opener has already been
    sent, the exchange sentinels are left completely unchanged."""
    user = _make_user(db)
    walk = _make_activity(db, user, start=T0, elapsed=1200, type="Walk")
    block = assign_activity_to_block(db, walk, gap_seconds=GAP)
    run_start = T0 + timedelta(seconds=1800)
    run = _make_activity(db, user, start=run_start, elapsed=2400, type="Run")
    assign_activity_to_block(db, run, gap_seconds=GAP)
    db.refresh(block)

    opened_at = datetime(2026, 6, 1, 9, 0, tzinfo=timezone.utc)
    opener_sent = datetime(2026, 6, 1, 9, 0, 5, tzinfo=timezone.utc)
    ex = _exchange(db, block)
    ex.opened_at = opened_at
    ex.opener_sent_at = opener_sent
    db.commit()

    # Delete the walk (non-primary); recompute should happen but sentinels stay.
    walk.is_deleted = True
    db.commit()
    remove_activity_from_block(db, walk)

    db.refresh(ex)
    # Sentinels untouched.
    assert _aware(ex.opened_at) == opened_at
    assert _aware(ex.opener_sent_at) == opener_sent
    assert ex.fuller_sent_at is None  # was not set, still not set


def test_deletion_when_no_exchange_sentinels_set_leaves_exchange_fresh(db):
    """Deleting from an unopened block leaves the exchange in its initial state
    (all sentinels null) — nothing spins up any delivery."""
    user = _make_user(db)
    walk = _make_activity(db, user, start=T0, elapsed=1200, type="Walk")
    block = assign_activity_to_block(db, walk, gap_seconds=GAP)
    run_start = T0 + timedelta(seconds=1800)
    run = _make_activity(db, user, start=run_start, elapsed=2400, type="Run")
    assign_activity_to_block(db, run, gap_seconds=GAP)
    db.refresh(block)

    ex = _exchange(db, block)
    # All sentinels are null.
    assert ex.opened_at is None
    assert ex.opener_sent_at is None
    assert ex.fuller_sent_at is None

    walk.is_deleted = True
    db.commit()
    remove_activity_from_block(db, walk)

    db.refresh(ex)
    assert ex.opened_at is None
    assert ex.opener_sent_at is None
    assert ex.fuller_sent_at is None


# ---------------------------------------------------------------------------
# AC4: deletion after exchange opened or closed leaves delivery semantics intact
# ---------------------------------------------------------------------------


def test_deletion_after_exchange_closed_preserves_closed_state(db):
    """Deleting an activity from a block whose exchange is fully CLOSED (both
    stages sent) must not reopen or alter the closed exchange in any way."""
    user = _make_user(db)
    walk = _make_activity(db, user, start=T0, elapsed=1200, type="Walk")
    block = assign_activity_to_block(db, walk, gap_seconds=GAP)
    run_start = T0 + timedelta(seconds=1800)
    run = _make_activity(db, user, start=run_start, elapsed=2400, type="Run")
    assign_activity_to_block(db, run, gap_seconds=GAP)
    db.refresh(block)

    opened_at = datetime(2026, 6, 1, 9, 0, tzinfo=timezone.utc)
    opener_sent = datetime(2026, 6, 1, 9, 0, 5, tzinfo=timezone.utc)
    fuller_sent = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)
    ex = _exchange(db, block)
    ex.opened_at = opened_at
    ex.opener_sent_at = opener_sent
    ex.fuller_sent_at = fuller_sent
    db.commit()

    # Now delete the primary (the run).
    run.is_deleted = True
    db.commit()
    remove_activity_from_block(db, run)

    db.refresh(ex)
    # Exchange remains closed and unchanged.
    assert ex.fuller_sent_at is not None
    assert _aware(ex.fuller_sent_at) == fuller_sent
    assert _aware(ex.opened_at) == opened_at
    assert _aware(ex.opener_sent_at) == opener_sent


def test_deletion_of_only_member_after_exchange_opened_removes_block_safely(db):
    """Deleting the sole member of a block whose exchange has already opened
    (but not yet closed) removes the block and exchange safely, preserving the
    at-most-once guarantee by removing the exchange rather than resetting it."""
    user = _make_user(db)
    run = _make_activity(db, user, start=T0, elapsed=1800, type="Run")
    block = assign_activity_to_block(db, run, gap_seconds=GAP)
    block_id = block.id

    ex = _exchange(db, block)
    ex.opened_at = datetime.now(timezone.utc)
    ex.opener_sent_at = datetime.now(timezone.utc)
    db.commit()

    run.is_deleted = True
    db.commit()
    remove_activity_from_block(db, run)

    db.refresh(run)
    assert run.block_id is None
    # Block and Exchange are gone; the at-most-once guarantee is preserved
    # because the exchange row no longer exists to be re-fired against.
    assert db.query(Block).filter(Block.id == block_id).count() == 0
    assert db.query(Exchange).filter(Exchange.block_id == block_id).count() == 0
